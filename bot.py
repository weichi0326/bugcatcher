# -*- coding: utf-8 -*-
"""QQ 机器人入口脚本。

业务实现已拆分到 botskill/ 包（QQ API、会话、人格、模型调用、HTTP/长连接等）。
本文件仅负责配置日志并启动 bootstrap()；日常部署仍执行：python bot.py
"""

import logging
import os
import sys
import threading

import service_logger
from botskill.bootstrap import bootstrap

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _install_excepthooks() -> None:
    """未捕获异常写入 log/（与 stderr 日志互补）。"""

    orig = sys.excepthook

    def _sys_hook(exc_type, exc, tb):
        if exc is not None and isinstance(exc, BaseException):
            try:
                service_logger.log_error(exc)
            except Exception:
                pass
        orig(exc_type, exc, tb)

    sys.excepthook = _sys_hook

    if hasattr(threading, "excepthook"):
        th_orig = threading.excepthook

        def _th_hook(args):
            try:
                if args.exc_value is not None:
                    service_logger.log_error(args.exc_value)
            except Exception:
                pass
            th_orig(args)

        threading.excepthook = _th_hook


if __name__ == "__main__":
    from pathlib import Path
    from botskill.reload_watch import INTEGRATED_ENV
    from scripts.project_processes import replace_existing

    if (os.environ.get(INTEGRATED_ENV) != "1"
            and os.environ.get("QQ_WEBUI_MANAGED") != "1"
            and os.environ.get(service_logger.FORK_RESTART_ENV) != "1"):
        try:
            stopped = replace_existing(Path(__file__).resolve().parent,
                                       ("bot.py", "scripts/integrated_launcher.py"))
        except (OSError, RuntimeError) as exc:
            raise SystemExit(f"旧项目进程未能关闭，新机器人未启动：{exc}") from None
        if stopped:
            print(f"已关闭旧项目进程：{', '.join(map(str, stopped))}", flush=True)
    _install_excepthooks()
    # 关键修改：由 reload_watch 子进程拉起时父进程已记录重启原因，此处不再重复「进程手动开启」。
    if os.environ.get(service_logger.FORK_RESTART_ENV) != "1":
        service_logger.log_manual_start()
    try:
        bootstrap()
    except KeyboardInterrupt:
        service_logger.log_manual_shutdown()
        raise
    except SystemExit as exc:
        code = getattr(exc, "code", None)
        from botskill.reload_watch import AI_RESTART_EXIT_CODE

        if code == AI_RESTART_EXIT_CODE:
            raise
        if code not in (0, None):
            try:
                service_logger.log_error(exc)
            except Exception:
                pass
        raise
