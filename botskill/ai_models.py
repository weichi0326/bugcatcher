# -*- coding: utf-8 -*-
from __future__ import annotations

"""
主文本模型调用（OpenAI 兼容接口）。

说明：
- 聊天模式已关闭，不再需要「人格 system 前缀 + 多轮 history」的对话链路。
- 本模块仅保留 `deepseek_chat`：纯 HTTP 调用主文本模型，供 QQ 舆情分析使用
  （具体任务 system prompt 由分析模块拼装）。
"""

import logging
from typing import List, Optional

from config import BOT_CONFIG, MODELS
from botskill.model_api import ModelApiError, chat_completion


def deepseek_chat(
    messages: List[dict],
    *,
    temperature: Optional[float] = None,
    response_format: Optional[dict] = None,
) -> tuple[Optional[str], str]:
    """
    纯 HTTP 调用主文本模型，不读写会话历史。
    成功返回 (content, "")；失败返回 (None, 面向用户的简短中文错误说明)。
    """
    model_config = MODELS.get("text", {})
    provider = str(model_config.get("provider") or "deepseek")
    api_key = str(model_config.get("api_key") or "")
    if provider not in ("ollama", "custom") and (not api_key or api_key == "sk-xxx"):
        return None, (
            "我这边暂时连不上主模型：密钥尚未配置好。"
            "请在模型配置里补全后再试。"
        )
    base_url = str(model_config.get("base_url") or "")
    model = model_config.get("model", "deepseek-v4-pro")
    temp = float(model_config.get("temperature", BOT_CONFIG.get("model_text_temperature", 0.7)))
    if temperature is not None:
        temp = float(temperature)
    request_timeout = int(model_config.get("request_timeout_sec", BOT_CONFIG.get("model_request_timeout_sec", 60)))
    try:
        data, _ = chat_completion(
            provider=provider, base_url=base_url, api_key=api_key, model=str(model),
            messages=messages, temperature=temp, response_format=response_format,
            timeout=request_timeout,
        )
    except ModelApiError as exc:
        logging.warning("主模型请求失败：%s", exc)
        return None, (
            "主模型这次没有顺利接通，请核对模型、密钥与接口地址后再试。"
        )
    choices = data.get("choices") or []
    if not choices:
        return None, "我这边没有收到完整的回复内容，方便换个说法或稍后再试一次吗？"
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, list):
        content = "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    answer = content.strip() if isinstance(content, str) else ""
    if not answer:
        return None, "回复内容为空，我还需要一点更具体的信息才能帮上忙。"
    return answer, ""
