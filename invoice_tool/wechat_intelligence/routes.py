"""HTTP ingestion and source-management boundary for the Windows WeChat collector."""
from __future__ import annotations

import hmac

from flask import flash, jsonify, redirect, render_template, request, url_for


def register_wechat_intelligence_routes(
    app,
    *,
    service,
    get_setting,
    admin_required,
):
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

    def ingest_wechat_group_catalog():
        if not _authorized():
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        payload = request.get_json(silent=True) or {}
        groups = payload.get("groups")
        if not isinstance(groups, list):
            return jsonify({"ok": False, "error": "groups must be a list"}), 400
        if len(groups) > 500:
            return jsonify({"ok": False, "error": "catalog too large"}), 413
        result = service.ingest_group_catalog(groups)
        return jsonify({"ok": not result["errors"], **result}), (207 if result["errors"] else 200)

    @admin_required
    def wechat_sources():
        if request.method == "POST":
            chat_username = request.form.get("chat_username", "").strip()
            enabled = request.form.get("enabled") == "1"
            project_name = request.form.get("project_name", "").strip()
            service_order_raw = request.form.get("service_order_id", "").strip()
            service_order_id = None
            if service_order_raw:
                try:
                    service_order_id = int(service_order_raw)
                except ValueError:
                    flash("Invalid service order.", "error")
                    return redirect(url_for("wechat_sources"))
            try:
                service.save_group_source(
                    chat_username,
                    enabled=enabled,
                    service_order_id=service_order_id,
                    project_name=project_name,
                )
            except ValueError as exc:
                flash(str(exc), "error")
            else:
                flash(
                    "WeChat work group enabled." if enabled else "WeChat work group disabled.",
                    "success",
                )
            return redirect(url_for("wechat_sources"))
        return render_template(
            "wechat_sources.html",
            groups=service.list_group_sources(),
            service_orders=service.list_service_orders(),
        )

    app.add_url_rule(
        "/api/v1/wechat/messages/batch",
        endpoint="wechat_messages_batch",
        view_func=ingest_wechat_messages,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/v1/wechat/groups/catalog",
        endpoint="wechat_group_catalog",
        view_func=ingest_wechat_group_catalog,
        methods=["POST"],
    )
    app.add_url_rule(
        "/admin/integrations/wechat",
        endpoint="wechat_sources",
        view_func=wechat_sources,
        methods=["GET", "POST"],
    )
    return {
        "ingest_wechat_messages": ingest_wechat_messages,
        "ingest_wechat_group_catalog": ingest_wechat_group_catalog,
        "wechat_sources": wechat_sources,
    }
