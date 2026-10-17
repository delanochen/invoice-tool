"""Service-report create, edit and delete endpoints; preserve legacy transactions."""
import os
import shutil
import secrets
from flask import abort, flash, g, redirect, render_template, request, url_for


def register_report_write_routes(app, *,
    resolve_REPORT_ATTACHMENTS_DIR,
    calculated_report_driving_miles,
    calculated_report_total_service_hours,
    calculated_report_total_time,
    can_create_service_report,
    claim_report_save_token,
    resolve_date,
    db,
    finish_report_save_token,
    get_report_attachments,
    has_action_permission,
    is_external_employee,
    is_external_manager,
    is_manager,
    log_action,
    login_required,
    now,
    posted_report_time,
    posted_report_writer_id,
    prune_empty_report_folders,
    relocate_report_attachments,
    report_attachment_path,
    report_form_defaults,
    report_locked_by_approved_reimbursement,
    report_parts,
    report_storage_context_values,
    report_worker_rows_from_form,
    report_writer_options,
    require_service_order,
    require_service_order_start_date,
    require_service_report,
    reset_ai_drafts_for_deleted_service_report,
    same_order_adjacent_report_ids,
    save_report_detail_rows,
    save_report_uploads,
    service_report_worker_options,
    service_report_workers,
):
    @login_required
    def new_service_report(order_id):
        order = require_service_order(order_id)
        if not can_create_service_report(order):
            abort(403)
        if report_locked_by_approved_reimbursement(order):
            return redirect(url_for("service_order_detail", order_id=order_id))
        if not is_external_employee():
            start_date_redirect = require_service_order_start_date(order)
            if start_date_redirect:
                return start_date_redirect
        users_rows = service_report_worker_options(order)
        report_writers = report_writer_options()
        if request.method == "POST":
            save_token = request.form.get("save_token", "")
            if not claim_report_save_token(save_token):
                existing = db().execute(
                    "select report_id from service_report_save_tokens where token = ?",
                    (save_token,),
                ).fetchone()
                if existing and existing["report_id"]:
                    return redirect(url_for("edit_service_report", report_id=existing["report_id"]))
                flash("该日报正在保存，请稍候。", "error")
                return redirect(url_for("new_service_report", order_id=order_id))
            try:
                worker_rows = report_worker_rows_from_form()
                arrival_time = posted_report_time("arrival_time")
                departure_time = posted_report_time("departure_time")
                total_service_hours = calculated_report_total_service_hours(arrival_time, departure_time)
                total_time = calculated_report_total_time()
                driving_miles = calculated_report_driving_miles()
                cursor = db().execute(
                    """
                    insert into service_reports (
                        service_order_id, report_date, actual_work_date, total_service_hours, travel_hours, public_transport_hours,
                        driving_miles, mileage_billing_method, departure_address, site_address, total_time, cabinet_number,
                        arrival_time, departure_time, service_description, report_writer_id, created_by, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        request.form.get("report_date"),
                        request.form.get("actual_work_date") or request.form.get("report_date"),
                        total_service_hours,
                        sum(row["travel_hours"] for row in worker_rows),
                        sum(row["public_transport_hours"] for row in worker_rows),
                        driving_miles,
                        request.form.get("mileage_billing_method", "per_person"),
                        request.form.get("departure_address", "").strip(),
                        request.form.get("site_address", "").strip(),
                        total_time,
                        request.form.get("cabinet_number", "").strip(),
                        arrival_time,
                        departure_time,
                        request.form.get("service_description", "").strip(),
                        posted_report_writer_id(),
                        g.user["id"],
                        now(),
                        now(),
                    ),
                )
                report_id = cursor.lastrowid
                finish_report_save_token(save_token, report_id)
                save_report_detail_rows(report_id)
                storage_context = report_storage_context_values(
                    order["order_number"], request.form.get("report_date"), report_id
                )
                save_report_uploads(report_id, storage_context=storage_context)
                log_action(
                    "create",
                    "service_report",
                    report_id,
                    f"{order['order_number']} / {request.form.get('report_date')}",
                    "创建工作日报",
                )
                db().commit()
            except ValueError as error:
                db().rollback()
                flash(str(error), "error")
                return redirect(url_for("new_service_report", order_id=order_id))
            flash("工作日报已保存。", "success")
            return redirect(url_for("edit_service_report", report_id=report_id))
        source_report = None
        report_data = report_form_defaults(order=order)
        worker_rows = []
        saved_parts = [{} for _ in range(4)]
        replaced_parts = [{} for _ in range(4)]
        copy_from = request.args.get("copy_from")
        if copy_from is not None:
            if not copy_from.isdigit():
                abort(400)
            if not has_action_permission("service_reports", "view"):
                abort(403)
            source_report, source_order = require_service_report(int(copy_from))
            if source_order["id"] != order_id:
                abort(404)
            if is_external_employee() and source_report["created_by"] != g.user["id"]:
                abort(403)
            report_data = report_form_defaults(report=source_report)
            for name in ("id", "created_by", "created_at", "updated_at"):
                report_data.pop(name, None)
            report_data["report_date"] = resolve_date().today().isoformat()
            report_data["actual_work_date"] = resolve_date().today().isoformat()
            allowed_workers = {row["id"] for row in users_rows}
            original_workers = service_report_workers(source_report["id"])
            worker_rows = [row for row in original_workers if row["id"] in allowed_workers]
            if len(worker_rows) != len(original_workers):
                flash("部分原服务人员已不可选，请补充本次日报的服务人员。", "error")
            if report_data["report_writer_id"] not in {row["id"] for row in report_writers}:
                report_data["report_writer_id"] = None
            saved_parts = list(report_parts("service_report_saved_parts", source_report["id"])) or saved_parts
            replaced_parts = list(report_parts("service_report_replaced_parts", source_report["id"])) or replaced_parts
        return render_template(
            "service_report_form.html",
            order=order,
            report=report_data,
            source_report=source_report,
            users=users_rows,
            report_writers=report_writers,
            worker_rows=worker_rows,
            saved_parts=saved_parts,
            replaced_parts=replaced_parts,
            attachments=get_report_attachments(0),
            save_token=secrets.token_urlsafe(24),
            is_edit=False,
            can_edit_report=True,
        )


    @login_required
    def edit_service_report(report_id):
        report, order = require_service_report(report_id)
        if is_external_employee() and report["created_by"] != g.user["id"]:
            abort(403)
        if report_locked_by_approved_reimbursement(order):
            return redirect(url_for("service_order_detail", order_id=order["id"]))
        users_rows = service_report_worker_options(order, report_id=report_id)
        report_writers = report_writer_options()
        if request.method == "POST":
            if is_external_manager():
                abort(403)
            save_token = request.form.get("save_token", "")
            if not claim_report_save_token(save_token, report_id):
                flash("该日报已经保存，请勿重复提交。", "success")
                return redirect(url_for("edit_service_report", report_id=report_id))
            try:
                worker_rows = report_worker_rows_from_form()
                arrival_time = posted_report_time("arrival_time")
                departure_time = posted_report_time("departure_time")
                total_service_hours = calculated_report_total_service_hours(arrival_time, departure_time)
                total_time = calculated_report_total_time()
                driving_miles = calculated_report_driving_miles()
                db().execute(
                    """
                    update service_reports
                    set report_date = ?, actual_work_date = ?, total_service_hours = ?, travel_hours = ?, public_transport_hours = ?,
                        driving_miles = ?, mileage_billing_method = ?, departure_address = ?, site_address = ?, total_time = ?, cabinet_number = ?,
                        arrival_time = ?, departure_time = ?, service_description = ?, report_writer_id = ?, updated_at = ?
                    where id = ?
                    """,
                    (
                        request.form.get("report_date"),
                        request.form.get("actual_work_date") or request.form.get("report_date"),
                        total_service_hours,
                        sum(row["travel_hours"] for row in worker_rows),
                        sum(row["public_transport_hours"] for row in worker_rows),
                        driving_miles,
                        request.form.get("mileage_billing_method", "per_person"),
                        request.form.get("departure_address", "").strip(),
                        request.form.get("site_address", "").strip(),
                        total_time,
                        request.form.get("cabinet_number", "").strip(),
                        arrival_time,
                        departure_time,
                        request.form.get("service_description", "").strip(),
                        posted_report_writer_id(),
                        now(),
                        report_id,
                    ),
                )
                save_report_detail_rows(report_id)
                relocate_report_attachments(report_id)
                save_report_uploads(report_id)
                log_action(
                    "update",
                    "service_report",
                    report_id,
                    f"{order['order_number']} / {request.form.get('report_date')}",
                    "修改工作日报",
                )
                db().commit()
            except ValueError as error:
                db().rollback()
                flash(str(error), "error")
                return redirect(url_for("edit_service_report", report_id=report_id))
            flash("工作日报已保存。", "success")
            return redirect(url_for("edit_service_report", report_id=report_id))
        worker_rows = service_report_workers(report_id)
        saved_parts = list(report_parts("service_report_saved_parts", report_id)) or [{} for _ in range(4)]
        replaced_parts = list(report_parts("service_report_replaced_parts", report_id)) or [{} for _ in range(4)]
        return render_template(
            "service_report_form.html",
            order=order,
            report=report_form_defaults(report=report),
            users=users_rows,
            report_writers=report_writers,
            worker_rows=worker_rows,
            saved_parts=saved_parts,
            replaced_parts=replaced_parts,
            attachments=get_report_attachments(report_id),
            save_token=secrets.token_urlsafe(24),
            is_edit=True,
            can_edit_report=not is_external_manager(),
            adjacent_reports=same_order_adjacent_report_ids(report_id),
        )


    @login_required
    def delete_service_report(report_id):
        report, order = require_service_report(report_id)
        if report["created_by"] != g.user["id"] and not is_manager():
            abort(403)
        if report_locked_by_approved_reimbursement(order):
            return redirect(url_for("service_order_detail", order_id=order["id"]))
        # 日报一旦被员工工资组件引用，删除会撞外键约束。先给友好提示，不再 500。
        live_refs = db().execute(
            "select count(*) as n from employee_payment_components where daily_report_id = ? and superseded_at is null",
            (report_id,),
        ).fetchone()["n"]
        if live_refs:
            flash(
                f"该日报已被 {live_refs} 条员工工资组件引用（draft 工资单），不能删除。"
                "请先在工资模块作废或重算相关工资单。",
                "error",
            )
            return redirect(url_for("service_order_detail", order_id=order["id"]))
        attachments = db().execute(
            "select * from service_report_attachments where report_id = ?",
            (report_id,),
        ).fetchall()
        for attachment in attachments:
            attachment_path = report_attachment_path(attachment)
            try:
                os.remove(attachment_path)
                prune_empty_report_folders(attachment_path)
            except FileNotFoundError:
                pass
        shutil.rmtree(os.path.join(resolve_REPORT_ATTACHMENTS_DIR(), str(report_id)), ignore_errors=True)
        reset_ai_drafts_for_deleted_service_report(db(), report_id)
        db().execute("delete from service_report_attachments where report_id = ?", (report_id,))
        db().execute("delete from service_report_workers where report_id = ?", (report_id,))
        db().execute("delete from service_report_saved_parts where report_id = ?", (report_id,))
        db().execute("delete from service_report_replaced_parts where report_id = ?", (report_id,))
        db().execute("delete from service_reports where id = ?", (report_id,))
        log_action(
            "delete",
            "service_report",
            report_id,
            f"{order['order_number']} / {report['report_date']}",
            "删除工作日报",
        )
        db().commit()
        flash("工作日报已删除。", "success")
        return redirect(url_for("service_order_detail", order_id=order["id"]))


    app.add_url_rule('/service-orders/<int:order_id>/reports/new', endpoint='new_service_report', view_func=new_service_report, methods=['GET', 'POST'])
    app.add_url_rule('/service-reports/<int:report_id>/edit', endpoint='edit_service_report', view_func=edit_service_report, methods=['GET', 'POST'])
    app.add_url_rule('/service-reports/<int:report_id>/delete', endpoint='delete_service_report', view_func=delete_service_report, methods=['POST'])
    return {'new_service_report': new_service_report, 'edit_service_report': edit_service_report, 'delete_service_report': delete_service_report}
