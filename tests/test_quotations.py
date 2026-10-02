"""Quotation module regression tests (PostgreSQL).

Covers:
- menu / permission gating for the new quotations endpoints;
- create / edit / delete round-trips (form -> row -> detail page);
- pricing line auto-totals (qty x rate) and stored subtotal / total;
- PDF export returns a valid PDF;
- service orders can link a quotation (and are rejected on client mismatch);
- deleting a quotation that a service order references is blocked.
"""

import json
import unittest
from io import BytesIO

import tests_pg

tests_pg.activate()

import app as app_module  # noqa: E402
from database import PostgreSQLConnection  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


def _db():
    return PostgreSQLConnection()


def _login(client, email, password):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def _quotation_form(client_a_id):
    return {
        "quotation_number": "",
        "quotation_date": "2026-10-01",
        "valid_until": "2026-10-31",
        "prepared_by": "QA Tester",
        "customer": "Sunfield Solar LLC",
        "contact": "Maria Gomez",
        "project_name": "Spring Creek PV Retrofit",
        "project_no": "SC-2026-014",
        "project_location": "Fort Worth, TX",
        "po_no": "",
        "status": "draft",
        "currency": "USD",
        "client_id": str(client_a_id),
        "project_description": "Retrofit and recommissioning of 2 MW PV inverter stations.",
        "scope_1": "Inverter diagnostics and firmware updates",
        "scope_2": "Replacement of failed cooling fans (12 units)",
        "scope_3": "",
        "pricing_qty_regular_labor": "40",
        "pricing_rate_regular_labor": "120",
        "pricing_amount_regular_labor": "",
        "pricing_qty_mileage": "650",
        "pricing_rate_mileage": "0.67",
        "pricing_amount_mileage": "",
        "tax": "0",
        "rate_regular_labor": "120",
        "rate_overtime_labor": "180",
        "rate_mileage": "0.67",
        "crew_size": "2",
        "workdays": "5",
        "hours_per_day": "8",
        "expected_start_date": "2026-11-02",
        "expected_completion": "2026-11-06",
        "normal_working_hours": "7:00 - 17:00",
        "customer_provides": ["access", "escort"],
        "assumptions_other": "Gate key provided at site office.",
        "payment_terms": "net_30",
        "payment_terms_other": "",
        "quotation_validity": "30 calendar days",
        "invoice_frequency": "upon_completion",
        "pricing_type": "estimated",
        "tax_note": "Excluded unless stated",
        "notes": "",
    }


