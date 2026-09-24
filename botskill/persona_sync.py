# -*- coding: utf-8 -*-
"""
分析规则加载与注入（保留既有接口名）。

说明（已按需求大幅精简）：
- 飞书日常对话已停用，运行时仅加载 QQ 分析规则（persona_qq_analysis.json）。
- 不再有世代号、按轮短注入、6h 定时同步。
- 每次分析舆情时一次性注入规则全文。

会话隔离：
- QQ 舆情分析：虚拟 chat_id `analysis:qq_sentiment`，不写 history，每次分析注入规则全文。
- 飞书日常对话已关闭（不再走 call_deepseek 聊天链路）。

本模块对外保留的最小接口：
- ANALYSIS_SESSION_QQ / resolve_persona_profile
- build_qq_analysis_persona_messages / build_analysis_persona_messages
- get_display_name_for_wake / get_greeting_text（身份名 / 问候，供 help 等命令使用）
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

from config.constants import (
    FILE_PERSONA_QQ_ANALYSIS,
    REL_CONFIG_DIR_NAME,
)

from botskill.paths import PROJECT_ROOT

_PERSONA_QQ_ANALYSIS_PATH = os.path.join(PROJECT_ROOT, REL_CONFIG_DIR_NAME, FILE_PERSONA_QQ_ANALYSIS)

ANALYSIS_SESSION_QQ = "analysis:qq_sentiment"

# 机器人默认显示名称。
_DEFAULT_IDENTITY_NAME = "群聊分析助手"
# 默认问候语（帮助面板使用）。
_DEFAULT_GREETING = "群聊分析助手已就绪。请在群内 @ 机器人查询已有报告，或使用 /舆情明细、/help。"

_QQ_LOCK = threading.Lock()
_QQ_CACHE: Dict[str, Any] = {}


def _load_persona_json(path: str, label: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        logging.warning("%s 不存在：%s", label, path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        logging.warning("读取 %s 失败：%s", label, exc)
        return {}
    return data if isinstance(data, dict) else {}


def reload_qq_analysis_from_disk() -> Dict[str, Any]:
    """从磁盘重新加载 QQ 分析规则（每次分析时调用，确保最新）。"""
    global _QQ_CACHE
    data = _load_persona_json(_PERSONA_QQ_ANALYSIS_PATH, "persona_qq_analysis.json")
    with _QQ_LOCK:
        _QQ_CACHE = data
    return dict(_QQ_CACHE)


def get_qq_analysis() -> Dict[str, Any]:
    with _QQ_LOCK:
        return dict(_QQ_CACHE) if _QQ_CACHE else {}


def resolve_persona_profile(chat_id: str) -> str:
    """分析专用会话返回 qq_analysis；其它聊天入口已关闭。"""
    cid = (chat_id or "").strip()
    if cid == ANALYSIS_SESSION_QQ or cid.startswith("analysis:qq"):
        return "qq_analysis"
    return "qq_analysis"  # 只有一套分析规则


def _bundle_text_parts(bundle: Dict[str, Any], keys: tuple[str, ...]) -> List[str]:
    parts: List[str] = []
    for key in keys:
        text = str(bundle.get(key) or "").strip()
        if text:
            parts.append(text)
    return parts


def build_qq_analysis_persona_messages() -> List[dict]:
    """
    QQ 专项：每次分析时一次性注入规则全文（无 SESSIONS、无世代同步、无按轮短注入）。
    """
    reload_qq_analysis_from_disk()
    parts = _bundle_text_parts(
        get_qq_analysis(),
        ("personality_long_text", "personality_short_text", "anchor_short_text"),
    )
    if not parts:
        return []
    combined = "\n\n".join(parts)
    logging.info(
        "QQ 分析规则一次性注入 session=%s chars=%s",
        ANALYSIS_SESSION_QQ,
        len(combined),
    )
    return [{"role": "system", "content": combined}]


def build_analysis_persona_messages(job_chat_id: str) -> List[dict]:
    """分析会话（QQ 舆情）返回规则 system。"""
    return build_qq_analysis_persona_messages()


def get_display_name_for_wake() -> str:
    """机器人默认显示名称。"""
    return _DEFAULT_IDENTITY_NAME


def get_persona_name_for_wake() -> str:
    return _DEFAULT_IDENTITY_NAME


def get_greeting_text() -> str:
    """帮助面板的默认问候语。"""
    return _DEFAULT_GREETING
