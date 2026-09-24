# -*- coding: utf-8 -*-
"""NapCat 安装目录解析：支持相对项目根的路径（推荐 napcat/NapCat.Shell.Windows.OneKey）。"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from botskill.paths import PROJECT_ROOT

# 项目内独立目录，与 config、conversation_logs 同级
DEFAULT_NAPCAT_REL = os.path.join("napcat", "NapCat.Shell.Windows.OneKey")


def resolve_napcat_root(raw: Optional[str] = None, integration: Optional[Dict[str, Any]] = None) -> str:
    """将 napcat_root 解析为绝对路径；相对路径相对于项目根。"""
    path = str(raw or "").strip()
    if not path and integration:
        path = str(integration.get("napcat_root") or "").strip()
    if not path:
        path = DEFAULT_NAPCAT_REL
    if not os.path.isabs(path):
        path = os.path.join(PROJECT_ROOT, path.replace("/", os.sep))
    return os.path.abspath(os.path.normpath(path))


def napcat_root_from_integration(integration: dict) -> str:
    return resolve_napcat_root(integration=integration)


def find_shell_dir(napcat_root: str) -> str:
    """查找 OneKey 安装器生成的 NapCat.XXXX.Shell，兼容 QQ 版本变化。"""
    if not os.path.isdir(napcat_root):
        raise FileNotFoundError(f"未找到 NapCat 目录：{napcat_root}")
    candidates = []
    for name in os.listdir(napcat_root):
        if re.fullmatch(r"NapCat\.\d+\.Shell", name, re.IGNORECASE):
            path = os.path.join(napcat_root, name)
            if os.path.isfile(os.path.join(path, "NapCatWinBootMain.exe")):
                candidates.append((int(name.split(".")[1]), path))
    if candidates:
        return max(candidates)[1]
    fallback = os.path.join(napcat_root, "bootmain")
    if os.path.isfile(os.path.join(fallback, "NapCatWinBootMain.exe")):
        return fallback
    raise FileNotFoundError(f"未找到 NapCatWinBootMain.exe：{napcat_root}")


def napcat_config_dir(napcat_root: str) -> str:
    """返回 versions/.../resources/app/napcat/config 目录。"""
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
