# -*- coding: utf-8 -*-
"""
手动重写 NapCat ↔ 本地机器人消息连接配置（正常启动及切换账号会自动配置）。

用法（项目根）：
  python scripts/link_napcat_bridge.py
  python scripts/link_napcat_bridge.py --uin QQ_NUMBER
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="重写 NapCat 与本地机器人之间的 QQ 消息连接配置")
    parser.add_argument("--uin", help="指定 QQ 号；省略则自动检测当前登录账号")
    parser.add_argument("--wait", type=int, default=120, help="等待 onebot 配置文件出现的秒数")
    args = parser.parse_args()

    from botskill.napcat_bridge import (
        apply_bridge,
        bridge_settings_from,
        link_summary,
        load_integration,
    )

    integration = load_integration()
    settings = bridge_settings_from(integration=integration)
    result = apply_bridge(uin=args.uin, wait_config_sec=args.wait, integration=integration)
    print(link_summary(result, settings))
    print("\n配置已写入。请在运行管理重新启动全部服务，以使网络配置生效；此操作不连接飞书机器人。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
