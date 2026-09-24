# -*- coding: utf-8 -*-
"""
路径与目录解析。

职责：根据 config.BOT_CONFIG 解析项目根目录、对话归档目录等绝对路径，
避免依赖进程当前工作目录（cwd）导致找不到文件。
"""

import os

from config import BOT_CONFIG
from config.constants import FILE_BOT_CONFIG, REL_CONFIG_DIR_NAME

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CONVERSATION_LOG = BOT_CONFIG.get("conversation_log_dir", "conversation_logs")
CONVERSATION_DIR = (
    _CONVERSATION_LOG if os.path.isabs(_CONVERSATION_LOG) else os.path.join(PROJECT_ROOT, _CONVERSATION_LOG)
)
CONVERSATION_MAX_BYTES = int(BOT_CONFIG.get("conversation_max_file_mb", 100)) * 1024 * 1024

CONFIG_DIR = os.path.join(PROJECT_ROOT, REL_CONFIG_DIR_NAME)
CONFIG_FILE_PATH = os.path.join(CONFIG_DIR, FILE_BOT_CONFIG)
