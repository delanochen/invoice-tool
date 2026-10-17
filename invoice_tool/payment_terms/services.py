"""Payment-term queries and form validation, preserving legacy behavior."""
from flask import request


def build_payment_term_services(*, db, resolve_default_payment_term_id, resolve_calculate_payment_due_date):
    def payment_term_rows(active_only=False):
        where = "where is_active = 1" if active_only else ""
        return db().execute(
            f"select * from payment_terms {where} order by is_active desc, name collate nocase"
        ).fetchall()

    def default_payment_term_id():
        row = db().execute(
            "select id from payment_terms where name = 'Net 30' order by id limit 1"
        ).fetchone()
        return row["id"] if row else None

    def posted_payment_term_id(allow_inactive=False):
        raw_value = request.form.get("payment_term_id", "").strip()
        if not raw_value:
            return resolve_default_payment_term_id()
        if not raw_value.isdigit():
            raise ValueError("请选择有效的账期方案。")
        row = db().execute(
            "select id, is_active from payment_terms where id = ?",
            (int(raw_value),),
        ).fetchone()
        if not row or (not row["is_active"] and not allow_inactive):
            raise ValueError("选择的账期方案不存在或已停用。")
        return row["id"]

    def payment_term_form_values():
        name = request.form.get("name", "").strip()
        rule_type = request.form.get("rule_type", "fixed_days").strip()
        if not name:
            raise ValueError("请输入账期名称。")
        if rule_type not in {"fixed_days", "monthly_cutoff"}:
            raise ValueError("请选择有效的账期类型。")
        if rule_type == "fixed_days":
            required_fields = (("fixed_days", "固定天数"),)
        else:
            required_fields = (
                ("cutoff_day", "截止日"), ("due_day", "到期日"),
                ("before_due_months", "截止日前到期月偏移"),
                ("after_due_months", "截止日后到期月偏移"),
            )
        for field_name, label in required_fields:
            if request.form.get(field_name, "").strip() == "":
                raise ValueError(f"请填写{label}。")
        try:
            fixed_days = int(request.form.get("fixed_days", "30") or 30)
            cutoff_day = int(request.form.get("cutoff_day", "20") or 20)
            due_day = int(request.form.get("due_day", "19") or 19)
            before_due_months = int(request.form.get("before_due_months", "1") or 1)
            after_due_months = int(request.form.get("after_due_months", "2") or 2)
        except ValueError as error:
            raise ValueError("账期参数必须是整数。") from error
        if not 0 <= fixed_days <= 3650:
            raise ValueError("固定天数必须在0至3650之间。")
        if not 1 <= cutoff_day <= 31 or not 1 <= due_day <= 31:
            raise ValueError("截止日和到期日必须在1至31之间。")
        if not 0 <= before_due_months <= 24 or not 0 <= after_due_months <= 24:
            raise ValueError("到期月份偏移必须在0至24之间。")
        if rule_type == "monthly_cutoff" and after_due_months < before_due_months:
            raise ValueError("截止日后的到期月份不能早于截止日前。")
        return {
            "name": name,
            "rule_type": rule_type,
            "fixed_days": fixed_days,
            "cutoff_day": cutoff_day if rule_type == "monthly_cutoff" else None,
            "due_day": due_day if rule_type == "monthly_cutoff" else None,
            "before_due_months": before_due_months,
            "after_due_months": after_due_months,
            "notes": request.form.get("notes", "").strip(),
        }

    def eligible_invoice_due_date_changes():
        rows = db().execute(
            """
            select invoices.id, invoices.invoice_number, invoices.issue_date, invoices.due_date,
                   clients.name as client_name, payment_terms.*,
                   service_orders.order_number
            from invoices
            join clients on clients.id = invoices.client_id
            join payment_terms on payment_terms.id = clients.payment_term_id
            join service_orders on service_orders.id = invoices.service_order_id
            where service_orders.status != 'closed'
              and invoices.paid_at is null
              and trim(coalesce(invoices.sent_at, '')) = ''
              and not exists (
                  select 1 from email_delivery_logs
                  where entity_type = 'invoice' and entity_id = invoices.id
              )
            order by invoices.issue_date, invoices.id
            """
        ).fetchall()
        changes = []
        for row in rows:
            new_due_date = resolve_calculate_payment_due_date(row["issue_date"], row).isoformat()
            if new_due_date != row["due_date"]:
                changes.append({"invoice": row, "new_due_date": new_due_date})
        return changes

    return {
        "payment_term_rows": payment_term_rows,
        "default_payment_term_id": default_payment_term_id,
        "posted_payment_term_id": posted_payment_term_id,
        "payment_term_form_values": payment_term_form_values,
        "eligible_invoice_due_date_changes": eligible_invoice_due_date_changes,
    }
