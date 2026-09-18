"""菜单配置驱动的权限（v0.1.239）。

用户要求：发票的删除、工单结算的修改状态/删除，权限不要在代码里按角色写死，
必须全部由菜单权限配置（role_action_permissions，权限管理页面）决定，
默认财务/经理/管理员都有权限。

覆盖：
- 权限目录默认值（发票删除 / 工单结算 修改状态·删除·审核）
- 发票删除：关闭权限后删除被拒、开启后删除成功
- 工单结算：审核需 approve 权限；修改状态（重置）需 reset 权限；删除需 delete 权限
- 模板契约：不再按角色写死按钮可见性，改用 has_action_permission
"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent


class MenuConfigurablePermissionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_permission_menu_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    # ─── Setup helpers ──────────────────────────────────────────────────────

    def _set_action(self, role, resource_key, action_key, enabled):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute(
                """
                insert into role_action_permissions (role, resource_key, action_key, is_enabled, updated_by, updated_at)
                values (?, ?, ?, ?, null, '2026-09-17T00:00:00')
                on conflict(role, resource_key, action_key) do update set is_enabled = excluded.is_enabled
                """,
                (role, resource_key, action_key, 1 if enabled else 0),
            )
            connection.commit()

    def _login(self, user_id):
        self.http = self.module.app.test_client()
        with self.http.session_transaction() as session:
            session["user_id"] = user_id

    def setUp(self):
        with self.module.app.app_context():
            connection = self.module.db()
            for table in (
                "customer_reimbursement_attachments", "customer_reimbursement_items",
                "customer_reimbursements", "invoice_items", "invoices",
                "service_orders", "clients", "messages", "audit_logs", "users",
            ):
                connection.execute(f"delete from {table}")
            self.finance_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Finance', 'finance@example.com', 'unused', 'finance', 1, '2026-09-17T00:00:00')
                """
            ).lastrowid
            self.creator_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Manager', 'manager@example.com', 'unused', 'manager', 1, '2026-09-17T00:00:00')
                """
            ).lastrowid
            self.client_id = connection.execute(
                """
                insert into clients (client_number, name, short_name, created_at)
                values ('77777', 'Menu Config Client', 'MC Client', '2026-09-17T00:00:00')
                """
            ).lastrowid
            self.order_id = connection.execute(
                """
                insert into service_orders (
                    order_number, client_id, client_name, site_address, client_order_number,
                    status, created_by, created_at
                ) values ('SO-MENU-1', ?, 'Menu Config Client', 'Test Site', 'CO-MENU-1',
                          'open', ?, '2026-09-17T00:00:00')
                """,
                (self.client_id, self.creator_id),
            ).lastrowid
            connection.commit()
        self._login(self.finance_id)

    def _insert_invoice(self, status="draft", created_by=None):
        with self.module.app.app_context():
            connection = self.module.db()
            invoice_id = connection.execute(
                """
                insert into invoices (
                    invoice_number, client_id, service_order_id, issue_date, due_date,
                    status, created_by, created_at
                ) values (?, ?, ?, '2026-09-17', '2026-10-17', ?, ?, '2026-09-17T00:00:00')
                """,
                (f"INV-MENU-{status}", self.client_id, self.order_id, status,
                 created_by or self.creator_id),
            ).lastrowid
            connection.commit()
            return invoice_id

    def _invoice_exists(self, invoice_id):
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select id from invoices where id = ?", (invoice_id,)
            ).fetchone()
            return row is not None

    def _insert_reimbursement(self, status="submitted"):
        with self.module.app.app_context():
            connection = self.module.db()
            reimbursement_id = connection.execute(
                """
                insert into customer_reimbursements (
                    service_order_id, file_name, stored_filename, status, created_by, created_at
                ) values (?, 'menu-config.pdf', 'menu-config.pdf', ?, ?, '2026-09-17T00:00:00')
                """,
                (self.order_id, status, self.creator_id),
            ).lastrowid
            connection.commit()
            return reimbursement_id

    def _reimbursement_status(self, reimbursement_id):
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select status from customer_reimbursements where id = ?", (reimbursement_id,)
            ).fetchone()
            return row["status"] if row else None

    def _reimbursement_exists(self, reimbursement_id):
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select id from customer_reimbursements where id = ?", (reimbursement_id,)
            ).fetchone()
            return row is not None

    # ─── 默认权限 ───────────────────────────────────────────────────────────

    def test_default_roles_cover_finance_manager_admin(self):
        defaults = self.module.DEFAULT_ACTION_ROLES
        expected = {"admin", "manager", "finance"}
        self.assertEqual(defaults[("invoices", "delete")], expected)
        self.assertEqual(defaults[("customer_reimbursements", "delete")], expected)
        self.assertEqual(defaults[("customer_reimbursements", "reset")], expected)
        self.assertIn("manager", defaults[("customer_reimbursements", "approve")])
        self.assertIn("admin", defaults[("customer_reimbursements", "approve")])
        # 新 action 必须出现在权限管理页面的可选动作里
        actions = [
            item["actions"]
            for group in self.module.ROLE_ACTION_PERMISSION_GROUPS
            for item in group["items"]
            if item["key"] == "customer_reimbursements"
        ][0]
        self.assertIn("reset", actions)
        self.assertEqual(self.module.ACTION_LABELS["reset"], "修改状态")

    # ─── 发票删除 ───────────────────────────────────────────────────────────

    def test_invoice_delete_follows_menu_configuration(self):
        invoice_id = self._insert_invoice(status="void")
        self._set_action("finance", "invoices", "delete", False)
        response = self.http.post(f"/invoices/{invoice_id}/delete")
        self.assertEqual(response.status_code, 403, "关闭删除权限后应被拦截")
        self.assertTrue(self._invoice_exists(invoice_id), "关闭删除权限后发票不应被删除")

        self._set_action("finance", "invoices", "delete", True)
        response = self.http.post(f"/invoices/{invoice_id}/delete")
        self.assertIn(response.status_code, (302, 303))
        self.assertFalse(self._invoice_exists(invoice_id), "开启删除权限后发票应被删除")

    def test_invoice_delete_is_permission_only_not_creator_fallback(self):
        """v0.1.239: 删除完全由菜单配置决定，发起人身份不再绕过权限。"""
        self._set_action("manager", "invoices", "delete", False)
        self._login(self.creator_id)
        invoice_id = self._insert_invoice(status="draft", created_by=self.creator_id)
        response = self.http.post(f"/invoices/{invoice_id}/delete")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(self._invoice_exists(invoice_id))

    # ─── 工单结算：审核 / 修改状态 / 删除 ───────────────────────────────────

    def test_settlement_approve_requires_approve_permission(self):
        reimbursement_id = self._insert_reimbursement(status="submitted")
        self._set_action("finance", "customer_reimbursements", "approve", False)
        response = self.http.post(f"/customer-reimbursements/{reimbursement_id}/approve")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._reimbursement_status(reimbursement_id), "submitted")

        self._set_action("finance", "customer_reimbursements", "approve", True)
        response = self.http.post(f"/customer-reimbursements/{reimbursement_id}/approve")
        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(self._reimbursement_status(reimbursement_id), "approved")

    def test_settlement_return_requires_approve_permission(self):
        reimbursement_id = self._insert_reimbursement(status="submitted")
        self._set_action("finance", "customer_reimbursements", "approve", False)
        response = self.http.post(
            f"/customer-reimbursements/{reimbursement_id}/return",
            data={"return_reason": "请核对油费"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._reimbursement_status(reimbursement_id), "submitted")

    def test_settlement_reset_follows_menu_configuration(self):
        reimbursement_id = self._insert_reimbursement(status="approved")
        self._set_action("finance", "customer_reimbursements", "reset", False)
        response = self.http.post(f"/customer-reimbursements/{reimbursement_id}/reset")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._reimbursement_status(reimbursement_id), "approved")

        self._set_action("finance", "customer_reimbursements", "reset", True)
        response = self.http.post(f"/customer-reimbursements/{reimbursement_id}/reset")
        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(self._reimbursement_status(reimbursement_id), "draft")

    def test_settlement_delete_follows_menu_configuration(self):
        reimbursement_id = self._insert_reimbursement(status="draft")
        self._set_action("finance", "customer_reimbursements", "delete", False)
        response = self.http.post(f"/customer-reimbursements/{reimbursement_id}/delete")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(self._reimbursement_exists(reimbursement_id))

        self._set_action("finance", "customer_reimbursements", "delete", True)
        response = self.http.post(f"/customer-reimbursements/{reimbursement_id}/delete")
        self.assertIn(response.status_code, (302, 303))
        self.assertFalse(self._reimbursement_exists(reimbursement_id))

    # ─── 模板契约：按钮可见性不再按角色写死 ─────────────────────────────────

    def test_invoice_detail_template_uses_permission_not_role(self):
        source = (REPO_DIR / "templates" / "invoice_detail.html").read_text(encoding="utf-8")
        self.assertIn('has_action_permission("invoices", "delete")', source)
        self.assertNotIn('normalized_role() == "admin" or (invoice.status == "void"', source)
    def test_settlement_template_uses_permission_not_role(self):
        source = (REPO_DIR / "templates" / "customer_reimbursement_form.html").read_text(encoding="utf-8")
        self.assertIn('has_action_permission("customer_reimbursements", "approve")', source)
        self.assertIn('has_action_permission("customer_reimbursements", "reset")', source)
        self.assertIn('has_action_permission("customer_reimbursements", "delete")', source)
        self.assertNotIn('normalized_role() in ["admin", "manager"]', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
