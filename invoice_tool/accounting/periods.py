"""Natural-month accounting period lifecycle."""

import calendar
from datetime import date
import json


class PeriodStateError(RuntimeError):
    code = "accounting_period_state"


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
            "update accounting_periods set status='closed',closed_by=?,"
            "closed_at=CURRENT_TIMESTAMP::text,reason_code=? where id=?",
            (actor_id, reason_code, period_id),
        )
        self._audit(period_id, "period_closed", "open", "closed",
                    reason_code, actor_id)

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
