# -*- coding: utf-8 -*-
"""将 NapCat 从外部目录复制到项目内 napcat/ 并更新配置。"""

from __future__ import annotations

import json
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from botskill.napcat_paths import DEFAULT_NAPCAT_REL, resolve_napcat_root

INTEGRATION_PATH = os.path.join(ROOT, "config", "napcat_integration.json")
BOT_CONFIG_PATH = os.path.join(ROOT, "config", "bot_config.json")
OLD_DEFAULT = os.path.join(os.path.expanduser("~"), "Downloads", "NapCat.Shell.Windows.OneKey")


def _load_json(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _save_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _pick_source(integration: dict) -> str:
    for candidate in (
        str(integration.get("napcat_root") or "").strip(),
        OLD_DEFAULT,
    ):
        if not candidate:
            continue
        abs_path = resolve_napcat_root(candidate)
        exe = os.path.join(abs_path, "NapCat.44498.Shell", "NapCatWinBootMain.exe")
        if os.path.isfile(exe):
            return abs_path
        exe2 = os.path.join(abs_path, "bootmain", "NapCatWinBootMain.exe")
        if os.path.isfile(exe2):
            return abs_path
    raise FileNotFoundError(
        f"未找到可迁移的 NapCat 安装包。请确认存在：\n  {OLD_DEFAULT}\n"
        f"或先在 config/napcat_integration.json 填写当前 napcat_root。"
    )


def main() -> int:
    integration = _load_json(INTEGRATION_PATH)
    dest_rel = DEFAULT_NAPCAT_REL.replace("\\", "/")
    dest_abs = resolve_napcat_root(dest_rel)

    if os.path.isdir(dest_abs):
        exe = os.path.join(dest_abs, "NapCat.44498.Shell", "NapCatWinBootMain.exe")
        if os.path.isfile(exe):
            print(f"目标已存在且可启动，跳过复制：{dest_abs}")
        else:
            print(f"目标目录已存在但缺少启动程序，将尝试从源覆盖复制：{dest_abs}")
            shutil.rmtree(dest_abs)
    else:
        os.makedirs(os.path.dirname(dest_abs), exist_ok=True)

    if not os.path.isdir(dest_abs) or not os.path.isfile(
        os.path.join(dest_abs, "NapCat.44498.Shell", "NapCatWinBootMain.exe")
    ):
        src = _pick_source(integration)
        print(f"正在复制 NapCat …\n  源：{src}\n  目标：{dest_abs}")
        print("（体积较大，请耐心等待）")
        shutil.copytree(src, dest_abs, dirs_exist_ok=True)
        print("复制完成。")

    integration["napcat_root"] = dest_rel
    _save_json(INTEGRATION_PATH, integration)

    bot_cfg = _load_json(BOT_CONFIG_PATH)
    if isinstance(bot_cfg, dict):
        bot_cfg["napcat_root"] = dest_rel
        _save_json(BOT_CONFIG_PATH, bot_cfg)

    print(f"\n已更新配置 napcat_root → {dest_rel}")
    print("请关闭旧 NapCat 窗口后，在 WebUI 概览页点击“启动全部”。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"迁移失败：{exc}")
        raise SystemExit(1)
