"""Expense create, edit and detail endpoint registration."""
import secrets
from flask import abort, g, flash, redirect, render_template, request, url_for


def register_expense_write_routes(app, *,
    resolve_EXPENSE_STATUS_LABELS,
    can_create_expense,
    can_delete_expense,
    claim_expense_save_token,
    create_message,
    db,
    employee_expense_block_reason,
    expense_beneficiary_id,
    expense_beneficiary_options,
    expense_defaults,
    expense_duplicate_checks,
    expense_items,
    expense_items_from_form,
    finish_expense_save_token,
    get_expense_attachments,
    group_expense_attachments,
    has_action_permission,
    is_manager,
    latest_customer_reimbursement,
    log_action,
    login_required,
    money,
    next_expense_number,
    notify_role,
    now,
    posted_expense_beneficiary,
    require_expense,
    require_service_order,
    require_service_order_start_date,
    run_expense_duplicate_checks,
    save_expense_items,
    save_expense_uploads,
    sync_expense_attachments_to_settlement,
):
    @login_required
    def new_expense(order_id):
        if not can_create_expense():
            abort(403)
        order = require_service_order(order_id)
        start_date_redirect = require_service_order_start_date(order)
        if start_date_redirect:
            return start_date_redirect
        expense_projects = db().execute(
            "select * from projects where project_type = 'expense' and is_active = 1 order by name"
        ).fetchall()
        if not expense_projects:
            flash("请先由经理或管理员创建员工报销项目。", "error")
            return redirect(url_for("projects") if is_manager() else url_for("service_order_detail", order_id=order_id))
        if request.method == "POST":
            save_token = request.form.get("save_token", "")
            if not claim_expense_save_token(save_token):
                existing = db().execute(
                    "select expense_id from expense_save_tokens where token = ?",
                    (save_token,),
                ).fetchone()
                if existing and existing["expense_id"]:
                    return redirect(url_for("edit_expense", expense_id=existing["expense_id"]))
                flash("该报销正在保存，请稍候。", "error")
                return redirect(url_for("new_expense", order_id=order_id))
            submit_for_review = request.form.get("action") == "submit"
            business_purpose = (request.form.get("business_purpose", "") or "").strip()
            try:
                # Phase 3D：草稿可暂缺业务用途；提交审核前必须显式填写。
                if submit_for_review and not business_purpose:
                    raise ValueError("请填写业务用途后再提交报销。")
                beneficiary = posted_expense_beneficiary()
                fixed_price_reason = employee_expense_block_reason(db(), order_id, beneficiary["id"])
                if fixed_price_reason:
                    raise ValueError(fixed_price_reason)
                item_rows = expense_items_from_form()
                total_amount = sum(item["amount"] for item in item_rows)
                project_names = ", ".join(dict.fromkeys(item["project"]["name"] for item in item_rows))
                first_project = item_rows[0]["project"]
                expense_number = next_expense_number()
                cursor = db().execute(
                    """
                    insert into expenses (
                        service_order_id, expense_number, project_id, project, expense_date, amount, currency,
                        description, status, created_by, created_at, updated_at, beneficiary_id, business_purpose
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        expense_number,
                        first_project["id"],
                        project_names,
                        request.form.get("expense_date"),
                        total_amount,
                        "USD",
                        request.form.get("description", "").strip(),
                        "submitted" if submit_for_review else "draft",
                        g.user["id"],
                        now(),
                        now(),
                        beneficiary["id"],
                        business_purpose,
                    ),
                )
                expense_id = cursor.lastrowid
                finish_expense_save_token(save_token, expense_id)
                save_expense_items(expense_id, item_rows)
                save_expense_uploads(expense_id, item_rows)
                # 查重与附件同步是保存主流程之外的辅助步骤，任何环境性异常
                # （文件占用、磁盘、图片解码等）都不应让报销保存失败（与 edit_expense 一致）。
                try:
                    run_expense_duplicate_checks(expense_id, use_deepseek=submit_for_review)
                except Exception:
                    app.logger.exception("Expense duplicate checks failed for expense %s", expense_id)
                try:
                    sync_expense_attachments_to_settlement(order["id"])
                except Exception:
                    app.logger.exception("Expense attachment sync failed for expense %s", expense_id)
                expense_summary = f"报销归属员工：{beneficiary['name']}；工单：{order['order_number']}；金额：{money(total_amount)}"
                log_action("create", "expense", expense_id, expense_number, expense_summary)
                if submit_for_review:
                    log_action("submit", "expense", expense_id, expense_number, expense_summary)
                db().commit()
            except ValueError as error:
                db().rollback()
                flash(str(error), "error")
                return redirect(url_for("new_expense", order_id=order_id))
            except Exception as error:
                db().rollback()
                app.logger.exception("Unexpected error saving expense for order %s", order_id)
                flash(
                    f"保存报销时发生意外错误（{type(error).__name__}: {str(error)[:200]}），请重试；若反复出现请联系管理员。",
                    "error",
                )
                return redirect(url_for("new_expense", order_id=order_id))
            if submit_for_review:
                try:
                    if beneficiary["id"] != g.user["id"]:
                        create_message(
                            beneficiary["id"], "报销已提交审核",
                            f"{g.user['name']}为你提交了报销 {expense_number}，金额 {money(total_amount)}。",
                            url_for("expense_detail", expense_id=expense_id),
                        )
                    notify_role(
                        ["admin", "manager"],
                        "新报销待审核",
                        f"{g.user['name']}提交了归属 {beneficiary['name']} 的报销 {expense_number}，工单 {order['order_number']}，金额 {money(total_amount)}。",
                        url_for("expense_detail", expense_id=expense_id),
                    )
                    db().commit()
                except Exception:
                    # 报销数据已保存成功，仅通知失败时不阻塞用户。
                    app.logger.exception("Expense submit notification failed for expense %s", expense_id)
                flash("报销已提交经理审核。", "success")
                return redirect(url_for("expense_detail", expense_id=expense_id))
            flash("报销已保存。", "success")
            return redirect(url_for("edit_expense", expense_id=expense_id))
        return render_template(
            "expense_form.html",
            order=order,
            expense=expense_defaults(),
            beneficiaries=expense_beneficiary_options(),
            form_items=[],
            expense_projects=expense_projects,
            is_edit=False,
            attachments=[],
            attachments_by_item={},
            save_token=secrets.token_urlsafe(24),
        )


    @login_required
    def edit_expense(expense_id):
        expense, order = require_expense(expense_id)
        if expense["status"] not in {"draft", "returned"}:
            flash("只有保存未提交或被退回的报销可以编辑。", "error")
            return redirect(url_for("expense_detail", expense_id=expense_id))
        if expense["created_by"] != g.user["id"] and not is_manager():
            abort(403)
        expense_projects = db().execute(
            """
            select * from projects
            where project_type = 'expense' and (
                is_active = 1 or id in (
                    select project_id from expense_items where expense_id = ?
                )
            )
            order by is_active desc, name
            """,
            (expense_id,),
        ).fetchall()
        if request.method == "POST":
            save_token = request.form.get("save_token", "")
            if not claim_expense_save_token(save_token, expense_id):
                flash("该报销已经保存，请勿重复提交。", "success")
                return redirect(url_for("edit_expense", expense_id=expense_id))
            submit_for_review = request.form.get("action") == "submit"
            business_purpose = (request.form.get("business_purpose", "") or "").strip()
            try:
                # Phase 3D：历史单查看不强制补；只有真正编辑并重新提交审核时才要求。
                if submit_for_review and not business_purpose:
                    raise ValueError("请填写业务用途后再提交报销。")
                beneficiary = posted_expense_beneficiary(expense)
                fixed_price_reason = employee_expense_block_reason(
                    db(), order["id"], beneficiary["id"]
                )
                if fixed_price_reason:
                    raise ValueError(fixed_price_reason)
                item_rows = expense_items_from_form(expense_id)
                total_amount = sum(item["amount"] for item in item_rows)
                project_names = ", ".join(dict.fromkeys(item["project"]["name"] for item in item_rows))
                first_project = item_rows[0]["project"]
                db().execute(
                    """
                    update expenses
                    set project_id = ?, project = ?, expense_date = ?, amount = ?, currency = ?, description = ?,
                        status = ?, return_reason = null, updated_at = ?, beneficiary_id = ?, business_purpose = ?
                    where id = ?
                    """,
                    (
                        first_project["id"],
                        project_names,
                        request.form.get("expense_date"),
                        total_amount,
                        "USD",
                        request.form.get("description", "").strip(),
                        "submitted" if submit_for_review else "draft",
                        now(),
                        beneficiary["id"],
                        business_purpose,
                        expense_id,
                    ),
                )
                save_expense_items(expense_id, item_rows)
                save_expense_uploads(expense_id, item_rows)
                # 查重与附件同步是保存主流程之外的辅助步骤，任何环境性异常
                # （文件占用、磁盘、图片解码等）都不应让报销保存返回 500。
                try:
                    run_expense_duplicate_checks(expense_id, use_deepseek=submit_for_review)
                except Exception:
                    app.logger.exception("Expense duplicate checks failed for expense %s", expense_id)
                try:
                    sync_expense_attachments_to_settlement(order["id"])
                except Exception:
                    app.logger.exception("Expense attachment sync failed for expense %s", expense_id)
                expense_summary = f"报销归属员工：{beneficiary['name']}；工单：{order['order_number']}；金额：{money(total_amount)}"
                log_action("update", "expense", expense_id, expense["expense_number"], expense_summary)
                if submit_for_review:
                    log_action("submit", "expense", expense_id, expense["expense_number"], expense_summary)
                db().commit()
            except ValueError as error:
                db().rollback()
                flash(str(error), "error")
                return redirect(url_for("edit_expense", expense_id=expense_id))
            except Exception:
                db().rollback()
                app.logger.exception("Unexpected error saving expense %s", expense_id)
                flash("保存报销时发生意外错误，请重试；若反复出现请联系管理员。", "error")
                return redirect(url_for("edit_expense", expense_id=expense_id))
            if submit_for_review:
                try:
                    if beneficiary["id"] != g.user["id"]:
                        create_message(
                            beneficiary["id"], "报销已提交审核",
                            f"{g.user['name']}为你提交了报销 {expense['expense_number']}，金额 {money(total_amount)}。",
                            url_for("expense_detail", expense_id=expense_id),
                        )
                    notify_role(
                        ["admin", "manager"],
                        "报销已提交审核",
                        f"{g.user['name']}提交了归属 {beneficiary['name']} 的报销 {expense['expense_number']}，工单 {order['order_number']}，金额 {money(total_amount)}。",
                        url_for("expense_detail", expense_id=expense_id),
                    )
                    db().commit()
                except Exception:
                    # 报销数据已保存成功，仅通知失败时不阻塞用户。
                    app.logger.exception("Expense submit notification failed for expense %s", expense_id)
                flash("报销已提交经理审核。", "success")
                return redirect(url_for("expense_detail", expense_id=expense_id))
            flash("报销已保存。", "success")
            return redirect(url_for("edit_expense", expense_id=expense_id))
        attachments = get_expense_attachments(expense_id)
        attachments_by_item = group_expense_attachments(attachments)
        return render_template(
            "expense_form.html",
            order=order,
            expense=expense_defaults(expense),
            beneficiaries=expense_beneficiary_options(expense),
            form_items=expense_items(expense_id),
            expense_projects=expense_projects,
            is_edit=True,
            attachments=attachments,
            attachments_by_item=attachments_by_item,
            transfer_reimbursement=latest_customer_reimbursement(order["id"]),
            save_token=secrets.token_urlsafe(24),
        )


    @login_required
    def expense_detail(expense_id):
        expense, order = require_expense(expense_id)
        can_transfer_attachments = has_action_permission("customer_reimbursements", "edit")
        creator = db().execute("select name, email from users where id = ?", (expense["created_by"],)).fetchone()
        reviewer = db().execute("select name, email from users where id = ?", (expense["reviewed_by"],)).fetchone() if expense["reviewed_by"] else None
        reimburser = db().execute(
            "select name, email from users where id = ?",
            (expense["reimbursed_by"],),
        ).fetchone() if expense["reimbursed_by"] else None
        payment_order = db().execute(
            "select id,payment_number,status from employee_payment_orders where source_key=?",
            (f"expense:{expense_id}",),
        ).fetchone()
        attachments = get_expense_attachments(expense_id)
        attachments_by_item = group_expense_attachments(attachments)
        # 旧数据兜底：迁移完成前仍存在的“未挂明细行”附件，顶部警告提示而不是渲染通用附件区
        unassigned_attachments = attachments_by_item.pop("", [])
        can_review = has_action_permission("expenses", "ai_review")
        ai_review = db().execute(
            "select * from expense_ai_reviews where expense_id = ?", (expense_id,)
        ).fetchone() if can_review else None
        # AI 审核意见由夜间批量 worker（默认 03:00，见 ai_review_worker.py）生成；
        # 详情页只展示已有结果并提供手动「立即审核」按钮，白天不占用模型资源。
        attachment_ids = [attachment["id"] for attachment in attachments]
        interpretations = {}
        if attachment_ids:
            placeholders = ",".join("?" for _ in attachment_ids)
            interpretations = {
                row["attachment_id"]: dict(row)
                for row in db().execute(
                    f"select * from expense_attachment_interpretations where attachment_id in ({placeholders})",
                    attachment_ids,
                ).fetchall()
            }
        return render_template(
            "expense_detail.html",
            order=order,
            expense=expense,
            items=expense_items(expense_id),
            creator=creator,
            beneficiary=db().execute("select name from users where id = ?", (expense_beneficiary_id(expense),)).fetchone(),
            reviewer=reviewer,
            reimburser=reimburser,
            payment_order=payment_order,
            attachments=attachments,
            attachments_by_item=attachments_by_item,
            unassigned_attachments=unassigned_attachments,
            interpretations=interpretations,
            ai_review=ai_review,
            can_review=can_review,
            transfer_reimbursement=latest_customer_reimbursement(order["id"]) if can_transfer_attachments else None,
            can_transfer_attachments=can_transfer_attachments,
            can_delete=can_delete_expense(expense),
            duplicate_checks=expense_duplicate_checks(expense_id),
            labels=resolve_EXPENSE_STATUS_LABELS(),
        )


    app.add_url_rule('/service-orders/<int:order_id>/expenses/new', endpoint='new_expense', view_func=new_expense, methods=['GET', 'POST'])
    app.add_url_rule('/expenses/<int:expense_id>/edit', endpoint='edit_expense', view_func=edit_expense, methods=['GET', 'POST'])
    app.add_url_rule('/expenses/<int:expense_id>', endpoint='expense_detail', view_func=expense_detail, methods=['GET'])
    return {'new_expense': new_expense, 'edit_expense': edit_expense, 'expense_detail': expense_detail}
