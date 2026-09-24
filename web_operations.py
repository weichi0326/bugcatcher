"""WebUI 中的部署、检测和进程管理；只接受预定义操作。"""

from __future__ import annotations

import json
import hashlib
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


class OperationError(Exception):
    pass


class OperationManager:
    JOBS = {"diagnose", "deploy", "bridge", "napcat-setup", "napcat-download", "napcat-install"}
    MODES = {"bot", "integrated"}

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.lock = threading.RLock()
        self.job: dict | None = None
        self.service: subprocess.Popen | None = None
        self.service_mode = ""
        self.service_log: Path | None = None
        self.service_started_at = ""
        self.service_started_epoch = 0.0
        self.installer_process: subprocess.Popen | None = None
        self.qq_was_running_at_start = False
        self.last_service_exit: int | None = None
        self.service_stopped_by_user = False

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def _venv_python(self) -> Path:
        return self.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def _bot_python(self) -> str:
        path = self._venv_python()
        return str(path) if path.is_file() else sys.executable

    @staticmethod
    def _tail(path: Path | None, limit: int = 160) -> list[str]:
        if not path or not path.is_file():
            return []
        # 只读末尾 64 KiB，长驻服务不会让状态接口读完整日志。
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                handle.seek(max(0, handle.tell() - 65536))
                raw = handle.read()
            return raw.decode("utf-8", errors="replace").splitlines()[-limit:]
        except OSError:
            return []

    def snapshot(self) -> dict:
        from napcat_setup import installation_status

        napcat = installation_status(self.root)
        qq = self.qq_status(napcat)
        try:
            integration = json.loads((self.root / "config" / "napcat_integration.json").read_text(encoding="utf-8-sig"))
            bot_port = int(integration.get("bot_port") or 5000) if isinstance(integration, dict) else 5000
        except (OSError, ValueError, TypeError):
            bot_port = 5000
        with self.lock:
            service = self.service
            running = service is not None and service.poll() is None
            if service is not None and not running:
                self.last_service_exit = service.returncode
            job = self.job
            return {
                "ok": True,
                "service": {
                    "mode": self.service_mode,
                    "pid": service.pid if service else None,
                    "running": running,
                    "ready": running and self._port_open(bot_port),
                    "exit_code": None if running or self.service_stopped_by_user else self.last_service_exit,
                    "stopped_by_user": self.service_stopped_by_user and not running,
                    "started_at": self.service_started_at,
                    "lines": self._tail(self.service_log),
                },
                "job": ({**job, "lines": list(job["lines"])} if job else None),
                "python": self._bot_python(),
                "napcat": napcat,
                "qq": qq,
            }

    def _qq_qrcode_path(self, napcat: dict) -> Path | None:
        shell = napcat.get("shell_dir")
        if not shell:
            return None
        versions = Path(shell) / "versions"
        if not versions.is_dir():
            return None
        candidates = sorted((entry for entry in versions.iterdir() if entry.is_dir() and
                             re.fullmatch(r"\d+\.\d+\.\d+-\d+", entry.name)),
                            key=lambda entry: tuple(int(part) for part in entry.name.replace("-", ".").split(".")),
                            reverse=True)
        if not candidates:
            return None
        return candidates[0] / "resources" / "app" / "napcat" / "cache" / "qrcode.png"

    def _napcat_webui_details(self, napcat: dict) -> dict:
        """只读取本机 NapCat WebUI 的账号与版本；不可用时交给 OneBot 回退。"""
        qr = self._qq_qrcode_path(napcat)
        config = qr.parent.parent / "config" / "webui.json" if qr else None
        if not config or not config.is_file():
            return {}
        try:
            settings = json.loads(config.read_text(encoding="utf-8-sig"))
            if not isinstance(settings, dict) or settings.get("disableWebUI"):
                return {}
            token = str(settings.get("token") or "")
            port = int(settings.get("port") or 6099)
            if not token or not 1 <= port <= 65535:
                return {}
        except (OSError, ValueError, TypeError):
            return {}

        def call(path: str, *, credential: str = "", body: dict | None = None) -> dict:
            headers = {"Content-Type": "application/json"}
            if credential:
                headers["Authorization"] = f"Bearer {credential}"
            data = json.dumps(body).encode("utf-8") if body is not None else None
            request = Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers)
            with urlopen(request, timeout=1.2) as response:
                result = json.load(response)
            return result if isinstance(result, dict) and result.get("code") == 0 else {}

        try:
            digest = hashlib.sha256((token + ".napcat").encode("utf-8")).hexdigest()
            if (getattr(self, "_napcat_credential_port", None) != port
                    or getattr(self, "_napcat_credential_token_hash", None) != digest
                    or time.time() - getattr(self, "_napcat_credential_at", 0) > 3000):
                auth = call("/api/auth/login", body={"hash": digest}).get("data") or {}
                credential = auth.get("Credential") if isinstance(auth, dict) else None
                if not credential:
                    return {}
                self._napcat_credential = str(credential)
                self._napcat_credential_port = port
                self._napcat_credential_token_hash = digest
                self._napcat_credential_at = time.time()
            credential = self._napcat_credential
            login = call("/api/QQLogin/CheckLoginStatus", credential=credential, body={}).get("data") or {}
            info = (call("/api/QQLogin/GetQQLoginInfo", credential=credential, body={}).get("data") or {}
                    if isinstance(login, dict) and login.get("isLogin") else {})
            version = call("/api/base/GetNapCatVersion", credential=credential).get("data") or {}
            if not isinstance(info, dict):
                info = {}
            if not isinstance(version, dict):
                version = {}
            return {"account": info, "napcat_version": str(version.get("version") or "")}
        except (OSError, ValueError, TypeError, URLError):
            return {}

    def qq_status(self, napcat: dict | None = None) -> dict:
        from napcat_setup import installation_status
        from scripts.napcat_detect import napcat_process_running

        napcat = napcat or installation_status(self.root)
        with self.lock:
            externally_started = bool(self.service and self.service.poll() is None and
                                      self.service_mode == "integrated" and self.qq_was_running_at_start)
        if not napcat["installed"]:
            return {"running": False, "logged_in": False, "qrcode_version": None,
                    "account": None, "napcat_version": None,
                    "externally_started": externally_started}
        running = napcat_process_running()
        account = None
        napcat_version = None
        if running:
            webui = self._napcat_webui_details(napcat)
            info = webui.get("account") or {}
            if info.get("uin"):
                account = {"user_id": str(info["uin"]), "nickname": str(info.get("nick") or "QQ 用户"),
                           "avatar_url": str(info.get("avatarUrl") or "")}
            napcat_version = webui.get("napcat_version") or None
        if running and account is None:
            integration = self.root / "config" / "napcat_integration.json"
            try:
                settings = json.loads(integration.read_text(encoding="utf-8-sig"))
                if not isinstance(settings, dict):
                    settings = {}
                port = int(settings.get("napcat_http_port") or 3000)
                token = str(settings.get("napcat_http_token") or "")
                headers = {"Authorization": f"Bearer {token}"} if token else {}
                request = Request(f"http://127.0.0.1:{port}/get_login_info", data=b"{}", headers=headers)
                with urlopen(request, timeout=1.5) as response:
                    payload = json.load(response)
                valid = isinstance(payload, dict) and payload.get("status", "ok") != "failed" and payload.get("retcode", 0) == 0
                data = payload.get("data") if valid else None
                if isinstance(data, dict) and data.get("user_id"):
                    account = {"user_id": str(data["user_id"]), "nickname": str(data.get("nickname") or "QQ 用户")}
            except (OSError, ValueError, TypeError, URLError):
                pass
        qr = self._qq_qrcode_path(napcat)
        qr_version = None
        with self.lock:
            fresh_since = (self.service_started_epoch if self.service and self.service.poll() is None and
                           self.service_mode == "integrated" and not self.qq_was_running_at_start else 0.0)
        if running and account is None and qr and qr.is_file():
            try:
                stat = qr.stat()
                if (0 < stat.st_size < 2 * 1024 * 1024 and time.time() - stat.st_mtime < 180 and
                        stat.st_mtime >= fresh_since):
                    qr_version = stat.st_mtime_ns
            except OSError:
                pass
        return {"running": running, "logged_in": account is not None,
                "qrcode_version": qr_version, "account": account, "napcat_version": napcat_version,
                "externally_started": externally_started}

    def qq_qrcode(self) -> Path | None:
        from napcat_setup import installation_status

        napcat = installation_status(self.root)
        if self.qq_status(napcat)["qrcode_version"] is None:
            return None
        return self._qq_qrcode_path(napcat)

    def _line(self, message: str) -> None:
        with self.lock:
            if self.job is not None:
                self.job["lines"].append(str(message).rstrip())

    def _run_command(self, args: list[str], *, timeout: int = 600) -> int:
        self._line("执行：" + " ".join(args[:3]))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            args, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=flags,
        )
        deadline = time.monotonic() + timeout
        lines: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=read_output, daemon=True).start()
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("操作超时")
                try:
                    line = lines.get(timeout=min(0.5, remaining))
                except queue.Empty:
                    continue
                if line is None:
                    break
                self._line(line)
            return process.wait(timeout=max(1, deadline - time.monotonic()))
        except (TimeoutError, subprocess.TimeoutExpired):
            self._stop_process(process)
            raise OperationError("操作超时，已停止子进程") from None
        finally:
            if process.stdout:
                process.stdout.close()

    @staticmethod
    def _stop_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, timeout=20, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    @staticmethod
    def _port_open(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False

    def _dependency_problems(self, python: Path) -> list[str]:
        """用项目解释器核对安装结果；不执行耗时的第三方模块初始化。"""
        requirements = self.root / "requirements.txt"
        expected: dict[str, str] = {}
        for line in requirements.read_text(encoding="utf-8-sig").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.!+\-]+)", line)
            if not match:
                raise OperationError(f"暂不支持核验依赖清单中的条目：{line}")
            expected[match.group(1)] = match.group(2)
        script = (
            "import importlib.metadata as metadata, importlib.util, json, sys\n"
            "names = json.loads(sys.argv[1])\n"
            "modules = {'flask': 'flask', 'requests': 'requests', "
            "'websocket-client': 'websocket', 'lark-oapi': 'lark_oapi', "
            "'pynacl': 'nacl', 'watchdog': 'watchdog'}\n"
            "result = {}\n"
            "for name in names:\n"
            "    try: version = metadata.version(name)\n"
            "    except metadata.PackageNotFoundError: version = None\n"
            "    module = modules.get(name.lower())\n"
            "    result[name] = {'version': version, 'module': "
            "bool(importlib.util.find_spec(module)) if version and module else None}\n"
            "print(json.dumps(result))\n"
        )
        try:
            check = subprocess.run(
                [str(python), "-c", script, json.dumps(list(expected))],
                cwd=self.root, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise OperationError("依赖检查执行超过 30 秒；请重新检测，持续超时可查看系统负载") from exc
        if check.returncode:
            detail = (check.stderr or "").strip().splitlines()[-1:] or ["子进程异常退出"]
            raise OperationError(f"依赖检查程序执行失败：{detail[0]}")
        try:
            actual = json.loads(check.stdout)
            if not isinstance(actual, dict):
                raise ValueError("结果不是对象")
            problems = []
            for name, version in expected.items():
                entry = actual[name]
                installed = entry["version"]
                if installed is None:
                    problems.append(f"{name} 未安装")
                elif installed != version:
                    problems.append(f"{name} 版本为 {installed}，需要 {version}")
                elif entry["module"] is False:
                    problems.append(f"{name} 模块入口未找到")
            return problems
        except (KeyError, TypeError, ValueError) as exc:
            raise OperationError("依赖检查程序返回了无效结果") from exc

    def _diagnose(self) -> int:
        issues: list[dict[str, str]] = []

        def issue(title: str, detail: str, action: str = "") -> None:
            issues.append({"title": title, "detail": detail, "action": action})
            self._line(f"需处理：{title}。{detail}")

        self._line(f"管理页 Python：{sys.executable}")
        self._line(f"Python 版本：{sys.version_info.major}.{sys.version_info.minor}")
        if sys.version_info < (3, 10):
            issue("Python 版本过低", "需要 3.10 或更高版本")
        bot_python = self._venv_python()
        if bot_python.is_file():
            self._line(f"项目虚拟环境：已找到 {bot_python}")
        else:
            issue("项目虚拟环境未创建", "点击「开始部署」创建并安装依赖", "deploy")
        requirements = self.root / "requirements.txt"
        if requirements.is_file():
            if bot_python.is_file():
                problems = self._dependency_problems(bot_python)
                for problem in problems:
                    issue("Python 依赖需要修复", f"{problem}；点击「开始部署」重新安装", "deploy")
                if not problems:
                    self._line("Python 依赖：清单版本与模块入口均正常")
        else:
            issue("requirements.txt 缺失", "请恢复项目依赖清单")
        master = self.root / "config" / "配置主表.json"
        if not master.is_file():
            issue("配置主表缺失", "请在概览页从示例创建配置")
        else:
            try:
                data = json.loads(master.read_text(encoding="utf-8-sig"))
                self._line("配置主表：JSON 格式正常" if isinstance(data, dict) else "配置主表：根节点不是对象")
                if not isinstance(data, dict):
                    issue("配置主表格式错误", "根节点应是 JSON 对象")
            except (OSError, ValueError):
                issue("配置主表无法读取", "请检查 JSON 格式")
        for name in ("bot_config.json", "analysis_config.json", "feishu_bot.json", "models.json"):
            if not (self.root / "config" / name).is_file():
                issue(f"运行时配置 {name} 未生成", "请点击「应用配置」")
        integration = self.root / "config" / "napcat_integration.json"
        settings = {}
        if integration.is_file():
            try:
                settings = json.loads(integration.read_text(encoding="utf-8-sig"))
                if not isinstance(settings, dict):
                    settings = {}
                    self._line("NapCat 集成配置：根节点不是对象")
            except (OSError, ValueError):
                self._line("NapCat 集成配置：读取或 JSON 格式错误")
        try:
            bot_port = int(settings.get("bot_port") or 5000)
            napcat_port = int(settings.get("napcat_http_port") or 3000)
        except (TypeError, ValueError):
            bot_port, napcat_port = 5000, 3000
            self._line("NapCat 端口配置无效，按默认端口检测")
        for port, label in ((bot_port, "机器人 OneBot"), (napcat_port, "NapCat HTTP")):
            self._line(f"{label} 端口 {port}：" + ("已监听" if self._port_open(port) else "未监听"))
        if self._port_open(bot_port):
            try:
                with urlopen(f"http://127.0.0.1:{bot_port}/health", timeout=3) as response:
                    result = json.load(response)
                healthy = isinstance(result, dict) and result.get("status") == "ok"
                self._line("机器人健康接口：" + ("正常" if healthy else "返回异常"))
            except (OSError, ValueError, URLError):
                self._line("机器人健康接口：不可用或并非此项目服务")
        if self._port_open(napcat_port):
            try:
                token = str(settings.get("napcat_http_token") or "")
                headers = {"Authorization": f"Bearer {token}"} if token else {}
                req = Request(f"http://127.0.0.1:{napcat_port}/get_login_info", data=b"{}", headers=headers)
                with urlopen(req, timeout=3) as response:
                    result = json.load(response)
                account = result.get("data") or {} if isinstance(result, dict) else {}
                if not isinstance(account, dict):
                    account = {}
                self._line("NapCat QQ 登录：" + (str(account.get("user_id")) if account.get("user_id") else "未确认"))
            except (OSError, ValueError, URLError):
                self._line("NapCat QQ 登录：接口不可用")
        if integration.is_file():
            try:
                from napcat_setup import installation_status

                if installation_status(self.root)["installed"]:
                    self._line("NapCat 程序：已找到")
                else:
                    issue("QQNT / NapCat 未安装", "到 QQ 管理点击「下载并安装 QQ」", "napcat-download")
            except Exception as exc:
                issue("NapCat 路径无法检查", str(exc))
        else:
            issue("NapCat 集成配置未找到", "整合启动不可用")
        summary = f"检测完成，发现 {len(issues)} 项环境问题" if issues else "检测完成，基础条件正常"
        with self.lock:
            if self.job is not None:
                self.job["diagnostic"] = {"summary": summary, "issues": issues}
        self._line(summary + "。")
        return 0

    def _deploy(self) -> int:
        python = self._venv_python()
        if not python.is_file():
            self._line("正在创建项目虚拟环境…")
            code = self._run_command([sys.executable, "-m", "venv", str(self.root / ".venv")], timeout=180)
            if code:
                return code
        if not python.is_file():
            raise OperationError("虚拟环境创建后未找到 Python")
        self._line("正在安装 requirements.txt 中的依赖…")
        code = self._run_command([str(python), "-m", "pip", "install", "-r", str(self.root / "requirements.txt")], timeout=1800)
        if code:
            return code
        problems = self._dependency_problems(python)
        for problem in problems:
            self._line(f"依赖安装后校验失败：{problem}")
        if problems:
            return 1
        self._line("依赖安装并校验通过。")
        return 0

    def _run_job(self, name: str) -> int:
        if name == "diagnose":
            return self._diagnose()
        if name == "deploy":
            return self._deploy()
        if name == "napcat-setup":
            from napcat_setup import download_onekey, installation_status

            status = installation_status(self.root)
            if status["installed"]:
                self._line("QQNT / NapCat 已安装，无需重复安装。")
                return 0
            if not status["staged"]:
                download_onekey(self.root, progress=self._line)
            return self._install_napcat()
        if name == "napcat-download":
            from napcat_setup import download_onekey

            result = download_onekey(self.root, progress=self._line)
            self._line("官方安装组件已下载并校验，网页将自动启动安装程序。")
            self._line(f"当前状态：{result['state']}")
            return 0
        if name == "napcat-install":
            return self._install_napcat()
        scripts = {"bridge": "link_napcat_bridge.py"}
        script = self.root / "scripts" / scripts[name]
        if not script.is_file():
            raise OperationError(f"找不到 {script.name}")
        return self._run_command([self._bot_python(), "-u", str(script)], timeout=240)

    def _install_napcat(self) -> int:
        from napcat_setup import installation_status, launch_installer

        with self.lock:
            if self.installer_process is not None and self.installer_process.poll() is None:
                raise OperationError("安装程序仍在运行，请先完成现有安装窗口")
        installer = launch_installer(self.root)
        with self.lock:
            self.installer_process = installer
        self._line("已自动启动官方安装程序；如出现安装窗口，请按提示完成。页面正在等待安装结束…")
        try:
            code = installer.wait(timeout=1800)
        except subprocess.TimeoutExpired:
            self._line("安装程序仍在运行；请在现有安装窗口完成操作，勿重复启动安装器。")
            return 1
        installed = installation_status(self.root)["installed"]
        self._line("QQNT / NapCat 已安装，可以整合启动。" if installed else
                   f"安装程序已退出（代码 {code}），尚未识别到完整安装；请检查安装窗口输出。")
        return 0 if installed else 1

    def start_job(self, name: str) -> dict:
        if name not in self.JOBS:
            raise OperationError("未知操作")
        with self.lock:
            if self.job and self.job["status"] == "running":
                raise OperationError("已有任务正在执行，请等待完成")
            self.job = {"name": name, "status": "running", "lines": deque(maxlen=250),
                        "exit_code": None, "started_at": self._now(), "finished_at": ""}

        def worker() -> None:
            try:
                code = self._run_job(name)
            except Exception as exc:
                self._line(f"操作失败：{exc}")
                with self.lock:
                    if self.job is not None:
                        self.job["error"] = str(exc)
                code = 1
            with self.lock:
                assert self.job is not None
                self.job["exit_code"] = code
                self.job["status"] = "success" if code == 0 else "failed"
                self.job["finished_at"] = self._now()

        threading.Thread(target=worker, daemon=True, name=f"webui-{name}").start()
        return self.snapshot()

    def start_service(self, mode: str) -> dict:
        if mode not in self.MODES:
            raise OperationError("未知启动模式")
        with self.lock:
            if self.job and self.job["status"] == "running":
                raise OperationError("后台任务正在执行，请等待完成")
            if not (self.root / "config" / "配置主表.json").is_file():
                raise OperationError("请先创建配置主表并应用")
            python = self._bot_python()
            script = self.root / ("bot.py" if mode == "bot" else "scripts/integrated_launcher.py")
            if not script.is_file():
                raise OperationError("启动文件不存在")
            if self.service and self.service.poll() is None:
                old_process = self.service
                self._stop_process(old_process)
                try:
                    old_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    raise OperationError("旧服务未能关闭，新服务未启动") from None
            from scripts.project_processes import replace_existing

            try:
                replace_existing(self.root, ("bot.py", "scripts/integrated_launcher.py"))
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                raise OperationError(f"旧服务未能关闭，新服务未启动：{exc}") from None
            log = self.root / "log" / "webui_service.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            # 每次启动覆盖旧日志，保证状态页展示本次启动的结果。
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            env["QQ_WEBUI_MANAGED"] = "1"
            env.pop("QQ_WEBUI_QUICK_LOGIN_UIN", None)
            args = [python, "-u", str(script)]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            from scripts.napcat_detect import napcat_process_running

            qq_was_running = napcat_process_running() if mode == "integrated" else False
            started_epoch = time.time()
            try:
                with log.open("wb") as output:
                    output.write((f"WebUI 启动 {mode}：{self._now()}\n").encode("utf-8"))
                    output.flush()
                    process = subprocess.Popen(args, cwd=self.root, env=env, stdout=output, stderr=subprocess.STDOUT,
                                               creationflags=flags)
            except OSError as exc:
                raise OperationError(f"启动失败：{exc}") from None
            self.service = process
            self.service_mode = mode
            self.service_log = log
            self.service_started_at = self._now()
            self.service_started_epoch = started_epoch
            self.qq_was_running_at_start = qq_was_running
            self.last_service_exit = None
            self.service_stopped_by_user = False
            return self.snapshot()

    def stop_service(self) -> dict:
        with self.lock:
            if not self.service or self.service.poll() is not None:
                raise OperationError("当前没有由管理页运行的机器人")
            process = self.service
            self._stop_process(process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                raise OperationError("停止失败，进程仍在运行") from None
            self.last_service_exit = process.poll()
            self.service_stopped_by_user = True
            return self.snapshot()
