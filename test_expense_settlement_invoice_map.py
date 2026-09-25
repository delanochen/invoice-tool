"""员工报销项目 → 结算字段 → 发票项目 映射表：结算字段查询、发票按来源项目拆分。"""
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


class ExpenseSettlementInvoiceMapTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        source = Path(self.temp.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", source)
        spec = importlib.util.spec_from_file_location("expense_map_test", source)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.m.app.config.update(TESTING=True, SECRET_KEY="test")
        self.http = self.m.app.test_client()
        with self.m.app.app_context():
            db = self.m.db()
            self.invoice_projects = {}
            for name in ("MRO Supplies配件及耗材费", "Other", "Express Delivery Fees"):
                self.invoice_projects[name] = db.execute(
                    "insert into projects (name, name_key, project_type, unit_price, tax_rate, is_active, created_at)"
                    " values (?, ?, 'invoice', 0, 0, 1, ?)",
                    (name, self.m.project_name_key(name), self.m.now()),
                ).lastrowid
            db.commit()

    def query(self, sql, params=()):
        with self.m.app.app_context():
            return [dict(row) for row in self.m.db().execute(sql, params).fetchall()]

    def test_seed_rows_are_inserted(self):
        rows = self.query(
            "select expense_project_name, settlement_field, invoice_project_name from expense_settlement_invoice_map order by id"
        )
        self.assertEqual(len(rows), 10)
        by_name = {row["expense_project_name"]: row for row in rows}
        self.assertEqual(by_name["MRO Supplies配件及耗材费"]["settlement_field"], "other")
        self.assertEqual(by_name["MRO Supplies配件及耗材费"]["invoice_project_name"], "MRO Supplies配件及耗材费")
        self.assertEqual(by_name["Client Entertainment Expenses客户招待费"]["invoice_project_name"], "Other")
        self.assertEqual(by_name["Express Delivery Fees快递费"]["invoice_project_name"], "Express Delivery Fees")
        self.assertEqual(by_name["Accommodation/Lodging住宿费"]["settlement_field"], "lodging")

    def test_expense_field_lookup_reads_table(self):
        with self.m.app.app_context():
            self.assertEqual(self.m.customer_reimbursement_expense_field("MRO Supplies配件及耗材费"), "other")
            self.assertEqual(self.m.customer_reimbursement_expense_field("MRO Supplies 某某分部"), "other")
            self.assertEqual(self.m.customer_reimbursement_expense_field("Taxi Fare / Ride-Hailing Fare打车费"), "taxi")
            self.assertIsNone(self.m.customer_reimbursement_expense_field("Fuel Expenses燃油费"))
            self.assertEqual(self.m.customer_reimbursement_expense_field("Fuel Expenses燃油费", "rental"), "fuel")
            self.assertIsNone(self.m.customer_reimbursement_expense_field("完全不认识的项目"))

    def fake_items(self, sources):
        return [dict(auto_expense_sources=json.dumps({"other": sources}), other=len(sources))]

    def test_invoice_items_split_other_by_source_project(self):
        sources = [
            {"project_name": "MRO Supplies配件及耗材费", "amount": 30},
            {"project_name": "Client Entertainment Expenses客户招待费", "amount": 12},
            {"project_name": "Express Delivery Fees快递费", "amount": 8},
        ]
        reimbursement = {
            "id": 1, "labor_total": 100, "travel_total": 50,
            "mileage_total": 20, "mro_supplies_total": 50,
        }
        with patch.object(self.m, "customer_reimbursement_items", return_value=self.fake_items(sources)):
            with self.m.app.app_context():
                items = self.m.customer_reimbursement_invoice_items(reimbursement)
        by_project = {}
        invoice_names = {row["id"]: row["name"] for row in self.query("select id, name from projects where project_type = 'invoice'")}
        for item in items:
            by_project[invoice_names[item["project_id"]]] = item["amount"]
        self.assertEqual(by_project, {
            "Technical Services": 100,
            "Travel Expenses Reimbursement": 50,
            "Mileage Reimbursement": 20,
            "MRO Supplies配件及耗材费": 30,
            "Other": 12,
            "Express Delivery Fees": 8,
        })

    def test_invoice_items_without_entertainment_stay_unchanged(self):
        sources = [{"project_name": "MRO Supplies配件及耗材费", "amount": 30}]
        reimbursement = {
            "id": 1, "labor_total": 100, "travel_total": 50,
            "mileage_total": 20, "mro_supplies_total": 30,
        }
        with patch.object(self.m, "customer_reimbursement_items", return_value=self.fake_items(sources)):
            with self.m.app.app_context():
                items = self.m.customer_reimbursement_invoice_items(reimbursement)
        self.assertEqual(len(items), 4)
        mro = [item for item in items if item["project_id"] == self.invoice_projects["MRO Supplies配件及耗材费"]]
        self.assertEqual(mro[0]["amount"], 30)

    def test_invoice_items_legacy_fallback_without_sources(self):
        reimbursement = {
            "id": 1, "labor_total": 100, "travel_total": 50,
            "mileage_total": 20, "mro_supplies_total": 25,
        }
        with patch.object(self.m, "customer_reimbursement_items", return_value=self.fake_items([])):
            with self.m.app.app_context():
                items = self.m.customer_reimbursement_invoice_items(reimbursement)
        mro = [item for item in items if item["project_id"] == self.invoice_projects["MRO Supplies配件及耗材费"]]
        self.assertEqual(mro[0]["amount"], 25)

    def test_unmapped_other_source_is_rejected(self):
        sources = [{"project_name": "来路不明的项目", "amount": 99}]
        reimbursement = {
            "id": 1, "labor_total": 0, "travel_total": 0,
            "mileage_total": 0, "mro_supplies_total": 0,
        }
        with patch.object(self.m, "customer_reimbursement_items", return_value=self.fake_items(sources)):
            with self.m.app.app_context():
                with self.assertRaises(ValueError) as caught:
                    self.m.customer_reimbursement_invoice_items(reimbursement)
        self.assertIn("来路不明的项目", str(caught.exception))

    def test_other_cell_is_readonly_in_settlement_form(self):
        source = (ROOT / "templates" / "customer_reimbursement_form.html").read_text(encoding="utf-8")
        self.assertIn("readonly", source)
        self.assertIn("expense_amount_cell(item, 'other', readonly=True)", source)


if __name__ == "__main__":
    unittest.main()
