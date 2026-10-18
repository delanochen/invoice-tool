"""Business routes with explicit current-app resolvers; no reverse import."""


def register_messages_list_routes(app, *,
    _cb_resolve_db,
    _cb_resolve_g,
    _cb_resolve_login_required,
    _cb_resolve_render_template,
):
    @_cb_resolve_login_required()
    def messages():
        rows = _cb_resolve_db()().execute(
            "select * from messages where user_id = ? order by is_read asc, datetime(created_at) desc, id desc",
            (_cb_resolve_g().user["id"],),
        ).fetchall()
        return _cb_resolve_render_template()("messages.html", messages=rows)


    app.add_url_rule('/messages', endpoint='messages', view_func=messages, methods=['GET'])
    return {'messages': messages}


def register_messages_mark_routes(app, *,
    _cb_resolve__message_ids_for_user,
    _cb_resolve_db,
    _cb_resolve_flash,
    _cb_resolve_g,
    _cb_resolve_login_required,
    _cb_resolve_redirect,
    _cb_resolve_request,
    _cb_resolve_url_for,
):
    @_cb_resolve_login_required()
    def mark_messages():
        """把选中的消息标为已读或未读。

    对应页面上「标为已读 / 标为未读」两个按钮：未读的可以选中设为已读，
    已读的也可以选中设为未读。两个方向共用一个端点，靠 is_read 取值区分。
    """
        is_read = 1 if _cb_resolve_request().form.get("is_read") == "1" else 0
        ids = _cb_resolve__message_ids_for_user()(_cb_resolve_request().form.getlist("message_id"))
        if not ids:
            _cb_resolve_flash()("请先勾选要标记的消息。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("messages"))
        placeholders = ",".join("?" for _ in ids)
        _cb_resolve_db()().execute(
            f"update messages set is_read = ? where user_id = ? and id in ({placeholders})",
            (is_read, _cb_resolve_g().user["id"], *ids),
        )
        _cb_resolve_db()().commit()
        _cb_resolve_flash()(f"已将 {len(ids)} 条消息标为{'已读' if is_read else '未读'}。", "success")
        return _cb_resolve_redirect()(_cb_resolve_url_for()("messages"))


    app.add_url_rule('/messages/mark', endpoint='mark_messages', view_func=mark_messages, methods=['POST'])
    return {'mark_messages': mark_messages}
