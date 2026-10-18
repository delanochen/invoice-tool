"""Business routes with explicit current-app resolvers; no reverse import."""


def register_settlement_actions_routes(app, *,
    _cb_resolve_archive_customer_reimbursement_pdf,
    _cb_resolve_CUSTOMER_REIMBURSEMENT_DIR,
    _cb_resolve_CUSTOMER_REIMBURSEMENT_STATUS_LABELS,
    _cb_resolve_abort,
    _cb_resolve_can_approve_customer_reimbursement,
    _cb_resolve_can_delete_customer_reimbursement,
    _cb_resolve_can_manage_customer_reimbursement,
    _cb_resolve_can_reset_customer_reimbursement,
    _cb_resolve_create_message,
    _cb_resolve_customer_reimbursement_gate_error,
    _cb_resolve_customer_reimbursement_items,
    _cb_resolve_customer_reimbursement_linked_invoice,
    _cb_resolve_customer_reimbursement_pending_sources,
    _cb_resolve_db,
    _cb_resolve_flash,
    _cb_resolve_g,
    _cb_resolve_json,
    _cb_resolve_latest_customer_reimbursement,
    _cb_resolve_log_action,
    _cb_resolve_login_required,
    _cb_resolve_money,
    _cb_resolve_now,
    _cb_resolve_os,
    _cb_resolve_redirect,
    _cb_resolve_remove_customer_reimbursement_pdf,
    _cb_resolve_request,
    _cb_resolve_require_customer_reimbursement,
    _cb_resolve_require_service_order,
    _cb_resolve_shutil,
    _cb_resolve_update_customer_reimbursement_totals,
    _cb_resolve_url_for,
):
    @_cb_resolve_login_required()
    def approve_customer_reimbursement(reimbursement_id):
        reimbursement, order = _cb_resolve_require_customer_reimbursement()(reimbursement_id)
        if not _cb_resolve_can_approve_customer_reimbursement()():
            _cb_resolve_abort()(403)
        if reimbursement["status"] != "submitted":
            _cb_resolve_flash()("只有待经理审核的工单结算可以审核通过。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]))
        gate = _cb_resolve_customer_reimbursement_gate_error()(reimbursement)
        if gate:
            _cb_resolve_flash()(gate[1], "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]))
        _cb_resolve_db()().execute(
            """
        update customer_reimbursements
        set status = 'approved', return_reason = null, reviewed_by = ?, reviewed_at = ?
        where id = ?
        """,
            (_cb_resolve_g().user["id"], _cb_resolve_now()(), reimbursement_id),
        )
        message = (
            f"{_cb_resolve_g().user['name']}已审核通过工单 {order['order_number']} 的工单结算，"
        f"金额 {_cb_resolve_money()(reimbursement['total_amount'])}。"
        )
        _cb_resolve_create_message()(
            reimbursement["created_by"],
            "工单结算已审核通过",
            message,
            _cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]),
        )
        _cb_resolve_log_action()(
            "approve",
            "customer_reimbursement",
            reimbursement_id,
            reimbursement["file_name"],
            f"金额：{_cb_resolve_money()(reimbursement['total_amount'])}",
        )
        _cb_resolve_db()().commit()
        _cb_resolve_flash()("工单结算已审核通过。", "success")
        return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]))


    @_cb_resolve_login_required()
    def sync_customer_reimbursement_sources(order_id):
        """方案 A「计入并重算」：把已审核但未计入的报销明细链接到本结算并重算快照。
    manual_review 模式下 merge 只读 links 表，所以这里必须先补插 links。"""
        if not _cb_resolve_can_manage_customer_reimbursement()():
            _cb_resolve_abort()(403)
        _cb_resolve_require_service_order()(order_id)
        reimbursement = _cb_resolve_latest_customer_reimbursement()(order_id)
        if not reimbursement:
            _cb_resolve_abort()(404)
        if reimbursement["status"] not in {"draft", "returned"}:
            _cb_resolve_flash()("只有保存未提交或已退回的工单结算可以计入并重算。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order_id))

        if reimbursement["sources_frozen"]:
            # 修订版快照：语义就是不重读最新来源。快照后新审核的报销只能在
            # 「差旅 / 其他」页签手动勾选计入，不能走这条自动补链路径。
            _cb_resolve_flash()("修订版按原版本快照生成，不会自动计入新的报销来源；请在上方页签手动勾选需要计入的明细。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order_id))
        sources = _cb_resolve_customer_reimbursement_pending_sources()(reimbursement)
        linked_count = 0
        skipped_other = 0
        for item in sources["includable"]:
            row = _cb_resolve_db()().execute(
                """
            select ei.id as expense_item_id, ei.amount,
                   coalesce(p.name, ei.project) as project_name,
                   e.status
            from expense_items ei
            join expenses e on e.id = ei.expense_id
            left join projects p on p.id = ei.project_id
            where ei.expense_id = ? and ei.line_key = ?
            """,
                (item["expense_id"], item["line_key"]),
            ).fetchone()
            if not row:
                continue
            already = _cb_resolve_db()().execute(
                "select 1 from customer_reimbursement_expense_links where customer_reimbursement_id = ? and expense_item_id = ?",
                (reimbursement["id"], row["expense_item_id"]),
            ).fetchone()
            if already:
                continue
            other = _cb_resolve_db()().execute(
                "select 1 from customer_reimbursement_expense_links "
            "where expense_item_id = ? and customer_reimbursement_id != ? "
            "and customer_reimbursement_id in ("
            "  select id from customer_reimbursements "
            "  where series_id <> (select series_id from customer_reimbursements where id = ?)"
            ") limit 1",
                (row["expense_item_id"], reimbursement["id"], reimbursement["id"]),
            ).fetchone()
            if other:
                skipped_other += 1
                continue
            _cb_resolve_db()().execute(
                """
            insert into customer_reimbursement_expense_links
                (customer_reimbursement_id, expense_item_id, amount_snapshot, project_snapshot,
                 expense_status_snapshot, selected_by, selected_at)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    reimbursement["id"],
                    row["expense_item_id"],
                    float(row["amount"] or 0),
                    row["project_name"] or "",
                    row["status"] or "approved",
                    _cb_resolve_g().user["id"],
                    _cb_resolve_now()(),
                ),
            )
            # 注意：被「标记无需计入」的明细已从 includable 排除，不会走到这里；
            # 手动勾选计入（settlement_review.replace_expense_links 分支）会单独取消忽略。
            linked_count += 1

        # 把 cutoff 推到现在，确保新链接的报销不被 reviewed_at <= cutoff 条件过滤
        _cb_resolve_db()().execute(
            "update customer_reimbursements set expense_transfer_cutoff_at = ? where id = ?",
            (_cb_resolve_now()(), reimbursement["id"]),
        )

        _cb_resolve_update_customer_reimbursement_totals()(reimbursement["id"])
        _cb_resolve_db()().commit()
        if linked_count:
            _cb_resolve_flash()(f"已将 {linked_count} 笔已审核报销计入工单结算并重算。", "success")
        else:
            _cb_resolve_flash()("没有新的已审核报销需要计入。", "success")
        if skipped_other:
            _cb_resolve_flash()(f"其中 {skipped_other} 笔已链接到其他结算单，已跳过。", "error")
        return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order_id))



    @_cb_resolve_login_required()
    def ignore_customer_reimbursement_source(order_id):
        """方案 B「标记无需计入」：显式确认某笔已审核报销明细不计入本工单结算。

    与方案 A（计入并重算）互斥：已计入当前结算的明细不能标记忽略；
    标记后 gate 不再拦截该明细，操作人/时间/原因写入忽略表并留审计日志。
    仅保存未提交或已退回的结算可标记（提交后来源已冻结）。"""
        if not _cb_resolve_can_manage_customer_reimbursement()():
            _cb_resolve_abort()(403)
        _cb_resolve_require_service_order()(order_id)
        reimbursement = _cb_resolve_latest_customer_reimbursement()(order_id)
        if not reimbursement:
            _cb_resolve_abort()(404)
        if reimbursement["status"] not in {"draft", "returned"}:
            _cb_resolve_flash()("只有保存未提交或已退回的工单结算可以标记忽略。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order_id))
        if reimbursement["sources_frozen"]:
            # 修订版快照：忽略表已从原版本照抄。对快照后新出现的报销标记「无需计入」
            # 会把它误记成「客户明确不承担」（利润表按 0 计收入），语义不对；
            # 快照后新审核的报销只能手动勾选计入，或留给后续版本处理。
            _cb_resolve_flash()("修订版按原版本快照生成，不能对快照外的报销标记无需计入；请手动勾选需要计入的明细。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order_id))
        try:
            expense_item_id = int(_cb_resolve_request().form.get("expense_item_id", ""))
        except (TypeError, ValueError):
            _cb_resolve_flash()("请选择要标记忽略的报销明细。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order_id))
        reason = _cb_resolve_request().form.get("reason", "").strip()
        try:
            row = _cb_resolve_db()().execute(
                """
            select expense_items.id as expense_item_id, expenses.id as expense_id,
                   expenses.expense_number, expense_items.amount
            from expense_items
            join expenses on expenses.id = expense_items.expense_id
            where expense_items.id = ? and expenses.service_order_id = ? and expenses.status = 'approved'
            """,
                (expense_item_id, order_id),
            ).fetchone()
            if not row:
                raise ValueError("该报销明细不属于本工单，或不是已审核状态。")
            # 已计入校验分两种来源口径，都命中即拒绝：
            # - manual_review 模式：customer_reimbursement_expense_links 链接表；
            # - legacy 模式：结算明细行 auto_expense_sources 快照（JSON）。
            linked = _cb_resolve_db()().execute(
                "select 1 from customer_reimbursement_expense_links where customer_reimbursement_id=? and expense_item_id=?",
                (reimbursement["id"], expense_item_id),
            ).fetchone()
            if not linked:
                source_key = _cb_resolve_db()().execute(
                    "select expense_id, line_key from expense_items where id=?", (expense_item_id,)
                ).fetchone()
                if source_key:
                    for item in _cb_resolve_customer_reimbursement_items()(reimbursement["id"]):
                        sources = item["auto_expense_sources"]
                        if isinstance(sources, str):
                            try:
                                sources = _cb_resolve_json().loads(sources or "{}")
                            except (ValueError, TypeError):
                                sources = {}
                        for field_sources in (sources or {}).values():
                            for source in field_sources:
                                if (source.get("expense_id") == source_key["expense_id"]
                                        and source.get("line_key") == source_key["line_key"]):
                                    linked = True
                                    break
                            if linked:
                                break
                        if linked:
                            break
            if linked:
                raise ValueError("该明细已计入本工单结算，不能标记忽略；如需不计入请先取消勾选。")
            _cb_resolve_db()().execute(
                """
            insert into customer_reimbursement_expense_ignores
                (customer_reimbursement_id, expense_item_id, reason, ignored_by, ignored_at)
            values (?, ?, ?, ?, ?)
            on conflict (customer_reimbursement_id, expense_item_id)
            do update set reason = excluded.reason, ignored_by = excluded.ignored_by,
                          ignored_at = excluded.ignored_at
            """,
                (reimbursement["id"], expense_item_id, reason, _cb_resolve_g().user["id"], _cb_resolve_now()()),
            )
            _cb_resolve_log_action()(
                "ignore",
                "customer_reimbursement",
                reimbursement["id"],
                reimbursement["file_name"],
                f"报销 {row['expense_number']} 明细标记无需计入（{_cb_resolve_money()(row['amount'])}）：{reason}",
            )
            _cb_resolve_db()().commit()
            _cb_resolve_flash()("该报销明细已标记为无需计入，提交经理审核时不再拦截。", "success")
        except ValueError as error:
            _cb_resolve_db()().rollback()
            _cb_resolve_flash()(str(error), "error")
        return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order_id))



    @_cb_resolve_login_required()
    def unignore_customer_reimbursement_source(reimbursement_id):
        """取消「标记无需计入」，让该明细重新参与结算来源校验。"""
        if not _cb_resolve_can_manage_customer_reimbursement()():
            _cb_resolve_abort()(403)
        reimbursement, order = _cb_resolve_require_customer_reimbursement()(reimbursement_id)
        if reimbursement["status"] not in {"draft", "returned"}:
            _cb_resolve_flash()("只有保存未提交或已退回的工单结算可以取消忽略。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]))
        try:
            expense_item_id = int(_cb_resolve_request().form.get("expense_item_id", ""))
        except (TypeError, ValueError):
            _cb_resolve_flash()("请选择要取消忽略的报销明细。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]))
        deleted = _cb_resolve_db()().execute(
            "delete from customer_reimbursement_expense_ignores where customer_reimbursement_id=? and expense_item_id=?",
            (reimbursement["id"], expense_item_id),
        ).rowcount
        if deleted:
            _cb_resolve_log_action()(
                "unignore",
                "customer_reimbursement",
                reimbursement["id"],
                reimbursement["file_name"],
                f"取消报销明细 {expense_item_id} 的无需计入标记",
            )
            _cb_resolve_db()().commit()
            _cb_resolve_flash()("已取消忽略，该报销明细重新参与结算校验。", "success")
        else:
            _cb_resolve_flash()("该明细没有忽略标记，无需取消。", "error")
        return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]))


    @_cb_resolve_login_required()
    def return_customer_reimbursement(reimbursement_id):
        reimbursement, order = _cb_resolve_require_customer_reimbursement()(reimbursement_id)
        if not _cb_resolve_can_approve_customer_reimbursement()():
            _cb_resolve_abort()(403)
        if reimbursement["status"] != "submitted":
            _cb_resolve_flash()("只有待经理审核的工单结算可以退回。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]))
        reason = _cb_resolve_request().form.get("return_reason", "").strip()
        if not reason:
            _cb_resolve_flash()("请填写退回原因。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]))
        _cb_resolve_db()().execute(
            """
        update customer_reimbursements
        set status = 'returned', return_reason = ?, reviewed_by = ?, reviewed_at = ?
        where id = ?
        """,
            (reason, _cb_resolve_g().user["id"], _cb_resolve_now()(), reimbursement_id),
        )
        _cb_resolve_create_message()(
            reimbursement["created_by"],
            "工单结算已被退回",
            f"工单 {order['order_number']} 的工单结算已被退回。原因：{reason}",
            _cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]),
        )
        _cb_resolve_log_action()(
            "return",
            "customer_reimbursement",
            reimbursement_id,
            reimbursement["file_name"],
            f"原因：{reason}",
        )
        _cb_resolve_db()().commit()
        _cb_resolve_flash()("工单结算已退回。", "success")
        return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]))


    @_cb_resolve_login_required()
    def reset_customer_reimbursement(reimbursement_id):
        reimbursement, order = _cb_resolve_require_customer_reimbursement()(reimbursement_id)
        if not _cb_resolve_can_reset_customer_reimbursement()():
            _cb_resolve_abort()(403)
        if reimbursement["status"] != "approved":
            _cb_resolve_flash()("只有已审核通过的工单结算可以重置。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]))
        linked_invoice = _cb_resolve_customer_reimbursement_linked_invoice()(reimbursement, order["id"])
        if linked_invoice:
            _cb_resolve_flash()("这份工单结算已经生成发票。请先删除对应发票，再重置工单结算后进行修改。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]))
        _cb_resolve_db()().execute(
            """
        update customer_reimbursements
        set status = 'draft', return_reason = null, reviewed_by = null, reviewed_at = null,
            invoice_id = null
        where id = ?
        """,
            (reimbursement_id,),
        )
        # 旧 PDF 先归档再删除：已发送给甲方的版本必须留底，否则改完只剩新版本，
        # 「甲方砍了哪笔、砍了多少」无从对账。
        archived_pdf = _cb_resolve_archive_customer_reimbursement_pdf()(reimbursement)
        revision_reason = (_cb_resolve_request().form.get("revision_reason") or "").strip()
        _cb_resolve_remove_customer_reimbursement_pdf()(reimbursement)
        detail = "重置为保存未提交，允许重新编辑"
        if archived_pdf:
            detail += f"；原结算 PDF 已归档：{archived_pdf}"
        if revision_reason:
            detail += f"；修订原因：{revision_reason}"
        _cb_resolve_log_action()(
            "reset",
            "customer_reimbursement",
            reimbursement_id,
            reimbursement["file_name"],
            detail,
        )
        _cb_resolve_db()().commit()
        _cb_resolve_flash()("工单结算已重置为保存未提交，可以重新编辑。修改后请重新提交经理审核。", "success")
        return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]))



    @_cb_resolve_login_required()
    def delete_customer_reimbursement(reimbursement_id):
        reimbursement, order = _cb_resolve_require_customer_reimbursement()(reimbursement_id)
        if not _cb_resolve_can_delete_customer_reimbursement()():
            _cb_resolve_abort()(403)
        if reimbursement["status"] not in {"draft", "returned"}:
            _cb_resolve_flash()("只有保存未提交或已退回的工单结算草稿可以删除。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"]))
        reimbursement_dir = _cb_resolve_os().path.join(_cb_resolve_CUSTOMER_REIMBURSEMENT_DIR(), str(reimbursement_id))
        _cb_resolve_db()().execute("delete from customer_reimbursement_attachments where customer_reimbursement_id = ?", (reimbursement_id,))
        _cb_resolve_db()().execute("delete from customer_reimbursement_items where customer_reimbursement_id = ?", (reimbursement_id,))
        _cb_resolve_db()().execute("delete from customer_reimbursements where id = ?", (reimbursement_id,))
        _cb_resolve_shutil().rmtree(reimbursement_dir, ignore_errors=True)
        _cb_resolve_log_action()(
            "delete",
            "customer_reimbursement",
            reimbursement_id,
            reimbursement["file_name"],
            f"工单：{order['order_number']}，状态：{_cb_resolve_CUSTOMER_REIMBURSEMENT_STATUS_LABELS().get(reimbursement['status'], reimbursement['status'])}",
        )
        _cb_resolve_db()().commit()
        _cb_resolve_flash()("工单结算草稿已删除。", "success")
        return _cb_resolve_redirect()(_cb_resolve_url_for()("service_order_detail", order_id=order["id"]))


    app.add_url_rule('/customer-reimbursements/<int:reimbursement_id>/approve', endpoint='approve_customer_reimbursement', view_func=approve_customer_reimbursement, methods=['POST'])
    app.add_url_rule('/service-orders/<int:order_id>/customer-reimbursement/sync-sources', endpoint='sync_customer_reimbursement_sources', view_func=sync_customer_reimbursement_sources, methods=['POST'])
    app.add_url_rule('/service-orders/<int:order_id>/customer-reimbursement/ignore-source', endpoint='ignore_customer_reimbursement_source', view_func=ignore_customer_reimbursement_source, methods=['POST'])
    app.add_url_rule('/customer-reimbursements/<int:reimbursement_id>/unignore-source', endpoint='unignore_customer_reimbursement_source', view_func=unignore_customer_reimbursement_source, methods=['POST'])
    app.add_url_rule('/customer-reimbursements/<int:reimbursement_id>/return', endpoint='return_customer_reimbursement', view_func=return_customer_reimbursement, methods=['POST'])
    app.add_url_rule('/customer-reimbursements/<int:reimbursement_id>/reset', endpoint='reset_customer_reimbursement', view_func=reset_customer_reimbursement, methods=['POST'])
    app.add_url_rule('/customer-reimbursements/<int:reimbursement_id>/delete', endpoint='delete_customer_reimbursement', view_func=delete_customer_reimbursement, methods=['POST'])
    return {'approve_customer_reimbursement': approve_customer_reimbursement, 'sync_customer_reimbursement_sources': sync_customer_reimbursement_sources, 'ignore_customer_reimbursement_source': ignore_customer_reimbursement_source, 'unignore_customer_reimbursement_source': unignore_customer_reimbursement_source, 'return_customer_reimbursement': return_customer_reimbursement, 'reset_customer_reimbursement': reset_customer_reimbursement, 'delete_customer_reimbursement': delete_customer_reimbursement}
