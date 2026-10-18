"""Business routes with explicit current-app resolvers; no reverse import."""


def register_draft_list_routes(app, *,
    _cb_resolve_ai_daily_report_draft_list_filters,
    _cb_resolve_db,
    _cb_resolve_g,
    _cb_resolve_is_internal_user,
    _cb_resolve_json,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_reconcile_travel_verification_fields,
    _cb_resolve_request,
    _cb_resolve_url_for,
):
    @_cb_resolve_login_required()
    def ai_daily_report_drafts_list():
        """List AI Daily Report Drafts, filtered by user permission.

    Query params: status, date_from, date_to, service_order_id, page, per_page.
    List is filtered at SQL level by user permission (not query-all-then-hide).
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        user = _cb_resolve_g().user
        clauses, params = _cb_resolve_ai_daily_report_draft_list_filters()(user)

        # Filters
        status = _cb_resolve_request().args.get("status")
        if status and status in ("draft", "confirmed", "cancelled", "saved"):
            clauses.append("status = ?")
            params.append(status)

        date_from = _cb_resolve_request().args.get("date_from")
        if date_from:
            clauses.append("report_date >= ?")
            params.append(date_from)

        date_to = _cb_resolve_request().args.get("date_to")
        if date_to:
            clauses.append("report_date <= ?")
            params.append(date_to)

        service_order_id = _cb_resolve_request().args.get("service_order_id", type=int)
        if service_order_id:
            clauses.append("service_order_id = ?")
            params.append(service_order_id)

        # Pagination
        page = max(1, _cb_resolve_request().args.get("page", 1, type=int))
        per_page = min(100, max(1, _cb_resolve_request().args.get("per_page", 20, type=int)))
        offset = (page - 1) * per_page

        where = " and ".join(clauses) if clauses else "1=1"

        # Count
        count_row = _cb_resolve_db()().execute(
            f"select count(*) as cnt from ai_daily_report_drafts where {where}",
            params,
        ).fetchone()
        total = count_row["cnt"] if count_row else 0

        # Query (draft first, then updated_at desc)
        rows = _cb_resolve_db()().execute(
            f"""select id, service_order_id, report_date, status, draft_version,
                   created_by, created_at, updated_at, draft_data, saved_report_id
            from ai_daily_report_drafts
            where {where}
            order by
                case when status = 'draft' then 0 else 1 end,
                updated_at desc
            limit ? offset ?""",
            params + [per_page, offset],
        ).fetchall()

        # Enrich with service order info and worker count
        drafts = []
        for row in rows:
            draft_data = {}
            try:
                draft_data = _cb_resolve_json().loads(row["draft_data"] or "{}") if "draft_data" in row.keys() else {}
            except (_cb_resolve_json().JSONDecodeError, TypeError):
                pass

            service_order = _cb_resolve_db()().execute(
                "select order_number, client_name, site_address from service_orders where id = ?",
                (row["service_order_id"],),
            ).fetchone()

            worker_count = len(draft_data.get("workers", []))
            photo_count = len(draft_data.get("photo_candidates", []))
            # v0.1.241: 列表徽标同步清理已确认的出行字段与 work_items.* 存量标记
            verification_fields = _cb_resolve_reconcile_travel_verification_fields()(
                draft_data.get("workers", []),
                draft_data.get("verification_fields") or [],
            )
            verification_fields = [
                f for f in verification_fields
                if not str(f).lower().startswith("work_items.")
            ]

            drafts.append({
                "id": row["id"],
                "service_order_id": row["service_order_id"],
                "order_number": service_order["order_number"] if service_order else None,
                "client_name": service_order["client_name"] if service_order else None,
                "report_date": row["report_date"],
                "status": row["status"],
                "draft_version": row["draft_version"],
                "worker_count": worker_count,
                "photo_count": photo_count,
                "has_verification_required": len(verification_fields) > 0,
                "verification_fields": verification_fields,
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "saved_report_id": row["saved_report_id"],
                "report_url": (
                    _cb_resolve_url_for()("edit_service_report", report_id=row["saved_report_id"])
                    if row["saved_report_id"] else None
                ),
            })

        return _cb_resolve_jsonify()({
            "ok": True,
            "drafts": drafts,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page if total > 0 else 0,
        })


    app.add_url_rule('/api/ai/daily-report/drafts', endpoint='ai_daily_report_drafts_list', view_func=ai_daily_report_drafts_list, methods=['GET'])
    return {'ai_daily_report_drafts_list': ai_daily_report_drafts_list}


def register_draft_pages_routes(app, *,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve_abort,
    _cb_resolve_ai_daily_report_csrf_token,
    _cb_resolve_can_view_ai_daily_report_draft,
    _cb_resolve_g,
    _cb_resolve_is_internal_user,
    _cb_resolve_login_required,
    _cb_resolve_render_template,
):
    @_cb_resolve_login_required()
    def ai_daily_report_drafts_page():
        """Review Center - Draft List page."""
        if not _cb_resolve_is_internal_user()():
            _cb_resolve_abort()(403)
        return _cb_resolve_render_template()(
            "ai_daily_report_drafts.html",
            csrf_token=_cb_resolve_ai_daily_report_csrf_token()(),
        )


    @_cb_resolve_login_required()
    def ai_daily_report_draft_detail_page(draft_id):
        """Review Center - Draft Detail/Preview page."""
        if not _cb_resolve_is_internal_user()():
            _cb_resolve_abort()(403)

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            _cb_resolve_abort()(404)

        if not _cb_resolve_can_view_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            _cb_resolve_abort()(403)

        return _cb_resolve_render_template()(
            "ai_daily_report_draft_detail.html",
            draft_id=draft_id,
            csrf_token=_cb_resolve_ai_daily_report_csrf_token()(),
        )


    app.add_url_rule('/ai-daily-report/drafts', endpoint='ai_daily_report_drafts_page', view_func=ai_daily_report_drafts_page, methods=['GET'])
    app.add_url_rule('/ai-daily-report/drafts/<int:draft_id>', endpoint='ai_daily_report_draft_detail_page', view_func=ai_daily_report_draft_detail_page, methods=['GET'])
    return {'ai_daily_report_drafts_page': ai_daily_report_drafts_page, 'ai_daily_report_draft_detail_page': ai_daily_report_draft_detail_page}
