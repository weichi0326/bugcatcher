# -*- coding: utf-8 -*-
"""本地项目管理界面：配置、聊天归档与分析报告。"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_file, send_from_directory
from web_operations import OperationError, OperationManager

PROJECT_ROOT = Path(__file__).resolve().parent
UI_ROOT = PROJECT_ROOT / "config_ui"
WEB_PORT = int(os.environ.get("QINGLAN_CFG_WEB_PORT") or 8088)
_WRITE_LOCK = threading.RLock()
_MAX_CONFIG_BYTES = 2 * 1024 * 1024
_MAX_REPORT_BYTES = 8 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
_MAX_LIST_FILES = 2000
_OPERATIONS: OperationManager | None = None
_OPERATIONS_LOCK = threading.Lock()

app = Flask(__name__, static_folder=None)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@app.errorhandler(ApiError)
def _api_error(exc: ApiError):
    return jsonify({"ok": False, "error": str(exc)}), exc.status


@app.errorhandler(OperationError)
def _operation_error(exc: OperationError):
    return jsonify({"ok": False, "error": str(exc)}), 409


def _operations() -> OperationManager:
    global _OPERATIONS
    with _OPERATIONS_LOCK:
        if _OPERATIONS is None or _OPERATIONS.root != PROJECT_ROOT.resolve():
            _OPERATIONS = OperationManager(PROJECT_ROOT)
        return _OPERATIONS


def _config_dir() -> Path:
    return PROJECT_ROOT / "config"


def _master_path() -> Path:
    return _config_dir() / "配置主表.json"


def _read_json_or_empty(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}


def _configured_root(raw: Any, fallback: str) -> Path:
    value = str(raw or fallback).strip()
    path = Path(value)
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def _archive_root() -> Path:
    runtime = _read_json_or_empty(_config_dir() / "bot_config.json")
    master = _read_json_or_empty(_master_path())
    bot = runtime or master.get("bot") or {}
    return _configured_root(bot.get("conversation_log_dir"), "conversation_logs")


def _report_root() -> Path:
    runtime = _read_json_or_empty(_config_dir() / "analysis_config.json")
    master = _read_json_or_empty(_master_path())
    analysis = runtime or master.get("analysis") or {}
    return _configured_root(analysis.get("archive_dir"), "analysis_reports")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _relative(raw: Any) -> Path:
    value = str(raw or "").replace("\\", "/")
    if not value or len(value) > 1000 or value.startswith("/") or "\x00" in value:
        raise ApiError("无效的文件路径")
    parts = value.split("/")
    if any(part in ("", ".", "..") or ":" in part for part in parts):
        raise ApiError("文件路径不能包含上级目录或绝对路径")
    return Path(*parts)


def _source_markdown_paths() -> set[Path]:
    """只列出当前主表实际引用、应用时会写入 JSON 的 Markdown。"""
    source = _master_path()
    if not source.is_file():
        source = _config_dir() / "配置主表.example.json"
    master = _read_json_or_empty(source)
    config_root = _config_dir().resolve()
    paths: set[Path] = set()

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            raw = value.strip()
            file_ref = raw.lower().startswith("@file:")
            if not file_ref and key not in ("task_system_prompt_file", "merge_batch_system_prompt_file", "long_text_file"):
                return
            if file_ref:
                raw = raw[6:].strip()
            try:
                path = (PROJECT_ROOT / _relative(raw)).resolve()
            except ApiError:
                return
            if path.suffix.lower() == ".md" and _inside(path, config_root) and path.is_file():
                paths.add(path)

    visit(master)
    return paths


def _config_policy(path: Path) -> tuple[bool, bool, str, str]:
    """返回 (可浏览, 可编辑, 说明, 类型)。"""
    config_root = _config_dir().resolve()
    if _inside(path, config_root):
        if path == _master_path().resolve():
            return True, True, "主配置；保存后点击「应用配置」生成运行时文件。", "master"
        if path in _source_markdown_paths():
            return True, True, "源 Markdown；保存并应用后会写入对应的运行时 JSON。", "source_markdown"
    return False, False, "", ""


def _scope_root(scope: str) -> Path:
    if scope == "config":
        return PROJECT_ROOT.resolve()
    if scope == "archives":
        return _archive_root()
    if scope == "reports":
        return _report_root()
    raise ApiError("未知的文件范围")


def _lookup(scope: str, raw_path: Any) -> tuple[Path, bool, str, str]:
    root = _scope_root(scope)
    path = (root / _relative(raw_path)).resolve()
    if not _inside(path, root):
        raise ApiError("文件路径超出允许范围", 403)
    if scope == "config":
        allowed, editable, note, kind = _config_policy(path)
    elif scope == "archives":
        allowed = path.suffix.lower() in (".json", ".txt") and path.name != "session.meta.json"
        editable, note, kind = False, "聊天归档只读。", "archive"
    else:
        allowed = path.suffix.lower() == ".md"
        editable, note, kind = False, "分析报告只读。", "report"
    if not allowed:
        raise ApiError("该文件不在可管理范围内", 403)
    if not path.is_file():
        raise ApiError("文件不存在", 404)
    return path, editable, note, kind


def _file_info(scope: str, path: Path) -> dict:
    root = _scope_root(scope)
    stat = path.stat()
    if scope == "config":
        _, editable, note, kind = _config_policy(path)
    else:
        editable, note, kind = False, "", "archive" if scope == "archives" else "report"
    return {
        "path": path.relative_to(root).as_posix(),
        "name": path.name,
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "editable": editable,
        "note": note,
        "kind": kind,
        "version": f"{stat.st_size}:{stat.st_mtime_ns}" if scope in ("archives", "reports") else "",
    }


def _scan(scope: str) -> list[dict]:
    root = _scope_root(scope)
    candidates: list[Path] = []
    if scope == "config":
        candidates.extend(_source_markdown_paths())
    elif root.is_dir():
        candidates.extend(root.rglob("*"))

    files: list[dict] = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        path = candidate.resolve()
        if not _inside(path, root):
            continue
        if scope == "config":
            allowed = _config_policy(path)[0]
        elif scope == "archives":
            allowed = path.suffix.lower() in (".json", ".txt") and path.name != "session.meta.json"
        else:
            allowed = path.suffix.lower() == ".md"
        if allowed:
            try:
                files.append(_file_info(scope, path))
            except OSError:
                continue
    files.sort(key=lambda item: (item["modified"], item["path"]), reverse=True)
    return files


def _etag(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _int_arg(name: str, default: int, maximum: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        raise ApiError(f"{name} 必须是整数") from None
    return max(0, min(maximum, value))


@app.get("/")
def index():
    return send_from_directory(UI_ROOT, "index.html")


@app.get("/assets/<path:name>")
@app.get("/config-ui/<path:name>")
def assets(name: str):
    return send_from_directory(UI_ROOT, name)


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "master_exists": _master_path().is_file(),
        "archives_exist": _archive_root().is_dir(),
        "reports_exist": _report_root().is_dir(),
    })


@app.get("/api/overview")
def overview():
    configs = _scan("config")
    archives = _scan("archives")
    reports = _scan("reports")
    sessions = {item["path"].split("/", 1)[0] for item in archives}
    return jsonify({
        "ok": True,
        "stats": {
            "config_files": len(configs),
            "archive_files": len(archives),
            "report_files": len(reports),
            "sessions": len(sessions),
        },
        "recent_reports": reports[:5],
        "roots": {
            "config": str(_config_dir()),
            "archives": str(_archive_root()),
            "reports": str(_report_root()),
        },
        "master_exists": _master_path().is_file(),
    })


@app.get("/api/files")
def files():
    scope = str(request.args.get("scope") or "")
    all_files = _scan(scope)
    query = str(request.args.get("q") or "").strip().casefold()
    if query:
        all_files = [item for item in all_files if query in item["path"].casefold()]
    return jsonify({
        "ok": True,
        "files": all_files[:_MAX_LIST_FILES],
        "total": len(all_files),
        "truncated": len(all_files) > _MAX_LIST_FILES,
    })


@app.get("/api/file")
def get_file():
    scope = str(request.args.get("scope") or "")
    path, editable, note, kind = _lookup(scope, request.args.get("path"))
    info = _file_info(scope, path)
    size = info["size"]
    if scope == "archives" and path.suffix.lower() == ".json":
        if size > _MAX_ARCHIVE_BYTES:
            raise ApiError("归档文件超过 128 MiB，无法在页面中加载", 413)
        try:
            data = _read_json_or_empty(path)
            records = data.get("messages")
            if not isinstance(records, list):
                raise ApiError("归档 JSON 没有 messages 数组", 422)
        except UnicodeError:
            raise ApiError("归档文件编码无效", 422) from None
        offset = _int_arg("offset", 0, 100000000)
        limit = max(1, _int_arg("limit", 100, 200))
        return jsonify({
            "ok": True,
            **info,
            "records": records[offset:offset + limit],
            "total": len(records),
            "offset": offset,
            "limit": limit,
            "channel": data.get("channel", ""),
            "session_id": data.get("session_id", ""),
        })

    maximum = _MAX_CONFIG_BYTES if scope == "config" else _MAX_REPORT_BYTES
    if size > maximum:
        raise ApiError("文件过大，无法在页面中加载", 413)
    try:
        raw = path.read_bytes()
        content = raw.decode("utf-8-sig")
    except UnicodeError:
        raise ApiError("文件不是 UTF-8 文本", 422) from None
    result = {"ok": True, **info, "content": content, "etag": _etag(raw)}
    if scope == "reports":
        meta = _read_json_or_empty(path.with_suffix(".meta.json"))
        if meta:
            result["meta"] = meta
    return jsonify(result)


@app.put("/api/file")
def put_file():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("请提交 JSON 请求体")
    scope = str(data.get("scope") or "")
    path, editable, note, kind = _lookup(scope, data.get("path"))
    if scope != "config" or not editable:
        raise ApiError("此文件只读", 403)
    content = data.get("content")
    if not isinstance(content, str):
        raise ApiError("content 必须是文本")
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_CONFIG_BYTES:
        raise ApiError("配置文件不能超过 2 MiB", 413)
    if path.suffix.lower() == ".json":
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ApiError(f"JSON 第 {exc.lineno} 行、第 {exc.colno} 列有错误：{exc.msg}") from None
        if kind == "master" and not isinstance(parsed, dict):
            raise ApiError("配置主表必须是 JSON 对象")
    expected = data.get("etag")
    if not isinstance(expected, str) or not expected:
        raise ApiError("缺少文件版本；请重新打开文件后保存", 428)
    with _WRITE_LOCK:
        current = path.read_bytes()
        if _etag(current) != expected:
            raise ApiError("文件已被其他程序修改，请重新加载后再保存", 409)
        _atomic_write(path, encoded)
    return jsonify({"ok": True, "path": data["path"], "etag": _etag(encoded), "editable": True, "note": note})


def _report_pending(path: Path) -> bool:
    state = _read_json_or_empty(_report_root() / ".scheduler_state.json")
    pending = state.get("qq_sentiment_daily_pending")
    if not isinstance(pending, dict):
        return False
    for item in pending.values():
        raw = item.get("path") if isinstance(item, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            continue
        candidate = Path(raw)
        candidate = (candidate if candidate.is_absolute() else PROJECT_ROOT / candidate).resolve()
        if candidate == path:
            return True
    return False


@app.delete("/api/file")
def delete_file():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("请提交 JSON 请求体")
    scope = str(data.get("scope") or "")
    if scope not in ("archives", "reports"):
        raise ApiError("此范围不允许删除", 403)
    version = data.get("version")
    if not isinstance(version, str) or not version:
        raise ApiError("缺少文件版本，请重新打开后再删除", 428)
    with _WRITE_LOCK:
        path, _, _, _ = _lookup(scope, data.get("path"))
        current = _file_info(scope, path)
        if current["version"] != version:
            raise ApiError("文件已改变，请重新打开确认后再删除", 409)
        if scope == "reports" and _report_pending(path):
            raise ApiError("该报告仍有待发送任务，完成或取消推送后才能删除", 409)
        companion = path.with_suffix(".meta.json") if scope == "reports" else None
        if companion is not None and companion.is_file() and not _inside(companion.resolve(), _report_root()):
            raise ApiError("报告元数据路径超出归档目录", 403)
        try:
            if companion is not None and companion.is_file():
                companion.unlink()
            path.unlink()
        except OSError as exc:
            raise ApiError(f"删除失败：{exc}", 500) from None
    return jsonify({"ok": True, "scope": scope, "path": current["path"]})


@app.post("/api/bootstrap")
def bootstrap():
    source = _config_dir() / "配置主表.example.json"
    target = _master_path()
    with _WRITE_LOCK:
        if target.exists():
            raise ApiError("配置主表已存在，不会覆盖", 409)
        if not source.is_file():
            raise ApiError("找不到配置主表示例", 404)
        raw = source.read_bytes()
        try:
            parsed = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError):
            raise ApiError("配置主表示例不是有效 JSON", 422) from None
        if not isinstance(parsed, dict):
            raise ApiError("配置主表示例必须是 JSON 对象", 422)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(raw)
    return jsonify({"ok": True, "path": "config/配置主表.json"})


@app.post("/api/apply")
@app.post("/apply")
def apply():
    if not _master_path().is_file():
        raise ApiError("请先创建配置主表", 404)
    from config.config_master import apply_master_config

    with _WRITE_LOCK:
        result = apply_master_config(master_path=str(_master_path()), reload_runtime=True)
    ok = bool(result.get("ok"))
    return jsonify({
        "ok": ok,
        "written": [str(Path(item).name) for item in result.get("written") or []],
        "errors": [str(item) for item in result.get("errors") or []],
        "restart_hint": "部分启动时加载的配置需要重启机器人或 NapCat 后生效。",
    }), 200 if ok else 500


def _send_feishu_test(chat_id: str, message: str) -> bool:
    """使用已应用的飞书凭据发送一条纯文本测试消息。"""
    from botskill.message_routing import apply_feishu_credentials
    from botskill.feishu_client import TOKEN_CACHE, TOKEN_LOCK, send_message

    if not apply_feishu_credentials():
        raise ApiError("飞书凭据尚未应用，请先保存并应用设置", 422)
    # 设置页可能刚更换 app_id / app_secret；测试时必须重新获取对应 token。
    with TOKEN_LOCK:
        TOKEN_CACHE["value"] = ""
        TOKEN_CACHE["expire_at"] = 0
    return bool(send_message(chat_id, message))


@app.post("/api/push-test")
def push_test():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("请提交 JSON 请求体")
    chat_id = str(data.get("chat_id") or "").strip()
    message = str(data.get("message") or "").strip()
    if not chat_id.startswith("oc_") or len(chat_id) > 128:
        raise ApiError("请输入有效的飞书群会话 ID（oc_ 开头）")
    if not message or len(message) > 4000:
        raise ApiError("测试消息长度须为 1–4000 字")

    master = _read_json_or_empty(_master_path())
    analysis = master.get("analysis")
    qq_settings = analysis.get("qq_game_sentiment") if isinstance(analysis, dict) else None
    targets = qq_settings.get("feishu_push_chat_ids") if isinstance(qq_settings, dict) else []
    if not isinstance(targets, list) or chat_id not in [str(item).strip() for item in targets]:
        raise ApiError("请先将该会话 ID 加入飞书推送会话列表并保存", 422)
    with _WRITE_LOCK:
        success = _send_feishu_test(chat_id, message)
    if not success:
        raise ApiError("发送失败；请确认机器人已加入该群且飞书凭据有效", 502)
    return jsonify({"ok": True, "chat_id": chat_id, "message": "测试消息已发送"})


@app.get("/api/model/providers")
def model_providers():
    from botskill.model_api import PROVIDERS

    return jsonify({"ok": True, "providers": list(PROVIDERS)})


def _model_request() -> dict[str, str]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("请提交 JSON 请求体")
    fields = ("provider", "base_url", "api_key", "model")
    values = {}
    for field in fields:
        value = data.get(field, "")
        if not isinstance(value, str) or len(value) > (2000 if field == "api_key" else 1000):
            raise ApiError(f"{field} 格式无效")
        values[field] = value.strip()
    values["provider"] = values["provider"] or "custom"
    return values


@app.post("/api/model/list")
def model_list():
    from botskill.model_api import ModelApiError, list_models

    values = _model_request()
    try:
        models, latency_ms = list_models(**{key: values[key] for key in ("provider", "base_url", "api_key")})
    except ModelApiError as exc:
        raise ApiError(str(exc), exc.status) from None
    return jsonify({"ok": True, "models": models, "latency_ms": latency_ms})


@app.post("/api/model/test")
def model_test():
    from botskill.model_api import ModelApiError, test_model

    values = _model_request()
    try:
        latency_ms, preview = test_model(**values)
    except ModelApiError as exc:
        raise ApiError(str(exc), exc.status) from None
    return jsonify({"ok": True, "latency_ms": latency_ms, "model": values["model"], "preview": preview})


@app.get("/api/operations/status")
def operations_status():
    return jsonify(_operations().snapshot())


@app.get("/api/qq/qrcode")
def qq_qrcode():
    path = _operations().qq_qrcode()
    if path is None:
        raise ApiError("当前没有可用的登录二维码，请先启动 QQ", 404)
    response = send_file(path, mimetype="image/png", max_age=0)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/operations/job")
def operations_job():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        raise ApiError("请指定操作名称")
    return jsonify(_operations().start_job(data["name"]))


@app.post("/api/operations/start")
def operations_start():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("mode"), str):
        raise ApiError("请指定启动模式")
    return jsonify(_operations().start_service(data["mode"]))


@app.post("/api/operations/stop")
def operations_stop():
    return jsonify(_operations().stop_service())


def main() -> int:
    from scripts.project_processes import replace_existing

    stopped = replace_existing(PROJECT_ROOT, ("config_web.py",))
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if stopped:
        print(f"已关闭旧管理界面进程：{', '.join(map(str, stopped))}")
    print(f"项目管理界面：http://127.0.0.1:{WEB_PORT}")
    app.run(host="127.0.0.1", port=WEB_PORT, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
