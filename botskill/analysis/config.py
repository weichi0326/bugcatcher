# -*- coding: utf-8 -*-
"""加载 config/analysis_config.json。"""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from typing import Any, Dict, List

from botskill.paths import PROJECT_ROOT

_CONFIG_REL = os.path.join("config", "analysis_config.json")
_CONFIG_EXAMPLE_REL = os.path.join("config", "analysis_config.example.json")
_PATH = os.path.join(PROJECT_ROOT, _CONFIG_REL)
_PATH_EXAMPLE = os.path.join(PROJECT_ROOT, _CONFIG_EXAMPLE_REL)

_CACHE: Dict[str, Any] = {}


def _default_config() -> Dict[str, Any]:
    return {
        "archive_dir": "analysis_reports",
        "push_use_interactive_card": False,
        "push_card_header_template": "blue",
        "qq_game_sentiment": {
            "enabled": False,
            "evening_run_at": "23:00",
            "run_at": "23:00",
            "group_whitelist": [],
            "push_to_feishu": True,
            "feishu_push_chat_ids": [],
            "push_to_each_group": False,
            "max_messages_per_group": 0,
            "report_retention_days": 7,
            "transcript_max_bytes_per_batch": 32768,
            "task_system_prompt": "",
            "merge_batch_system_prompt": "",
        },
    }


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def load_analysis_config(*, reload: bool = False) -> Dict[str, Any]:
    global _CACHE  # noqa: PLW0603
    if _CACHE and not reload:
        return _CACHE
    if not os.path.isfile(_PATH):
        if os.path.isfile(_PATH_EXAMPLE):
            os.makedirs(os.path.dirname(_PATH), exist_ok=True)
            with open(_PATH_EXAMPLE, "r", encoding="utf-8") as src:
                with open(_PATH, "w", encoding="utf-8") as dst:
                    dst.write(src.read())
            logging.info("已从 example 生成 %s", _CONFIG_REL)
        else:
            _CACHE = _default_config()
            return _CACHE
    raw = _read_json(_PATH)
    merged = _default_config()
    for key in ("archive_dir", "push_use_interactive_card", "push_card_header_template"):
        if key in raw:
            merged[key] = raw[key]
    for key in ("qq_game_sentiment",):
        if key in raw:
            if isinstance(raw[key], dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **raw[key]}
            else:
                merged[key] = raw[key]
    _CACHE = merged
    return _CACHE


def get_archive_dir() -> str:
    cfg = load_analysis_config()
    rel = str(cfg.get("archive_dir") or "analysis_reports").strip()
    return rel if os.path.isabs(rel) else os.path.join(PROJECT_ROOT, rel)


def normalize_whitelist(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        s = str(item or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def qq_cfg() -> Dict[str, Any]:
    return deepcopy(load_analysis_config().get("qq_game_sentiment") or {})


def report_push_use_interactive_card() -> bool:
    """分析报告（QQ 舆情）是否用 interactive 卡片推送；闲聊不走此开关。"""
    return bool(load_analysis_config().get("push_use_interactive_card", False))


def report_push_card_header_template() -> str:
    template = str(load_analysis_config().get("push_card_header_template") or "blue").strip()
    return template or "blue"
