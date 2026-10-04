"""Transactional, idempotent accounting posting kernel.

Business modules own their source rows. They call ``preflight_event`` before
creating a source row, then call ``post_event`` in the same database
transaction. This module never commits; the caller decides whether the source
fact and its accounting event commit together.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re

from database import IntegrityError


CENT = Decimal("0.01")
REQUIRED_TABLES = {
    "accounts", "accounting_periods", "posting_events", "vouchers",
    "voucher_entries", "voucher_source_links", "voucher_settlement_lines",
    "voucher_attachment_links", "accounting_opening_lines",
    "accounting_opening_cutover", "posting_audit", "customer_receipts",
    "receipt_allocations", "customer_prepayments",
}
REQUIRED_TRIGGERS = {
    "voucher_entries_protect_posted", "voucher_source_links_protect_posted",
    "voucher_attachment_links_protect_posted",
    "voucher_settlement_lines_protect_posted", "vouchers_mutation_guard",
    "voucher_entries_balance_check", "vouchers_balance_check",
}


class PostingError(RuntimeError):
    code = "posting_error"


class AccountingDisabled(PostingError):
    code = "accounting_disabled"


class AccountingStructureError(PostingError):
    code = "accounting_structure_error"


class PayloadConflict(PostingError):
    code = "payload_conflict"


class IdempotencyConflict(PostingError):
    code = "idempotency_conflict"


class ClosedPeriod(PostingError):
    code = "closed_period"


class NoBusinessFact(PostingError):
    code = "no_business_fact"


class UnbalancedVoucher(PostingError):
    code = "unbalanced_voucher"


class SettlementExceeded(PostingError):
    code = "settlement_exceeded"


@dataclass(frozen=True)
class EventKey:
    source_type: str
    source_id: int
    source_version: int
    event_type: str
    occurrence_no: int = 1
    related_source_id: int = 0

    def validate(self):
        if not self.source_type or not self.event_type:
            raise ValueError("source_type and event_type are required")
        if self.source_id <= 0 or self.related_source_id < 0:
            raise ValueError("source ids are invalid")
        if self.source_version < 1 or self.occurrence_no < 1:
            raise ValueError("source_version and occurrence_no must be positive")


@dataclass(frozen=True)
class PostingLine:
    account_code: str
    debit: object = Decimal("0")
    credit: object = Decimal("0")
    source_line_type: str = ""
    source_line_id: int | None = None
    memo: str = ""


@dataclass(frozen=True)
class SettlementLine:
    entry_line_no: int
    target_type: str
    target_id: int
    amount: object


@dataclass(frozen=True)
class EventResult:
    event_id: int
    voucher_id: int | None
    payload_hash: str
    created: bool


def _money(value):
    try:
        amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid monetary amount") from exc
    if amount < 0:
        raise ValueError("monetary amounts cannot be negative")
    return amount


def _date_text(value):
    if isinstance(value, date):
        return value.isoformat()
    value = str(value or "")
    return date.fromisoformat(value).isoformat()


class PostingService:
    def __init__(self, connection):
        self.connection = connection

    def _rows(self, sql, params=()):
        return self.connection.execute(sql, params).fetchall()

    def require_posting_ready(self):
        row = self.connection.execute(
            "select value from settings where key = ?", ("accounting_base_enabled",)
        ).fetchone()
        if not row or row[0] != "1":
            return False
        tables = {row[0] for row in self._rows(
            "select table_name from information_schema.tables where table_schema = ?",
            ("public",),
        )}
        triggers = {row[0] for row in self._rows(
            "select tgname from pg_trigger where not tgisinternal"
        )}
        missing = sorted(REQUIRED_TABLES - tables)
        missing_triggers = sorted(REQUIRED_TRIGGERS - triggers)
        if missing or missing_triggers:
            details = []
            if missing:
                details.append("tables=" + ",".join(missing))
            if missing_triggers:
                details.append("triggers=" + ",".join(missing_triggers))
            raise AccountingStructureError("missing accounting structure: " + "; ".join(details))
        return True

    @staticmethod
    def normalize_lines(lines):
        normalized = []
        for item in lines:
            debit = _money(item.debit)
            credit = _money(item.credit)
            if (debit > 0) == (credit > 0):
                raise ValueError("each line must contain exactly one positive side")
            normalized.append({
                "account_code": str(item.account_code).strip(),
                "debit": format(debit, ".2f"),
                "credit": format(credit, ".2f"),
                "source_line_type": str(item.source_line_type or ""),
                "source_line_id": item.source_line_id,
                "memo": str(item.memo or ""),
            })
        if len(normalized) < 2:
            raise UnbalancedVoucher("a general-ledger event requires at least two lines")
        if sum(Decimal(item["debit"]) for item in normalized) != sum(
            Decimal(item["credit"]) for item in normalized
        ):
            raise UnbalancedVoucher("voucher debit and credit totals differ")
        return normalized

    @staticmethod
    def payload_hash(*, key, business_date, accounting_date, currency, lines,
                     settlements, snapshot):
        key.validate()
        payload = {
            "key": {
                "source_type": key.source_type,
                "source_id": key.source_id,
                "related_source_id": key.related_source_id,
                "source_version": key.source_version,
                "event_type": key.event_type,
                "occurrence_no": key.occurrence_no,
            },
            "business_date": _date_text(business_date),
            "accounting_date": _date_text(accounting_date),
            "currency": currency,
            "lines": sorted(lines, key=lambda item: (
                item["account_code"], item["source_line_type"],
                item["source_line_id"] if item["source_line_id"] is not None else -1,
                item["debit"], item["credit"], item["memo"],
            )),
            "settlements": sorted(settlements, key=lambda item: (
                item["target_type"], item["target_id"],
                item["entry_line_no"], item["amount"],
            )),
            "snapshot": snapshot or {},
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def normalize_settlements(settlements, line_count):
        normalized = []
        seen = set()
        for item in settlements:
            target_type = str(item.target_type or "").strip()
            if target_type not in {"payable", "receivable", "prepayment"}:
                raise ValueError("invalid settlement target type")
            if item.target_id <= 0:
                raise ValueError("settlement target id must be positive")
            if item.entry_line_no < 1 or item.entry_line_no > line_count:
                raise ValueError("settlement entry line does not exist")
            amount = _money(item.amount)
            if amount == 0:
                raise ValueError("settlement amount must be positive")
            key = (target_type, item.target_id)
            if key in seen:
                raise ValueError("one event may settle each target only once")
            seen.add(key)
            normalized.append({
                "entry_line_no": item.entry_line_no,
                "target_type": target_type,
                "target_id": item.target_id,
                "amount": format(amount, ".2f"),
            })
        return normalized

    def _existing(self, key):
        return self.connection.execute(
            "select id,voucher_id,payload_hash from posting_events "
            "where source_type=? and source_id=? and related_source_id=? "
            "and source_version=? and event_type=? and occurrence_no=?",
            (key.source_type, key.source_id, key.related_source_id,
             key.source_version, key.event_type, key.occurrence_no),
        ).fetchone()

    def preflight_event(self, key, payload_hash):
        key.validate()
        row = self._existing(key)
        if not row:
            return None
        if row[2] != payload_hash:
            raise PayloadConflict(f"payload_conflict for {key.event_type}")
        return EventResult(int(row[0]), int(row[1]) if row[1] else None,
                           row[2], False)

    def _next_voucher_number(self, accounting_date):
        prefix = "JV-" + _date_text(accounting_date)[:7].replace("-", "") + "-"
        rows = self._rows(
            "select voucher_number from vouchers where voucher_number like ?",
            (prefix + "%",),
        )
        highest = 0
        pattern = re.compile(r"^" + re.escape(prefix) + r"(\d+)$")
        for row in rows:
            match = pattern.match(row[0])
            if match:
                highest = max(highest, int(match.group(1)))
        return f"{prefix}{highest + 1:04d}"

    def _require_open_period(self, accounting_date):
        row = self.connection.execute(
            "select id from accounting_periods where period_start <= ? "
            "and period_end >= ? and status='open' order by period_start desc limit 1",
            (_date_text(accounting_date), _date_text(accounting_date)),
        ).fetchone()
        if not row:
            raise ClosedPeriod(f"no open accounting period for {_date_text(accounting_date)}")

    def post_event(
        self, *, key, business_anchor_type, business_anchor_id,
        business_date, accounting_date, lines=(), currency="USD",
        description="", source_snapshot=None, actor_id=None,
        idempotency_key=None, requires_gl=True, reversal_of=None,
        corrects_opening_line=None, reason_code="", settlements=(),
        settlement_limits=None,
    ):
        if not self.require_posting_ready():
            raise AccountingDisabled("accounting base is disabled")
        if not business_anchor_type or not business_anchor_id:
            raise NoBusinessFact("no_business_fact")
        if currency != "USD":
            raise ValueError("only USD is supported")
        normalized = self.normalize_lines(lines) if requires_gl else []
        normalized_settlements = self.normalize_settlements(
            settlements, len(normalized)
        )
        if normalized_settlements and not requires_gl:
            raise ValueError("non-GL events cannot contain settlement lines")
        limits = settlement_limits or {}
        for item in normalized_settlements:
            target_key = (item["target_type"], item["target_id"])
            if target_key not in limits:
                raise ValueError("settlement target limit is required")
            _money(limits[target_key])
        digest = self.payload_hash(
            key=key, business_date=business_date, accounting_date=accounting_date,
            currency=currency, lines=normalized, settlements=normalized_settlements,
            snapshot=source_snapshot,
        )
        self.connection.execute("BEGIN IMMEDIATE")
        existing = self.preflight_event(key, digest)
        if existing:
            return existing

        # Everything after this point is provisional. A competing transaction
        # may win the unique business-event key after our preflight; rolling
        # back this savepoint also removes the draft voucher and its entries.
        self.connection.execute("SAVEPOINT accounting_event")

        voucher_id = None
        if requires_gl:
            self._require_open_period(accounting_date)
            codes = [item["account_code"] for item in normalized]
            placeholders = ",".join("?" for _ in codes)
            account_rows = self._rows(
                f"select id,account_code from accounts where is_active=true "
                f"and account_code in ({placeholders})", tuple(codes),
            )
            account_ids = {row[1]: int(row[0]) for row in account_rows}
            missing = sorted(set(codes) - set(account_ids))
            if missing:
                raise PostingError("unmapped accounts: " + ",".join(missing))
            voucher_number = self._next_voucher_number(accounting_date)
            cursor = self.connection.execute(
                "insert into vouchers(voucher_number,voucher_type,status,business_date,"
                "accounting_date,currency,description,source_snapshot,reversal_of,"
                "corrects_opening_line,reason_code,created_by) "
                "values(?,?,'draft',?,?,?,?,?::jsonb,?,?,?,?)",
                (voucher_number, key.event_type, _date_text(business_date),
                 _date_text(accounting_date), currency, description,
                 json.dumps(source_snapshot or {}, ensure_ascii=False, sort_keys=True,
                            default=str), reversal_of, corrects_opening_line,
                 reason_code, actor_id),
            )
            voucher_id = int(cursor.lastrowid)
            for line_no, item in enumerate(normalized, 1):
                self.connection.execute(
                    "insert into voucher_entries(voucher_id,line_no,account_id,debit,credit,"
                    "currency,source_line_type,source_line_id,memo) values(?,?,?,?,?,?,?,?,?)",
                    (voucher_id, line_no, account_ids[item["account_code"]],
                     item["debit"], item["credit"], currency,
                     item["source_line_type"], item["source_line_id"], item["memo"]),
                )

        try:
            event_cursor = self.connection.execute(
                "insert into posting_events(source_type,source_id,related_source_id,source_version,"
                "event_type,occurrence_no,requires_gl,payload_hash,business_anchor_type,"
                "business_anchor_id,idempotency_key,voucher_id,created_by) "
                "values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key.source_type, key.source_id, key.related_source_id, key.source_version,
                 key.event_type, key.occurrence_no, requires_gl, digest,
                 business_anchor_type, business_anchor_id, idempotency_key,
                 voucher_id, actor_id),
            )
        except IntegrityError:
            self.connection.execute("ROLLBACK TO SAVEPOINT accounting_event")
            self.connection.execute("RELEASE SAVEPOINT accounting_event")
            existing = self.preflight_event(key, digest)
            if existing:
                return existing
            if idempotency_key:
                reused = self.connection.execute(
                    "select id from posting_events where idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if reused:
                    raise IdempotencyConflict("idempotency key belongs to another event")
            raise
        event_id = int(event_cursor.lastrowid)
        if requires_gl:
            self.connection.execute(
                "insert into voucher_source_links(voucher_id,source_type,source_id,source_version) "
                "values(?,?,?,?)",
                (voucher_id, key.source_type, key.source_id, key.source_version),
            )
            entry_ids = {
                int(row[0]): int(row[1]) for row in self._rows(
                    "select line_no,id from voucher_entries where voucher_id=?",
                    (voucher_id,),
                )
            }
            for item in normalized_settlements:
                target_key = (item["target_type"], item["target_id"])
                limit = _money(limits[target_key])
                lock_key = f"settlement:{target_key[0]}:{target_key[1]}"
                self.connection.execute(
                    "select pg_advisory_xact_lock(hashtextextended(?,0))", (lock_key,)
                )
                occupied = self.connection.execute(
                    "select coalesce(sum(amount),0) from voucher_settlement_lines "
                    "where target_type=? and target_id=? and status='active'",
                    target_key,
                ).fetchone()[0]
                if Decimal(str(occupied)) + Decimal(item["amount"]) > limit:
                    self.connection.execute("ROLLBACK TO SAVEPOINT accounting_event")
                    self.connection.execute("RELEASE SAVEPOINT accounting_event")
                    raise SettlementExceeded(
                        f"settlement exceeds remaining {target_key[0]} balance"
                    )
                self.connection.execute(
                    "insert into voucher_settlement_lines(voucher_id,voucher_entry_id,"
                    "target_type,target_id,amount,currency,source_event_id) "
                    "values(?,?,?,?,?,?,?)",
                    (voucher_id, entry_ids[item["entry_line_no"]],
                     item["target_type"], item["target_id"], item["amount"],
                     currency, event_id),
                )
            self.connection.execute(
                "update vouchers set status='posted',posted_by=?,posted_at=CURRENT_TIMESTAMP::text "
                "where id=? and status='draft'", (actor_id, voucher_id),
            )
        self.connection.execute(
            "insert into posting_audit(posting_event_id,voucher_id,action,old_value,new_value,"
            "reason_code,actor_id) values(?,?,?,?::jsonb,?::jsonb,?,?)",
            (event_id, voucher_id, "posted" if requires_gl else "recorded",
             json.dumps({"status": "draft"} if requires_gl else {}),
             json.dumps({"status": "posted"} if requires_gl else {"requires_gl": False}),
             reason_code, actor_id),
        )
        self.connection.execute("RELEASE SAVEPOINT accounting_event")
        return EventResult(event_id, voucher_id, digest, True)

    def mark_reversed(self, voucher_id, *, actor_id, reason_code):
        if not reason_code:
            raise ValueError("reason_code is required")
        if not actor_id:
            raise ValueError("actor_id is required")
        row = self.connection.execute(
            "select status from vouchers where id=?", (voucher_id,)
        ).fetchone()
        if not row or row[0] != "posted":
            raise PostingError("only posted vouchers can be marked reversed")
        self.connection.execute(
            "update vouchers set status='reversed',reversed_by=?,"
            "reversed_at=CURRENT_TIMESTAMP::text,reason_code=? where id=?",
            (actor_id, reason_code, voucher_id),
        )
        self.connection.execute(
            "insert into posting_audit(voucher_id,action,old_value,new_value,reason_code,actor_id) "
            "values(?,'reversed',?::jsonb,?::jsonb,?,?)",
            (voucher_id, json.dumps({"status": "posted"}),
             json.dumps({"status": "reversed"}), reason_code, actor_id),
        )
