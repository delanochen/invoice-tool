"""Legacy-compatible residual_hooks helpers; explicit runtime dependencies."""

def prevent_stale_business_pages(response):
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def load_user(*, SUPPORTED_LANGUAGES, abort, app, current_user, g, has_action_permission, request, required_action_for_request, session):
    requested_language = request.args.get("lang")
    if requested_language in SUPPORTED_LANGUAGES:
        session["language"] = requested_language
    g.user = current_user()
    if request.method == "POST":
        app.logger.warning("POST diag: path=%s content_length=%s max_cl=%s max_fms=%s terminated=%s",
            request.path, request.content_length,
            getattr(request, 'max_content_length', 'N/A'),
            getattr(request, 'max_form_memory_size', 'N/A'),
            request.environ.get('wsgi.input_terminated'))
    if g.user:
        permission_rule = required_action_for_request()
        if permission_rule and not has_action_permission(*permission_rule):
            abort(403)


def inject_globals(*, APP_VERSION, SUPPORTED_LANGUAGES, app, can_manage_company_info, current_language, unread_message_count):
    return {
        "unread_message_count": unread_message_count(),
        "current_language": current_language(),
        "supported_languages": SUPPORTED_LANGUAGES,
        "can_manage_company_info": can_manage_company_info,
        "app_version": APP_VERSION,
        "deployment_env": app.config["DEPLOYMENT_ENV"],
        "production_postgres": True,
    }
