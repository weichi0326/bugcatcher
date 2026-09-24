# -*- coding: utf-8 -*-
"""
对话归档运行时单例。

职责：按 config 中的目录与单文件大小上限，构造 conversation_logger.ConversationArchive，
并在收到 QQ 消息时由 im_handler 写入用户侧与助手侧记录。

实现的 AI 配套能力：持久化人机对话与媒体引用路径，便于审计与复盘（不参与模型推理）。
"""

from conversation_logger import ConversationArchive

from botskill.im_transport import fetch_chat_info
from botskill.paths import CONVERSATION_DIR, CONVERSATION_MAX_BYTES

CONVERSATION_ARCHIVE = ConversationArchive(CONVERSATION_DIR, CONVERSATION_MAX_BYTES, fetch_chat_info)
