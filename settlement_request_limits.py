"""Multipart parsing limits for the large settlement editing form."""

from flask import request


def configure_settlement_request_limits():
    # Each settlement row submits 26 fields, including source IDs and automatic
    # expense amounts. Werkzeug's default 1,000-part limit rejects ~39 rows
    # independently of MAX_CONTENT_LENGTH. Keep a finite, endpoint-only limit.
    if request.method == "POST" and request.endpoint == "customer_reimbursement_form":
        request.max_form_parts = 30_000


def register_settlement_request_limits(app):
    # Register before authentication/permission hooks can access request.form.
    app.before_request(configure_settlement_request_limits)
