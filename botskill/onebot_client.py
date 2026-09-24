# -*- coding: utf-8 -*-
"""
OneBot v11 客户端（配合 NapCat / LLOneBot 等登录个人 QQ）。

通过 HTTP 调用 OneBot API 发消息；收消息由 onebot_app 转入 im_handler。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import requests

from config import BOT_CONFIG

_CQ_AT_RE = re.compile(r"\[CQ:at,qq=(\d+)\]", re.IGNORECASE)
_CQ_NON_TEXT_RE = re.compile(
    r"\[CQ:(?!at\b)(?:image|face|mface|record|video|file|json|xml|markdown|reply|forward|poke|rps|dice|shake|share|contact|location)[^\]]*\]",
    re.IGNORECASE,
)
_TEXT_SEGMENT_TYPES = frozenset({"text", "at"})
_ARCHIVE_TRIVIAL_EXACT = frozenset(
    {
        "嗯",
        "哦",
        "啊",
        "额",
        "好",
        "好的",
        "收到",
        "知道了",
        "ok",
        "OK",
        "Ok",
        "666",
        "+1",
        "哈哈",
        "哈哈哈",
        "呵呵",
        "行",
        "可以",
        "赞成",
        "顶",
        "赞",
        "谢谢",
        "多谢",
        "在吗",
        "？",
        "?",
        "。",
        "…",
        "x",
        "X",
        "X﹏X",
        "x﹏x",
        "orz",
        "ORZ",
        "qaq",
        "QAQ",
        "awsl",
        "AWSL",
    }
)
_NON_TEXT_SEGMENT_TYPES = frozenset(
    {
        "image",
        "face",
        "mface",
        "record",
        "video",
        "file",
        "json",
        "xml",
        "markdown",
        "reply",
        "forward",
        "poke",
        "rps",
        "dice",
        "shake",
        "share",
        "contact",
        "location",
    }
)


def _api_base() -> str:
    return str(BOT_CONFIG.get("onebot_http_api", "http://127.0.0.1:3000")).rstrip("/")


def _token() -> str:
    return str(BOT_CONFIG.get("onebot_token") or "").strip()


def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    tok = _token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _self_id() -> int:
    try:
        return int(BOT_CONFIG.get("onebot_self_id") or 0)
    except (TypeError, ValueError):
        return 0


def parse_chat_id(chat_id: str) -> Tuple[str, int]:
    """返回 (kind, id)，kind 为 group 或 private。"""
    cid = (chat_id or "").strip()
    if cid.startswith("group:"):
        return "group", int(cid[6:])
    if cid.startswith("private:") or cid.startswith("c2c:"):
        key = "private:" if cid.startswith("private:") else "c2c:"
        return "private", int(cid[len(key) :])
    if cid.isdigit():
        return "group", int(cid)
    raise ValueError(f"无法解析 chat_id: {chat_id}")


def format_group_chat_id(group_id: int | str) -> str:
    return f"group:{int(group_id)}"


def format_private_chat_id(user_id: int | str) -> str:
    return f"private:{int(user_id)}"


def _onebot_post(action: str, params: dict) -> dict:
    url = f"{_api_base()}/{action}"
    try:
        response = requests.post(url, json=params, headers=_headers(), timeout=20)
        data = response.json() if response.text else {}
    except Exception as exc:  # pylint: disable=broad-except
        logging.exception("OneBot API %s 异常：%s", action, exc)
        return {"status": "failed", "retcode": -1, "msg": str(exc)}
    if response.status_code != 200:
        logging.error("OneBot API %s HTTP %s: %s", action, response.status_code, data or response.text)
    return data if isinstance(data, dict) else {}


def send_message(chat_id: str, text: str, msg_id: Optional[str] = None) -> bool:
    _ = msg_id
    try:
        kind, target_id = parse_chat_id(chat_id)
    except ValueError as exc:
        logging.error("%s", exc)
        return False
    action = "send_group_msg" if kind == "group" else "send_private_msg"
    key = "group_id" if kind == "group" else "user_id"
    data = _onebot_post(action, {key: target_id, "message": text or ""})
    ok = data.get("status") == "ok" or data.get("retcode") == 0
    if not ok:
        logging.error("OneBot 发送失败 action=%s resp=%s", action, data)
    return bool(ok)


def send_rich_message(chat_id: str, markdown_text: str, post_title: Optional[str] = None) -> bool:
    _ = post_title
    return send_message(chat_id, markdown_to_plain_for_fallback(markdown_text))


def fetch_chat_info(chat_id: str) -> dict:
    try:
        kind, target_id = parse_chat_id(chat_id)
    except ValueError:
        return {"chat_id": chat_id, "name": "", "chat_type": ""}
    if kind != "group":
        return {"chat_id": chat_id, "name": "单聊", "chat_type": "private"}
    data = _onebot_post("get_group_info", {"group_id": target_id})
    if data.get("status") != "ok" and data.get("retcode") not in (0, None):
        return {"chat_id": chat_id, "name": "", "chat_type": "group"}
    info = data.get("data") or {}
    return {
        "chat_id": chat_id,
        "name": info.get("group_name") or info.get("name") or "",
        "chat_type": "group",
        "description": str(target_id),
    }


def strip_im_markup(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw)
    s = _CQ_AT_RE.sub("", s)
    s = re.sub(r"\[CQ:[^\]]+\]", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<@[^>]+>", "", s)
    s = s.replace("\u200b", "").replace("\ufeff", "")
    return s.strip()


def markdown_to_plain_for_fallback(md: str) -> str:
    s = (md or "").replace("\r\n", "\n").strip()
    if not s:
        return "（无内容）"
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"^>\s*", "", s, flags=re.MULTILINE)
    return s.strip()


_AT_ONLY_RE = re.compile(r"^(\s*@\d+\s*)+$")
_BRACKET_ONLY_RE = re.compile(r"^(\[[^\]]+\]\s*)+$", re.IGNORECASE)


def is_trivial_qq_archive_text(text: str) -> bool:
    """无实质信息的 QQ 群消息不入 conversation_logs。"""
    t = (text or "").strip()
    if not t:
        return True
    if t in _ARCHIVE_TRIVIAL_EXACT or t.lower() in _ARCHIVE_TRIVIAL_EXACT:
        return True
    if _AT_ONLY_RE.match(t):
        return True
    if _BRACKET_ONLY_RE.match(t):
        return True
    without_at = re.sub(r"@\d+", "", t).strip()
    if not without_at and "@" in t:
        return True
    without_brackets = re.sub(r"\[[^\]]+\]", "", t, flags=re.IGNORECASE).strip()
    if not without_brackets and "[" in t:
        return True
    # 无连续汉字叙述：如 X﹏X、???、纯符号
    if not re.search(r"[\u4e00-\u9fff]{2,}", t) and len(t) <= 20:
        return True
    cjk = re.findall(r"[\u4e00-\u9fff]", t)
    if len(cjk) <= 1 and len(t) <= 8:
        return True
    return False


def _is_trivial_archive_text(text: str) -> bool:
    return is_trivial_qq_archive_text(text)


_LEGACY_EMPTY_ARCHIVE_TEXT = frozenset({"（无文本正文）", "（无内容）"})


def should_drop_qq_stored_message(record: dict) -> bool:
    """历史 conversation_logs 清洗：与入站归档过滤一致。"""
    if not isinstance(record, dict):
        return True
    text = str(record.get("text") or "").strip()
    if text in _LEGACY_EMPTY_ARCHIVE_TEXT:
        return True
    return is_trivial_qq_archive_text(text)


def _parse_inbound_for_archive(message_field: Any, raw_message: str) -> Optional[dict]:
    """
    解析 NapCat/OneBot 入站消息；返回 None 则不入库。
    方案 D：纯图片/语音/表情等跳过；无意义短文本跳过。
    """
    texts: List[str] = []

    if isinstance(message_field, list):
        for seg in message_field:
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type") or "").lower()
            data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
            if seg_type == "text":
                texts.append(str(data.get("text") or ""))
            elif seg_type == "at":
                qq = str(data.get("qq") or "").strip()
                if qq:
                    texts.append(f"@{qq}")

    raw = str(raw_message or "")
    caption = strip_im_markup("".join(texts)) or strip_im_markup(raw)
    if caption and re.fullmatch(r"(\[[^\]]+\]\s*)+", caption):
        caption = ""

    if not caption:
        return None

    if _is_trivial_archive_text(caption):
        return None

    return {"text": caption, "message_type": "text"}


def _analysis_group_whitelist() -> set:
    """当前舆情分析允许的消息来源群号（analysis_config → group_whitelist）。"""
    try:
        from botskill.analysis.config import qq_cfg

        raw = qq_cfg().get("group_whitelist") or []
        return {str(x).strip() for x in raw if str(x).strip()}
    except Exception:
        return set()


def try_archive_napcat_inbound_message(event: dict) -> None:
    """NapCat/QQ 个人号入站：仅归档到 conversation_logs（不触发 AI）。"""
    from botskill.message_routing import napcat_archive_only

    if not napcat_archive_only():
        return
    if str(event.get("post_type") or "") != "message":
        return

    msg_type = str(event.get("message_type") or "")
    if msg_type not in ("group", "private"):
        return

    try:
        user_id = int(event.get("user_id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    member_id = str(user_id) if user_id else "unknown_user"
    message_id = str(event.get("message_id") or event.get("id") or "")

    if msg_type == "group":
        if not bool(BOT_CONFIG.get("archive_all_group_messages", True)):
            return
        try:
            group_id = int(event.get("group_id") or 0)
        except (TypeError, ValueError):
            group_id = 0
        if not group_id:
            gid = str(event.get("group_openid") or "").strip()
            if not gid:
                return
            chat_id = f"group:{gid}"
        else:
            chat_id = format_group_chat_id(group_id)
            if str(group_id) not in _analysis_group_whitelist():
                logging.info("NapCat 跳过非分析白名单群 group=%s（未入库）", group_id)
                return
    else:
        if not user_id:
            return
        chat_id = format_private_chat_id(user_id)

    parsed = _parse_inbound_for_archive(
        event.get("message"),
        str(event.get("raw_message") or event.get("content") or ""),
    )
    if not parsed:
        logging.debug("NapCat 跳过不入库消息 chat=%s", chat_id)
        return
    try:
        from botskill.archive_runtime import CONVERSATION_ARCHIVE

        CONVERSATION_ARCHIVE.log_user_message(
            chat_id,
            member_id,
            message_id,
            str(parsed.get("message_type") or "text"),
            str(parsed.get("text") or ""),
        )
        logging.info(
            "NapCat 已归档 chat=%s user=%s len=%s",
            chat_id,
            member_id,
            len(str(parsed.get("text") or "")),
        )
    except Exception as exc:  # pylint: disable=broad-except
        logging.warning("NapCat 归档失败 chat=%s：%s", chat_id, exc)
