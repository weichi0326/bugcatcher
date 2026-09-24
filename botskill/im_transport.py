# -*- coding: utf-8 -*-
"""按 event_mode 选择官方 QQ 开放平台或 OneBot（个人号协议）收发实现。"""

from __future__ import annotations

from config import BOT_CONFIG


def _mode() -> str:
    return str(BOT_CONFIG.get("event_mode", "websocket")).lower()


def _use_onebot_for_send() -> bool:
    """出站消息：hybrid 下仅飞书 API 发回复，个人号 NapCat 不发送回复。"""
    from botskill.message_routing import is_hybrid_mode

    if is_hybrid_mode():
        return False
    return _mode() in ("onebot", "napcat", "personal", "个人号")


def _is_napcat_chat_id(chat_id: str) -> bool:
    cid = (chat_id or "").strip()
    return cid.startswith("group:") or cid.startswith("private:") or cid.startswith("c2c:")


def _is_feishu_chat_id(chat_id: str) -> bool:
    """飞书官方机器人会话 ID（oc_ 开头）；与 NapCat group:/private: 区分。"""
    cid = (chat_id or "").strip()
    if _is_napcat_chat_id(cid) or cid.startswith("c2c:"):
        return False
    return cid.startswith("oc_")


def bind_transport(chat_id: str = ""):
    """按 chat_id 选择出站：飞书 oc_ / NapCat / QQ 开放平台。"""
    cid = (chat_id or "").strip()
    if _is_feishu_chat_id(cid):
        from botskill import feishu_client as backend

        return backend
    if _use_onebot_for_send() and _is_napcat_chat_id(cid):
        from botskill import onebot_client as backend

        return backend
    if _use_onebot_for_send():
        from botskill import onebot_client as backend

        return backend
    from botskill import qq_client as backend

    return backend


def _backend_module(chat_id: str = ""):
    return bind_transport(chat_id)


def reload_transport() -> None:
    pass


def send_message(chat_id: str, text: str, msg_id=None) -> bool:
    return _backend_module(chat_id).send_message(chat_id, text, msg_id=msg_id)


def send_rich_message(chat_id: str, markdown_text: str, post_title=None) -> bool:
    return _backend_module(chat_id).send_rich_message(
        chat_id, markdown_text, post_title=post_title
    )


def fetch_chat_info(chat_id: str) -> dict:
    if _is_napcat_chat_id(chat_id):
        from botskill import onebot_client

        return onebot_client.fetch_chat_info(chat_id)
    return _backend_module(chat_id).fetch_chat_info(chat_id)


def strip_im_markup(raw: str) -> str:
    from botskill import feishu_client

    return feishu_client.strip_im_markup(raw)


def markdown_to_plain_for_fallback(md: str) -> str:
    from botskill import feishu_client

    return feishu_client.markdown_to_plain_for_fallback(md)
