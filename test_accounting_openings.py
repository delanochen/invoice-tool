import unittest
from datetime import date
from decimal import Decimal

from database import OperationalError, PostgreSQLConnection
from invoice_tool.accounting import OpeningBalanceError, OpeningBalanceService


class OpeningBalanceServiceTest(unittest.TestCase):
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
             ("2000", "Employee Payable", "liability", "credit"),
             ("2200", "Customer Prepayments", "liability", "credit"),
             ("3000", "Opening Balance Equity", "equity", "credit")),
        )
        self.actor_id = self.db.execute(
            "insert into users(name,email,password_hash,role,created_at) "
            "values('Opening Tester','opening@example.invalid','x','admin',"
            "CURRENT_TIMESTAMP::text)"
        ).lastrowid
        self.service = OpeningBalanceService(self.db)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def account_id(self, code):
        return self.db.execute(
            "select id from accounts where account_code=?", (code,)
        ).fetchone()[0]

    def test_posts_normal_balance_directions_and_balancing_equity(self):
        cutover_id = self.service.create_cutover(date(2026, 10, 1), actor_id=self.actor_id)
        bank_line = self.service.add_line(
            cutover_id, origin_type="bank_account", origin_id=10,
            account_id=self.account_id("1000"), amount="500.00",
            reason_code="cutover_reclassification",
        )
        payable_line = self.service.add_line(
            cutover_id, origin_type="employee_payable", origin_id=20,
            account_id=self.account_id("2000"), amount="125.00",
            reason_code="cutover_reclassification",
        )
        result = self.service.post(cutover_id, actor_id=self.actor_id)
        entries = self.db.execute(
            "select a.account_code,e.debit,e.credit,e.source_line_id "
            "from voucher_entries e join accounts a on a.id=e.account_id "
            "where e.voucher_id=? order by e.line_no", (result.voucher_id,),
        ).fetchall()
        self.assertEqual(
            [(row["account_code"], Decimal(str(row["debit"])),
              Decimal(str(row["credit"]))) for row in entries],
            [("1000", Decimal("500"), Decimal("0")),
             ("2000", Decimal("0"), Decimal("125")),
             ("3000", Decimal("0"), Decimal("375"))],
        )
        linked = self.db.execute(
            "select id,voucher_entry_id from accounting_opening_lines "
            "where id in (?,?) order by id", (bank_line, payable_line),
        ).fetchall()
        self.assertTrue(all(row["voucher_entry_id"] for row in linked))
        cutover = self.db.execute(
            "select status,voucher_id from accounting_opening_cutover where id=?",
            (cutover_id,),
        ).fetchone()
        self.assertEqual((cutover["status"], cutover["voucher_id"]),
                         ("posted", result.voucher_id))

    def test_post_is_idempotent_and_posted_lines_cannot_be_removed(self):
        cutover_id = self.service.create_cutover("2026-10-01", actor_id=self.actor_id)
        line_id = self.service.add_line(
            cutover_id, origin_type="prepayment", origin_id=1,
            account_id=self.account_id("2200"), amount="80",
            reason_code="cutover_reclassification",
        )
        first = self.service.post(cutover_id, actor_id=self.actor_id)
        second = self.service.post(cutover_id, actor_id=self.actor_id)
        self.assertEqual(first.voucher_id, second.voucher_id)
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 1)
        with self.assertRaises(OpeningBalanceError):
            self.service.remove_line(line_id)
        self.db.execute("SAVEPOINT opening_line_immutable")
        with self.assertRaises(OperationalError):
            self.db.execute(
                "update accounting_opening_lines set amount=81 where id=?", (line_id,)
            )
        self.db.execute("ROLLBACK TO SAVEPOINT opening_line_immutable")
        self.db.execute("RELEASE SAVEPOINT opening_line_immutable")

    def test_rejects_manual_opening_equity_line(self):
        cutover_id = self.service.create_cutover("2026-10-01", actor_id=self.actor_id)
        with self.assertRaises(OpeningBalanceError):
            self.service.add_line(
                cutover_id, origin_type="manual", origin_id=1,
                account_id=self.account_id("3000"), amount="10",
                reason_code="prior_period_error",
            )


if __name__ == "__main__":
    unittest.main()
