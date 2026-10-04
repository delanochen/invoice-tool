import unittest
from datetime import date
from decimal import Decimal

from database import PostgreSQLConnection
from invoice_tool.accounting import (
    EventKey,
    FinancialReportService,
    PostingLine,
    PostingService,
)


class FinancialReportServiceTest(unittest.TestCase):
    def setUp(self):
        self.db = PostgreSQLConnection()
        self.db.execute(
            "insert into settings(key,value) values('accounting_base_enabled','1') "
            "on conflict(key) do update set value='1'"
        )
        self.db.execute(
            "insert into accounting_periods(period_start,period_end,status) "
            "values('2026-10-01','2026-10-31','open')"
        )
        self.db.executemany(
            "insert into accounts(account_code,account_name,account_type,normal_balance) "
            "values(?,?,?,?)",
            (("1000", "Bank", "asset", "debit"),
             ("2000", "Payable", "liability", "credit"),
             ("3000", "Equity", "equity", "credit"),
             ("4000", "Revenue", "revenue", "credit"),
             ("5000", "Expense", "expense", "debit")),
        )
        self.actor_id = self.db.execute(
            "insert into users(name,email,password_hash,role,created_at) "
            "values('Report Tester','financial-report@example.invalid','x','admin',"
            "CURRENT_TIMESTAMP::text)"
        ).lastrowid
        posting = PostingService(self.db)
        posting.post_event(
            key=EventKey("report_test", 1, 1, "revenue.recorded"),
            business_anchor_type="report_test", business_anchor_id=1,
            business_date=date(2026, 10, 5), accounting_date=date(2026, 10, 5),
            lines=(PostingLine("1000", debit="150"),
                   PostingLine("4000", credit="150")), actor_id=self.actor_id,
        )
        posting.post_event(
            key=EventKey("report_test", 2, 1, "expense.recorded"),
            business_anchor_type="report_test", business_anchor_id=2,
            business_date=date(2026, 10, 6), accounting_date=date(2026, 10, 6),
            lines=(PostingLine("5000", debit="40"),
                   PostingLine("1000", credit="40")), actor_id=self.actor_id,
        )
        self.reports = FinancialReportService(self.db)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_income_statement_uses_normal_balances(self):
        report = self.reports.income_statement(
            date_from="2026-10-01", date_to="2026-10-31"
        )
        self.assertEqual(report["revenue_total"], Decimal("150"))
        self.assertEqual(report["expense_total"], Decimal("40"))
        self.assertEqual(report["net_income"], Decimal("110"))

    def test_balance_sheet_includes_current_earnings_and_balances(self):
        report = self.reports.balance_sheet(as_of="2026-10-31")
        self.assertEqual(report["asset_total"], Decimal("110"))
        self.assertEqual(report["current_earnings"], Decimal("110"))
        self.assertEqual(report["right_total"], Decimal("110"))
        self.assertEqual(report["difference"], Decimal("0"))
        self.assertTrue(report["balanced"])

    def test_date_filter_excludes_later_expense(self):
        report = self.reports.income_statement(date_to="2026-10-05")
        self.assertEqual(report["revenue_total"], Decimal("150"))
        self.assertEqual(report["expense_total"], Decimal("0"))
        self.assertEqual(report["net_income"], Decimal("150"))


if __name__ == "__main__":
    unittest.main()
