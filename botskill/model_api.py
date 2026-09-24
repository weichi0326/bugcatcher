# -*- coding: utf-8 -*-
"""OpenAI 兼容模型接口，供运行时和本地管理页共用。"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests


PROVIDERS = (
    {"id": "deepseek", "label": "DeepSeek", "base_url": "https://api.deepseek.com", "requires_key": True},
    {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1", "requires_key": True},
    {"id": "gemini", "label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "requires_key": True},
    {"id": "claude", "label": "Claude 兼容接口（评估用）", "base_url": "https://api.anthropic.com/v1", "requires_key": True},
    {"id": "xai", "label": "xAI Grok", "base_url": "https://api.x.ai/v1", "requires_key": True},
    {"id": "qwen", "label": "阿里云百炼 / 通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "requires_key": True},
    {"id": "zhipu", "label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "requires_key": True},
    {"id": "moonshot", "label": "Moonshot / Kimi", "base_url": "https://api.moonshot.cn/v1", "requires_key": True},
    {"id": "siliconflow", "label": "硅基流动", "base_url": "https://api.siliconflow.cn/v1", "requires_key": True},
    {"id": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "requires_key": True},
    {"id": "ollama", "label": "Ollama 本地", "base_url": "http://127.0.0.1:11434/v1", "requires_key": False},
    {"id": "custom", "label": "自定义兼容接口", "base_url": "", "requires_key": False},
)
_PROVIDER_MAP = {item["id"]: item for item in PROVIDERS}


class ModelApiError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def normalize_base_url(value: str, provider: str = "custom") -> str:
    """保留厂商路径前缀，兼容用户填入完整 chat 或 models 地址。"""
    raw = str(value or _PROVIDER_MAP.get(provider, {}).get("base_url") or "").strip().rstrip("/")
    if not raw or len(raw) > 1000:
        raise ModelApiError("请填写模型接口的 Base URL")
    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ModelApiError("Base URL 必须是有效的 HTTP 或 HTTPS 地址")
    if parsed.query or parsed.fragment:
        raise ModelApiError("Base URL 不能包含查询参数或片段")
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if not path and parsed.hostname != "api.deepseek.com":
        path = "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _headers(api_key: str, provider: str) -> dict[str, str]:
    key = str(api_key or "").strip()
    if _PROVIDER_MAP.get(provider, {}).get("requires_key") and not key:
        raise ModelApiError("请先填写该厂商的 API Key")
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if provider == "claude" and key:
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    if provider == "gemini":
        headers["x-goog-api-client"] = "qqbot-webui/1.0"
    return headers


def _response_error(response: requests.Response, action: str) -> ModelApiError:
    status = response.status_code
    if status in (401, 403):
        return ModelApiError(f"{action}失败（HTTP {status}）：请检查 API Key 和模型权限", 422)
    if status == 404 and action == "获取模型列表":
        return ModelApiError("该接口没有提供兼容的模型列表（HTTP 404）；可手填模型 ID 后测试", 422)
    return ModelApiError(f"{action}失败（HTTP {status}）；请检查 Base URL、模型 ID 与厂商接口", 422)


def _json_response(response: requests.Response, action: str) -> dict[str, Any]:
    if not response.ok:
        raise _response_error(response, action)
    try:
        data = response.json()
    except ValueError:
        raise ModelApiError(f"{action}失败：接口返回的不是 JSON") from None
    if not isinstance(data, dict):
        raise ModelApiError(f"{action}失败：接口返回格式无法识别")
    if data.get("error"):
        raise ModelApiError(f"{action}失败：接口返回错误，请检查模型配置")
    return data


def list_models(*, provider: str, base_url: str, api_key: str) -> tuple[list[dict[str, str]], int]:
    base = normalize_base_url(base_url, provider)
    headers = _headers(api_key, provider)
    start = time.perf_counter()
    try:
        params = {"sub_type": "chat"} if provider == "siliconflow" else None
        response = requests.get(f"{base}/models", headers=headers, params=params, timeout=(5, 20))
    except requests.RequestException as exc:
        raise ModelApiError(f"获取模型列表失败：{type(exc).__name__}；可检查网络或手填模型 ID") from None
    latency_ms = round((time.perf_counter() - start) * 1000)
    data = _json_response(response, "获取模型列表")
    rows = data.get("data", data.get("models"))
    if not isinstance(rows, list):
        raise ModelApiError("模型列表格式无法识别；可手填模型 ID 后测试")
    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in rows:
        if isinstance(item, str):
            model_id, name = item.strip(), item.strip()
        elif isinstance(item, dict):
            model_id = str(item.get("id") or item.get("model") or "").strip()
            name = str(item.get("name") or item.get("display_name") or model_id).strip()
        else:
            continue
        if model_id and model_id not in seen:
            models.append({"id": model_id, "name": name})
            seen.add(model_id)
    models.sort(key=lambda item: item["id"].casefold())
    return models[:5000], latency_ms


def chat_completion(
    *, provider: str, base_url: str, api_key: str, model: str,
    messages: list[dict], timeout: int = 60, temperature: float | None = None,
    response_format: dict | None = None,
) -> tuple[dict[str, Any], int]:
    if not str(model or "").strip():
        raise ModelApiError("请先选择或填写模型 ID")
    base = normalize_base_url(base_url, provider)
    headers = _headers(api_key, provider)
    headers["Content-Type"] = "application/json"
    payload: dict[str, Any] = {"model": str(model).strip(), "messages": messages, "stream": False}
    if temperature is not None:
        payload["temperature"] = temperature
    if response_format:
        payload["response_format"] = response_format
    start = time.perf_counter()
    for attempt in range(3):
        try:
            response = requests.post(
                f"{base}/chat/completions", headers=headers, json=payload,
                timeout=(5, max(5, min(int(timeout), 300))),
            )
        except requests.RequestException as exc:
            raise ModelApiError(f"模型调用失败：{type(exc).__name__}") from None
        if response.status_code == 400 and attempt < 2:
            message = response.text[:1000].lower()
            optional = next((key for key in ("temperature", "response_format") if key in payload and key in message), None)
            if optional and any(word in message for word in (
                "unsupported", "not support", "invalid", "unknown", "not allowed", "unexpected", "extra_forbidden", "不支持"
            )):
                del payload[optional]
                continue
        data = _json_response(response, "模型调用")
        if not isinstance(data.get("choices"), list) or not data["choices"]:
            raise ModelApiError("模型调用成功，但没有返回 choices")
        return data, round((time.perf_counter() - start) * 1000)
    raise ModelApiError("模型调用失败：接口不接受当前请求参数")


def test_model(*, provider: str, base_url: str, api_key: str, model: str) -> tuple[int, str]:
    data, latency_ms = chat_completion(
        provider=provider, base_url=base_url, api_key=api_key, model=model,
        messages=[{"role": "user", "content": "请只回复：好"}], timeout=35,
    )
    content = (data["choices"][0].get("message") or {}).get("content")
    preview = content.strip()[:120] if isinstance(content, str) else ""
    return latency_ms, preview
