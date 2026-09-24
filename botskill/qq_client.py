# -*- coding: utf-8 -*-
"""
QQ 机器人开放平台 HTTP 客户端与 IM 消息格式。

- AccessToken 获取与缓存（appId + clientSecret）。
- 群聊 / 单聊被动回复（msg_id）与文本发送。
- 附件 URL 下载、@ 标记清洗、Markdown 降级为纯文本。
"""

import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from config import BOT_CONFIG

TOKEN_CACHE = {"value": "", "expire_at": 0}
TOKEN_LOCK = threading.Lock()

# 凭证被 QQ 判定无效后短期内不再重复请求（避免日志刷屏）
_CREDENTIAL_INVALID_UNTIL = 0.0

# 被动回复上下文：chat_id -> {msg_id, is_c2c, user_openid}
_REPLY_CTX: Dict[str, Dict[str, Any]] = {}
_REPLY_CTX_LOCK = threading.Lock()

API_BASE = "https://api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"

_MSG_SEQ_LOCK = threading.Lock()
_MSG_SEQ_BY_MSG_ID: Dict[str, int] = {}


def now_ts() -> int:
    return int(time.time())


def _app_id() -> str:
    return str(BOT_CONFIG.get("qq_app_id") or "").strip()


def _client_secret() -> str:
    return str(BOT_CONFIG.get("qq_app_secret") or "").strip()


class QQCredentialError(RuntimeError):
    """AppID / AppSecret 被 QQ 开放平台拒绝（如 code=100016）。"""


def credential_error_hint() -> str:
    return (
        "请在 QQ 开放平台（https://q.qq.com/）→ 你的机器人 →「开发设置」复制：\n"
        "  · AppID（应用 ID，不是「机器人 QQ 号」）→ qq_app_id\n"
        "  · AppSecret（客户端密钥，通常约 16 位，不是 32 位的 Token）→ qq_app_secret\n"
        "  · 机器人 QQ 号、Token(机器人令牌) 可记入 qq_bot_number / qq_bot_token 备查，"
        "不能替代上述两项用于鉴权。\n"
        "若曾重置过 AppSecret，必须使用最新一串；复制时不要带空格或换行。"
    )


def get_access_token() -> str:
    """获取 QQBot AccessToken（带缓存）。"""
    global _CREDENTIAL_INVALID_UNTIL
    with TOKEN_LOCK:
        if _CREDENTIAL_INVALID_UNTIL > now_ts():
            return ""
        if TOKEN_CACHE["value"] and TOKEN_CACHE["expire_at"] > now_ts() + 60:
            return TOKEN_CACHE["value"]
        app_id = _app_id()
        secret = _client_secret()
        if not app_id or not secret:
            logging.error("qq_app_id 或 qq_app_secret 为空，请填写 config/bot_config.json")
            return ""
        payload = {"appId": app_id, "clientSecret": secret}
        try:
            response = requests.post(TOKEN_URL, json=payload, timeout=20)
            data = response.json()
        except Exception as exc:  # pylint: disable=broad-except
            logging.exception("获取 QQ AccessToken 请求异常：%s", exc)
            return ""
        token = str(data.get("access_token") or "").strip()
        if not token:
            code = data.get("code")
            if code == 100016:
                _CREDENTIAL_INVALID_UNTIL = now_ts() + 300
                logging.error(
                    "QQ 凭证无效（code=100016 invalid appid or secret）。%s",
                    credential_error_hint().replace("\n", " "),
                )
            else:
                logging.error("获取 QQ AccessToken 失败：%s", data)
            return ""
        try:
            expire = int(data.get("expires_in") or 7200)
        except (TypeError, ValueError):
            expire = 7200
        TOKEN_CACHE["value"] = token
        TOKEN_CACHE["expire_at"] = now_ts() + max(60, expire - 120)
        return token


def get_ws_identify_token() -> str:
    """WebSocket 鉴权 token：QQBot {AccessToken}。"""
    token = get_access_token()
    if not token:
        return ""
    return f"QQBot {token}"


def _auth_headers() -> Dict[str, str]:
    token = get_access_token()
    if not token:
        return {}
    return {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}


def fetch_gateway_url() -> str:
    """GET /gateway 获取 WSS 地址。"""
    headers = _auth_headers()
    if not headers:
        return ""
    try:
        response = requests.get(f"{API_BASE}/gateway", headers=headers, timeout=15)
        data = response.json()
    except Exception as exc:  # pylint: disable=broad-except
        logging.exception("获取 QQ Gateway 异常：%s", exc)
        return ""
    url = str(data.get("url") or "").strip()
    if not url:
        logging.error("获取 QQ Gateway 失败：%s", data)
    return url


