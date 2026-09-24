"""本地管理界面的文件边界和基本工作流。"""

import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config_web
from config import config_master


class ConfigWebTests(unittest.TestCase):
    def test_operations_manager_is_initialized_once_under_concurrent_requests(self):
        def create(root):
            time.sleep(0.02)
            return SimpleNamespace(root=root.resolve())

        with patch.object(config_web, "_OPERATIONS", None), patch.object(
            config_web, "OperationManager", side_effect=create
        ) as constructor, ThreadPoolExecutor(max_workers=6) as pool:
            managers = list(pool.map(lambda _: config_web._operations(), range(6)))
        constructor.assert_called_once()
        self.assertTrue(all(manager is managers[0] for manager in managers))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project_patch = patch.object(config_web, "PROJECT_ROOT", self.root)
        self.project_patch.start()
        self.addCleanup(self.project_patch.stop)
        self.client = config_web.app.test_client()

        config = self.root / "config"
        (config / "prompts").mkdir(parents=True)
        (config / "配置主表.json").write_text(
            json.dumps({"bot": {"conversation_log_dir": "conversation_logs"},
                        "analysis": {"archive_dir": "analysis_reports",
                                     "qq_game_sentiment": {"task_system_prompt_file": "config/prompts/task.md"}}}, ensure_ascii=False),
            encoding="utf-8",
        )
        (config / "配置主表.example.json").write_text("{}", encoding="utf-8")
        (config / "analysis_config.json").write_text(
            '{"archive_dir": "analysis_reports"}', encoding="utf-8"
        )
        (config / "prompts" / "task.md").write_text("旧提示词", encoding="utf-8")
        (config / "prompts" / "unused.md").write_text("未被引用", encoding="utf-8")
        (config / "配置说明.md").write_text("说明", encoding="utf-8")
        napcat = self.root / "napcat" / "shell" / "resources" / "app" / "napcat" / "config"
        napcat.mkdir(parents=True)
        (napcat / "onebot11_test.json").write_text("{}", encoding="utf-8")

        archive = self.root / "conversation_logs" / "group_1" / "2026年9月24日"
        archive.mkdir(parents=True)
        (archive / "对话文件.json").write_text(
            json.dumps({"channel": "qq", "session_id": "1", "messages": [
                {"timestamp": "2026-09-24 10:00:00", "text": "第一条"},
                {"timestamp": "2026-09-24 10:01:00", "text": "第二条"},
                {"timestamp": "2026-09-24 10:02:00", "text": "第三条"},
            ]}, ensure_ascii=False), encoding="utf-8"
        )
        report = self.root / "analysis_reports" / "qq_sentiment" / "2026-09-24"
        report.mkdir(parents=True)
        (report / "日报.md").write_text("# 测试日报\n", encoding="utf-8")
        (report / "日报.meta.json").write_text('{"total_messages":3}', encoding="utf-8")

    def test_file_listing_and_overview(self):
        response = self.client.get("/api/files?scope=config")
        self.assertEqual(response.status_code, 200)
        files = {item["path"]: item for item in response.json["files"]}
        self.assertEqual(set(files), {"config/prompts/task.md"})
        self.assertTrue(files["config/prompts/task.md"]["editable"])
        self.assertEqual(files["config/prompts/task.md"]["kind"], "source_markdown")
        for hidden in ("config/analysis_config.json", "config/配置说明.md", "config/prompts/unused.md",
                       "napcat/shell/resources/app/napcat/config/onebot11_test.json"):
            self.assertEqual(self.client.get("/api/file", query_string={"scope": "config", "path": hidden}).status_code, 403)
        self.assertEqual(self.client.get("/api/file", query_string={
            "scope": "config", "path": "config/配置主表.json"}).status_code, 200)
        stats = self.client.get("/api/overview").json["stats"]
        self.assertEqual((stats["archive_files"], stats["report_files"], stats["sessions"]), (1, 1, 1))

    def test_config_save_validation_and_conflict(self):
        path = "config/配置主表.json"
        original = self.client.get("/api/file", query_string={"scope": "config", "path": path}).json
        base = {"scope": "config", "path": path, "etag": original["etag"]}
        bad = self.client.put("/api/file", json={**base, "content": "{"})
        self.assertEqual(bad.status_code, 400)
        good = self.client.put("/api/file", json={**base, "content": '{"bot": {"port": 5001}}'})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(json.loads((self.root / path).read_text(encoding="utf-8"))["bot"]["port"], 5001)
        conflict = self.client.put("/api/file", json={**base, "content": '{}'})
        self.assertEqual(conflict.status_code, 409)
        readonly = self.client.put("/api/file", json={
            "scope": "config", "path": "config/analysis_config.json", "etag": "x", "content": "{}"
        })
        self.assertEqual(readonly.status_code, 403)

    def test_path_boundary_and_archive_paging(self):
        blocked = self.client.get("/api/file", query_string={"scope": "config", "path": "../outside.json"})
        self.assertEqual(blocked.status_code, 400)
        path = "group_1/2026年9月24日/对话文件.json"
        page = self.client.get("/api/file", query_string={
            "scope": "archives", "path": path, "offset": 1, "limit": 1
        })
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json["total"], 3)
        self.assertEqual(page.json["records"][0]["text"], "第二条")
        report = self.client.get("/api/file", query_string={
            "scope": "reports", "path": "qq_sentiment/2026-09-24/日报.md"
        })
        self.assertEqual(report.json["meta"]["total_messages"], 3)

    def test_delete_archive_and_report_with_version(self):
        archive_path = "group_1/2026年9月24日/对话文件.json"
        archive = self.client.get("/api/file", query_string={"scope": "archives", "path": archive_path}).json
        self.assertTrue(archive["version"])
        blocked = self.client.delete("/api/file", json={"scope": "config", "path": "config/配置主表.json", "version": "x"})
        self.assertEqual(blocked.status_code, 403)
        stale = self.client.delete("/api/file", json={"scope": "archives", "path": archive_path, "version": "stale"})
        self.assertEqual(stale.status_code, 409)
        removed = self.client.delete("/api/file", json={"scope": "archives", "path": archive_path, "version": archive["version"]})
        self.assertEqual(removed.status_code, 200)
        self.assertFalse((self.root / "conversation_logs" / archive_path).exists())

        report_path = "qq_sentiment/2026-09-24/日报.md"
        report = self.client.get("/api/file", query_string={"scope": "reports", "path": report_path}).json
        removed = self.client.delete("/api/file", json={"scope": "reports", "path": report_path, "version": report["version"]})
        self.assertEqual(removed.status_code, 200)
        self.assertFalse((self.root / "analysis_reports" / report_path).exists())
        self.assertFalse((self.root / "analysis_reports" / "qq_sentiment/2026-09-24/日报.meta.json").exists())

    def test_pending_report_cannot_be_deleted(self):
        report_path = "qq_sentiment/2026-09-24/日报.md"
        state = {"qq_sentiment_daily_pending": {"2026-09-24": {"path": str(self.root / "analysis_reports" / report_path)}}}
        (self.root / "analysis_reports" / ".scheduler_state.json").write_text(json.dumps(state), encoding="utf-8")
        report = self.client.get("/api/file", query_string={"scope": "reports", "path": report_path}).json
        blocked = self.client.delete("/api/file", json={"scope": "reports", "path": report_path, "version": report["version"]})
        self.assertEqual(blocked.status_code, 409)
        self.assertTrue((self.root / "analysis_reports" / report_path).exists())

    def test_bootstrap_does_not_replace_existing_master(self):
        self.assertEqual(self.client.post("/api/bootstrap").status_code, 409)
        (self.root / "config" / "配置主表.json").unlink()
        response = self.client.post("/api/bootstrap")
        self.assertEqual(response.status_code, 200)
        self.assertEqual((self.root / "config" / "配置主表.json").read_text(encoding="utf-8"), "{}")

    def test_apply_uses_current_master(self):
        with patch("config.config_master.apply_master_config", return_value={
            "ok": True, "written": ["bot_config.json"], "errors": []
        }) as apply:
            response = self.client.post("/api/apply")
        self.assertEqual(response.status_code, 200)
        apply.assert_called_once_with(
            master_path=str(self.root / "config" / "配置主表.json"), reload_runtime=True
        )

    def test_markdown_save_then_apply_updates_generated_json(self):
        path = "config/prompts/task.md"
        source = self.client.get("/api/file", query_string={"scope": "config", "path": path}).json
        saved = self.client.put("/api/file", json={
            "scope": "config", "path": path, "etag": source["etag"], "content": "新的分析提示词"
        })
        self.assertEqual(saved.status_code, 200)
        original_apply = config_master.apply_master_config

        def apply_without_runtime_reload(**kwargs):
            self.assertTrue(kwargs["reload_runtime"])
            return original_apply(master_path=kwargs["master_path"], reload_runtime=False)

        with patch.object(config_master, "_PROJECT_ROOT", str(self.root)), patch.object(
            config_master, "_CONFIG_DIR", str(self.root / "config")
        ), patch.object(config_master, "apply_master_config", side_effect=apply_without_runtime_reload):
            applied = self.client.post("/api/apply")

        self.assertEqual(applied.status_code, 200, applied.json)
        generated = json.loads((self.root / "config" / "analysis_config.json").read_text(encoding="utf-8"))
        self.assertEqual(generated["qq_game_sentiment"]["task_system_prompt"], "新的分析提示词")

    def test_push_test_requires_saved_target_and_text(self):
        master_path = self.root / "config" / "配置主表.json"
        master = json.loads(master_path.read_text(encoding="utf-8"))
        master["analysis"]["qq_game_sentiment"] = {
            "feishu_push_chat_ids": ["oc_allowed"]
        }
        master_path.write_text(json.dumps(master), encoding="utf-8")
        with patch.object(config_web, "_send_feishu_test", return_value=True) as send:
            invalid = self.client.post("/api/push-test", json={
                "chat_id": "oc_other", "message": "测试"
            })
            empty = self.client.post("/api/push-test", json={
                "chat_id": "oc_allowed", "message": "  "
            })
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(empty.status_code, 400)
            send.assert_not_called()
            valid = self.client.post("/api/push-test", json={
                "chat_id": "oc_allowed", "message": "测试消息"
            })
            self.assertEqual(valid.status_code, 200)
            send.assert_called_once_with("oc_allowed", "测试消息")

    def test_push_test_reports_send_failure(self):
        master_path = self.root / "config" / "配置主表.json"
        master = json.loads(master_path.read_text(encoding="utf-8"))
        master["analysis"]["qq_game_sentiment"] = {
            "feishu_push_chat_ids": ["oc_allowed"]
        }
        master_path.write_text(json.dumps(master), encoding="utf-8")
        with patch.object(config_web, "_send_feishu_test", return_value=False):
            response = self.client.post("/api/push-test", json={
                "chat_id": "oc_allowed", "message": "测试消息"
            })
        self.assertEqual(response.status_code, 502)

    def test_model_probe_endpoints_do_not_modify_master(self):
        path = self.root / "config" / "配置主表.json"
        original = path.read_bytes()
        providers = self.client.get("/api/model/providers")
        self.assertEqual(providers.status_code, 200)
        self.assertTrue(any(item["id"] == "deepseek" for item in providers.json["providers"]))
        with patch("botskill.model_api.list_models", return_value=([{"id": "demo", "name": "Demo"}], 12)) as listing:
            listed = self.client.post("/api/model/list", json={
                "provider": "custom", "base_url": "https://example.test/v1", "api_key": "test"
            })
        self.assertEqual(listed.json["models"][0]["id"], "demo")
        listing.assert_called_once()
        with patch("botskill.model_api.test_model", return_value=(42, "好")) as testing:
            tested = self.client.post("/api/model/test", json={
                "provider": "custom", "base_url": "https://example.test/v1",
                "api_key": "test", "model": "demo"
            })
        self.assertEqual(tested.json["latency_ms"], 42)
        testing.assert_called_once()
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
