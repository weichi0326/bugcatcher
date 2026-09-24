# -*- coding: utf-8 -*-
"""Markdown → 飞书 interactive 消息卡片（尽量还原 Markdown 结构）。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from botskill.md_to_feishu_post import (
    _normalize_lang,
    _split_markdown_blocks,
    feishu_optimize_report_markdown,
    split_markdown_for_send,
)

# 单条 interactive 消息 content 建议 ≤30KB（留 JSON 开销）
_DEFAULT_MAX_CARD_BYTES = 28000
_HEADER_TEMPLATES = frozenset(
    {
        "blue",
        "wathet",
        "turquoise",
        "green",
        "yellow",
        "orange",
        "red",
        "purple",
        "violet",
        "indigo",
        "grey",
        "default",
    }
)


def _block_to_elements(kind: str, payload: str) -> List[Dict[str, Any]]:
    if kind == "hr":
        return [{"tag": "hr"}]
    if kind == "code":
        lang = "TEXT"
        code = payload
        if "\n" in payload:
            first, rest = payload.split("\n", 1)
            if first.strip() and not rest.startswith("```"):
                lang = _normalize_lang(first)
                code = rest
        code = (code or " ").strip("\n")
        fence_lang = lang.lower() if lang != "TEXT" else ""
        content = f"```{fence_lang}\n{code}\n```" if fence_lang else f"```\n{code}\n```"
        return [{"tag": "markdown", "content": content}]
    text = (payload or "").strip()
    if not text:
        return []
    return [{"tag": "markdown", "content": text}]


def _make_card(
    elements: List[Dict[str, Any]],
    *,
    title: str,
    header_template: str = "blue",
    wide_screen: bool = True,
) -> Dict[str, Any]:
    template = header_template if header_template in _HEADER_TEMPLATES else "blue"
    card: Dict[str, Any] = {
        "config": {"wide_screen_mode": wide_screen},
        "elements": elements,
    }
    title_text = (title or "").strip()
    if title_text:
        card["header"] = {
            "template": template,
            "title": {"tag": "plain_text", "content": title_text[:200]},
        }
    return card


def _chunk_to_elements(chunk: str) -> List[Dict[str, Any]]:
    elements: List[Dict[str, Any]] = []
    for kind, payload in _split_markdown_blocks(chunk):
        elements.extend(_block_to_elements(kind, payload))
    return elements or [{"tag": "markdown", "content": chunk.strip() or " "}]


def markdown_to_interactive_cards(
    markdown_text: str,
    *,
    card_title: Optional[str] = None,
    header_template: str = "blue",
    optimize: bool = True,
    max_card_bytes: int = _DEFAULT_MAX_CARD_BYTES,
) -> List[Dict[str, Any]]:
    """
    将 Markdown 转为一张或多张飞书 interactive 卡片 JSON（可直接作为 msg content 序列化）。
    """
    raw = (markdown_text or "").replace("\r\n", "\n").strip()
    text = feishu_optimize_report_markdown(raw) if optimize else raw
    if not text:
        return [
            _make_card(
                [{"tag": "markdown", "content": "（无报告内容）"}],
                title=card_title or "报告",
                header_template=header_template,
            )
        ]

    title_base = (card_title or "报告").strip()
    # 先按 Markdown 块边界分片，再每片转成一张卡片（控制 30KB 上限）
    chunk_limit = min(12000, max(4000, max_card_bytes // 3))
    chunks = split_markdown_for_send(text, max_chars=chunk_limit) or [text]
    cards: List[Dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        part_title = title_base if idx == 0 else f"{title_base}（续{idx + 1}）"
        elements = _chunk_to_elements(chunk)
        card = _make_card(elements, title=part_title, header_template=header_template)
        if len(json.dumps(card, ensure_ascii=False).encode("utf-8")) > max_card_bytes:
            # 单片仍过大：降级为单元素截断
            plain = chunk[:10000] + ("\n\n…（内容过长已截断）" if len(chunk) > 10000 else "")
            card = _make_card(
                [{"tag": "markdown", "content": plain}],
                title=part_title,
                header_template=header_template,
            )
        cards.append(card)
    return cards
