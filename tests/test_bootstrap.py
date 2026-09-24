"""Startup rejects incompatible routing before creating background services."""

import unittest
from unittest.mock import patch

import botskill.bootstrap as bootstrap_module


class BootstrapTests(unittest.TestCase):
    def test_invalid_hybrid_config_does_not_start_background_services(self):
        with patch.object(bootstrap_module, "reload_transport"), patch.object(
            bootstrap_module, "is_hybrid_mode", return_value=True
        ), patch.object(bootstrap_module, "napcat_archive_only", return_value=False), patch.object(
            bootstrap_module, "_start_onebot_archive_server_thread"
        ) as archive, patch.object(bootstrap_module, "start_analysis_scheduler") as scheduler:
            with self.assertRaises(SystemExit) as result:
                bootstrap_module.bootstrap()
        self.assertEqual(result.exception.code, 1)
        archive.assert_not_called()
        scheduler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
