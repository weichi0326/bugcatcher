# -*- coding: utf-8 -*-
"""飞书发消息 / 会话查询（出站）；凭证使用 BOT_CONFIG 中 feishu_app_id / feishu_app_secret。"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Optional

import requests

from config import BOT_CONFIG

from botskill.md_to_feishu_post import (
    markdown_to_feishu_post,
    markdown_to_plain_for_fallback,
    split_markdown_for_send,
)

TOKEN_CACHE = {"value": "", "expire_at": 0}
TOKEN_LOCK = threading.Lock()

def _send_chunk_chars() -> int:
    """单条飞书消息的分片阈值（字符数）。

    默认约 1 万字（10000）才拆成多条；可在 bot_config 用 `feishu_post_chunk_chars` 调整。
    """
    try:
        return max(2000, int(BOT_CONFIG.get("feishu_post_chunk_chars", 10000)))
    except (TypeError, ValueError):
        return 10000


def now_ts() -> int:
    return int(time.time())


def get_token() -> str:
    with TOKEN_LOCK:
        if TOKEN_CACHE["value"] and TOKEN_CACHE["expire_at"] > now_ts() + 60:
            return TOKEN_CACHE["value"]
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": BOT_CONFIG.get("feishu_app_id", ""),
            "app_secret": BOT_CONFIG.get("feishu_app_secret", ""),
        }
        try:
            response = requests.post(url, json=payload, timeout=20)
            data = response.json()
        except Exception as exc:  # pylint: disable=broad-except
            logging.exception("获取飞书 token 请求异常：%s", exc)
            return ""
        if response.status_code != 200 or data.get("code") != 0:
            logging.error("获取飞书 token 失败：%s", data)
            return ""
        token = data.get("tenant_access_token", "")
        expire = int(data.get("expire", 7200))
        TOKEN_CACHE["value"] = token
        TOKEN_CACHE["expire_at"] = now_ts() + max(60, expire - 120)
        return token


def fetch_chat_info(chat_id: str) -> dict:
    token = get_token()
    if not token or not chat_id:
        return {"chat_id": chat_id, "name": ""}
    url = f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        data = response.json()
    except Exception as exc:  # pylint: disable=broad-except
        logging.warning("查询飞书会话信息异常：%s", exc)
        return {"chat_id": chat_id, "name": ""}
    if response.status_code != 200 or data.get("code") != 0:
        return {"chat_id": chat_id, "name": ""}
    chat = data.get("data") or {}
    return {
        "chat_id": chat_id,
        "name": chat.get("name") or "",
        "description": chat.get("description") or "",
        "chat_type": chat.get("chat_type") or chat.get("chat_mode") or "",
    }


def _send_interactive_payload(chat_id: str, card: dict) -> bool:
    token = get_token()
    if not token:
        return False
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        data = response.json()
    except Exception as exc:  # pylint: disable=broad-except
        logging.exception("发送飞书消息卡片异常：%s", exc)
        return False
    if response.status_code != 200 or data.get("code") != 0:
        logging.error("发送飞书消息卡片失败：%s", data)
        return False
    return True


def send_interactive_card(chat_id: str, card: dict) -> bool:
    """发送单条 interactive 消息卡片。"""
    if not isinstance(card, dict) or not card:
        return send_message(chat_id, "（无内容）")
    return _send_interactive_payload(chat_id, card)


def send_interactive_cards(chat_id: str, cards: list) -> bool:
    """按序发送多张卡片；任一张失败则中止并返回 False。"""
    if not cards:
        return send_message(chat_id, "（无内容）")
    for card in cards:
        if not send_interactive_card(chat_id, card):
            return False
    return True


def _send_post_payload(chat_id: str, post_content: dict) -> bool:
    token = get_token()
    if not token:
        return False
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    payload = {
        "receive_id": chat_id,
        "msg_type": "post",
        "content": json.dumps(post_content, ensure_ascii=False),
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        data = response.json()
    except Exception as exc:  # pylint: disable=broad-except
        logging.exception("发送飞书富文本异常：%s", exc)
        return False
    if response.status_code != 200 or data.get("code") != 0:
        logging.error("发送飞书富文本失败：%s", data)
        return False
    return True


def send_message(chat_id: str, text: str, msg_id=None) -> bool:
    """纯文本；支持飞书 text 内嵌 **加粗** / *斜体* / ~~删除线~~ / [链接](url)。"""
    _ = msg_id
    token = get_token()
    if not token:
        return False
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        data = response.json()
    except Exception as exc:  # pylint: disable=broad-except
        logging.exception("发送飞书消息异常：%s", exc)
        return False
    if response.status_code != 200 or data.get("code") != 0:
        logging.error("发送飞书消息失败：%s", data)
        return False
    return True


def send_rich_message(chat_id: str, markdown_text: str, post_title: Optional[str] = None) -> bool:
    """
    发送 Markdown 富文本（post + md / code_block / hr）。
    超长报告自动分多条 post；失败时降级纯文本。
    """
    body = (markdown_text or "").strip()
    if not body:
        return send_message(chat_id, "（无内容）")

    chunks = split_markdown_for_send(body, max_chars=_send_chunk_chars())
    if not chunks:
        chunks = [body]

    ok = True
    for idx, chunk in enumerate(chunks):
        title = post_title if idx == 0 and post_title else None
        post_content = markdown_to_feishu_post(chunk, post_title=title)
        if not _send_post_payload(chat_id, post_content):
            ok = False

    if ok:
        return True

    plain = markdown_to_plain_for_fallback(body)
    parts = split_markdown_for_send(plain, max_chars=_send_chunk_chars()) or [plain]
    fallback_ok = True
    for part in parts:
        if not send_message(chat_id, part):
            fallback_ok = False
    return fallback_ok


def strip_im_markup(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw)
    s = re.sub(r"<at\b[^>]*>.*?</at>", "", s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r"<at\b[^>]*/>", "", s, flags=re.IGNORECASE)
    s = s.replace("\u200b", "").replace("\ufeff", "")
    s = s.replace("／", "/").replace("\uff0f", "/")
    return s.strip()
