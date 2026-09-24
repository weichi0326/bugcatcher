# -*- coding: utf-8 -*-
"""对外导出 JSON 配置与保存接口；启动时完成 bootstrap。"""

from config.config_manager import (
    BOT_CONFIG,
    MODELS,
    bootstrap_config,
    get_bot_config_path,
    get_config_dir,
    get_models_config_path,
    reload_config,
    save_bot_config,
    save_models,
)

bootstrap_config()

__all__ = [
    "BOT_CONFIG",
    "MODELS",
    "bootstrap_config",
    "reload_config",
    "save_bot_config",
    "save_models",
    "get_config_dir",
    "get_bot_config_path",
    "get_models_config_path",
]
