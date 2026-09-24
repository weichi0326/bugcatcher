# -*- coding: utf-8 -*-
"""
模型分析：单次 / 分片批次 + 合并。

- QQ 分析会话：analysis:qq_sentiment（每次分析注入通用规则）
- 不写入 SESSIONS.messages；与 oc_* 日常会话隔离
"""

from __future__ import annotations

import logging
from typing import List

from botskill.ai_models import deepseek_chat
from botskill.persona_sync import (
    ANALYSIS_SESSION_QQ,
    build_analysis_persona_messages,
    resolve_persona_profile,
)

_DEFAULT_MERGE_PROMPT = (
    "以下是同一统计周期内多批次聊天记录的【分批分析摘要】。"
    "请合并为一份完整、去重的最终报告，保留所有缺陷/BUG 要点与优先级建议，"
    "不要编造各批次摘要中未出现的内容。使用 Markdown。"
)
_DEFAULT_MERGE_MAX_BYTES = 32 * 1024


def _persona_messages(job_chat_id: str, *, inject_persona: bool) -> List[dict]:
    if not inject_persona:
        return []
    return build_analysis_persona_messages(job_chat_id)


def run_analysis(
    job_chat_id: str,
    task_system_prompt: str,
    user_payload: str,
    *,
    temperature: float = 0.35,
    inject_persona: bool = True,
) -> tuple[str, str]:
    task = (task_system_prompt or "").strip()
    if not task:
        return "", "未配置 task_system_prompt"
    content = (user_payload or "").strip()
    if not content:
        return "", "无聊天内容可分析"

    messages: List[dict] = list(_persona_messages(job_chat_id, inject_persona=inject_persona))
    messages.append({"role": "system", "content": task})
    messages.append(
        {
            "role": "user",
            "content": (
                "请严格基于以下归档聊天记录完成分析，不要编造记录中不存在的事实。\n\n"
                f"{content}"
            ),
        }
    )
    answer, err = deepseek_chat(messages, temperature=temperature)
    if err:
        logging.warning(
            "分析任务 DS 失败 chat=%s profile=%s: %s",
            job_chat_id,
            resolve_persona_profile(job_chat_id),
            err,
        )
        return "", err
    return answer or "", ""


def run_batched_analysis(
    job_chat_id: str,
    task_system_prompt: str,
    batch_payloads: List[str],
    *,
    merge_system_prompt: str = "",
    merge_max_bytes: int = _DEFAULT_MERGE_MAX_BYTES,
    temperature: float = 0.35,
) -> tuple[str, str]:
    """多分片：仅首片注入完整规则；合并步仅 task prompt（避免重复长文）。"""
    if not batch_payloads:
        return "", "无分片数据"
    if len(batch_payloads) == 1:
        return run_analysis(
            job_chat_id,
            task_system_prompt,
            batch_payloads[0],
            temperature=temperature,
            inject_persona=True,
        )

    partials: List[str] = []
    batch_task = (
        (task_system_prompt or "").strip()
        + "\n\n【本分片说明】以下为多群聊天记录的一个分片，请仅针对本分片输出结构化摘要"
        "（缺陷/BUG 列表、现象、群号、频次），勿编造。"
    )
    for idx, chunk in enumerate(batch_payloads, start=1):
        header = f"【分片 {idx}/{len(batch_payloads)}】\n"
        report, err = run_analysis(
            job_chat_id,
            batch_task,
            header + chunk,
            temperature=temperature,
            inject_persona=(idx == 1),
        )
        if err:
            return "", f"分片{idx}分析失败: {err}"
        partials.append(f"### 分片 {idx} 摘要\n\n{report}")

    merge_prompt = (merge_system_prompt or _DEFAULT_MERGE_PROMPT).strip()
    # 给 run_analysis 附加的用户指令留余量，合并步也遵守单次输入预算。
    payload_budget = merge_max_bytes - len(merge_prompt.encode("utf-8")) - 1024
    if payload_budget < 1024:
        return "", "合并提示词过长，无法在单次模型输入预算内执行"

    current = partials
    for _round in range(12):
        groups: List[List[str]] = []
        group: List[str] = []
        group_size = len("【各分片分析摘要】\n\n".encode("utf-8"))
        for summary in current:
            part_size = len(("\n\n### 分片摘要\n\n" + summary).encode("utf-8"))
            if part_size + len("【各分片分析摘要】\n\n".encode("utf-8")) > payload_budget:
                return "", "单个分片摘要超过合并输入预算，请缩短模型摘要"
            if group and group_size + part_size > payload_budget:
                groups.append(group)
                group = []
                group_size = len("【各分片分析摘要】\n\n".encode("utf-8"))
            group.append(summary)
            group_size += part_size
        if group:
            groups.append(group)
        if len(groups) == len(current) and len(groups) > 1:
            return "", "各分片摘要无法合并进同一输入预算，请缩短模型摘要"

        merged: List[str] = []
        for group in groups:
            if len(group) == 1 and len(groups) > 1:
                merged.append(group[0])
                continue
            payload = "【各分片分析摘要】\n\n" + "".join(
                "\n\n### 分片摘要\n\n" + item for item in group
            )
            report, err = run_analysis(
                job_chat_id,
                merge_prompt,
                payload,
                temperature=temperature,
                inject_persona=False,
            )
            if err:
                return "", err
            merged.append(report)
        if len(groups) == 1:
            return merged[0], ""
        current = merged
    return "", "分片摘要合并轮数超过上限，请缩短模型摘要"


def run_qq_analysis(
    task_system_prompt: str,
    user_payload: str,
    *,
    temperature: float = 0.35,
) -> tuple[str, str]:
    jid = ANALYSIS_SESSION_QQ
    return run_analysis(
        jid, task_system_prompt, user_payload, temperature=temperature, inject_persona=True
    )


def run_qq_batched_analysis(
    task_system_prompt: str,
    batch_payloads: List[str],
    *,
    merge_system_prompt: str = "",
    merge_max_bytes: int = _DEFAULT_MERGE_MAX_BYTES,
    temperature: float = 0.35,
) -> tuple[str, str]:
    return run_batched_analysis(
        ANALYSIS_SESSION_QQ,
        task_system_prompt,
        batch_payloads,
        merge_system_prompt=merge_system_prompt,
        merge_max_bytes=merge_max_bytes,
        temperature=temperature,
    )


def qq_job_chat_id() -> str:
    return ANALYSIS_SESSION_QQ
