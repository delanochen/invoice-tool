"""报销删除：依赖行清理 + 失败时给友好提示（不再 500）。

背景：删除报销时只清了 `expense_attachments` / `expense_items`，但
`expense_save_tokens` 与 `expense_duplicate_checks`（含被别人 `matched_expense_id`
指到这条报销的记录）也外键引用 `expenses`。SQLite 的外键带 on delete cascade 所以本地
看不出问题，PostgreSQL 生产库不一定有，删到引用行就抛外键错误 —— 用户只看到 Internal
Server Error。现在：依赖行逐个显式删除，删除失败走 RuntimeError（全局处理器转成页面提示）。
"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


class ExpenseDeleteDependenciesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        source = Path(self.temp.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", source)
        spec = importlib.util.spec_from_file_location("expense_delete_test", source)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.m.app.config.update(TESTING=True, SECRET_KEY="test")
        self.m.app.template_folder = str(ROOT / "templates")
        self.http = self.m.app.test_client()
        with self.m.app.app_context():
            db = self.m.db()
            self.people = {}
            for name, role in [("Submitter", "employee"), ("Manager", "manager")]:
                self.people[name] = db.execute(
                    "insert into users (name,email,password_hash,role,is_active,created_at) values (?,?,?,?,1,?)",
                    (name, name.lower() + "@test.invalid", "unused", role, self.m.now()),
                ).lastrowid
            self.order = db.execute(
                """insert into service_orders (order_number,client_name,site_address,client_order_number,
                   start_date,created_by,created_at) values ('SO-DELETE','Site','Address','ORDER','2026-09-01',?,?)""",
                (self.people["Submitter"], self.m.now()),
            ).lastrowid
            db.commit()
        self.counter = 0
        self.login("Manager")

    # ------------------------------------------------------------------ 工具
    def login(self, name):
        with self.http.session_transaction() as session:
            session["user_id"] = self.people[name]

    def rows(self, sql, params=()):
        with self.m.app.app_context():
            return [dict(row) for row in self.m.db().execute(sql, params).fetchall()]

    def create_expense(self, status="draft"):
        self.counter += 1
        with self.m.app.app_context():
            db = self.m.db()
            expense_id = db.execute(
                """insert into expenses (service_order_id,expense_number,project,expense_date,amount,currency,
                   description,status,created_by,created_at,updated_at,beneficiary_id)
                   values (?,?,'Project','2026-09-01',10,'USD','delete me',?,?,?,?,?)""",
                (self.order, f"EX-DEL-{self.counter}", status, self.people["Submitter"], self.m.now(),
                 self.m.now(), self.people["Submitter"]),
            ).lastrowid
            db.commit()
        return expense_id

    def create_attachment(self, expense_id):
        with self.m.app.app_context():
            db = self.m.db()
            attachment_id = db.execute(
                """insert into expense_attachments (expense_id,original_filename,stored_filename,content_type,
                   uploaded_by,uploaded_at) values (?,'receipt.png','stored.png','image/png',?,?)""",
                (expense_id, self.people["Submitter"], self.m.now()),
            ).lastrowid
            db.commit()
        return attachment_id

    def create_check(self, expense_id, attachment_id, matched_expense_id=None, matched_attachment_id=None):
        with self.m.app.app_context():
            db = self.m.db()
            db.execute(
                """insert into expense_duplicate_checks (expense_id,attachment_id,matched_expense_id,
                   matched_attachment_id,risk_level,score,reasons,review_status,created_at,updated_at)
                   values (?,?,?,?,'medium',50,'same receipt','pending',?,?)""",
                (expense_id, attachment_id, matched_expense_id or expense_id,
                 matched_attachment_id or attachment_id, self.m.now(), self.m.now()),
            )
            db.commit()

    def create_save_token(self, expense_id):
        with self.m.app.app_context():
            db = self.m.db()
            db.execute(
                "insert into expense_save_tokens (token,expense_id,created_at) values ('tok-del',?,?)",
                (expense_id, self.m.now()),
            )
            db.commit()

    def flashes(self):
        with self.http.session_transaction() as session:
            return [text for _, text in session.get("_flashes", [])]

    # ------------------------------------------------------------------ 用例
    def test_delete_clears_tokens_and_duplicate_checks(self):
        expense_id = self.create_expense()
        attachment_id = self.create_attachment(expense_id)
        self.create_attachment(expense_id)
        self.create_check(expense_id, attachment_id)
        self.create_save_token(expense_id)

        response = self.http.post(f"/expenses/{expense_id}/delete")
        self.assertEqual(response.status_code, 302)

        self.assertEqual(self.rows("select id from expenses where id = ?", (expense_id,)), [])
        self.assertEqual(self.rows("select token from expense_save_tokens where expense_id = ?", (expense_id,)), [])
        self.assertEqual(
            self.rows(
                "select id from expense_duplicate_checks where expense_id = ? or matched_expense_id = ?",
                (expense_id, expense_id),
            ),
            [],
        )
        self.assertEqual(self.rows("select id from expense_attachments where expense_id = ?", (expense_id,)), [])

    def test_delete_clears_checks_that_reference_it_as_matched(self):
        """别的报销的检查记录把这条报销记为「重复项」时，也要一并清掉。"""
        expense_id = self.create_expense()
        other_id = self.create_expense()
        other_attachment = self.create_attachment(other_id)
        self.create_check(other_id, other_attachment, matched_expense_id=expense_id, matched_attachment_id=other_attachment)

        response = self.http.post(f"/expenses/{expense_id}/delete")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.rows(
                "select id from expense_duplicate_checks where matched_expense_id = ?", (expense_id,),
            ),
            [],
        )
        # 另一条报销本身不受影响
        self.assertNotEqual(self.rows("select id from expenses where id = ?", (other_id,)), [])

    def test_delete_failure_shows_friendly_message_and_keeps_data(self):
        expense_id = self.create_expense()
        with patch.object(self.m, "log_action", side_effect=RuntimeError("boom")):
            response = self.http.post(f"/expenses/{expense_id}/delete")

        self.assertNotEqual(response.status_code, 500)  # 不再是 Internal Server Error
        self.assertEqual(response.status_code, 302)
        self.assertTrue(any("删除报销失败" in text for text in self.flashes()), self.flashes())
        self.assertNotEqual(self.rows("select id from expenses where id = ?", (expense_id,)), [])

    def test_attachment_delete_clears_duplicate_checks(self):
        expense_id = self.create_expense()
        attachment_id = self.create_attachment(expense_id)
        self.create_check(expense_id, attachment_id)
        self.login("Submitter")  # 附件删除限本人或经理

        response = self.http.post(f"/expense-attachments/{attachment_id}/delete")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.rows(
                "select id from expense_duplicate_checks where attachment_id = ? or matched_attachment_id = ?",
                (attachment_id, attachment_id),
            ),
            [],
        )
        self.assertEqual(self.rows("select id from expense_attachments where id = ?", (attachment_id,)), [])


if __name__ == "__main__":
    unittest.main()
