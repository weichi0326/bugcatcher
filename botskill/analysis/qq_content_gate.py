# -*- coding: utf-8 -*-
"""QQ 舆情/缺陷日报：归档无有效内容时不调用 DS、不写报告、不推送。"""

from __future__ import annotations

import re
from typing import List, Sequence, Tuple

_EMPTY_MARKERS = (
    "（无聊天内容）",
    "（当日无文本消息归档）",
    "（区间内无文本消息归档）",
)

_MSG_LINE_RE = re.compile(
    r"^\[\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\]\s+(?:用户\d+|助手):\s*(.*)$",
    re.MULTILINE,
)

_TRIVIAL_EXACT = frozenset(
    {
        "嗯",
        "哦",
        "啊",
        "额",
        "好",
        "好的",
        "收到",
        "知道了",
        "ok",
        "OK",
        "Ok",
        "666",
        "+1",
        "哈哈",
        "哈哈哈",
        "呵呵",
        "行",
        "可以",
        "赞成",
        "顶",
        "赞",
        "谢谢",
        "多谢",
        "在吗",
        "？",
        "?",
        "。",
        "…",
        "x",
        "X",
        "X﹏X",
        "x﹏x",
        "orz",
        "ORZ",
        "qaq",
        "QAQ",
        "awsl",
        "AWSL",
    }
)

_VALUE_HINT_RE = re.compile(
    r"bug|缺陷|闪退|卡顿|崩溃|异常|黑屏|卡死|无法|报错|登录|支付|匹配|掉线|回档|"
    r"数据|奖励|闪屏|延迟|掉帧|复现|版本|外网|玩家|测试|反馈|吐槽|不满",
    re.IGNORECASE,
)


def _extract_message_texts(batches: Sequence[str]) -> List[str]:
    texts: List[str] = []
    for batch in batches or []:
        if not batch:
            continue
        if any(marker in batch for marker in _EMPTY_MARKERS) and "========== QQ群" not in batch:
            continue
        for match in _MSG_LINE_RE.finditer(batch):
            body = (match.group(1) or "").strip()
            if body:
                texts.append(body)
    return texts


def _is_substantive_message(text: str) -> bool:
    from botskill.onebot_client import is_trivial_qq_archive_text

    if is_trivial_qq_archive_text(text):
        return False
    t = (text or "").strip()
    if _VALUE_HINT_RE.search(t):
        return True
    if len(t) >= 8:
        return True
    # 较短但含明显中文叙述（非纯符号/数字）
    cjk = re.findall(r"[\u4e00-\u9fff]", t)
    if len(cjk) >= 4:
        return True
    return False


def should_skip_qq_report(
    total_messages: int,
    batches: Sequence[str],
) -> Tuple[bool, str]:
    """
    判断是否跳过 QQ 外网缺陷报告生成。
    返回 (skip, reason)。
    """
    if int(total_messages or 0) <= 0:
        return True, "当日 QQ 归档消息条数为 0，已跳过报告生成与推送"

    texts = _extract_message_texts(batches)
    if not texts:
        return True, "当日 QQ 归档无有效文本消息，已跳过报告生成与推送"

    substantive = sum(1 for t in texts if _is_substantive_message(t))
    if substantive <= 0:
        return (
            True,
            f"当日 QQ 消息共 {total_messages} 条，但均为空内容、寒暄或无效短句，已跳过报告生成与推送",
        )

    return False, ""
