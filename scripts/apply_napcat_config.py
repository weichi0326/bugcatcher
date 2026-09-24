# -*- coding: utf-8 -*-
"""将机器人通用桥接配置写入 NapCat（委托 botskill.napcat_bridge）。"""

from __future__ import annotations

import os
import sys
from typing import Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def apply(qq_uin: Optional[str] = None, *, wait_config_sec: int = 120) -> str:
    from botskill.napcat_bridge import apply_bridge, bridge_settings_from, load_integration

    integration = load_integration()
    result = apply_bridge(
        uin=qq_uin,
        wait_config_sec=wait_config_sec,
        integration=integration,
    )
    return result.primary_path


def napcat_config_dir(napcat_root: str) -> str:
    from botskill.napcat_paths import napcat_config_dir as _dir

    return _dir(napcat_root)


def wait_for_onebot_config(napcat_root: str, qq_uin: str, timeout_sec: int = 120) -> str:
    from botskill.napcat_bridge import wait_for_onebot_config as _wait

    return _wait(napcat_root, qq_uin, timeout_sec)


def main() -> None:
    from botskill.napcat_bridge import apply_bridge, bridge_settings_from, link_summary, load_integration

    integration = load_integration()
    settings = bridge_settings_from(integration=integration)
    result = apply_bridge(integration=integration)
    print("已写入 NapCat 通用桥接（config/napcat_bridge.json）：")
    print(link_summary(result, settings))
    print()
    print("若 NapCat 已在运行：重启 NapCat 或在 WebUI 重载网络配置。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"失败: {exc}", file=sys.stderr)
        sys.exit(1)
