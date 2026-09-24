# -*- coding: utf-8 -*-
"""将 config/配置主表.json 一键写入各运行时配置文件。"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _setup_stdio() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    _setup_stdio()
    from config.config_master import MASTER_PATH, apply_master_config

    reload = "--reload" in sys.argv
    result = apply_master_config(master_path=MASTER_PATH, reload_runtime=reload)
    print("=" * 50)
    print("  配置主表 · 一键应用")
    print("=" * 50)
    if result.get("written"):
        print("已写入：")
        for rel in result["written"]:
            print(f"  - {rel}")
    if result.get("errors"):
        print("\n错误：")
        for err in result["errors"]:
            print(f"  - {err}")
    if result.get("ok"):
        print("\n完成。修改主表后请重新运行本脚本；运行中服务可用 --reload 尝试热加载。")
        return 0
    print("\n未完成，请检查 config/配置主表.json 格式与 @file: 引用路径。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
