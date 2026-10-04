import unittest
from datetime import date
from decimal import Decimal

from database import PostgreSQLConnection
from invoice_tool.accounting import (
    CustomerReceiptService,
    ReceiptAllocation,
    ReceiptConflict,
    ReceiptError,
    UnallocatedReceipt,
)


class CustomerReceiptServiceTest(unittest.TestCase):
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
             ("2200", "Customer Prepayments", "liability", "credit"),
             ("4000", "Revenue", "revenue", "credit")),
        )
        self.actor_id = self.db.execute(
            "insert into users(name,email,password_hash,role,created_at) "
            "values('Receipt Tester','receipt-test@example.invalid','x','admin',"
            "CURRENT_TIMESTAMP::text)"
        ).lastrowid
        self.customer_id = self.db.execute(
            "insert into clients(client_number,name,short_name,created_at) "
            "values('C-RCT','Receipt Client','RCT',CURRENT_TIMESTAMP::text)"
        ).lastrowid
        self.project_id = self.db.execute(
            "insert into projects(name,created_at) values('Receipt Project',CURRENT_TIMESTAMP::text)"
        ).lastrowid
        self.bank_id = self.db.execute(
            "insert into bank_accounts(account_name,bank_name,currency,is_active,created_by,"
            "created_at,updated_at) values('Receipt Test Bank','Test Bank','USD',1,?,"
            "CURRENT_TIMESTAMP::text,CURRENT_TIMESTAMP::text)", (self.actor_id,)
        ).lastrowid
        self.invoice_id = self.db.execute(
            "insert into invoices(invoice_number,client_id,issue_date,due_date,currency,status,"
            "created_by,created_at) values('INV-RCT',?,'2026-10-01','2026-10-31','USD',"
            "'completed',?,CURRENT_TIMESTAMP::text)",
            (self.customer_id, self.actor_id),
        ).lastrowid
        self.db.execute(
            "insert into invoice_items(invoice_id,project_id,description,amount,tax_rate) "
            "values(?,?,?,'1000','0')",
            (self.invoice_id, self.project_id, "Receivable"),
        )
        self.service = CustomerReceiptService(self.db)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def receive(self, receipt_no="RCT-1", amount="600.00", allocations=(),
                prepayment_reason=""):
        return self.service.receive(
            receipt_no=receipt_no, customer_id=self.customer_id,
            receipt_date=date(2026, 10, 5), amount=amount,
            bank_account_id=self.bank_id, allocations=allocations,
            prepayment_reason=prepayment_reason, actor_id=self.actor_id,
        )

    def test_allocated_receipt_posts_bank_and_receivable_and_settles_invoice(self):
        result = self.receive(
            allocations=(ReceiptAllocation(self.invoice_id, "600.00"),)
        )
        entries = self.db.execute(
            "select a.account_code,e.debit,e.credit from voucher_entries e "
            "join accounts a on a.id=e.account_id where e.voucher_id=? order by e.line_no",
            (result.voucher_id,),
        ).fetchall()
        self.assertEqual(
            [(row[0], Decimal(str(row[1])), Decimal(str(row[2]))) for row in entries],
            [("1000", Decimal("600"), Decimal("0")),
             ("1100", Decimal("0"), Decimal("600"))],
        )
        self.assertEqual(
            self.db.execute("select count(*) from receipt_allocations").fetchone()[0], 1
        )
        self.assertEqual(
            self.db.execute("select count(*) from voucher_settlement_lines").fetchone()[0], 1
        )

    def test_cumulative_overallocation_rolls_back_entire_second_receipt(self):
        allocation = (ReceiptAllocation(self.invoice_id, "600.00"),)
        self.receive(allocations=allocation)
        with self.assertRaises(ReceiptError):
            self.receive(
                receipt_no="RCT-2", amount="500.00",
                allocations=(ReceiptAllocation(self.invoice_id, "500.00"),),
            )
        self.assertEqual(self.db.execute("select count(*) from customer_receipts").fetchone()[0], 1)
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 1)
        self.assertEqual(
            Decimal(str(self.db.execute(
                "select sum(amount) from receipt_allocations where status='active'"
            ).fetchone()[0])), Decimal("600"),
        )

    def test_unallocated_receipt_becomes_customer_prepayment_not_revenue(self):
        result = self.receive(prepayment_reason="deposit before invoice")
        codes = self.db.execute(
            "select a.account_code from voucher_entries e join accounts a on a.id=e.account_id "
            "where e.voucher_id=? order by e.line_no", (result.voucher_id,),
        ).fetchall()
        self.assertEqual([row[0] for row in codes], ["1000", "2200"])
        prepayment = self.db.execute(
            "select original_amount,status,reason_code from customer_prepayments"
        ).fetchone()
        self.assertEqual(
            (Decimal(str(prepayment[0])), prepayment[1], prepayment[2]),
            (Decimal("600"), "open", "deposit before invoice"),
        )
        self.assertEqual(
            self.db.execute(
                "select count(*) from voucher_entries e join accounts a on a.id=e.account_id "
                "where a.account_code='4000'"
            ).fetchone()[0], 0
        )

    def test_unallocated_receipt_without_reason_is_rejected_before_writes(self):
        with self.assertRaises(UnallocatedReceipt):
            self.receive()
        self.assertEqual(self.db.execute("select count(*) from customer_receipts").fetchone()[0], 0)

    def test_same_receipt_number_returns_existing_business_fact(self):
        allocation = (ReceiptAllocation(self.invoice_id, "600.00"),)
        first = self.receive(allocations=allocation)
        second = self.receive(allocations=allocation)
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual((second.receipt_id, second.voucher_id),
                         (first.receipt_id, first.voucher_id))
        self.assertEqual(self.db.execute("select count(*) from customer_receipts").fetchone()[0], 1)
        self.assertEqual(self.db.execute("select count(*) from receipt_allocations").fetchone()[0], 1)

    def test_same_receipt_number_with_changed_allocation_is_conflict(self):
        self.receive(allocations=(ReceiptAllocation(self.invoice_id, "600.00"),))
        with self.assertRaises(ReceiptConflict):
            self.receive(
                allocations=(ReceiptAllocation(self.invoice_id, "500.00"),),
                prepayment_reason="changed allocation remainder",
            )
        self.assertEqual(self.db.execute("select count(*) from customer_receipts").fetchone()[0], 1)
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
