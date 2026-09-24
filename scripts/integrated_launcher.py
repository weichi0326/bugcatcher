# -*- coding: utf-8 -*-
"""
一键整合启动：先启动机器人；NapCat 登录与桥接在后台完成。
"""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.request import Request, urlopen

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

INTEGRATION_PATH = os.path.join(ROOT, "config", "napcat_integration.json")
_WEBUI_AUTH: dict = {"port": None, "token_hash": None, "credential": "", "at": 0.0}


def _setup_stdio() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _log(msg: str) -> None:
    text = str(msg)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"), flush=True)


def load_integration() -> dict:
    with open(INTEGRATION_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def resolve_napcat_launcher(integration: dict) -> Tuple[str, str]:
    from botskill.napcat_paths import find_shell_dir, napcat_root_from_integration

    napcat_root = napcat_root_from_integration(integration)
    launch_dir = find_shell_dir(napcat_root)
    return launch_dir, os.path.join(launch_dir, "NapCatWinBootMain.exe")


def disable_saved_auto_login(integration: dict) -> None:
    """启动前清除 NapCat 的自动登录账号，让新登录走二维码。"""
    from botskill.napcat_paths import napcat_config_dir, napcat_root_from_integration

    path = os.path.join(napcat_config_dir(napcat_root_from_integration(integration)), "webui.json")
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8-sig") as file:
        config = json.load(file)
    if not isinstance(config, dict) or not config.get("autoLoginAccount"):
        return
    config["autoLoginAccount"] = ""
    temporary = path + ".scan.tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temporary, path)


def start_napcat(integration: dict) -> subprocess.Popen:
    launch_dir, exe_path = resolve_napcat_launcher(integration)
    disable_saved_auto_login(integration)
    args = [exe_path]
    _log(f"      启动 NapCat 并等待扫码：{exe_path}")
    # NapCat 保持后台运行，登录二维码统一由 WebUI 展示。
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0
    log_path = os.path.join(ROOT, "log", "napcat_service.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    env = os.environ.copy()
    env.update({"NO_COLOR": "1", "FORCE_COLOR": "0", "TERM": "dumb"})
    with open(log_path, "ab") as output:
        return subprocess.Popen(args, cwd=launch_dir, env=env,
                                stdout=output, stderr=subprocess.STDOUT, creationflags=flags)


def _bridge_settings(integration: dict):
    from botskill.napcat_bridge import bridge_settings_from

    return bridge_settings_from(integration=integration)


def _api_base(integration: dict) -> str:
    return _bridge_settings(integration).api_base


def _api_headers(integration: dict) -> Dict[str, str]:
    return _bridge_settings(integration).api_headers()


def wait_for_qq_login(integration: dict, cfg_dir: str, baseline: Dict[str, float], since_ts: float) -> int:
    from scripts.napcat_detect import (
        detect_login_from_config_changes,
        try_get_logged_in_uin,
    )

    api_base = _api_base(integration)
    headers = _api_headers(integration)
    timeout = int(integration.get("login_wait_timeout_sec") or 600)
    poll = float(integration.get("login_poll_interval_sec") or 2.0)
    deadline = time.time() + timeout

    _log("      等待 QQ 登录（扫码/密码），检测配置文件与 HTTP API …")
    last_hint = 0.0
    while time.time() < deadline:
        try:
            uin = current_webui_qq(cfg_dir)
            if uin is not None:
                _log(f"      QQ 已登录（NapCat WebUI）：{uin}")
                return uin
        except (OSError, ValueError, RuntimeError):
            pass
        uin = try_get_logged_in_uin(api_base, headers)
        if uin is not None:
            _log(f"      QQ 已登录（HTTP）：{uin}")
            return uin
        uin = detect_login_from_config_changes(cfg_dir, baseline, since_ts)
        if uin is not None:
            _log(f"      QQ 已登录（配置 onebot11_{uin}.json）：{uin}")
            return uin
        now = time.time()
        if now - last_hint >= 15:
            _log("      …请在 NapCat/QQ 窗口完成登录 …")
            last_hint = now
        time.sleep(poll)
    raise TimeoutError("等待 QQ 登录超时，请确认 NapCat 已成功登录后重试。")


def write_network_config(uin: int, integration: dict) -> str:
    from botskill.napcat_bridge import apply_bridge, link_summary, bridge_settings_from

    settings = bridge_settings_from(integration=integration)
    result = apply_bridge(
        uin=str(uin),
        wait_config_sec=int(integration.get("config_wait_sec") or 90),
        integration=integration,
    )
    _log("      " + link_summary(result, settings).replace("\n", "\n      "))
    return result.primary_path


def _napcat_webui_call(cfg_dir: str, path: str, body: dict) -> dict:
    with open(os.path.join(cfg_dir, "webui.json"), "r", encoding="utf-8") as file:
        webui = json.load(file)
    token = str(webui.get("token") or "")
    port = int(webui.get("port") or 6099)
    if not token or webui.get("disableWebUI") or not 1 <= port <= 65535:
        raise RuntimeError("NapCat WebUI 未启用，无法热加载桥接配置")

    def post(endpoint: str, payload: dict, credential: str = "") -> dict:
        headers = {"Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        request = Request(f"http://127.0.0.1:{port}{endpoint}", data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urlopen(request, timeout=5) as response:
            result = json.load(response)
        if not isinstance(result, dict) or result.get("code") != 0:
            detail = result.get("message") if isinstance(result, dict) else "响应格式错误"
            raise RuntimeError(f"NapCat WebUI 配置接口失败：{detail}")
        return result

    digest = hashlib.sha256((token + ".napcat").encode("utf-8")).hexdigest()
    if (_WEBUI_AUTH["port"] != port or _WEBUI_AUTH["token_hash"] != digest
            or time.time() - _WEBUI_AUTH["at"] > 3000):
        auth = post("/api/auth/login", {"hash": digest}).get("data") or {}
        credential = auth.get("Credential") if isinstance(auth, dict) else None
        if not credential:
            raise RuntimeError("NapCat WebUI 登录需要额外验证，无法自动管理桥接配置")
        _WEBUI_AUTH.update({"port": port, "token_hash": digest, "credential": str(credential), "at": time.time()})
    try:
        return post(path, body, _WEBUI_AUTH["credential"])
    except RuntimeError:
        _WEBUI_AUTH["at"] = 0.0
        raise


def hot_reload_network_config(onebot_path: str) -> None:
    """通过 NapCat WebUI 实时加载账号网络配置，不靠重启进程。"""
    with open(onebot_path, "r", encoding="utf-8") as file:
        onebot = json.load(file)
    _napcat_webui_call(os.path.dirname(onebot_path), "/api/OB11Config/SetConfig",
                       {"config": json.dumps(onebot, ensure_ascii=False)})


def current_webui_qq(cfg_dir: str) -> Optional[int]:
    """账号切换时 OneBot 端口可能短暂消失，优先用 NapCat WebUI 确认新账号。"""
    status = _napcat_webui_call(cfg_dir, "/api/QQLogin/CheckLoginStatus", {}).get("data") or {}
    if not isinstance(status, dict) or not status.get("isLogin"):
        return None
    info = _napcat_webui_call(cfg_dir, "/api/QQLogin/GetQQLoginInfo", {}).get("data") or {}
    if not isinstance(info, dict):
        return None
    uin = str(info.get("uin") or "")
    return int(uin) if uin.isdigit() else None


def ensure_login_qrcode(cfg_dir: str, since_ts: float, timeout_sec: float = 15.0) -> None:
    """二维码迟迟未生成时，让 NapCat 原进程刷新扫码登录。"""
    qr_path = os.path.join(os.path.dirname(cfg_dir), "cache", "qrcode.png")
    deadline = time.monotonic() + timeout_sec
    login_error = ""
    while time.monotonic() < deadline:
        try:
            status = _napcat_webui_call(cfg_dir, "/api/QQLogin/CheckLoginStatus", {}).get("data") or {}
            if isinstance(status, dict):
                if status.get("isLogin"):
                    return
                login_error = str(status.get("loginError") or "")
            if os.path.isfile(qr_path) and os.path.getmtime(qr_path) >= since_ts - 2:
                return
            if login_error:
                break
        except (OSError, ValueError, RuntimeError):
            pass
        time.sleep(1)
    _log(f"      QQ 登录未生成二维码{f'（{login_error}）' if login_error else ''}，正在请求 NapCat 刷新二维码…")
    try:
        _napcat_webui_call(cfg_dir, "/api/QQLogin/RefreshQRcode", {})
    except (OSError, ValueError, RuntimeError) as exc:
        _log(f"      自动刷新二维码失败：{exc}；可在 NapCat WebUI 的 QQ 登录页手动刷新。")


def ensure_napcat_http_bridge(
    integration: dict,
    napcat_proc: Optional[subprocess.Popen],
    uin: int,
) -> subprocess.Popen:
    """等待 NapCat HTTP 返回目标 QQ；配置由 WebUI 热加载，过程不重启 QQ。"""
    from scripts.napcat_detect import napcat_chain_ready, wait_http_server_ready

    api_base = _api_base(integration)
    headers = _api_headers(integration)
    bot_port = _bridge_settings(integration).bot_port

    http_up = wait_http_server_ready(api_base, timeout_sec=3)
    if http_up:
        ready, detected = napcat_chain_ready(api_base, headers)
        if ready and detected == uin:
            _log("      NapCat HTTP 已就绪，消息可上报到机器人。")
            return napcat_proc
        _log(f"      NapCat HTTP 已监听，但当前 QQ 为 {detected or '未知'}；等待账号 {uin} 就绪…")

    _log("      正在等待 NapCat HTTP 加载已热更新的桥接配置，不重启 QQ…")

    deadline = time.time() + int(integration.get("http_ready_timeout_sec") or 120)
    while time.time() < deadline:
        if wait_http_server_ready(api_base, timeout_sec=3):
            ready, detected = napcat_chain_ready(api_base, headers)
            if ready and detected == uin:
                _log(f"      NapCat HTTP 已就绪（QQ {uin}），通信链路已打通。")
                return napcat_proc
        time.sleep(2)

    raise RuntimeError(
        "NapCat HTTP 仍未就绪；NapCat 进程未重启，QQ 登录状态已保留。请打开 NapCat WebUI → 网络配置 → 确认 "
        f"HTTP 服务端 {integration.get('napcat_http_port', 3000)} 与 HTTP 客户端 "
        f"http://127.0.0.1:{bot_port}/onebot 均已启用 → 保存/重载，然后在 WebUI 重新启动全部。"
    )


def main() -> int:
    _setup_stdio()
    _log("=" * 50)
    _log("  QQ 群聊分析机器人 · 整合服务")
    _log("=" * 50)

    managed = os.environ.get("QQ_WEBUI_MANAGED") == "1"
    if not managed:
        from scripts.project_processes import replace_existing

        try:
            stopped = replace_existing(Path(ROOT), ("scripts/integrated_launcher.py", "bot.py"))
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            _log(f"启动失败：旧项目进程未能关闭：{exc}")
            return 1
        if stopped:
            _log(f"[0/4] 已关闭旧项目进程：{', '.join(map(str, stopped))}")

    from scripts.napcat_detect import (
        napcat_chain_ready,
        napcat_config_dir,
        napcat_process_running,
        snapshot_onebot_configs,
        wait_http_server_ready,
    )

    integration = load_integration()
    from botskill.napcat_paths import napcat_root_from_integration

    from botskill.reload_watch import AI_RESTART_EXIT_CODE, INTEGRATED_ENV
    from scripts.napcat_detect import wait_tcp_port

    bot_port = int(_bridge_settings(integration).bot_port)
    env = os.environ.copy()
    env[INTEGRATED_ENV] = "1"
    bot_proc: Optional[subprocess.Popen] = None
    qq_state: dict = {"uin": None, "cfg_dir": None, "ready": False}

    def start_bot() -> subprocess.Popen:
        _log("[1/4] 启动机器人；飞书连接无需等待 QQ 扫码…")
        return subprocess.Popen([sys.executable, os.path.join(ROOT, "bot.py")], cwd=ROOT, env=env)

    def connect_qq() -> None:
        napcat_proc: Optional[subprocess.Popen] = None
        try:
            from napcat_setup import installation_status

            if not installation_status(Path(ROOT))["installed"]:
                _log("[2/4] QQNT / NapCat 尚未安装；飞书已启动。可在 QQ 管理页安装，安装后会继续启动 QQ。")
            while not installation_status(Path(ROOT))["installed"]:
                time.sleep(5)
            napcat_root = napcat_root_from_integration(integration)
            cfg_dir = napcat_config_dir(napcat_root)
            qq_state["cfg_dir"] = cfg_dir
            api_base = _api_base(integration)
            headers = _api_headers(integration)
            baseline = snapshot_onebot_configs(cfg_dir)
            ready, uin = napcat_chain_ready(api_base, headers)
            http_up = wait_http_server_ready(api_base, timeout_sec=3)
            if ready and uin is not None and napcat_process_running():
                _log(f"[2/4] NapCat 已在运行且 HTTP 正常（QQ {uin}）。")
            else:
                if napcat_process_running() and not http_up:
                    _log("[2/4] NapCat 已运行，等待账号登录后热加载配置，不重启 QQ。")
                elif napcat_process_running():
                    _log("[2/4] NapCat 已运行，等待登录，不重复启动。")
                else:
                    _log("[2/4] 正在启动 NapCat…")
                    napcat_proc = start_napcat(integration)
                _log("      登录二维码将在管理页显示")
                since_ts = time.time()
                if uin is None:
                    ensure_login_qrcode(cfg_dir, since_ts)
                    while uin is None:
                        try:
                            uin = wait_for_qq_login(integration, cfg_dir, baseline, since_ts)
                        except TimeoutError:
                            _log("      QQ 尚未登录，继续等待扫码；飞书保持运行。")
            _log("[3/4] 等待 QQ 归档接收端，然后写入桥接配置…")
            if not wait_tcp_port("127.0.0.1", bot_port, timeout_sec=120.0):
                raise RuntimeError(f"机器人归档接收端 :{bot_port} 未就绪")
            path = write_network_config(uin, integration)
            _log(f"[4/4] 已写入 NapCat 桥接配置：{path}")
            try:
                hot_reload_network_config(path)
                _log("      NapCat 网络配置已热加载，无需重新扫码。")
            except Exception as exc:
                _log(f"      网络配置热加载未完成：{exc}；QQ 保持登录，可到运行管理重写连接配置并重新启动全部服务。")
            ensure_napcat_http_bridge(integration, napcat_proc, uin)
            qq_state["uin"] = uin
            qq_state["ready"] = True
            _log("      QQ 归档链路已就绪；飞书与 QQ 均可使用。")
        except Exception as exc:
            _log(f"      QQ 启动或桥接失败：{exc}；飞书继续运行。")

    try:
        bot_proc = start_bot()
        threading.Thread(target=connect_qq, name="qq-login-and-bridge", daemon=True).start()
        last_account_check = 0.0
        last_account_error = 0.0
        while True:
            observed_code = bot_proc.poll()
            if observed_code is None:
                if qq_state["ready"] and time.monotonic() - last_account_check >= 5:
                    last_account_check = time.monotonic()
                    try:
                        current_uin = current_webui_qq(qq_state["cfg_dir"])
                        if current_uin is not None and current_uin != qq_state["uin"]:
                            _log(f"      检测到 QQ 切换为 {current_uin}，正在更新桥接…")
                            new_path = write_network_config(current_uin, integration)
                            qq_state["uin"] = current_uin
                            try:
                                hot_reload_network_config(new_path)
                                _log("      新账号桥接已热加载，无需重启或再次扫码。")
                            except Exception as exc:
                                _log(f"      新账号桥接热加载失败：{exc}；QQ 保持登录，请到运行管理重写连接配置并重新启动全部服务。")
                    except Exception as exc:
                        if time.monotonic() - last_account_error >= 60:
                            _log(f"      QQ 切换状态暂不可用：{exc}")
                            last_account_error = time.monotonic()
                time.sleep(2)
                continue
            code = int(observed_code or 0)
            if code == AI_RESTART_EXIT_CODE:
                _log("机器人进程正在重启；NapCat 登录态保持不变…")
                time.sleep(0.4)
                bot_proc = start_bot()
                wait_tcp_port("127.0.0.1", bot_port, timeout_sec=90.0)
                continue
            return code
    except KeyboardInterrupt:
        _log("\n用户中断。")
        return 130
    except Exception as exc:
        _log(f"\n启动失败：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
