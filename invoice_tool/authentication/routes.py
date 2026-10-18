"""Business routes with explicit current-app resolvers; no reverse import."""


def register_authentication_routes(app, *,
    _cb_resolve_DEFAULT_LANGUAGE,
    _cb_resolve_IntegrityError,
    _cb_resolve_SUPPORTED_LANGUAGES,
    _cb_resolve_check_password_hash,
    _cb_resolve_clear_session_preserving_language,
    _cb_resolve_communication_languages_from_form,
    _cb_resolve_country_from_form,
    _cb_resolve_country_rows,
    _cb_resolve_current_language,
    _cb_resolve_db,
    _cb_resolve_flash,
    _cb_resolve_generate_password_hash,
    _cb_resolve_is_safe_redirect_target,
    _cb_resolve_log_login_action,
    _cb_resolve_normalize_phone,
    _cb_resolve_notify_role,
    _cb_resolve_now,
    _cb_resolve_re,
    _cb_resolve_redirect,
    _cb_resolve_render_template,
    _cb_resolve_request,
    _cb_resolve_session,
    _cb_resolve_url_for,
):
    def login():
        if _cb_resolve_request().method == "POST":
            language = _cb_resolve_request().form.get("language")
            if language in _cb_resolve_SUPPORTED_LANGUAGES():
                _cb_resolve_session()["language"] = language
            email = _cb_resolve_request().form.get("email", "").strip().lower()
            password = _cb_resolve_request().form.get("password", "")
            user = _cb_resolve_db()().execute("select * from users where email = ?", (email,)).fetchone()
            if user and _cb_resolve_check_password_hash()(user["password_hash"], password):
                if not user["is_active"]:
                    _cb_resolve_flash()("账号尚未启用，请等待管理员或经理批准。", "error")
                    return _cb_resolve_redirect()(_cb_resolve_url_for()("login"))
                _cb_resolve_clear_session_preserving_language()()
                user_language = user["default_language"] if "default_language" in user.keys() else _cb_resolve_DEFAULT_LANGUAGE()
                _cb_resolve_session()["language"] = user_language if user_language in _cb_resolve_SUPPORTED_LANGUAGES() else _cb_resolve_DEFAULT_LANGUAGE()
                _cb_resolve_session()["user_id"] = user["id"]
                _cb_resolve_session().permanent = True
                _cb_resolve_log_login_action()(user)
                _cb_resolve_db()().commit()
                next_target = _cb_resolve_request().args.get("next")
                if not _cb_resolve_is_safe_redirect_target()(next_target):
                    next_target = _cb_resolve_url_for()("dashboard")
                return _cb_resolve_redirect()(next_target)
            _cb_resolve_flash()("邮箱或密码不正确。", "error")
        return _cb_resolve_render_template()("login.html")


    def register():
        if _cb_resolve_request().method == "POST":
            name = _cb_resolve_request().form.get("name", "").strip()
            english_name = _cb_resolve_request().form.get("english_name", "").strip()
            address = _cb_resolve_request().form.get("address", "").strip()
            email = _cb_resolve_request().form.get("registration_email", "").strip().lower()
            password = _cb_resolve_request().form.get("registration_password", "")
            password_confirm = _cb_resolve_request().form.get("registration_password_confirm", "")
            account_type = _cb_resolve_request().form.get("account_type", "external_employee")
            country = _cb_resolve_country_from_form()()
            preferred_language, communication_languages = _cb_resolve_communication_languages_from_form()(_cb_resolve_current_language()())
            if account_type not in {"employee", "external_employee"}:
                account_type = "external_employee"
            if not address:
                _cb_resolve_flash()("请填写地址。", "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("register"))
            try:
                phone = _cb_resolve_normalize_phone()(_cb_resolve_request().form.get("phone"), country["code"])
            except ValueError as error:
                _cb_resolve_flash()(str(error), "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("register"))
            if "�" in name:
                _cb_resolve_flash()("姓名包含损坏字符，请重新输入正确姓名。", "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("register"))
            if english_name and not _cb_resolve_re().fullmatch(r"[A-Za-z ]+", english_name):
                _cb_resolve_flash()("英文名只能包含英文字母和空格，不能包含中文、数字或其他符号。", "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("register"))
            if len(password) < 8:
                _cb_resolve_flash()("密码至少需要 8 位。", "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("register"))
            if password != password_confirm:
                _cb_resolve_flash()("两次输入的密码不一致。", "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("register"))
            try:
                cursor = _cb_resolve_db()().execute(
                    """
                insert into users (
                    name, english_name, email, password_hash, role, is_active, default_language, region_code, country_code,
                    created_at, address, phone, preferred_communication_language, communication_languages
                )
                values (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        name or email,
                        english_name,
                        email,
                        _cb_resolve_generate_password_hash()(password),
                        account_type,
                        _cb_resolve_current_language()(),
                        country["region_code"],
                        country["code"],
                        _cb_resolve_now()(),
                        address,
                        phone,
                        preferred_language,
                        communication_languages,
                    ),
                )
                user_id = cursor.lastrowid
                account_label = "员工" if account_type == "employee" else "外部员工"
                _cb_resolve_notify_role()(
                    ["admin", "manager"],
                    f"新{account_label}账号待批准",
                    f"{name or email} 已注册{account_label}账号，请审核并启用。",
                    _cb_resolve_url_for()("users"),
                )
                _cb_resolve_db()().commit()
                _cb_resolve_flash()("注册成功，请等待管理员或经理批准启用。", "success")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("login"))
            except _cb_resolve_IntegrityError():
                _cb_resolve_flash()("这个邮箱已经注册。", "error")
        return _cb_resolve_render_template()("register.html", countries=_cb_resolve_country_rows()())


    app.add_url_rule('/login', endpoint='login', view_func=login, methods=['GET', 'POST'])
    app.add_url_rule('/register', endpoint='register', view_func=register, methods=['GET', 'POST'])
    return {'login': login, 'register': register}
