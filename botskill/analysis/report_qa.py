# -*- coding: utf-8 -*-
"""报告检索问答：@机器人 问「关于XXX有什么看法/反馈吗」→ 从最近 N 天分析报告检索并回答。

- 纯本地检索（不调用 DeepSeek），秒回。
- 只响应「明确询问某主题反馈/看法」的问题；闲聊/非明确询问返回 None，由上层拒绝。
- 回答通过 text 消息回发并 @ 提问用户。

启发式意图识别（可用性优先，可按需用 DeepSeek 增强）：提取「查询对象」并做常见闲聊主题过滤。
"""

from __future__ import annotations

import glob
import os
import re
from datetime import date, datetime, timedelta
from typing import List, Optional

from botskill.analysis.config import get_archive_dir
from botskill.ai_models import deepseek_chat

_AT_RE = re.compile(r"<at\b[^>]*>.*?</at>", re.IGNORECASE | re.DOTALL)
_GREETING_RE = re.compile(
    r"^(?:在吗|你好|hello|hi|哈喽|早上好|中午好|下午好|晚上好|帮忙|帮我|请|请问)\s*[，,。]?\s*",
    re.IGNORECASE,
)
_QUESTION_WORDS = ("吗", "？", "?", "怎么样", "怎么看", "有什么看法", "看法", "评价", "反馈", "如何", "呢", "怎么了")

# 明确指向「玩家反馈/看法/评价」的提示词（用于区分闲聊与真实询问）
_FEEDBACK_HINTS = ("玩家", "大家", "他们", "用户", "反馈", "看法", "评价", "意见", "怎么看待", "怎么看", "如何看待", "觉得")
# 明显与游戏反馈无关的闲聊主题（命中则拒绝，不回答）
_STOPLIST = frozenset(
    {
        "天气", "下雨", "吃饭", "午饭", "晚饭", "早饭", "上班", "下班", "工作", "学习",
        "电影", "游戏", "软件", "电脑", "手机", "今天", "明天", "昨天", "时间", "几点",
        "忙不忙", "在不在", "好不好", "行不行",
    }
)

# 提取查询对象的模式（按优先级）
_PATTERNS = [
    re.compile(r"关于(.+?)(?:的|有|怎么|看法|反馈|认为|觉得|怎么样|如何|是什么|吗|呢|情况|？|\?)"),
    re.compile(r"对(.+?)(?:的|有|怎么|看法|反馈|评价)"),
    re.compile(r"玩家(.+?)(?:怎么|有|说|反馈|觉得|认为|看)"),
    re.compile(r"(.+?)(?:怎么样|怎么看|有什么看法|如何|有反馈吗|怎么评价|怎么样啊)"),
]

# 去掉主题前缀（得到真正的检索对象）
_PREFIX_STRIP = ("玩家", "卡牌", "这个", "那个", "该", "当前", "最近", "今天")


def _clean_subject(subj: str) -> str:
    subj = (subj or "").strip().strip(" 　，,。：:的了吗呢？！?")
    for p in _PREFIX_STRIP:
        if subj.startswith(p) and len(subj) > len(p):
            subj = subj[len(p):].strip(" 　，,。的了吗呢？！?")
            break
    return subj.strip()


def extract_qa_subject(raw_text: str) -> Optional[str]:
    """若文本是「关于XXX有什么看法/反馈吗」类明确询问，返回检索对象；否则 None。"""
    t = _AT_RE.sub("", raw_text or "").strip()
    t = _GREETING_RE.sub("", t).strip()
    t = re.sub(r"[\u200b\ufeff]+", "", t)
    t = t.strip(" 　，,。！!~～")
    if not t or len(t) < 4:
        return None
    if not any(w in t for w in _QUESTION_WORDS) and not any(h in t for h in _FEEDBACK_HINTS):
        return None
    for pat in _PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        subj = _clean_subject(m.group(1))
        if not subj or len(subj) < 2 or subj.isdigit():
            continue
        if subj in _STOPLIST:
            continue
        return subj
    return None


def is_probable_question(raw_text: str) -> bool:
    """快速粗滤：是否是「可能在问什么」的消息。是则交给 DS 判定，否则立即拒绝（避免白调 DS）。

    只要带疑问词/反馈提示词就放行；纯寒暄（哈哈哈/你好/在吗）返回 False 直接拒绝。
    """
    t = _AT_RE.sub("", raw_text or "").strip()
    t = _GREETING_RE.sub("", t).strip()
    t = re.sub(r"[\u200b\ufeff]+", "", t).strip()
    if not t:
        return False
    return any(w in t for w in _QUESTION_WORDS) or any(h in t for h in _FEEDBACK_HINTS)


