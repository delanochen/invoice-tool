"""Explicit registration for the existing knowledge-base endpoints."""

import os
from datetime import date, datetime, timedelta

from flask import abort, flash, g, redirect, render_template, request, send_file, url_for


def register_knowledge_routes(
    app,
    *,
    service,
    db,
    now,
    app_timezone,
    login_required,
    log_action,
):
    """Register knowledge routes without a Blueprint endpoint prefix.

    The returned mapping is used by the root module for temporary compatibility
    exports. Only the dependencies used by this domain are accepted.
    """

    def knowledge_expiry_from_form():
        value = request.form.get("expires_on", "").strip()
        if not value:
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as error:
            raise ValueError("到期日期格式无效。") from error

    @login_required
    def knowledge_base():
        if request.method == "POST":
            saved = None
            try:
                saved = service.save_pdf_upload(request.files.get("document"))
                expires_on = knowledge_expiry_from_form()
                title = (
                    request.form.get("title", "").strip()
                    or os.path.splitext(saved["original_filename"])[0]
                    or "未命名文档"
                )[:200]
                category = (request.form.get("category", "").strip() or "其他")[:80]
                description = request.form.get("description", "").strip()[:2000]
                is_pinned = 1 if request.form.get("is_pinned") == "1" else 0
                timestamp = now()
                cursor = db().execute(
                    """
                    insert into knowledge_documents (
                        title, category, description, original_filename, stored_filename,
                        content_type, file_size, uploaded_by, uploaded_at, updated_at,
                        is_pinned, expires_on, search_text, text_indexed_at, current_version
                    ) values (?, ?, ?, ?, ?, 'application/pdf', ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        title,
                        category,
                        description,
                        saved["original_filename"],
                        saved["stored_filename"],
                        saved["file_size"],
                        g.user["id"],
                        timestamp,
                        timestamp,
                        is_pinned,
                        expires_on,
                        saved["extracted_text"],
                        timestamp,
                    ),
                )
                document_id = cursor.lastrowid
                db().execute(
                    """
                    insert into knowledge_document_versions (
                        document_id, version_number, original_filename, stored_filename,
                        file_size, extracted_text, change_note, uploaded_by, uploaded_at
                    ) values (?, 1, ?, ?, ?, ?, '初始版本', ?, ?)
                    """,
                    (
                        document_id,
                        saved["original_filename"],
                        saved["stored_filename"],
                        saved["file_size"],
                        saved["extracted_text"],
                        g.user["id"],
                        timestamp,
                    ),
                )
                log_action("create", "knowledge_document", document_id, title, f"分类：{category}")
                db().commit()
            except ValueError as error:
                db().rollback()
                if saved is not None:
                    try:
                        os.remove(saved["destination"])
                    except FileNotFoundError:
                        pass
                flash(str(error), "error")
                return redirect(url_for("knowledge_base"))
            except Exception:
                db().rollback()
                if saved is not None:
                    try:
                        os.remove(saved["destination"])
                    except FileNotFoundError:
                        pass
                raise
            flash("知识库文档已上传。", "success")
            return redirect(url_for("knowledge_base"))

        keyword = request.args.get("q", "").strip()
        category = request.args.get("category", "").strip()
        expiry_status = request.args.get("expiry", "").strip()
        if expiry_status not in {"expired", "soon", "current"}:
            expiry_status = ""
        today_value = datetime.now(app_timezone()).date()
        upcoming_value = today_value + timedelta(days=30)
        clauses = []
        params = []
        if keyword:
            pattern = f"%{keyword}%"
            clauses.append(
                "(knowledge_documents.title like ? or knowledge_documents.description like ? "
                "or knowledge_documents.original_filename like ? or knowledge_documents.search_text like ?)"
            )
            params.extend([pattern, pattern, pattern, pattern])
        if category:
            clauses.append("knowledge_documents.category = ?")
            params.append(category)
        if expiry_status == "expired":
            clauses.append("knowledge_documents.expires_on < ?")
            params.append(today_value.isoformat())
        elif expiry_status == "soon":
            clauses.append("knowledge_documents.expires_on >= ? and knowledge_documents.expires_on <= ?")
            params.extend([today_value.isoformat(), upcoming_value.isoformat()])
        elif expiry_status == "current":
            clauses.append("(knowledge_documents.expires_on is null or knowledge_documents.expires_on > ?)")
            params.append(upcoming_value.isoformat())
        where_clause = f"where {' and '.join(clauses)}" if clauses else ""
        documents = db().execute(
            f"""
            select knowledge_documents.*, users.name as uploader_name
            from knowledge_documents
            left join users on users.id = knowledge_documents.uploaded_by
            {where_clause}
            order by knowledge_documents.is_pinned desc, knowledge_documents.updated_at desc, knowledge_documents.id desc
            """,
            params,
        ).fetchall()
        categories = db().execute(
            "select distinct category from knowledge_documents order by category"
        ).fetchall()
        version_rows = db().execute(
            """
            select knowledge_document_versions.*, users.name as uploader_name
            from knowledge_document_versions
            left join users on users.id = knowledge_document_versions.uploaded_by
            order by knowledge_document_versions.document_id, knowledge_document_versions.version_number desc
            """
        ).fetchall()
        versions_by_document = {}
        for version in version_rows:
            versions_by_document.setdefault(version["document_id"], []).append(version)
        knowledge_metrics = db().execute(
            """
            select count(*) as total,
                   sum(case when expires_on < ? then 1 else 0 end) as expired,
                   sum(case when expires_on >= ? and expires_on <= ? then 1 else 0 end) as expiring_soon,
                   sum(view_count) as views,
                   sum(download_count) as downloads
            from knowledge_documents
            """,
            (today_value.isoformat(), today_value.isoformat(), upcoming_value.isoformat()),
        ).fetchone()
        return render_template(
            "knowledge_base.html",
            documents=documents,
            categories=[row["category"] for row in categories],
            keyword=keyword,
            selected_category=category,
            selected_expiry=expiry_status,
            today_date=today_value.isoformat(),
            upcoming_date=upcoming_value.isoformat(),
            versions_by_document=versions_by_document,
            knowledge_metrics=knowledge_metrics,
        )

    @login_required
    def preview_knowledge_document(document_id):
        document = service.document_or_404(document_id)
        if not os.path.isfile(service.document_path(document)):
            abort(404)
        db().execute("update knowledge_documents set view_count = view_count + 1 where id = ?", (document_id,))
        db().commit()
        return send_file(
            service.document_path(document),
            mimetype="application/pdf",
            as_attachment=False,
            download_name=document["original_filename"],
            conditional=True,
        )

    @login_required
    def download_knowledge_document(document_id):
        document = service.document_or_404(document_id)
        if not os.path.isfile(service.document_path(document)):
            abort(404)
        db().execute("update knowledge_documents set download_count = download_count + 1 where id = ?", (document_id,))
        db().commit()
        return send_file(
            service.document_path(document),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=document["original_filename"],
            conditional=True,
        )

    @login_required
    def edit_knowledge_document(document_id):
        document = service.document_or_404(document_id)
        title = request.form.get("title", "").strip()[:200]
        if not title:
            flash("文档标题不能为空。", "error")
            return redirect(url_for("knowledge_base"))
        try:
            expires_on = knowledge_expiry_from_form()
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("knowledge_base"))
        category = (request.form.get("category", "").strip() or "其他")[:80]
        description = request.form.get("description", "").strip()[:2000]
        is_pinned = 1 if request.form.get("is_pinned") == "1" else 0
        db().execute(
            """
            update knowledge_documents
            set title = ?, category = ?, description = ?, is_pinned = ?, expires_on = ?, updated_at = ?
            where id = ?
            """,
            (title, category, description, is_pinned, expires_on, now(), document_id),
        )
        log_action("update", "knowledge_document", document_id, title, f"原文件：{document['original_filename']}")
        db().commit()
        flash("文档信息已更新。", "success")
        return redirect(url_for("knowledge_base"))

    @login_required
    def upload_knowledge_document_version(document_id):
        document = service.document_or_404(document_id)
        saved = None
        try:
            saved = service.save_pdf_upload(request.files.get("document"))
            version_number = db().execute(
                "select coalesce(max(version_number), 0) + 1 as value from knowledge_document_versions where document_id = ?",
                (document_id,),
            ).fetchone()["value"]
            timestamp = now()
            change_note = request.form.get("change_note", "").strip()[:500]
            db().execute(
                """
                insert into knowledge_document_versions (
                    document_id, version_number, original_filename, stored_filename,
                    file_size, extracted_text, change_note, uploaded_by, uploaded_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    version_number,
                    saved["original_filename"],
                    saved["stored_filename"],
                    saved["file_size"],
                    saved["extracted_text"],
                    change_note,
                    g.user["id"],
                    timestamp,
                ),
            )
            db().execute(
                """
                update knowledge_documents
                set original_filename = ?, stored_filename = ?, file_size = ?, search_text = ?,
                    text_indexed_at = ?, current_version = ?, updated_at = ?
                where id = ?
                """,
                (
                    saved["original_filename"],
                    saved["stored_filename"],
                    saved["file_size"],
                    saved["extracted_text"],
                    timestamp,
                    version_number,
                    timestamp,
                    document_id,
                ),
            )
            log_action(
                "update",
                "knowledge_document",
                document_id,
                document["title"],
                f"上传 v{version_number}：{change_note or saved['original_filename']}",
            )
            db().commit()
        except ValueError as error:
            db().rollback()
            flash(str(error), "error")
            return redirect(url_for("knowledge_base"))
        except Exception:
            db().rollback()
            if saved is not None:
                try:
                    os.remove(saved["destination"])
                except FileNotFoundError:
                    pass
            raise
        flash(f"文档已更新为 v{version_number}。", "success")
        return redirect(url_for("knowledge_base"))

    @login_required
    def preview_knowledge_document_version(version_id):
        version = service.version_or_404(version_id)
        if not os.path.isfile(service.version_path(version)):
            abort(404)
        db().execute(
            "update knowledge_documents set view_count = view_count + 1 where id = ?",
            (version["document_id"],),
        )
        db().commit()
        return send_file(
            service.version_path(version),
            mimetype="application/pdf",
            as_attachment=False,
            download_name=version["original_filename"],
            conditional=True,
        )

    @login_required
    def download_knowledge_document_version(version_id):
        version = service.version_or_404(version_id)
        if not os.path.isfile(service.version_path(version)):
            abort(404)
        db().execute(
            "update knowledge_documents set download_count = download_count + 1 where id = ?",
            (version["document_id"],),
        )
        db().commit()
        return send_file(
            service.version_path(version),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=version["original_filename"],
            conditional=True,
        )

    @login_required
    def delete_knowledge_document(document_id):
        document = service.document_or_404(document_id)
        version_paths = {
            service.version_path(version)
            for version in db().execute(
                "select stored_filename from knowledge_document_versions where document_id = ?",
                (document_id,),
            ).fetchall()
        }
        db().execute("delete from knowledge_documents where id = ?", (document_id,))
        log_action("delete", "knowledge_document", document_id, document["title"], document["original_filename"])
        db().commit()
        version_paths.add(service.document_path(document))
        for path in version_paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        flash("知识库文档已删除。", "success")
        return redirect(url_for("knowledge_base"))

    routes = {
        "knowledge_base": ("/knowledge-base", knowledge_base, ["GET", "POST"]),
        "preview_knowledge_document": ("/knowledge-base/<int:document_id>/preview", preview_knowledge_document, ["GET"]),
        "download_knowledge_document": ("/knowledge-base/<int:document_id>/download", download_knowledge_document, ["GET"]),
        "edit_knowledge_document": ("/knowledge-base/<int:document_id>/edit", edit_knowledge_document, ["POST"]),
        "upload_knowledge_document_version": ("/knowledge-base/<int:document_id>/versions", upload_knowledge_document_version, ["POST"]),
        "preview_knowledge_document_version": ("/knowledge-base/versions/<int:version_id>/preview", preview_knowledge_document_version, ["GET"]),
        "download_knowledge_document_version": ("/knowledge-base/versions/<int:version_id>/download", download_knowledge_document_version, ["GET"]),
        "delete_knowledge_document": ("/knowledge-base/<int:document_id>/delete", delete_knowledge_document, ["POST"]),
    }
    for endpoint, (rule, view_func, methods) in routes.items():
        app.add_url_rule(rule, endpoint=endpoint, view_func=view_func, methods=methods)

    return {
        **{endpoint: item[1] for endpoint, item in routes.items()},
        "extract_knowledge_pdf_text": service.extract_pdf_text,
        "save_knowledge_pdf_upload": service.save_pdf_upload,
        "knowledge_expiry_from_form": knowledge_expiry_from_form,
        "knowledge_document_path": service.document_path,
        "knowledge_document_or_404": service.document_or_404,
        "knowledge_version_or_404": service.version_or_404,
        "knowledge_version_path": service.version_path,
    }
