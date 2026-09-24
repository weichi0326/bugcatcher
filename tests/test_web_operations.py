"""运行管理接口只接受预定义操作，并管理自己启动的进程。"""

import tempfile
import threading
import time
import unittest
import subprocess
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config_web
from web_operations import OperationError, OperationManager


class FakeProcess:
    pid = 24680
    returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class WebOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "配置主表.json").write_text("{}", encoding="utf-8")
        (self.root / "bot.py").write_text("pass", encoding="utf-8")
        self.manager = OperationManager(self.root)

    def test_allowlist_and_job_state(self):
        with self.assertRaises(OperationError):
            self.manager.start_job("not-allowed")
        gate = threading.Event()

        def work(name):
            gate.wait(timeout=2)
            return 0

        with patch.object(self.manager, "_run_job", side_effect=work):
            started = self.manager.start_job("diagnose")
            self.assertEqual(started["job"]["status"], "running")
            with self.assertRaises(OperationError):
                self.manager.start_job("deploy")
            gate.set()
            for _ in range(100):
                if self.manager.snapshot()["job"]["status"] == "success":
                    break
                time.sleep(0.01)
            self.assertEqual(self.manager.snapshot()["job"]["status"], "success")

    def test_qq_setup_downloads_then_launches_and_verifies_installation(self):
        status = {"installed": False, "staged": False}
        installer = FakeProcess()

        def download(*args, **kwargs):
            status["staged"] = True
            return {"state": "staged"}

        def wait(timeout=None):
            self.assertTrue(status["staged"])
            self.assertEqual(timeout, 1800)
            status["installed"] = True
            return 0

        installer.wait = wait
        with patch("napcat_setup.installation_status", side_effect=lambda _: dict(status)), patch(
            "napcat_setup.download_onekey", side_effect=download
        ) as downloaded, patch("napcat_setup.launch_installer", return_value=installer) as launched:
            self.assertEqual(self.manager._run_job("napcat-setup"), 0)
        downloaded.assert_called_once()
        launched.assert_called_once_with(self.root)

    def test_diagnosis_completes_and_reports_environment_issues(self):
        self.manager.start_job("diagnose")
        for _ in range(500):
            job = self.manager.snapshot()["job"]
            if job["status"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "success")
        self.assertGreater(len(job["diagnostic"]["issues"]), 0)
        self.assertIn("检测完成", job["diagnostic"]["summary"])

    def test_dependency_check_reports_missing_and_wrong_versions_without_importing(self):
        (self.root / "requirements.txt").write_text("Flask==3.0.0\nwebsocket-client==1.8.0\n", encoding="utf-8")
        result = SimpleNamespace(returncode=0, stdout='{"Flask":{"version":"3.0.0","module":true},"websocket-client":{"version":null,"module":null}}', stderr="")
        with patch("web_operations.subprocess.run", return_value=result) as run:
            self.assertEqual(self.manager._dependency_problems(Path("python")), ["websocket-client 未安装"])
        self.assertIn("importlib.metadata", run.call_args.args[0][2])
        self.assertNotIn("import lark_oapi", run.call_args.args[0][2])

    def test_dependency_check_timeout_is_not_reported_as_missing_dependency(self):
        (self.root / "requirements.txt").write_text("Flask==3.0.0\n", encoding="utf-8")
        with patch("web_operations.subprocess.run", side_effect=subprocess.TimeoutExpired("python", 30)):
            with self.assertRaisesRegex(OperationError, "依赖检查执行超过 30 秒"):
                self.manager._dependency_problems(Path("python"))

    def test_deploy_verifies_installed_dependencies(self):
        (self.root / "requirements.txt").write_text("Flask==3.0.0\n", encoding="utf-8")
        python = self.manager._venv_python()
        python.parent.mkdir(parents=True)
        python.touch()
        with patch.object(self.manager, "_run_command", return_value=0), patch.object(
            self.manager, "_dependency_problems", return_value=["Flask 未安装"]
        ):
            self.assertEqual(self.manager._deploy(), 1)

    def test_qq_qrcode_is_served_only_during_active_login(self):
        shell = self.root / "napcat" / "NapCat.Shell.Windows.OneKey" / "NapCat.55555.Shell"
        cache = shell / "versions" / "9.9.26-55555" / "resources" / "app" / "napcat" / "cache"
        cache.mkdir(parents=True)
        (shell / "QQ.exe").touch()
        (shell / "NapCatWinBootMain.exe").touch()
        (cache / "qrcode.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        with patch("scripts.napcat_detect.napcat_process_running", return_value=False):
            self.assertIsNone(self.manager.qq_qrcode())
        with patch("scripts.napcat_detect.napcat_process_running", return_value=True):
            path = self.manager.qq_qrcode()
            self.assertEqual(path, cache / "qrcode.png")
            with patch.object(config_web, "_operations", return_value=self.manager):
                response = config_web.app.test_client().get("/api/qq/qrcode")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "image/png")
            response.close()
        self.manager.service = FakeProcess()
        self.manager.service_mode = "integrated"
        self.manager.service_started_epoch = time.time() + 10
        with patch("scripts.napcat_detect.napcat_process_running", return_value=True):
            self.assertIsNone(self.manager.qq_qrcode())

    def test_qq_account_and_version_from_napcat_webui(self):
        napcat = {"installed": True, "shell_dir": None}
        details = {"account": {"uin": "123456", "nick": "测试账号", "avatarUrl": "https://example.com/avatar.png"},
                   "napcat_version": "4.18.2"}
        with patch("scripts.napcat_detect.napcat_process_running", return_value=True), patch.object(
            self.manager, "_napcat_webui_details", return_value=details
        ):
            status = self.manager.qq_status(napcat)
        self.assertTrue(status["logged_in"])
        self.assertEqual(status["account"]["nickname"], "测试账号")
        self.assertEqual(status["account"]["avatar_url"], "https://example.com/avatar.png")
        self.assertEqual(status["napcat_version"], "4.18.2")

    def test_webui_token_change_refreshes_cached_credential(self):
        cache = self.root / "shell" / "versions" / "9.9.26-55555" / "resources" / "app" / "napcat" / "cache"
        cache.mkdir(parents=True)
        webui = cache.parent / "config" / "webui.json"
        webui.parent.mkdir()
        webui.write_text(json.dumps({"port": 6099, "token": "first"}), encoding="utf-8")
        login_hashes = []

        def reply(request, timeout):
            if request.full_url.endswith("/api/auth/login"):
                login_hashes.append(json.loads(request.data)["hash"])
                data = {"Credential": "credential"}
            elif request.full_url.endswith("/api/QQLogin/CheckLoginStatus"):
                data = {"isLogin": False}
            else:
                data = {"version": "4.18.2"}
            return io.BytesIO(json.dumps({"code": 0, "data": data}).encode())

        with patch.object(self.manager, "_qq_qrcode_path", return_value=cache / "qrcode.png"), patch(
            "web_operations.urlopen", side_effect=reply
        ):
            self.manager._napcat_webui_details({})
            webui.write_text(json.dumps({"port": 6099, "token": "second"}), encoding="utf-8")
            self.manager._napcat_webui_details({})
        self.assertEqual(len(login_hashes), 2)
        self.assertNotEqual(login_hashes[0], login_hashes[1])

    def test_installer_timeout_does_not_launch_duplicate_installer(self):
        installer = FakeProcess()
        installer.wait = lambda timeout: (_ for _ in ()).throw(subprocess.TimeoutExpired("installer", timeout))
        with patch("napcat_setup.launch_installer", return_value=installer) as launch:
            self.assertEqual(self.manager._install_napcat(), 1)
            with self.assertRaises(OperationError):
                self.manager._install_napcat()
        launch.assert_called_once()

    def test_webui_managed_napcat_starts_without_console(self):
        import scripts.integrated_launcher as launcher

        shell = self.root / "shell"
        shell.mkdir()
        exe = shell / "NapCatWinBootMain.exe"
        exe.touch()
        with patch.object(launcher, "ROOT", str(self.root)), patch.object(
            launcher, "resolve_napcat_launcher", return_value=(str(shell), str(exe))
        ), patch.dict("os.environ", {"QQ_WEBUI_MANAGED": "1", "QQ_WEBUI_QUICK_LOGIN_UIN": "789012"}), patch.object(
            launcher, "disable_saved_auto_login"
        ) as disable, patch.object(
            launcher.subprocess, "Popen", return_value=FakeProcess()
        ) as spawn:
            launcher.start_napcat({})
        disable.assert_called_once_with({})
        self.assertEqual(spawn.call_args.args[0], [str(exe)])
        self.assertTrue(spawn.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)

    def test_hot_reload_uses_napcat_config_api_without_restarting(self):
        import scripts.integrated_launcher as launcher

        config_dir = self.root / "config"
        (config_dir / "webui.json").write_text(json.dumps({"port": 6099, "token": "test-token"}), encoding="utf-8")
        onebot = config_dir / "onebot11_789012.json"
        onebot.write_text('{"network":{"httpServers":[],"httpClients":[]}}', encoding="utf-8")
        replies = [io.BytesIO(b'{"code":0,"data":{"Credential":"test-credential"}}'),
                   io.BytesIO(b'{"code":0,"data":null}')]
        launcher._WEBUI_AUTH["at"] = 0.0
        with patch.object(launcher, "urlopen", side_effect=replies) as request:
            launcher.hot_reload_network_config(str(onebot))
        self.assertEqual(request.call_count, 2)
        self.assertTrue(request.call_args.args[0].full_url.endswith("/api/OB11Config/SetConfig"))

    def test_saved_auto_login_is_cleared_before_qr_login(self):
        import scripts.integrated_launcher as launcher

        config_dir = self.root / "napcat-config"
        config_dir.mkdir()
        webui = config_dir / "webui.json"
        webui.write_text('{"autoLoginAccount":"789012","port":6099}', encoding="utf-8")
        with patch("botskill.napcat_paths.napcat_root_from_integration", return_value=str(self.root)), patch(
            "botskill.napcat_paths.napcat_config_dir", return_value=str(config_dir)
        ):
            launcher.disable_saved_auto_login({})
        self.assertEqual(json.loads(webui.read_text(encoding="utf-8")), {"autoLoginAccount": "", "port": 6099})

    def test_qrcode_refreshes_when_login_reports_error(self):
        import scripts.integrated_launcher as launcher

        calls = []

        def webui_call(_cfg, path, _body):
            calls.append(path)
            return {"data": {"isLogin": False, "loginError": "该账号已登录"}} if path.endswith("CheckLoginStatus") else {"code": 0}

        with patch.object(launcher, "_napcat_webui_call", side_effect=webui_call), patch.object(
            launcher.os.path, "isfile", return_value=False
        ), patch.object(launcher, "_log"):
            launcher.ensure_login_qrcode(str(self.root / "config"), time.time(), timeout_sec=1)
        self.assertEqual(calls, ["/api/QQLogin/CheckLoginStatus", "/api/QQLogin/RefreshQRcode"])

    def test_existing_fresh_qrcode_is_not_refreshed(self):
        import scripts.integrated_launcher as launcher

        with patch.object(launcher, "_napcat_webui_call", return_value={"data": {"isLogin": False}}) as call, patch.object(
            launcher.os.path, "isfile", return_value=True
        ), patch.object(launcher.os.path, "getmtime", return_value=time.time()):
            launcher.ensure_login_qrcode(str(self.root / "config"), time.time(), timeout_sec=1)
        call.assert_called_once_with(str(self.root / "config"), "/api/QQLogin/CheckLoginStatus", {})

    def test_switch_detection_uses_confirmed_webui_login(self):
        import scripts.integrated_launcher as launcher
        from scripts.napcat_detect import parse_login_from_ob11_response

        with patch.object(launcher, "_napcat_webui_call", side_effect=[
            {"data": {"isLogin": True}}, {"data": {"uin": "789012"}}
        ]):
            self.assertEqual(launcher.current_webui_qq("config"), 789012)
        self.assertIsNone(parse_login_from_ob11_response({
            "status": "failed", "retcode": 100, "data": {"user_id": 123456}
        }))

    def test_bridge_waits_for_http_without_restarting_napcat(self):
        import scripts.integrated_launcher as launcher

        process = FakeProcess()
        with patch.object(launcher, "_api_base", return_value="http://127.0.0.1:3000"), patch.object(
            launcher, "_api_headers", return_value={}
        ), patch("scripts.napcat_detect.wait_http_server_ready", side_effect=[False, True]), patch(
            "scripts.napcat_detect.napcat_chain_ready", return_value=(True, 789012)
        ), patch.object(launcher, "start_napcat") as start:
            self.assertIs(launcher.ensure_napcat_http_bridge({}, process, 789012), process)
        start.assert_not_called()

    def test_service_start_stop_only_uses_managed_handle(self):
        fake = FakeProcess()
        replacement = FakeProcess()
        with patch("web_operations.subprocess.Popen", side_effect=[fake, replacement]) as launch, patch(
            "scripts.project_processes.replace_existing", return_value=[]
        ):
            started = self.manager.start_service("bot")
            self.assertTrue(started["service"]["running"])
            self.assertEqual(started["service"]["pid"], fake.pid)
            self.assertEqual(launch.call_args.kwargs["env"]["QQ_WEBUI_MANAGED"], "1")

            def replace_old(process):
                self.assertIs(process, fake)
                fake.returncode = 0

            with patch.object(self.manager, "_stop_process", side_effect=replace_old) as stopping:
                restarted = self.manager.start_service("bot")
            stopping.assert_called_once_with(fake)
            self.assertEqual(restarted["service"]["pid"], replacement.pid)
            self.assertTrue(restarted["service"]["running"])

        def stop(process):
            self.assertIs(process, replacement)
            replacement.returncode = 0

        with patch.object(self.manager, "_stop_process", side_effect=stop) as stopping:
            stopped = self.manager.stop_service()
        stopping.assert_called_once_with(replacement)
        self.assertFalse(stopped["service"]["running"])
        self.assertTrue(stopped["service"]["stopped_by_user"])
        self.assertIsNone(stopped["service"]["exit_code"])
        with self.assertRaises(OperationError):
            self.manager.stop_service()

    def test_integrated_service_starts_while_qq_is_not_installed(self):
        script = self.root / "scripts" / "integrated_launcher.py"
        script.parent.mkdir()
        script.write_text("pass", encoding="utf-8")
        with patch("web_operations.subprocess.Popen", return_value=FakeProcess()) as launch, patch(
            "scripts.project_processes.replace_existing", return_value=[]
        ):
            started = self.manager.start_service("integrated")
        self.assertTrue(started["service"]["running"])
        self.assertEqual(started["service"]["mode"], "integrated")
        self.assertFalse(started["napcat"]["installed"])
        self.assertEqual(launch.call_args.args[0][-1], str(script))

    def test_launcher_starts_bot_before_qq_setup(self):
        import scripts.integrated_launcher as launcher

        events = []
        process = FakeProcess()
        process.returncode = 0

        def spawn(*args, **kwargs):
            events.append("bot")
            return process

        def qq_setup(*args):
            events.append("qq")
            raise RuntimeError("QQ 暂不可用")

        with patch.object(launcher, "load_integration", return_value={}), patch.object(
            launcher, "_bridge_settings", return_value=SimpleNamespace(bot_port=5000)
        ), patch("scripts.project_processes.replace_existing", return_value=[]), patch(
            "napcat_setup.installation_status", return_value={"installed": True}
        ), patch("botskill.napcat_paths.napcat_root_from_integration", side_effect=qq_setup), patch.object(
            launcher.subprocess, "Popen", side_effect=spawn
        ), patch.object(launcher.threading, "Thread") as thread, patch.object(launcher, "_log"):
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs["target"]()
            self.assertEqual(launcher.main(), 0)
        self.assertEqual(events, ["bot", "qq"])

    def test_api_rejects_unknown_or_missing_action(self):
        with patch.object(config_web, "PROJECT_ROOT", self.root):
            client = config_web.app.test_client()
            self.assertEqual(client.get("/api/operations/status").status_code, 200)
            self.assertEqual(client.post("/api/operations/job", json={}).status_code, 400)
            self.assertEqual(client.post("/api/operations/job", json={"name": "shell"}).status_code, 409)
            self.assertEqual(client.post("/api/operations/start", json={"mode": "shell"}).status_code, 409)
            self.assertEqual(client.post("/api/operations/stop").status_code, 409)


if __name__ == "__main__":
    unittest.main()
