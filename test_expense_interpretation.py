"""报销附件智能解读：结果落库/失败可重试/端点行为/模型配置映射。"""
import ai_interpretation
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


class ExpenseInterpretationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        source = Path(self.temp.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", source)
        spec = importlib.util.spec_from_file_location("expense_interpret_test", source)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.m.app.config.update(TESTING=True, SECRET_KEY="test")
        self.http = self.m.app.test_client()
        with self.m.app.app_context():
            db = self.m.db()
            self.user_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,created_at) values ('U','u@t.invalid','x','employee',1,?)",
                (self.m.now(),),
            ).lastrowid
            order_id = db.execute(
                "insert into service_orders (order_number,client_name,site_address,client_order_number,start_date,created_by,created_at) values ('SO-AI','C','S','O','2026-09-01',?,?)",
                (self.user_id, self.m.now()),
            ).lastrowid
            self.expense_id = db.execute(
                "insert into expenses (service_order_id,expense_number,project,expense_date,amount,currency,description,status,created_by,created_at,updated_at,beneficiary_id) values (?,?,?,?,?,?,?,?,?,?,?,?)",
                (order_id, "EX-AI", "P", "2026-09-01", 10, "USD", "d", "submitted", self.user_id, self.m.now(), self.m.now(), self.user_id),
            ).lastrowid
            self.attachment_id = db.execute(
                "insert into expense_attachments (expense_id,original_filename,stored_filename,content_type,uploaded_by,uploaded_at) values (?,?,?,?,?,?)",
                (self.expense_id, "receipt.png", "abc.png", "image/png", self.user_id, self.m.now()),
            ).lastrowid
            db.commit()
        self.attachment_path = os.path.join(
            self.m.EXPENSE_ATTACHMENTS_DIR, str(self.expense_id), "abc.png"
        )
        os.makedirs(os.path.dirname(self.attachment_path), exist_ok=True)
        with open(self.attachment_path, "wb") as handle:
            handle.write(b"fake-png")

    def query(self, sql, params=()):
        with self.m.app.app_context():
            return [dict(row) for row in self.m.db().execute(sql, params).fetchall()]

    def test_interpret_saves_result_and_pending_excludes_done(self):
        with patch.object(ai_interpretation, "call_chat_completion", return_value="解读要点文本"):
            with self.m.app.app_context():
                result = self.m.run_expense_attachment_interpretation(
                    self.m.db(), self.attachment_id, self.m.EXPENSE_ATTACHMENTS_DIR
                )
                self.m.db().commit()
        self.assertTrue(result["ok"])
        self.assertEqual(result["content"], "解读要点文本")
        rows = self.query("select * from expense_attachment_interpretations where attachment_id = ?", (self.attachment_id,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "done")
        with self.m.app.app_context():
            pending = ai_interpretation.pending_attachment_ids(self.m.db())
        self.assertNotIn(self.attachment_id, pending)

    def test_failure_is_recorded_and_attachment_stays_pending(self):
        with patch.object(
            ai_interpretation, "call_chat_completion",
            side_effect=RuntimeError("无法连接大模型接口"),
        ):
            with self.m.app.app_context():
                result = self.m.run_expense_attachment_interpretation(
                    self.m.db(), self.attachment_id, self.m.EXPENSE_ATTACHMENTS_DIR
                )
                self.m.db().commit()
        self.assertFalse(result["ok"])
        rows = self.query("select status, error from expense_attachment_interpretations where attachment_id = ?", (self.attachment_id,))
        self.assertEqual(rows[0]["status"], "failed")
        self.assertIn("无法连接", rows[0]["error"])
        with self.m.app.app_context():
            pending = ai_interpretation.pending_attachment_ids(self.m.db())
        self.assertIn(self.attachment_id, pending)

    def test_model_choice_mapping_uses_settings(self):
        with self.m.app.app_context():
            db = self.m.db()
            db.execute("insert into settings (key, value) values ('ai_interpret_model_choice', 'qwen9b')")
            db.execute("insert into settings (key, value) values ('ai_interpret_model_qwen9b', 'qwen3:8b')")
            db.commit()
            settings = ai_interpretation.effective_settings(self.m.db())
        self.assertEqual(settings["model"], "qwen3:8b")

    def test_endpoints_return_result(self):
        with self.http.session_transaction() as session:
            session["user_id"] = self.user_id
        response = self.http.get(f"/expense-attachments/{self.attachment_id}/interpretation")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "none")
        with patch.object(ai_interpretation, "call_chat_completion", return_value="模拟解读结果"):
            interpreted = self.http.post(f"/expense-attachments/{self.attachment_id}/interpret")
        self.assertEqual(interpreted.status_code, 200)
        self.assertTrue(interpreted.json["ok"])
        again = self.http.get(f"/expense-attachments/{self.attachment_id}/interpretation")
        self.assertEqual(again.json["status"], "done")


if __name__ == "__main__":
    unittest.main()