def set_reply_context(
    chat_id: str,
    msg_id: str,
    *,
    is_c2c: bool = False,
    user_openid: str = "",
) -> None:
    """记录当前会话最近一条入站消息，供被动回复使用。"""
    if not chat_id or not msg_id:
        return
    with _REPLY_CTX_LOCK:
        _REPLY_CTX[chat_id] = {
            "msg_id": msg_id,
            "is_c2c": bool(is_c2c),
            "user_openid": (user_openid or "").strip(),
        }


def _next_msg_seq(msg_id: str) -> int:
    with _MSG_SEQ_LOCK:
        seq = _MSG_SEQ_BY_MSG_ID.get(msg_id, 0) + 1
        _MSG_SEQ_BY_MSG_ID[msg_id] = seq
        if len(_MSG_SEQ_BY_MSG_ID) > 5000:
            oldest = list(_MSG_SEQ_BY_MSG_ID.keys())[:1000]
            for key in oldest:
                _MSG_SEQ_BY_MSG_ID.pop(key, None)
        return seq


def _feishu_oc_prefix_enabled() -> bool:
    if "feishu_chat_id_oc_prefix" in BOT_CONFIG:
        return bool(BOT_CONFIG.get("feishu_chat_id_oc_prefix"))
    return True


def _normalize_feishu_group_chat_id(group_openid: str) -> str:
    """飞书/QQ 机器人群：归档目录以 oc_ 会话 ID 为键（与 conversation_logs 布局一致）。"""
    gid = (group_openid or "").strip()
    if not gid:
        return ""
    if gid.startswith("oc_"):
        return gid
    if _feishu_oc_prefix_enabled():
        return f"oc_{gid}"
    return f"group:{gid}"


def _parse_chat_id(chat_id: str) -> Tuple[bool, str]:
    """返回 (is_c2c, openid)；oc_ 会话发消息时去掉前缀再调 QQ 开放平台 API。"""
    cid = (chat_id or "").strip()
    if cid.startswith("c2c:"):
        return True, cid[4:].strip()
    if cid.startswith("group:"):
        return False, cid[6:].strip()
    if cid.startswith("oc_"):
        return False, cid[3:].strip()
    return False, cid


def fetch_chat_info(chat_id: str) -> dict:
    """查询会话信息；QQ 群名称接口受限时仅返回 openid。"""
    is_c2c, openid = _parse_chat_id(chat_id)
    if is_c2c:
        return {"chat_id": chat_id, "name": "单聊", "chat_type": "c2c", "description": openid}
    return {"chat_id": chat_id, "name": "", "chat_type": "group", "description": ""}


def strip_im_markup(raw: str) -> str:
    """去掉 QQ / 飞书式 @ 标记与零宽字符，便于识别命令。"""
    if not raw:
        return ""
    s = str(raw)
    s = re.sub(r"<@[^>]+>", "", s)
    s = re.sub(r"<at\b[^>]*>.*?</at>", "", s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r"<at\b[^>]*/>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<img\b[^>]*/>", "", s, flags=re.IGNORECASE)
    s = s.replace("\u200b", "").replace("\ufeff", "")
    s = s.replace("／", "/").replace("\uff0f", "/")
    return s.strip()


def markdown_to_plain_for_fallback(md: str) -> str:
    """富文本发送失败或 QQ 纯文本通道时的 Markdown 降级。"""
    s = (md or "").replace("\r\n", "\n").strip()
    if not s:
        return "（无内容）"
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"^>\s*", "", s, flags=re.MULTILINE)
    return s.strip()


def _send_qq_message(
    chat_id: str,
    text: str,
    *,
    msg_id: Optional[str] = None,
    msg_type: int = 0,
) -> bool:
    token_headers = _auth_headers()
    if not token_headers:
        return False
    is_c2c, openid = _parse_chat_id(chat_id)
    if not openid:
        return False
    body: Dict[str, Any] = {"content": text, "msg_type": msg_type}
    ctx_msg_id = msg_id
    if not ctx_msg_id:
        with _REPLY_CTX_LOCK:
            ctx = _REPLY_CTX.get(chat_id) or {}
        ctx_msg_id = str(ctx.get("msg_id") or "").strip()
    if ctx_msg_id:
        body["msg_id"] = ctx_msg_id
        body["msg_seq"] = _next_msg_seq(ctx_msg_id)
    if is_c2c:
        url = f"{API_BASE}/v2/users/{openid}/messages"
    else:
        url = f"{API_BASE}/v2/groups/{openid}/messages"
    try:
        response = requests.post(url, headers=token_headers, json=body, timeout=20)
        data = response.json() if response.text else {}
    except Exception as exc:  # pylint: disable=broad-except
        logging.exception("发送 QQ 消息异常：%s", exc)
        return False
    if response.status_code not in (200, 201):
        logging.error("发送 QQ 消息失败 status=%s body=%s", response.status_code, data or response.text)
        return False
    if isinstance(data, dict) and data.get("code") not in (None, 0):
        logging.error("发送 QQ 消息失败：%s", data)
        return False
    return True


