# -*- coding: utf-8 -*-
"""定时生成当天 QQ 舆情报告，并可靠地推送到配置的飞书群。"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import date, datetime, timedelta

from botskill.analysis import archive as analysis_archive
from botskill.analysis.config import load_analysis_config, qq_cfg
from botskill.analysis.log_reader import purge_qq_conversation_logs
from botskill.analysis.push import push_qq_analysis_report, resolve_qq_feishu_push_chat_ids
from botskill.analysis.qq_sentiment import run_qq_scheduled_daily
from botskill.startup_state import StartupPhase, get_phase

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_CHECK_INTERVAL_SEC = 45
_PUSH_RETRY_INTERVAL = timedelta(minutes=5)
_PENDING_KEY = "qq_sentiment_daily_pending"
_LAST_KEY = "qq_sentiment_current_day_last"


def _parse_run_at(raw: str) -> tuple[int, int] | None:
    m = _TIME_RE.match(str(raw or "").strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h <= 23 and 0 <= mi <= 59:
        return h, mi
    return None


def _should_run_now(run_at: str, job_key: str, state: dict) -> bool:
    """今天的定时点是否已到且尚未执行（允许错过精确分钟后补跑，避免循环占用漏跑）。"""
    parsed = _parse_run_at(run_at)
    if not parsed:
        return False
    h, mi = parsed
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if state.get(job_key) == today:
        return False  # 今天已执行
    target = datetime(now.year, now.month, now.day, h, mi)
    return now >= target


def _qq_daily_run_at(cfg: dict) -> str:
    return str(
        cfg.get("evening_run_at") or cfg.get("run_at") or cfg.get("morning_run_at") or "23:00"
    ).strip()


def _pending_reports(state: dict) -> dict:
    pending = state.get(_PENDING_KEY)
    if not isinstance(pending, dict):
        pending = {}
        state[_PENDING_KEY] = pending
    return pending


def _generate_today_if_due(cfg: dict, state: dict) -> bool:
    run_day = date.today()
    day_key = run_day.isoformat()
    if not _should_run_now(_qq_daily_run_at(cfg), _LAST_KEY, state):
        return False
    pending = _pending_reports(state)
    if day_key in pending:
        return False

    logging.info("定时生成当天 QQ 舆情报告 day=%s", day_key)
    result = run_qq_scheduled_daily(target_day=run_day, push=False)
    if not result.get("ok"):
        logging.warning(
            "定时生成当天报告失败（将重试）：%s",
            result.get("reason") or result.get("error") or "未知错误",
        )
        return False

    if not cfg.get("push_to_feishu", True):
        state[_LAST_KEY] = day_key
    else:
        pending[day_key] = {"path": result["path"], "sent": [], "last_attempt": ""}
    # 先持久化生成结果，重启后只补推送，不重复分析。
    analysis_archive.save_scheduler_state(state)
    return True


def _retry_pending_pushes(cfg: dict, state: dict) -> None:
    if not cfg.get("push_to_feishu", True):
        return
    pending = _pending_reports(state)
    targets = resolve_qq_feishu_push_chat_ids(cfg)
    if not targets:
        if pending and state.get("qq_sentiment_daily_missing_target_warned") != date.today().isoformat():
            logging.warning("QQ 定时报告待推送，但 feishu_push_chat_ids 为空")
            state["qq_sentiment_daily_missing_target_warned"] = date.today().isoformat()
            analysis_archive.save_scheduler_state(state)
        return

    now = datetime.now()
    for day_key in sorted(pending):
        item = pending[day_key]
        if not isinstance(item, dict):
            logging.warning("跳过无效的 QQ 待推送状态 day=%s", day_key)
            continue
        sent = set(item.get("sent") or [])
        missing = [target for target in targets if target not in sent]
        if not missing:
            del pending[day_key]
            if str(state.get(_LAST_KEY) or "") < day_key:
                state[_LAST_KEY] = day_key
            analysis_archive.save_scheduler_state(state)
            continue
        try:
            last_attempt = datetime.fromisoformat(str(item.get("last_attempt") or ""))
        except ValueError:
            last_attempt = None
        if last_attempt is not None and now - last_attempt < _PUSH_RETRY_INTERVAL:
            continue
        try:
            report_day = date.fromisoformat(day_key)
        except ValueError:
            logging.warning("跳过无效的 QQ 待推送日期 day=%s", day_key)
            continue
        item["last_attempt"] = now.isoformat(timespec="seconds")
        analysis_archive.save_scheduler_state(state)
        report = analysis_archive.read_report_body(str(item.get("path") or ""))
        if not report.strip():
            logging.warning("QQ 定时报告文件缺失或为空，待推送 day=%s", day_key)
            continue
        for target in missing:
            pushed = push_qq_analysis_report(
                cfg, report, title=analysis_archive.qq_report_title(report_day), targets=[target]
            )
            if target in pushed:
                sent.add(target)
                item["sent"] = sorted(sent)
                analysis_archive.save_scheduler_state(state)
        if all(target in sent for target in targets):
            del pending[day_key]
            if str(state.get(_LAST_KEY) or "") < day_key:
                state[_LAST_KEY] = day_key
            analysis_archive.save_scheduler_state(state)


def _analysis_loop() -> None:
    while True:
        try:
            if get_phase() != StartupPhase.READY:
                time.sleep(_CHECK_INTERVAL_SEC)
                continue

            load_analysis_config(reload=True)
            state = analysis_archive.load_scheduler_state()
            changed = False

            qc = qq_cfg()
            if qc.get("enabled"):
                _generate_today_if_due(qc, state)
                _retry_pending_pushes(qc, state)

                keep = int(qc.get("report_retention_days") or 7)
                if state.get("qq_purge_last") != date.today().strftime("%Y-%m-%d"):
                    removed = analysis_archive.purge_job_reports("qq_sentiment", keep)
                    if removed:
                        logging.info("已清理 %s 个过期 QQ 分析报告目录（保留 %s 天）", removed, keep)
                    log_removed = purge_qq_conversation_logs(keep)
                    if log_removed:
                        logging.info("已清理 %s 个过期 QQ 聊天日期目录（保留 %s 天）", log_removed, keep)
                    state["qq_purge_last"] = date.today().strftime("%Y-%m-%d")
                    changed = True

            if changed:
                analysis_archive.save_scheduler_state(state)
        except Exception as exc:  # pylint: disable=broad-except
            logging.exception("分析定时任务异常：%s", exc)
        time.sleep(_CHECK_INTERVAL_SEC)


def start_analysis_scheduler() -> None:
    load_analysis_config()
    t = threading.Thread(target=_analysis_loop, name="analysis-scheduler", daemon=True)
    t.start()
    logging.info("群聊分析定时器已启动（每天 %s 生成当天报告，时刻见 analysis_config.json）", _qq_daily_run_at(qq_cfg()))