def _parse_day_dir(name: str) -> Optional[date]:
    try:
        return datetime.strptime(str(name)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _report_files(days: int = 7) -> List[tuple]:
    """最近 days 天、每天取一份报告：从今天往前回溯（今天→昨天→…→today-(days-1)）。

    同一天若同时存在单日报告与区间报告，优先单日报告（当日分析）；无单日则取该目录最新一份。
    """
    now = date.today()
    root = os.path.join(get_archive_dir(), "qq_sentiment")
    out: List[tuple] = []
    if not os.path.isdir(root):
        return out
    for offset in range(max(1, int(days or 1))):
        d = now - timedelta(days=offset)
        day_dir = os.path.join(root, d.strftime("%Y-%m-%d"))
        if not os.path.isdir(day_dir):
            continue
        files = [f for f in glob.glob(os.path.join(day_dir, "*.md")) if os.path.isfile(f)]
        if not files:
            continue
        # 每天取内容最完整（体积最大）的一份，跳过空壳报告
        path = max(files, key=os.path.getsize)
        out.append((d, path))
    return out


def search_reports(subject: str, days: int = 7, max_hits: int = 3, max_lines: int = 8) -> List[dict]:
    """在最近 days 天报告里检索 subject，返回 [{date, lines}]。"""
    subj = (subject or "").strip().lower()
    if not subj:
        return []
    results: List[dict] = []
    for d, path in _report_files(days):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            continue
        matched = [ln.strip() for ln in content.split("\n") if subj in ln.lower() and ln.strip()]
        if matched:
            results.append({"date": d.isoformat(), "lines": matched[:max_lines]})
        if len(results) >= max_hits:
            break
    return results


def build_qa_answer(subject: str, user_id: str, days: int = 7) -> str:
    """构造回答（text，含 @ 用户）。"""
    mention = f'<at user_id="{user_id}"></at>'
    results = search_reports(subject, days=days)
    if not results:
        return (
            f"{mention} 最近 {days} 天报告里暂时没有「{subject}」的相关反馈。\n"
            "建议先触发一次今天的分析（/舆情明细）看看最新情况；若仍没有，可能这一阶段玩家还没提到它。"
        )
    parts = [f"{mention} 最近 {days} 天报告里关于「{subject}」的反馈如下："]
    for r in results:
        parts.append(f"\n【{r['date']}】")
        parts.extend(f"- {ln}" for ln in r["lines"])
    return "\n".join(parts)


def build_qa_context(days: int = 7, max_chars: int = 6000) -> str:
    """拼接最近 days 天报告正文（封顶 max_chars），供 DS 理解与回答。"""
    parts: List[str] = []
    total = 0
    for d, path in _report_files(days):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read().strip()
        except OSError:
            continue
        if not content:
            continue
        head = f"【{d.isoformat()}】\n{content}\n"
        if total + len(head) > max_chars:
            parts.append(head[: max_chars - total])
            break
        parts.append(head)
        total += len(head)
    return "\n".join(parts).strip() or "（无可用报告）"


def _qa_ds(question: str, context: str) -> tuple:
    """调用 DeepSeek 判断是否游戏反馈类询问并据报告回答。返回 (content, err)。"""
    system = "请依据下方最近报告内容回答游戏测试舆情相关问题；报告未包含的内容不要编造。"
    user = (
        "判断下面这条群消息是否为「明确询问某游戏主题（玩家/卡牌/机制/版本内容）的反馈、看法或评价」。\n"
        "- 若否（闲聊、打招呼、非游戏问题、命令）：只回复：NO_QA\n"
        "- 若是：结合下方最近报告，用简洁中文总结该主题的反馈（可少量分点，提到具体卡牌/机制/现象/等级；报告没写的不编造）。"
        "若报告完全没提到该主题：只回复：无反馈\n\n"
        f"【最近报告内容】\n{context}\n\n"
        f"【群消息】\n{question}\n"
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return deepseek_chat(messages, temperature=0.3)


def answer_qa_via_ds(question: str, user_id: str, days: int = 7) -> Optional[str]:
    """DS 增强版回答：判定意图 + 总结报告。返回含 @用户的回复；None 表示非询问（调用方拒绝）。"""
    context = build_qa_context(days=days)
    content, err = _qa_ds(question, context)
    if err:
        # DS 失败 → 本地回退：仍尽力用正则主题 + 本地检索回答
        subject = extract_qa_subject(question)
        if subject:
            return build_qa_answer(subject, user_id, days=days)
        return None
    content = (content or "").strip()
    if content.upper().startswith("NO_QA"):
        return None
    mention = f'<at user_id="{user_id}"></at>'
    if content.startswith("无反馈"):
        return (
            f"{mention} 最近 {days} 天报告里暂时没有该主题的相关反馈。\n"
            "建议先跑一次今天的分析（/舆情明细）看看最新情况。"
        )
    # 去掉可能残留的前导"回复："等，保证以内容开头
    content = re.sub(r"^(回复[:：]|好的[:：]|嗯[:：])\s*", "", content).strip()
    return f"{mention}\n{content}"
