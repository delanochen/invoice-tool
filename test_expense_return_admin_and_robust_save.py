"""报销单三问题回归（EX2609032 反馈）：

1. 管理员退回报销不再 403 —— expenses.approve 默认组补 admin，
   且老库通过一次性数据迁移（settings 哨兵 expense_approve_admin_v1）修正。
2. 报销编辑/提交不再因辅助步骤（附件同步、查重、指纹、通知）的环境性
   异常返回 500 —— 保存主流程对非校验异常兜底。
3. 附件拷贝在文件占用 / 唯一索引并发冲突时按「跳过」处理而不是抛错。

闪烁问题（system-grid.js 小计/合计行）为纯前端熔断，见 JS 内注释与本目录
repro 脚本说明；此处覆盖其触发不到的 Python 链路。
"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent


class ExpenseReturnAdminTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_expense_return_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()


class AdminReturnExpenseTest(ExpenseReturnAdminTestBase):
    def _make_fixtures(self):
        module = self.module
        with module.app.app_context():
            connection = module.db()
            connection.execute("delete from expense_attachments")
            connection.execute("delete from expense_items")
            connection.execute("delete from expenses")
            connection.execute("delete from service_orders where order_number = 'SO-RET'")
            connection.execute("delete from users where email in ('ret-admin@example.com', 'ret-creator@example.com')")
            admin_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Ret Admin', 'ret-admin@example.com', ?, 'admin', 1, '2026-09-19T08:00:00')
                """,
                (module.generate_password_hash("ret-pass-123"),),
            ).lastrowid
            creator_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Ret Creator', 'ret-creator@example.com', ?, 'employee', 1, '2026-09-19T08:00:00')
                """,
                (module.generate_password_hash("ret-pass-123"),),
            ).lastrowid
            order_id = connection.execute(
                """
                insert into service_orders (
                    order_number, client_name, site_address, client_order_number, start_date,
                    created_by, created_at
                ) values ('SO-RET', 'Ret client', 'Ret address', 'RET-CLIENT', '2026-09-01', ?, '2026-09-01T08:00:00')
                """,
                (admin_id,),
            ).lastrowid
            expense_id = connection.execute(
                """
                insert into expenses (
                    expense_number, service_order_id, project, expense_date, amount,
                    currency, description, status, payout_status, created_by, beneficiary_id,
                    created_at, updated_at
                ) values ('EX-RET1', ?, 'Accommodation/Lodging住宿费', '2026-09-10', 100,
                          'USD', '', 'submitted', 'pending', ?, ?, '2026-09-10T08:00:00', '2026-09-10T08:00:00')
                """,
                (order_id, creator_id, creator_id),
            ).lastrowid
            connection.commit()
            return admin_id, expense_id

    def _login(self, email):
        client = self.module.app.test_client()
        response = client.post(
            "/login",
            data={"email": email, "password": "ret-pass-123"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        return client

    def test_admin_approve_permission_enabled_after_init(self):
        self._make_fixtures()
        with self.module.app.app_context():
            row = self.module.db().execute(
                """
                select is_enabled from role_action_permissions
                where role = 'admin' and resource_key = 'expenses' and action_key = 'approve'
                """
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["is_enabled"], 1)

    def test_admin_can_return_submitted_expense(self):
        _, expense_id = self._make_fixtures()
        client = self._login("ret-admin@example.com")
        response = client.post(
            f"/expenses/{expense_id}/return",
            data={"return_reason": "附件不清晰，请重新上传"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        with self.module.app.app_context():
            status = self.module.db().execute(
                "select status from expenses where id = ?", (expense_id,)
            ).fetchone()["status"]
        self.assertEqual(status, "returned")

    def test_migration_uses_sentinel_and_respects_manual_revoke(self):
        module = self.module
        with module.app.app_context():
            connection = module.db()
            # 模拟老库：行已固化关闭、哨兵不存在 → 迁移应开启
            connection.execute("delete from settings where key = 'expense_approve_admin_v1'")
            connection.execute(
                """
                update role_action_permissions set is_enabled = 0
                where role = 'admin' and resource_key = 'expenses' and action_key = 'approve'
                """
            )
            connection.commit()
            module.migrate_admin_expense_approve(connection)
            connection.commit()
            enabled = connection.execute(
                """
                select is_enabled from role_action_permissions
                where role = 'admin' and resource_key = 'expenses' and action_key = 'approve'
                """
            ).fetchone()["is_enabled"]
            self.assertEqual(enabled, 1)
            # 管理员事后手动关闭 + 哨兵已在 → 重启迁移不得再打开
            connection.execute(
                """
                update role_action_permissions set is_enabled = 0
                where role = 'admin' and resource_key = 'expenses' and action_key = 'approve'
                """
            )
            connection.commit()
            module.migrate_admin_expense_approve(connection)
            connection.commit()
            still_disabled = connection.execute(
                """
                select is_enabled from role_action_permissions
                where role = 'admin' and resource_key = 'expenses' and action_key = 'approve'
                """
            ).fetchone()["is_enabled"]
            self.assertEqual(still_disabled, 0)


class RobustExpenseSaveTest(ExpenseReturnAdminTestBase):
    def _make_expense(self):
        module = self.module
        with module.app.app_context():
            connection = module.db()
            connection.execute("delete from customer_reimbursement_expense_links")
            connection.execute("delete from customer_reimbursement_attachments")
            connection.execute("delete from customer_reimbursement_items")
            connection.execute("delete from customer_reimbursements")
            connection.execute("delete from expense_attachments")
            connection.execute("delete from expense_items")
            connection.execute("delete from expenses")
            connection.execute("delete from service_orders where order_number = 'SO-ROB'")
            connection.execute("delete from users where email = 'rob-admin@example.com'")
            connection.execute("delete from projects where project_type = 'expense' and name = 'Lodging'")
            admin_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Rob Admin', 'rob-admin@example.com', ?, 'admin', 1, '2026-09-19T08:00:00')
                """,
                (module.generate_password_hash("rob-pass-123"),),
            ).lastrowid
            order_id = connection.execute(
                """
                insert into service_orders (
                    order_number, client_name, site_address, client_order_number, start_date,
                    created_by, created_at
                ) values ('SO-ROB', 'Rob client', 'Rob address', 'ROB-CLIENT', '2026-09-01', ?, '2026-09-01T08:00:00')
                """,
                (admin_id,),
            ).lastrowid
            project_id = connection.execute(
                """
                insert into projects (name, name_key, project_type, tax_rate, is_active, created_at)
                values ('Lodging', 'lodging', 'expense', 0, 1, '2026-09-01T08:00:00')
                """
            ).lastrowid
            expense_id = connection.execute(
                """
                insert into expenses (
                    expense_number, service_order_id, project, expense_date, amount,
                    currency, description, status, payout_status, created_by, beneficiary_id,
                    created_at, updated_at
                ) values ('EX-ROB1', ?, 'Lodging', '2026-09-10', 100,
                          'USD', '', 'draft', 'pending', ?, ?, '2026-09-10T08:00:00', '2026-09-10T08:00:00')
                """,
                (order_id, admin_id, admin_id),
            ).lastrowid
            connection.execute(
                """
                insert into expense_items (expense_id, project_id, project, amount, line_key)
                values (?, ?, 'Lodging', 100, 'line-a')
                """,
                (expense_id, project_id),
            )
            connection.commit()
            return admin_id, order_id, project_id, expense_id

    def _login(self):
        client = self.module.app.test_client()
        response = client.post(
            "/login",
            data={"email": "rob-admin@example.com", "password": "rob-pass-123"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        return client

    def _save_token(self, client, expense_id):
        page = client.get(f"/expenses/{expense_id}/edit")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        marker = 'name="save_token" value="'
        start = html.index(marker) + len(marker)
        return html[start:html.index('"', start)]

    def _post_save(self, client, expense_id, project_id, admin_id):
        return client.post(
            f"/expenses/{expense_id}/edit",
            data={
                "save_token": self._save_token(client, expense_id),
                "beneficiary_id": str(admin_id),
                "expense_date": "2026-09-10",
                "description": "updated",
                "project_id": str(project_id),
                "item_line_key": "line-a",
                "item_amount": "100",
                "item_description": "hotel",
                "action": "save",
            },
            follow_redirects=False,
        )

    def test_sync_copy_swallows_copyfile_oserror(self):
        module = self.module
        _, order_id, _, _ = self._make_expense()
        with module.app.app_context():
            reimbursement_id = module.db().execute(
                """
                insert into customer_reimbursements (
                    service_order_id, file_name, stored_filename, created_by, created_at
                ) values (?, 'settle.pdf', 'stored-settle.pdf', 1, '2026-09-19T08:00:00')
                """,
                (order_id,),
            ).lastrowid
            source = Path(self.module.DATA_DIR) / "copy-source.bin"
            source.write_bytes(b"attachment-bytes")
            original_copyfile = module.shutil.copyfile

            def broken_copyfile(*args, **kwargs):
                raise PermissionError(32, "文件被另一个进程占用")

            module.shutil.copyfile = broken_copyfile
            try:
                copied = module.copy_file_to_customer_reimbursement_attachment(
                    reimbursement_id, str(source), "receipt.pdf", "application/pdf", None
                )
            finally:
                module.shutil.copyfile = original_copyfile
        self.assertFalse(copied)

    def test_sync_copy_swallows_unique_index_race(self):
        module = self.module
        _, order_id, _, expense_id = self._make_expense()
        with module.app.app_context():
            reimbursement_id = module.db().execute(
                """
                insert into customer_reimbursements (
                    service_order_id, file_name, stored_filename, created_by, created_at
                ) values (?, 'settle.pdf', 'stored-settle.pdf', 1, '2026-09-19T08:00:00')
                """,
                (order_id,),
            ).lastrowid
            attachment_id = module.db().execute(
                """
                insert into expense_attachments (
                    expense_id, expense_item_key, original_filename, stored_filename,
                    content_type, uploaded_by, uploaded_at
                ) values (?, 'line-a', 'receipt.pdf', 'stored-receipt.pdf', 'application/pdf', 1, '2026-09-10T08:00:00')
                """,
                (expense_id,),
            ).lastrowid
            source = Path(self.module.DATA_DIR) / "expense-attachments" / str(expense_id) / "stored-receipt.pdf"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"%PDF-1.4 race")
            # 并发对手已把同一来源附件写入结算附件（唯一索引冲突）
            module.db().execute(
                """
                insert into customer_reimbursement_attachments (
                    customer_reimbursement_id, original_filename, stored_filename,
                    content_type, source_expense_attachment_id, uploaded_by, uploaded_at
                ) values (?, 'receipt.pdf', 'already-copied.pdf', 'application/pdf', ?, 1, '2026-09-19T08:00:00')
                """,
                (reimbursement_id, attachment_id),
            )
            connection = module.db()
            copied = module.copy_file_to_customer_reimbursement_attachment(
                reimbursement_id, str(source), "receipt.pdf", "application/pdf", 1,
                source_expense_attachment_id=attachment_id,
            )
            connection.rollback()
        self.assertFalse(copied)

    def test_fingerprints_survive_pillow_bomb_style_errors(self):
        module = self.module
        target = Path(self.module.DATA_DIR) / "fingerprint.bin"
        target.write_bytes(b"fake-image-bytes")
        original_open = module.Image.open

        def bomb_open(*args, **kwargs):
            raise module.Image.DecompressionBombError(" Resolution is too large.")

        module.Image.open = bomb_open
        try:
            file_sha256, image_dhash = module.expense_attachment_fingerprints(str(target))
        finally:
            module.Image.open = original_open
        self.assertEqual(len(file_sha256), 64)
        self.assertEqual(image_dhash, "")

    def test_edit_save_survives_duplicate_check_crash(self):
        module = self.module
        admin_id, _, project_id, expense_id = self._make_expense()
        client = self._login()
        original = module.run_expense_duplicate_checks

        def crash(*args, **kwargs):
            raise RuntimeError("deepseek exploded")

        module.run_expense_duplicate_checks = crash
        try:
            response = self._post_save(client, expense_id, project_id, admin_id)
        finally:
            module.run_expense_duplicate_checks = original
        self.assertEqual(response.status_code, 302)
        with module.app.app_context():
            description = module.db().execute(
                "select description from expenses where id = ?", (expense_id,)
            ).fetchone()["description"]
        self.assertEqual(description, "updated")

    def test_edit_save_survives_sync_crash(self):
        module = self.module
        admin_id, _, project_id, expense_id = self._make_expense()
        client = self._login()
        original = module.sync_expense_attachments_to_settlement

        def crash(*args, **kwargs):
            raise OSError("attachment store unavailable")

        module.sync_expense_attachments_to_settlement = crash
        try:
            response = self._post_save(client, expense_id, project_id, admin_id)
        finally:
            module.sync_expense_attachments_to_settlement = original
        self.assertEqual(response.status_code, 302)
        with module.app.app_context():
            status = module.db().execute(
                "select status from expenses where id = ?", (expense_id,)
            ).fetchone()["status"]
        self.assertEqual(status, "draft")


if __name__ == "__main__":
    unittest.main()
