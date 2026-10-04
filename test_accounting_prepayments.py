import unittest
from datetime import date
from decimal import Decimal

from database import PostgreSQLConnection
from invoice_tool.accounting import (
    CustomerPrepaymentService,
    CustomerReceiptService,
    PrepaymentApplicationError,
)


class CustomerPrepaymentServiceTest(unittest.TestCase):
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
             ("1100", "Accounts Receivable", "asset", "debit"),
             ("2200", "Customer Prepayments", "liability", "credit")),
        )
        self.actor_id = self.db.execute(
            "insert into users(name,email,password_hash,role,created_at) "
            "values('Prepayment Tester','prepayment-test@example.invalid','x','admin',"
            "CURRENT_TIMESTAMP::text)"
        ).lastrowid
        self.customer_id = self._customer("C-PP-1", "Prepayment Client")
        self.other_customer_id = self._customer("C-PP-2", "Other Client")
        self.project_id = self.db.execute(
            "insert into projects(name,created_at) values('Prepayment Project',CURRENT_TIMESTAMP::text)"
        ).lastrowid
        self.bank_id = self.db.execute(
            "insert into bank_accounts(account_name,bank_name,currency,is_active,created_by,"
            "created_at,updated_at) values('Prepayment Test Bank','Test Bank','USD',1,?,"
            "CURRENT_TIMESTAMP::text,CURRENT_TIMESTAMP::text)", (self.actor_id,)
        ).lastrowid
        self.invoice_id = self._invoice(self.customer_id, "INV-PP-1")
        self.other_invoice_id = self._invoice(self.other_customer_id, "INV-PP-2")
        self.receipts = CustomerReceiptService(self.db)
        self.service = CustomerPrepaymentService(self.db)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _customer(self, number, name):
        return self.db.execute(
            "insert into clients(client_number,name,short_name,created_at) "
            "values(?,?,?,CURRENT_TIMESTAMP::text)", (number, name, number),
        ).lastrowid

    def _invoice(self, customer_id, number):
        invoice_id = self.db.execute(
            "insert into invoices(invoice_number,client_id,issue_date,due_date,currency,status,"
            "created_by,created_at) values(?,?,'2026-10-01','2026-10-31','USD',"
            "'completed',?,CURRENT_TIMESTAMP::text)",
            (number, customer_id, self.actor_id),
        ).lastrowid
        self.db.execute(
            "insert into invoice_items(invoice_id,project_id,description,amount,tax_rate) "
            "values(?,?,?,'1000','0')", (invoice_id, self.project_id, "Receivable"),
        )
        return invoice_id

    def _prepayment(self, amount="600.00", receipt_no="PREPAY-1"):
        self.receipts.receive(
            receipt_no=receipt_no, customer_id=self.customer_id,
            receipt_date=date(2026, 10, 2), amount=amount,
            bank_account_id=self.bank_id, prepayment_reason="advance deposit",
            actor_id=self.actor_id,
        )
        return self.db.execute(
            "select id from customer_prepayments where receipt_id=("
            "select id from customer_receipts where receipt_no=?)", (receipt_no,),
        ).fetchone()[0]

    def apply(self, prepayment_id, amount, invoice_id=None):
        return self.service.apply(
            prepayment_id=prepayment_id, invoice_id=invoice_id or self.invoice_id,
            amount=amount, accounting_date=date(2026, 10, 6), actor_id=self.actor_id,
        )

    def test_apply_prepayment_reduces_liability_and_receivable_without_revenue(self):
        prepayment_id = self._prepayment()
        result = self.apply(prepayment_id, "600.00")
        entries = self.db.execute(
            "select a.account_code,e.debit,e.credit from voucher_entries e "
            "join accounts a on a.id=e.account_id where e.voucher_id=? order by e.line_no",
            (result.voucher_id,),
        ).fetchall()
        self.assertEqual(
            [(row[0], Decimal(str(row[1])), Decimal(str(row[2]))) for row in entries],
            [("2200", Decimal("600"), Decimal("0")),
             ("1100", Decimal("0"), Decimal("600"))],
        )
        self.assertEqual(
            self.db.execute("select status from customer_prepayments where id=?",
                            (prepayment_id,)).fetchone()[0], "applied"
        )
        self.assertEqual(
            self.db.execute("select count(*) from voucher_settlement_lines where voucher_id=?",
                            (result.voucher_id,)).fetchone()[0], 2
        )

    def test_partial_applications_receive_monotonic_occurrence_numbers(self):
        prepayment_id = self._prepayment()
        first = self.apply(prepayment_id, "200.00")
        second = self.apply(prepayment_id, "300.00")
        self.assertEqual((first.occurrence_no, second.occurrence_no), (1, 2))
        self.assertEqual(
            self.db.execute("select status from customer_prepayments where id=?",
                            (prepayment_id,)).fetchone()[0], "open"
        )

    def test_application_over_prepayment_balance_rolls_back(self):
        prepayment_id = self._prepayment()
        self.apply(prepayment_id, "500.00")
        before = self.db.execute("select count(*) from vouchers").fetchone()[0]
        with self.assertRaises(PrepaymentApplicationError):
            self.apply(prepayment_id, "200.00")
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], before)
        self.assertEqual(
            self.db.execute("select count(*) from customer_prepayment_applications").fetchone()[0], 1
        )

    def test_application_cannot_cross_customers(self):
        prepayment_id = self._prepayment()
        with self.assertRaises(PrepaymentApplicationError):
            self.apply(prepayment_id, "100.00", self.other_invoice_id)
        self.assertEqual(
            self.db.execute("select count(*) from customer_prepayment_applications").fetchone()[0], 0
        )


if __name__ == "__main__":
    unittest.main()
