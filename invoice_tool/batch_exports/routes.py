from __future__ import annotations

import json
import os
import stat
import threading
import uuid

from flask import abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for

from invoice_tool.customer_reimbursements.expense_report import batch_contexts

from .service import EXPORT_LABELS, ensure_export_job_schema, run_export_job, utcnow_text


def register_batch_export_routes(app, *, db, data_dir, login_required,
                                 build_service_report_docx, build_simple_xlsx,
                                 customer_reimbursement_items, expense_amount,
                                 render_invoice_pdf, report_attachment_path,
                                 is_external_manager, is_external_employee, get_company_profile=None):
    def visible_order_ids(user, requested):
        requested = sorted({int(v) for v in requested if str(v).isdigit()})
        if not requested:
            return []
        placeholders = ",".join("?" for _ in requested)
        rows = db().execute(
            f"select id from service_orders where id in ({placeholders})",
            tuple(requested),
        ).fetchall()
        return [int(row["id"]) for row in rows]

    @login_required
    def create_batch_export(export_type):
        if is_external_manager() or is_external_employee():
            abort(403)
        if export_type not in EXPORT_LABELS:
            abort(404)
        ensure_export_job_schema(db)
        wants_json = request.accept_mimetypes.best == "application/json"

        def reject(message):
            if wants_json:
                return jsonify(error=message), 400
            flash(message, "error")
            return redirect(url_for("service_orders"))

        requested = request.form.getlist("order_ids")
        order_ids = visible_order_ids(g.user, requested)
        if not order_ids:
            return reject("请至少选择一个有权访问的工单。")
        if len(order_ids) > 200:
            return reject("单次最多选择 200 个工单。")
        if export_type == "settlements":
            try:
                batch_contexts(db(), order_ids)
            except ValueError as error:
                return reject(str(error))
        job_id = str(uuid.uuid4())
        db().execute(
            "insert into batch_export_jobs(id,user_id,export_type,order_ids,status,progress,created_at) values(?,?,?,?,?,?,?)",
            (job_id, int(g.user["id"]), export_type, json.dumps(order_ids), "queued", 0, utcnow_text()),
        )
        db().commit()
        thread = threading.Thread(
            target=run_export_job,
            kwargs=dict(
                job_id=job_id, app=app, db=db, data_dir=data_dir,
                build_service_report_docx=build_service_report_docx,
                build_simple_xlsx=build_simple_xlsx,
                customer_reimbursement_items=customer_reimbursement_items,
                expense_amount=expense_amount,
                render_invoice_pdf=render_invoice_pdf,
                report_attachment_path=report_attachment_path,
                get_company_profile=get_company_profile,
            ),
            daemon=True,
            name=f"batch-export-{job_id[:8]}",
        )
        thread.start()
        if wants_json:
            return jsonify(url=url_for("batch_export_jobs")), 202
        flash(f"{EXPORT_LABELS[export_type]}批量导出任务已创建，请到「导出任务」查看进度并下载 ZIP。", "success")
        return redirect(url_for("batch_export_jobs"))

    @login_required
    def batch_export_jobs():
        if is_external_manager() or is_external_employee():
            abort(403)
        ensure_export_job_schema(db)
        role = str(g.user["role"] or "")
        query = (
            "select jobs.*, users.name as operator_name from batch_export_jobs jobs "
            "left join users on users.id = jobs.user_id "
        )
        params = ()
        if role not in {"admin", "manager", "finance"}:
            query += "where jobs.user_id = ? "
            params = (g.user["id"],)
        rows = db().execute(query + "order by jobs.created_at desc limit 200", params).fetchall()
        jobs = []
        for row in rows:
            job = dict(row)
            job["file_size"] = None
            if job["status"] == "completed" and job["stored_path"]:
                try:
                    file_stat = os.stat(job["stored_path"])
                except OSError:
                    pass
                else:
                    if stat.S_ISREG(file_stat.st_mode):
                        job["file_size"] = file_stat.st_size
            jobs.append(job)
        return render_template("batch_export_jobs.html", jobs=jobs, export_labels=EXPORT_LABELS)

    @login_required
    def download_batch_export(job_id):
        if is_external_manager() or is_external_employee():
            abort(403)
        ensure_export_job_schema(db)
        row = db().execute("select * from batch_export_jobs where id=?", (job_id,)).fetchone()
        if not row:
            abort(404)
        role = str(g.user["role"] or "")
        if role not in {"admin", "manager", "finance"} and int(row["user_id"]) != int(g.user["id"]):
            abort(403)
        if row["status"] != "completed" or not row["stored_path"] or not os.path.isfile(row["stored_path"]):
            abort(404)
        return send_file(row["stored_path"], mimetype="application/zip", as_attachment=True, download_name=row["file_name"] or "batch-export.zip")

    app.add_url_rule("/service-orders/batch-export/<export_type>", endpoint="create_batch_export", view_func=create_batch_export, methods=["POST"])
    app.add_url_rule("/admin/export-jobs", endpoint="batch_export_jobs", view_func=batch_export_jobs, methods=["GET"])
    app.add_url_rule("/admin/export-jobs/<job_id>/download", endpoint="download_batch_export", view_func=download_batch_export, methods=["GET"])
    return {"create_batch_export": create_batch_export, "batch_export_jobs": batch_export_jobs, "download_batch_export": download_batch_export}
