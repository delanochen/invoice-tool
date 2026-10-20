"""HTTP ingestion boundary for the Windows WeChat collector."""
from __future__ import annotations

import hmac

from flask import jsonify, request


def register_wechat_intelligence_routes(app, *, service, get_setting):
    def _authorized():
        expected = str(get_setting("wechat_ingest_token", "") or "").strip()
        supplied = str(request.headers.get("X-WeChat-Ingest-Token", "") or "").strip()
        return bool(expected and supplied and hmac.compare_digest(expected, supplied))

    def ingest_wechat_messages():
        if not _authorized():
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        payload = request.get_json(silent=True) or {}
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return jsonify({"ok": False, "error": "messages must be a non-empty list"}), 400
        if len(messages) > 500:
            return jsonify({"ok": False, "error": "batch too large"}), 413
        result = service.ingest_batch(messages)
        return jsonify({"ok": not result["errors"], **result}), (207 if result["errors"] else 200)

    app.add_url_rule(
        "/api/v1/wechat/messages/batch",
        endpoint="wechat_messages_batch",
        view_func=ingest_wechat_messages,
        methods=["POST"],
    )
    return {"ingest_wechat_messages": ingest_wechat_messages}
