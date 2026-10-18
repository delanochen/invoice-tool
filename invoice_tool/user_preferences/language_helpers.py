"""Legacy-compatible residual_language helpers; explicit runtime dependencies."""

def current_language(*, DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, session):
    language = session.get("language", DEFAULT_LANGUAGE)
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def language_from_form(default, *, DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, request):
    language = request.form.get("default_language", default or DEFAULT_LANGUAGE)
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def clear_session_preserving_language(*, current_language, session):
    language = current_language()
    session.clear()
    session["language"] = language
