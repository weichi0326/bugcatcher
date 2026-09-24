# -*- coding: utf-8 -*-
"""命令模块。
所有命令在模块顶部集中定义，所有处理函数在下方统一实现。
"""

import logging
import threading
import time
from typing import Callable, List

COMMANDS = [
    # ════════════════════════════════════════
    # 命令: help
    # 说明: 显示所有可用命令
    # ════════════════════════════════════════
    {
        "trigger": "help",
        "description": "显示所有可用命令",
        "handler": "handle_help",
    },
    # ════════════════════════════════════════
    # 命令: sid
    # 说明: 返回当前用户的飞书标识，便于排查消息来源
    # ════════════════════════════════════════
    {
        "trigger": "sid",
        "description": "查看当前飞书用户标识",
        "handler": "handle_sid",
    },
    # ════════════════════════════════════════
    # 命令: gsid
    # 说明: 查看当前会话标识 chat_id
    # ════════════════════════════════════════
    {
        "trigger": "gsid",
        "description": "查看当前会话标识（chat_id）",
        "handler": "handle_gsid",
    },
    # ════════════════════════════════════════
    # 命令: restart
    # 说明: 仅重启机器人进程（bot.py），不重启 NapCat
    # ════════════════════════════════════════
    {
        "trigger": "restart",
        "description": "重启机器人进程（不影响 NapCat）",
        "handler": "handle_restart",
    },
    # ════════════════════════════════════════
    # 命令: 舆情明细
    # 说明: 舆情分析——分析当日 QQ 群舆情并生成报告推送飞书（所有人可用）
    # ════════════════════════════════════════
    {
        "trigger": "舆情明细",
        "description": "舆情分析：给出当日 QQ 群舆情报告",
        "handler": "handle_qq_game_sentiment",
    },
]


def _normalize_message(text: str) -> str:
    """标准化命令文本。"""
    return str(text or "").strip()


def _parse_command(text: str):
    """解析命令与参数。"""
    message = _normalize_message(text)
    if not message.startswith("/"):
        return "", ""
    content = message[1:].strip()
    if not content:
        return "", ""
    if " " in content:
        trigger, args = content.split(" ", 1)
        return trigger.strip(), args.strip()
    return content.strip(), ""


def dispatch_command(
    text: str,
    user_id: str,
    chat_id: str,
    context: dict,
):
    """命令路由入口（所有人可用）。
    返回 (是否命中命令, 回复文本)。
    """
    trigger, args = _parse_command(text)
    if not trigger:
        return False, ""
    matched = next((item for item in COMMANDS if item["trigger"] == trigger), None)
    if not matched:
        return False, ""
    handler_name = matched.get("handler")
    handler: Callable = globals().get(handler_name)
    if not callable(handler):
        return True, "这条命令暂时无法执行，请检查机器人配置。"
    try:
        return True, handler(args=args, user_id=user_id, chat_id=chat_id, context=context)
    except Exception as exc:  # pylint: disable=broad-except
        return True, f"命令执行失败：{exc}"


def _brief_help_desc(description: str) -> str:
    """把命令说明压成一行能看完的短句。"""
    s = str(description or "").strip()
    if not s:
        return "—"
    for sep in ("；", "，", "。", "（", "|"):
        if sep in s:
            s = s.split(sep, 1)[0].strip()
    return s or "—"


def _command_markdown_lines(cmd_items: List[dict]) -> List[str]:
    """命令列表（markdown 无序列表 + 命令名行内代码），供飞书富文本渲染。"""
    lines: List[str] = []
    for item in cmd_items:
        cmd = f"/{str(item['trigger'])}"
        desc = _brief_help_desc(str(item.get("description") or ""))
        lines.append(f"- `{cmd}` {desc}")
    return lines


def _build_help_panel(
    context: dict,
    *,
    chat_id: str = "",
) -> str:
    """生成帮助面板：markdown 富文本（加粗标题 + 命令列表），供飞书渲染。"""
    from botskill.persona_sync import get_display_name_for_wake

    wake = get_display_name_for_wake() or "群聊分析助手"
    cmds = [item for item in COMMANDS if item.get("trigger") != "help"]

    out: List[str] = [f"**{wake} · 帮助**"]
    out.append("")
    out.append("**命令**")
    if cmds:
        out.extend(_command_markdown_lines(cmds))
    else:
        out.append("（暂无命令）")
    return "\n".join(out).strip()


def handle_help(args: str, user_id: str, chat_id: str, context: dict) -> str:
    """显示命令帮助。"""
    _ = (args, user_id, context)
    return _build_help_panel(context, chat_id=chat_id or "")


def handle_sid(args: str, user_id: str, chat_id: str, context: dict) -> str:
    """返回当前用户的飞书标识。"""
    _ = (args, chat_id, context)
    if not user_id or user_id == "unknown_user":
        return "我暂时没能识别到你的用户标识，请稍后再试一次。"
    uid = str(user_id or "").strip()
    return f"这是你当前的飞书用户标识：\n{uid}"


