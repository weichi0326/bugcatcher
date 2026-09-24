# -*- coding: utf-8 -*-
"""分析任务后台执行：不占用 IM 线程，避免阻塞飞书日常对话。

提供任务级"守门狗"超时：无论 _job 内部发生什么（外部模型挂起、网络卡住等），
都保证在 analysis_job_timeout_sec（默认 90 秒）内向 on_done 回调强制返回一次结果，
避免用户长时间等不到反馈。
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from config import BOT_CONFIG

_EXECUTOR_LOCK = threading.Lock()
_EXECUTOR: Optional[ThreadPoolExecutor] = None


def _max_workers() -> int:
    raw = BOT_CONFIG.get("analysis_executor_max_workers", 2)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 2


def _job_timeout_sec(timeout_sec: Optional[Any]) -> int:
    raw = timeout_sec if timeout_sec is not None else BOT_CONFIG.get("analysis_job_timeout_sec", 90)
    try:
        val = int(raw)
    except (TypeError, ValueError):
        val = 90
    return val if val > 0 else 90


def get_analysis_executor() -> ThreadPoolExecutor:
    global _EXECUTOR  # noqa: PLW0603
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            n = _max_workers()
            _EXECUTOR = ThreadPoolExecutor(max_workers=n, thread_name_prefix="analysis_job")
            logging.info("分析任务线程池已初始化 workers=%s", n)
        return _EXECUTOR


def submit_analysis_job(
    label: str,
    fn: Callable[..., Dict[str, Any]],
    *args: Any,
    on_done: Optional[Callable[[Dict[str, Any]], None]] = None,
    timeout_sec: Optional[Any] = None,
    **kwargs: Any,
) -> None:
    """在独立线程池中运行分析（定时/聊天命令均可用）。

    无论 fn 是否完成，都会在 timeout_sec 内触发一次 on_done：
    - 正常完成 → 传入 fn 结果；
    - 任务异常 → 传入 {"ok": False, "error": ...}；
    - 超过 timeout_sec 未完成 → 传入 {"ok": False, "reason": "分析超时...", "error": "timeout"}。
    """
    limit = _job_timeout_sec(timeout_sec)
    done_evt = threading.Event()
    abort_evt = threading.Event()  # 守门狗超时后置位：任务应跳过报告推送，避免"失败后又推送"
    fired_lock = threading.Lock()
    fired = {"v": False}

    def _fire(result: Dict[str, Any]) -> None:
        with fired_lock:
            if fired["v"]:
                return
            fired["v"] = True
        if on_done:
            try:
                on_done(result)
            except Exception as exc:  # pylint: disable=broad-except
                logging.exception("分析任务 on_done 异常 label=%s: %s", label, exc)

    def _run() -> None:
        try:
            result = fn(*args, abort_check=abort_evt.is_set, **kwargs)
        except Exception as exc:  # pylint: disable=broad-except
            logging.exception("分析任务异常 label=%s: %s", label, exc)
            result = {"ok": False, "error": str(exc)}
        done_evt.set()
        _fire(result)

    def _watchdog() -> None:
        # Timer 已在 limit 秒后触发；若任务仍未完成，置中止位并强制返回超时结果。
        if not done_evt.is_set():
            logging.error("分析任务超时，已强制返回 label=%s timeout=%ss", label, limit)
            abort_evt.set()
            _fire(
                {
                    "ok": False,
                    "reason": f"分析超时，超过 {limit} 秒未完成。",
                    "error": "timeout",
                }
            )

    get_analysis_executor().submit(_run)
    wd = threading.Timer(limit, _watchdog)
    wd.daemon = True
    wd.start()
    logging.info("分析任务已提交后台 label=%s", label)
