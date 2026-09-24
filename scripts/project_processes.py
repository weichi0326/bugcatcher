"""Replace older Python processes belonging to this project on Windows."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path


_QUERY = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
    "| Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
)


def _script_argument(command_line: str, path: Path) -> bool:
    """Match an absolute script argument, not a similar name in another project."""
    escaped = re.escape(str(path.resolve()))
    return bool(re.search(rf'(?:^|\s)(?:"{escaped}"|{escaped})(?=\s|$)',
                          command_line, flags=re.IGNORECASE))


def matching_pids(root: Path, scripts: tuple[str, ...]) -> list[int]:
    if os.name != "nt":
        return []
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _QUERY],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        creationflags=flags, check=False,
    )
    if result.returncode:
        raise RuntimeError("无法检查现有项目进程：" + (result.stderr.strip() or str(result.returncode)))
    try:
        processes = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError("无法解析现有项目进程列表") from exc
    if isinstance(processes, dict):
        processes = [processes]
    parents = {int(process.get("ProcessId") or 0): int(process.get("ParentProcessId") or 0)
               for process in processes if isinstance(process, dict)}
    # Windows 虚拟环境的 python.exe 可能再拉起系统解释器。当前进程的
    # 启动器也带同一个脚本参数，结束它的进程树会把新服务一起杀掉。
    protected = set()
    ancestor = os.getpid()
    while ancestor > 0 and ancestor not in protected:
        protected.add(ancestor)
        ancestor = parents.get(ancestor, 0)
    paths = tuple(root.resolve() / script for script in scripts)
    found = set()
    for process in processes:
        if not isinstance(process, dict):
            continue
        pid = int(process.get("ProcessId") or 0)
        command_line = str(process.get("CommandLine") or "")
        if pid > 0 and pid not in protected and any(_script_argument(command_line, path) for path in paths):
            found.add(pid)
    return sorted(found)


def replace_existing(root: Path, scripts: tuple[str, ...]) -> list[int]:
    """Stop only matching project process trees; refuse to launch duplicates on failure."""
    pids = matching_pids(root, scripts)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for pid in pids:
        result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                capture_output=True, text=True, timeout=20,
                                creationflags=flags, check=False)
        if result.returncode:
            # Python venv 的转发进程和实际解释器可能同时匹配；父进程 /T 会一并结束子进程。
            if pid in matching_pids(root, scripts):
                raise RuntimeError(f"无法关闭旧项目进程 {pid}：{result.stderr.strip() or result.stdout.strip()}")
    if pids:
        time.sleep(0.5)
    return pids
