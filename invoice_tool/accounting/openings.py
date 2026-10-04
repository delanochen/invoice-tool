"""Opening-balance cutover workflow."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .posting import EventKey, PostingLine, PostingService


CENT = Decimal("0.01")
REASON_CODES = {
    "prior_period_error",
    "post_cutover_new",
    "cutover_reclassification",
}


class OpeningBalanceError(RuntimeError):
    code = "opening_balance_error"


@dataclass(frozen=True)
class OpeningPostResult:
    cutover_id: int
    event_id: int
    voucher_id: int


def _money(value):
    amount = Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise OpeningBalanceError("opening amount must be positive")
    return amount


class OpeningBalanceService:
    def __init__(self, connection):
        self.connection = connection
        self.posting = PostingService(connection)

    def create_cutover(self, cutover_date, *, actor_id):
        if not actor_id:
            raise ValueError("actor_id is required")
        cutover_date = date.fromisoformat(str(cutover_date)).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        existing = self.connection.execute(
            "select id from accounting_opening_cutover where cutover_date=?",
            (cutover_date,),
        ).fetchone()
        if existing:
            return int(existing[0])
        cursor = self.connection.execute(
            "insert into accounting_opening_cutover(cutover_date,created_by) values(?,?)",
            (cutover_date, actor_id),
        )
        return int(cursor.lastrowid)

    def add_line(self, cutover_id, *, origin_type, origin_id, account_id,
                 amount, reason_code):
        origin_type = str(origin_type or "").strip()
        if not origin_type:
            raise OpeningBalanceError("origin type is required")
        try:
            origin_id = int(origin_id)
            account_id = int(account_id)
        except (TypeError, ValueError) as error:
            raise OpeningBalanceError("origin and account are required") from error
        if origin_id <= 0:
            raise OpeningBalanceError("origin id must be positive")
        if reason_code not in REASON_CODES:
            raise OpeningBalanceError("invalid opening reason code")
        amount = _money(amount)
        self.connection.execute("BEGIN IMMEDIATE")
        cutover = self.connection.execute(
            "select status from accounting_opening_cutover where id=?", (cutover_id,),
        ).fetchone()
        if not cutover or cutover[0] != "draft":
            raise OpeningBalanceError("only a draft cutover can be edited")
        account = self.connection.execute(
            "select account_code from accounts where id=? and is_active=true",
            (account_id,),
        ).fetchone()
        if not account:
            raise OpeningBalanceError("active account not found")
        if account[0] == "3000":
            raise OpeningBalanceError("opening equity is calculated automatically")
        cursor = self.connection.execute(
            "insert into accounting_opening_lines(cutover_id,origin_type,origin_id,"
            "account_id,amount,reason_code) values(?,?,?,?,?,?)",
            (cutover_id, origin_type, origin_id, account_id, str(amount), reason_code),
        )
        return int(cursor.lastrowid)

    def remove_line(self, line_id):
        self.connection.execute("BEGIN IMMEDIATE")
        row = self.connection.execute(
            "select l.id,c.status from accounting_opening_lines l "
            "join accounting_opening_cutover c on c.id=l.cutover_id where l.id=?",
            (line_id,),
        ).fetchone()
        if not row or row["status"] != "draft":
            raise OpeningBalanceError("only a draft opening line can be removed")
        self.connection.execute("delete from accounting_opening_lines where id=?", (line_id,))

    def post(self, cutover_id, *, actor_id):
        if not actor_id:
            raise ValueError("actor_id is required")
        self.connection.execute("BEGIN IMMEDIATE")
        self.connection.execute(
            "select pg_advisory_xact_lock(hashtextextended(?,0))",
            (f"opening-cutover:{cutover_id}",),
        )
        cutover = self.connection.execute(
            "select * from accounting_opening_cutover where id=?", (cutover_id,),
        ).fetchone()
        if not cutover:
            raise OpeningBalanceError("opening cutover not found")
        if cutover["status"] == "posted":
            event = self.connection.execute(
                "select id,voucher_id from posting_events where source_type='opening_cutover' "
                "and source_id=? and event_type='opening.established'",
                (cutover_id,),
            ).fetchone()
            return OpeningPostResult(cutover_id, int(event["id"]), int(event["voucher_id"]))
        lines = self.connection.execute(
            "select l.*,a.account_code,a.account_name,a.normal_balance "
            "from accounting_opening_lines l join accounts a on a.id=l.account_id "
            "where l.cutover_id=? order by l.id", (cutover_id,),
        ).fetchall()
        if not lines:
            raise OpeningBalanceError("opening cutover has no lines")
        posting_lines = []
        debit_total = Decimal("0")
        credit_total = Decimal("0")
        for row in lines:
            amount = Decimal(str(row["amount"]))
            debit = amount if row["normal_balance"] == "debit" else Decimal("0")
            credit = amount if row["normal_balance"] == "credit" else Decimal("0")
            debit_total += debit
            credit_total += credit
            posting_lines.append(PostingLine(
                row["account_code"], debit=debit, credit=credit,
                source_line_type="opening_line", source_line_id=row["id"],
                memo=f"Opening {row['origin_type']} #{row['origin_id']}",
            ))
        difference = debit_total - credit_total
        if difference:
            posting_lines.append(PostingLine(
                "3000", credit=difference if difference > 0 else 0,
                debit=-difference if difference < 0 else 0,
                source_line_type="opening_equity", source_line_id=cutover_id,
                memo="Opening balance equity",
            ))
        result = self.posting.post_event(
            key=EventKey("opening_cutover", cutover_id, 1, "opening.established", 1),
            business_anchor_type="opening_cutover", business_anchor_id=cutover_id,
            business_date=cutover["cutover_date"], accounting_date=cutover["cutover_date"],
            lines=tuple(posting_lines), description=f"Opening balances {cutover['cutover_date']}",
            source_snapshot={
                "cutover_id": cutover_id,
                "cutover_date": str(cutover["cutover_date"]),
                "line_count": len(lines),
            },
            actor_id=actor_id, reason_code="opening_established",
        )
        entry_rows = self.connection.execute(
            "select id,source_line_id from voucher_entries where voucher_id=? "
            "and source_line_type='opening_line'", (result.voucher_id,),
        ).fetchall()
        for entry in entry_rows:
            self.connection.execute(
                "update accounting_opening_lines set voucher_entry_id=? where id=?",
                (entry["id"], entry["source_line_id"]),
            )
        self.connection.execute(
            "update accounting_opening_cutover set status='posted',voucher_id=?,posted_by=?,"
            "posted_at=CURRENT_TIMESTAMP::text where id=? and status='draft'",
            (result.voucher_id, actor_id, cutover_id),
        )
        return OpeningPostResult(cutover_id, result.event_id, result.voucher_id)
