# -*- coding: utf-8 -*-
"""将 QQ 舆情分析报告推送到飞书（走飞书机器人）。"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from botskill import feishu_client
from botskill.analysis.config import (
    normalize_whitelist,
    qq_cfg,
    report_push_card_header_template,
    report_push_use_interactive_card,
)
from botskill.md_to_feishu_post import feishu_optimize_report_markdown
from botskill.md_to_feishu_card import markdown_to_interactive_cards
from botskill.qq_client import _normalize_feishu_group_chat_id


def _chunk_text(text: str, limit: int = 3500) -> list[str]:
    body = (text or "").strip()
    if not body:
        return []
    if len(body) <= limit:
        return [body]
    chunks: list[str] = []
    start = 0
    while start < len(body):
        chunks.append(body[start : start + limit])
        start += limit
    return chunks


def feishu_post_display_title(title: str) -> str:
    """飞书 post 卡片标题（唯一主标题，勿再在正文重复）。"""
    t = (title or "").strip() or "外网缺陷收集分析报告"
    # 旧报告标题带固定角色前缀；保留读取兼容，新的卡片不再附加角色名。
    old_title = re.fullmatch(r"【姜澄·(.+)】", t)
    return old_title.group(1).strip() if old_title else t


def _strip_duplicate_heading_markdown(report: str, title: str) -> str:
    """去掉正文开头与标题重复的一级标题行（# xxx），避免推送出现三条标题。"""
    text = (report or "").strip()
    if not text:
        return text
    plain = (title or "").strip()
    plain_no_bracket = feishu_post_display_title(plain)
    lines = text.splitlines()
    while lines:
        first = lines[0].strip()
        if not first:
            lines.pop(0)
            continue
        normalized = first.lstrip("#").strip()
        if normalized in (plain, plain_no_bracket) or feishu_post_display_title(normalized) == plain_no_bracket:
            lines.pop(0)
            continue
        break
    return "\n".join(lines).lstrip("\n")


def resolve_feishu_chat_id(session_id: str, meta_chat_id: str) -> str:
    cid = (meta_chat_id or "").strip()
    if cid.startswith("oc_") or cid.startswith("c2c:"):
        return cid
    sid = (session_id or "").strip()
    if sid.startswith("oc_"):
        return sid
    return _normalize_feishu_group_chat_id(sid) if sid else ""


def resolve_qq_feishu_push_chat_ids(cfg: Optional[dict] = None) -> List[str]:
    """QQ 外网缺陷报告推送目标：qq_game_sentiment.feishu_push_chat_ids 中的全部 oc_ 群。"""
    qc = cfg if cfg is not None else qq_cfg()
    if not qc.get("push_to_feishu", True):
        return []
    return normalize_whitelist(qc.get("feishu_push_chat_ids"))


def push_qq_report_to_feishu_interactive(
    feishu_chat_ids: List[str],
    report: str,
    *,
    title: str = "外网缺陷收集分析报告",
    header_template: str = "blue",
) -> List[str]:
    """以飞书 interactive 消息卡片推送（尽量还原 Markdown）；失败时降级 post 富文本。"""
    pushed: List[str] = []
    display = feishu_post_display_title(title)
    body = feishu_optimize_report_markdown(
        _strip_duplicate_heading_markdown((report or "").strip(), title)
    )
    if not body.strip():
        body = "（无报告内容）"
    cards = markdown_to_interactive_cards(
        body,
        card_title=display,
        header_template=header_template,
    )
    for cid in feishu_chat_ids:
        chat_id = resolve_feishu_chat_id(cid, cid)
        if not chat_id:
            logging.warning("飞书卡片推送跳过：无效 chat_id %r（不重试）", cid)
            continue
        if feishu_client.send_interactive_cards(chat_id, cards):
            pushed.append(chat_id)
            logging.info(
                "飞书卡片推送成功 chat_id=%s title=%r cards=%s",
                chat_id,
                title,
                len(cards),
            )
            continue
        logging.warning(
            "飞书卡片推送失败 chat_id=%s，降级 post 富文本（不重试卡片）",
            chat_id,
        )
        if push_qq_report_to_feishu([cid], report, title=title):
            pushed.append(chat_id)
        else:
            logging.error(
                "飞书推送失败 chat_id=%s（可能未加机器人或权限不足），已跳过且不重试",
                chat_id,
            )
    return pushed


def push_qq_report_to_feishu(
    feishu_chat_ids: List[str],
    report: str,
    *,
    title: str = "外网缺陷收集分析报告",
) -> List[str]:
    """
    向多个飞书群各推送一次；某群失败（如机器人不在群内）仅记日志，不重试、不阻塞其它群。
    返回成功推送的 chat_id 列表。
    """
    pushed: List[str] = []
    display = feishu_post_display_title(title)
    body = feishu_optimize_report_markdown(
        _strip_duplicate_heading_markdown((report or "").strip(), title)
    )
    if not body.strip():
        body = "（无报告内容）"
    for cid in feishu_chat_ids:
        chat_id = resolve_feishu_chat_id(cid, cid)
        if not chat_id:
            logging.warning("飞书推送跳过：无效 chat_id %r（不重试）", cid)
            continue
        parts = _chunk_text(body)
        ok = True
        for idx, part in enumerate(parts or [body]):
            chunk_title = display if idx == 0 else f"{display}（续{idx + 1}）"
            if not feishu_client.send_rich_message(
                chat_id,
                part,
                post_title=chunk_title,
            ):
                ok = False
                break
        if ok:
            pushed.append(chat_id)
            logging.info("飞书推送成功 chat_id=%s title=%r", chat_id, title)
        else:
            logging.error(
                "飞书推送失败 chat_id=%s（可能未加机器人或权限不足），已跳过且不重试",
                chat_id,
            )
    return pushed


def push_qq_analysis_report(
    cfg: dict,
    report: str,
    *,
    title: str,
    targets: Optional[List[str]] = None,
) -> List[str]:
    """按 analysis_config 将 QQ 分析报告推送到飞书。

    targets 为空时用配置里的 feishu_push_chat_ids；否则以 targets 为准
    （用于"在哪个群提出就只推送到哪个群"，避免跨群推送）。
    """
    if not cfg.get("push_to_feishu", True):
        return []
    push_targets = normalize_whitelist(targets) if targets else resolve_qq_feishu_push_chat_ids(cfg)
    if not push_targets:
        logging.warning("推送目标为空，跳过 QQ 报告推送")
        return []
    if report_push_use_interactive_card():
        return push_qq_report_to_feishu_interactive(
            push_targets,
            report,
            title=title,
            header_template=report_push_card_header_template(),
        )
    return push_qq_report_to_feishu(push_targets, report, title=title)
