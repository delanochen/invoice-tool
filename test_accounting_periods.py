import unittest
from datetime import date

from database import IntegrityError, PostgreSQLConnection
from invoice_tool.accounting import (
    AccountingPeriodService,
    ClosedPeriod,
    EventKey,
    PostingLine,
    PostingService,
    PeriodStateError,
)


class AccountingPeriodServiceTest(unittest.TestCase):
    def setUp(self):
        self.db = PostgreSQLConnection()
        self.actor_id = self.db.execute(
            "insert into users(name,email,password_hash,role,created_at) "
            "values('Period Tester','period-test@example.invalid','x','admin',"
            "CURRENT_TIMESTAMP::text)"
        ).lastrowid
        self.db.execute(
            "insert into settings(key,value) values('accounting_base_enabled','1') "
            "on conflict(key) do update set value='1'"
        )
        self.db.execute(
            "insert into accounts(account_code,account_name,account_type,normal_balance) "
            "values('1000','Bank','asset','debit')"
        )
        self.db.execute(
            "insert into accounts(account_code,account_name,account_type,normal_balance) "
            "values('3000','Opening Balance Equity','equity','credit')"
        )
        self.periods = AccountingPeriodService(self.db)
        self.posting = PostingService(self.db)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _post_gl(self, source_id):
        return self.posting.post_event(
            key=EventKey("period_test", source_id, 1, "period.test"),
            business_anchor_type="period_test_row",
            business_anchor_id=source_id,
            business_date=date(2026, 10, 15),
            accounting_date=date(2026, 10, 15),
            lines=(PostingLine("1000", debit="5"), PostingLine("3000", credit="5")),
            actor_id=self.actor_id,
        )

    def test_create_month_is_idempotent_and_uses_calendar_boundaries(self):
        first = self.periods.create_month("2026-10-19", actor_id=self.actor_id)
        second = self.periods.create_month("2026-10-01", actor_id=self.actor_id)
        self.assertEqual(first, second)
        row = self.db.execute(
            "select period_start,period_end,status from accounting_periods where id=?",
            (first,),
        ).fetchone()
        self.assertEqual(tuple(row), ("2026-10-01", "2026-10-31", "open"))

    def test_database_rejects_partial_calendar_month(self):
        with self.assertRaises(IntegrityError):
            self.db.execute(
                "insert into accounting_periods(period_start,period_end,status) "
                "values('2026-10-02','2026-10-31','open')"
            )
        with self.assertRaises(IntegrityError):
            self.db.execute(
                "insert into accounting_periods(period_start,period_end,status) "
                "values('2026-10-01','2026-10-30','open')"
            )

    def test_close_blocks_gl_reopen_allows_it_and_audits_both(self):
        period_id = self.periods.create_month("2026-10-01", actor_id=self.actor_id)
        self.periods.close(period_id, actor_id=self.actor_id)
        with self.assertRaises(ClosedPeriod):
            self._post_gl(1)
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 0)
        self.periods.reopen(
            period_id, actor_id=self.actor_id, reason_code="approved_reopen"
        )
        result = self._post_gl(2)
        self.assertIsNotNone(result.voucher_id)
        actions = [row[0] for row in self.db.execute(
            "select action from posting_audit where action like 'period_%' order by id"
        ).fetchall()]
        self.assertEqual(actions, ["period_created", "period_closed", "period_reopened"])

    def test_non_gl_event_remains_auditable_while_period_is_closed(self):
        period_id = self.periods.create_month("2026-10-01", actor_id=self.actor_id)
        self.periods.close(period_id, actor_id=self.actor_id)
        result = self.posting.post_event(
            key=EventKey("bank_transaction.payment", 8, 1, "bank.matched",
                         related_source_id=9),
            business_anchor_type="payment_order_event",
            business_anchor_id=10,
            business_date="2026-10-15",
            accounting_date="2026-10-15",
            requires_gl=False,
            actor_id=self.actor_id,
        )
        self.assertIsNone(result.voucher_id)
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 0)

    def test_reopen_requires_reason(self):
        period_id = self.periods.create_month("2026-10-01", actor_id=self.actor_id)
        self.periods.close(period_id, actor_id=self.actor_id)
        with self.assertRaisesRegex(ValueError, "reason_code"):
            self.periods.reopen(period_id, actor_id=self.actor_id, reason_code="  ")

    def test_close_is_blocked_by_draft_voucher(self):
        period_id = self.periods.create_month("2026-10-01", actor_id=self.actor_id)
        self.db.execute(
            "insert into vouchers(voucher_number,voucher_type,status,business_date,"
            "accounting_date,currency,description,source_snapshot,reason_code,created_by) "
            "values('JV-2610-DRAFT','manual','draft','2026-10-15','2026-10-15',"
            "'USD','Draft close blocker','{}'::jsonb,'',?)", (self.actor_id,)
        )
        check = self.periods.close_check(period_id)
        self.assertFalse(check.ready)
        self.assertEqual(check.draft_vouchers, 1)
        with self.assertRaisesRegex(PeriodStateError, "draft voucher"):
            self.periods.close(period_id, actor_id=self.actor_id)
        self.assertEqual(
            self.db.execute("select status from accounting_periods where id=?", (period_id,))
            .fetchone()[0],
            "open",
        )

    def test_close_is_blocked_by_unposted_opening_cutover(self):
        period_id = self.periods.create_month("2026-10-01", actor_id=self.actor_id)
        self.db.execute(
            "insert into accounting_opening_cutover(cutover_date,created_by) values(?,?)",
            ("2026-10-01", self.actor_id),
        )
        check = self.periods.close_check(period_id)
        self.assertEqual(check.draft_openings, 1)
        with self.assertRaisesRegex(PeriodStateError, "draft opening"):
            self.periods.close(period_id, actor_id=self.actor_id)

    def test_close_check_is_ready_after_balanced_posting(self):
        period_id = self.periods.create_month("2026-10-01", actor_id=self.actor_id)
        self._post_gl(50)
        check = self.periods.close_check(period_id)
        self.assertTrue(check.ready)
        self.assertEqual(check.blockers, ())


if __name__ == "__main__":
    unittest.main()
