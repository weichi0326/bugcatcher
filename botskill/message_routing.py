# -*- coding: utf-8 -*-
"""
双通道消息路由（hybrid）：
- 飞书官方机器人：监听 → 分析与命令处理 → 飞书群回复
- NapCat 个人号：仅写入 conversation_logs，不调用模型、不回复
"""

from __future__ import annotations

import logging

from config import BOT_CONFIG

def event_mode() -> str:
    return str(BOT_CONFIG.get("event_mode", "websocket")).lower()


def is_hybrid_mode() -> bool:
    return event_mode() in ("hybrid", "feishu_and_napcat", "双通道")


def napcat_archive_only() -> bool:
    if "napcat_archive_only" in BOT_CONFIG:
        return bool(BOT_CONFIG.get("napcat_archive_only"))
    return is_hybrid_mode() or event_mode() in ("onebot", "napcat", "personal", "个人号")


def feishu_listen_enabled() -> bool:
    if "feishu_listen_enabled" in BOT_CONFIG:
        return bool(BOT_CONFIG.get("feishu_listen_enabled"))
    return event_mode() in ("websocket", "http", "webhook", "ws", "长连接", "hybrid", "feishu_and_napcat", "双通道")


def apply_feishu_credentials() -> bool:
    """将 config/feishu_bot.json 凭证合并进 feishu_app_id / feishu_app_secret（不覆盖 QQ 凭证）。"""
    from botskill.feishu_config import load_feishu_config

    fc = load_feishu_config()
    if not fc:
        return False
    mapping = (
        ("app_id", "feishu_app_id"),
        ("app_secret", "feishu_app_secret"),
    )
    applied = False
    for src, dst in mapping:
        val = str(fc.get(src) or "").strip()
        if val:
            BOT_CONFIG[dst] = val
            applied = True
    if applied:
        logging.info("已加载飞书机器人配置：%s", BOT_CONFIG.get("feishu_config_path", "config/feishu_bot.json"))
    return applied
