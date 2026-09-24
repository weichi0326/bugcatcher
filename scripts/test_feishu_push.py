# -*- coding: utf-8 -*-
"""向 qq_game_sentiment.feishu_push_chat_ids 推送测试报告（失败不重试）。"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import date, datetime

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_LOG_PATH = os.path.join(_ROOT, "log", "push_test_last.log")
_TEST_REPORT_REL = os.path.join("analysis_reports", "test_push_report.md")


def _setup_stdio() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _log(line: str) -> None:
    text = str(line)
    print(text, flush=True)
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError:
        pass


def _load_test_body() -> str:
    from botskill.analysis.archive import qq_report_title

    path = os.path.join(_ROOT, _TEST_REPORT_REL)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    today = date.today()
    title = qq_report_title(today)
    return (
        f"未找到 {_TEST_REPORT_REL}，使用内置短文。\n\n"
        "- 这是一条推送测试\n"
        "- 若收到说明飞书链路正常\n"
    )


def main() -> int:
    _setup_stdio()
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "w", encoding="utf-8") as handle:
            handle.write(f"=== push test {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
    except OSError:
        pass

    try:
        from botskill.analysis.archive import qq_report_title
        from botskill.analysis.config import (
            load_analysis_config,
            normalize_whitelist,
            qq_cfg,
            report_push_card_header_template,
            report_push_use_interactive_card,
        )
        from botskill.analysis.push import (
            push_qq_report_to_feishu,
            push_qq_report_to_feishu_interactive,
        )
        from botskill.message_routing import apply_feishu_credentials
    except Exception as exc:
        _log(f"导入失败：{exc}")
        _log(traceback.format_exc())
        return 1

    if not apply_feishu_credentials():
        _log("错误：未加载飞书凭证，请填写 config/feishu_bot.json")
        return 1

    load_analysis_config(reload=True)
    targets = normalize_whitelist(qq_cfg().get("feishu_push_chat_ids"))
    if not targets:
        _log("错误：qq_game_sentiment.feishu_push_chat_ids 为空（请配置推送目标群）")
        return 1

    body = _load_test_body()
    today = date.today()
    title = f"{qq_report_title(today)}（推送测试）"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    _log("=" * 50)
    _log("  群聊分析助手 · 飞书推送测试")
    _log("=" * 50)
    _log(f"时间：{stamp}")
    _log(f"标题：{title}")
    _log(f"目标群数：{len(targets)}")
    for cid in targets:
        _log(f"  - {cid}")
    use_card = report_push_use_interactive_card()
    _log(f"推送格式：{'interactive 消息卡片' if use_card else 'post 富文本'}")
    _log("")

    if use_card:
        pushed = push_qq_report_to_feishu_interactive(
            targets,
            body,
            title=title,
            header_template=report_push_card_header_template(),
        )
    else:
        pushed = push_qq_report_to_feishu(targets, body, title=title)

    _log("-" * 50)
    _log(f"成功：{len(pushed)} / {len(targets)}")
    for cid in pushed:
        _log(f"  [OK] {cid}")
    failed = [c for c in targets if c not in pushed]
    for cid in failed:
        _log(f"  [失败·不重试] {cid}")
    _log("-" * 50)
    if failed:
        _log("失败常见原因：机器人未加入该群、无发消息权限、chat_id 无效。")
    if not pushed:
        _log("全部推送失败，请检查飞书后台与机器人是否在群内。")
    return 0 if pushed else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _setup_stdio()
        _log(f"未捕获异常：{exc}")
        _log(traceback.format_exc())
        raise SystemExit(3) from exc
