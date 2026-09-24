# -*- coding: utf-8 -*-
"""NapCat 登录状态检测（配置文件 + HTTP API 双通道）。"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from typing import Dict, Optional

import requests

_ONEOBOT_FILE_RE = re.compile(r"^onebot11_(\d+)\.json$", re.IGNORECASE)


def napcat_config_dir(napcat_root: str) -> str:
    from botskill.napcat_paths import find_shell_dir

    shell = os.path.join(find_shell_dir(napcat_root), "versions")
    if not os.path.isdir(shell):
        raise FileNotFoundError(f"未找到 NapCat versions 目录：{shell}")
    versions = sorted(
        [d for d in os.listdir(shell) if os.path.isdir(os.path.join(shell, d))],
        reverse=True,
    )
    if not versions:
        raise FileNotFoundError(f"NapCat versions 为空：{shell}")
    cfg = os.path.join(shell, versions[0], "resources", "app", "napcat", "config")
    if not os.path.isdir(cfg):
        raise FileNotFoundError(f"未找到 napcat config：{cfg}")
    return cfg


def snapshot_onebot_configs(cfg_dir: str) -> Dict[str, float]:
    snap: Dict[str, float] = {}
    if not os.path.isdir(cfg_dir):
        return snap
    for name in os.listdir(cfg_dir):
        if _ONEOBOT_FILE_RE.match(name):
            snap[name] = os.path.getmtime(os.path.join(cfg_dir, name))
    return snap


def _uin_from_filename(name: str) -> Optional[int]:
    m = _ONEOBOT_FILE_RE.match(name)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def detect_login_from_config_changes(
    cfg_dir: str,
    baseline: Dict[str, float],
    since_ts: float,
) -> Optional[int]:
    """根据 onebot11_{uin}.json 新增或更新判断已登录。"""
    best_uin: Optional[int] = None
    best_mtime = 0.0
    for name in os.listdir(cfg_dir):
        if not _ONEOBOT_FILE_RE.match(name):
            continue
        path = os.path.join(cfg_dir, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        uin = _uin_from_filename(name)
        if uin is None:
            continue
        prev = baseline.get(name)
        changed = prev is None or mtime > prev + 0.5
        if not changed:
            continue
        if mtime < since_ts - 60:
            continue
        if mtime > best_mtime:
            best_mtime = mtime
            best_uin = uin
    return best_uin


def newest_onebot_uin(cfg_dir: str, max_age_sec: int = 600) -> Optional[int]:
    """取最近修改的 onebot 配置对应的 QQ 号（用于 NapCat 已先启动的情况）。"""
    now = time.time()
    best_uin: Optional[int] = None
    best_mtime = 0.0
    for name in os.listdir(cfg_dir):
        if not _ONEOBOT_FILE_RE.match(name):
            continue
        path = os.path.join(cfg_dir, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if now - mtime > max_age_sec:
            continue
        uin = _uin_from_filename(name)
        if uin is None:
            continue
        if mtime > best_mtime:
            best_mtime = mtime
            best_uin = uin
    return best_uin


def parse_login_from_ob11_response(data: dict) -> Optional[int]:
    if not isinstance(data, dict):
        return None
    if data.get("status") not in ("ok", "success", None) or data.get("retcode") not in (0, None):
        return None
    payload = data.get("data")
    if not isinstance(payload, dict):
        payload = data
    for key in ("user_id", "userId", "uin"):
        val = payload.get(key) if isinstance(payload, dict) else None
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                continue
    return None


def try_get_logged_in_uin(api_base: str, headers: dict) -> Optional[int]:
    """OneBot HTTP get_login_info（需 HTTP 服务端已启用）。"""
    url = f"{api_base.rstrip('/')}/get_login_info"
    for method in ("post", "get"):
        try:
            if method == "post":
                resp = requests.post(url, json={}, headers=headers, timeout=5)
            else:
                resp = requests.get(url, headers=headers, timeout=5)
            data = resp.json() if resp.text else {}
            uin = parse_login_from_ob11_response(data if isinstance(data, dict) else {})
            if uin is not None:
                return uin
        except requests.RequestException:
            continue
    return None


def napcat_chain_ready(api_base: str, headers: dict) -> tuple[bool, Optional[int]]:
    """NapCat HTTP 已监听且已登录时返回 (True, uin)。"""
    if not wait_http_server_ready(api_base, timeout_sec=3):
        return False, None
    uin = try_get_logged_in_uin(api_base, headers)
    if uin is None:
        return False, None
    return True, uin


def napcat_process_running() -> bool:
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq NapCatWinBootMain.exe"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return "NapCatWinBootMain.exe" in (out.stdout or "")
    except Exception:
        return False


def wait_http_server_ready(api_base: str, timeout_sec: int = 90) -> bool:
    """等待 OneBot HTTP 服务可访问（GET / 返回 NapCat 运行中）。"""
    url = f"{api_base.rstrip('/')}/"
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            resp = requests.post(url, json={}, timeout=3)
            text = (resp.text or "").lower()
            if resp.status_code == 200 and ("napcat" in text or "running" in text or resp.json()):
                return True
        except requests.RequestException:
            pass
        time.sleep(1.5)
    return False


def wait_tcp_port(
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout_sec: float = 90.0,
) -> bool:
    """等待本机 TCP 端口可连接（机器人 /onebot 已 bind）。"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with socket.create_connection((host, int(port)), timeout=1.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False
