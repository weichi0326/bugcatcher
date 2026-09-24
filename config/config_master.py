# -*- coding: utf-8 -*-
"""
配置主表 → 各运行时 JSON。WebUI「配置 → 应用配置」调用此模块，
生成 bot_config.json、models.json、analysis_config.json 等。
"""

from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from config.constants import REL_CONFIG_DIR_NAME
from config.file_write_manager import FileWriteManager

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CONFIG_DIR = os.path.join(_PROJECT_ROOT, REL_CONFIG_DIR_NAME)
MASTER_BASENAME = "配置主表.json"
MASTER_EXAMPLE_BASENAME = "配置主表.example.json"
MASTER_PATH = os.path.join(_CONFIG_DIR, MASTER_BASENAME)
MASTER_EXAMPLE_PATH = os.path.join(_CONFIG_DIR, MASTER_EXAMPLE_BASENAME)

_SAVE_TIMEOUT = 45.0
_FILE_REF_RE = re.compile(r"^@file:(.+)$", re.IGNORECASE)


def _read_json(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _resolve_text(value: Any, *, base_dir: str) -> Any:
    if not isinstance(value, str):
        return value
    raw = value.strip()
    match = _FILE_REF_RE.match(raw)
    if not match:
        return value
    rel = match.group(1).strip().replace("/", os.sep)
    path = rel if os.path.isabs(rel) else os.path.join(_PROJECT_ROOT, rel)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"配置主表引用的文件不存在：{path}")
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip()


def _deep_resolve(obj: Any, *, base_dir: str) -> Any:
    if isinstance(obj, dict):
        return {k: _deep_resolve(v, base_dir=base_dir) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_resolve(v, base_dir=base_dir) for v in obj]
    return _resolve_text(obj, base_dir=base_dir)


def _strip_meta(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if not str(k).startswith("_")}


def _write_json(path: str, payload: Dict[str, Any], *, meta: Dict[str, str]) -> None:
    out = dict(payload)
    out.update(meta)
    FileWriteManager.write_json_atomically(
        path,
        out,
        acquire_timeout_sec=_SAVE_TIMEOUT,
        backup_before_replace=True,
    )


def _meta_block() -> Dict[str, str]:
    return {
        "_generated_from": f"config/{MASTER_BASENAME}",
        "_apply_hint": "勿直接改本文件；请在 WebUI 的配置页面保存并应用配置主表",
    }


