"""Parser regression tests; no application import or database connection."""

import io
import unittest

from flask import Flask, request
from werkzeug.datastructures import MultiDict

from settlement_request_limits import register_settlement_request_limits


class SettlementRequestLimitsTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
        register_settlement_request_limits(self.app)

        def parse():
            return {"rows": len(request.form.getlist("worker_name")),
                    "action": request.form.get("action"),
                    "attachments": len(request.files.getlist("attachments"))}

        self.app.add_url_rule("/settlement", "customer_reimbursement_form", parse, methods=["POST"])
        self.app.add_url_rule("/other", "other", parse, methods=["POST"])
        self.client = self.app.test_client()

    def payload(self, rows):
        fields = [("action", "submit")]
        for i in range(rows):
            fields.append(("worker_name", f"Worker {i}"))
            fields.extend((f"field_{j}", "0") for j in range(25))
        fields.append(("attachments", (io.BytesIO(b"test attachment"), "proof.pdf")))
        return MultiDict(fields)

    def test_large_settlement_preserves_rows_action_and_attachment(self):
        response = self.client.post("/settlement", data=self.payload(180), content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"rows": 180, "action": "submit", "attachments": 1})

    def test_other_endpoints_keep_default_part_limit(self):
        response = self.client.post("/other", data=self.payload(180), content_type="multipart/form-data")
        self.assertEqual(response.status_code, 413)

    def test_settlement_part_limit_remains_bounded(self):
        response = self.client.post("/settlement", data=self.payload(1154), content_type="multipart/form-data")
        self.assertEqual(response.status_code, 413)

    def test_total_byte_limit_is_still_enforced(self):
        self.app.config["MAX_CONTENT_LENGTH"] = 1000
        response = self.client.post("/settlement", data=self.payload(2), content_type="multipart/form-data")
        self.assertEqual(response.status_code, 413)
