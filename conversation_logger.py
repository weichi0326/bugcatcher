# -*- coding: utf-8 -*-
"""对话归档：按会话 ID 分目录，按日期子目录写入 JSON。

布局示例::

    conversation_logs/
      oc_xxxxxxxxxxxxxxxxxxxxxxxxxx/   # 飞书会话 ID（示例）
        2025年5月21日/
          对话文件.json
      QQ群号/                          # QQ 群号（示例）
        2025年5月21日/
          对话文件.json

同一会话靠 session.meta.json 中的 session_id / chat_id 绑定，改文件夹名后仍写入原目录。
"""

import json
import os
import re
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

try:
    from config import BOT_CONFIG
except ImportError:
    BOT_CONFIG = {}  # type: ignore

ARCHIVE_LAYOUT_VERSION = "dated_json_v1"
DEFAULT_DIALOG_FILENAME = "对话文件.json"


class ConversationArchive:
    """按会话维度持久化用户消息与机器人回复。"""

    def __init__(
        self,
        base_dir: str,
        max_file_bytes: int,
        fetch_chat_meta: Callable[[str], dict],
    ):
        self.base_dir = base_dir
        self.max_file_bytes = max_file_bytes
        self.fetch_chat_meta = fetch_chat_meta
        self._locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)

    def _parse_chat_id(self, chat_id: str) -> tuple[str, str]:
        cid = (chat_id or "").strip()
        if cid.startswith("oc_"):
            return "feishu", cid
        if cid.startswith("group:"):
            return "group", cid[6:].strip()
        if cid.startswith("private:") or cid.startswith("c2c:"):
            key = "private:" if cid.startswith("private:") else "c2c:"
            return "private", cid[len(key) :].strip()
        return "unknown", cid

    def _archive_channel(self, chat_id: str) -> str:
        """feishu：开放平台群 openid / oc_ 会话；qq：NapCat 数字群号/QQ 号。"""
        kind, nid = self._parse_chat_id(chat_id)
        if not nid:
            return "feishu"
        if nid.startswith("oc_") or kind == "feishu":
            return "feishu"
        if kind == "group" and nid.isdigit():
            return "qq"
        if kind in ("private", "c2c"):
            return "qq" if nid.isdigit() else "feishu"
        if kind == "group":
            return "feishu"
        return "feishu"

    def _session_id(self, chat_id: str) -> str:
        """会话唯一标识（写入 meta，用于查找目录）。"""
        kind, nid = self._parse_chat_id(chat_id)
        if nid.startswith("oc_"):
            return nid
        if kind == "feishu":
            return nid
        if kind == "group" and nid.isdigit():
            return nid
        if kind in ("private", "c2c"):
            return nid
        return nid or "unknown"

    def _dialog_filename(self) -> str:
        raw = ""
        if isinstance(BOT_CONFIG, dict):
            raw = str(BOT_CONFIG.get("archive_dialog_filename") or "").strip()
        return raw or DEFAULT_DIALOG_FILENAME

    def _dialog_stem(self) -> str:
        name = self._dialog_filename()
        if name.lower().endswith(".json"):
            return name[:-5]
        return name

    def _chinese_date_folder(self, when: Optional[datetime] = None) -> str:
        dt = when or datetime.now()
        return f"{dt.year}年{dt.month}月{dt.day}日"

    def _archive_alias_maps(self) -> Dict[str, str]:
        merged: Dict[str, str] = {}
        if not isinstance(BOT_CONFIG, dict):
            return merged
        for cfg_key in ("group_archive_aliases", "feishu_group_archive_aliases"):
            raw = BOT_CONFIG.get(cfg_key)
            if not isinstance(raw, dict):
                continue
            for key, val in raw.items():
                k = str(key).strip()
                v = str(val or "").strip()
                if k and v:
                    merged[k] = v
        return merged

    def _local_folder_alias(self, session_id: str) -> str:
        aliases = self._archive_alias_maps()
        return str(aliases.get(session_id) or aliases.get(str(session_id)) or "").strip()

    def _legacy_chat_id_variants(self, kind: str, session_id: str, chat_id: str) -> List[str]:
        target = str(session_id or "").strip()
        cid = (chat_id or "").strip()
        variants: List[str] = []
        if cid:
            variants.append(cid)
        if target:
            variants.append(target)
            if kind == "group":
                variants.extend([f"group:{target}", f"oc_{target}"])
            if kind in ("private", "c2c"):
                variants.extend([f"c2c:{target}", f"private:{target}"])
        seen = set()
        out: List[str] = []
        for item in variants:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def _safe_dir_name(self, name: str) -> str:
        stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "").strip())
        return stem.strip("._ ")[:120] or "unknown"

    def _default_folder_name(self, chat_id: str) -> str:
        """新建目录：默认用会话 ID（飞书 oc_… / QQ 群号），不用群名拼接。"""
        session_id = self._session_id(chat_id)
        existing = self._find_existing_archive_dir(chat_id, session_id)
        if existing:
            return existing
        alias = self._local_folder_alias(session_id)
        if alias:
            return self._safe_dir_name(alias)
        return self._safe_dir_name(session_id)

    def _find_existing_archive_dir(self, chat_id: str, session_id: str) -> str:
        if not session_id or not os.path.isdir(self.base_dir):
            return ""
        kind, _ = self._parse_chat_id(chat_id)
        target = str(session_id)
        legacy_names = [
            f"group_{target}",
            f"private_{target}",
            f"c2c_{target}",
            target,
        ]
        id_variants = set(self._legacy_chat_id_variants(kind, target, chat_id))

        for name in os.listdir(self.base_dir):
            path = os.path.join(self.base_dir, name)
            if not os.path.isdir(path):
                continue
            if name in legacy_names:
                return name
            meta = self._read_meta(path)
            if str(meta.get("session_id") or "").strip() == target:
                return name
            meta_cid = str(meta.get("chat_id") or "").strip()
            if meta_cid and meta_cid in id_variants:
                return name
            meta_nid = str(meta.get("numeric_id") or "").strip()
            if meta_nid == target:
                return name
            if kind == "group" and name.endswith(f"__{target}"):
                return name
            if kind in ("private", "c2c") and name in (f"私聊__{target}", f"private_{target}"):
                return name
        return ""

    def _folder_name_for_chat(self, chat_id: str) -> str:
        session_id = self._session_id(chat_id)
        existing = self._find_existing_archive_dir(chat_id, session_id)
        if existing:
            return existing
        return self._default_folder_name(chat_id)

    def _meta_path(self, chat_dir: str) -> str:
        return os.path.join(chat_dir, "session.meta.json")

    def _read_meta(self, chat_dir: str) -> dict:
        meta_file = self._meta_path(chat_dir)
        if not os.path.exists(meta_file):
            return {}
        try:
            with open(meta_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_meta(self, chat_dir: str, meta: dict) -> None:
        try:
            with open(self._meta_path(chat_dir), "w", encoding="utf-8") as handle:
                json.dump(meta, handle, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _ensure_chat_dir(self, chat_id: str) -> str:
        folder = self._folder_name_for_chat(chat_id)
        path = os.path.join(self.base_dir, folder)
        os.makedirs(path, exist_ok=True)
        kind, numeric_id = self._parse_chat_id(chat_id)
        session_id = self._session_id(chat_id)
        channel = self._archive_channel(chat_id)
        meta = self._read_meta(path)
        meta["chat_id"] = chat_id
        meta["session_id"] = session_id
        meta["channel"] = channel
        meta["archive_layout"] = ARCHIVE_LAYOUT_VERSION
        meta["chat_kind"] = kind if kind != "unknown" else ("group" if channel == "feishu" else kind)
        meta["numeric_id"] = numeric_id
        if channel == "feishu" and kind == "group":
            try:
                info = self.fetch_chat_meta(chat_id) or {}
                meta["chat_name"] = (info.get("name") or info.get("chat_name") or "").strip()
            except Exception:
                pass
        self._write_meta(path, meta)
        return path

    def _ensure_day_dir(self, chat_dir: str, when: Optional[datetime] = None) -> str:
        day_name = self._chinese_date_folder(when)
        path = os.path.join(chat_dir, day_name)
        os.makedirs(path, exist_ok=True)
        return path

    def _pick_json_path(self, day_dir: str) -> str:
        stem = self._dialog_stem()
        primary = os.path.join(day_dir, f"{stem}.json")
        if not os.path.exists(primary):
            return primary
        try:
            if os.path.getsize(primary) < self.max_file_bytes:
                return primary
        except OSError:
            return primary
        index = 2
        while True:
            candidate = os.path.join(day_dir, f"{stem}__{index}.json")
            if not os.path.exists(candidate):
                return candidate
            try:
                if os.path.getsize(candidate) < self.max_file_bytes:
                    return candidate
            except OSError:
                return candidate
            index += 1

    def _load_json_log(self, json_path: str, chat_id: str, chat_dir: str) -> dict:
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict) and isinstance(data.get("messages"), list):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        meta = self._read_meta(chat_dir)
        return {
            "version": 1,
            "layout": ARCHIVE_LAYOUT_VERSION,
            "channel": meta.get("channel") or self._archive_channel(chat_id),
            "session_id": meta.get("session_id") or self._session_id(chat_id),
            "chat_id": chat_id,
            "messages": [],
        }

    def _save_json_log(self, json_path: str, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        encoded = payload.encode("utf-8", errors="replace")
        if len(encoded) > self.max_file_bytes:
            notice = {
                "message_type": "notice",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "text": "（本条写入超过单文件上限，已截断部分历史后保存）",
            }
            msgs = data.get("messages")
            if isinstance(msgs, list):
                trimmed: List[Any] = []
                budget = max(0, self.max_file_bytes - len(json.dumps(notice, ensure_ascii=False).encode()) - 4096)
                for item in reversed(msgs):
                    trimmed.insert(0, item)
                    trial = {**data, "messages": trimmed + [notice]}
                    if len(json.dumps(trial, ensure_ascii=False).encode()) > budget:
                        trimmed.pop(0)
                        break
                data = {**data, "messages": trimmed + [notice]}
            payload = json.dumps(data, ensure_ascii=False, indent=2)
        tmp = json_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, json_path)

    def _append_record(self, chat_id: str, record: dict) -> None:
        chat_dir = self._ensure_chat_dir(chat_id)
        day_dir = self._ensure_day_dir(chat_dir)
        json_path = self._pick_json_path(day_dir)
        lock = self._locks[chat_id]
        with lock:
            data = self._load_json_log(json_path, chat_id, chat_dir)
            messages = data.setdefault("messages", [])
            if not isinstance(messages, list):
                messages = []
                data["messages"] = messages
            messages.append(record)
            self._save_json_log(json_path, data)

    def log_user_message(
        self,
        chat_id: str,
        user_id: str,
        message_id: str,
        msg_type: str,
        text: str,
        *,
        media_urls: Optional[List[str]] = None,
    ) -> None:
        """记录用户侧消息。"""
        record = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": user_id,
            "message_id": message_id,
            "message_type": msg_type,
            "text": text if text else "（无文本正文）",
        }
        urls = [str(u).strip() for u in (media_urls or []) if str(u).strip()]
        if urls:
            record["media_urls"] = urls
        self._append_record(chat_id, record)

    def log_assistant_message(self, chat_id: str, body: str) -> None:
        """记录机器人 / DeepSeek 回复。"""
        record = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "text": body if body else "（空回复）",
        }
        self._append_record(chat_id, record)
