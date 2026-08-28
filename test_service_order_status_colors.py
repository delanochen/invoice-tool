import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_DIR = Path(__file__).resolve().parent


class ServiceOrderStatusColorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_status_color_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_list_colors_are_not_gated_by_invoice_view_permission(self):
        source = (REPO_DIR / "templates" / "service_orders.html").read_text(encoding="utf-8")
        color_rule = source.split("<tr{% if", 1)[1].split(">", 1)[0]
        self.assertNotIn("can_view_invoices()", color_rule)
        self.assertIn('class="closed-paid"', color_rule)
        self.assertIn('class="closed-invoiced"', color_rule)
        self.assertIn('class="open-invoiced"', color_rule)
        self.assertIn("{% if can_view_invoices() %}<th>发票</th>", source)

    def test_calendar_colors_are_available_without_invoice_view_permission(self):
        rows = [
            self.calendar_row(1, "closed", 2, 2),
            self.calendar_row(2, "closed", 2, 0),
            self.calendar_row(3, "active", 1, 0),
        ]
        with self.module.app.test_request_context("/service-orders/calendar?year=2026&month=8"):
            with mock.patch.object(self.module, "service_order_calendar_rows", return_value=rows):
                with mock.patch.object(self.module, "can_view_invoices", return_value=False):
                    weeks = self.module.service_order_calendar_weeks(2026, 8)

        states = {
            event["order_id"]: event["billing_state"]
            for week in weeks
            for day in week
            for event in day["events"]
        }
        self.assertEqual(
            states,
            {1: "closed-paid", 2: "closed-invoiced", 3: "open-invoiced"},
        )

    @staticmethod
    def calendar_row(order_id, status, invoice_count, paid_invoice_count):
        return {
            "id": order_id,
            "order_number": f"SO{order_id:06d}",
            "client_order_number": f"SHPG202608{order_id:06d}",
            "customer_name": "Sanhe Tongfei Refrigeration Co., Ltd.",
            "client_name": f"Site {order_id}",
            "buyer_owner": "Owner",
            "status": status,
            "invoice_count": invoice_count,
            "paid_invoice_count": paid_invoice_count,
            "work_order_type_name": "Service",
            "start_date": "2026-08-10",
            "calendar_date": "2026-08-10",
            "has_report": 1,
        }


if __name__ == "__main__":
    unittest.main()
