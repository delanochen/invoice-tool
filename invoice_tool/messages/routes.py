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
