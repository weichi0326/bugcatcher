# -*- coding: utf-8 -*-
"""从 conversation_logs 读取群聊记录（自然日 / 时间区间）。"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from botskill.paths import CONVERSATION_DIR

_META_NAME = "session.meta.json"
_CHINESE_DAY_DIR_RE = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$")


def _parse_chinese_date_dir(name: str) -> Optional[date]:
    m = _CHINESE_DAY_DIR_RE.match(str(name or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def purge_qq_conversation_logs(keep_days: int) -> int:
    """删除 conversation_logs 下 QQ 群目录中早于 keep_days 的日期子文件夹；飞书 oc_ 不删。"""
    if keep_days <= 0 or not os.path.isdir(CONVERSATION_DIR):
        return 0
    cutoff = date.today() - timedelta(days=keep_days)
    removed = 0
    for folder in os.listdir(CONVERSATION_DIR):
        if folder.startswith("oc_"):
            continue
        chat_dir = os.path.join(CONVERSATION_DIR, folder)
        if not os.path.isdir(chat_dir):
            continue
        meta = _read_meta(chat_dir)
        if str(meta.get("channel") or "").strip() == "feishu":
            continue
        for day_name in os.listdir(chat_dir):
            day_date = _parse_chinese_date_dir(day_name)
            if day_date is None or day_date >= cutoff:
                continue
            path = os.path.join(chat_dir, day_name)
            if not os.path.isdir(path):
                continue
            try:
                shutil.rmtree(path)
                removed += 1
                logging.info("已清理过期 QQ 聊天归档 %s/%s", folder, day_name)
            except OSError as exc:
                logging.warning("清理 QQ 聊天归档失败 %s: %s", path, exc)
    return removed


def chinese_date_folder(day: date) -> str:
    return f"{day.year}年{day.month}月{day.day}日"


def _day_prefix(day: date) -> str:
    return day.strftime("%Y-%m-%d")


def _parse_timestamp(raw: str) -> Optional[datetime]:
    ts = str(raw or "").strip()
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(ts[:19], fmt)
        except ValueError:
            continue
    return None


def _read_meta(chat_dir: str) -> dict:
    path = os.path.join(chat_dir, _META_NAME)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _message_on_day(record: dict, day: date) -> bool:
    ts = str(record.get("timestamp") or "").strip()
    return ts.startswith(_day_prefix(day))


def _message_in_range(record: dict, start: datetime, end: datetime) -> bool:
    dt = _parse_timestamp(str(record.get("timestamp") or ""))
    if dt is None:
        return False
    return start <= dt <= end


def _format_line(record: dict) -> str:
    ts = str(record.get("timestamp") or "")
    text = str(record.get("text") or "").strip()
    msg_type = str(record.get("message_type") or "").strip().lower()
    role = str(record.get("role") or "").strip().lower()
    if msg_type == "notice" or role == "system":
        return f"[{ts}] 系统: {text}"
    uid = str(record.get("user_id") or "").strip()
    if uid or role == "user":
        who = f"用户{uid}" if uid else "用户"
        return f"[{ts}] {who}: {text}"
    return f"[{ts}] 助手: {text}"


def _iter_json_records(chat_dir: str, day: Optional[date] = None) -> List[dict]:
    records: List[dict] = []
    if day is not None:
        dirs = [os.path.join(chat_dir, chinese_date_folder(day))]
    else:
        dirs = []
        if os.path.isdir(chat_dir):
            for name in os.listdir(chat_dir):
                p = os.path.join(chat_dir, name)
                if os.path.isdir(p) and "年" in name and "月" in name and "日" in name:
                    dirs.append(p)
    for day_dir in dirs:
        if not os.path.isdir(day_dir):
            continue
        for name in sorted(os.listdir(day_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(day_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (json.JSONDecodeError, OSError):
                continue
            msgs = data.get("messages") if isinstance(data, dict) else None
            if isinstance(msgs, list):
                for rec in msgs:
                    if isinstance(rec, dict):
                        records.append(rec)
    return records


def _load_lines_for_day(chat_dir: str, day: date) -> List[str]:
    day_dir = os.path.join(chat_dir, chinese_date_folder(day))
    lines: List[str] = []
    for rec in _iter_json_records(chat_dir, day):
        if _message_on_day(rec, day):
            line = _format_line(rec)
            if line.strip():
                lines.append(line)
    if not lines:
        lines = _load_legacy_log(chat_dir, day)
    return lines


def _load_lines_for_range(chat_dir: str, start: datetime, end: datetime) -> List[str]:
    lines: List[str] = []
    day = start.date()
    end_day = end.date()
    while day <= end_day:
        for rec in _iter_json_records(chat_dir, day):
            if _message_in_range(rec, start, end):
                line = _format_line(rec)
                if line.strip():
                    lines.append(line)
        if not lines and day == start.date():
            legacy = _load_legacy_log(chat_dir, day)
            for line in legacy:
                dt = _parse_timestamp(line[1:20] if len(line) > 20 else "")
                if dt and start <= dt <= end:
                    lines.append(line)
        from datetime import timedelta

        day += timedelta(days=1)
    return lines


def _load_legacy_log(chat_dir: str, day: date) -> List[str]:
    lines: List[str] = []
    prefix = _day_prefix(day)
    block_re = re.compile(
        r"===== (USER|ASSISTANT) (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) =====",
        re.MULTILINE,
    )
    for name in os.listdir(chat_dir):
        if not name.endswith(".log"):
            continue
        path = os.path.join(chat_dir, name)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                content = handle.read()
        except OSError:
            continue
        for match in block_re.finditer(content):
            role, ts = match.group(1), match.group(2)
            if not ts.startswith(prefix):
                continue
            start = match.end()
            nxt = block_re.search(content, start)
            end = nxt.start() if nxt else len(content)
            body = content[start:end]
            text_match = re.search(
                r"-----BEGIN_TEXT-----\s*(.*?)\s*-----END_TEXT-----",
                body,
                re.DOTALL,
            )
            if text_match:
                text = text_match.group(1).strip()
            else:
                rich = re.search(
                    r"-----BEGIN_RICH_TEXT-----\s*(.*?)\s*-----END_RICH_TEXT-----",
                    body,
                    re.DOTALL,
                )
                text = rich.group(1).strip() if rich else ""
            label = "用户" if role == "USER" else "助手"
            if text:
                lines.append(f"[{ts}] {label}: {text}")
    return lines


def list_sessions(
    *,
    whitelist: List[str],
) -> List[Dict[str, str]]:
    """列出 QQ 归档会话（白名单内、非飞书）。"""
    allow = {str(x).strip() for x in whitelist if str(x).strip()}
    if not allow or not os.path.isdir(CONVERSATION_DIR):
        return []
    hits: List[Dict[str, str]] = []
    for folder in os.listdir(CONVERSATION_DIR):
        chat_dir = os.path.join(CONVERSATION_DIR, folder)
        if not os.path.isdir(chat_dir):
            continue
        meta = _read_meta(chat_dir)
        session_id = str(meta.get("session_id") or meta.get("numeric_id") or "").strip()
        ch = str(meta.get("channel") or "").strip()
        chat_id = str(meta.get("chat_id") or "").strip()
        if not session_id:
            if folder in allow:
                session_id = folder
            elif folder.startswith("group_"):
                session_id = folder[6:]
            elif folder.startswith("oc_"):
                session_id = folder
        if session_id not in allow:
            continue
        # 仅 QQ：跳过飞书（oc_ / channel=feishu）会话
        if ch == "feishu" or session_id.startswith("oc_"):
            continue
        if ch and ch not in ("qq", "group", "private", ""):
            if not str(session_id).isdigit():
                continue
        hits.append(
            {
                "folder": folder,
                "chat_dir": chat_dir,
                "session_id": session_id,
                "chat_id": chat_id,
                "channel": ch or "qq",
            }
        )
    return hits


def collect_day_transcript(
    session: Dict[str, str],
    day: date,
    *,
    max_messages: int = 0,
    include_lines: bool = False,
) -> tuple:
    chat_dir = session["chat_dir"]
    lines = _load_lines_for_day(chat_dir, day)
    if max_messages > 0 and len(lines) > max_messages:
        lines = lines[-max_messages:]
    header = (
        f"会话ID: {session.get('session_id')}\n"
        f"归档目录: {session.get('folder')}\n"
        f"统计日期: {_day_prefix(day)}\n"
        f"消息条数: {len(lines)}\n"
        "---\n"
    )
    body = "\n".join(lines) if lines else "（当日无文本消息归档）"
    result = (header + body, len(lines))
    return (*result, lines) if include_lines else result


def collect_intraday_transcript(
    session: Dict[str, str],
    *,
    max_messages: int = 0,
    include_lines: bool = False,
) -> tuple:
    """当日 0:00 至当前时刻。"""
    now = datetime.now()
    start = datetime.combine(now.date(), time.min)
    return collect_range_transcript(
        session, start, now, max_messages=max_messages, include_lines=include_lines
    )


def collect_range_transcript(
    session: Dict[str, str],
    start: datetime,
    end: datetime,
    *,
    max_messages: int = 0,
    include_lines: bool = False,
) -> tuple:
    """统计区间 [start, end] 的文本消息（跨自然日：读取区间内所有日期子目录并按时戳过滤）。"""
    chat_dir = session["chat_dir"]
    lines = _load_lines_for_range(chat_dir, start, end)
    if max_messages > 0 and len(lines) > max_messages:
        lines = lines[-max_messages:]
    header = (
        f"会话ID: {session.get('session_id')}\n"
        f"归档目录: {session.get('folder')}\n"
        f"统计区间: {start.strftime('%Y-%m-%d %H:%M:%S')} ~ {end.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"消息条数: {len(lines)}\n"
        "---\n"
    )
    body = "\n".join(lines) if lines else "（区间内无文本消息归档）"
    result = (header + body, len(lines))
    return (*result, lines) if include_lines else result


def pack_qq_batches(
    sessions: List[Dict[str, str]],
    *,
    collect_fn,
    max_bytes_per_batch: int,
) -> Tuple[List[str], int, List[Dict[str, str]]]:
    """
    多群数据按 UTF-8 字节上限分片。collect_fn(session) -> (text, count, lines)。
    lines 中每项为一条完整消息；旧的 (text, count) 返回值仍可使用，
    但超大整群文本会报错，不再按字节截断。
    返回 (batch_payloads, total_messages, group_stats)。
    """
    limit = int(max_bytes_per_batch)
    if limit <= 0:
        raise ValueError("单批字节上限必须大于 0")
    group_stats: List[Dict[str, str]] = []
    total = 0
    batches: List[str] = []
    current_parts: List[str] = []
    current_size = 0

    def _flush() -> None:
        nonlocal current_parts, current_size
        if current_parts:
            batches.append("".join(current_parts))
            current_parts = []
            current_size = 0

    for sess in sessions:
        sid = sess["session_id"]
        collected = collect_fn(sess)
        text, count = collected[:2]
        total += count
        group_stats.append(
            {"session_id": sid, "message_count": count, "folder": sess.get("folder", "")}
        )
        if len(collected) > 2:
            metadata, separator, _body = text.partition("---\n")
            if not separator:
                raise ValueError(f"QQ群 {sid} 归档格式缺少元数据分隔符")
            lines = collected[2] or ["（无聊天内容）"]
            group_header = f"\n\n========== QQ群 {sid} ==========\n{metadata}{separator}"
        else:
            lines = [text]
            group_header = f"\n\n========== QQ群 {sid} ==========\n"

        group_started = False
        for line in lines:
            prefix = "" if group_started else group_header
            part = prefix + str(line) + "\n"
            part_size = len(part.encode("utf-8"))
            if current_size + part_size > limit:
                _flush()
                group_started = False
                part = group_header + str(line) + "\n"
                part_size = len(part.encode("utf-8"))
            if part_size > limit:
                raise ValueError(
                    f"QQ群 {sid} 存在超过单批上限 {limit} 字节的消息或元数据；"
                    "请缩短该消息或移除该条归档后重试"
                )
            current_parts.append(part)
            current_size += part_size
            group_started = True
    _flush()

    if not batches:
        batches.append("（无聊天内容）")
    return batches, total, group_stats
