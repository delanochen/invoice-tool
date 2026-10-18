"""Business routes with explicit current-app resolvers; no reverse import."""


def register_settlement_attachments_routes(app, *,
    _cb_resolve_abort,
    _cb_resolve_can_manage_customer_reimbursement,
    _cb_resolve_customer_reimbursement_attachment_path,
    _cb_resolve_db,
    _cb_resolve_flash,
    _cb_resolve_log_action,
    _cb_resolve_login_required,
    _cb_resolve_os,
    _cb_resolve_redirect,
    _cb_resolve_require_customer_reimbursement,
    _cb_resolve_safe_attachment_response,
    _cb_resolve_send_file,
    _cb_resolve_url_for,
):
    @_cb_resolve_login_required()
    def download_customer_reimbursement_attachment(attachment_id):
        attachment = _cb_resolve_db()().execute("select * from customer_reimbursement_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            _cb_resolve_abort()(404)
        _cb_resolve_require_customer_reimbursement()(attachment["customer_reimbursement_id"])
        return _cb_resolve_send_file()(_cb_resolve_customer_reimbursement_attachment_path()(attachment), as_attachment=True, download_name=attachment["original_filename"])


    @_cb_resolve_login_required()
    def preview_customer_reimbursement_attachment(attachment_id):
        attachment = _cb_resolve_db()().execute("select * from customer_reimbursement_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            _cb_resolve_abort()(404)
        _cb_resolve_require_customer_reimbursement()(attachment["customer_reimbursement_id"])
        return _cb_resolve_safe_attachment_response()(
            _cb_resolve_customer_reimbursement_attachment_path()(attachment),
            attachment["original_filename"],
        )


    @_cb_resolve_login_required()
    def delete_customer_reimbursement_attachment(attachment_id):
        attachment = _cb_resolve_db()().execute("select * from customer_reimbursement_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            _cb_resolve_abort()(404)
        reimbursement, order = _cb_resolve_require_customer_reimbursement()(attachment["customer_reimbursement_id"])
        if not _cb_resolve_can_manage_customer_reimbursement()():
            _cb_resolve_abort()(403)
        if reimbursement["status"] not in {"draft", "returned"}:
            _cb_resolve_flash()("只有保存未提交或已退回的工单结算可以删除附件。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"], _anchor="customerReimbursementAttachmentsSection"))
        try:
            _cb_resolve_os().remove(_cb_resolve_customer_reimbursement_attachment_path()(attachment))
        except FileNotFoundError:
            pass
        _cb_resolve_db()().execute("delete from customer_reimbursement_attachments where id = ?", (attachment_id,))
        _cb_resolve_log_action()("delete", "customer_reimbursement_attachment", attachment_id, attachment["original_filename"], f"工单：{order['order_number']}")
        _cb_resolve_db()().commit()
        _cb_resolve_flash()("附件已删除。", "success")
        return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=order["id"], _anchor="customerReimbursementAttachmentsSection"))


    app.add_url_rule('/customer-reimbursement-attachments/<int:attachment_id>/download', endpoint='download_customer_reimbursement_attachment', view_func=download_customer_reimbursement_attachment, methods=['GET'])
    app.add_url_rule('/customer-reimbursement-attachments/<int:attachment_id>/preview', endpoint='preview_customer_reimbursement_attachment', view_func=preview_customer_reimbursement_attachment, methods=['GET'])
    app.add_url_rule('/customer-reimbursement-attachments/<int:attachment_id>/delete', endpoint='delete_customer_reimbursement_attachment', view_func=delete_customer_reimbursement_attachment, methods=['POST'])
    return {'download_customer_reimbursement_attachment': download_customer_reimbursement_attachment, 'preview_customer_reimbursement_attachment': preview_customer_reimbursement_attachment, 'delete_customer_reimbursement_attachment': delete_customer_reimbursement_attachment}
