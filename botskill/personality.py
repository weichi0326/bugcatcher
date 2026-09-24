# -*- coding: utf-8 -*-
"""
唤醒判定（分析模式简化版）。

只保留 _has_at_mention：判断消息是否真的 @ 了机器人，供 im_handler 在分析模式下
决定是回复「分析模式」提示还是保持静默。
"""

import re


def _has_at_mention(raw_feishu_text: str, logical_text: str) -> bool:
    """判定消息是否真的 @ 了机器人。

    strip_im_markup 会去掉 <at> 标签，因此必须在 raw 原文里探测：
      - 飞书 <at ...>...</at> / <at .../>
      - 通用 <@...>、@昵称、@_user_N 等占位
    仅当原文含真正的 @ 提及，才视为命中。
    """
    _ = logical_text
    raw = str(raw_feishu_text or "")
    if re.search(r"<at\b[^>]*>", raw, flags=re.IGNORECASE) or re.search(r"<@[^>]+>", raw):
        return True
    if re.search(r"@\s*[\w\u4e00-\u9fff_-]+", raw):
        return True
    return False
