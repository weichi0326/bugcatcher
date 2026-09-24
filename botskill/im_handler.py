# -*- coding: utf-8 -*-
"""
QQ 即时消息（IM）编排：分析模式，仅处理命令（飞书消息不写入归档）。

实现的端到端能力（已关闭日常聊天）：
- 仅处理飞书官方机器人入站纯文本：/ 命令 → 命令处理 → 飞书回复；非命令且未 @ 则忽略，
  @ 而非命令则回复「分析模式」提示。
- QQ 舆情分析在独立线程池执行（/舆情明细），结果由飞书机器人推送。
- NapCat/QQ 个人号入站由 onebot_app 仅归档文字，不进入本模块。
- 飞书侧不再写入 conversation_logs。
- 异步入口 spawn_im_handler_thread：共享线程池 + 排队上限，池满时对会话发送繁忙提示。
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from datetime import datetime
from typing import Optional, Tuple

from commands import dispatch_command
from config import BOT_CONFIG, MODELS

from botskill.im_transport import (
    fetch_chat_info,
    markdown_to_plain_for_fallback,
    send_message,
    send_rich_message,
    strip_im_markup,
)
from botskill.paths import CONFIG_FILE_PATH
from botskill.personality import _has_at_mention
from botskill.reload_watch import restart_ai_only
from botskill.startup_state import StartupPhase, get_phase

# BOT_CONFIG 键名（集中常量，避免散落魔法字符串）
_CFG_IM_EXECUTOR_MAX_WORKERS = "im_executor_max_workers"
_CFG_IM_EXECUTOR_MAX_QUEUED = "im_executor_max_queued"
_IM_BUSY_USER_MESSAGE = (
    "当前同时处理的请求较多，请稍候片刻再发送；"
    "不必连续重复同一条消息，就绪后我会按顺序回复。"
)

_im_pool_lock = threading.Lock()
_im_executor: Optional[ThreadPoolExecutor] = None
_im_slot_semaphore: Optional[threading.Semaphore] = None


def ensure_im_message_pool() -> Tuple[ThreadPoolExecutor, threading.Semaphore]:
    """初始化 IM 线程池（bootstrap 启动阶段调用，避免首条消息才懒加载）。"""
    return _ensure_im_message_pool()


def _ensure_im_message_pool() -> Tuple[ThreadPoolExecutor, threading.Semaphore]:
    """懒加载 IM 处理线程池与槽位信号量（并发数 + 排队上限）。"""
    global _im_executor, _im_slot_semaphore
    if _im_executor is not None and _im_slot_semaphore is not None:
        return _im_executor, _im_slot_semaphore
    with _im_pool_lock:
        if _im_executor is not None and _im_slot_semaphore is not None:
            return _im_executor, _im_slot_semaphore
        try:
            workers = int(BOT_CONFIG.get(_CFG_IM_EXECUTOR_MAX_WORKERS, 8))
        except (TypeError, ValueError):
            workers = 8
        try:
            max_queued = int(BOT_CONFIG.get(_CFG_IM_EXECUTOR_MAX_QUEUED, 64))
        except (TypeError, ValueError):
            max_queued = 64
        workers = max(1, workers)
        max_queued = max(0, max_queued)
        slots = workers + max_queued
        _im_slot_semaphore = threading.Semaphore(slots)
        _im_executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="im_msg")
        logging.info(
            "IM 处理线程池已初始化 workers=%s max_queued=%s total_slots=%s",
            workers,
            max_queued,
            slots,
        )
        return _im_executor, _im_slot_semaphore


def _send_visible_reply(chat_id: str, sender_open_id: str, markdown_body: str) -> None:
    """发送可见回复：优先富文本，失败降级纯文本（不 @ 用户）。"""
    _ = sender_open_id
    if send_rich_message(chat_id, markdown_body):
        return
    plain = markdown_to_plain_for_fallback(markdown_body)
    if not send_message(chat_id, plain):
        logging.error("回复发送失败，chat_id=%s", chat_id)
        send_message(
            chat_id,
            "刚才那条回复没能顺利发到群里，你可以稍后再试一次；"
            "若一直如此，请检查消息发送通道。",
        )


def handle_im_message_event(
    chat_id: str,
    user_id: str,
    message_id: str,
    msg_type: str,
    content_json_str: str,
    *,
    enqueued_phase: Optional[StartupPhase] = None,
) -> None:
    """统一处理一条即时消息（HTTP Webhook 与长连接共用同一套逻辑）。"""
    _ = enqueued_phase
    current_phase = get_phase()
    if current_phase == StartupPhase.PRE_START:
        logging.debug("丢弃消息（启动前）chat_id=%s message_id=%s", chat_id, message_id)
        return
    if msg_type != "text":
        logging.debug("飞书侧忽略非文本入站 msg_type=%s chat_id=%s", msg_type, chat_id)
        return

    try:
        content = json.loads(content_json_str or "{}")
    except json.JSONDecodeError:
        content = {}

    content_text = str(content.get("text") or "").strip()
    if not content_text:
        logging.debug("飞书侧忽略空文本 chat_id=%s", chat_id)
        return

    logical_text = strip_im_markup(content_text)
    if logical_text:
        preview = logical_text.replace("\n", "\\n")[:160]
        logging.info("飞书入站 chat_id=%s user=%s preview=%r", chat_id, user_id, preview)
    effective_text = logical_text

    command_context = {
        "bot_config": BOT_CONFIG,
        "models": MODELS,
        "config_path": CONFIG_FILE_PATH,
        "now_func": datetime.now,
        # 群内 /restart 仅重启机器人进程（bot.py），不重启 NapCat。
        "restart_app": partial(restart_ai_only, reason="manual"),
        "fetch_chat_info": fetch_chat_info,
    }
    handled, command_reply = dispatch_command(effective_text, user_id, chat_id, command_context)
    if handled:
        logging.info("已处理命令 chat_id=%s preview=%r", chat_id, (effective_text or "")[:120])
        _send_visible_reply(chat_id, user_id, command_reply)
        return

    # 已关闭「闲聊/日常对话」：非命令消息一律不再走通用人格对话；仅 @ 机器人才提示分析模式。
    if not _has_at_mention(content_text, logical_text):
        logging.info("未 @ 机器人，且非命令，忽略（闲聊已关闭）chat_id=%s user=%s", chat_id, user_id)
        return
    if current_phase != StartupPhase.READY:
        logging.warning(
            "忽略消息（尚未就绪）current=%s chat_id=%s",
            current_phase.value,
            chat_id,
        )
        return

    # 报告检索问答：@机器人 明确询问某主题反馈/看法 → 检索最近 7 天报告回答。
    # 先本地粗滤（纯闲聊立刻拒绝，不白调 DS）；可能是询问则发 ack + 调 DS 判定意图与总结（失败退回本地检索）。
    from botskill.analysis.report_qa import answer_qa_via_ds, is_probable_question

    if is_probable_question(effective_text):
        # 直接静默检索并回答（不发送「正在检索」ack）
        qa_reply = answer_qa_via_ds(effective_text, user_id, days=7)
        if qa_reply is not None:
            if send_message(chat_id, qa_reply):
                logging.info("已回答报告检索问答 chat_id=%s", chat_id)
            else:
                logging.warning("QA 回答发送失败 chat_id=%s", chat_id)
        else:
            logging.info("QA 判定为闲聊（NO_QA），回复分析模式提示 chat_id=%s", chat_id)
            _send_visible_reply(
                chat_id,
                user_id,
                "群聊分析助手当前只处理分析类任务与命令（/舆情明细、/help 等），"
                "日常闲聊暂不回复。需要分析请在群里发送 /舆情明细。",
            )
        return

    logging.info("已 @ 机器人但非命令，回复「专注分析」提示 chat_id=%s user=%s", chat_id, user_id)
    _send_visible_reply(
        chat_id,
        user_id,
        "群聊分析助手当前只处理分析类任务与命令（/舆情明细、/help 等），"
        "日常闲聊暂不回复。需要分析请在群里发送 /舆情明细。",
    )


def handle_im_message_thread_entry(
    chat_id: str,
    user_id: str,
    message_id: str,
    msg_type: str,
    content_json_str: str,
    *,
    enqueued_phase: Optional[StartupPhase] = None,
) -> None:
    """长连接后台线程入口：捕获异常并记录日志。"""
    try:
        handle_im_message_event(
            chat_id,
            user_id,
            message_id,
            msg_type,
            content_json_str,
            enqueued_phase=enqueued_phase,
        )
    except Exception as exc:  # pylint: disable=broad-except
        logging.exception("处理即时消息失败：%s", exc)


def spawn_im_handler_thread(
    chat_id: str, user_id: str, message_id: str, msg_type: str, content_json_str: str
) -> None:
    """将一条 IM 提交到共享线程池（限制并发与排队，避免无界创建线程）。"""
    if get_phase() == StartupPhase.PRE_START:
        logging.debug("拒绝入队（启动前）chat_id=%s message_id=%s", chat_id, message_id)
        return
    enqueued_phase = get_phase()
    executor, slots = _ensure_im_message_pool()
    if not slots.acquire(blocking=False):
        logging.warning(
            "im_pool_reject chat_id=%s message_id=%s reason=queue_full",
            chat_id,
            message_id,
        )
        try:
            send_message(chat_id, _IM_BUSY_USER_MESSAGE)
        except Exception as send_exc:  # pylint: disable=broad-except
            logging.warning("繁忙提示发送失败 chat_id=%s err=%s", chat_id, send_exc)
        return

    def _run() -> None:
        try:
            handle_im_message_thread_entry(
                chat_id,
                user_id,
                message_id,
                msg_type,
                content_json_str,
                enqueued_phase=enqueued_phase,
            )
        finally:
            slots.release()

    try:
        executor.submit(_run)
    except Exception as exc:  # pylint: disable=broad-except
        slots.release()
        logging.exception("IM 任务提交线程池失败 chat_id=%s message_id=%s err=%s", chat_id, message_id, exc)
        try:
            send_message(chat_id, _IM_BUSY_USER_MESSAGE)
        except Exception as send_exc:  # pylint: disable=broad-except
            logging.warning("繁忙提示发送失败 chat_id=%s err=%s", chat_id, send_exc)
