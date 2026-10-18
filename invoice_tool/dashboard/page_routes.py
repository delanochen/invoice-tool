"""Business routes with explicit current-app resolvers; no reverse import."""


def register_workspace_pages_routes(app, *,
    _cb_resolve_login_required,
    _cb_resolve_redirect,
    _cb_resolve_render_template,
    _cb_resolve_url_for,
):
    @_cb_resolve_login_required()
    def workspace():
        return _cb_resolve_render_template()("workspace.html")


    @_cb_resolve_login_required()
    def report_center():
        return _cb_resolve_redirect()(_cb_resolve_url_for()("service_order_query"))


    app.add_url_rule('/workspace', endpoint='workspace', view_func=workspace, methods=['GET'])
    app.add_url_rule('/reports', endpoint='report_center', view_func=report_center, methods=['GET'])
    return {'workspace': workspace, 'report_center': report_center}
