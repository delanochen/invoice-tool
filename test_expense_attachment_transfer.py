import importlib.util
import shutil
import tempfile
import unittest
from io import BytesIO
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent


class ExpenseAttachmentTransferTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", cls.module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_transfer_test_app", cls.module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute("delete from audit_logs")
            connection.execute("delete from customer_reimbursement_attachments")
            connection.execute("delete from customer_reimbursements")
            connection.execute("delete from expense_attachments")
            connection.execute("delete from expenses")
            connection.execute("delete from service_orders")
            connection.execute("delete from users")
            cursor = connection.execute(
                """
                insert into users (name, email, password_hash, role, created_at)
                values ('Admin', 'admin@example.com', 'unused', 'admin', '2026-08-02T12:00:00')
                """
            )
            self.user_id = cursor.lastrowid
            cursor = connection.execute(
                """
                insert into service_orders (
                    order_number, client_name, site_address, client_order_number, created_by, created_at
                ) values ('SO-TEST', 'Test client', 'Test address', 'CLIENT-TEST', ?, '2026-08-02T12:00:00')
                """,
                (self.user_id,),
            )
            self.order_id = cursor.lastrowid
            cursor = connection.execute(
                """
                insert into expenses (
                    service_order_id, expense_number, project, expense_date, amount, status,
                    created_by, created_at, updated_at
                ) values (?, 'EXP-TEST', 'Travel', '2026-08-02', 10, 'draft', ?,
                          '2026-08-02T12:00:00', '2026-08-02T12:00:00')
                """,
                (self.order_id, self.user_id),
            )
            self.expense_id = cursor.lastrowid
            cursor = connection.execute(
                """
                insert into projects (name, unit_price, is_active, project_type, created_at)
                values ('Travel', 0, 1, 'expense', '2026-08-02T12:00:00')
                """
            )
            project_id = cursor.lastrowid
            connection.execute(
                """
                insert into expense_items (expense_id, line_key, project_id, project, amount, sort_order)
                values (?, 'line-travel', ?, 'Travel', 10, 0)
                """,
                (self.expense_id, project_id),
            )
            cursor = connection.execute(
                """
                insert into expense_attachments (
                    expense_id, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
                ) values (?, 'receipt.png', 'source.png', 'image/png', ?, '2026-08-02T12:00:00')
                """,
                (self.expense_id, self.user_id),
            )
            self.attachment_id = cursor.lastrowid
            source_path = Path(self.module.expense_attachment_dir(self.expense_id)) / "source.png"
            source_path.write_bytes(b"test-image")
            cursor = connection.execute(
                """
                insert into expense_attachments (
                    expense_id, expense_item_key, original_filename, stored_filename,
                    content_type, uploaded_by, uploaded_at
                ) values (?, 'line-travel', 'travel-receipt.png', 'line-source.png',
                          'image/png', ?, '2026-08-02T12:00:00')
                """,
                (self.expense_id, self.user_id),
            )
            self.line_attachment_id = cursor.lastrowid
            line_source_path = Path(self.module.expense_attachment_dir(self.expense_id)) / "line-source.png"
            line_source_path.write_bytes(b"line-image")
            cursor = connection.execute(
                """
                insert into customer_reimbursements (
                    service_order_id, file_name, stored_filename, status, created_by, created_at
                ) values (?, 'settlement.pdf', 'settlement.pdf', 'draft', ?, '2026-08-02T12:00:00')
                """,
                (self.order_id, self.user_id),
            )
            self.reimbursement_id = cursor.lastrowid
            connection.commit()

        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id

    def transferred_rows(self):
        with self.module.app.app_context():
            return self.module.db().execute(
                """
                select * from customer_reimbursement_attachments
                where source_expense_attachment_id = ?
                """,
                (self.attachment_id,),
            ).fetchall()

    def test_transfer_copies_file_and_is_idempotent(self):
        edit_response = self.client.get(f"/expenses/{self.expense_id}/edit")
        self.assertEqual(edit_response.status_code, 200)
        self.assertIn('class="inline-thumb"', edit_response.get_data(as_text=True))
        self.assertIn(">传递</button>", edit_response.get_data(as_text=True))

        response = self.client.post(f"/expense-attachments/{self.attachment_id}/transfer")
        self.assertEqual(response.status_code, 302)
        rows = self.transferred_rows()
        self.assertEqual(len(rows), 1)
        copied_path = (
            Path(self.module.CUSTOMER_REIMBURSEMENT_DIR)
            / str(self.reimbursement_id)
            / "attachments"
            / rows[0]["stored_filename"]
        )
        self.assertEqual(copied_path.read_bytes(), b"test-image")

        edit_response = self.client.get(f"/expenses/{self.expense_id}/edit")
        self.assertIn(">已传递</button>", edit_response.get_data(as_text=True))

        response = self.client.post(f"/expense-attachments/{self.attachment_id}/transfer")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.transferred_rows()), 1)

    def test_transfer_rejects_locked_settlement(self):
        with self.module.app.app_context():
            self.module.db().execute(
                "update customer_reimbursements set status = 'submitted' where id = ?",
                (self.reimbursement_id,),
            )
            self.module.db().commit()

        response = self.client.post(f"/expense-attachments/{self.attachment_id}/transfer")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.transferred_rows(), [])

    def test_processing_detail_can_transfer_approved_expense_attachment(self):
        with self.module.app.app_context():
            self.module.db().execute(
                "update expenses set status = 'approved' where id = ?",
                (self.expense_id,),
            )
            self.module.db().commit()

        detail_response = self.client.get(f"/expenses/{self.expense_id}")
        detail_html = detail_response.get_data(as_text=True)
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn('class="inline-thumb"', detail_html)
        self.assertIn(">传递</button>", detail_html)

        response = self.client.post(
            f"/expense-attachments/{self.attachment_id}/transfer",
            data={"return_to": "detail"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], f"/expenses/{self.expense_id}")
        self.assertEqual(len(self.transferred_rows()), 1)

    def test_admin_can_reset_expense_without_approve_permission(self):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute(
                """
                update role_action_permissions
                set is_enabled = 0
                where role = 'admin' and resource_key = 'expenses' and action_key = 'approve'
                """
            )
            connection.execute(
                """
                update expenses
                set status = 'approved', payout_status = 'paid', reimbursed_by = ?, reimbursed_at = ?
                where id = ?
                """,
                (self.user_id, "2026-08-02T12:00:00", self.expense_id),
            )
            connection.commit()

        response = self.client.post(
            "/expense-processing/action",
            data={"expense_id": self.expense_id, "action": "reset_workflow"},
        )
        self.assertEqual(response.status_code, 302)
        with self.module.app.app_context():
            expense = self.module.db().execute(
                "select status, payout_status from expenses where id = ?", (self.expense_id,)
            ).fetchone()
        self.assertEqual(expense["status"], "submitted")
        self.assertEqual(expense["payout_status"], "pending")

    def test_admin_without_approve_permission_still_cannot_reimburse(self):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute(
                """
                update role_action_permissions
                set is_enabled = 0
                where role = 'admin' and resource_key = 'expenses' and action_key = 'approve'
                """
            )
            connection.execute(
                "update expenses set status = 'approved', payout_status = 'pending' where id = ?",
                (self.expense_id,),
            )
            connection.commit()

        response = self.client.post(
            "/expense-processing/action",
            data={"expense_id": self.expense_id, "action": "reimburse"},
        )
        self.assertEqual(response.status_code, 403)

    def test_line_attachment_is_rendered_with_its_expense_item(self):
        edit_html = self.client.get(f"/expenses/{self.expense_id}/edit").get_data(as_text=True)
        self.assertIn('name="item_line_key" value="line-travel"', edit_html)
        self.assertIn('name="item_attachments_line-travel"', edit_html)
        self.assertIn("travel-receipt.png", edit_html)
        self.assertIn("通用附件", edit_html)

        detail_html = self.client.get(f"/expenses/{self.expense_id}").get_data(as_text=True)
        self.assertIn("对应附件", detail_html)
        self.assertLess(detail_html.index("travel-receipt.png"), detail_html.index("通用附件"))

    def test_removing_expense_item_also_removes_its_specific_attachment(self):
        with self.module.app.app_context():
            attachment = self.module.db().execute(
                "select * from expense_attachments where id = ?", (self.line_attachment_id,)
            ).fetchone()
            path = Path(self.module.expense_attachment_path(attachment))
            self.assertTrue(path.exists())
            self.module.save_expense_items(self.expense_id, [])
            self.module.db().commit()
            self.assertIsNone(
                self.module.db().execute(
                    "select id from expense_attachments where id = ?", (self.line_attachment_id,)
                ).fetchone()
            )
            self.assertFalse(path.exists())
            self.assertIsNotNone(
                self.module.db().execute(
                    "select id from expense_attachments where id = ?", (self.attachment_id,)
                ).fetchone()
            )

    def test_uploaded_file_is_linked_to_its_expense_line(self):
        with self.module.app.test_request_context(
            "/expenses/upload",
            method="POST",
            data={"item_attachments_line-travel": (BytesIO(b"new-line-image"), "new-line.jpg")},
        ):
            with self.module.app.app_context():
                self.module.g.user = self.module.db().execute(
                    "select * from users where id = ?", (self.user_id,)
                ).fetchone()
                self.module.save_expense_uploads(
                    self.expense_id,
                    [{"line_key": "line-travel"}],
                )
                self.module.db().commit()
                attachment = self.module.db().execute(
                    """
                    select * from expense_attachments
                    where expense_id = ? and original_filename = 'new-line.jpg'
                    """,
                    (self.expense_id,),
                ).fetchone()
                self.assertEqual(attachment["expense_item_key"], "line-travel")
                self.assertEqual(
                    Path(self.module.expense_attachment_path(attachment)).read_bytes(),
                    b"new-line-image",
                )


if __name__ == "__main__":
    unittest.main()
