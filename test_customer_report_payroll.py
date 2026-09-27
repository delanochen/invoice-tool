import unittest
from pathlib import Path

from customer_report_logic import normalize_customer_report_choice


REPO_DIR = Path(__file__).resolve().parent


class CustomerReportPayrollTest(unittest.TestCase):
    def test_customer_report_choice_controls_required_employee(self):
        self.gaoyang_id = 7
        self.assertEqual(normalize_customer_report_choice("no", str(self.gaoyang_id)), ("no", ""))
        with self.assertRaisesRegex(ValueError, "必须选择一位填写员工"):
            normalize_customer_report_choice("yes", "")
        self.assertEqual(
            normalize_customer_report_choice("yes", str(self.gaoyang_id)),
            ("yes", str(self.gaoyang_id)),
        )

    def test_form_contains_conditional_customer_report_controls(self):
        template = (REPO_DIR / "templates" / "service_report_form.html").read_text(encoding="utf-8")
        script = (REPO_DIR / "static" / "service-report.js").read_text(encoding="utf-8")
        self.assertIn('name="has_customer_report"', template)
        self.assertIn('name="report_writer_id" id="customerReportWriter"', template)
        self.assertIn("customerReportWriter.required = isRequired", script)
        self.assertIn("customerReportWriter.disabled = !isRequired", script)


if __name__ == "__main__":
    unittest.main()
