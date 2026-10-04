import unittest
from datetime import date
from decimal import Decimal

from database import PostgreSQLConnection
from invoice_tool.accounting import (
    AccountingDisabled,
    ClosedPeriod,
    EventKey,
    IdempotencyConflict,
    PayloadConflict,
    PostingLine,
    PostingService,
    SettlementExceeded,
    SettlementLine,
)


class AccountingPostingServiceTest(unittest.TestCase):
    def setUp(self):
        self.db = PostgreSQLConnection()
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
        self.db.execute(
            "insert into accounting_periods(period_start,period_end,status) "
            "values('2026-10-01','2026-10-31','open')"
        )
        self.actor_id = self.db.execute(
            "insert into users(name,email,password_hash,role,created_at) "
            "values('Accounting Tester','accounting-test@example.invalid','x','admin',"
            "CURRENT_TIMESTAMP::text)"
        ).lastrowid
        self.service = PostingService(self.db)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    @staticmethod
    def key(event_type="test.posted"):
        return EventKey("test_source", 9001, 1, event_type)

    @staticmethod
    def lines(amount="10.00"):
        return (
            PostingLine("1000", debit=amount, source_line_type="test", source_line_id=1),
            PostingLine("3000", credit=amount, source_line_type="test", source_line_id=1),
        )

    def post(self, **overrides):
        values = {
            "key": self.key(),
            "business_anchor_type": "test_row",
            "business_anchor_id": 7001,
            "business_date": date(2026, 10, 3),
            "accounting_date": date(2026, 10, 3),
            "lines": self.lines(),
            "description": "posting kernel acceptance",
            "source_snapshot": {"amount": "10.00"},
            "actor_id": self.actor_id,
        }
        values.update(overrides)
        return self.service.post_event(**values)

    def test_disabled_base_preserves_old_path(self):
        self.db.execute(
            "update settings set value='0' where key='accounting_base_enabled'"
        )
        with self.assertRaises(AccountingDisabled):
            self.post()
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 0)

    def test_posts_balanced_voucher_and_same_payload_is_idempotent(self):
        first = self.post()
        second = self.post()
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(second.voucher_id, first.voucher_id)
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 1)
        self.assertEqual(self.db.execute("select count(*) from voucher_entries").fetchone()[0], 2)
        self.assertEqual(self.db.execute("select count(*) from posting_events").fetchone()[0], 1)
        voucher = self.db.execute(
            "select voucher_number,status from vouchers where id=?", (first.voucher_id,)
        ).fetchone()
        self.assertRegex(voucher[0], r"^JV-202610-\d{4}$")
        self.assertEqual(voucher[1], "posted")

    def test_same_event_key_with_different_payload_is_rejected(self):
        self.post()
        with self.assertRaises(PayloadConflict):
            self.post(lines=self.lines("11.00"), source_snapshot={"amount": "11.00"})
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 1)
        self.assertEqual(self.db.execute("select count(*) from posting_events").fetchone()[0], 1)

    def test_non_gl_event_is_audited_without_voucher(self):
        result = self.post(
            key=self.key("advance.offset_reserved"), lines=(), requires_gl=False,
            source_snapshot={"amount": "10.00", "advance_id": 5},
        )
        self.assertIsNone(result.voucher_id)
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 0)
        event = self.db.execute(
            "select requires_gl,voucher_id from posting_events where id=?", (result.event_id,)
        ).fetchone()
        self.assertEqual((event[0], event[1]), (False, None))
        self.assertEqual(self.db.execute("select count(*) from posting_audit").fetchone()[0], 1)

    def test_closed_period_blocks_all_writes(self):
        self.db.execute("update accounting_periods set status='closed'")
        with self.assertRaises(ClosedPeriod):
            self.post()
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 0)
        self.assertEqual(self.db.execute("select count(*) from posting_events").fetchone()[0], 0)

    def test_reused_idempotency_key_rolls_back_provisional_voucher(self):
        self.post(idempotency_key="same-http-request")
        with self.assertRaises(IdempotencyConflict):
            self.post(
                key=EventKey("test_source", 9002, 1, "test.posted"),
                business_anchor_id=7002,
                idempotency_key="same-http-request",
            )
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 1)
        self.assertEqual(self.db.execute("select count(*) from voucher_entries").fetchone()[0], 2)
        self.assertEqual(self.db.execute("select count(*) from posting_events").fetchone()[0], 1)

    def test_mark_reversed_uses_allowed_one_way_transition(self):
        original = self.post()
        self.service.mark_reversed(
            original.voucher_id, actor_id=self.actor_id, reason_code="test_reversal"
        )
        row = self.db.execute(
            "select status,reversed_by,reason_code from vouchers where id=?",
            (original.voucher_id,),
        ).fetchone()
        self.assertEqual((row[0], row[1], row[2]),
                         ("reversed", self.actor_id, "test_reversal"))
        self.assertEqual(
            self.db.execute("select count(*) from posting_audit").fetchone()[0], 2
        )

    def test_settlement_is_written_atomically_before_voucher_is_posted(self):
        result = self.post(
            settlements=(SettlementLine(1, "payable", 4401, "6.00"),),
            settlement_limits={("payable", 4401): "10.00"},
        )
        row = self.db.execute(
            "select s.amount,s.status,e.line_no,v.status "
            "from voucher_settlement_lines s "
            "join voucher_entries e on e.id=s.voucher_entry_id "
            "join vouchers v on v.id=s.voucher_id where s.source_event_id=?",
            (result.event_id,),
        ).fetchone()
        self.assertEqual((Decimal(str(row[0])), row[1], row[2], row[3]),
                         (Decimal("6.00"), "active", 1, "posted"))

    def test_cumulative_settlement_excess_rejects_whole_second_event(self):
        self.post(
            settlements=(SettlementLine(1, "payable", 4402, "6.00"),),
            settlement_limits={("payable", 4402): "10.00"},
        )
        with self.assertRaises(SettlementExceeded):
            self.post(
                key=EventKey("test_source", 9002, 1, "test.posted"),
                business_anchor_id=7002,
                settlements=(SettlementLine(1, "payable", 4402, "5.00"),),
                settlement_limits={("payable", 4402): "10.00"},
            )
        self.assertEqual(self.db.execute("select count(*) from vouchers").fetchone()[0], 1)
        self.assertEqual(
            self.db.execute("select count(*) from voucher_settlement_lines").fetchone()[0], 1
        )

    def test_settlement_changes_participate_in_payload_conflict_detection(self):
        self.post(
            settlements=(SettlementLine(1, "receivable", 4403, "4.00"),),
            settlement_limits={("receivable", 4403): "10.00"},
        )
        with self.assertRaises(PayloadConflict):
            self.post(
                settlements=(SettlementLine(1, "receivable", 4403, "5.00"),),
                settlement_limits={("receivable", 4403): "10.00"},
            )


if __name__ == "__main__":
    unittest.main()
