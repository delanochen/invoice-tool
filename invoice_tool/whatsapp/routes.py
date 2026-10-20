"""WhatsApp Cloud API webhook and employee binding routes."""

import hmac
from datetime import datetime, timezone
from flask import abort, g, jsonify, request


def register_whatsapp_routes(app, *, service, get_setting, login_required, can_manage_users):
    def webhook():
        if request.method == "GET":
            verify_token = get_setting("whatsapp_verify_token") or ""
            mode = request.args.get("hub.mode", "")
            token = request.args.get("hub.verify_token", "")
            challenge = request.args.get("hub.challenge", "")
            if mode == "subscribe" and verify_token and hmac.compare_digest(token, verify_token):
                return challenge, 200
            abort(403)

        app_secret = str(get_setting("whatsapp_app_secret") or "").strip()
        if not app_secret:
            # Fail closed: never accept an unsigned Meta webhook merely because
            # server configuration is incomplete.
            return jsonify({"ok": False, "error": "webhook_not_configured"}), 503
        signature = request.headers.get("X-Hub-Signature-256", "")
        import hashlib
        expected = "sha256=" + hmac.new(
            app_secret.encode("utf-8"), request.get_data(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            abort(403)
        payload = request.get_json(silent=True) or {}
        processed = 0
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                contacts = {c.get("wa_id"): c for c in value.get("contacts", []) if c.get("wa_id")}
                for msg in value.get("messages", []):
                    wa_id = msg.get("from") or ""
                    if not wa_id:
                        continue
                    msg_type = msg.get("type") or "unknown"
                    text = (msg.get("text") or {}).get("body", "") if msg_type == "text" else ""
                    media = msg.get(msg_type) if isinstance(msg.get(msg_type), dict) else {}
                    media_id = media.get("id")
                    timestamp = msg.get("timestamp")
                    message_time = None
                    if timestamp and str(timestamp).isdigit():
                        message_time = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat()
                    if not service.record_inbound(
                        meta_message_id=msg.get("id"), wa_id=wa_id, message_type=msg_type,
                        content=text, media_id=media_id, message_time=message_time, payload=msg,
                    ):
                        continue
                    contact = contacts.get(wa_id) or {}
                    display_name = (contact.get("profile") or {}).get("name")
                    if msg_type == "text" and text.strip().upper().startswith("PP-"):
                        service.consume_binding_code(text, wa_id, wa_id, display_name)
                    processed += 1
        return jsonify({"ok": True, "processed": processed})

    @login_required
    def binding_status(user_id):
        if user_id != g.user["id"] and not can_manage_users():
            abort(403)
        row = service.binding_for_user(user_id)
        if not row:
            return jsonify({"bound": False})
        return jsonify({
            "bound": True,
            "phone_number": row["phone_number"],
            "display_name": row["display_name"],
            "verified_at": row["verified_at"],
            "enabled": bool(row["enabled"]),
        })

    @login_required
    def create_binding_code(user_id):
        if user_id != g.user["id"] and not can_manage_users():
            abort(403)
        return jsonify(service.create_binding_code(user_id, g.user["id"]))

    @login_required
    def unbind(user_id):
        if user_id != g.user["id"] and not can_manage_users():
            abort(403)
        service.unbind(user_id)
        return jsonify({"ok": True})

    app.add_url_rule('/api/v1/channels/whatsapp/webhook', endpoint='whatsapp_webhook', view_func=webhook, methods=['GET', 'POST'])
    app.add_url_rule('/api/v1/users/<int:user_id>/whatsapp', endpoint='whatsapp_binding_status', view_func=binding_status, methods=['GET'])
    app.add_url_rule('/api/v1/users/<int:user_id>/whatsapp/binding-code', endpoint='whatsapp_binding_code', view_func=create_binding_code, methods=['POST'])
    app.add_url_rule('/api/v1/users/<int:user_id>/whatsapp/unbind', endpoint='whatsapp_unbind', view_func=unbind, methods=['POST'])
    return {"whatsapp_webhook": webhook}
