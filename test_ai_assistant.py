import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_DIR = Path(__file__).resolve().parent


class AiAssistantTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_ai_test_app", module_path)
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
            connection.execute("delete from expenses")
            connection.execute("delete from service_orders")
            connection.execute("delete from users")
            first = connection.execute(
                "insert into users (name, email, password_hash, role, created_at) values (?, ?, 'unused', 'employee', ?)",
                ("员工甲", "a@example.com", "2026-08-15T08:00:00"),
            )
            self.user_id = first.lastrowid
            second = connection.execute(
                "insert into users (name, email, password_hash, role, created_at) values (?, ?, 'unused', 'employee', ?)",
                ("员工乙", "b@example.com", "2026-08-15T08:00:00"),
            )
            self.other_user_id = second.lastrowid
            order = connection.execute(
                """
                insert into service_orders (
                    order_number, client_order_number, client_name, site_address, status, created_by, created_at
                ) values ('SO-AI-001', 'SHPG202608150001', '测试客户', '100 Test Rd', 'open', ?, '2026-08-15T08:00:00')
                """,
                (self.user_id,),
            )
            self.order_id = order.lastrowid
            connection.execute(
                """
                insert into expenses (expense_number, service_order_id, project, expense_date, amount,
                                      currency, status, payout_status, created_by, created_at, updated_at)
                values ('EX-AI-OWN', ?, 'Fuel Expenses', '2026-08-14', 20, 'USD', 'approved', 'pending', ?, ?, ?)
                """,
                (self.order_id, self.user_id, "2026-08-15T08:00:00", "2026-08-15T08:00:00"),
            )
            connection.execute(
                """
                insert into expenses (expense_number, service_order_id, project, expense_date, amount,
                                      currency, status, payout_status, created_by, created_at, updated_at)
                values ('EX-AI-OTHER', ?, 'Fuel Expenses', '2026-08-14', 30, 'USD', 'approved', 'pending', ?, ?, ?)
                """,
                (self.order_id, self.other_user_id, "2026-08-15T08:00:00", "2026-08-15T08:00:00"),
            )
            self.module.set_setting("deepseek_enabled", "false")
            self.module.set_setting("deepseek_api_key", "")
            connection.commit()
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id

    def test_page_is_available_but_api_requires_configuration(self):
        page = self.client.get("/ai-assistant")
        self.assertEqual(page.status_code, 200)
        self.assertIn("只读", page.get_data(as_text=True))
        response = self.client.post("/api/ai-assistant/chat", json={"messages": [{"role": "user", "content": "我的报销"}]})
        self.assertEqual(response.status_code, 503)

    def test_employee_expense_search_only_returns_own_records(self):
        with self.module.app.test_request_context("/ai-assistant"):
            self.module.g.user = self.module.db().execute("select * from users where id = ?", (self.user_id,)).fetchone()
            result = self.module.ai_search_business_records({"domain": "expenses", "status": "待报销"})
        self.assertEqual(result["summary"]["count"], 1)
        self.assertEqual(result["records"][0]["expense_number"], "EX-AI-OWN")

    def test_chat_executes_only_whitelisted_read_tool(self):
        with self.module.app.app_context():
            self.module.set_setting("deepseek_enabled", "true")
            self.module.set_setting("deepseek_api_key", "test-key")
            self.module.db().commit()
        tool_reply = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "search_business_records", "arguments": '{"domain":"expenses","status":"待报销"}'},
            }],
        }
        final_reply = {"role": "assistant", "content": "你有 1 条待报销记录。"}
        with patch.object(self.module, "call_deepseek_chat", side_effect=[tool_reply, final_reply]) as mocked:
            response = self.client.post(
                "/api/ai-assistant/chat",
                json={"messages": [{"role": "user", "content": "我有多少报销没有付款？"}]},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("1 条", response.get_json()["answer"])
        second_messages = mocked.call_args_list[1].args[0]
        tool_messages = [message for message in second_messages if message.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertIn("EX-AI-OWN", tool_messages[0]["content"])
        self.assertNotIn("EX-AI-OTHER", tool_messages[0]["content"])

    def test_deepseek_request_uses_only_official_request_fields(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"role":"assistant","content":"OK"}}]}'

        settings = {"model": "deepseek-v4-flash", "api_key": "test-key"}
        with self.module.app.test_request_context("/"), patch.object(
            self.module, "urlopen", return_value=FakeResponse()
        ) as mocked:
            result = self.module.call_deepseek_chat(
                [{"role": "user", "content": "test"}], settings, include_tools=False, max_tokens=32
            )
        self.assertEqual(result["content"], "OK")
        request_payload = json.loads(mocked.call_args.args[0].data.decode("utf-8"))
        self.assertNotIn("user", request_payload)
        self.assertNotIn("tools", request_payload)
        self.assertEqual(request_payload["max_tokens"], 32)

    def test_connection_test_returns_readable_api_error(self):
        with self.module.app.app_context():
            self.module.set_setting("deepseek_api_key", "test-key")
            self.module.db().execute("update users set role = 'admin' where id = ?", (self.user_id,))
            self.module.db().commit()
        with patch.object(self.module, "call_deepseek_chat", side_effect=RuntimeError("DeepSeek 账户余额不足")):
            response = self.client.post("/api/settings/deepseek-test")
        self.assertEqual(response.status_code, 422)
        self.assertIn("余额不足", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
