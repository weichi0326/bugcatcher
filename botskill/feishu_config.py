# -*- coding: utf-8 -*-
"""飞书机器人配置路径解析（与 QQ 归档配置分离）。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from botskill.paths import PROJECT_ROOT
from config import BOT_CONFIG

DEFAULT_FEISHU_CONFIG_REL = os.path.join("config", "feishu_bot.json")


def resolve_feishu_config_path() -> str:
    raw = str(BOT_CONFIG.get("feishu_config_path") or DEFAULT_FEISHU_CONFIG_REL).strip()
    if not raw:
        raw = DEFAULT_FEISHU_CONFIG_REL
    if not os.path.isabs(raw):
        raw = os.path.join(PROJECT_ROOT, raw.replace("/", os.sep))
    return os.path.abspath(os.path.normpath(raw))


def load_feishu_config() -> Dict[str, Any]:
    path = resolve_feishu_config_path()
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}
