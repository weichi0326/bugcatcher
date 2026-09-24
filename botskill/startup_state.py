# -*- coding: utf-8 -*-
"""
进程启动阶段：控制 IM 是否入队、就绪后恢复处理。

阶段：
  PRE_START — 启动前：丢弃所有 IM（不入线程池、不响应）
  WARMING   — 链路已接入但未就绪：暂不响应
  READY     — 全服务就绪：正常处理命令
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum

from config import BOT_CONFIG

_PHASE_LOCK = threading.Lock()
_PHASE = "pre"  # pre | warming | ready
_READY_EVENT = threading.Event()
_PROGRESS_STOP = threading.Event()
_PROGRESS_THREAD: threading.Thread | None = None


class StartupPhase(str, Enum):
    PRE_START = "pre"
    WARMING = "warming"
    READY = "ready"


def get_phase() -> StartupPhase:
    with _PHASE_LOCK:
        return StartupPhase(_PHASE)


def is_ready() -> bool:
    return get_phase() == StartupPhase.READY


def begin_startup() -> None:
    """bootstrap 入口：启动进度日志线程，进入 PRE_START。"""
    global _PROGRESS_THREAD
    _READY_EVENT.clear()
    _PROGRESS_STOP.clear()
    with _PHASE_LOCK:
        global _PHASE
        _PHASE = StartupPhase.PRE_START.value

    if _PROGRESS_THREAD is None or not _PROGRESS_THREAD.is_alive():
        _PROGRESS_THREAD = threading.Thread(target=_progress_log_loop, name="startup_progress", daemon=True)
        _PROGRESS_THREAD.start()
    logging.info("启动阶段：PRE_START（不接收群聊消息处理）")


def enter_warming_phase() -> None:
    """传输层即将接入（长连接/HTTP）：进入启动中（暂不响应消息）。"""
    with _PHASE_LOCK:
        global _PHASE
        _PHASE = StartupPhase.WARMING.value
    logging.info("启动阶段：WARMING（链路已接入，暂不响应消息）")


def schedule_finalize_startup(*, reason: str = "ready_delay") -> None:
    """在后台等待就绪延迟后标记 READY（避免与 cli.start / app.run 同线程阻塞）。"""

    def _wait() -> None:
        try:
            delay = float(BOT_CONFIG.get("startup_ready_delay_sec", 3))
        except (TypeError, ValueError):
            delay = 3.0
        delay = max(0.5, delay)
        time.sleep(delay)
        finalize_startup(reason=reason)

    threading.Thread(target=_wait, name="startup_finalize", daemon=True).start()


def finalize_startup(*, reason: str = "manual") -> None:
    """全服务就绪：停止进度刷屏，打印完成句，推送上线话术。"""
    with _PHASE_LOCK:
        global _PHASE
        if _PHASE == StartupPhase.READY.value:
            return
        _PHASE = StartupPhase.READY.value
    _READY_EVENT.set()
    _PROGRESS_STOP.set()
    logging.info("所有服务已全部启动")
    print("所有服务已全部启动", flush=True)
    logging.info("启动完成原因：%s", reason)
    try:
        _send_restart_complete_notice()
    except Exception as exc:  # pylint: disable=broad-except
        logging.warning("重启完成通知发送失败（已忽略）：%s", exc)


def _send_restart_complete_notice() -> None:
    """若是重启拉起的新进程且带通知目标，向该飞书群发送「服务已经重启完成。」。"""
    import json
    import os

    import botskill.reload_watch as rw

    path = rw._RESTART_NOTIFY_PATH
    if not os.path.isfile(path):
        return
    chat_id = ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        chat_id = str((data or {}).get("chat_id") or "").strip()
    except (OSError, json.JSONDecodeError):
        chat_id = ""
    try:
        os.remove(path)
    except OSError:
        pass
    if not chat_id:
        return
    try:
        from botskill.im_transport import send_message

        send_message(chat_id, "服务已经重启完成。")
        logging.info("已发送重启完成通知 chat_id=%s", chat_id)
    except Exception as exc:  # pylint: disable=broad-except
        logging.warning("重启完成通知发送失败 chat_id=%s: %s", chat_id, exc)


def _progress_log_loop() -> None:
    try:
        interval = float(BOT_CONFIG.get("startup_progress_interval_sec", 2))
    except (TypeError, ValueError):
        interval = 2.0
    interval = max(1.0, interval)
    while not _PROGRESS_STOP.wait(timeout=interval):
        if _READY_EVENT.is_set():
            break
        logging.info("服务正在启动中")
        print("服务正在启动中", flush=True)
