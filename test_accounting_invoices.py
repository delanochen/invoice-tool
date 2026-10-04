import unittest
from datetime import date
from decimal import Decimal

from database import IntegrityError, PostgreSQLConnection
from invoice_tool.accounting import (
    CustomerReceiptService,
    InvoiceRecognitionError,
    InvoiceRecognitionService,
    InvoiceVoidError,
    ReceiptAllocation,
)


class InvoiceRecognitionServiceTest(unittest.TestCase):
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
             ("2100", "Sales Tax Payable", "liability", "credit"),
             ("4000", "Revenue", "revenue", "credit")),
        )
        self.actor_id = self.db.execute(
            "insert into users(name,email,password_hash,role,created_at) "
            "values('Invoice Accounting Tester','invoice-accounting@example.invalid','x',"
            "'admin',CURRENT_TIMESTAMP::text)"
        ).lastrowid
        self.customer_id = self.db.execute(
            "insert into clients(client_number,name,short_name,created_at) "
            "values('C-INV-ACCT','Accounting Client','ACCT',CURRENT_TIMESTAMP::text)"
        ).lastrowid
        self.project_id = self.db.execute(
            "insert into projects(name,created_at) values('Accounting Project',"
            "CURRENT_TIMESTAMP::text)"
        ).lastrowid
        self.bank_id = self.db.execute(
            "insert into bank_accounts(account_name,bank_name,currency,is_active,created_by,"
            "created_at,updated_at) values('Invoice Accounting Bank','Test Bank','USD',1,?,"
            "CURRENT_TIMESTAMP::text,CURRENT_TIMESTAMP::text)", (self.actor_id,)
        ).lastrowid
        self.invoice_id = self.db.execute(
            "insert into invoices(invoice_number,client_id,issue_date,due_date,currency,status,"
            "created_by,created_at) values('INV-ACCT',?,'2026-10-03','2026-10-31',"
            "'USD','completed',?,CURRENT_TIMESTAMP::text)",
            (self.customer_id, self.actor_id),
        ).lastrowid
        self.service = InvoiceRecognitionService(self.db)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def add_item(self, amount, tax_rate="0", description="Service"):
        return self.db.execute(
            "insert into invoice_items(invoice_id,project_id,description,amount,tax_rate) "
            "values(?,?,?,?,?)",
            (self.invoice_id, self.project_id, description, amount, tax_rate),
        ).lastrowid

    def recognize(self, **overrides):
        values = {"source_version": 1, "accounting_date": date(2026, 10, 3),
                  "actor_id": self.actor_id}
        values.update(overrides)
        return self.service.recognize(self.invoice_id, **values)

    def test_recognizes_receivable_revenue_and_sales_tax(self):
        self.add_item("100.00", "7.25")
        result = self.recognize()
        self.assertEqual(
            (result.receivable, result.revenue, result.sales_tax),
            (Decimal("107.25"), Decimal("100.00"), Decimal("7.25")),
        )
        entries = self.db.execute(
            "select a.account_code,e.debit,e.credit from voucher_entries e "
            "join accounts a on a.id=e.account_id where e.voucher_id=? order by e.line_no",
            (result.voucher_id,),
        ).fetchall()
        self.assertEqual(
            [(row[0], Decimal(str(row[1])), Decimal(str(row[2]))) for row in entries],
            [("1100", Decimal("107.25"), Decimal("0")),
             ("4000", Decimal("0"), Decimal("100")),
             ("2100", Decimal("0"), Decimal("7.25"))],
        )

    def test_tax_is_rounded_per_source_line_then_summed(self):
        self.add_item("0.05", "10", "Line one")
        self.add_item("0.05", "10", "Line two")
        result = self.recognize()
        self.assertEqual(result.sales_tax, Decimal("0.02"))
        self.assertEqual(result.receivable, Decimal("0.12"))

    def test_same_snapshot_is_idempotent(self):
        self.add_item("100.00")
        first = self.recognize()
        second = self.recognize()
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(second.voucher_id, first.voucher_id)
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 1)

    def test_posted_invoice_source_line_cannot_be_mutated(self):
        item_id = self.add_item("100.00")
        self.recognize()
        self.db.execute("SAVEPOINT invoice_item_guard_test")
        with self.assertRaises(IntegrityError):
            self.db.execute("update invoice_items set amount='101.00' where id=?", (item_id,))
        self.db.execute("ROLLBACK TO SAVEPOINT invoice_item_guard_test")
        self.db.execute("RELEASE SAVEPOINT invoice_item_guard_test")
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 1)

    def test_posted_invoice_cannot_be_deleted_or_returned_to_draft(self):
        self.add_item("100.00")
        self.recognize()
        self.db.execute("SAVEPOINT invoice_guard_test")
        with self.assertRaises(IntegrityError):
            self.db.execute("delete from invoices where id=?", (self.invoice_id,))
        self.db.execute("ROLLBACK TO SAVEPOINT invoice_guard_test")
        self.db.execute("RELEASE SAVEPOINT invoice_guard_test")
        self.db.execute("SAVEPOINT invoice_status_guard_test")
        with self.assertRaises(IntegrityError):
            self.db.execute(
                "update invoices set status='draft' where id=?", (self.invoice_id,)
            )
        self.db.execute("ROLLBACK TO SAVEPOINT invoice_status_guard_test")
        self.db.execute("RELEASE SAVEPOINT invoice_status_guard_test")
        self.assertEqual(
            self.db.execute("select status from invoices where id=?", (self.invoice_id,)).fetchone()[0],
            "completed",
        )

    def test_unconfirmed_invoice_is_rejected(self):
        self.add_item("100.00")
        self.db.execute("update invoices set status='draft' where id=?", (self.invoice_id,))
        with self.assertRaises(InvoiceRecognitionError):
            self.recognize()
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 0)

    def test_void_preserves_original_and_posts_exact_reversal(self):
        self.add_item("100.00", "7.25")
        original = self.recognize()
        voided = self.service.void(
            self.invoice_id, accounting_date=date(2026, 10, 8),
            reason_code="customer_cancelled", actor_id=self.actor_id,
        )
        statuses = self.db.execute(
            "select id,status,reversal_of from vouchers order by id"
        ).fetchall()
        self.assertEqual(
            [(row[0], row[1], row[2]) for row in statuses],
            [(original.voucher_id, "reversed", None),
             (voided.voucher_id, "posted", original.voucher_id)],
        )
        balances = self.db.execute(
            "select a.account_code,sum(e.debit),sum(e.credit) from voucher_entries e "
            "join accounts a on a.id=e.account_id group by a.account_code order by a.account_code"
        ).fetchall()
        self.assertTrue(all(Decimal(str(row[1])) == Decimal(str(row[2])) for row in balances))
        self.assertEqual(
            self.db.execute("select status from invoices where id=?", (self.invoice_id,)).fetchone()[0],
            "void",
        )
        replay = self.service.void(
            self.invoice_id, accounting_date=date(2026, 10, 8),
            reason_code="customer_cancelled", actor_id=self.actor_id,
        )
        self.assertFalse(replay.created)
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 2)

    def test_void_rejects_invoice_with_active_receipt_settlement(self):
        self.add_item("100.00")
        self.recognize()
        CustomerReceiptService(self.db).receive(
            receipt_no="RCT-INVOICE-VOID", customer_id=self.customer_id,
            receipt_date=date(2026, 10, 7), amount="10.00",
            bank_account_id=self.bank_id,
            allocations=(ReceiptAllocation(self.invoice_id, "10.00"),),
            actor_id=self.actor_id,
        )
        with self.assertRaises(InvoiceVoidError):
            self.service.void(
                self.invoice_id, accounting_date=date(2026, 10, 8),
                reason_code="customer_cancelled", actor_id=self.actor_id,
            )
        self.assertEqual(
            self.db.execute("select status from invoices where id=?", (self.invoice_id,)).fetchone()[0],
            "completed",
        )

    def test_positive_and_negative_corrections_post_delta_vouchers(self):
        self.add_item("100.00")
        self.recognize()
        positive = self.service.correct(
            self.invoice_id, revenue_delta="20.00", sales_tax_delta="2.00",
            accounting_date=date(2026, 10, 9), reason_code="scope_increase",
            actor_id=self.actor_id,
        )
        negative = self.service.correct(
            self.invoice_id, revenue_delta="-10.00", sales_tax_delta="-1.00",
            accounting_date=date(2026, 10, 10), reason_code="price_credit",
            actor_id=self.actor_id,
        )
        self.assertEqual((positive.correction_no, positive.receivable_delta),
                         (1, Decimal("22.00")))
        self.assertEqual((negative.correction_no, negative.receivable_delta),
                         (2, Decimal("-11.00")))
        negative_entries = self.db.execute(
            "select a.account_code,e.debit,e.credit from voucher_entries e "
            "join accounts a on a.id=e.account_id where e.voucher_id=? order by e.line_no",
            (negative.voucher_id,),
        ).fetchall()
        self.assertEqual(
            [(row[0], Decimal(str(row[1])), Decimal(str(row[2])))
             for row in negative_entries],
            [("1100", Decimal("0"), Decimal("11")),
             ("4000", Decimal("10"), Decimal("0")),
             ("2100", Decimal("1"), Decimal("0"))],
        )

    def test_receipt_can_allocate_increased_corrected_receivable(self):
        self.add_item("100.00")
        self.recognize()
        self.service.correct(
            self.invoice_id, revenue_delta="50.00",
            accounting_date=date(2026, 10, 9), reason_code="approved_change_order",
            actor_id=self.actor_id,
        )
        result = CustomerReceiptService(self.db).receive(
            receipt_no="RCT-CORRECTED", customer_id=self.customer_id,
            receipt_date=date(2026, 10, 10), amount="150.00",
            bank_account_id=self.bank_id,
            allocations=(ReceiptAllocation(self.invoice_id, "150.00"),),
            actor_id=self.actor_id,
        )
        self.assertTrue(result.created)
        self.assertEqual(
            Decimal(str(self.db.execute(
                "select sum(amount) from receipt_allocations where invoice_id=?",
                (self.invoice_id,),
            ).fetchone()[0])), Decimal("150"),
        )


if __name__ == "__main__":
    unittest.main()
