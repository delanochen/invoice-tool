"""Quotation routes and form handling.

Registered via ``register_quotation_routes(app, api)`` following the repo
feature-module pattern (rate_engine / payroll / knowledge): ``api`` is app.py's
globals(), so this module never imports app.py.

Endpoints:
    GET  /quotations                    list (q + status filters)
    GET  /quotations/new                new form
    POST /quotations/new                create
    GET  /quotations/<id>               detail
    GET  /quotations/<id>/edit          edit form
    POST /quotations/<id>/edit          update
    POST /quotations/<id>/delete        delete
    GET  /quotations/<id>/pdf           inline PDF (template layout)
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import abort, flash, redirect, render_template, request, send_file

from .documents import (
    INVOICE_FREQUENCY_LABELS,
    PAYMENT_TERMS_LABELS,
    PRICING_LINES,
    PRICING_TYPE_LABELS,
    QUOTATION_STATUS_LABELS,
    RATE_SCHEDULE_LINES,
    build_quotation_pdf,
)

_QUOTATION_COLUMNS = (
    "quotation_number, quotation_date, valid_until, prepared_by, customer, contact, "
    "project_name, project_no, project_location, po_no, status, currency, client_id, "
    "project_description, scope_1, scope_2, scope_3, pricing_lines, subtotal, tax, total, "
    "rate_schedule, crew_size, workdays, hours_per_day, expected_start_date, "
    "expected_completion, normal_working_hours, customer_provides, assumptions_other, "
    "payment_terms, payment_terms_other, quotation_validity, invoice_frequency, "
    "pricing_type, tax_note, notes"
)
# created_by / created_at / updated_at 由后端补充，不属于表单。
_FORM_COLUMNS = _QUOTATION_COLUMNS

# 报价单费率表 key → 合同费率版本 rate_type 映射。
# 合同与报价单费率口径统一：报价单定价汇总/费率表的费率自动从客户的
# 当前生效合同费率版本带出（带不出的行允许手填）。
QUOTATION_RATE_TO_CONTRACT = {
    "regular_labor": "regular_hours",
    "overtime_labor": "overtime_hours",
    "holiday_labor": "holiday_hours",
    "travel_time": "travel_hours",
    "waiting_standby": "waiting_standby_hours",
    "technical_support": "technical_support_hours",
    "mileage": "mileage",
    "lodging": "lodging_cap",
    "per_diem": "per_diem",
}


def client_contract_rates(api, client_id):
    """按客户当前生效的合同费率版本取费率，映射到报价单费率表 key。

    取该客户全部合同中今天生效的最新费率版本（跨合同合并，最新版本优先）；
    版本缺条目或数值为空时对应 key 不出现在结果里（表单留空，允许手填）。
    """
    if not client_id:
        return {}
    today = date.today().isoformat()
    version = api["db"]().execute(
        """
        select contract_rate_versions.id, contract_rate_versions.version_no
        from contract_rate_versions
        join contracts on contracts.id = contract_rate_versions.contract_id
        where contracts.client_id = ?
          and contract_rate_versions.status = 'active'
          and contract_rate_versions.effective_from <= ?
          and (contract_rate_versions.effective_to is null
               or contract_rate_versions.effective_to = ''
               or contract_rate_versions.effective_to >= ?)
        order by contract_rate_versions.effective_from desc,
                 contract_rate_versions.version_no desc,
                 contract_rate_versions.id desc
        limit 1
        """,
        (client_id, today, today),
    ).fetchone()
    if not version:
        return {}
    items = api["db"]().execute(
        "select rate_type, rate from contract_rate_items where version_id = ?",
        (version["id"],),
    ).fetchall()
    by_type = {item["rate_type"]: item["rate"] for item in items}
    return {
        key: float(by_type[rate_type])
        for key, rate_type in QUOTATION_RATE_TO_CONTRACT.items()
        if rate_type in by_type and by_type[rate_type] is not None
    }


def _decimal(value, default=None):
    """Parse a form number into Decimal, tolerating empty/invalid input."""
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return default


def _fmt_decimal(value):
    if value in (None, ""):
        return ""
    return f"{Decimal(str(value)):.2f}"


def can_view_quotations(api):
    return bool(api["g"].user) and api["has_action_permission"]("quotations", "view")


def can_manage_quotations(api):
    user = api["g"].user
    if not user:
        return False
    return any(
        api["has_action_permission"]("quotations", action)
        for action in ("create", "edit", "delete")
    )


def quotation_form_values(api, quotation=None):
    """Parse the quotation form into a dict of database column values.

    Pricing lines: each fixed row keeps qty / rate / amount; amount defaults to
    qty x rate when both are present. Subtotal / total are always recomputed
    from the parsed rows so the PDF and the list never drift.
    """
    form = request.form

    def field(name, default=""):
        return str(form.get(name, default)).strip()

    pricing_type = field("pricing_type", "estimated") or "estimated"
    if pricing_type == "fixed":
        # 总价合同（Fixed Price）：不逐行填报，直接给合同总价；
        # subtotal = 总价 - 税，明细与费率表不保留。
        total = _decimal(field("total"), Decimal("0")) or Decimal("0")
        total = total.quantize(Decimal("0.01"))
        tax = _decimal(field("tax"), Decimal("0")) or Decimal("0")
        tax = tax.quantize(Decimal("0.01"))
        subtotal = (total - tax).quantize(Decimal("0.01"))
        pricing_lines = []
        rate_schedule = []
    else:
        pricing_lines = []
        for key, label, unit in PRICING_LINES:
            qty = field(f"pricing_qty_{key}")
            rate = field(f"pricing_rate_{key}")
            amount_input = field(f"pricing_amount_{key}")
            qty_decimal = _decimal(qty)
            rate_decimal = _decimal(rate)
            amount_decimal = _decimal(amount_input)
            if amount_decimal is None and qty_decimal is not None and rate_decimal is not None:
                amount_decimal = (qty_decimal * rate_decimal).quantize(Decimal("0.01"))
            pricing_lines.append({
                "key": key,
                "label": label,
                "unit": unit,
                "qty": qty,
                "rate": rate,
                "amount": "" if amount_decimal is None else _fmt_decimal(amount_decimal),
            })

        rate_schedule = []
        for key, label, unit in RATE_SCHEDULE_LINES:
            rate = field(f"rate_{key}")
            rate_schedule.append({
                "key": key,
                "label": label,
                "unit": unit,
                "rate": rate,
            })

        subtotal = sum(
            (Decimal(str(line["amount"])) for line in pricing_lines if line["amount"]),
            Decimal("0"),
        ).quantize(Decimal("0.01"))
        tax = _decimal(field("tax"), Decimal("0")) or Decimal("0")
        tax = tax.quantize(Decimal("0.01"))
        total = (subtotal + tax).quantize(Decimal("0.01"))
    client_id = field("client_id")
    return {
        "quotation_number": field("quotation_number"),
        "quotation_date": field("quotation_date"),
        "valid_until": field("valid_until"),
        "prepared_by": field("prepared_by"),
        "customer": field("customer"),
        "contact": field("contact"),
        "project_name": field("project_name"),
        "project_no": field("project_no"),
        "project_location": field("project_location"),
        "po_no": field("po_no"),
        "status": field("status", "draft"),
        "currency": field("currency", "USD") or "USD",
        "client_id": int(client_id) if client_id.isdigit() else None,
        "project_description": field("project_description"),
        "scope_1": field("scope_1"),
        "scope_2": field("scope_2"),
        "scope_3": field("scope_3"),
        "pricing_lines": json.dumps(pricing_lines, ensure_ascii=False),
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "rate_schedule": json.dumps(rate_schedule, ensure_ascii=False),
        "crew_size": field("crew_size"),
        "workdays": field("workdays"),
        "hours_per_day": field("hours_per_day"),
        "expected_start_date": field("expected_start_date"),
        "expected_completion": field("expected_completion"),
        "normal_working_hours": field("normal_working_hours"),
        "customer_provides": ",".join(
            value for value in form.getlist("customer_provides")
            if value in {key for key, _label in (
                ("access", "Access"), ("escort", "Escort"), ("loto", "LOTO"), ("permits", "Permits")
            )}
        ),
        "assumptions_other": field("assumptions_other"),
        "payment_terms": field("payment_terms", "net_30") or "net_30",
        "payment_terms_other": field("payment_terms_other"),
        "quotation_validity": field("quotation_validity", "30 calendar days") or "30 calendar days",
        "invoice_frequency": field("invoice_frequency", "upon_completion") or "upon_completion",
        "pricing_type": field("pricing_type", "estimated") or "estimated",
        "tax_note": field("tax_note", "Excluded unless stated") or "Excluded unless stated",
        "notes": field("notes"),
    }


def next_quotation_number(api):
    """编号规则与工单同构：QT + YYMM + 3 位序号，取最小未用序号（工单为 SO + YYMM）。"""
    db = api["db"]
    api["lock_number_allocation"](db())
    prefix = f"QT{date.today():%y%m}"
    rows = db().execute(
        """
        select quotation_number from quotations
        where quotation_number like ?
        order by quotation_number
        """,
        (f"{prefix}%",),
    ).fetchall()
    used = set()
    for row in rows:
        suffix = row["quotation_number"][len(prefix):]
        if suffix.isdigit() and int(suffix) > 0:
            used.add(int(suffix))
    sequence = 1
    while sequence in used:
        sequence += 1
    return f"{prefix}{sequence:03d}"


def _quotation_or_404(api, quotation_id):
    quotation = api["db"]().execute(
        """
        select quotations.*, users.name as creator_name, clients.name as client_name,
               clients.client_number
        from quotations
        left join users on users.id = quotations.created_by
        left join clients on clients.id = quotations.client_id
        where quotations.id = ?
        """,
        (quotation_id,),
    ).fetchone()
    if not quotation:
        abort(404)
    return quotation


def _render_form(api, quotation, clients, form_title, defaults=None, errors=None):
    if defaults is None:
        defaults = dict(quotation) if quotation else {}
    pricing = {}
    try:
        raw = defaults.get("pricing_lines") or "[]"
        if isinstance(raw, str):
            raw = json.loads(raw) if raw.strip() else []
        pricing = {str(item.get("key")): item for item in raw if isinstance(item, dict)}
    except (ValueError, TypeError):
        pricing = {}
    rates = {}
    try:
        raw = defaults.get("rate_schedule") or "[]"
        if isinstance(raw, str):
            raw = json.loads(raw) if raw.strip() else []
        rates = {str(item.get("key")): item for item in raw if isinstance(item, dict)}
    except (ValueError, TypeError):
        rates = {}
    return render_template(
        "quotation_form.html",
        quotation=quotation,
        clients=clients,
        defaults=defaults,
        pricing=pricing,
        rates=rates,
        contract_rates=client_contract_rates(
            api, defaults.get("client_id") or (quotation or {}).get("client_id")
        ),
        pricing_lines=PRICING_LINES,
        rate_schedule_lines=RATE_SCHEDULE_LINES,
        payment_terms_labels=PAYMENT_TERMS_LABELS,
        invoice_frequency_labels=INVOICE_FREQUENCY_LABELS,
        pricing_type_labels=PRICING_TYPE_LABELS,
        status_labels=QUOTATION_STATUS_LABELS,
        form_title=form_title,
        errors=errors or [],
    )


def register_quotation_routes(app, api):
    login_required = api["login_required"]

    @app.get("/quotations")
    @login_required
    def quotations():
        if not can_view_quotations(api):
            abort(403)
        q = request.args.get("q", "").strip()
        status = request.args.get("status", "").strip()
        clauses = ["1 = 1"]
        params = []
        if q:
            clauses.append(
                "(quotations.quotation_number like ? or quotations.customer like ? "
                "or quotations.project_name like ? or quotations.contact like ?)"
            )
            params.extend([f"%{q}%"] * 4)
        if status in QUOTATION_STATUS_LABELS:
            clauses.append("quotations.status = ?")
            params.append(status)
        rows = api["db"]().execute(
            f"""
            select quotations.*, users.name as creator_name, clients.name as client_name
            from quotations
            left join users on users.id = quotations.created_by
            left join clients on clients.id = quotations.client_id
            where {" and ".join(clauses)}
            order by quotations.quotation_date desc, quotations.id desc
            """,
            params,
        ).fetchall()
        return render_template(
            "quotations.html",
            quotations=rows,
            q=q,
            selected_status=status,
            status_labels=QUOTATION_STATUS_LABELS,
        )

    @app.get("/quotations/rates")
    @login_required
    def quotation_client_rates():
        """报价单表单按客户自动带出合同费率（映射到报价单费率表 key）。"""
        if not can_view_quotations(api):
            abort(403)
        client_id = request.args.get("client_id", "").strip()
        if not client_id.isdigit():
            return api["jsonify"]({"rates": {}})
        return api["jsonify"]({"rates": client_contract_rates(api, int(client_id))})

    @app.route("/quotations/new", methods=["GET", "POST"])
    @login_required
    def new_quotation():
        if not api["has_action_permission"]("quotations", "create"):
            abort(403)
        clients = api["db"]().execute(
            "select * from clients order by client_number, name"
        ).fetchall()
        if request.method == "POST":
            values = quotation_form_values(api)
            if not values["quotation_number"]:
                values["quotation_number"] = next_quotation_number(api)
            if not values["quotation_date"]:
                flash("请填写报价日期。", "error")
                return _render_form(api, None, clients, "新建报价单", defaults=request.form, errors=["报价日期必填"])
            columns = [column.strip() for column in _FORM_COLUMNS.split(",")]
            values.update({
                "created_by": api["g"].user["id"],
                "created_at": api["now"](),
                "updated_at": api["now"](),
            })
            cursor = api["db"]().execute(
                f"""
                insert into quotations (
                    {", ".join(columns)}, created_by, created_at, updated_at
                ) values ({", ".join("?" for _ in columns)}, ?, ?, ?)
                """,
                tuple(values[column] for column in columns)
                + (values["created_by"], values["created_at"], values["updated_at"]),
            )
            quotation_id = cursor.lastrowid
            api["log_action"](
                "create",
                "quotation",
                quotation_id,
                values["quotation_number"],
                f"{values['customer'] or values['project_name'] or ''}".strip(),
            )
            api["db"]().commit()
            flash("报价单已创建。", "success")
            return redirect(api["url_for"]("quotation_detail", quotation_id=quotation_id))
        defaults = dict(request.form) if request.method == "POST" else {
            "quotation_number": next_quotation_number(api),
            "quotation_date": str(date.today()),
            "valid_until": str(date.today() + timedelta(days=30)),
            "status": "draft",
            "currency": "USD",
            "payment_terms": "net_30",
            "invoice_frequency": "upon_completion",
            "pricing_type": "estimated",
        }
        return _render_form(api, None, clients, "新建报价单", defaults=defaults)

    @app.get("/quotations/<int:quotation_id>")
    @login_required
    def quotation_detail(quotation_id):
        if not can_view_quotations(api):
            abort(403)
        quotation = _quotation_or_404(api, quotation_id)
        view = dict(quotation)
        for column, target in (("pricing_lines", "pricing"), ("rate_schedule", "rates")):
            try:
                raw = view.get(column) or "[]"
                view[target] = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw or [])
            except (ValueError, TypeError):
                view[target] = []
        return render_template(
            "quotation_detail.html",
            quotation=view,
            status_labels=QUOTATION_STATUS_LABELS,
            payment_terms_labels=PAYMENT_TERMS_LABELS,
            invoice_frequency_labels=INVOICE_FREQUENCY_LABELS,
            pricing_type_labels=PRICING_TYPE_LABELS,
        )

    @app.route("/quotations/<int:quotation_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_quotation(quotation_id):
        if not api["has_action_permission"]("quotations", "edit"):
            abort(403)
        quotation = _quotation_or_404(api, quotation_id)
        clients = api["db"]().execute(
            "select * from clients order by client_number, name"
        ).fetchall()
        if request.method == "POST":
            values = quotation_form_values(api, quotation)
            if not values["quotation_number"] or not values["quotation_date"]:
                flash("请填写报价单编号和报价日期。", "error")
                return _render_form(api, quotation, clients, "编辑报价单", defaults=request.form, errors=["编号与日期必填"])
            columns = [column.strip() for column in _FORM_COLUMNS.split(",")]
            api["db"]().execute(
                f"""
                update quotations set {", ".join(f"{column} = ?" for column in columns)}, updated_at = ? where id = ?
                """,
                (tuple(values[column] for column in columns) + (api["now"](), quotation_id)),
            )
            api["log_action"](
                "update",
                "quotation",
                quotation_id,
                values["quotation_number"],
                "修改报价单",
            )
            api["db"]().commit()
            flash("报价单已更新。", "success")
            return redirect(api["url_for"]("quotation_detail", quotation_id=quotation_id))
        defaults = dict(quotation)
        return _render_form(api, quotation, clients, "编辑报价单", defaults=defaults)

    @app.post("/quotations/<int:quotation_id>/delete")
    @login_required
    def delete_quotation(quotation_id):
        if not api["has_action_permission"]("quotations", "delete"):
            abort(403)
        quotation = _quotation_or_404(api, quotation_id)
        linked = api["db"]().execute(
            "select count(*) as count from service_orders where quotation_id = ?",
            (quotation_id,),
        ).fetchone()["count"]
        if linked:
            flash("该报价单已被工单引用，不能删除；可将状态改为已作废。", "error")
            return redirect(api["url_for"]("quotation_detail", quotation_id=quotation_id))
        api["db"]().execute("delete from quotations where id = ?", (quotation_id,))
        api["log_action"](
            "delete",
            "quotation",
            quotation_id,
            quotation["quotation_number"],
            quotation["customer"] or quotation["project_name"] or "",
        )
        api["db"]().commit()
        flash("报价单已删除。", "success")
        return redirect(api["url_for"]("quotations"))

    @app.get("/quotations/<int:quotation_id>/pdf")
    @login_required
    def quotation_pdf(quotation_id):
        if not can_view_quotations(api):
            abort(403)
        quotation = _quotation_or_404(api, quotation_id)
        from io import BytesIO

        buffer = BytesIO()
        build_quotation_pdf(dict(quotation), buffer)
        buffer.seek(0)
        return send_file(
            buffer,
            mimetype="application/pdf",
            as_attachment=False,
            download_name=f"{quotation['quotation_number']}.pdf",
            max_age=0,
        )

    return {
        "can_view_quotations": can_view_quotations,
        "can_manage_quotations": can_manage_quotations,
        "next_quotation_number": next_quotation_number,
        "quotations": quotations,
        "new_quotation": new_quotation,
        "quotation_detail": quotation_detail,
        "edit_quotation": edit_quotation,
        "delete_quotation": delete_quotation,
        "quotation_pdf": quotation_pdf,
    }
