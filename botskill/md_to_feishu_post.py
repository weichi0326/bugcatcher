# -*- coding: utf-8 -*-
"""
Markdown → 飞书 post 富文本（对齐开放平台 md / code_block / hr 等标签）。

DeepSeek 分析报告常用语法：加粗、斜体、删除线、代码块、表格、列表、引用、链接等。
表格在 md 标签内以标准 Markdown 表格语法透传；超长内容由 send 层分片。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

# 飞书 code_block language 枚举（不区分大小写）；未知语言映射为 TEXT
_FEISHU_LANG_MAP = {
    "py": "PYTHON",
    "python": "PYTHON",
    "js": "JAVASCRIPT",
    "javascript": "JAVASCRIPT",
    "ts": "TYPESCRIPT",
    "typescript": "TYPESCRIPT",
    "sh": "SHELL",
    "bash": "BASH",
    "shell": "SHELL",
    "yml": "YAML",
    "yaml": "YAML",
    "md": "TEXT",
    "markdown": "TEXT",
    "txt": "TEXT",
    "text": "TEXT",
    "": "TEXT",
}

_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")


def _normalize_lang(lang: str) -> str:
    key = (lang or "").strip().lower()
    mapped = _FEISHU_LANG_MAP.get(key)
    if mapped:
        return mapped
    upper = key.upper().replace("-", "_")
    if upper in {
        "PYTHON", "C", "CPP", "GO", "JAVA", "KOTLIN", "SWIFT", "PHP", "RUBY", "RUST",
        "JAVASCRIPT", "TYPESCRIPT", "BASH", "SHELL", "SQL", "JSON", "XML", "YAML",
        "HTML", "THRIFT", "TEXT",
    }:
        return upper
    return "TEXT"


def _is_table_row(line: str) -> bool:
    return bool(_TABLE_ROW_RE.match(line))


def _is_table_sep(line: str) -> bool:
    return bool(_TABLE_SEP_RE.match(line)) and "|" in line


def _split_markdown_blocks(text: str) -> List[Tuple[str, str]]:
    """
    将 Markdown 拆为 (kind, payload) 列表。
    kind: md | code | hr
    """
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: List[Tuple[str, str]] = []
    buf: List[str] = []
    i = 0
    n = len(lines)

    def flush_md() -> None:
        if not buf:
            return
        chunk = "\n".join(buf).strip("\n")
        buf.clear()
        if chunk.strip():
            blocks.append(("md", chunk))

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_md()
            fence = stripped[3:].strip()
            lang = fence
            code_lines: List[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < n:
                i += 1
            blocks.append(("code", f"{lang}\n" + "\n".join(code_lines)))
            continue

        if stripped in ("---", "***", "___") or re.fullmatch(r"-{3,}", stripped):
            flush_md()
            blocks.append(("hr", ""))
            i += 1
            continue

        if _is_table_row(line) or (_is_table_sep(line) and i + 1 < n and _is_table_row(lines[i + 1])):
            flush_md()
            table_lines = [line]
            i += 1
            while i < n and (_is_table_row(lines[i]) or _is_table_sep(lines[i])):
                table_lines.append(lines[i])
                i += 1
            blocks.append(("md", "\n".join(table_lines)))
            continue

        buf.append(line)
        i += 1

    flush_md()
    return blocks


def _blocks_to_post_rows(blocks: List[Tuple[str, str]]) -> List[List[Dict[str, Any]]]:
    rows: List[List[Dict[str, Any]]] = []
    for kind, payload in blocks:
        if kind == "hr":
            rows.append([{"tag": "hr"}])
            continue
        if kind == "code":
            lang = "TEXT"
            code = payload
            if "\n" in payload:
                first, rest = payload.split("\n", 1)
                if first.strip() and not rest.startswith("```"):
                    lang = _normalize_lang(first)
                    code = rest
            code = code.strip("\n")
            rows.append([{"tag": "code_block", "language": lang, "text": code or " "}])
            continue
        md_text = payload.strip()
        if md_text:
            rows.append([{"tag": "md", "text": md_text}])
    return rows


def markdown_to_feishu_post(markdown_text: str, post_title: Optional[str] = None) -> dict:
    """将 Markdown 转为飞书 post 结构（zh_cn）。"""
    text = (markdown_text or "").replace("\r\n", "\n").strip()
    if not text:
        zh_cn: Dict[str, Any] = {"content": [[{"tag": "text", "text": " "}]]}
        if post_title and str(post_title).strip():
            zh_cn["title"] = str(post_title).strip()
        return {"zh_cn": zh_cn}

    blocks = _split_markdown_blocks(text)
    content = _blocks_to_post_rows(blocks)
    if not content:
        content = [[{"tag": "md", "text": text}]]

    zh_cn: Dict[str, Any] = {"content": content}
    if post_title is not None and str(post_title).strip():
        zh_cn["title"] = str(post_title).strip()
    return {"zh_cn": zh_cn}


def split_markdown_for_send(markdown_text: str, max_chars: int = 12000) -> List[str]:
    """按块边界分片，避免单条 post 过大导致发送失败。"""
    text = (markdown_text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    blocks = _split_markdown_blocks(text)
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0

    for kind, payload in blocks:
        piece = payload if kind != "hr" else "\n --- \n"
        if kind == "code" and not piece.startswith("```"):
            lang, _, body = payload.partition("\n")
            piece = f"```{lang}\n{body}```" if lang else f"```\n{body}```"
        add_len = len(piece) + (2 if current else 0)
        if current and current_len + add_len > max_chars:
            flush()
        if len(piece) > max_chars and kind == "md":
            flush()
            start = 0
            while start < len(piece):
                chunks.append(piece[start : start + max_chars])
                start += max_chars
            continue
        current.append(piece)
        current_len += add_len

    flush()
    return chunks if chunks else [text[:max_chars]]


_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _clean_html_breaks(text: str) -> str:
    return _BR_RE.sub("\n", text or "")


def _clean_br_in_table(table_md: str) -> str:
    """表格行内 <br> 换成空格，避免拆行导致解析失败。"""
    return _BR_RE.sub(" ", table_md or "")


def _strip_md_bold(text: str) -> str:
    return re.sub(r"\*\*([^*]+)\*\*", r"\1", text or "")


def _parse_md_table(table_md: str) -> Optional[Tuple[List[str], List[List[str]]]]:
    lines = [ln for ln in (table_md or "").replace("\r\n", "\n").split("\n") if ln.strip()]
    if len(lines) < 2:
        return None
    if not all(_is_table_row(ln) or _is_table_sep(ln) for ln in lines):
        return None
    header = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    data_rows: List[List[str]] = []
    for ln in lines[1:]:
        if _is_table_sep(ln):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) == len(header):
            data_rows.append(cells)
    if not header or not data_rows:
        return None
    return header, data_rows


def _table_should_expand(
    header: List[str], rows: List[List[str]], *, max_cell: int = 18, min_cols: int = 3
) -> bool:
    if len(header) >= min_cols:
        return True
    for row in rows:
        for cell in row:
            plain = _strip_md_bold(cell)
            plain = _clean_html_breaks(plain).strip()
            if len(plain) > max_cell or "\n" in plain:
                return True
    return False


def _cell_lines_for_list(cell: str) -> List[str]:
    text = _clean_html_breaks(cell).strip()
    if not text:
        return []
    parts = re.split(r"\n+|(?<=[：:。])\s*(?=\d+\.\s)", text)
    return [p.strip() for p in parts if p.strip()]


def _table_to_list_block(table_md: str, *, force_list: bool = False) -> str:
    """飞书卡片内窄表格易竖排换行；长单元格改为列表排版。"""
    parsed = _parse_md_table(table_md) or _parse_md_table(_clean_br_in_table(table_md))
    if not parsed:
        return _clean_html_breaks(table_md)
    header, rows = parsed
    if not force_list and not _table_should_expand(header, rows):
        return _clean_html_breaks(table_md)

    blocks: List[str] = []
    for row in rows:
        cells = [row[i].strip() for i in range(len(header))]
        if not any(cells):
            continue
        title_bits = [_strip_md_bold(c) for c in cells[:2] if c]
        title = " · ".join(title_bits) if title_bits else _strip_md_bold(cells[0])
        blocks.append(f"\n**{title}**")
        start = 2 if len(cells) > 2 else 1
        for i in range(start, len(header)):
            cell = cells[i]
            if not cell:
                continue
            lines = _cell_lines_for_list(cell)
            if len(lines) <= 1:
                blocks.append(f"- **{header[i]}**：{lines[0] if lines else _clean_html_breaks(cell)}")
                continue
            blocks.append(f"- **{header[i]}**：")
            for ln in lines:
                blocks.append(f"  - {ln}")
    return "\n".join(blocks).strip()


# 问题展示条目：**描述**（等级）· 次数  → 用于推送前做三列对齐（次数可为数字或「多」）
_ISSUE_LINE_RE = re.compile(r"^\s*\*\*(.+?)\*\*[（(]([^（()]+)[）)]\s*[·・]\s*(\d+|多)\s*次\s*$")

# 描述列最大显示宽度（约屏幕 55%）：超过则折行，避免整列被最长描述撑宽。
MAX_DESC_W = 24


def _disp_w(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _wrap_display_text(text: str, width: int) -> List[str]:
    """按显示宽度把文本切成 <= width 的多段（尽量不打断，按字符保守断行）。"""
    text = (text or "").strip()
    if not text:
        return []
    out: List[str] = []
    cur = ""
    cur_w = 0
    for ch in text:
        w = _disp_w(ch)
        if cur and cur_w + w > width:
            out.append(cur)
            cur = ch
            cur_w = w
        else:
            cur += ch
            cur_w += w
    if cur:
        out.append(cur)
    return out


def _pad_units(text: str, width: int) -> str:
    """按显示宽度补齐：优先全角空格（宽 2），奇数余量补一个半角空格。"""
    pad = width - _disp_w(text)
    if pad <= 0:
        return text
    return text + ("　" * (pad // 2)) + (" " * (pad % 2))


def _center_units(text: str, width: int) -> str:
    """把 text 在 width 内按显示宽度居中。"""
    pad = width - _disp_w(text)
    if pad <= 0:
        return text
    left = pad // 2
    right = pad - left

    def _fill(u: int) -> str:
        return ("　" * (u // 2)) + (" " * (u % 2))

    return _fill(left) + text + _fill(right)


def _align_issue_lines(block: str) -> str:
    """把「问题展示」里 `**描述**（等级）· N 次` 的连续行，按三列对齐：描述左对齐、等级/次数居中。

    描述列宽以 MAX_DESC_W 为上限；超过上限的描述折行展示（每段 <= MAX_DESC_W），
    避免最长描述把整列撑宽导致消息框过满。
    """
    lines = block.split("\n")
    hits = []
    for i, line in enumerate(lines):
        m = _ISSUE_LINE_RE.match(line)
        if m:
            hits.append((i, m.group(1), m.group(2), m.group(3)))
    if not hits:
        return block
    desc_w = min(max(_disp_w(d) for _, d, _, _ in hits), MAX_DESC_W)
    out = list(lines)
    for i, desc, level, count in hits:
        cnt_str = (f"{count} 次") if count != "多" else "多次"
        if _disp_w(desc) <= MAX_DESC_W:
            out[i] = f"{_pad_units(desc, desc_w)}　{_center_units(level, 2)}　{_center_units(cnt_str, 5)}"
        else:
            chunks = _wrap_display_text(desc, MAX_DESC_W)
            wrapped = chunks[:-1] + [f"{chunks[-1]}　（{level}）· {cnt_str}"]
            out[i] = "\n".join(wrapped)
    return "\n".join(out)


def feishu_optimize_report_markdown(md: str) -> str:
    """
    推送飞书前优化排版：去掉无效 HTML 换行；表格改为列表，避免窄列竖排换行拉长消息框。
    """
    text = (md or "").replace("\r\n", "\n")
    if not text.strip():
        return text

    blocks = _split_markdown_blocks(text)
    out_parts: List[str] = []
    for kind, payload in blocks:
        if kind == "hr":
            out_parts.append("\n --- \n")
            continue
        if kind == "code":
            out_parts.append(payload if payload.startswith("```") else f"```\n{payload}```")
            continue
        chunk = payload.strip()
        if chunk and _is_table_row(chunk.split("\n")[0]):
            chunk = _table_to_list_block(chunk, force_list=True)
        else:
            chunk = _clean_html_breaks(chunk)
            chunk = _align_issue_lines(chunk)
        out_parts.append(chunk)
    return "\n\n".join(p for p in out_parts if p).strip()


def markdown_to_plain_for_fallback(md: str) -> str:
    """发送失败时降级为纯文本。"""
    s = feishu_optimize_report_markdown(md)
    if not s:
        return "（无内容）"
    s = re.sub(r"```(?:[a-zA-Z0-9_-]+)?\n([\s\S]*?)```", lambda m: m.group(1).strip(), s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", s)
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    s = re.sub(r"~(.+?)~", r"\1", s)
    s = re.sub(r"^>\s*", "", s, flags=re.MULTILINE)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return s.strip()
