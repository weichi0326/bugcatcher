# -*- coding: utf-8 -*-
"""
飞书 WebSocket 长连接（参照 FeiShu-BOT/botskill/feishu_longconn.py）。

凭证：直接读取项目 config/feishu_bot.json（app_id / app_secret），不依赖 qq_app_id。
入站：im.message.receive_v1 → spawn_im_handler_thread（飞书消息不归档）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Tuple

from config import BOT_CONFIG

from botskill.im_handler import spawn_im_handler_thread
from botskill.paths import PROJECT_ROOT

_FEISHU_BOT_JSON = os.path.join(PROJECT_ROOT, "config", "feishu_bot.json")


def _load_feishu_credentials() -> Tuple[str, str]:
    """从 config/feishu_bot.json 读取凭证并写入 BOT_CONFIG（供发消息复用）。"""
    app_id = str(BOT_CONFIG.get("feishu_app_id") or "").strip()
    app_secret = str(BOT_CONFIG.get("feishu_app_secret") or "").strip()
    if app_id and app_secret:
        return app_id, app_secret
    if not os.path.isfile(_FEISHU_BOT_JSON):
        return "", ""
    try:
        with open(_FEISHU_BOT_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logging.error("读取 %s 失败：%s", _FEISHU_BOT_JSON, exc)
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    app_id = str(data.get("app_id") or "").strip()
    app_secret = str(data.get("app_secret") or "").strip()
    if app_id:
        BOT_CONFIG["feishu_app_id"] = app_id
    if app_secret:
        BOT_CONFIG["feishu_app_secret"] = app_secret
    return app_id, app_secret


def _start_feishu_ws_blocking() -> None:
    """建立长连接并阻塞运行（正常情况不返回）。"""
    import lark_oapi as lark
    from lark_oapi.api.im.v1.model import P2ImMessageReceiveV1

    app_id, app_secret = _load_feishu_credentials()
    if not app_id or not app_secret:
        raise RuntimeError(
            f"飞书凭证为空，请在 {_FEISHU_BOT_JSON} 填写 app_id 与 app_secret"
        )

    def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
        try:
            ev = data.event
            if not ev or not ev.message:
                return
            msg = ev.message
            chat_id = msg.chat_id
            if not chat_id:
                return
            sender_obj = ev.sender
            if sender_obj and getattr(sender_obj, "sender_type", None) == "app":
                return
            user_id = "unknown_user"
            if sender_obj and sender_obj.sender_id:
                sid = sender_obj.sender_id
                user_id = sid.open_id or sid.user_id or user_id
            message_id = msg.message_id or ""
            msg_type = msg.message_type or "text"
            raw_content = msg.content
            if isinstance(raw_content, str):
                content_json_str = raw_content
            else:
                content_json_str = json.dumps(raw_content or {}, ensure_ascii=False)
            spawn_im_handler_thread(chat_id, user_id, message_id, msg_type, content_json_str)
        except Exception as exc:  # pylint: disable=broad-except
            logging.exception("飞书长连接事件预处理失败：%s", exc)

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1)
        .build()
    )
    cli = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )
    logging.info(
        "正在连接飞书长连接（app_id=%s…）；开放平台需启用「使用长连接接收事件」。",
        app_id[:12],
    )
    cli.start()


def start_feishu_long_connection() -> None:
    """启动飞书 WebSocket 长连接（首连/异常退避重试）。"""
    from lark_oapi.ws.const import EXCEED_CONN_LIMIT
    from lark_oapi.ws.exception import ClientException

    backoff = 8.0
    while True:
        try:
            _start_feishu_ws_blocking()
            return
        except KeyboardInterrupt:
            raise
        except SystemExit:
            raise
        except BaseException as exc:
            try:
                from service_logger import log_error

                log_error(exc)
            except Exception:
                pass
            wait_sec = backoff
            if isinstance(exc, ClientException) and getattr(exc, "code", None) == EXCEED_CONN_LIMIT:
                wait_sec = max(wait_sec, 28.0)
            logging.exception("飞书长连接启动失败或已异常退出：%s，%.0f 秒后重试", exc, wait_sec)
            time.sleep(wait_sec)
            backoff = min(backoff * 1.5, 120.0)
