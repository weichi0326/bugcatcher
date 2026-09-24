# -*- coding: utf-8 -*-
"""
服务生命周期与异常日志（按天切割）。

- 根目录下自动创建 log/，每日文件 log_yyyy_mm_dd.txt。
- 单行格式：【YYYY-MM-DD HH:MM:SS】: <事件描述>
- 多线程写入使用全局锁，避免交错；flush + fsync 降低异常断电丢尾行概率。
"""

from __future__ import annotations

import os
import threading
import traceback
from datetime import datetime

_LOCK = threading.Lock()
_ROOT = os.path.dirname(os.path.abspath(__file__))
# 与 reload_watch 子进程拉起逻辑一致：子进程不重复写「进程手动开启」。
FORK_RESTART_ENV = "QQ_BOT_FORK_RESTART"


def _log_dir() -> str:
    path = os.path.join(_ROOT, "log")
    os.makedirs(path, exist_ok=True)
    return path


def _append(message_suffix: str) -> None:
    """message_suffix 为冒号后的完整事件描述（须与用户约定文案一致）。"""
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    line = f"【{stamp}】: {message_suffix}\n"
    day = now.strftime("%Y_%m_%d")
    log_path = os.path.join(_log_dir(), f"log_{day}.txt")
    with _LOCK:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass


def log_manual_start() -> None:
    """记录：进程由操作者直接启动（python bot.py），不含本程序触发的子进程拉起。"""
    _append("进程手动开启")


def log_manual_shutdown() -> None:
    _append("进程手动关闭")


def log_manual_restart() -> None:
    _append("进程手动重启")


def log_auto_reload_restart() -> None:
    _append("进程因文件变动自动重启")


def log_error(exc: BaseException) -> None:
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
    _append(f"进程遇到错误，错误原因为：{detail}")