def _load_master(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or MASTER_PATH
    if not os.path.isfile(p):
        raise FileNotFoundError(
            f"未找到配置主表：{p}\n请复制 config/{MASTER_EXAMPLE_BASENAME} 为 config/{MASTER_BASENAME} 后填写。"
        )
    master = _read_json(p)
    return _deep_resolve(master, base_dir=_CONFIG_DIR)


def _build_bot_config(master: Dict[str, Any]) -> Dict[str, Any]:
    bot = _strip_meta(dict(master.get("bot") or {}))
    for legacy in ("qq_app_id", "qq_app_secret", "qq_bot_number", "qq_bot_token"):
        bot.pop(legacy, None)
    bot.pop("admins", None)  # 兼容旧主表；当前命令不再区分管理员。
    ports = master.get("ports") or {}
    if ports:
        bot["port"] = int(ports.get("bot", bot.get("port", 5000)))
        bot["napcat_http_port"] = int(ports.get("napcat_http", bot.get("napcat_http_port", 3000)))
        bot["onebot_http_api"] = f"http://127.0.0.1:{bot['napcat_http_port']}"
    return bot


def _build_models(master: Dict[str, Any]) -> Dict[str, Any]:
    models = _strip_meta(dict(master.get("models") or {}))
    # Provider profiles belong to the editable master only. Runtime consumers
    # read models.text and should receive just the active provider credentials.
    models.pop("provider_profiles", None)
    return models


def _build_feishu_bot(master: Dict[str, Any]) -> Dict[str, Any]:
    return _strip_meta(dict(master.get("feishu_bot") or {}))


def _build_analysis(master: Dict[str, Any]) -> Dict[str, Any]:
    analysis = deepcopy(master.get("analysis") or {})
    analysis.pop("feishu_daily_summary", None)  # 已移除飞书日报功能
    for section in ("qq_game_sentiment",):
        block = analysis.get(section)
        if not isinstance(block, dict):
            continue
        file_key = "task_system_prompt_file"
        if file_key in block and block.get(file_key):
            block["task_system_prompt"] = _resolve_text(f"@file:{block.pop(file_key)}", base_dir=_CONFIG_DIR)
        merge_file = "merge_batch_system_prompt_file"
        if merge_file in block and block.get(merge_file):
            block["merge_batch_system_prompt"] = _resolve_text(
                f"@file:{block.pop(merge_file)}", base_dir=_CONFIG_DIR
            )
    return _strip_meta(analysis)


def _build_napcat_integration(master: Dict[str, Any]) -> Dict[str, Any]:
    nap = _strip_meta(dict(master.get("napcat_integration") or {}))
    ports = master.get("ports") or {}
    if ports:
        nap["bot_port"] = int(ports.get("bot", nap.get("bot_port", 5000)))
        nap["napcat_http_port"] = int(ports.get("napcat_http", nap.get("napcat_http_port", 3000)))
    return nap


def _build_napcat_bridge(master: Dict[str, Any]) -> Dict[str, Any]:
    bridge = deepcopy(master.get("napcat_bridge") or {})
    ports = master.get("ports") or {}
    bot = bridge.get("bot") if isinstance(bridge.get("bot"), dict) else {}
    nap = bridge.get("napcat") if isinstance(bridge.get("napcat"), dict) else {}
    if ports:
        bot["port"] = int(ports.get("bot", bot.get("port", 5000)))
        nap["http_port"] = int(ports.get("napcat_http", nap.get("http_port", 3000)))
    bridge["bot"] = bot
    bridge["napcat"] = nap
    return _strip_meta(bridge)


def _apply_persona_block(
    block: Dict[str, Any],
    existing_path: str,
) -> Dict[str, Any]:
    """合并人格标量字段；长文默认保留磁盘已有 personality_long_text（避免用说明文档 .md 覆盖）。"""
    existing = _read_json(existing_path)
    out = dict(existing)
    skip = {"long_text_file", "personality_long_text"}
    out.update({k: v for k, v in block.items() if k not in skip})
    long_file = block.get("long_text_file")
    if long_file:
        out["personality_long_text"] = _resolve_text(f"@file:{long_file}", base_dir=_CONFIG_DIR)
    elif block.get("personality_long_text"):
        out["personality_long_text"] = block["personality_long_text"]
    return out


def _build_persona_runtime(master: Dict[str, Any], existing_path: str) -> Dict[str, Any]:
    block = _strip_meta(dict((master.get("persona") or {}).get("runtime") or {}))
    return _apply_persona_block(block, existing_path)


def _build_persona_qq(master: Dict[str, Any], existing_path: str) -> Dict[str, Any]:
    block = _strip_meta(dict((master.get("persona") or {}).get("qq_analysis") or {}))
    return _apply_persona_block(block, existing_path)


def apply_master_config(*, master_path: Optional[str] = None, reload_runtime: bool = False) -> Dict[str, Any]:
    """
    将配置主表写入各 JSON。返回 {ok, written: [paths], errors: []}。
    """
    written: List[str] = []
    errors: List[str] = []
    try:
        master = _load_master(master_path)
    except Exception as exc:  # pylint: disable=broad-except
        return {"ok": False, "written": [], "errors": [str(exc)]}

    meta = _meta_block()
    targets: List[Tuple[str, Dict[str, Any]]] = [
        (os.path.join(_CONFIG_DIR, "bot_config.json"), _build_bot_config(master)),
        (os.path.join(_CONFIG_DIR, "models.json"), _build_models(master)),
        (os.path.join(_CONFIG_DIR, "feishu_bot.json"), _build_feishu_bot(master)),
        (os.path.join(_CONFIG_DIR, "analysis_config.json"), _build_analysis(master)),
        (os.path.join(_CONFIG_DIR, "napcat_integration.json"), _build_napcat_integration(master)),
        (os.path.join(_CONFIG_DIR, "napcat_bridge.json"), _build_napcat_bridge(master)),
        (
            os.path.join(_CONFIG_DIR, "persona_runtime.json"),
            _build_persona_runtime(master, os.path.join(_CONFIG_DIR, "persona_runtime.json")),
        ),
        (
            os.path.join(_CONFIG_DIR, "persona_qq_analysis.json"),
            _build_persona_qq(master, os.path.join(_CONFIG_DIR, "persona_qq_analysis.json")),
        ),
    ]

    for path, payload in targets:
        try:
            _write_json(path, payload, meta=meta)
            written.append(os.path.relpath(path, _PROJECT_ROOT))
            logging.info("配置主表已写入 %s", path)
        except Exception as exc:  # pylint: disable=broad-except
            errors.append(f"{path}: {exc}")

    if reload_runtime and not errors:
        try:
            from config.config_manager import reload_config
            from botskill.analysis.config import load_analysis_config
            from botskill.persona_sync import reload_qq_analysis_from_disk

            reload_config()
            load_analysis_config(reload=True)
            reload_qq_analysis_from_disk()
        except Exception as exc:  # pylint: disable=broad-except
            errors.append(f"热重载失败: {exc}")

    return {"ok": not errors, "written": written, "errors": errors}
