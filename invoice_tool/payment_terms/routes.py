"""Explicit payment-term endpoint registration."""
from flask import abort, flash, redirect, render_template, request, url_for
from database import IntegrityError


def register_payment_term_routes(app, *, db, now, login_required, log_action, payment_term_form_values, payment_term_rows, eligible_invoice_due_date_changes, payment_term_description):
    @login_required
    def payment_terms():
        if request.method == "POST":
            try:
                values = payment_term_form_values()
                cursor = db().execute(
                    """
                    insert into payment_terms (
                        name, rule_type, fixed_days, cutoff_day, due_day,
                        before_due_months, after_due_months, notes, is_active, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        values["name"], values["rule_type"], values["fixed_days"],
                        values["cutoff_day"], values["due_day"], values["before_due_months"],
                        values["after_due_months"], values["notes"], now(), now(),
                    ),
                )
                log_action("create", "payment_term", cursor.lastrowid, values["name"], "新增账期方案")
                db().commit()
                flash("账期方案已创建。", "success")
            except ValueError as error:
                db().rollback()
                flash(str(error), "error")
            except IntegrityError:
                db().rollback()
                flash("账期名称已经存在。", "error")
            return redirect(url_for("payment_terms"))
        terms = payment_term_rows()
        assigned_counts = {
            row["payment_term_id"]: row["count"]
            for row in db().execute(
                "select payment_term_id, count(*) as count from clients group by payment_term_id"
            ).fetchall()
        }
        return render_template(
            "payment_terms.html",
            payment_terms=terms,
            assigned_counts=assigned_counts,
            due_date_changes=eligible_invoice_due_date_changes(),
            payment_term_description=payment_term_description,
        )

    @login_required
    def edit_payment_term(payment_term_id):
        term = db().execute("select * from payment_terms where id = ?", (payment_term_id,)).fetchone()
        if not term:
            abort(404)
        try:
            values = payment_term_form_values()
            db().execute(
                """
                update payment_terms
                set name = ?, rule_type = ?, fixed_days = ?, cutoff_day = ?, due_day = ?,
                    before_due_months = ?, after_due_months = ?, notes = ?, updated_at = ?
                where id = ?
                """,
                (
                    values["name"], values["rule_type"], values["fixed_days"],
                    values["cutoff_day"], values["due_day"], values["before_due_months"],
                    values["after_due_months"], values["notes"], now(), payment_term_id,
                ),
            )
            log_action("update", "payment_term", payment_term_id, values["name"], "修改账期方案")
            db().commit()
            flash("账期方案已更新。", "success")
        except ValueError as error:
            db().rollback()
            flash(str(error), "error")
        except IntegrityError:
            db().rollback()
            flash("账期名称已经存在。", "error")
        return redirect(url_for("payment_terms"))

    @login_required
    def toggle_payment_term(payment_term_id):
        term = db().execute("select * from payment_terms where id = ?", (payment_term_id,)).fetchone()
        if not term:
            abort(404)
        if term["name"] == "Net 30" and term["is_active"]:
            flash("默认 Net 30 方案不能停用。", "error")
            return redirect(url_for("payment_terms"))
        next_status = 0 if term["is_active"] else 1
        db().execute(
            "update payment_terms set is_active = ?, updated_at = ? where id = ?",
            (next_status, now(), payment_term_id),
        )
        log_action(
            "update", "payment_term", payment_term_id, term["name"],
            "启用账期方案" if next_status else "停用账期方案",
        )
        db().commit()
        flash("账期状态已更新。", "success")
        return redirect(url_for("payment_terms"))

    @login_required
    def recalculate_payment_term_invoices():
        changes = eligible_invoice_due_date_changes()
        for change in changes:
            invoice = change["invoice"]
            db().execute(
                "update invoices set due_date = ? where id = ?",
                (change["new_due_date"], invoice["id"]),
            )
            log_action(
                "recalculate_due_date", "invoice", invoice["id"], invoice["invoice_number"],
                f"账期重算：{invoice['due_date']} → {change['new_due_date']}",
            )
        db().commit()
        flash(f"已更新 {len(changes)} 张符合条件的未发送、未核销发票。", "success")
        return redirect(url_for("payment_terms"))

    app.add_url_rule('/settings/payment-terms', endpoint='payment_terms', view_func=payment_terms, methods=['GET', 'POST'])
    app.add_url_rule('/settings/payment-terms/<int:payment_term_id>/edit', endpoint='edit_payment_term', view_func=edit_payment_term, methods=['POST'])
    app.add_url_rule('/settings/payment-terms/<int:payment_term_id>/toggle', endpoint='toggle_payment_term', view_func=toggle_payment_term, methods=['POST'])
    app.add_url_rule('/settings/payment-terms/recalculate-invoices', endpoint='recalculate_payment_term_invoices', view_func=recalculate_payment_term_invoices, methods=['POST'])
    return {
        "payment_terms": payment_terms,
        "edit_payment_term": edit_payment_term,
        "toggle_payment_term": toggle_payment_term,
        "recalculate_payment_term_invoices": recalculate_payment_term_invoices,
    }
