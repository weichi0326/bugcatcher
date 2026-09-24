# -*- coding: utf-8 -*-
"""
统一文件写入：按绝对路径串行化、锁获取超时、文本原子写（临时文件 + os.replace）。

说明：
- 仅用于本进程内多线程协调；多进程部署需额外文件锁或外部存储。
- 与业务 botskill 解耦，避免 config ↔ botskill 循环导入。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from typing import Any, Dict, Optional

# 模块级常量（非业务魔法字符串）
_DEFAULT_ENCODING = "utf-8"
_TMP_SUFFIX = ".tmp"
_REGISTRY_LOCK = threading.Lock()
_PATH_LOCKS: Dict[str, threading.Lock] = {}


def _norm_key(abs_path: str) -> str:
    return os.path.normcase(os.path.abspath(abs_path))


def _lock_for_path(abs_path: str) -> threading.Lock:
    key = _norm_key(abs_path)
    with _REGISTRY_LOCK:
        if key not in _PATH_LOCKS:
            _PATH_LOCKS[key] = threading.Lock()
        return _PATH_LOCKS[key]


def _optional_backup(abs_path: str) -> None:
    """若目标已存在则复制为 .bak（覆盖旧备份），失败仅记日志。"""
    if not os.path.isfile(abs_path):
        return
    bak = abs_path + ".bak"
    try:
        shutil.copy2(abs_path, bak)
    except OSError as exc:
        logging.warning("config_backup_skip path=%s err=%s", abs_path, exc)


class FileWriteManager:
    """进程内安全写文件入口（静态方法集，无全局可变单例状态）。"""

    @staticmethod
    def write_text_atomically(
        abs_path: str,
        text: str,
        *,
        encoding: str = _DEFAULT_ENCODING,
        acquire_timeout_sec: float = 30.0,
        backup_before_replace: bool = False,
    ) -> None:
        """整文件覆盖写入：tmp 同目录 + fsync + replace；锁超时抛 TimeoutError。"""
        parent = os.path.dirname(os.path.abspath(abs_path))
        os.makedirs(parent, exist_ok=True)
        lock = _lock_for_path(abs_path)
        if not lock.acquire(timeout=max(0.0, float(acquire_timeout_sec))):
            raise TimeoutError(f"获取写锁超时：{abs_path}")
        tmp_path = ""
        try:
            if backup_before_replace:
                _optional_backup(abs_path)
            tmp_path = abs_path + _TMP_SUFFIX + "." + uuid.uuid4().hex[:12]
            with open(tmp_path, "w", encoding=encoding, newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, abs_path)
            tmp_path = ""
        finally:
            lock.release()
            if tmp_path and os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    @staticmethod
    def write_json_atomically(
        abs_path: str,
        obj: Any,
        *,
        indent: int = 2,
        ensure_ascii: bool = False,
        acquire_timeout_sec: float = 30.0,
        backup_before_replace: bool = False,
    ) -> None:
        """将对象 JSON 序列化后原子写入。"""
        text = json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent)
        FileWriteManager.write_text_atomically(
            abs_path,
            text,
            acquire_timeout_sec=acquire_timeout_sec,
            backup_before_replace=backup_before_replace,
        )

    @staticmethod
    def append_utf8_line(
        abs_path: str,
        line: str,
        *,
        acquire_timeout_sec: float = 15.0,
        ensure_parent_dir: bool = True,
    ) -> None:
        """在文件末尾追加一行 UTF-8 文本（带锁与超时；非原子多进程安全）。"""
        parent = os.path.dirname(os.path.abspath(abs_path))
        if ensure_parent_dir:
            os.makedirs(parent, exist_ok=True)
        lock = _lock_for_path(abs_path)
        if not lock.acquire(timeout=max(0.0, float(acquire_timeout_sec))):
            raise TimeoutError(f"获取写锁超时：{abs_path}")
        try:
            with open(abs_path, "a", encoding=_DEFAULT_ENCODING, newline="\n") as handle:
                handle.write(line)
                if not line.endswith("\n"):
                    handle.write("\n")
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
        finally:
            lock.release()
