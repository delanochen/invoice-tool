"""Business routes with explicit current-app resolvers; no reverse import."""


def register_language_session_routes(app, *,
    _cb_resolve_SUPPORTED_LANGUAGES,
    _cb_resolve_clear_session_preserving_language,
    _cb_resolve_g,
    _cb_resolve_parse_qsl,
    _cb_resolve_redirect,
    _cb_resolve_request,
    _cb_resolve_session,
    _cb_resolve_url_for,
    _cb_resolve_urlencode,
    _cb_resolve_urlsplit,
    _cb_resolve_urlunsplit,
):
    def logout():
        _cb_resolve_clear_session_preserving_language()()
        return _cb_resolve_redirect()(_cb_resolve_url_for()("login"))


    def switch_language():
        language = _cb_resolve_request().form.get("language", "")
        if language in _cb_resolve_SUPPORTED_LANGUAGES():
            _cb_resolve_session()["language"] = language
        next_url = _cb_resolve_request().form.get("next", "").strip()
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = _cb_resolve_url_for()("dashboard") if _cb_resolve_g().user else _cb_resolve_url_for()("login")
        else:
            parsed = _cb_resolve_urlsplit()(next_url)
            cleaned_query = _cb_resolve_urlencode()(
                [(key, value) for key, value in _cb_resolve_parse_qsl()(parsed.query, keep_blank_values=True) if key != "lang"],
                doseq=True,
            )
            next_url = _cb_resolve_urlunsplit()(("", "", parsed.path, cleaned_query, parsed.fragment))
        return _cb_resolve_redirect()(next_url)


    app.add_url_rule('/logout', endpoint='logout', view_func=logout, methods=['GET'])
    app.add_url_rule('/language', endpoint='switch_language', view_func=switch_language, methods=['POST'])
    return {'logout': logout, 'switch_language': switch_language}
