"""Contract and attachment endpoints, registered without changing their public names."""
import os
import shutil
from flask import abort, flash, g, redirect, render_template, request, send_file, url_for
from database import IntegrityError


def register_contract_routes(app, *, CONTRACT_STATUS_LABELS, CONTRACT_TYPE_LABELS, STATUS_LABELS, can_manage_contracts, can_view_contracts, contract_attachment_path, contract_form_values, db, ensure_commercial_document, is_external_manager, log_action, next_contract_number, now, require_contract_access, safe_attachment_response, save_contract_uploads, contract_attachments_root, login_required):
    @login_required
    def contracts():
        if not can_view_contracts():
            abort(403)
        q = request.args.get("q", "").strip()
        contract_type = request.args.get("contract_type", "").strip()
        status = request.args.get("status", "").strip()
        clauses = ["1 = 1"]
        params = []
        if is_external_manager():
            clauses.append("contracts.client_id = ?")
            params.append(g.user["client_id"] or 0)
        if q:
            clauses.append(
                "(contracts.contract_number like ? or contracts.title like ? "
                "or clients.name like ? or contracts.project_name like ?)"
            )
            params.extend([f"%{q}%"] * 4)
        if contract_type in CONTRACT_TYPE_LABELS:
            clauses.append("contracts.contract_type = ?")
            params.append(contract_type)
        if status in CONTRACT_STATUS_LABELS:
            clauses.append("contracts.status = ?")
            params.append(status)
        rows = db().execute(
            f"""
            select contracts.*, clients.name as client_name,
                   count(distinct service_orders.id) as work_order_count
            from contracts
            join clients on clients.id = contracts.client_id
            left join service_orders on service_orders.contract_id = contracts.id
            where {" and ".join(clauses)}
            group by contracts.id, clients.id
            order by
              case contracts.status when 'active' then 0 when 'signed' then 1 when 'review' then 2 else 3 end,
              coalesce(contracts.end_date, '9999-12-31'), contracts.id desc
            """,
            params,
        ).fetchall()
        return render_template(
            "contracts.html",
            contracts=rows,
            q=q,
            selected_type=contract_type,
            selected_status=status,
        )


    @login_required
    def new_contract():
        if not can_manage_contracts():
            abort(403)
        clients_rows = db().execute("select * from clients order by client_number, name").fetchall()
        if not clients_rows:
            flash("请先创建客户。", "error")
            return redirect(url_for("clients"))
        if request.method == "POST":
            try:
                values = contract_form_values()
                cursor = db().execute(
                    """
                    insert into contracts (
                        contract_number, client_id, contract_type, title, status, signed_date,
                        start_date, end_date, currency, amount, payment_terms, rate_card,
                        project_name, notes, created_by, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        values["contract_number"], values["client_id"], values["contract_type"],
                        values["title"], values["status"], values["signed_date"], values["start_date"],
                        values["end_date"], values["currency"], values["amount"],
                        values["payment_terms"], values["rate_card"], values["project_name"],
                        values["notes"], g.user["id"], now(), now(),
                    ),
                )
                contract_id = cursor.lastrowid
                save_contract_uploads(contract_id)
                # 商务单据父行：现有合同迁移时已回填；新建合同确保存在，默认仅里程/按实际
                ensure_commercial_document(
                    db(), "contract", contract_id,
                    client_id=values["client_id"],
                    currency=values["currency"],
                    driving_billing_mode="mileage_only",
                    settlement_mode="actual",
                )
                log_action(
                    "create",
                    "contract",
                    contract_id,
                    values["contract_number"],
                    f"{CONTRACT_TYPE_LABELS[values['contract_type']]} · {values['title']}",
                )
                db().commit()
                flash("合同已创建。", "success")
                return redirect(url_for("contract_detail", contract_id=contract_id))
            except ValueError as error:
                db().rollback()
                flash(str(error), "error")
            except IntegrityError:
                db().rollback()
                flash("合同编号已经存在，请更换合同编号。", "error")
        defaults = dict(request.form) if request.method == "POST" else {
            "contract_number": next_contract_number(),
            "contract_type": "framework",
            "status": "draft",
            "currency": "USD",
        }
        return render_template(
            "contract_form.html",
            contract=None,
            clients=clients_rows,
            defaults=defaults,
            attachments=[],
            form_title="新建合同",
        )


    @login_required
    def contract_detail(contract_id):
        contract = require_contract_access(contract_id)
        client = db().execute("select * from clients where id = ?", (contract["client_id"],)).fetchone()
        attachments = db().execute(
            """
            select contract_attachments.*, users.name as uploader_name
            from contract_attachments
            left join users on users.id = contract_attachments.uploaded_by
            where contract_attachments.contract_id = ?
            order by contract_attachments.uploaded_at desc, contract_attachments.id desc
            """,
            (contract_id,),
        ).fetchall()
        work_orders = db().execute(
            """
            select service_orders.*, work_order_types.name as work_order_type_name
            from service_orders
            left join work_order_types on work_order_types.id = service_orders.work_order_type_id
            where service_orders.contract_id = ?
            order by service_orders.created_at desc, service_orders.id desc
            """,
            (contract_id,),
        ).fetchall()
        invoices = db().execute(
            """
            select distinct invoices.*
            from invoices
            join service_orders on service_orders.id = invoices.service_order_id
            where service_orders.contract_id = ? and invoices.status != 'void'
            order by invoices.issue_date desc, invoices.id desc
            """,
            (contract_id,),
        ).fetchall()
        return render_template(
            "contract_detail.html",
            contract=contract,
            client=client,
            attachments=attachments,
            work_orders=work_orders,
            invoices=invoices,
            labels=STATUS_LABELS,
        )


    @login_required
    def edit_contract(contract_id):
        if not can_manage_contracts():
            abort(403)
        contract = require_contract_access(contract_id)
        clients_rows = db().execute("select * from clients order by client_number, name").fetchall()
        if request.method == "POST":
            try:
                values = contract_form_values(contract)
                linked_clients = db().execute(
                    """
                    select count(*) as count from service_orders
                    where contract_id = ? and client_id != ?
                    """,
                    (contract_id, values["client_id"]),
                ).fetchone()["count"]
                if linked_clients:
                    raise ValueError("合同已有其他客户的工单，不能更改合同客户。")
                db().execute(
                    """
                    update contracts
                    set contract_number = ?, client_id = ?, contract_type = ?, title = ?, status = ?,
                        signed_date = ?, start_date = ?, end_date = ?, currency = ?, amount = ?,
                        payment_terms = ?, rate_card = ?, project_name = ?, notes = ?, updated_at = ?
                    where id = ?
                    """,
                    (
                        values["contract_number"], values["client_id"], values["contract_type"],
                        values["title"], values["status"], values["signed_date"], values["start_date"],
                        values["end_date"], values["currency"], values["amount"],
                        values["payment_terms"], values["rate_card"], values["project_name"],
                        values["notes"], now(), contract_id,
                    ),
                )
                save_contract_uploads(contract_id)
                log_action("update", "contract", contract_id, values["contract_number"], "修改合同资料")
                db().commit()
                flash("合同已更新。", "success")
                return redirect(url_for("contract_detail", contract_id=contract_id))
            except ValueError as error:
                db().rollback()
                flash(str(error), "error")
            except IntegrityError:
                db().rollback()
                flash("合同编号已经存在，请更换合同编号。", "error")
        attachments = db().execute(
            "select * from contract_attachments where contract_id = ? order by uploaded_at desc, id desc",
            (contract_id,),
        ).fetchall()
        defaults = dict(request.form) if request.method == "POST" else dict(contract)
        return render_template(
            "contract_form.html",
            contract=contract,
            clients=clients_rows,
            defaults=defaults,
            attachments=attachments,
            form_title="编辑合同",
        )


    @login_required
    def delete_contract(contract_id):
        if not can_manage_contracts():
            abort(403)
        contract = require_contract_access(contract_id)
        work_order_count = db().execute(
            "select count(*) as count from service_orders where contract_id = ?",
            (contract_id,),
        ).fetchone()["count"]
        if work_order_count:
            flash("合同已有工单关联，不能删除；可以将状态改为已终止。", "error")
            return redirect(url_for("contract_detail", contract_id=contract_id))
        shutil.rmtree(os.path.join(contract_attachments_root(), str(contract_id)), ignore_errors=True)
        db().execute("delete from contracts where id = ?", (contract_id,))
        log_action("delete", "contract", contract_id, contract["contract_number"], contract["title"])
        db().commit()
        flash("合同已删除。", "success")
        return redirect(url_for("contracts"))


    @login_required
    def preview_contract_attachment(attachment_id):
        attachment = db().execute(
            "select * from contract_attachments where id = ?",
            (attachment_id,),
        ).fetchone()
        if not attachment:
            abort(404)
        require_contract_access(attachment["contract_id"])
        return safe_attachment_response(
            contract_attachment_path(attachment),
            attachment["original_filename"],
        )


    @login_required
    def download_contract_attachment(attachment_id):
        attachment = db().execute(
            "select * from contract_attachments where id = ?",
            (attachment_id,),
        ).fetchone()
        if not attachment:
            abort(404)
        require_contract_access(attachment["contract_id"])
        return send_file(
            contract_attachment_path(attachment),
            as_attachment=True,
            download_name=attachment["original_filename"],
        )


    @login_required
    def delete_contract_attachment(attachment_id):
        if not can_manage_contracts():
            abort(403)
        attachment = db().execute(
            "select * from contract_attachments where id = ?",
            (attachment_id,),
        ).fetchone()
        if not attachment:
            abort(404)
        require_contract_access(attachment["contract_id"])
        try:
            os.remove(contract_attachment_path(attachment))
        except FileNotFoundError:
            pass
        db().execute("delete from contract_attachments where id = ?", (attachment_id,))
        db().commit()
        flash("合同附件已删除。", "success")
        return redirect(url_for("edit_contract", contract_id=attachment["contract_id"]))


    app.add_url_rule('/contracts', endpoint='contracts', view_func=contracts, methods=['GET'])
    app.add_url_rule('/contracts/new', endpoint='new_contract', view_func=new_contract, methods=['GET', 'POST'])
    app.add_url_rule('/contracts/<int:contract_id>', endpoint='contract_detail', view_func=contract_detail, methods=['GET'])
    app.add_url_rule('/contracts/<int:contract_id>/edit', endpoint='edit_contract', view_func=edit_contract, methods=['GET', 'POST'])
    app.add_url_rule('/contracts/<int:contract_id>/delete', endpoint='delete_contract', view_func=delete_contract, methods=['POST'])
    app.add_url_rule('/contract-attachments/<int:attachment_id>', endpoint='preview_contract_attachment', view_func=preview_contract_attachment, methods=['GET'])
    app.add_url_rule('/contract-attachments/<int:attachment_id>/download', endpoint='download_contract_attachment', view_func=download_contract_attachment, methods=['GET'])
    app.add_url_rule('/contract-attachments/<int:attachment_id>/delete', endpoint='delete_contract_attachment', view_func=delete_contract_attachment, methods=['POST'])
    return {'contracts': contracts, 'new_contract': new_contract, 'contract_detail': contract_detail, 'edit_contract': edit_contract, 'delete_contract': delete_contract, 'preview_contract_attachment': preview_contract_attachment, 'download_contract_attachment': download_contract_attachment, 'delete_contract_attachment': delete_contract_attachment}
