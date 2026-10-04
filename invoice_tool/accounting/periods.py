"""Natural-month accounting period lifecycle."""

import calendar
from datetime import date
import json
from dataclasses import dataclass


class PeriodStateError(RuntimeError):
    code = "accounting_period_state"


@dataclass(frozen=True)
class PeriodCloseCheck:
    period_id: int
    draft_vouchers: int
    unbalanced_vouchers: int
    incomplete_postings: int
    draft_openings: int

    @property
    def ready(self):
        return not any((self.draft_vouchers, self.unbalanced_vouchers,
                        self.incomplete_postings, self.draft_openings))

    @property
    def blockers(self):
        items = []
        if self.draft_vouchers:
            items.append(f"{self.draft_vouchers} draft voucher(s)")
        if self.unbalanced_vouchers:
            items.append(f"{self.unbalanced_vouchers} unbalanced voucher(s)")
        if self.incomplete_postings:
            items.append(f"{self.incomplete_postings} incomplete posting(s)")
        if self.draft_openings:
            items.append(f"{self.draft_openings} draft opening cutover(s)")
        return tuple(items)


def _month_bounds(value):
    if isinstance(value, str):
        value = date.fromisoformat(value)
    start = value.replace(day=1)
    end = value.replace(day=calendar.monthrange(value.year, value.month)[1])
    return start.isoformat(), end.isoformat()


class AccountingPeriodService:
    def __init__(self, connection):
        self.connection = connection

    def create_month(self, month, *, actor_id=None):
        start, end = _month_bounds(month)
        self.connection.execute("BEGIN IMMEDIATE")
        row = self.connection.execute(
            "select * from accounting_periods where period_start=? and period_end=?",
            (start, end),
        ).fetchone()
        if row:
            return row["id"]
        cursor = self.connection.execute(
            "insert into accounting_periods(period_start,period_end,status) "
            "values(?,?,'open')", (start, end),
        )
        period_id = cursor.lastrowid
        self._audit(period_id, "period_created", None, "open", "", actor_id)
        return period_id

    def close(self, period_id, *, actor_id, reason_code="period_close"):
        if not actor_id:
            raise ValueError("actor_id is required")
        self.connection.execute("BEGIN IMMEDIATE")
        row = self.connection.execute(
            "select * from accounting_periods where id=?", (period_id,)
        ).fetchone()
        if not row:
            raise PeriodStateError("accounting period not found")
        if row["status"] != "open":
            raise PeriodStateError("only an open accounting period can be closed")
        self.connection.execute(
            "select pg_advisory_xact_lock(hashtextextended(?,0))",
            (f"accounting-period-close:{period_id}",),
        )
        check = self.close_check(period_id, period=row)
        if not check.ready:
            raise PeriodStateError("period close blocked: " + "; ".join(check.blockers))
        self.connection.execute(
            "update accounting_periods set status='closed',closed_by=?,"
            "closed_at=CURRENT_TIMESTAMP::text,reason_code=? where id=?",
            (actor_id, reason_code, period_id),
        )
        self._audit(period_id, "period_closed", "open", "closed",
                    reason_code, actor_id)

    def close_check(self, period_id, *, period=None):
        period = period or self.connection.execute(
            "select * from accounting_periods where id=?", (period_id,)
        ).fetchone()
        if not period:
            raise PeriodStateError("accounting period not found")
        bounds = (period["period_start"], period["period_end"])
        draft_vouchers = int(self.connection.execute(
            "select count(*) from vouchers where accounting_date between ? and ? "
            "and status='draft'", bounds,
        ).fetchone()[0])
        unbalanced_vouchers = int(self.connection.execute(
            "select count(*) from (select v.id from vouchers v "
            "left join voucher_entries e on e.voucher_id=v.id "
            "where v.accounting_date between ? and ? group by v.id "
            "having coalesce(sum(e.debit),0)<>coalesce(sum(e.credit),0)) q", bounds,
        ).fetchone()[0])
        incomplete_postings = int(self.connection.execute(
            "select count(*) from vouchers where accounting_date between ? and ? "
            "and status in ('posted','reversed') and posted_at is null", bounds,
        ).fetchone()[0])
        draft_openings = int(self.connection.execute(
            "select count(*) from accounting_opening_cutover "
            "where cutover_date between ? and ? and status='draft'", bounds,
        ).fetchone()[0])
        return PeriodCloseCheck(
            int(period_id), draft_vouchers, unbalanced_vouchers,
            incomplete_postings, draft_openings,
        )

    def reopen(self, period_id, *, actor_id, reason_code):
        if not actor_id:
            raise ValueError("actor_id is required")
        if not str(reason_code or "").strip():
            raise ValueError("reason_code is required")
        self.connection.execute("BEGIN IMMEDIATE")
        row = self.connection.execute(
            "select * from accounting_periods where id=?", (period_id,)
        ).fetchone()
        if not row:
            raise PeriodStateError("accounting period not found")
        if row["status"] != "closed":
            raise PeriodStateError("only a closed accounting period can be reopened")
        self.connection.execute(
            "update accounting_periods set status='open',reopened_by=?,"
            "reopened_at=CURRENT_TIMESTAMP::text,reason_code=? where id=?",
            (actor_id, reason_code.strip(), period_id),
        )
        self._audit(period_id, "period_reopened", "closed", "open",
                    reason_code.strip(), actor_id)

    def _audit(self, period_id, action, old_status, new_status, reason_code, actor_id):
        self.connection.execute(
            "insert into posting_audit(action,old_value,new_value,reason_code,actor_id) "
            "values(?,?::jsonb,?::jsonb,?,?)",
            (action,
             json.dumps({"period_id": period_id, "status": old_status}),
             json.dumps({"period_id": period_id, "status": new_status}),
             reason_code, actor_id),
        )
