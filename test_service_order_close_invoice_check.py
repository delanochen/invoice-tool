"""工单未开发票不能变更为“已完成”（v0.1.355 业务校验）。

用户诉求：如果工单还没有开发票，不能把工单的状态变更为“已完成”。

契约：
- 编辑工单提交 status=closed，而该工单没有任何非 void 发票 → 拒绝保存，
  工单状态保持原值，并提示先开具发票；
- 工单已有非 void 发票（含草稿/已开票）→ 允许变更为已完成；
- void 的发票不算“已开发票”。
"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent


class ServiceOrderCloseInvoiceCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_close_invoice_check_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute("delete from invoices")
            connection.execute("delete from service_orders")
            connection.execute("delete from buyers")
            connection.execute("delete from manufacturers")
            connection.execute("delete from owners")
            connection.execute("delete from work_order_types")
            connection.execute("delete from clients")
            connection.execute("delete from users")
            self.user_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, created_at)
                values ('Admin', 'admin@example.com', 'unused', 'admin', '2026-08-11T12:00:00')
                """
            ).lastrowid
            self.client_id = connection.execute(
                """
                insert into clients (client_number, name, short_name, created_at)
                values ('CLI999', 'Test client', 'Test', '2026-08-11T12:00:00')
                """
            ).lastrowid
            owner_id = connection.execute(
                "insert into owners (owner_number, name, created_at) values ('OWN999', 'Test owner', '2026-08-11T12:00:00')"
            ).lastrowid
            manufacturer_id = connection.execute(
                "insert into manufacturers (manufacturer_number, name, created_at) values ('MFG001', 'Default maker', '2026-08-11T12:00:00')"
            ).lastrowid
            self.buyer_id = connection.execute(
                """
                insert into buyers (
                    buyer_number, client_id, country, country_code, name, owner_id, owner,
                    manufacturer_id, detailed_address, equipment_manufacturer, created_at
                ) values ('BUY99999', ?, 'US', 'US', 'Test site', ?, 'Test owner', ?,
                          '100 Test St', 'Default maker', '2026-08-11T12:00:00')
                """,
                (self.client_id, owner_id, manufacturer_id),
            ).lastrowid
            self.work_order_type_id = connection.execute(
                """
                insert into work_order_types (code, name, description, is_active, created_at)
                values ('TEST', 'Test type', '', 1, '2026-08-11T12:00:00')
                """
            ).lastrowid
            self.order_id = connection.execute(
                """
                insert into service_orders (
                    order_number, client_id, buyer_id, manufacturer_id, client_name,
                    site_address, client_order_number, status, work_order_type_id,
                    region_code, country_code, created_by, created_at
                ) values ('SO2699999', ?, ?, ?, 'Test site', '100 Test St', 'CLIENT-SO-1',
                          'open', ?, 'americas', 'US', ?, '2026-08-11T12:00:00')
                """,
                (self.client_id, self.buyer_id, manufacturer_id, self.work_order_type_id, self.user_id),
            ).lastrowid
            connection.commit()

        self.http = self.module.app.test_client()
        with self.http.session_transaction() as session:
            session["user_id"] = self.user_id

    def _edit_form_data(self, status="open"):
        return {
            "client_id": str(self.client_id),
            "buyer_id": str(self.buyer_id),
            "site_address": "100 Test St",
            "client_order_number": "CLIENT-SO-1",
            "work_order_type_id": str(self.work_order_type_id),
            "status": status,
        }

    def _insert_invoice(self, status="issued"):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute(
                """
                insert into invoices (
                    invoice_number, client_id, service_order_id, issue_date, due_date,
                    currency, notes, status, created_by, created_at
                ) values ('INV99999', ?, ?, '2026-09-01', '2026-10-01', 'USD', '',
                          ?, ?, '2026-09-01T12:00:00')
                """,
                (self.client_id, self.order_id, status, self.user_id),
            )
            connection.commit()

    def _order_status(self):
        with self.module.app.app_context():
            return self.module.db().execute(
                "select status from service_orders where id = ?", (self.order_id,)
            ).fetchone()["status"]

    def test_cannot_close_without_invoice(self):
        """无发票时提交“已完成”被拒绝，工单状态保持进行中。"""
        response = self.http.post(f"/service-orders/{self.order_id}/edit", data=self._edit_form_data(status="closed"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._order_status(), "open")

    def test_can_close_after_invoice(self):
        """已有非 void 发票后可正常变更为已完成。"""
        self._insert_invoice(status="issued")
        response = self.http.post(f"/service-orders/{self.order_id}/edit", data=self._edit_form_data(status="closed"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._order_status(), "closed")

    def test_void_invoice_does_not_count(self):
        """只有 void 发票仍视为未开发票，拒绝变更为已完成。"""
        self._insert_invoice(status="void")
        response = self.http.post(f"/service-orders/{self.order_id}/edit", data=self._edit_form_data(status="closed"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._order_status(), "open")

    def test_open_status_always_allowed(self):
        """不涉及变更为已完成时保存不受影响。"""
        response = self.http.post(f"/service-orders/{self.order_id}/edit", data=self._edit_form_data(status="open"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._order_status(), "open")

    def test_already_closed_without_invoice_can_resave(self):
        """历史遗留的已关闭但无发票工单，重新保存（状态不变）不应被拦。"""
        with self.module.app.app_context():
            self.module.db().execute(
                "update service_orders set status = 'closed' where id = ?", (self.order_id,)
            )
            self.module.db().commit()
        response = self.http.post(f"/service-orders/{self.order_id}/edit", data=self._edit_form_data(status="closed"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._order_status(), "closed")

    def test_edit_form_disables_closed_without_invoice(self):
        """未开票工单的编辑表单中“已完成”选项应被禁用并带提示。"""
        html = self.http.get(f"/service-orders/{self.order_id}/edit").get_data(as_text=True)
        self.assertIn('value="closed"', html)
        self.assertIn('disabled title="该工单尚未开具发票，不能标记为已完成"', html)
        self.assertIn(">已完成</option>", html)


if __name__ == "__main__":
    unittest.main()