class TestQuotations(unittest.TestCase):
    """报价单 CRUD、PDF 导出与工单关联。"""

    ADMIN_EMAIL = "quote-admin@example.com"
    ADMIN_PASSWORD = "pw-admin-123"
    FINANCE_EMAIL = "quote-finance@example.com"
    FINANCE_PASSWORD = "pw-finance-123"
    EMPLOYEE_EMAIL = "quote-employee@example.com"
    EMPLOYEE_PASSWORD = "pw-employee-123"

    @classmethod
    def setUpClass(cls):
        conn = tests_pg.connection()
        try:
            admin_id = conn.execute(
                "insert into users (name, email, password_hash, role, is_active, default_language, created_at) "
                "values (%s, %s, %s, 'admin', 1, 'zh-CN', '2026-01-01T00:00:00') returning id",
                ("Quote Admin", cls.ADMIN_EMAIL, generate_password_hash(cls.ADMIN_PASSWORD)),
            ).fetchone()[0]
            finance_id = conn.execute(
                "insert into users (name, email, password_hash, role, is_active, default_language, created_at) "
                "values (%s, %s, %s, 'finance', 1, 'zh-CN', '2026-01-01T00:00:00') returning id",
                ("Quote Finance", cls.FINANCE_EMAIL, generate_password_hash(cls.FINANCE_PASSWORD)),
            ).fetchone()[0]
            employee_id = conn.execute(
                "insert into users (name, email, password_hash, role, is_active, default_language, created_at) "
                "values (%s, %s, %s, 'user', 1, 'zh-CN', '2026-01-01T00:00:00') returning id",
                ("Quote Employee", cls.EMPLOYEE_EMAIL, generate_password_hash(cls.EMPLOYEE_PASSWORD)),
            ).fetchone()[0]
            client_a_id = conn.execute(
                "insert into clients (client_number, name, short_name, contact_name, email, country, created_at) "
                "values (%s, %s, %s, %s, %s, 'United States', '2026-01-01T00:00:00') returning id",
                ("CL-A", "Alpha Energy LLC", "Alpha", "Ada Lovelace", "ada@alpha.example"),
            ).fetchone()[0]
            client_b_id = conn.execute(
                "insert into clients (client_number, name, short_name, contact_name, email, country, created_at) "
                "values (%s, %s, %s, %s, %s, 'United States', '2026-01-01T00:00:00') returning id",
                ("CL-B", "Beta Farms Co", "Beta", "Bob Builder", "bob@beta.example"),
            ).fetchone()[0]
            buyer_id = conn.execute(
                "insert into buyers (buyer_number, name, contact_name, contact_details, country, created_at, client_id) "
                "values (%s, %s, %s, %s, 'United States', '2026-01-01T00:00:00', %s) returning id",
                ("BY-A", "Spring Creek PV Site", "Maria Gomez", "+1-555-0100", client_a_id),
            ).fetchone()[0]
            wot_id = conn.execute(
                "insert into work_order_types (code, name, description, is_active, created_at) "
                "values (%s, %s, %s, 1, '2026-01-01T00:00:00') returning id",
                ("PV-MAINT", "PV 维护", "PV maintenance"),
            ).fetchone()[0]
        finally:
            conn.close()
        cls.admin_id = admin_id
        cls.finance_id = finance_id
        cls.employee_id = employee_id
        cls.client_a_id = client_a_id
        cls.client_b_id = client_b_id
        cls.buyer_id = buyer_id
        cls.wot_id = wot_id

        cls.app = app_module.app
        cls.app.config.update(TESTING=True)
        cls.client = cls.app.test_client()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _login_admin(self):
        response = _login(self.client, self.ADMIN_EMAIL, self.ADMIN_PASSWORD)
        self.assertEqual(response.status_code, 200)

    def _login_finance(self):
        response = _login(self.client, self.FINANCE_EMAIL, self.FINANCE_PASSWORD)
        self.assertEqual(response.status_code, 200)

    def _login_employee(self):
        response = _login(self.client, self.EMPLOYEE_EMAIL, self.EMPLOYEE_PASSWORD)
        self.assertEqual(response.status_code, 200)

    def _create_quotation(self, **overrides):
        data = _quotation_form(self.client_a_id)
        data.update(overrides)
        response = self.client.post("/quotations/new", data=data)
        self.assertEqual(response.status_code, 302)
        with _db() as db:
            row = db.execute(
                "select * from quotations order by id desc limit 1"
            ).fetchone()
        return row

    # ------------------------------------------------------------------
    # permissions
    # ------------------------------------------------------------------

    def test_quotation_list_requires_login(self):
        response = self.app.test_client().get("/quotations")
        self.assertEqual(response.status_code, 302)

    def test_employee_cannot_view_quotations(self):
        self._login_employee()
        response = self.client.get("/quotations")
        self.assertEqual(response.status_code, 403)

    def test_admin_can_view_quotation_list(self):
        self._login_admin()
        response = self.client.get("/quotations")
        self.assertEqual(response.status_code, 200)
        self.assertIn("报价单管理", response.get_data(as_text=True))

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def test_create_quotation_generates_number_and_auto_totals(self):
        self._login_admin()
        row = self._create_quotation()
        self.assertTrue(row["quotation_number"].startswith("QT"))
        # 40 * 120 = 4800；650 * 0.67 = 435.50 → subtotal 5235.50
        self.assertEqual(float(row["subtotal"]), 5235.50)
        self.assertEqual(float(row["total"]), 5235.50)
        lines = json.loads(row["pricing_lines"])
        by_key = {line["key"]: line for line in lines}
        self.assertEqual(by_key["regular_labor"]["amount"], "4800.00")
        self.assertEqual(by_key["mileage"]["amount"], "435.50")

    def test_quotation_detail_page_renders(self):
        self._login_admin()
        row = self._create_quotation()
        response = self.client.get(f"/quotations/{row['id']}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(row["quotation_number"], html)
        self.assertIn("Sunfield Solar LLC", html)

    def test_edit_quotation_updates_row(self):
        self._login_admin()
        row = self._create_quotation()
        data = _quotation_form(self.client_a_id)
        data["quotation_number"] = row["quotation_number"]
        data["customer"] = "Sunfield Solar LLC (updated)"
        data["pricing_qty_overtime_labor"] = "8"
        data["pricing_rate_overtime_labor"] = "180"
        response = self.client.post(f"/quotations/{row['id']}/edit", data=data)
        self.assertEqual(response.status_code, 302)
        with _db() as db:
            updated = db.execute(
                "select * from quotations where id = ?", (row["id"],)
            ).fetchone()
        self.assertEqual(updated["customer"], "Sunfield Solar LLC (updated)")
        # subtotal 增加 1440 → 6675.50
        self.assertEqual(float(updated["subtotal"]), 6675.50)

    def test_fixed_price_quotation_stores_total_only(self):
        self._login_admin()
        row = self._create_quotation(pricing_type="fixed", total="15000", tax="0")
        self.assertEqual(row["pricing_type"], "fixed")
        # 总价合同：subtotal = 总价 - 税；明细与费率表不保留
        self.assertEqual(float(row["subtotal"]), 15000.00)
        self.assertEqual(float(row["total"]), 15000.00)
        self.assertEqual(json.loads(row["pricing_lines"]), [])
        self.assertEqual(json.loads(row["rate_schedule"]), [])
        response = self.client.get(f"/quotations/{row['id']}")
        html = response.get_data(as_text=True)
        self.assertIn("Fixed Price（合同总价）", html)
        self.assertIn("合同总价", html)

    def test_fixed_price_with_tax_subtracts_from_total(self):
        self._login_admin()
        row = self._create_quotation(pricing_type="fixed", total="15000", tax="1000")
        self.assertEqual(float(row["subtotal"]), 14000.00)
        self.assertEqual(float(row["tax"]), 1000.00)
        self.assertEqual(float(row["total"]), 15000.00)

    def test_quotation_number_follows_service_order_rule(self):
        """编号规则与工单同构：QT + YYMM + 3 位序号，取最小未用。"""
        self._login_admin()
        row = self._create_quotation()
        import re

        self.assertRegex(row["quotation_number"], r"^QT\d{7}$")

    def test_client_rates_endpoint_maps_contract_rates(self):
        """/quotations/rates 把客户合同费率映射到报价单费率表 key。"""
        self._login_admin()
        NOW = "2026-01-01T00:00:00"
        with _db() as db:
            contract_id = db.execute(
                "insert into contracts (contract_number, client_id, contract_type, title, "
                "status, currency, created_by, created_at, updated_at) "
                "values (?, ?, 'service', 'Alpha O&M', 'active', 'USD', ?, ?, ?) returning id",
                ("CT-QR1", self.client_a_id, self.admin_id, NOW, NOW),
            ).fetchone()["id"]
            version_id = db.execute(
                "insert into contract_rate_versions (contract_id, version_no, effective_from, "
                "effective_to, status, notes, created_by, created_at, updated_at) "
                "values (?, 1, '2026-01-01', NULL, 'active', '', ?, ?, ?) returning id",
                (contract_id, self.admin_id, NOW, NOW),
            ).fetchone()["id"]
            for rate_type, rate in (("regular_hours", 125), ("travel_hours", 90),
                                    ("waiting_standby_hours", 75), ("mileage", 0.67),
                                    ("per_diem", 55)):
                db.execute(
                    "insert into contract_rate_items (version_id, rate_type, unit, rate) "
                    "values (?, ?, ?, ?)",
                    (version_id, rate_type, "hour" if rate_type != "mileage" else "mile", rate),
                )
        response = self.client.get(f"/quotations/rates?client_id={self.client_a_id}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        rates = data["rates"]
        self.assertEqual(rates["regular_labor"], 125.0)
        self.assertEqual(rates["travel_time"], 90.0)
        self.assertEqual(rates["waiting_standby"], 75.0)
        self.assertEqual(rates["mileage"], 0.67)
        self.assertEqual(rates["per_diem"], 55.0)
        # 未配置的项不出现在结果里
        self.assertNotIn("technical_support", rates)
        # 无客户参数返回空
        response = self.client.get("/quotations/rates")
        self.assertEqual(response.get_json(), {"rates": {}})

    def test_delete_quotation(self):
        self._login_admin()
        row = self._create_quotation()
        response = self.client.post(f"/quotations/{row['id']}/delete")
        self.assertEqual(response.status_code, 302)
        with _db() as db:
            gone = db.execute(
                "select count(*) as count from quotations where id = ?",
                (row["id"],),
            ).fetchone()["count"]
        self.assertEqual(gone, 0)

    # ------------------------------------------------------------------
    # PDF export
    # ------------------------------------------------------------------

    def test_quotation_pdf_export(self):
        self._login_admin()
        row = self._create_quotation()
        response = self.client.get(f"/quotations/{row['id']}/pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        payload = response.get_data()
        self.assertTrue(payload.startswith(b"%PDF"))
        self.assertGreater(len(payload), 2000)

    # ------------------------------------------------------------------
    # service order linkage
    # ------------------------------------------------------------------

    def _service_order_payload(self, quotation_id=""):
        return {
            "client_id": str(self.client_a_id),
            "buyer_id": str(self.buyer_id),
            "work_order_type_id": str(self.wot_id),
            "site_address": "123 Solar Way, Fort Worth, TX",
            "client_order_number": "PO-2026-0888",
            "start_date": "2026-11-02",
            "quotation_id": str(quotation_id) if quotation_id else "",
            "status": "open",
        }

    def test_service_order_links_quotation(self):
        self._login_finance()
        row = self._create_quotation()
        response = self.client.post("/service-orders/new", data=self._service_order_payload(row["id"]))
        self.assertEqual(response.status_code, 302)
        with _db() as db:
            order = db.execute(
                "select service_orders.* from service_orders order by id desc limit 1"
            ).fetchone()
        self.assertEqual(order["quotation_id"], row["id"])
        # 详情页展示报价单链接
        detail = self.client.get(f"/service-orders/{order['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(row["quotation_number"], detail.get_data(as_text=True))

    def test_service_order_rejects_mismatched_quotation_client(self):
        self._login_finance()
        # 报价单属于 Client A；工单客户选 Client B → 拒绝且不插入工单
        row = self._create_quotation()
        payload = self._service_order_payload(row["id"])
        payload["client_id"] = str(self.client_b_id)
        response = self.client.post("/service-orders/new", data=payload)
        self.assertEqual(response.status_code, 302)
        with _db() as db:
            count = db.execute(
                "select count(*) as count from service_orders"
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_delete_quotation_blocked_when_linked_to_service_order(self):
        self._login_finance()
        row = self._create_quotation()
        self.client.post("/service-orders/new", data=self._service_order_payload(row["id"]))
        response = self.client.post(f"/quotations/{row['id']}/delete", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("已被工单引用", response.get_data(as_text=True))
        with _db() as db:
            alive = db.execute(
                "select count(*) as count from quotations where id = ?",
                (row["id"],),
            ).fetchone()["count"]
        self.assertEqual(alive, 1)


if __name__ == "__main__":
    unittest.main()
