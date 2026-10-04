"""Accounting voucher, receipt, and prepayment screens."""

from datetime import date
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for

from .prepayments import CustomerPrepaymentService, PrepaymentApplicationError
from .periods import AccountingPeriodService, PeriodStateError
from .invoices import InvoiceRecognitionError, InvoiceRecognitionService
from .openings import OpeningBalanceError, OpeningBalanceService, REASON_CODES
from .reports import FinancialReportService
from .receipts import (
    CustomerReceiptService,
    ReceiptAllocation,
    ReceiptError,
)


def register_accounting_routes(app, api):
    login_required = api["login_required"]

    def require_view():
        user = api["g"].user
        if not user or user["role"] not in {"admin", "manager", "finance"}:
            abort(403)

    def require_edit(action="edit"):
        user = api["g"].user
        if (not user or user["role"] not in {"admin", "finance"} or
                not api["has_action_permission"]("accounting_receipts", action)):
            abort(403)

    @app.get("/finance/accounting/vouchers")
    @login_required
    def accounting_vouchers():
        require_view()
        clauses = ["1=1"]
        params = []
        status = request.args.get("status", "").strip()
        event_type = request.args.get("event_type", "").strip()
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        if status in {"draft", "posted", "reversed"}:
            clauses.append("v.status=?")
            params.append(status)
        if event_type:
            clauses.append("v.voucher_type=?")
            params.append(event_type)
        if date_from:
            clauses.append("v.accounting_date>=?")
            params.append(date_from)
        if date_to:
            clauses.append("v.accounting_date<=?")
            params.append(date_to)
        rows = api["db"]().execute(
            "select v.*,coalesce(sum(e.debit),0) as debit_total,"
            "coalesce(sum(e.credit),0) as credit_total,u.name as creator_name "
            "from vouchers v left join voucher_entries e on e.voucher_id=v.id "
            "left join users u on u.id=v.created_by where " + " and ".join(clauses) +
            " group by v.id,u.name order by v.accounting_date desc,v.id desc limit 300",
            params,
        ).fetchall()
        types = api["db"]().execute(
            "select distinct voucher_type from vouchers order by voucher_type"
        ).fetchall()
        return render_template(
            "accounting_vouchers.html", rows=rows, voucher_types=types,
            filters={"status": status, "event_type": event_type,
                     "date_from": date_from, "date_to": date_to},
        )

    @app.get("/finance/accounting/vouchers/<int:voucher_id>")
    @login_required
    def accounting_voucher_detail(voucher_id):
        require_view()
        voucher = api["db"]().execute(
            "select v.*,creator.name as creator_name,poster.name as poster_name,"
            "reverser.name as reverser_name from vouchers v "
            "left join users creator on creator.id=v.created_by "
            "left join users poster on poster.id=v.posted_by "
            "left join users reverser on reverser.id=v.reversed_by where v.id=?",
            (voucher_id,),
        ).fetchone()
        if not voucher:
            abort(404)
        entries = api["db"]().execute(
            "select e.*,a.account_code,a.account_name,a.account_type "
            "from voucher_entries e join accounts a on a.id=e.account_id "
            "where e.voucher_id=? order by e.line_no", (voucher_id,),
        ).fetchall()
        sources = api["db"]().execute(
            "select * from voucher_source_links where voucher_id=? order by id",
            (voucher_id,),
        ).fetchall()
        settlements = api["db"]().execute(
            "select s.*,e.line_no from voucher_settlement_lines s "
            "join voucher_entries e on e.id=s.voucher_entry_id "
            "where s.voucher_id=? order by s.id", (voucher_id,),
        ).fetchall()
        audits = api["db"]().execute(
            "select a.*,u.name as actor_name from posting_audit a "
            "left join users u on u.id=a.actor_id where a.voucher_id=? "
            "order by a.id desc", (voucher_id,),
        ).fetchall()
        return render_template(
            "accounting_voucher_detail.html", voucher=voucher, entries=entries,
            sources=sources, settlements=settlements, audits=audits,
        )

    @app.route("/finance/accounting/receipts", methods=["GET", "POST"])
    @login_required
    def accounting_receipts():
        require_view()
        if request.method == "POST":
            require_edit("create")
            try:
                customer_id = int(request.form.get("customer_id", ""))
                bank_account_id = int(request.form.get("bank_account_id", ""))
                invoice_id_text = request.form.get("invoice_id", "").strip()
                allocation_text = request.form.get("allocation_amount", "").strip()
                allocations = ()
                if invoice_id_text or allocation_text:
                    if not invoice_id_text or not allocation_text:
                        raise ValueError("发票和分配金额必须同时填写。")
                    allocations = (ReceiptAllocation(
                        int(invoice_id_text), Decimal(allocation_text)
                    ),)
                CustomerReceiptService(api["db"]()).receive(
                    receipt_no=request.form.get("receipt_no", "").strip(),
                    customer_id=customer_id,
                    receipt_date=request.form.get("receipt_date") or date.today().isoformat(),
                    amount=request.form.get("amount", ""),
                    bank_account_id=bank_account_id,
                    allocations=allocations,
                    prepayment_reason=request.form.get("prepayment_reason", "").strip(),
                    actor_id=api["g"].user["id"],
                    idempotency_key=request.form.get("idempotency_key", "").strip() or None,
                )
                api["db"]().commit()
                flash("客户收款已登记并完成过账。", "success")
                return redirect(url_for("accounting_receipts"))
            except (ReceiptError, ValueError) as error:
                api["db"]().rollback()
                flash(str(error), "error")
        rows = api["db"]().execute(
            "select r.*,c.name as customer_name,b.account_name,v.voucher_number,"
            "p.id as prepayment_id,p.status as prepayment_status,"
            "coalesce(sum(case when a.status='active' then a.amount else 0 end),0) "
            "as allocated_amount from customer_receipts r "
            "join clients c on c.id=r.customer_id join bank_accounts b on b.id=r.bank_account_id "
            "left join vouchers v on v.id=r.voucher_id "
            "left join receipt_allocations a on a.receipt_id=r.id "
            "left join customer_prepayments p on p.receipt_id=r.id "
            "group by r.id,c.name,b.account_name,v.voucher_number,p.id,p.status "
            "order by r.receipt_date desc,r.id desc limit 300"
        ).fetchall()
        customers = api["db"]().execute(
            "select id,name from clients order by name"
        ).fetchall()
        banks = api["db"]().execute(
            "select id,account_name from bank_accounts where is_active=1 and currency='USD' "
            "order by account_name"
        ).fetchall()
        invoices = api["db"]().execute(
            "select i.id,i.invoice_number,i.client_id,"
            "coalesce(sum(ii.amount*(1+ii.tax_rate/100.0)),0)+"
            "coalesce((select sum(c.revenue_delta+c.sales_tax_delta) "
            "from invoice_accounting_corrections c where c.invoice_id=i.id "
            "and c.status='posted'),0) as total "
            "from invoices i left join invoice_items ii on ii.invoice_id=i.id "
            "where i.status='completed' and i.currency='USD' "
            "group by i.id order by i.issue_date desc,i.id desc"
        ).fetchall()
        return render_template(
            "accounting_receipts.html", rows=rows, customers=customers,
            banks=banks, invoices=invoices,
            can_edit=(api["g"].user["role"] in {"admin", "finance"} and
                      api["has_action_permission"]("accounting_receipts", "create")),
            today=date.today().isoformat(),
        )

    @app.route("/finance/accounting/prepayments/<int:prepayment_id>/apply",
               methods=["GET", "POST"])
    @login_required
    def accounting_prepayment_apply(prepayment_id):
        require_edit()
        prepayment = api["db"]().execute(
            "select p.*,c.name as customer_name,r.receipt_no from customer_prepayments p "
            "join clients c on c.id=p.customer_id "
            "join customer_receipts r on r.id=p.receipt_id where p.id=?",
            (prepayment_id,),
        ).fetchone()
        if not prepayment:
            abort(404)
        if request.method == "POST":
            try:
                CustomerPrepaymentService(api["db"]()).apply(
                    prepayment_id=prepayment_id,
                    invoice_id=int(request.form.get("invoice_id", "")),
                    amount=request.form.get("amount", ""),
                    accounting_date=request.form.get("accounting_date") or date.today().isoformat(),
                    actor_id=api["g"].user["id"],
                )
                api["db"]().commit()
                flash("预收款已应用到发票。", "success")
                return redirect(url_for("accounting_receipts"))
            except (PrepaymentApplicationError, ValueError) as error:
                api["db"]().rollback()
                flash(str(error), "error")
        invoices = api["db"]().execute(
            "select i.id,i.invoice_number,coalesce(sum(ii.amount*(1+ii.tax_rate/100.0)),0)+"
            "coalesce((select sum(c.revenue_delta+c.sales_tax_delta) "
            "from invoice_accounting_corrections c where c.invoice_id=i.id "
            "and c.status='posted'),0) as total "
            "from invoices i left join invoice_items ii on ii.invoice_id=i.id "
            "where i.client_id=? and i.status='completed' and i.currency='USD' "
            "group by i.id order by i.issue_date desc,i.id desc",
            (prepayment["customer_id"],),
        ).fetchall()
        used = api["db"]().execute(
            "select coalesce(sum(amount),0) from customer_prepayment_applications "
            "where prepayment_id=? and status='active'", (prepayment_id,),
        ).fetchone()[0]
        return render_template(
            "accounting_prepayment_apply.html", prepayment=prepayment,
            invoices=invoices,
            remaining=Decimal(str(prepayment["original_amount"])) - Decimal(str(used)),
            today=date.today().isoformat(),
        )

    @app.route("/finance/accounting/periods", methods=["GET", "POST"])
    @login_required
    def accounting_periods():
        require_view()
        if request.method == "POST":
            action = request.form.get("action", "").strip()
            permission = {"create": "create", "close": "close", "reopen": "reopen"}.get(action)
            if not permission or not api["has_action_permission"](
                    "accounting_periods", permission):
                abort(403)
            service = AccountingPeriodService(api["db"]())
            try:
                if action == "create":
                    month = request.form.get("month", "").strip()
                    if not month:
                        raise ValueError("请选择会计月份。")
                    service.create_month(month + "-01", actor_id=api["g"].user["id"])
                    message = "会计期间已创建。"
                elif action == "close":
                    service.close(
                        int(request.form.get("period_id", "")),
                        actor_id=api["g"].user["id"],
                        reason_code=request.form.get("reason_code", "").strip() or "period_close",
                    )
                    message = "会计期间已关账。"
                else:
                    service.reopen(
                        int(request.form.get("period_id", "")),
                        actor_id=api["g"].user["id"],
                        reason_code=request.form.get("reason_code", "").strip(),
                    )
                    message = "会计期间已重开。"
                api["db"]().commit()
                flash(message, "success")
                return redirect(url_for("accounting_periods"))
            except (PeriodStateError, ValueError) as error:
                api["db"]().rollback()
                flash(str(error), "error")
        periods = api["db"]().execute(
            "select p.*,closer.name as closer_name,reopener.name as reopener_name "
            "from accounting_periods p left join users closer on closer.id=p.closed_by "
            "left join users reopener on reopener.id=p.reopened_by "
            "order by p.period_start desc"
        ).fetchall()
        period_service = AccountingPeriodService(api["db"]())
        close_checks = {
            row["id"]: period_service.close_check(row["id"], period=row)
            for row in periods if row["status"] == "open"
        }
        return render_template(
            "accounting_periods.html", periods=periods,
            close_checks=close_checks,
            can_create=api["has_action_permission"]("accounting_periods", "create"),
            can_close=api["has_action_permission"]("accounting_periods", "close"),
            can_reopen=api["has_action_permission"]("accounting_periods", "reopen"),
            current_month=date.today().strftime("%Y-%m"),
        )

    @app.route("/finance/accounting/opening-balances", methods=["GET", "POST"])
    @login_required
    def accounting_opening_balances():
        if not api["has_action_permission"]("accounting_opening", "view"):
            abort(403)
        service = OpeningBalanceService(api["db"]())
        if request.method == "POST":
            action = request.form.get("action", "").strip()
            permission = "post" if action == "post" else "create"
            if not api["has_action_permission"]("accounting_opening", permission):
                abort(403)
            try:
                if action == "create":
                    cutover_id = service.create_cutover(
                        request.form.get("cutover_date", ""),
                        actor_id=api["g"].user["id"],
                    )
                    message = "期初批次已创建。"
                elif action == "add_line":
                    cutover_id = int(request.form.get("cutover_id", ""))
                    service.add_line(
                        cutover_id,
                        origin_type=request.form.get("origin_type", ""),
                        origin_id=request.form.get("origin_id", ""),
                        account_id=request.form.get("account_id", ""),
                        amount=request.form.get("amount", ""),
                        reason_code=request.form.get("reason_code", ""),
                    )
                    message = "期初余额行已添加。"
                elif action == "remove_line":
                    cutover_id = int(request.form.get("cutover_id", ""))
                    service.remove_line(int(request.form.get("line_id", "")))
                    message = "期初余额行已移除。"
                elif action == "post":
                    cutover_id = int(request.form.get("cutover_id", ""))
                    service.post(cutover_id, actor_id=api["g"].user["id"])
                    message = "期初余额已过账，后续不可修改。"
                else:
                    raise ValueError("unknown opening action")
                api["db"]().commit()
                flash(message, "success")
                return redirect(url_for("accounting_opening_balances", cutover_id=cutover_id))
            except (OpeningBalanceError, ValueError) as error:
                api["db"]().rollback()
                flash(str(error), "error")
        cutovers = api["db"]().execute(
            "select c.*,v.voucher_number,u.name as creator_name,p.name as poster_name "
            "from accounting_opening_cutover c left join vouchers v on v.id=c.voucher_id "
            "left join users u on u.id=c.created_by left join users p on p.id=c.posted_by "
            "order by c.cutover_date desc,c.id desc"
        ).fetchall()
        selected_id = request.args.get("cutover_id", "").strip()
        selected = None
        if selected_id.isdigit():
            selected = next((row for row in cutovers if row["id"] == int(selected_id)), None)
        if selected is None and cutovers:
            selected = cutovers[0]
        lines = []
        if selected:
            lines = api["db"]().execute(
                "select l.*,a.account_code,a.account_name,a.normal_balance "
                "from accounting_opening_lines l join accounts a on a.id=l.account_id "
                "where l.cutover_id=? order by l.id", (selected["id"],),
            ).fetchall()
        accounts = api["db"]().execute(
            "select id,account_code,account_name,normal_balance from accounts "
            "where is_active=true and account_code<>'3000' order by account_code"
        ).fetchall()
        return render_template(
            "accounting_opening_balances.html", cutovers=cutovers, selected=selected,
            lines=lines, accounts=accounts, reason_codes=sorted(REASON_CODES),
            today=date.today().isoformat(),
            can_create=api["has_action_permission"]("accounting_opening", "create"),
            can_post=api["has_action_permission"]("accounting_opening", "post"),
        )

    @app.get("/finance/accounting/trial-balance")
    @login_required
    def accounting_trial_balance():
        require_view()
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        join_conditions = ["e.account_id=a.id", "v.status in ('posted','reversed')"]
        params = []
        if date_from:
            join_conditions.append("v.accounting_date>=?")
            params.append(date_from)
        if date_to:
            join_conditions.append("v.accounting_date<=?")
            params.append(date_to)
        rows = api["db"]().execute(
            "select a.id,a.account_code,a.account_name,a.account_type,a.normal_balance,"
            "coalesce(sum(case when v.id is not null then e.debit else 0 end),0) as debit_total,"
            "coalesce(sum(case when v.id is not null then e.credit else 0 end),0) as credit_total "
            "from accounts a left join voucher_entries e on e.account_id=a.id "
            "left join vouchers v on v.id=e.voucher_id and " +
            " and ".join(join_conditions[1:]) +
            " where a.is_active=true group by a.id order by a.account_code",
            params,
        ).fetchall()
        debit_total = sum(Decimal(str(row["debit_total"])) for row in rows)
        credit_total = sum(Decimal(str(row["credit_total"])) for row in rows)
        return render_template(
            "accounting_trial_balance.html", rows=rows,
            debit_total=debit_total, credit_total=credit_total,
            balanced=debit_total == credit_total,
            filters={"date_from": date_from, "date_to": date_to},
        )

    @app.get("/finance/accounting/income-statement")
    @login_required
    def accounting_income_statement():
        require_view()
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        report = FinancialReportService(api["db"]()).income_statement(
            date_from=date_from or None, date_to=date_to or None,
        )
        return render_template(
            "accounting_income_statement.html", report=report,
            filters={"date_from": date_from, "date_to": date_to},
        )

    @app.get("/finance/accounting/balance-sheet")
    @login_required
    def accounting_balance_sheet():
        require_view()
        as_of = request.args.get("as_of", "").strip()
        report = FinancialReportService(api["db"]()).balance_sheet(as_of=as_of or None)
        return render_template(
            "accounting_balance_sheet.html", report=report, as_of=as_of,
        )

    @app.get("/finance/accounting/accounts/<int:account_id>/ledger")
    @login_required
    def accounting_account_ledger(account_id):
        require_view()
        account = api["db"]().execute(
            "select * from accounts where id=?", (account_id,)
        ).fetchone()
        if not account:
            abort(404)
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        opening = Decimal("0")
        if date_from:
            opening_row = api["db"]().execute(
                "select coalesce(sum(e.debit-e.credit),0) from voucher_entries e "
                "join vouchers v on v.id=e.voucher_id where e.account_id=? "
                "and v.status in ('posted','reversed') and v.accounting_date<?",
                (account_id, date_from),
            ).fetchone()
            opening = Decimal(str(opening_row[0]))
        clauses = ["e.account_id=?", "v.status in ('posted','reversed')"]
        params = [account_id]
        if date_from:
            clauses.append("v.accounting_date>=?")
            params.append(date_from)
        if date_to:
            clauses.append("v.accounting_date<=?")
            params.append(date_to)
        source_rows = api["db"]().execute(
            "select e.*,v.voucher_number,v.voucher_type,v.status as voucher_status,"
            "v.accounting_date,v.business_date,v.description "
            "from voucher_entries e join vouchers v on v.id=e.voucher_id where " +
            " and ".join(clauses) +
            " order by v.accounting_date,v.id,e.line_no", params,
        ).fetchall()
        running = opening
        rows = []
        for row in source_rows:
            running += Decimal(str(row["debit"])) - Decimal(str(row["credit"]))
            rows.append({"row": row, "running": running})
        return render_template(
            "accounting_account_ledger.html", account=account, rows=rows,
            opening=opening, closing=running,
            filters={"date_from": date_from, "date_to": date_to},
        )

    @app.route("/finance/accounting/invoices/<int:invoice_id>/correct",
               methods=["GET", "POST"])
    @login_required
    def accounting_invoice_correct(invoice_id):
        if not api["has_action_permission"]("accounting_corrections", "create"):
            abort(403)
        invoice = api["db"]().execute(
            "select i.*,c.name as customer_name from invoices i "
            "join clients c on c.id=i.client_id where i.id=?", (invoice_id,),
        ).fetchone()
        if not invoice:
            abort(404)
        if request.method == "POST":
            try:
                InvoiceRecognitionService(api["db"]()).correct(
                    invoice_id,
                    revenue_delta=request.form.get("revenue_delta", "0"),
                    sales_tax_delta=request.form.get("sales_tax_delta", "0"),
                    accounting_date=request.form.get("accounting_date") or date.today().isoformat(),
                    reason_code=request.form.get("reason_code", "").strip(),
                    actor_id=api["g"].user["id"],
                )
                api["db"]().commit()
                flash("发票差额更正已过账。", "success")
                return redirect(url_for("accounting_invoice_correct", invoice_id=invoice_id))
            except (InvoiceRecognitionError, ValueError) as error:
                api["db"]().rollback()
                flash(str(error), "error")
        corrections = api["db"]().execute(
            "select c.*,v.voucher_number,rv.voucher_number as reversal_voucher_number,"
            "u.name as creator_name,ru.name as reverser_name "
            "from invoice_accounting_corrections c "
            "left join vouchers v on v.id=c.voucher_id "
            "left join vouchers rv on rv.id=c.reversal_voucher_id "
            "left join users u on u.id=c.created_by "
            "left join users ru on ru.id=c.reversed_by where c.invoice_id=? "
            "order by c.correction_no desc", (invoice_id,),
        ).fetchall()
        base_total = api["db"]().execute(
            "select coalesce(sum(amount*(1+tax_rate/100.0)),0) from invoice_items "
            "where invoice_id=?", (invoice_id,),
        ).fetchone()[0]
        correction_total = api["db"]().execute(
            "select coalesce(sum(revenue_delta+sales_tax_delta),0) "
            "from invoice_accounting_corrections where invoice_id=? and status='posted'",
            (invoice_id,),
        ).fetchone()[0]
        return render_template(
            "accounting_invoice_correct.html", invoice=invoice,
            corrections=corrections, base_total=base_total,
            corrected_total=Decimal(str(base_total)) + Decimal(str(correction_total)),
            today=date.today().isoformat(),
        )

    @app.post("/finance/accounting/invoice-corrections/<int:correction_id>/reverse")
    @login_required
    def accounting_invoice_correction_reverse(correction_id):
        if not api["has_action_permission"]("accounting_corrections", "create"):
            abort(403)
        correction = api["db"]().execute(
            "select invoice_id from invoice_accounting_corrections where id=?",
            (correction_id,),
        ).fetchone()
        if not correction:
            abort(404)
        try:
            InvoiceRecognitionService(api["db"]()).reverse_correction(
                correction_id,
                accounting_date=request.form.get("accounting_date") or date.today().isoformat(),
                reason_code=request.form.get("reason_code", "").strip(),
                actor_id=api["g"].user["id"],
            )
            api["db"]().commit()
            flash("发票更正已通过反向凭证撤销。", "success")
        except (InvoiceRecognitionError, ValueError) as error:
            api["db"]().rollback()
            flash(str(error), "error")
        return redirect(url_for(
            "accounting_invoice_correct", invoice_id=correction["invoice_id"]
        ))

    return {
        "accounting_vouchers": accounting_vouchers,
        "accounting_voucher_detail": accounting_voucher_detail,
        "accounting_receipts": accounting_receipts,
        "accounting_prepayment_apply": accounting_prepayment_apply,
        "accounting_periods": accounting_periods,
        "accounting_opening_balances": accounting_opening_balances,
        "accounting_trial_balance": accounting_trial_balance,
        "accounting_income_statement": accounting_income_statement,
        "accounting_balance_sheet": accounting_balance_sheet,
        "accounting_account_ledger": accounting_account_ledger,
        "accounting_invoice_correct": accounting_invoice_correct,
        "accounting_invoice_correction_reverse": accounting_invoice_correction_reverse,
    }
