"""Expense AI review tests: conclusion parsing / record reuse / failure retry / permissions."""
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_interpretation
import ai_review

ROOT = Path(__file__).resolve().parent


class ExpenseAiReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        source = Path(self.temp.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", source)
        spec = importlib.util.spec_from_file_location("expense_ai_review_test", source)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.m.app.config.update(TESTING=True, SECRET_KEY="test")
        self.http = self.m.app.test_client()
        with self.m.app.app_context():
            db = self.m.db()
            self.admin_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,created_at) values ('M','m@t.invalid','x','manager',1,?)",
                (self.m.now(),),
            ).lastrowid
            self.employee_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,created_at) values ('E','e@t.invalid','x','employee',1,?)",
                (self.m.now(),),
            ).lastrowid
            order_id = db.execute(
                "insert into service_orders (order_number,client_name,site_address,client_order_number,start_date,created_by,created_at)"
                " values ('SO-AIR','C','S','O','2026-09-01',?,?)",
                (self.admin_id, self.m.now()),
            ).lastrowid
            self.expense_id = db.execute(
                "insert into expenses (service_order_id,expense_number,project,expense_date,amount,currency,description,status,created_by,created_at,updated_at,beneficiary_id)"
                " values (?,?,?,?,?,?,?,?,?,?,?,?)",
                (order_id, "EX-AIR", "P", "2026-09-02", 100, "USD", "d", "submitted", self.employee_id, self.m.now(), self.m.now(), self.employee_id),
            ).lastrowid
            project_id = db.execute(
                "insert into projects (name, name_key, project_type, is_active, created_at)"
                " values ('MRO Supplies配件及耗材费', 'mro supplies配件及耗材费', 'expense', 1, ?)",
                (self.m.now(),),
            ).lastrowid
            db.execute(
                "insert into expense_items (expense_id,line_key,project_id,project,amount,sort_order)"
                " values (?, 'line-1', ?, 'MRO Supplies配件及耗材费', 100, 0)",
                (self.expense_id, project_id),
            )
            attachment_id = db.execute(
                "insert into expense_attachments (expense_id,expense_item_key,original_filename,stored_filename,content_type,uploaded_by,uploaded_at)"
                " values (?, 'line-1', 'receipt.png', 'r.png', 'image/png', ?, ?)",
                (self.expense_id, self.admin_id, self.m.now()),
            ).lastrowid
            path = os.path.join(self.m.EXPENSE_ATTACHMENTS_DIR, str(self.expense_id), "r.png")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(b"png")
            db.commit()
        self.attachment_id = attachment_id

    def query(self, sql, params=()):
        with self.m.app.app_context():
            return [dict(row) for row in self.m.db().execute(sql, params).fetchall()]

    def login(self, user_id):
        with self.http.session_transaction() as session:
            session["user_id"] = user_id

    REVIEW_TEXT = (
        "结论：存疑\n"
        "金额核对：明细 100 与附件 90 不一致，差额 10。\n"
        "项目核对：未见明显异常。\n"
        "重复检查：无命中。\n"
        "整体判断：建议人工确认差额。"
    )

    def test_review_generates_done_record_with_conclusion(self):
        self.login(self.admin_id)
        captured = []

        def fake_call(settings, messages, timeout=300):
            captured.append(messages)
            return self.REVIEW_TEXT

        with patch.object(ai_interpretation, "call_chat_completion", side_effect=fake_call), \
             patch.object(ai_review, "call_chat_completion", side_effect=fake_call):
            response = self.http.post(f"/expenses/{self.expense_id}/ai_review")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["status"], "done")
        self.assertEqual(response.json["conclusion"], "存疑")
        self.assertIn("差额 10", response.json["content"])
        rows = self.query("select * from expense_ai_reviews where expense_id = ?", (self.expense_id,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "done")
        sent = captured[-1][1]["content"]
        self.assertIn("MRO Supplies", sent)
        self.assertIn("receipt.png", sent)

    def test_done_record_is_reused_unless_forced(self):
        self.login(self.admin_id)
        with patch.object(ai_interpretation, "call_chat_completion", return_value=self.REVIEW_TEXT), \
             patch.object(ai_review, "call_chat_completion", return_value=self.REVIEW_TEXT):
            self.http.post(f"/expenses/{self.expense_id}/ai_review")
        calls = {"n": 0}

        def counting_call(settings, messages, timeout=300):
            calls["n"] += 1
            return self.REVIEW_TEXT

        with patch.object(ai_interpretation, "call_chat_completion", side_effect=counting_call), \
             patch.object(ai_review, "call_chat_completion", side_effect=counting_call):
            again = self.http.get(f"/expenses/{self.expense_id}/ai_review")
            self.assertEqual(again.json["status"], "done")
            self.assertEqual(calls["n"], 0)  # 已 done 直接复用
            forced = self.http.post(f"/expenses/{self.expense_id}/ai_review")
            self.assertEqual(calls["n"], 1)  # POST 强制重跑
            self.assertEqual(forced.json["status"], "done")

    def test_model_failure_records_failed_and_page_still_renders(self):
        self.login(self.admin_id)
        with patch.object(ai_interpretation, "call_chat_completion", side_effect=RuntimeError("无法连接大模型接口")), \
             patch.object(ai_review, "call_chat_completion", side_effect=RuntimeError("无法连接大模型接口")):
            response = self.http.post(f"/expenses/{self.expense_id}/ai_review")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["status"], "failed")
        rows = self.query("select status, error from expense_ai_reviews where expense_id = ?", (self.expense_id,))
        self.assertEqual(rows[0]["status"], "failed")
        self.assertIn("无法连接", rows[0]["error"])
        # 页面仍可渲染（AI 意见不阻塞人工审核）
        page = self.http.get(f"/expenses/{self.expense_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("AI 审核失败", page.get_data(as_text=True))

    def test_employee_is_forbidden(self):
        self.login(self.employee_id)
        response = self.http.get(f"/expenses/{self.expense_id}/ai_review")
        self.assertEqual(response.status_code, 403)

    def test_duplicate_check_findings_are_included_in_prompt_input(self):
        with self.m.app.app_context():
            db = self.m.db()
            db.execute(
                "insert into expense_duplicate_checks (expense_id, attachment_id, matched_expense_id, matched_attachment_id,"
                " risk_level, score, reasons, deepseek_analysis, review_status, created_at, updated_at)"
                " values (?, ?, ?, ?, 'high', 100, 'same sha256', 'same sha256', 'pending', ?, ?)",
                (self.expense_id, self.attachment_id, self.expense_id, self.attachment_id, self.m.now(), self.m.now()),
            )
            db.commit()
        self.login(self.admin_id)
        captured = []

        def fake_call(settings, messages, timeout=300):
            captured.append(messages)
            return self.REVIEW_TEXT

        with patch.object(ai_interpretation, "call_chat_completion", side_effect=fake_call), \
             patch.object(ai_review, "call_chat_completion", side_effect=fake_call):
            self.http.post(f"/expenses/{self.expense_id}/ai_review")
        sent = captured[-1][1]["content"]
        self.assertIn("high", sent)
        self.assertIn("same sha256", sent)


    def test_worker_pending_selection_and_settings(self):
        import ai_review_worker

        with self.m.app.app_context():
            db = self.m.db()
            # 再造两张 submitted（其中一张已有 done）与一张 draft
            done_expense = db.execute(
                "insert into expenses (service_order_id,expense_number,project,expense_date,amount,currency,description,status,created_by,created_at,updated_at,beneficiary_id)"
                " values (?, 'EX-DONE', 'P', '2026-09-02', 1, 'USD', '', 'submitted', ?, ?, ?, ?)",
                (self.m.db().execute("select service_order_id from expenses where id = ?", (self.expense_id,)).fetchone()[0],
                 self.employee_id, self.m.now(), self.m.now(), self.employee_id),
            ).lastrowid
            draft_expense = db.execute(
                "insert into expenses (service_order_id,expense_number,project,expense_date,amount,currency,description,status,created_by,created_at,updated_at,beneficiary_id)"
                " values (?, 'EX-DRAFT', 'P', '2026-09-02', 1, 'USD', '', 'submitted', ?, ?, ?, ?)",
                (self.m.db().execute("select service_order_id from expenses where id = ?", (self.expense_id,)).fetchone()[0],
                 self.employee_id, self.m.now(), self.m.now(), self.employee_id),
            ).lastrowid
            db.execute(
                "insert into expense_ai_reviews (expense_id, status, created_at, updated_at) values (?, 'done', ?, ?)",
                (done_expense, self.m.now(), self.m.now()),
            )
            db.execute(
                "insert into expense_ai_reviews (expense_id, status, error, created_at, updated_at) values (?, 'failed', 'x', ?, ?)",
                (self.expense_id, self.m.now(), self.m.now()),
            )
            db.commit()
            pending = ai_review.pending_review_expense_ids(db)
            self.assertIn(draft_expense, pending)
            self.assertNotIn(done_expense, pending)
            self.assertNotIn(self.expense_id, pending)  # failed 不自动重试
            # 时间解析：默认 03:00；合法值生效；非法值回退默认；开关默认启用
            self.assertEqual(ai_review_worker.run_time(db), (3, 0))
            db.execute("insert into settings (key, value) values ('ai_review_time', '07:30')")
            db.commit()
            self.assertEqual(ai_review_worker.run_time(db), (7, 30))
            db.execute("update settings set value = 'bad' where key = 'ai_review_time'")
            db.commit()
            self.assertEqual(ai_review_worker.run_time(db), (3, 0))
            self.assertTrue(ai_review_worker.enabled(db))
            db.execute("insert into settings (key, value) values ('ai_review_enabled', 'false')")
            db.commit()
            self.assertFalse(ai_review_worker.enabled(db))


if __name__ == "__main__":
    unittest.main()
