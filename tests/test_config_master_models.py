"""Model provider profiles remain in the master, outside runtime models.json."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import config_master


class ModelProfilesGenerationTests(unittest.TestCase):
    def test_master_ports_override_stale_bot_ports(self):
        generated = config_master._build_bot_config({
            "ports": {"bot": 5100, "napcat_http": 3100},
            "bot": {"port": 5000, "napcat_http_port": 3000,
                    "onebot_http_api": "http://127.0.0.1:3000"},
        })
        self.assertEqual(generated["port"], 5100)
        self.assertEqual(generated["napcat_http_port"], 3100)
        self.assertEqual(generated["onebot_http_api"], "http://127.0.0.1:3100")

    def test_legacy_admin_list_is_not_generated(self):
        generated = config_master._build_bot_config({"bot": {"admins": ["ou_legacy_user"], "event_mode": "hybrid"}})
        self.assertNotIn("admins", generated)
        self.assertEqual(generated["event_mode"], "hybrid")

    def test_generated_models_include_only_active_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            master = {
                "models": {
                    "text": {"provider": "openai", "api_key": "active-key", "model": "chosen"},
                    "image": {"model": "image-model"},
                    "provider_profiles": {
                        "openai": {"api_key": "active-key", "model": "chosen"},
                        "deepseek": {"api_key": "inactive-key", "model": "another"},
                    },
                }
            }
            master_path = config_dir / "配置主表.json"
            master_path.write_text(json.dumps(master), encoding="utf-8")

            with patch.object(config_master, "_PROJECT_ROOT", str(root)), patch.object(
                config_master, "_CONFIG_DIR", str(config_dir)
            ):
                result = config_master.apply_master_config(master_path=str(master_path))

            self.assertTrue(result["ok"], result["errors"])
            generated = json.loads((config_dir / "models.json").read_text(encoding="utf-8"))
            self.assertEqual(generated["text"], master["models"]["text"])
            self.assertEqual(generated["image"], master["models"]["image"])
            self.assertNotIn("provider_profiles", generated)
            self.assertIn("provider_profiles", json.loads(master_path.read_text(encoding="utf-8"))["models"])


if __name__ == "__main__":
    unittest.main()
