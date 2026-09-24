"""OneKey 下载校验与动态安装路径。"""

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from botskill.napcat_paths import find_shell_dir
from napcat_setup import NapCatSetupError, _validate_zip, download_onekey, installation_status


class NapCatSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_official_download_is_verified_and_staged(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("NapCatInstaller.exe", b"demo installer")
        raw_zip = payload.getvalue()
        release = {
            "tag_name": "v4.18.28", "draft": False, "prerelease": False,
            "assets": [{"name": "NapCat.Shell.Windows.OneKey.zip", "size": len(raw_zip),
                        "digest": "sha256:" + hashlib.sha256(raw_zip).hexdigest(),
                        "browser_download_url": "https://github.com/NapNeko/NapCatQQ/releases/download/v4.18.28/NapCat.Shell.Windows.OneKey.zip"}],
        }
        with patch("napcat_setup.urlopen", side_effect=[io.BytesIO(json.dumps(release).encode()), io.BytesIO(raw_zip)]):
            result = download_onekey(self.root)
        self.assertEqual(result["state"], "staged")
        self.assertTrue(installation_status(self.root)["staged"])
        self.assertFalse(installation_status(self.root)["installed"])
        self.assertEqual(installation_status(self.root)["release_version"], "v4.18.28")

    def test_unsafe_zip_and_existing_install_are_preserved(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("../outside.txt", "bad")
            archive.writestr("NapCatInstaller.exe", "demo")
        payload.seek(0)
        with zipfile.ZipFile(payload) as archive, self.assertRaises(NapCatSetupError):
            _validate_zip(archive)

        shell = self.root / "napcat" / "NapCat.Shell.Windows.OneKey" / "NapCat.55555.Shell"
        shell.mkdir(parents=True)
        (shell / "NapCatWinBootMain.exe").write_bytes(b"")
        (shell / "QQ.exe").write_bytes(b"")
        (shell / "versions" / "9.9.26-55555").mkdir(parents=True)
        with patch("napcat_setup.urlopen") as download:
            self.assertEqual(download_onekey(self.root)["state"], "installed")
            download.assert_not_called()
        self.assertEqual(Path(find_shell_dir(str(shell.parent))), shell)
        self.assertEqual(installation_status(self.root)["qq_version"], "9.9.26.55555")

    def test_configured_install_root_is_used_for_detection(self):
        custom = self.root / "custom" / "OneKey"
        shell = custom / "NapCat.55555.Shell"
        shell.mkdir(parents=True)
        (shell / "NapCatWinBootMain.exe").write_bytes(b"")
        (shell / "QQ.exe").write_bytes(b"")
        config = self.root / "config"
        config.mkdir()
        (config / "napcat_integration.json").write_text(
            json.dumps({"napcat_root": "custom/OneKey"}), encoding="utf-8"
        )
        status = installation_status(self.root)
        self.assertEqual(status["root"], str(custom))
        self.assertTrue(status["installed"])
        self.assertEqual(status["shell_dir"], str(shell))


if __name__ == "__main__":
    unittest.main()
