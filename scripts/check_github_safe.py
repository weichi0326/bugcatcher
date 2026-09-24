# -*- coding: utf-8 -*-
"""
上传 GitHub 前安全检查。

禁止上传（与 .gitignore 一致）：
  - DeepSeek 密钥、飞书机器人 app_secret 等（配置主表及生成 JSON）
  - 本地消息留存 conversation_logs/（QQ）
  - 舆情分析报告 analysis_reports/
  - NapCat 整包及运行时账号配置
  - 运行日志 log/

允许上传：
  - 源代码、config/*.example.json、config/配置主表.example.json
  - 通用规则：config/飞书对话规则.md、config/QQ群聊分析规则.md、config/prompts/*.md
  - NapCat 官方下载器 napcat_setup.py（整包由 WebUI 按需安装）

用法：py -3 scripts/check_github_safe.py
"""
from __future__ import annotations

import fnmatch
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORCE_IGNORE = {
    "config/配置主表.json",
    "config/bot_config.json",
    "config/models.json",
    "config/feishu_bot.json",
    "config/analysis_config.json",
    "config/napcat_integration.json",
    "config/napcat_bridge.json",
    "config/napcat_bridge_state.json",
    "napcat/NapCat.Shell.Windows.OneKey/",
}

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("DeepSeek API Key", re.compile(r"sk-[a-zA-Z0-9]{20,}")),
    ("飞书 app_secret 疑似实值", re.compile(r'"app_secret"\s*:\s*"[a-zA-Z0-9]{16,}"')),
    ("白名单手机号段", re.compile(r"123442\d{4}")),
    ("内网 IPv4 地址", re.compile(r"\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")),
]

ALLOW_PLACEHOLDER_IN = (
    ".example.json",
    "配置主表.example.json",
    "check_github_safe.py",
)

ALWAYS_IGNORE_PREFIXES = (
    "conversation_logs/",
    "analysis_reports/",
    "log/",
    "logs/",
)


def _load_gitignore() -> list[str]:
    path = os.path.join(ROOT, ".gitignore")
    lines: list[str] = []
    if not os.path.isfile(path):
        return lines
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if line and not line.startswith("#"):
                lines.append(line)
    return lines


def _rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace("\\", "/")


def _match_gitignore(rel: str, rules: list[str]) -> bool:
    rel = rel.replace("\\", "/").lstrip("/")
    for rule in rules:
        r = rule.strip()
        if not r:
            continue
        if r.startswith("/"):
            r = r[1:]
        if r.endswith("/"):
            prefix = r[:-1]
            if rel == prefix or rel.startswith(prefix + "/"):
                return True
            continue
        if "**" in r:
            if fnmatch.fnmatch(rel, r.replace("**", "*")):
                return True
            continue
        if fnmatch.fnmatch(rel, r) or fnmatch.fnmatch(os.path.basename(rel), r):
            return True
    return False


def _is_napcat_runtime_config(rel: str) -> bool:
    return "/napcat/config/" in rel.replace("\\", "/") and rel.endswith(".json")


def _iter_candidate_files() -> list[str]:
    rules = _load_gitignore()
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in (".git", "__pycache__", ".venv", "venv", "node_modules")
            and not _match_gitignore(_rel(os.path.join(dirpath, d)), rules)
        ]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = _rel(full)
            if any(rel.startswith(p) for p in ALWAYS_IGNORE_PREFIXES):
                continue
            if _is_napcat_runtime_config(rel):
                continue
            if _match_gitignore(rel, rules):
                continue
            if rel in FORCE_IGNORE:
                continue
            out.append(full)
    return sorted(out)


def _is_placeholder_ok(rel: str) -> bool:
    return any(s in rel for s in ALLOW_PLACEHOLDER_IN)


def main() -> int:
    errors: list[str] = []
    rules = _load_gitignore()

    for rel in sorted(FORCE_IGNORE):
        full = os.path.join(ROOT, rel.replace("/", os.sep))
        if os.path.exists(full) and not _match_gitignore(rel, rules):
            errors.append(f"敏感文件存在且未被 .gitignore 排除：{rel}")

    candidates = _iter_candidate_files()
    for full in candidates:
        rel = _rel(full)
        try:
            with open(full, "r", encoding="utf-8", errors="ignore") as handle:
                text = handle.read(512 * 1024)
        except OSError as exc:
            errors.append(f"无法读取 {rel}: {exc}")
            continue
        for label, pat in SECRET_PATTERNS:
            if pat.search(text):
                if _is_placeholder_ok(rel) and label.startswith("DeepSeek") and "sk-xxx" in text:
                    continue
                errors.append(f"{rel}：命中 [{label}]")

    print("=" * 50)
    print("  GitHub 上传前安全检查")
    print("=" * 50)
    print(f"扫描可提交文件数：{len(candidates)}")
    print()

    if errors:
        print("[未通过]")
        for e in errors:
            print(" ", e)
        return 1

    print("[通过] 可提交文件未检出典型密钥/归档/内网包体特征。")
    print("上传前请再执行 git status，确认无 配置主表.json / conversation_logs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
