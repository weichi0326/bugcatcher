# -*- coding: utf-8 -*-
"""一次性清洗 conversation_logs：剔除 QQ 不再归档的消息，去掉 role 字段。"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from botskill.onebot_client import should_drop_qq_stored_message  # noqa: E402
from botskill.paths import CONVERSATION_DIR  # noqa: E402

_DIALOG_SUFFIX = "对话文件.json"


def _sanitize_record(record: dict, channel: str) -> dict | None:
    if not isinstance(record, dict):
        return None
    out = {k: v for k, v in record.items() if k != "role"}
    if str(channel or "").strip() == "qq" and should_drop_qq_stored_message(out):
        return None
    return out


def _clean_json_file(path: str) -> tuple[int, int, int]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"跳过（无法读取）: {path} — {exc}")
        return 0, 0, 0

    if not isinstance(data, dict):
        return 0, 0, 0

    channel = str(data.get("channel") or "").strip()
    if not channel:
        session_id = str(data.get("session_id") or "")
        channel = "feishu" if session_id.startswith("oc_") else "qq"

    msgs = data.get("messages")
    if not isinstance(msgs, list):
        return 0, 0, 0

    before = len(msgs)
    had_role = any(isinstance(rec, dict) and "role" in rec for rec in msgs)
    cleaned: list[dict] = []
    removed = 0
    for rec in msgs:
        kept = _sanitize_record(rec, channel)
        if kept is None:
            removed += 1
            continue
        cleaned.append(kept)

    if removed == 0 and not had_role:
        return before, before, 0

    data["messages"] = cleaned
    tmp = path + ".tmp"
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(payload)
    os.replace(tmp, path)
    return before, len(cleaned), removed


def main() -> int:
    if not os.path.isdir(CONVERSATION_DIR):
        print(f"目录不存在: {CONVERSATION_DIR}")
        return 1

    files = 0
    total_before = 0
    total_after = 0
    total_removed = 0

    for root, _dirs, names in os.walk(CONVERSATION_DIR):
        for name in names:
            if name != _DIALOG_SUFFIX:
                continue
            path = os.path.join(root, name)
            files += 1
            before, after, removed = _clean_json_file(path)
            total_before += before
            total_after += after
            total_removed += removed
            rel = os.path.relpath(path, CONVERSATION_DIR)
            if removed or before != after:
                print(f"{rel}: {before} → {after}（删除 {removed}）")
            else:
                print(f"{rel}: {before} 条（仅去掉 role）")

    print(
        f"\n完成：{files} 个文件，{total_before} 条 → {total_after} 条，"
        f"共删除 {total_removed} 条无效/不再归档消息。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
