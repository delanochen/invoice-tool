import unittest
from datetime import date
from decimal import Decimal

from database import PostgreSQLConnection
from invoice_tool.accounting import InvoiceRecognitionService, SalesTaxReportService


class SalesTaxReportServiceTest(unittest.TestCase):
    def setUp(self):
        self.db = PostgreSQLConnection()
        self.db.execute(
            "insert into settings(key,value) values('accounting_base_enabled','1') "
            "on conflict(key) do update set value='1'"
        )
        self.db.executemany(
            "insert into accounting_periods(period_start,period_end,status) values(?,?,'open')",
            (("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28"),
             ("2026-03-01", "2026-03-31")),
        )
        self.db.executemany(
            "insert into accounts(account_code,account_name,account_type,normal_balance) "
            "values(?,?,?,?)",
            (("1100", "Accounts Receivable", "asset", "debit"),
             ("2100", "Sales Tax Payable", "liability", "credit"),
             ("4000", "Revenue", "revenue", "credit")),
        )
        self.actor_id = self.db.execute(
            "insert into users(name,email,password_hash,role,created_at) "
            "values('Sales Tax Tester','sales-tax@example.invalid','x','admin',"
            "CURRENT_TIMESTAMP::text)"
        ).lastrowid
        customer_id = self.db.execute(
            "insert into clients(client_number,name,short_name,created_at) "
            "values('C-TAX','Tax Client','TAX',CURRENT_TIMESTAMP::text)"
        ).lastrowid
        project_id = self.db.execute(
            "insert into projects(name,created_at) values('Tax Project',CURRENT_TIMESTAMP::text)"
        ).lastrowid
        invoice_id = self.db.execute(
            "insert into invoices(invoice_number,client_id,issue_date,due_date,currency,status,"
            "created_by,created_at) values('INV-TAX',?,'2026-01-10','2026-02-10','USD',"
            "'completed',?,CURRENT_TIMESTAMP::text)", (customer_id, self.actor_id),
        ).lastrowid
        self.db.execute(
            "insert into invoice_items(invoice_id,project_id,description,amount,tax_rate) "
            "values(?,?, 'Taxable service',100,7.25)", (invoice_id, project_id),
        )
        service = InvoiceRecognitionService(self.db)
        service.recognize(
            invoice_id, accounting_date=date(2026, 1, 10), actor_id=self.actor_id
        )
        correction = service.correct(
            invoice_id, revenue_delta="0", sales_tax_delta="2.00",
            accounting_date=date(2026, 2, 5), reason_code="tax_adjustment",
            actor_id=self.actor_id,
        )
        service.reverse_correction(
            correction.correction_id, accounting_date=date(2026, 3, 5),
            reason_code="tax_adjustment_rejected", actor_id=self.actor_id,
        )
        self.reports = SalesTaxReportService(self.db)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_roll_forward_shows_accrual_correction_and_reversal(self):
        report = self.reports.report()
        self.assertEqual(report["increases"], Decimal("9.25"))
        self.assertEqual(report["decreases"], Decimal("2.00"))
        self.assertEqual(report["closing_balance"], Decimal("7.25"))
        self.assertEqual(
            [row["event_type"] for row in report["rows"]],
            ["invoice.confirmed", "invoice.corrected", "invoice.correction_reversed"],
        )

    def test_period_roll_forward_carries_opening_balance(self):
        february = self.reports.report(date_from="2026-02-01", date_to="2026-02-28")
        self.assertEqual(february["opening_balance"], Decimal("7.25"))
        self.assertEqual(february["increases"], Decimal("2.00"))
        self.assertEqual(february["closing_balance"], Decimal("9.25"))
        march = self.reports.report(date_from="2026-03-01", date_to="2026-03-31")
        self.assertEqual(march["opening_balance"], Decimal("9.25"))
        self.assertEqual(march["decreases"], Decimal("2.00"))
        self.assertEqual(march["closing_balance"], Decimal("7.25"))


if __name__ == "__main__":
    unittest.main()
