"""External-manager view access for service reports (工作日报).

Rule change (v0.1.230): external accounts granted 工作日报 > 查看 in the
permission matrix can open a read-only, Word-export-style view page
(/service-reports/<id>/view) and the 日报查询 list page, scoped to their own
client. Without the grant they must still get 403.
"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent


class ServiceReportExternalViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_ext_view_test", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")
        with cls.module.app.app_context():
            cls.module.init_db()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            conn = self.module.db()
            # Clean slate for this test's rows
            conn.execute("delete from role_action_permissions")
            conn.execute("delete from service_reports")
            conn.execute("delete from service_orders")
            conn.execute("delete from users where email like 'extview%'")
            conn.execute("delete from clients where client_number like 'CLI-EV-%'")
            client_a = conn.execute(
                "insert into clients (name, client_number, short_name, country, created_at) values ('Client A', 'CLI-EV-A', 'A', 'US', '2026-09-17T00:00:00')"
            ).lastrowid
            client_b = conn.execute(
                "insert into clients (name, client_number, short_name, country, created_at) values ('Client B', 'CLI-EV-B', 'B', 'US', '2026-09-17T00:00:00')"
            ).lastrowid
            admin = conn.execute("select id from users where role='admin' limit 1").fetchone()
            ext_same = conn.execute(
                "insert into users (name, email, password_hash, role, client_id, created_at) values ('ExtSame', 'extview-same@test.com', 'x', 'external_manager', ?, '2026-09-17T00:00:00')",
                (client_a,),
            ).lastrowid
            ext_other = conn.execute(
                "insert into users (name, email, password_hash, role, client_id, created_at) values ('ExtOther', 'extview-other@test.com', 'x', 'external_manager', ?, '2026-09-17T00:00:00')",
                (client_b,),
            ).lastrowid
            order_a = conn.execute(
                "insert into service_orders (order_number, client_id, client_name, site_address, client_order_number, status, created_by, created_at) values ('SO-EV-A', ?, 'Client A', '100 Site Rd, Test City, TX 12345', 'CL-ORD-EV-A', 'open', ?, '2026-09-17T00:00:00')",
                (client_a, admin["id"]),
            ).lastrowid
            report_id = conn.execute(
                """
                insert into service_reports (
                    service_order_id, report_date, actual_work_date, created_by, created_at, updated_at,
                    service_description, arrival_time, departure_time
                ) values (?, '2026-09-16', '2026-09-16', ?, '2026-09-17T00:00:00', '2026-09-17T00:00:00',
                          '更换了 2 号保险丝', '09:00', '17:00')
                """,
                (order_a, admin["id"]),
            ).lastrowid
            conn.execute(
                "insert into service_report_workers (report_id, user_id, travel_mode) values (?, ?, 'self_drive')",
                (report_id, admin["id"]),
            )
            conn.commit()
            self.report_id = report_id
            self.ext_same_id = ext_same
            self.ext_other_id = ext_other
        self.client = self.module.app.test_client()

    def _login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id

    def _deny_view(self, role="external_manager"):
        """权限管理里取消勾选「工作日报-查看」→ 显式 is_enabled=0 覆盖。"""
        self._upsert_action(role, "view", 0)

    def _grant_view(self, role="external_manager"):
        self._upsert_action(role, "view", 1)

    def _upsert_action(self, role, action_key, is_enabled):
        with self.module.app.app_context():
            conn = self.module.db()
            admin_id = conn.execute("select id from users where role='admin' limit 1").fetchone()["id"]
            conn.execute(
                "insert or replace into role_action_permissions (role, resource_key, action_key, is_enabled, updated_by, updated_at) values (?, 'service_reports', ?, ?, ?, '2026-09-17T00:00:00')",
                (role, action_key, is_enabled, admin_id),
            )
            conn.commit()

    def test_01_default_view_allowed_without_explicit_grant(self):
        """权限矩阵默认给所有角色勾选了「工作日报-查看」：外部管理员默认可看。

        历史行为是路由里 is_internal_user() 一刀切 403，与矩阵不符；
        现在以矩阵为准。
        """
        self._login(self.ext_same_id)
        resp = self.client.get("/reports/service-reports")
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(f"/service-reports/{self.report_id}/view")
        self.assertEqual(resp.status_code, 200)

    def test_02_explicit_deny_gets_403(self):
        """权限管理里取消「查看」勾选后，外部管理员重新被拒。"""
        self._deny_view()
        self._login(self.ext_same_id)
        resp = self.client.get("/reports/service-reports")
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get(f"/service-reports/{self.report_id}/view")
        self.assertEqual(resp.status_code, 403)

    def test_02b_view_page_is_word_style_readonly(self):
        """默认权限下，外部管理员打开的是 Word 样式只读页。"""
        self._login(self.ext_same_id)
        resp = self.client.get("/reports/service-reports")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("SO-EV-A".encode(), resp.data)
        resp = self.client.get(f"/service-reports/{self.report_id}/view")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("现场服务日报", html)
        self.assertIn("现场服务人员", html)
        self.assertIn("更换了 2 号保险丝", html)
        # Read-only: must not contain the edit form's save controls
        self.assertNotIn("save_token", html)

    def test_03_client_scope_blocks_other_client(self):
        """授予查看权限也看不到别的客户工单的日报（403）。"""
        self._grant_view()
        self._login(self.ext_other_id)
        resp = self.client.get(f"/service-reports/{self.report_id}/view")
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get("/reports/service-reports")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("SO-EV-A".encode(), resp.data)

    def test_04_export_button_follows_export_permission(self):
        """查看页的导出 Word 按钮跟随导出权限显示（默认允许，显式取消后隐藏）。"""
        self._login(self.ext_same_id)
        self._upsert_action("external_manager", "export", 0)
        resp = self.client.get(f"/service-reports/{self.report_id}/view")
        self.assertNotIn("导出 Word".encode(), resp.data)
        self._upsert_action("external_manager", "export", 1)
        resp = self.client.get(f"/service-reports/{self.report_id}/view")
        self.assertIn("导出 Word".encode(), resp.data)

    def test_05_internal_admin_still_works(self):
        """内部管理员不受影响。"""
        with self.module.app.app_context():
            conn = self.module.db()
            admin_id = conn.execute("select id from users where role='admin' limit 1").fetchone()["id"]
        self._login(admin_id)
        resp = self.client.get(f"/service-reports/{self.report_id}/view")
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get("/reports/service-reports")
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
