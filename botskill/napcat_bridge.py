# -*- coding: utf-8 -*-
"""
NapCat ↔ 机器人通用桥接：单一配置文件定义端口/回调，换 QQ 号时自动识别并写入对应 onebot11_*.json。

配置文件：config/napcat_bridge.json
运行状态：config/napcat_bridge_state.json（自动生成）
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from botskill.paths import PROJECT_ROOT

BRIDGE_PATH = os.path.join(PROJECT_ROOT, "config", "napcat_bridge.json")
BRIDGE_STATE_PATH = os.path.join(PROJECT_ROOT, "config", "napcat_bridge_state.json")
INTEGRATION_PATH = os.path.join(PROJECT_ROOT, "config", "napcat_integration.json")
BOT_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "bot_config.json")

_ONEBOT_FILE_RE = re.compile(r"^onebot11_(\d+)\.json$", re.IGNORECASE)


@dataclass
class BridgeSettings:
    bot_host: str
    bot_port: int
    bot_callback_path: str
    bot_token: str
    napcat_host: str
    napcat_http_port: int
    napcat_token: str
    http_server_name: str
    http_client_name: str
    apply_to_all_accounts: bool
    preferred_uin: str
    auto_detect_active_uin: bool
    sync_bot_config: bool

    @property
    def api_base(self) -> str:
        return f"http://{self.napcat_host}:{self.napcat_http_port}"

    @property
    def callback_url(self) -> str:
        path = self.bot_callback_path
        if not path.startswith("/"):
            path = "/" + path
        return f"http://{self.bot_host}:{self.bot_port}{path}"

    def api_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.napcat_token:
            headers["Authorization"] = f"Bearer {self.napcat_token}"
        return headers


def _read_json(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def load_bridge() -> dict:
    data = _read_json(BRIDGE_PATH)
    if not data:
        raise FileNotFoundError(
            f"未找到桥接配置 {BRIDGE_PATH}，请复制 config/napcat_bridge.example.json 为 napcat_bridge.json"
        )
    return data


def load_integration() -> dict:
    return _read_json(INTEGRATION_PATH)


def load_bridge_state() -> dict:
    return _read_json(BRIDGE_STATE_PATH)


def save_bridge_state(state: dict) -> None:
    os.makedirs(os.path.dirname(BRIDGE_STATE_PATH), exist_ok=True)
    with open(BRIDGE_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def bridge_settings_from(
    bridge: Optional[dict] = None,
    integration: Optional[dict] = None,
) -> BridgeSettings:
    """合并 bridge（主）与 integration（兼容旧字段）。"""
    bridge = bridge or load_bridge()
    integration = integration or load_integration()

    bot = bridge.get("bot") if isinstance(bridge.get("bot"), dict) else {}
    napcat = bridge.get("napcat") if isinstance(bridge.get("napcat"), dict) else {}
    names = bridge.get("names") if isinstance(bridge.get("names"), dict) else {}

    bot_port = int(bot.get("port") or integration.get("bot_port") or 5000)
    napcat_port = int(
        napcat.get("http_port") or integration.get("napcat_http_port") or 3000
    )
    callback_path = str(
        bot.get("callback_path") or integration.get("bot_callback_path") or "/onebot"
    ).strip()

    preferred = str(bridge.get("preferred_uin") or integration.get("qq_uin") or "").strip()
    napcat_token = str(napcat.get("token") or integration.get("napcat_http_token") or "").strip()
    bot_token = str(bot.get("token") or "").strip() or napcat_token

    return BridgeSettings(
        bot_host=str(bot.get("host") or "127.0.0.1").strip() or "127.0.0.1",
        bot_port=bot_port,
        bot_callback_path=callback_path,
        bot_token=bot_token,
        napcat_host=str(napcat.get("host") or "127.0.0.1").strip() or "127.0.0.1",
        napcat_http_port=napcat_port,
        napcat_token=napcat_token,
        http_server_name=str(names.get("http_server") or "qinglan-onebot-api"),
        http_client_name=str(names.get("http_client") or "lingri-bot-callback"),
        apply_to_all_accounts=bool(bridge.get("apply_to_all_accounts", True)),
        preferred_uin=preferred,
        auto_detect_active_uin=bool(bridge.get("auto_detect_active_uin", True)),
        sync_bot_config=bool(bridge.get("sync_bot_config", True)),
    )


def build_network(settings: BridgeSettings) -> dict:
    return {
        "httpServers": [
            {
                "name": settings.http_server_name,
                "enable": True,
                "host": settings.napcat_host,
                "port": settings.napcat_http_port,
                "enableCors": True,
                "enableWebsocket": False,
                "messagePostFormat": "array",
                "token": settings.napcat_token,
                "debug": False,
                "reportSelfMessage": False,
            }
        ],
        "httpSseServers": [],
        "httpClients": [
            {
                "name": settings.http_client_name,
                "enable": True,
                "url": settings.callback_url,
                "messagePostFormat": "array",
                "reportSelfMessage": False,
                "token": settings.bot_token,
                "debug": False,
            }
        ],
        "websocketServers": [],
        "websocketClients": [],
        "plugins": [],
    }


def napcat_config_dir(napcat_root: str) -> str:
    from botskill.napcat_paths import napcat_config_dir as _dir

    return _dir(napcat_root)


def list_onebot_config_paths(cfg_dir: str) -> List[Tuple[str, str]]:
    """返回 [(uin, abs_path), ...]"""
    out: List[Tuple[str, str]] = []
    if not os.path.isdir(cfg_dir):
        return out
    for name in os.listdir(cfg_dir):
        m = _ONEBOT_FILE_RE.match(name)
        if not m:
            continue
        out.append((m.group(1), os.path.join(cfg_dir, name)))
    return out


def resolve_active_uin(
    settings: BridgeSettings,
    cfg_dir: str,
    *,
    explicit_uin: Optional[str] = None,
) -> Optional[int]:
    if explicit_uin:
        try:
            return int(str(explicit_uin).strip())
        except ValueError:
            pass

    if settings.auto_detect_active_uin:
        from scripts.napcat_detect import try_get_logged_in_uin

        uin = try_get_logged_in_uin(settings.api_base, settings.api_headers())
        if uin is not None:
            return uin

        from scripts.napcat_detect import newest_onebot_uin

        uin = newest_onebot_uin(cfg_dir, max_age_sec=86400 * 7)
        if uin is not None:
            return uin

    if settings.preferred_uin:
        try:
            return int(settings.preferred_uin)
        except ValueError:
            pass

    state = load_bridge_state()
    last = str(state.get("last_active_uin") or "").strip()
    if last.isdigit():
        return int(last)

    integration = load_integration()
    legacy = str(integration.get("qq_uin") or "").strip()
    if legacy.isdigit():
        return int(legacy)

    return None


def wait_for_onebot_config(
    napcat_root: str,
    uin: str,
    timeout_sec: int = 120,
) -> str:
    cfg_dir = napcat_config_dir(napcat_root)
    deadline = time.time() + timeout_sec
    path = os.path.join(cfg_dir, f"onebot11_{uin}.json")
    while time.time() < deadline:
        if os.path.isfile(path):
            return path
        time.sleep(1.5)
    raise FileNotFoundError(
        f"等待 onebot11_{uin}.json 超时；请确认 NapCat 已登录成功。"
    )


def _write_network_to_file(onebot_path: str, network: dict) -> None:
    if os.path.isfile(onebot_path):
        with open(onebot_path, "r", encoding="utf-8") as f:
            onebot = json.load(f)
        if not isinstance(onebot, dict):
            onebot = {}
    else:
        onebot = {}
    onebot["network"] = network
    os.makedirs(os.path.dirname(onebot_path), exist_ok=True)
    with open(onebot_path, "w", encoding="utf-8") as f:
        json.dump(onebot, f, ensure_ascii=False, indent=2)
        f.write("\n")


def sync_bot_config(settings: BridgeSettings, active_uin: int) -> None:
    if not settings.sync_bot_config or not os.path.isfile(BOT_CONFIG_PATH):
        return
    with open(BOT_CONFIG_PATH, "r", encoding="utf-8") as f:
        bot_cfg = json.load(f)
    if not isinstance(bot_cfg, dict):
        return
    bot_cfg["onebot_http_api"] = settings.api_base
    bot_cfg["onebot_token"] = settings.napcat_token
    bot_cfg["onebot_self_id"] = active_uin
    bot_cfg["napcat_http_port"] = settings.napcat_http_port
    with open(BOT_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(bot_cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    try:
        from config import BOT_CONFIG

        BOT_CONFIG["onebot_http_api"] = settings.api_base
        BOT_CONFIG["onebot_token"] = settings.napcat_token
        BOT_CONFIG["onebot_self_id"] = active_uin
        BOT_CONFIG["napcat_http_port"] = settings.napcat_http_port
    except Exception:
        pass


def refresh_bot_runtime_self_id(quiet: bool = True) -> Optional[int]:
    """启动机器人前从 NapCat HTTP 拉取当前登录 QQ，避免 self_id 与实号不一致。"""
    try:
        settings = bridge_settings_from()
        from scripts.napcat_detect import try_get_logged_in_uin

        uin = try_get_logged_in_uin(settings.api_base, settings.api_headers())
        if uin is None:
            return None
        from config import BOT_CONFIG

        BOT_CONFIG["onebot_self_id"] = uin
        BOT_CONFIG["onebot_http_api"] = settings.api_base
        BOT_CONFIG["onebot_token"] = settings.napcat_token
        if not quiet:
            import logging

            logging.info("已从 NapCat 同步当前 QQ：%s", uin)
        return uin
    except Exception:
        return None


@dataclass
class ApplyResult:
    active_uin: int
    primary_path: str
    written_paths: List[str]


def apply_bridge(
    uin: Optional[str] = None,
    *,
    wait_config_sec: int = 120,
    bridge: Optional[dict] = None,
    integration: Optional[dict] = None,
) -> ApplyResult:
    """
    将通用桥接网络配置写入 NapCat onebot11_*.json，并同步机器人 bot_config。
    apply_to_all_accounts=true 时对所有已存在账号配置写入，换号不断链。
    """
    from botskill.napcat_paths import napcat_root_from_integration

    bridge = bridge or load_bridge()
    integration = integration or load_integration()
    settings = bridge_settings_from(bridge, integration)
    napcat_root = napcat_root_from_integration(integration)
    cfg_dir = napcat_config_dir(napcat_root)
    network = build_network(settings)

    explicit = str(uin).strip() if uin is not None else None
    active = resolve_active_uin(settings, cfg_dir, explicit_uin=explicit)

    targets: List[Tuple[str, str]] = []
    if settings.apply_to_all_accounts:
        targets = list_onebot_config_paths(cfg_dir)

    if active is not None:
        active_path = os.path.join(cfg_dir, f"onebot11_{active}.json")
        if not os.path.isfile(active_path):
            active_path = wait_for_onebot_config(
                napcat_root, str(active), timeout_sec=wait_config_sec
            )
        if not any(u == str(active) for u, _ in targets):
            targets.append((str(active), active_path))
    elif explicit:
        active_path = wait_for_onebot_config(
            napcat_root, explicit, timeout_sec=wait_config_sec
        )
        targets = [(explicit, active_path)]
        active = int(explicit)
    elif not targets:
        raise ValueError(
            "无法确定当前 QQ 号：请先在 NapCat 完成登录，或在 napcat_bridge.json 设置 preferred_uin"
        )

    if not settings.apply_to_all_accounts and active is not None:
        targets = [(str(active), os.path.join(cfg_dir, f"onebot11_{active}.json"))]

    written: List[str] = []
    for _, path in targets:
        _write_network_to_file(path, network)
        written.append(path)

    if active is None and targets:
        active = int(targets[0][0])

    if active is None:
        raise ValueError("桥接写入后仍无法确定 active_uin")

    primary = os.path.join(cfg_dir, f"onebot11_{active}.json")
    if primary not in written:
        if not os.path.isfile(primary):
            primary = wait_for_onebot_config(
                napcat_root, str(active), timeout_sec=wait_config_sec
            )
        _write_network_to_file(primary, network)
        written.append(primary)

    sync_bot_config(settings, active)

    save_bridge_state(
        {
            "last_active_uin": str(active),
            "last_linked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "callback_url": settings.callback_url,
            "napcat_api": settings.api_base,
            "linked_onebot_files": [os.path.basename(p) for p in written],
        }
    )

    return ApplyResult(active_uin=active, primary_path=primary, written_paths=written)


def link_summary(result: ApplyResult, settings: BridgeSettings) -> str:
    lines = [
        f"当前 QQ：{result.active_uin}",
        f"已写入 {len(result.written_paths)} 个 NapCat 账号配置",
        f"主配置：{result.primary_path}",
        f"NapCat HTTP API：{settings.api_base}",
        f"机器人回调：{settings.callback_url}",
    ]
    return "\n".join(lines)
