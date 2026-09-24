# -*- coding: utf-8 -*-
"""分析报告存档：飞书永久保留，QQ 报告 7 天清理。"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from botskill.analysis.config import get_archive_dir

QQ_REPORT_STEM = "舆情分析_BUG汇总"


def qq_report_title(day: date) -> str:
    """飞书推送标题、日志与报告抬头（统计日 = 所分析的自然日）。"""
    return f"{day.month}月{day.day}日外网缺陷收集分析报告"


def qq_report_range_title(start_day: date, end_day: date) -> str:
    """多日区间报告标题：同日退化为单日标题。"""
    if start_day == end_day:
        return qq_report_title(end_day)
    return f"{start_day.month}月{start_day.day}日-{end_day.month}月{end_day.day}日外网缺陷收集分析报告"


def _safe_name(text: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in (text or ""))[:80] or "report"


def job_dir(job: str, day: date) -> str:
    day_key = day.strftime("%Y-%m-%d")
    return os.path.join(get_archive_dir(), job, day_key)


def qq_report_path(day: date) -> str:
    folder = job_dir("qq_sentiment", day)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{_safe_name(QQ_REPORT_STEM)}_{day.isoformat()}.md")


def qq_report_range_path(start_day: date, end_day: date) -> str:
    """多日区间报告存储路径：落在结束日目录，文件名含起止日期。"""
    folder = job_dir("qq_sentiment", end_day)
    os.makedirs(folder, exist_ok=True)
    stem = f"{QQ_REPORT_STEM}_{start_day.isoformat()}_{end_day.isoformat()}"
    return os.path.join(folder, f"{_safe_name(stem)}.md")


def save_report(
    job: str,
    day: date,
    filename_stem: str,
    body: str,
    *,
    meta: Optional[Dict[str, Any]] = None,
    overwrite: bool = True,
) -> str:
    folder = job_dir(job, day)
    os.makedirs(folder, exist_ok=True)
    stem = _safe_name(filename_stem)
    md_path = os.path.join(folder, f"{stem}.md")
    payload = body if body.endswith("\n") else body + "\n"
    mode = "w" if overwrite else "a"
    if not overwrite and os.path.isfile(md_path):
        with open(md_path, "a", encoding="utf-8") as handle:
            handle.write("\n\n---\n\n" + payload)
    else:
        with open(md_path, mode, encoding="utf-8") as handle:
            handle.write(payload)
    if meta:
        meta_path = os.path.join(folder, f"{stem}.meta.json")
        meta_out = {
            "job": job,
            "day": day.strftime("%Y-%m-%d"),
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **meta,
        }
        with open(meta_path, "w", encoding="utf-8") as handle:
            json.dump(meta_out, handle, ensure_ascii=False, indent=2)
    return md_path


def append_report_section(
    md_path: str,
    title: str,
    body: str,
) -> str:
    """向已有报告末尾追加章节。"""
    section = f"\n\n---\n\n## {title}\n\n{body.strip()}\n"
    if os.path.isfile(md_path):
        with open(md_path, "a", encoding="utf-8") as handle:
            handle.write(section)
    else:
        os.makedirs(os.path.dirname(md_path), exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as handle:
            handle.write(f"# {title}\n\n{body.strip()}\n")
    return md_path


def read_report_body(md_path: str) -> str:
    if not os.path.isfile(md_path):
        return ""
    try:
        with open(md_path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def purge_job_reports(job: str, keep_days: int) -> int:
    """删除 keep_days 天之前的报告目录。keep_days<=0 表示不清理。返回删除目录数。"""
    if keep_days <= 0:
        return 0
    root = os.path.join(get_archive_dir(), job)
    if not os.path.isdir(root):
        return 0
    cutoff = date.today() - timedelta(days=keep_days)
    removed = 0
    for name in os.listdir(root):
        try:
            folder_date = datetime.strptime(name[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if folder_date < cutoff:
            path = os.path.join(root, name)
            try:
                shutil.rmtree(path)
                removed += 1
                logging.info("已清理过期分析报告 %s/%s", job, name)
            except OSError as exc:
                logging.warning("清理报告失败 %s: %s", path, exc)
    return removed


def load_scheduler_state() -> Dict[str, Any]:
    path = os.path.join(get_archive_dir(), ".scheduler_state.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_scheduler_state(state: Dict[str, Any]) -> None:
    root = get_archive_dir()
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, ".scheduler_state.json")
    fd, temp_path = tempfile.mkstemp(prefix=".scheduler_state.", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
