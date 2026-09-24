"""模型兼容地址、探测与运行调用。"""

import unittest
from unittest.mock import patch

from botskill import ai_models, model_api


class FakeResponse:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self.ok = 200 <= status < 300
        self.data = data
        self.text = text

    def json(self):
        return self.data


class ModelApiTests(unittest.TestCase):
    def test_base_url_keeps_provider_prefixes_and_legacy_deepseek(self):
        self.assertEqual(model_api.normalize_base_url("https://api.deepseek.com", "deepseek"),
                         "https://api.deepseek.com")
        self.assertEqual(model_api.normalize_base_url(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "qwen"),
            "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(model_api.normalize_base_url("http://127.0.0.1:11434", "ollama"),
                         "http://127.0.0.1:11434/v1")

    def test_model_list_uses_compatible_endpoint_and_handles_missing_list(self):
        with patch.object(model_api.requests, "get", return_value=FakeResponse(200, {
            "data": [{"id": "model-b"}, {"id": "model-a", "name": "A"}, {"id": "model-a"}]
        })) as get:
            models, latency = model_api.list_models(
                provider="qwen", base_url="", api_key="test-key")
        self.assertEqual([item["id"] for item in models], ["model-a", "model-b"])
        self.assertGreaterEqual(latency, 0)
        self.assertEqual(get.call_args.args[0],
                         "https://dashscope.aliyuncs.com/compatible-mode/v1/models")
        with patch.object(model_api.requests, "get", return_value=FakeResponse(404)):
            with self.assertRaisesRegex(model_api.ModelApiError, "手填模型 ID"):
                model_api.list_models(provider="qwen", base_url="", api_key="test-key")

    def test_chat_retries_without_unsupported_temperature(self):
        responses = [
            FakeResponse(400, {"error": {"message": "unsupported temperature"}},
                         '{"error":{"message":"unsupported temperature"}}'),
            FakeResponse(200, {"choices": [{"message": {"content": "好"}}]}),
        ]
        sent_payloads = []
        def respond(*args, **kwargs):
            sent_payloads.append(dict(kwargs["json"]))
            return responses.pop(0)

        with patch.object(model_api.requests, "post", side_effect=respond):
            data, _ = model_api.chat_completion(
                provider="openai", base_url="", api_key="test-key", model="demo-model",
                messages=[{"role": "user", "content": "测试"}], temperature=0.35)
        self.assertEqual(data["choices"][0]["message"]["content"], "好")
        self.assertIn("temperature", sent_payloads[0])
        self.assertNotIn("temperature", sent_payloads[1])

    def test_runtime_uses_selected_analysis_model(self):
        config = {"text": {"provider": "ollama", "base_url": "http://127.0.0.1:11434/v1",
                           "api_key": "", "model": "local-analysis"}}
        with patch.object(ai_models, "MODELS", config):
            with patch.object(model_api.requests, "post", return_value=FakeResponse(200, {
                "choices": [{"message": {"content": "分析完成"}}]
            })) as post:
                content, error = ai_models.deepseek_chat([{"role": "user", "content": "分析"}])
        self.assertEqual((content, error), ("分析完成", ""))
        self.assertEqual(post.call_args.kwargs["json"]["model"], "local-analysis")
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:11434/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
