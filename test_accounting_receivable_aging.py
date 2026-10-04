import unittest
from datetime import date
from decimal import Decimal

from database import PostgreSQLConnection
from invoice_tool.accounting import (
    CustomerReceiptService,
    InvoiceRecognitionService,
    ReceiptAllocation,
    ReceivableAgingService,
)


class ReceivableAgingServiceTest(unittest.TestCase):
    def setUp(self):
        self.db = PostgreSQLConnection()
        self.db.execute(
            "insert into settings(key,value) values('accounting_base_enabled','1') "
            "on conflict(key) do update set value='1'"
        )
        self.db.executemany(
            "insert into accounting_periods(period_start,period_end,status) "
            "values(?,?,'open')",
            (("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28"),
             ("2026-03-01", "2026-03-31"), ("2026-04-01", "2026-04-30")),
        )
        self.db.executemany(
            "insert into accounts(account_code,account_name,account_type,normal_balance) "
            "values(?,?,?,?)",
            (("1000", "Bank", "asset", "debit"),
             ("1100", "Accounts Receivable", "asset", "debit"),
             ("2100", "Sales Tax Payable", "liability", "credit"),
             ("2200", "Customer Prepayments", "liability", "credit"),
             ("4000", "Revenue", "revenue", "credit")),
        )
        self.actor_id = self.db.execute(
            "insert into users(name,email,password_hash,role,created_at) "
            "values('Aging Tester','aging@example.invalid','x','admin',CURRENT_TIMESTAMP::text)"
        ).lastrowid
        self.customer_id = self.db.execute(
            "insert into clients(client_number,name,short_name,created_at) "
            "values('C-AGING','Aging Client','AGING',CURRENT_TIMESTAMP::text)"
        ).lastrowid
        self.project_id = self.db.execute(
            "insert into projects(name,created_at) values('Aging Project',CURRENT_TIMESTAMP::text)"
        ).lastrowid
        self.bank_id = self.db.execute(
            "insert into bank_accounts(account_name,bank_name,currency,is_active,created_by,"
            "created_at,updated_at) values('Aging Bank','Test','USD',1,?,"
            "CURRENT_TIMESTAMP::text,CURRENT_TIMESTAMP::text)", (self.actor_id,)
        ).lastrowid
        self.invoice_id = self._invoice("INV-AGING-1", "2026-01-01", "2026-01-31", "100")
        self.current_invoice_id = self._invoice(
            "INV-AGING-2", "2026-04-01", "2026-04-30", "50"
        )
        recognition = InvoiceRecognitionService(self.db)
        recognition.recognize(
            self.invoice_id, accounting_date=date(2026, 1, 1), actor_id=self.actor_id
        )
        recognition.recognize(
            self.current_invoice_id, accounting_date=date(2026, 4, 1), actor_id=self.actor_id
        )
        self.correction = recognition.correct(
            self.invoice_id, revenue_delta="20", sales_tax_delta="0",
            accounting_date=date(2026, 2, 1), reason_code="approved_change",
            actor_id=self.actor_id,
        )
        CustomerReceiptService(self.db).receive(
            receipt_no="RCT-AGING", customer_id=self.customer_id,
            receipt_date=date(2026, 3, 1), amount="30", bank_account_id=self.bank_id,
            allocations=(ReceiptAllocation(self.invoice_id, "30"),),
            actor_id=self.actor_id,
        )
        self.aging = ReceivableAgingService(self.db)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _invoice(self, number, issue_date, due_date, amount):
        invoice_id = self.db.execute(
            "insert into invoices(invoice_number,client_id,issue_date,due_date,currency,status,"
            "created_by,created_at) values(?,?,?,?,'USD','completed',?,CURRENT_TIMESTAMP::text)",
            (number, self.customer_id, issue_date, due_date, self.actor_id),
        ).lastrowid
        self.db.execute(
            "insert into invoice_items(invoice_id,project_id,description,amount,tax_rate) "
            "values(?,?, 'Aging service',?,0)",
            (invoice_id, self.project_id, amount),
        )
        return invoice_id

    def test_aging_uses_corrected_amount_less_settlements(self):
        report = self.aging.report(as_of="2026-04-15")
        rows = {row["invoice_number"]: row for row in report["rows"]}
        self.assertEqual(rows["INV-AGING-1"]["outstanding"], Decimal("90.00"))
        self.assertEqual(rows["INV-AGING-1"]["bucket"], "days_61_90")
        self.assertEqual(rows["INV-AGING-2"]["outstanding"], Decimal("50.00"))
        self.assertEqual(rows["INV-AGING-2"]["bucket"], "current")
        self.assertEqual(report["grand_total"], Decimal("140.00"))

    def test_customer_filter_and_historical_recognition_date(self):
        before_second_invoice = self.aging.report(
            as_of="2026-03-15", customer_id=self.customer_id
        )
        self.assertEqual([row["invoice_number"] for row in before_second_invoice["rows"]],
                         ["INV-AGING-1"])
        self.assertEqual(before_second_invoice["grand_total"], Decimal("90.00"))


if __name__ == "__main__":
    unittest.main()
