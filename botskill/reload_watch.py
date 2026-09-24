# -*- coding: utf-8 -*-
"""
开发态整进程重启与文件监听。

职责：
- 在修改源码或 config 目录下 JSON 后自动拉起子进程并退出当前进程，使配置与代码立即生效。
- 依赖 watchdog（可选）；未安装时仅记录警告。

与 AI 无直接关系，但保障你调整人格提示词或模型调用代码后的快速迭代体验。
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time

from config import BOT_CONFIG

from service_logger import FORK_RESTART_ENV

# 与 scripts/integrated_launcher 约定：仅重启 AI 子进程时返回此退出码，不结束 NapCat。
AI_RESTART_EXIT_CODE = 75
INTEGRATED_ENV = "QQ_BOT_INTEGRATED"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 手动 /restart 时记录「重启完成后要提醒哪个飞书群」的临时文件（放 log/ 避免触发文件监听重启）。
_RESTART_NOTIFY_PATH = os.path.join(PROJECT_ROOT, "log", ".restart_notify.json")


def _write_restart_notify(chat_id: str) -> None:
    try:
        os.makedirs(os.path.dirname(_RESTART_NOTIFY_PATH), exist_ok=True)
        with open(_RESTART_NOTIFY_PATH, "w", encoding="utf-8") as f:
            json.dump({"chat_id": (chat_id or "").strip()}, f, ensure_ascii=False)
    except OSError as exc:
        logging.warning("写入重启完成通知目标失败：%s", exc)


def _clear_restart_notify() -> None:
    try:
        if os.path.isfile(_RESTART_NOTIFY_PATH):
            os.remove(_RESTART_NOTIFY_PATH)
    except OSError:
        pass


def _bot_entry_path() -> str:
    return os.path.join(PROJECT_ROOT, "bot.py")


def restart_ai_only(*, reason: str = "file_change", notify_chat_id: str = "") -> None:
    """仅重启机器人进程（bot.py），不触碰 NapCat / 整合启动器。

    - 由 integrated_launcher 拉起时：通知父进程以退出码 75 重新 exec bot.py。
    - 单独 python bot.py 时：子进程拉起 bot.py 后当前进程退出。
    - notify_chat_id 非空时写入临时通知目标，重启完成后对新进程提醒该飞书群。
    """
    try:
        if reason == "manual":
            from service_logger import log_manual_restart

            log_manual_restart()
        else:
            from service_logger import log_auto_reload_restart

            log_auto_reload_restart()
    except Exception as exc:  # pylint: disable=broad-except
        logging.warning("写入 service 日志失败（仍继续重启）：%s", exc)

    if notify_chat_id:
        _write_restart_notify(notify_chat_id)

    if os.environ.get(INTEGRATED_ENV) == "1":
        logging.info("正在重启机器人进程（NapCat 保持运行，由整合启动器拉起新进程）…")
        time.sleep(0.2)
        os._exit(AI_RESTART_EXIT_CODE)

    python_exe = sys.executable
    bot_py = _bot_entry_path()
    argv = [python_exe, bot_py]
    logging.info("正在重启机器人进程（子进程拉起，NapCat 不受影响）：%s", argv)
    env = os.environ.copy()
    env[FORK_RESTART_ENV] = "1"
    try:
        subprocess.Popen(argv, cwd=PROJECT_ROOT, env=env, close_fds=os.name != "nt")
    except OSError as exc:
        logging.exception("子进程拉起失败：%s", exc)
        _clear_restart_notify()
        try:
            from service_logger import log_error

            log_error(exc)
        except Exception:
            pass
        os._exit(1)
    time.sleep(0.2)
    os._exit(0)


def _should_watch_restart_file(abs_path: str) -> bool:
    """判断文件变更是否需要触发整进程重启。"""
    norm = abs_path.replace("\\", "/")
    lower = norm.lower()
    if "__pycache__" in lower:
        return False
    # 关键修改：排除运行期高频写入目录，避免对话归档、业务日志等间接触发热重启。
    skip_markers = (
        "/conversation_logs/",
        "/log/",
        "/.git/",
        "\\conversation_logs\\",
        "\\log\\",
        "\\.git\\",
    )
    if any(m in lower for m in skip_markers):
        return False
    if lower.endswith(".py"):
        return True
    if ("/config/" in lower or "\\config\\" in lower) and lower.endswith(".json"):
        return True
    return False


def start_auto_reload_watcher() -> None:
    """在开发模式下监听源码与人格 JSON，变更后自动重启进程。"""
    if not BOT_CONFIG.get("auto_reload_on_change", True):
        return
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        logging.warning(
            "已开启 auto_reload_on_change 但未安装 watchdog，无法自动重启；请执行 pip install watchdog。"
        )
        return

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bump = [0]

    class _RestartOnChangeHandler(FileSystemEventHandler):
        """文件变更处理器（带去抖，避免编辑器连续触发）。"""

        def _schedule(self, src_path: str) -> None:
            if not _should_watch_restart_file(src_path):
                return
            bump[0] += 1
            token = bump[0]

            def debounced() -> None:
                time.sleep(1.2)
                if bump[0] != token:
                    return
                logging.info("检测到文件变更，即将整进程重启：%s", src_path)
                restart_ai_only(reason="file_change")

            threading.Thread(target=debounced, daemon=True).start()

        def on_modified(self, event):
            if event.is_directory:
                return
            self._schedule(event.src_path)

        def on_created(self, event):
            if event.is_directory:
                return
            self._schedule(event.src_path)

    observer = Observer()
    observer.schedule(_RestartOnChangeHandler(), project_root, recursive=True)
    observer.start()
    logging.info("已启用文件监听：修改项目内 .py 或 config 下 .json 后将自动重启。")
