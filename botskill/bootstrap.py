# -*- coding: utf-8 -*-
"""
进程启动编排：后台线程、热重载、长连接模式。

hybrid（推荐整合场景）：
  - 后台：飞书官方机器人 WebSocket → 命令处理 / QQ 舆情 → 飞书回复
  - 主线程：NapCat OneBot HTTP /onebot → 仅 conversation_logs 归档
"""

import logging
import socket
import threading
import time

from config import BOT_CONFIG

from botskill.im_transport import reload_transport
from botskill.im_handler import ensure_im_message_pool
from botskill.message_routing import (
    apply_feishu_credentials,
    feishu_listen_enabled,
    is_hybrid_mode,
    napcat_archive_only,
)
from botskill.onebot_app import run_onebot_http_server
from botskill.feishu_longconn import start_feishu_long_connection
from botskill.reload_watch import start_auto_reload_watcher
from botskill.analysis.scheduler import start_analysis_scheduler
from botskill.startup_state import begin_startup, enter_warming_phase, schedule_finalize_startup


_ONEBOT_HTTP_STARTED = False


def _wait_tcp_port(host: str, port: int, timeout_sec: float = 30.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _start_onebot_archive_server_thread() -> None:
    """hybrid 下尽早绑定 :5000，避免 NapCat 上报 ECONNREFUSED。"""
    global _ONEBOT_HTTP_STARTED
    if _ONEBOT_HTTP_STARTED:
        return
    _ONEBOT_HTTP_STARTED = True
    port = int(BOT_CONFIG.get("port", 5000))

    def _run() -> None:
        try:
            run_onebot_http_server()
        except Exception:
            logging.exception("NapCat /onebot HTTP 服务异常退出")

    t = threading.Thread(target=_run, name="onebot-http", daemon=True)
    t.start()
    if _wait_tcp_port("127.0.0.1", port, timeout_sec=45.0):
        logging.info("NapCat 归档通道已监听 127.0.0.1:%s/onebot", port)
    else:
        logging.warning("等待 127.0.0.1:%s 监听超时，NapCat 上报可能失败", port)


def _block_main_thread() -> None:
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        raise


def _start_feishu_listener_thread() -> None:
    def _run() -> None:
        try:
            # hybrid：/onebot 已在独立线程；飞书走 WebSocket 长连接
            start_feishu_long_connection()
        except Exception:
            logging.exception("飞书监听线程异常退出")

    t = threading.Thread(target=_run, name="feishu-listener", daemon=True)
    t.start()
    logging.info("飞书机器人监听已在后台启动（消息 → 命令处理 / QQ 舆情 → 飞书回复）")


def bootstrap() -> None:
    """启动后台线程并按配置选择消息接入方式。"""
    reload_transport()

    if is_hybrid_mode() and not napcat_archive_only():
        logging.error("hybrid 模式需要 napcat_archive_only=true")
        raise SystemExit(1)

    if is_hybrid_mode():
        _start_onebot_archive_server_thread()

    begin_startup()

    ensure_im_message_pool()

    start_analysis_scheduler()
    start_auto_reload_watcher()

    enter_warming_phase()
    schedule_finalize_startup(reason="post_init_delay")

    mode = str(BOT_CONFIG.get("event_mode", "hybrid")).lower()

    if is_hybrid_mode():
        apply_feishu_credentials()
        if feishu_listen_enabled():
            _start_feishu_listener_thread()
        _block_main_thread()
        return

    if feishu_listen_enabled():
        apply_feishu_credentials()
        start_feishu_long_connection()
        return

    if mode in ("onebot", "napcat", "personal", "个人号"):
        try:
            from botskill.napcat_bridge import refresh_bot_runtime_self_id

            refresh_bot_runtime_self_id(quiet=True)
        except Exception:
            pass
        run_onebot_http_server()
        return

    logging.error(
        "未知 event_mode=%s：可选 hybrid（推荐：飞书 + NapCat）、websocket、onebot",
        mode,
    )
    raise SystemExit(1)
