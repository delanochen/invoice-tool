"""Service-report read, export and attachment endpoint registration."""
import os
import re
from io import BytesIO
from werkzeug.utils import secure_filename
from flask import abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for


def register_report_read_routes(app, *,
    adjacent_service_report_ids,
    build_service_report_docx,
    db,
    get_company_profile,
    get_report_attachments,
    has_action_permission,
    login_required,
    prune_empty_report_folders,
    report_attachment_path,
    report_parts,
    require_service_report,
    safe_attachment_response,
    service_report_workers,
):
    @login_required
    def view_service_report(report_id):
        """Read-only, Word-export-style view of a service report.

        This is the external-facing "查看" surface: with the service_reports view
        action granted (权限管理 > 工作日报 > 查看), external managers/employees can
        read the report in the same layout as the exported Word document, but they
        never see the internal edit form. Internal users can use it too.
        """
        if not has_action_permission("service_reports", "view"):
            abort(403)
        report, order = require_service_report(report_id)
        workers = service_report_workers(report_id)
        worker_descriptions = [
            f"{worker['name']}：{worker['work_description']}"
            for worker in workers
            if worker["work_description"]
        ]
        description_text = report["service_description"] or ""
        if worker_descriptions:
            description_text = "\n".join(
                ([description_text] if description_text else []) + worker_descriptions
            )
        attachment_groups = get_report_attachments(report_id)
        photo_labels = {
            "arrival": "到达现场时间照片",
            "departure": "离开现场时间照片",
            "self_check": "自检照片",
            "site": "现场服务照片",
        }
        photo_sections = [
            {"title": title, "photos": attachment_groups.get(category, [])}
            for category, title in photo_labels.items()
            if attachment_groups.get(category)
        ]
        return render_template(
            "service_report_view.html",
            report=report,
            order=order,
            workers=workers,
            description_text=description_text,
            saved_parts=report_parts("service_report_saved_parts", report_id),
            replaced_parts=report_parts("service_report_replaced_parts", report_id),
            photo_sections=photo_sections,
            company=get_company_profile(),
            can_export=has_action_permission("service_reports", "export"),
            adjacent_reports=adjacent_service_report_ids(report_id),
        )


    @login_required
    def export_service_report(report_id):
        report, order = require_service_report(report_id)
        try:
            document_bytes = build_service_report_docx(report, order)
        except Exception:
            app.logger.exception("Failed to export service report %s", report_id)
            flash("工作日报导出失败，请检查日报内容或照片后重试。", "error")
            return redirect(url_for("edit_service_report", report_id=report_id))
        filename = secure_filename(f"{order['client_order_number']}-{report['report_date']}-report.docx") or "service-report.docx"
        return send_file(
            BytesIO(document_bytes),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name=filename,
        )


    @login_required
    def preview_report_attachment(attachment_id):
        attachment = db().execute("select * from service_report_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            abort(404)
        require_service_report(attachment["report_id"])
        return safe_attachment_response(
            report_attachment_path(attachment),
            attachment["original_filename"],
        )


    @login_required
    def delete_report_attachment(attachment_id):
        attachment = db().execute("select * from service_report_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            abort(404)
        report, order = require_service_report(attachment["report_id"])
        try:
            attachment_path = report_attachment_path(attachment)
            os.remove(attachment_path)
            prune_empty_report_folders(attachment_path)
        except FileNotFoundError:
            pass
        db().execute("delete from service_report_attachments where id = ?", (attachment_id,))
        db().commit()
        # 前端（service-report.js）用 fetch 就地删行，页面不刷新 —— 整页重载会先
        # 重绘到顶部再跳锚点，用户看到的是「删一张图，页面滚回最上面」，而且表单里
        # 没保存的修改也会一并丢掉。非 JS 场景仍保留原来的重定向 + 锚点回退。
        # 判定方式与 delete_invoice_attachment 保持一致。
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": True, "deleted": attachment_id})
        flash("附件已删除。", "success")
        redirect_anchor = request.form.get("redirect_anchor", "").strip()
        if not re.fullmatch(r"#[A-Za-z0-9_-]+", redirect_anchor):
            redirect_anchor = ""
        return redirect(url_for("edit_service_report", report_id=report["id"]) + redirect_anchor)


    app.add_url_rule('/service-reports/<int:report_id>/view', endpoint='view_service_report', view_func=view_service_report, methods=['GET'])
    app.add_url_rule('/service-reports/<int:report_id>/export', endpoint='export_service_report', view_func=export_service_report, methods=['POST'])
    app.add_url_rule('/service-report-attachments/<int:attachment_id>', endpoint='preview_report_attachment', view_func=preview_report_attachment, methods=['GET'])
    app.add_url_rule('/service-report-attachments/<int:attachment_id>/delete', endpoint='delete_report_attachment', view_func=delete_report_attachment, methods=['POST'])
    return {'view_service_report': view_service_report, 'export_service_report': export_service_report, 'preview_report_attachment': preview_report_attachment, 'delete_report_attachment': delete_report_attachment}
