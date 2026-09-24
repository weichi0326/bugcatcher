# -*- coding: utf-8 -*-
"""
OneBot 事件 HTTP 入口（NapCat 反向 HTTP / 通用上报）。

在 NapCat 网络配置中添加 HTTP 客户端，URL 填：http://127.0.0.1:{port}/onebot
"""

import json
import logging

from flask import Flask, jsonify, request

from config import BOT_CONFIG

from botskill.onebot_client import try_archive_napcat_inbound_message

app = Flask(__name__)


@app.route("/onebot", methods=["POST"])
def onebot_event():
    body = request.get_json(silent=True)
    if body is None:
        raw = request.get_data(as_text=True) or ""
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            body = {}
    if not isinstance(body, dict):
        return jsonify({"status": "ignored"})

    if str(body.get("post_type") or "") == "message":
        try_archive_napcat_inbound_message(body)
    return jsonify({"status": "ok"})


@app.route("/health", methods=["GET"])
def health():
    napcat_ok = False
    try:
        import requests

        port = int(BOT_CONFIG.get("napcat_http_port") or 3000)
        r = requests.post(f"http://127.0.0.1:{port}/", json={}, timeout=2)
        napcat_ok = r.status_code == 200
    except Exception:
        pass
    return jsonify({"status": "ok", "backend": "onebot", "napcat_http": napcat_ok})


def run_onebot_http_server() -> None:
    port = int(BOT_CONFIG.get("port", 5000))
    logging.info(
        "NapCat 归档通道：监听 0.0.0.0:%s/onebot（仅写入 conversation_logs，不调用 DeepSeek）",
        port,
    )
    logging.info(
        "请在 NapCat 配置反向 HTTP → http://127.0.0.1:%s/onebot",
        port,
    )
    app.run(host="0.0.0.0", port=port, threaded=True)