def handle_gsid(args: str, user_id: str, chat_id: str, context: dict) -> str:
    """返回当前会话 chat_id 及群名称等信息。"""
    _ = (args, user_id)
    fetch_fn = context.get("fetch_chat_info")
    if not callable(fetch_fn):
        return "无法查询会话信息：服务端未注册 fetch_chat_info。"
    info = fetch_fn(chat_id) or {}
    name = (info.get("name") or info.get("chat_name") or "").strip()
    desc = (info.get("description") or "").strip()
    ctype = (info.get("chat_type") or "").strip()
    lines = [
        "当前 QQ 会话标识（chat_id / group_openid，用于归档子目录名）：",
        chat_id,
        "",
        f"会话名称：{name or '（未获取到，可直接使用上方 chat_id）'}",
    ]
    if ctype:
        lines.append(f"会话类型：{ctype}")
    if desc:
        lines.append(f"描述：{desc}")
    lines.append("")
    lines.append("说明：")
    lines.append("- 飞书：conversation_logs/<oc_会话ID>/<年>月<日>日/对话文件.json")
    lines.append("- QQ(NapCat)：conversation_logs/<QQ群号>/<年>月<日>日/对话文件.json")
    lines.append("- 可本地重命名最外层文件夹；同群消息仍写入（session.meta.json 内 session_id 不变）")
    lines.append("- 新建目录别名：bot_config.json → group_archive_aliases（键为会话 ID）")
    return "\n".join(lines)


def handle_restart(args: str, user_id: str, chat_id: str, context: dict) -> str:
    """在短暂延迟后仅重启机器人进程，先返回提示文案以便客户端收到回复。"""
    _ = (args, user_id)
    restart_fn = context.get("restart_app")
    if not callable(restart_fn):
        return "重启入口未注册：请联系开发者检查消息处理层注入的 command_context。"
    notify_chat = (chat_id or "").strip()

    def delayed_restart() -> None:
        time.sleep(0.9)
        restart_fn(notify_chat_id=notify_chat)

    threading.Thread(target=delayed_restart, daemon=True).start()
    return "好的，我正在安静重启服务，大约一两分钟后会恢复如常。"


def _parse_optional_date_arg(args: str, *, default_today: bool = False):
    from datetime import date, datetime

    raw = (args or "").strip()
    if not raw:
        return date.today() if default_today else None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _notify_feishu_chat(chat_id: str, body: str) -> None:
    """向飞书群回写命令结果（后台分析完成后）。"""
    cid = (chat_id or "").strip()
    text = (body or "").strip()
    if not cid or not text:
        return
    try:
        from botskill.im_transport import send_message

        send_message(cid, text)
    except Exception as exc:  # pylint: disable=broad-except
        logging.warning("飞书通知发送失败 chat_id=%s: %s", cid, exc)


def _format_qq_sentiment_result(result: dict, mode_hint: str) -> str:
    if not result.get("ok"):
        return f"舆情分析失败：{result.get('reason') or result.get('error') or '未知错误'}"
    if result.get("insufficient"):
        return (
            f"舆情分析：当日 QQ 归档消息数量不足（{result.get('total_messages', 0)} 条），"
            "不足以支撑完整分析。\n"
            "请确认 NapCat 已登录、测试群有消息记录后重试。"
        )
    pushed = ", ".join(result.get("pushed_to") or []) or "无"
    analyzed = result.get("analyzed_day") or result.get("day") or ""
    title = result.get("report_title") or ""
    head = title or (f"{analyzed} 舆情分析" if analyzed else "舆情分析报告")
    return (
        f"{head}已完成（{mode_hint}）。\n"
        f"- 模式：{result.get('mode', '')}\n"
        f"- 消息合计：{result.get('total_messages', 0)} 条\n"
        f"- 分片：{result.get('batches', 1)}\n"
        f"- 报告：{result.get('path', '')}\n"
        f"- 已推送飞书群：{pushed}"
    )


def handle_qq_game_sentiment(args: str, user_id: str, chat_id: str, context: dict) -> str:
    """手动触发舆情分析（后台执行；结果推飞书）。只分析今天（0:00→当前）。

    每天按配置时刻自动生成当日报告，并保留为后续检索问答的数据源。
    """
    _ = (user_id, context)

    from botskill.analysis.jobs import submit_analysis_job
    from botskill.analysis.qq_sentiment import run_qq_manual_range

    notify_chat = (chat_id or "").strip()
    extra = ""
    if (args or "").strip():
        extra = "\n（注：本命令只支持分析今天；定时报告按配置时刻分析当天记录。）"

    mode_hint = "当日 0:00 至当前，覆写今日报告"
    label = "qq_manual_intraday"

    def _job(abort_check=None) -> dict:
        # 报告只推送到发起命令的群（不跨群）
        push_targets = [notify_chat] if notify_chat else None
        return run_qq_manual_range(days=1, push=True, abort_check=abort_check, push_to=push_targets)

    def _done(result: dict) -> None:
        # 成功（含数据不足）时报告已由 push 直接发到群内，不再回执摘要；
        # 仅失败/超时时回一句简短提示，避免「正在分析」后静默无结果。
        if not result.get("ok"):
            _notify_feishu_chat(notify_chat, _format_qq_sentiment_result(result, mode_hint))

    submit_analysis_job(label, _job, on_done=_done)
    return (
        f"好的，正在分析当日 QQ 群舆情（{mode_hint}）…\n"
        "正在读取测试群归档并调用模型整理，通常需要约 1~2 分钟。\n"
        "报告整理好后会直接发到本群；若失败会提示原因，请不要重复触发。"
        f"{extra}"
    )