def send_message(chat_id: str, text: str, msg_id: Optional[str] = None) -> bool:
    return _send_qq_message(chat_id, text, msg_id=msg_id, msg_type=0)


def send_rich_message(chat_id: str, markdown_text: str, post_title: Optional[str] = None) -> bool:
    """QQ 侧以纯文本发送（Markdown 降级）；post_title 保留参数以兼容调用方。"""
    _ = post_title
    plain = markdown_to_plain_for_fallback(markdown_text)
    return send_message(chat_id, plain)


def _feishu_text_only_enabled() -> bool:
    from config import BOT_CONFIG

    if "feishu_forward_text_only" in BOT_CONFIG:
        return bool(BOT_CONFIG.get("feishu_forward_text_only"))
    return True


def _build_content_from_qq_event(data: dict) -> Optional[Tuple[str, str]]:
    """将 QQ 开放平台事件转为 (msg_type, content_json_str)；仅纯文本。"""
    content = str(data.get("content") or "")
    attachments: List[dict] = data.get("attachments") or []
    if not isinstance(attachments, list):
        attachments = []

    if _feishu_text_only_enabled() and attachments:
        ctype = str((attachments[0] or {}).get("content_type") or "").lower()
        logging.debug("飞书/QQ 机器人忽略非文本消息 attachments=%s", ctype or "unknown")
        return None

    text = strip_im_markup(content)
    if not text.strip():
        logging.debug("飞书/QQ 机器人忽略空文本消息")
        return None
    return "text", json.dumps({"text": text}, ensure_ascii=False)


def normalize_qq_dispatch_to_im(event_type: str, data: dict) -> Optional[Tuple[str, str, str, str, str]]:
    """
    将 QQ 网关事件转为 im_handler 五元组：
    (chat_id, user_id, message_id, msg_type, content_json_str)
    """
    if not isinstance(data, dict):
        return None
    if event_type == "GROUP_AT_MESSAGE_CREATE":
        group_openid = str(data.get("group_openid") or "").strip()
        chat_id = _normalize_feishu_group_chat_id(group_openid)
        author = data.get("author") or {}
        user_id = str(author.get("member_openid") or "unknown_user").strip()
        is_c2c = False
    elif event_type == "C2C_MESSAGE_CREATE":
        author = data.get("author") or {}
        user_id = str(author.get("user_openid") or "unknown_user").strip()
        chat_id = f"c2c:{user_id}" if user_id != "unknown_user" else ""
        is_c2c = True
    else:
        return None

    message_id = str(data.get("id") or "").strip()
    if not chat_id or not message_id:
        return None

    built = _build_content_from_qq_event(data)
    if built is None:
        return None
    msg_type, content_json_str = built
    set_reply_context(chat_id, message_id, is_c2c=is_c2c, user_openid=user_id if is_c2c else "")
    return chat_id, user_id, message_id, msg_type, content_json_str


def try_archive_feishu_inbound_text(
    chat_id: str,
    user_id: str,
    message_id: str,
    msg_type: str,
    content_json_str: str,
) -> None:
    """飞书/QQ 机器人纯文本入站归档（与唤醒、AI 回复无关）。"""
    if msg_type != "text":
        return
    try:
        content = json.loads(content_json_str or "{}")
    except json.JSONDecodeError:
        content = {}
    text = strip_im_markup(str(content.get("text") or "").strip())
    if not text:
        return
    try:
        from botskill.archive_runtime import CONVERSATION_ARCHIVE

        CONVERSATION_ARCHIVE.log_user_message(
            chat_id,
            user_id,
            message_id,
            "text",
            text,
        )
        logging.debug("飞书入站已归档 chat_id=%s message_id=%s", chat_id, message_id)
    except Exception as exc:  # pylint: disable=broad-except
        logging.warning("飞书入站归档失败：%s", exc)

