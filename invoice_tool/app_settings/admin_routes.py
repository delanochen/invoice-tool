"""Business routes with explicit current-app resolvers; no reverse import."""

from .release_notes import load_release_notes, release_notes_context


def register_system_settings_routes(app, *,
    _cb_resolve_ACTION_LABELS,
    _cb_resolve_DEFAULT_SMTP_SETTINGS,
    _cb_resolve_DatabaseError,
    _cb_resolve_GOOGLE_PLACES_API_KEY_ENV,
    _cb_resolve_MENU_PERMISSION_GROUPS,
    _cb_resolve_ROLE_ACTION_PERMISSION_GROUPS,
    _cb_resolve_ROLE_OPTIONS,
    _cb_resolve_admin_required,
    _cb_resolve_db,
    _cb_resolve_flash,
    _cb_resolve_g,
    _cb_resolve_get_company_profile,
    _cb_resolve_get_google_geocoding_api_key,
    _cb_resolve_get_google_maps_browser_api_key,
    _cb_resolve_get_invoice_terms,
    _cb_resolve_get_payment_instructions,
    _cb_resolve_get_setting,
    _cb_resolve_get_smtp_settings,
    _cb_resolve_inspection_cycle_days,
    _cb_resolve_inspection_warning_days,
    _cb_resolve_is_read_sql,
    _cb_resolve_llm_config,
    _cb_resolve_log_action,
    _cb_resolve_login_required,
    _cb_resolve_now,
    _cb_resolve_permission_tree_groups,
    _cb_resolve_positive_int,
    _cb_resolve_re,
    _cb_resolve_redirect,
    _cb_resolve_render_template,
    _cb_resolve_request,
    _cb_resolve_set_setting,
    _cb_resolve_sql_statement_kind,
    _cb_resolve_url_for,
):
    @_cb_resolve_login_required()
    def system_settings():
        if _cb_resolve_request().method == "POST":
            # 大模型配置表的行级操作（删除 / 停用切换）先行处理，不保存其余表单
            llm_action = _cb_resolve_request().form.get("llm_action", "").strip()
            llm_target_id = _cb_resolve_request().form.get("llm_target_id", "").strip()
            if llm_action == "delete" and llm_target_id:
                _row = _cb_resolve_llm_config().get_row(_cb_resolve_db()(), llm_target_id)
                if _row is None:
                    _cb_resolve_flash()("要删除的配置不存在。", "error")
                else:
                    _used = [
                        label for key, label in _cb_resolve_llm_config().SCENES
                        if _cb_resolve_get_setting()("llm_scene_" + key, "").strip() == llm_target_id
                    ]
                    if _used:
                        _cb_resolve_flash()(f"该配置正被「{'、'.join(_used)}」使用，请先调整场景映射后再删除。", "error")
                    else:
                        _cb_resolve_db()().execute("delete from llm_configs where id = ?", (int(llm_target_id),))
                        _cb_resolve_db()().commit()
                        _cb_resolve_flash()("配置已删除。", "success")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("system_settings"))
            if llm_action == "toggle" and llm_target_id:
                _row = _cb_resolve_llm_config().get_row(_cb_resolve_db()(), llm_target_id)
                if _row is None:
                    _cb_resolve_flash()("要操作的配置不存在。", "error")
                else:
                    _next = not bool(_row["enabled"])
                    _cb_resolve_db()().execute(
                        "update llm_configs set enabled = ?, updated_at = ? where id = ?",
                        (_next, _cb_resolve_now()(), int(llm_target_id)),
                    )
                    _cb_resolve_db()().commit()
                    _cb_resolve_flash()("配置已" + ("停用" if _row["enabled"] else "启用") + "。", "success")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("system_settings"))
            warning_days = _cb_resolve_positive_int()(_cb_resolve_request().form.get("inspection_warning_days"), 150)
            cycle_days = _cb_resolve_positive_int()(_cb_resolve_request().form.get("inspection_cycle_days"), 180)
            if warning_days >= cycle_days:
                _cb_resolve_flash()("预警到期天数必须小于巡检周期天数。", "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("system_settings"))
            # 收款信息（payment_* / invoice_terms / company_tax_note）v0.1.325 起迁到「公司信息」页编辑，此处不再保存
            for key in _cb_resolve_DEFAULT_SMTP_SETTINGS():
                _cb_resolve_set_setting()(f"smtp_{key}", _cb_resolve_request().form.get(f"smtp_{key}", "").strip())
            _cb_resolve_set_setting()(
                "google_maps_browser_api_key",
                _cb_resolve_request().form.get("google_maps_browser_api_key", "").strip(),
            )
            _cb_resolve_set_setting()(
                "google_geocoding_api_key",
                _cb_resolve_request().form.get("google_geocoding_api_key", "").strip(),
            )
            _cb_resolve_set_setting()(
                "google_places_api_key",
                _cb_resolve_request().form.get("google_places_api_key", "").strip(),
            )
            _cb_resolve_set_setting()("inspection_warning_days", str(warning_days))
            _cb_resolve_set_setting()("inspection_cycle_days", str(cycle_days))
            _cb_resolve_set_setting()("field_watermark_time_password", _cb_resolve_request().form.get("field_watermark_time_password", "").strip())
            # 报销智能审核夜间批量（worker 读取）
            _cb_resolve_set_setting()("ai_review_enabled", "true" if _cb_resolve_request().form.get("ai_review_enabled") == "true" else "false")
            ai_review_time = _cb_resolve_request().form.get("ai_review_time", "03:00").strip()
            if not _cb_resolve_re().fullmatch(r"([01]\d|2[0-3]):[0-5]\d", ai_review_time):
                _cb_resolve_flash()("智能审核执行时间格式应为 HH:MM（例如 03:00）。", "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("system_settings"))
            _cb_resolve_set_setting()("ai_review_time", ai_review_time)
            # 场景映射（大模型配置表的行）
            for _key, _label in _cb_resolve_llm_config().SCENES:
                _cb_resolve_set_setting()("llm_scene_" + _key, _cb_resolve_request().form.get("llm_scene_" + _key, "").strip())
            # 大模型配置：修改（模态框）或新增（模态框）
            if llm_action == "edit":
                _edit_id = _cb_resolve_request().form.get("llm_edit_id", "").strip()
                _row = _cb_resolve_llm_config().get_row(_cb_resolve_db()(), _edit_id) if _edit_id else None
                if _row is None:
                    _cb_resolve_flash()("要修改的配置不存在。", "error")
                else:
                    _api_key = _cb_resolve_request().form.get("llm_new_api_key", "").strip()
                    if not _api_key:
                        _api_key = _row["api_key"]
                    _cb_resolve_db()().execute(
                        "update llm_configs set name=?, base_url=?, api_key=?, model=?, supports_vision=?, timeout_seconds=?, enabled=?, notes=?, updated_at=? where id=?",
                        (_cb_resolve_request().form.get("llm_new_name", "").strip() or _row["name"],
                         _cb_resolve_request().form.get("llm_new_base_url", "").strip() or _row["base_url"],
                         _api_key,
                         _cb_resolve_request().form.get("llm_new_model", "").strip() or _row["model"],
                         _cb_resolve_request().form.get("llm_new_supports_vision") == "on",
                         int(_cb_resolve_request().form.get("llm_new_timeout", "") or _row["timeout_seconds"]),
                         _cb_resolve_request().form.get("llm_new_enabled") == "on",
                         _cb_resolve_request().form.get("llm_new_notes", "").strip(),
                         _cb_resolve_now()(),
                         int(_edit_id)),
                    )
                    _cb_resolve_db()().commit()
                    _cb_resolve_flash()("配置已更新。", "success")
            elif llm_action == "new":
                _new_name = _cb_resolve_request().form.get("llm_new_name", "").strip()
                if not _new_name:
                    _cb_resolve_flash()("新增配置：名称不能为空。", "error")
                else:
                    _cb_resolve_db()().execute(
                        "insert into llm_configs (name, base_url, api_key, model, supports_vision, timeout_seconds, enabled, notes, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (_new_name,
                         _cb_resolve_request().form.get("llm_new_base_url", "").strip(),
                         _cb_resolve_request().form.get("llm_new_api_key", "").strip(),
                         _cb_resolve_request().form.get("llm_new_model", "").strip(),
                         _cb_resolve_request().form.get("llm_new_supports_vision") == "on",
                         int(_cb_resolve_request().form.get("llm_new_timeout", "300") or 300),
                         _cb_resolve_request().form.get("llm_new_enabled") == "on",
                         _cb_resolve_request().form.get("llm_new_notes", "").strip(),
                         _cb_resolve_now()(), _cb_resolve_now()()),
                    )
                    _cb_resolve_db()().commit()
                    _cb_resolve_flash()("配置已新增。", "success")
            else:
                _cb_resolve_db()().commit()
                _cb_resolve_flash()("系统设置已保存。", "success")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("system_settings"))
        return _cb_resolve_render_template()(
            "company_settings.html",
            company=_cb_resolve_get_company_profile()(),
            payment=_cb_resolve_get_payment_instructions()(),
            smtp=_cb_resolve_get_smtp_settings()(),
            terms=_cb_resolve_get_invoice_terms()(),
            google_maps_browser_api_key=_cb_resolve_get_google_maps_browser_api_key()(),
            google_geocoding_api_key=_cb_resolve_get_google_geocoding_api_key()(),
            google_places_api_key=_cb_resolve_get_setting()("google_places_api_key", _cb_resolve_GOOGLE_PLACES_API_KEY_ENV()).strip(),
            ai_review_enabled=_cb_resolve_get_setting()("ai_review_enabled", "true") == "true",
            ai_review_time=_cb_resolve_get_setting()("ai_review_time", "03:00"),
            llm_configs=_cb_resolve_llm_config().list_configs(_cb_resolve_db()()),
            llm_scenes=_cb_resolve_llm_config().SCENES,
            llm_scene_current={key: _cb_resolve_get_setting()("llm_scene_" + key, "") for key, _ in _cb_resolve_llm_config().SCENES},
            inspection_warning_days=_cb_resolve_inspection_warning_days()(),
            inspection_cycle_days=_cb_resolve_inspection_cycle_days()(),
            field_watermark_time_password=_cb_resolve_get_setting()("field_watermark_time_password", ""),
        )


    @_cb_resolve_admin_required()
    def menu_permissions():
        if _cb_resolve_request().method == "POST":
            menu_keys = [item["key"] for group in _cb_resolve_MENU_PERMISSION_GROUPS() for item in group["items"]]
            action_pairs = [
                (item["key"], action_key)
                for group in _cb_resolve_ROLE_ACTION_PERMISSION_GROUPS()
                for item in group["items"]
                for action_key in item["actions"]
            ]
            timestamp = _cb_resolve_now()()
            _cb_resolve_db()().execute("delete from role_menu_permissions")
            _cb_resolve_db()().execute("delete from role_action_permissions")
            for role in _cb_resolve_ROLE_OPTIONS():
                for menu_key in menu_keys:
                    enabled = 1 if _cb_resolve_request().form.get(f"menu__{role}__{menu_key}") == "1" else 0
                    _cb_resolve_db()().execute(
                        """
                    insert into role_menu_permissions (
                        role, menu_key, is_enabled, updated_by, updated_at
                    ) values (?, ?, ?, ?, ?)
                    """,
                        (role, menu_key, enabled, _cb_resolve_g().user["id"], timestamp),
                    )
                for resource_key, action_key in action_pairs:
                    enabled = 1 if _cb_resolve_request().form.get(f"action__{role}__{resource_key}__{action_key}") == "1" else 0
                    if action_key == "view" and _cb_resolve_request().form.get(f"menu__{role}__{resource_key}") == "1":
                        enabled = 1
                    _cb_resolve_db()().execute(
                        """
                    insert into role_action_permissions (
                        role, resource_key, action_key, is_enabled, updated_by, updated_at
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                        (role, resource_key, action_key, enabled, _cb_resolve_g().user["id"], timestamp),
                    )
            _cb_resolve_log_action()("update", "menu_permissions", None, "系统权限", "修改菜单和操作权限")
            _cb_resolve_db()().commit()
            _cb_resolve_g()._menu_permission_overrides = {
                (role, menu_key): _cb_resolve_request().form.get(f"menu__{role}__{menu_key}") == "1"
                for role in _cb_resolve_ROLE_OPTIONS()
                for menu_key in menu_keys
            }
            _cb_resolve_g()._action_permission_overrides = {
                (role, resource_key, action_key): (
                    _cb_resolve_request().form.get(f"action__{role}__{resource_key}__{action_key}") == "1"
                    or (
                        action_key == "view"
                        and _cb_resolve_request().form.get(f"menu__{role}__{resource_key}") == "1"
                    )
                )
                for role in _cb_resolve_ROLE_OPTIONS()
                for resource_key, action_key in action_pairs
            }
            _cb_resolve_flash()("权限配置已保存。", "success")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("menu_permissions"))
        return _cb_resolve_render_template()(
            "menu_permissions.html",
            role_options=_cb_resolve_ROLE_OPTIONS(),
            menu_groups=_cb_resolve_MENU_PERMISSION_GROUPS(),
            action_groups=_cb_resolve_ROLE_ACTION_PERMISSION_GROUPS(),
            action_labels=_cb_resolve_ACTION_LABELS(),
            permission_tree_groups=_cb_resolve_permission_tree_groups()(),
            users_by_role={
                role: _cb_resolve_db()().execute(
                    "select id, name, email, is_active from users where role = ? order by is_active desc, name",
                    (role,),
                ).fetchall()
                for role in _cb_resolve_ROLE_OPTIONS()
            },
        )


    @_cb_resolve_login_required()
    def database_console():
        sql = _cb_resolve_request().form.get("sql", "").strip() if _cb_resolve_request().method == "POST" else ""
        result = None
        columns = []
        rows = []
        row_count = None
        statement_kind = _cb_resolve_sql_statement_kind()(sql)
        if _cb_resolve_request().method == "POST":
            if not sql:
                _cb_resolve_flash()("请输入 SQL 语句。", "error")
            elif ";" in sql.rstrip(";"):
                _cb_resolve_flash()("为了降低误操作风险，每次只能执行一条 SQL 语句。", "error")
            else:
                read_query = _cb_resolve_is_read_sql()(sql)
                confirmed = _cb_resolve_request().form.get("confirm_write") == "1"
                if not read_query and not confirmed:
                    _cb_resolve_flash()("执行数据更改语句前，请先勾选确认。", "error")
                else:
                    try:
                        cursor = _cb_resolve_db()().execute(sql)
                        if cursor.description:
                            columns = [description[0] for description in cursor.description]
                            rows = cursor.fetchmany(500)
                            result = "query"
                            if cursor.fetchone() is not None:
                                _cb_resolve_flash()("查询结果超过 500 行，仅显示前 500 行。", "warning")
                            if not read_query:
                                _cb_resolve_log_action()(
                                    "sql",
                                    "database",
                                    None,
                                    "SQL Console",
                                    sql[:500],
                                )
                                _cb_resolve_db()().commit()
                                _cb_resolve_flash()("SQL 已执行。", "success")
                        else:
                            row_count = cursor.rowcount
                            result = "write"
                            _cb_resolve_log_action()(
                                "sql",
                                "database",
                                None,
                                "SQL Console",
                                sql[:500],
                            )
                            _cb_resolve_db()().commit()
                            _cb_resolve_flash()("SQL 已执行。", "success")
                    except _cb_resolve_DatabaseError() as error:
                        _cb_resolve_db()().rollback()
                        _cb_resolve_flash()(f"SQL 执行失败：{error}", "error")
        tables = _cb_resolve_db()().execute(
            """
        select table_name as name,
               case table_type when 'VIEW' then 'view' else 'table' end as type
        from information_schema.tables
        where table_schema = 'public'
        order by type, name
        """
        ).fetchall()
        table_dictionary = []
        for table in tables:
            table_dictionary.append(
                {
                    "name": table["name"],
                    "type": table["type"],
                    "columns": _cb_resolve_db()().execute(
                        """
                    select ordinal_position - 1 as cid, column_name as name,
                           data_type as type,
                           case is_nullable when 'NO' then 1 else 0 end as notnull,
                           column_default as dflt_value, 0 as pk
                    from information_schema.columns
                    where table_schema = 'public' and table_name = ?
                    order by ordinal_position
                    """,
                        (table["name"],),
                    ).fetchall(),
                }
            )
        return _cb_resolve_render_template()(
            "database_console.html",
            sql=sql,
            statement_kind=statement_kind,
            result=result,
            columns=columns,
            rows=rows,
            row_count=row_count,
            tables=tables,
            table_dictionary=table_dictionary,
        )


    @_cb_resolve_login_required()
    def release_notes():
        args = _cb_resolve_request().args
        return _cb_resolve_render_template()(
            'release_notes.html',
            **release_notes_context(load_release_notes(), args.get('q', ''), args.get('page', 1)),
        )

    app.add_url_rule('/settings/release-notes', endpoint='release_notes', view_func=release_notes, methods=['GET'])
    app.add_url_rule('/settings/system', endpoint='system_settings', view_func=system_settings, methods=['GET', 'POST'])
    app.add_url_rule('/settings/menu-permissions', endpoint='menu_permissions', view_func=menu_permissions, methods=['GET', 'POST'])
    app.add_url_rule('/settings/database', endpoint='database_console', view_func=database_console, methods=['GET', 'POST'])
    return {'system_settings': system_settings, 'menu_permissions': menu_permissions, 'database_console': database_console}
