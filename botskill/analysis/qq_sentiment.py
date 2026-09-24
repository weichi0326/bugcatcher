# -*- coding: utf-8 -*-
"""QQ 游戏测试群：定时 23:00 / 手动触发，均分析当日 0:00～触发时刻并覆写当日报告；报告保留 7 天。"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

from botskill.analysis import archive as analysis_archive
from botskill.analysis.config import normalize_whitelist, qq_cfg
from botskill.analysis.ds_runner import run_qq_analysis, run_qq_batched_analysis
from botskill.analysis.log_reader import (
    collect_day_transcript,
    collect_intraday_transcript,
    collect_range_transcript,
    list_sessions,
    pack_qq_batches,
)
from botskill.analysis.push import push_qq_analysis_report
from botskill.analysis.qq_content_gate import should_skip_qq_report

_DEFAULT_BATCH_BYTES = 32 * 1024
_REPORT_HEADER_RESERVE = 1024


def _qq_task_prompt(cfg: dict) -> str:
    return str(cfg.get("task_system_prompt") or "").strip()


def _merge_prompt(cfg: dict) -> str:
    return str(cfg.get("merge_batch_system_prompt") or "").strip()


def _max_bytes_per_batch(cfg: dict) -> int:
    raw = cfg.get("transcript_max_bytes_per_batch")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = _DEFAULT_BATCH_BYTES
    if value <= 0:
        value = _DEFAULT_BATCH_BYTES
    # 旧配置中的 1 GiB 上限按安全的模型输入预算处理。
    return min(_DEFAULT_BATCH_BYTES, max(2048, value))


def _analyze_batches(cfg: dict, batches: List[str]) -> tuple[str, str]:
    task = _qq_task_prompt(cfg)
    merge = _merge_prompt(cfg)
    if len(batches) <= 1:
        return run_qq_analysis(task, batches[0] if batches else "")
    return run_qq_batched_analysis(
        task, batches, merge_system_prompt=merge,
        merge_max_bytes=_max_bytes_per_batch(cfg),
    )


def _insufficient_report(
    cfg: dict,
    *,
    mode: str,
    day: date,
    total: int,
    group_stats: List[Dict[str, str]],
    report_title: str,
    do_push: bool,
    filename_stem: Optional[str] = None,
    push_to: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """数据不足：仍写一份简短报告并推送，避免无结果。"""
    body = (
        f"当日 QQ 归档聊天消息数量不足（共 {total} 条），暂不足以支撑完整的舆情分析。\n"
        "请确认 NapCat 已登录、测试群有消息记录后重试。"
    )
    stem = filename_stem or f"{analysis_archive.QQ_REPORT_STEM}_{day.isoformat()}"
    path = analysis_archive.save_report(
        "qq_sentiment",
        day,
        stem,
        f"# {report_title}\n\n{body}\n",
        meta={
            "mode": mode,
            "report_title": report_title,
            "total_messages": total,
            "groups": group_stats,
            "insufficient": True,
        },
        overwrite=True,
    )
    pushed_to = _push_all(cfg, body, title=report_title, targets=push_to) if do_push else []
    return {
        "ok": True,
        "mode": mode,
        "report_title": report_title,
        "day": str(day),
        "path": path,
        "total_messages": total,
        "batches": 0,
        "groups": group_stats,
        "pushed_to": pushed_to,
        "insufficient": True,
    }


def _push_all(cfg: dict, report: str, *, title: str, targets: Optional[List[str]] = None) -> List[str]:
    """QQ 分析结果统一由飞书机器人推送。"""
    if not report.strip():
        return []
    return push_qq_analysis_report(cfg, report, title=title, targets=targets)


def run_qq_scheduled_daily(
    target_day: Optional[date] = None,
    *,
    push: Optional[bool] = None,
    abort_check: Optional[Any] = None,
    push_to: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """定时/补跑：分析指定自然日全天（00:00–23:59）QQ 归档，写入该日目录并推送飞书。

    push_to：指定推送目标群；为空时用配置 feishu_push_chat_ids。
    """
    cfg = qq_cfg()
    if not cfg.get("enabled"):
        return {"ok": False, "reason": "qq_game_sentiment 未启用"}

    whitelist = normalize_whitelist(cfg.get("group_whitelist"))
    if not whitelist:
        return {"ok": False, "reason": "QQ group_whitelist 为空"}

    def _aborted() -> bool:
        try:
            return bool(abort_check and abort_check())
        except Exception:
            return False

    day = target_day or date.today()
    do_push = cfg.get("push_to_feishu", True) if push is None else bool(push)
    max_msgs = int(cfg.get("max_messages_per_group") or 0)
    max_bytes = _max_bytes_per_batch(cfg)
    mode = "scheduled_daily"
    report_title = analysis_archive.qq_report_title(day)

    sessions = list_sessions(whitelist=whitelist)
    if not sessions:
        return _insufficient_report(
            cfg, mode=mode, day=day, total=0, group_stats=[], report_title=report_title,
            do_push=(do_push and not _aborted()), push_to=push_to,
        )

    today = date.today()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _collect(sess: dict):
        if day == today:
            return collect_intraday_transcript(sess, max_messages=max_msgs, include_lines=True)
        return collect_day_transcript(sess, day, max_messages=max_msgs, include_lines=True)

    try:
        batches, total, group_stats = pack_qq_batches(
            sessions, collect_fn=_collect,
            max_bytes_per_batch=max_bytes - _REPORT_HEADER_RESERVE,
        )
    except ValueError as exc:
        return {"ok": False, "day": str(day), "error": str(exc)}
    range_note = f"00:00:00 ~ {stamp}" if day == today else "00:00:00 ~ 23:59:59"
    header = (
        f"【{report_title}·定时总结】\n"
        f"统计日期: {day.isoformat()}（{range_note}）\n"
        f"生成时间: {stamp}\n"
        f"分片数: {len(batches)}\n"
        f"合计消息: {total}\n"
        "---\n"
    )
    batches[0] = header + batches[0]
    if len(batches[0].encode("utf-8")) > max_bytes:
        return {"ok": False, "day": str(day), "error": "报告头超出单批输入上限"}

    skip, _reason = should_skip_qq_report(total, batches)
    if skip:
        return _insufficient_report(
            cfg, mode=mode, day=day, total=total, group_stats=group_stats, report_title=report_title,
            do_push=(do_push and not _aborted()), push_to=push_to,
        )

    report, err = _analyze_batches(cfg, batches)
    if err:
        return {"ok": False, "day": str(day), "error": err, "groups": group_stats}

    path = analysis_archive.qq_report_path(day)
    body = f"# {report_title}\n\n{report.strip()}\n"
    analysis_archive.save_report(
        "qq_sentiment",
        day,
        f"{analysis_archive.QQ_REPORT_STEM}_{day.isoformat()}",
        body,
        meta={
            "mode": mode,
            "report_title": report_title,
            "total_messages": total,
            "batches": len(batches),
            "groups": group_stats,
        },
        overwrite=True,
    )

    pushed_to = _push_all(cfg, body, title=report_title, targets=push_to) if (do_push and not _aborted()) else []
    logging.info("%s day=%s save=%s batches=%s", report_title, day, path, len(batches))
    return {
        "ok": True,
        "mode": mode,
        "report_title": report_title,
        "analyzed_day": str(day),
        "path": path,
        "total_messages": total,
        "batches": len(batches),
        "groups": group_stats,
        "pushed_to": pushed_to,
    }


def run_qq_manual_range(
    *,
    days: int = 1,
    push: Optional[bool] = None,
    abort_check: Optional[Any] = None,
    push_to: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """手动：统计区间 = 今天往前推 days-1 天的 0:00 至当前。

    days=1 即日报（当日 0:00→现在）；days=3 三日报（今天-2 的 0:00→现在）；days=7 七日报同理。
    报告落在结束日（今天）目录，文件名含起止日期；覆写同区间文件。
    abort_check：可调用对象，返回真表示任务已被守门狗判定超时，此时不再推送报告，避免「失败后又推送」。
    push_to：指定推送目标群（如发起命令的群）；为空时用配置 feishu_push_chat_ids。
    """
    days = max(1, int(days or 1))
    cfg = qq_cfg()
    if not cfg.get("enabled"):
        return {"ok": False, "reason": "qq_game_sentiment 未启用"}

    whitelist = normalize_whitelist(cfg.get("group_whitelist"))
    if not whitelist:
        return {"ok": False, "reason": "QQ group_whitelist 为空"}

    def _aborted() -> bool:
        try:
            return bool(abort_check and abort_check())
        except Exception:
            return False

    now = datetime.now()
    end_day = now.date()
    start_day = end_day - timedelta(days=days - 1)
    start = datetime.combine(start_day, time.min)
    end = now
    do_push = cfg.get("push_to_feishu", True) if push is None else bool(push)
    max_msgs = int(cfg.get("max_messages_per_group") or 0)
    max_bytes = _max_bytes_per_batch(cfg)
    mode = f"manual_range_{days}d"
    report_title = analysis_archive.qq_report_range_title(start_day, end_day)
    stem = f"{analysis_archive.QQ_REPORT_STEM}_{start_day.isoformat()}_{end_day.isoformat()}"

    sessions = list_sessions(whitelist=whitelist)
    if not sessions:
        return _insufficient_report(
            cfg, mode=mode, day=end_day, total=0, group_stats=[],
            report_title=report_title, do_push=(do_push and not _aborted()), filename_stem=stem,
            push_to=push_to,
        )

    def _collect(sess: dict):
        return collect_range_transcript(
            sess, start, end, max_messages=max_msgs, include_lines=True
        )

    try:
        batches, total, group_stats = pack_qq_batches(
            sessions, collect_fn=_collect,
            max_bytes_per_batch=max_bytes - _REPORT_HEADER_RESERVE,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    batches[0] = (
        f"【{days}日舆情·手动触发】\n"
        f"统计区间: {start.strftime('%Y-%m-%d %H:%M:%S')} ~ {end.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"分片数: {len(batches)}\n"
        f"合计消息: {total}\n"
        "---\n"
        + batches[0]
    )
    if len(batches[0].encode("utf-8")) > max_bytes:
        return {"ok": False, "error": "报告头超出单批输入上限"}

    skip, _reason = should_skip_qq_report(total, batches)
    if skip:
        return _insufficient_report(
            cfg, mode=mode, day=end_day, total=total, group_stats=group_stats,
            report_title=report_title, do_push=(do_push and not _aborted()), filename_stem=stem,
            push_to=push_to,
        )

    report, err = _analyze_batches(cfg, batches)
    if err:
        return {"ok": False, "error": err, "groups": group_stats}

    path = analysis_archive.qq_report_range_path(start_day, end_day)
    body = f"# {report_title}\n\n{report.strip()}\n"
    analysis_archive.save_report(
        "qq_sentiment",
        end_day,
        stem,
        body,
        meta={
            "mode": mode,
            "report_title": report_title,
            "start_day": str(start_day),
            "end_day": str(end_day),
            "days": days,
            "total_messages": total,
            "batches": len(batches),
            "groups": group_stats,
        },
        overwrite=True,
    )

    pushed_to = _push_all(cfg, body, title=report_title, targets=push_to) if (do_push and not _aborted()) else []
    logging.info(
        "%s（%d日） start=%s end=%s path=%s messages=%s",
        report_title, days, start_day, end_day, path, total,
    )
    return {
        "ok": True,
        "mode": mode,
        "report_title": report_title,
        "start_day": str(start_day),
        "end_day": str(end_day),
        "day": str(end_day),
        "days": days,
        "path": path,
        "total_messages": total,
        "batches": len(batches),
        "groups": group_stats,
        "pushed_to": pushed_to,
    }


def run_qq_manual_intraday(
    *,
    push: Optional[bool] = None,
) -> Dict[str, Any]:
    """手动：当日 0:00 至当前（日报）；覆写当日报告。兼容定时/旧调用名。"""
    return run_qq_manual_range(days=1, push=push)


def run_qq_game_sentiment(
    target_day: Optional[date] = None,
    *,
    push: Optional[bool] = None,
    manual_intraday: bool = False,
) -> Dict[str, Any]:
    """无 manual：指定日期则分析该自然日全天；未指定日期则当日 0:00~现在并追加到今日报告。"""
    if manual_intraday:
        return run_qq_manual_intraday(push=push)
    if target_day is None:
        return run_qq_manual_intraday(push=push)
    return run_qq_scheduled_daily(target_day=target_day, push=push)
