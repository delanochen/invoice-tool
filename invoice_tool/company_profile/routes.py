"""Business routes with explicit current-app resolvers; no reverse import."""


def register_company_page_routes(app, *,
    _cb_resolve_COMPANY_ATTACHMENT_DIR,
    _cb_resolve_DEFAULT_PAYMENT_INSTRUCTIONS,
    _cb_resolve_DEFAULT_TIMEZONE,
    _cb_resolve_ZoneInfo,
    _cb_resolve_ZoneInfoNotFoundError,
    _cb_resolve_abort,
    _cb_resolve_can_manage_company_info,
    _cb_resolve_db,
    _cb_resolve_flash,
    _cb_resolve_get_company_attachments,
    _cb_resolve_get_company_profile,
    _cb_resolve_get_invoice_terms,
    _cb_resolve_get_payment_instructions,
    _cb_resolve_get_timezone_name,
    _cb_resolve_is_internal_user,
    _cb_resolve_login_required,
    _cb_resolve_redirect,
    _cb_resolve_render_template,
    _cb_resolve_request,
    _cb_resolve_save_named_attachment,
    _cb_resolve_set_setting,
    _cb_resolve_uploaded_attachments_from_request,
    _cb_resolve_url_for,
):
    @_cb_resolve_login_required()
    def company_info():
        if not _cb_resolve_is_internal_user()():
            _cb_resolve_abort()(403)
        if _cb_resolve_request().method == "POST" and not _cb_resolve_can_manage_company_info()():
            _cb_resolve_abort()(403)
        if _cb_resolve_request().method == "POST":
            for key in ("name", "address", "email", "phone", "registration_number", "ein"):
                _cb_resolve_set_setting()(f"company_{key}", _cb_resolve_request().form.get(f"company_{key}", "").strip())
            timezone_name = _cb_resolve_request().form.get("app_timezone", _cb_resolve_DEFAULT_TIMEZONE()).strip() or _cb_resolve_DEFAULT_TIMEZONE()
            try:
                _cb_resolve_ZoneInfo()(timezone_name)
            except _cb_resolve_ZoneInfoNotFoundError():
                _cb_resolve_flash()("系统时区无效，请使用类似 America/Chicago 的时区名称。", "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("company_info"))
            _cb_resolve_set_setting()("app_timezone", timezone_name)
            try:
                for uploaded in _cb_resolve_uploaded_attachments_from_request()():
                    _cb_resolve_save_named_attachment()(uploaded, _cb_resolve_COMPANY_ATTACHMENT_DIR(), "company_attachments")
            except ValueError as error:
                _cb_resolve_db()().rollback()
                _cb_resolve_flash()(str(error), "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("company_info"))
            # 收款信息（v0.1.325 起从「系统设置」迁入本页）：与公司资料同权限（company_info.edit）、
            # 同一表单原子提交；数据仍存 settings 表 payment_* / invoice_terms，发票/邮件读侧不变。
            for key in _cb_resolve_DEFAULT_PAYMENT_INSTRUCTIONS():
                _cb_resolve_set_setting()(f"payment_{key}", _cb_resolve_request().form.get(f"payment_{key}", "").strip())
            _cb_resolve_set_setting()("invoice_terms", _cb_resolve_request().form.get("invoice_terms", "").strip())
            _cb_resolve_set_setting()("company_tax_note", _cb_resolve_request().form.get("company_tax_note", "").strip())
            _cb_resolve_db()().commit()
            _cb_resolve_flash()("公司信息已保存。", "success")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("company_info"))
        return _cb_resolve_render_template()(
            "company_info.html",
            company=_cb_resolve_get_company_profile()(),
            timezone_name=_cb_resolve_get_timezone_name()(),
            attachments=_cb_resolve_get_company_attachments()(),
            payment=_cb_resolve_get_payment_instructions()(),
            terms=_cb_resolve_get_invoice_terms()(),
        )


    app.add_url_rule('/company-info', endpoint='company_info', view_func=company_info, methods=['GET', 'POST'])
    return {'company_info': company_info}


def register_company_attachment_read_routes(app, *,
    _cb_resolve_COMPANY_ATTACHMENT_DIR,
    _cb_resolve_abort,
    _cb_resolve_db,
    _cb_resolve_is_internal_user,
    _cb_resolve_login_required,
    _cb_resolve_os,
    _cb_resolve_safe_attachment_response,
    _cb_resolve_send_file,
):
    @_cb_resolve_login_required()
    def download_company_attachment(attachment_id):
        if not _cb_resolve_is_internal_user()():
            _cb_resolve_abort()(403)
        attachment = _cb_resolve_db()().execute("select * from company_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            _cb_resolve_abort()(404)
        return _cb_resolve_send_file()(
            _cb_resolve_os().path.join(_cb_resolve_COMPANY_ATTACHMENT_DIR(), attachment["stored_filename"]),
            as_attachment=True,
            download_name=attachment["original_filename"],
        )


    @_cb_resolve_login_required()
    def preview_company_attachment(attachment_id):
        if not _cb_resolve_is_internal_user()():
            _cb_resolve_abort()(403)
        attachment = _cb_resolve_db()().execute("select * from company_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            _cb_resolve_abort()(404)
        return _cb_resolve_safe_attachment_response()(
            _cb_resolve_os().path.join(_cb_resolve_COMPANY_ATTACHMENT_DIR(), attachment["stored_filename"]),
            attachment["original_filename"],
        )


    app.add_url_rule('/company-attachments/<int:attachment_id>/download', endpoint='download_company_attachment', view_func=download_company_attachment, methods=['GET'])
    app.add_url_rule('/company-attachments/<int:attachment_id>/preview', endpoint='preview_company_attachment', view_func=preview_company_attachment, methods=['GET'])
    return {'download_company_attachment': download_company_attachment, 'preview_company_attachment': preview_company_attachment}


def register_company_attachment_delete_routes(app, *,
    _cb_resolve_COMPANY_ATTACHMENT_DIR,
    _cb_resolve_abort,
    _cb_resolve_db,
    _cb_resolve_flash,
    _cb_resolve_login_required,
    _cb_resolve_os,
    _cb_resolve_redirect,
    _cb_resolve_url_for,
):
    @_cb_resolve_login_required()
    def delete_company_attachment(attachment_id):
        attachment = _cb_resolve_db()().execute("select * from company_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            _cb_resolve_abort()(404)
        try:
            _cb_resolve_os().remove(_cb_resolve_os().path.join(_cb_resolve_COMPANY_ATTACHMENT_DIR(), attachment["stored_filename"]))
        except FileNotFoundError:
            pass
        _cb_resolve_db()().execute("delete from company_attachments where id = ?", (attachment_id,))
        _cb_resolve_db()().commit()
        _cb_resolve_flash()("公司附件已删除。", "success")
        return _cb_resolve_redirect()(_cb_resolve_url_for()("company_info"))


    @_cb_resolve_login_required()
    def legacy_company_settings():
        return _cb_resolve_redirect()(_cb_resolve_url_for()("system_settings"))


    app.add_url_rule('/company-attachments/<int:attachment_id>/delete', endpoint='delete_company_attachment', view_func=delete_company_attachment, methods=['POST'])
    app.add_url_rule('/settings/company', endpoint='legacy_company_settings', view_func=legacy_company_settings, methods=['GET'])
    return {'delete_company_attachment': delete_company_attachment, 'legacy_company_settings': legacy_company_settings}
