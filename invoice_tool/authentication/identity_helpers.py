"""Legacy-compatible identity helpers; explicit runtime dependencies."""

def current_user(*, clear_session_preserving_language, db, session):
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = db().execute(
        """
        select users.*, employee_grades.grade_name as employee_grade_name
        from users
        left join employee_grades on employee_grades.id = users.employee_grade_id
        where users.id = ?
        """,
        (user_id,),
    ).fetchone()
    if user and not user["is_active"]:
        clear_session_preserving_language()
        return None
    return user


def login_required(view, *, g, redirect, request, url_for, wraps):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def is_safe_redirect_target(target, *, unquote, urlsplit):
    if not isinstance(target, str):
        return False
    candidate = target.strip()
    if not candidate:
        return False
    for _ in range(3):
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in candidate):
            return False
        if "\\" in candidate:
            return False
        parsed = urlsplit(candidate)
        if not candidate.startswith("/") or candidate.startswith("//"):
            return False
        if parsed.scheme or parsed.netloc:
            return False
        decoded = unquote(candidate)
        if decoded == candidate:
            return True
        candidate = decoded
    return False


def admin_required(view, *, abort, g, redirect, request, url_for, wraps):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def is_external_user(*, g, normalized_role):
    return g.user and normalized_role() in {"external_manager", "external_employee"}


def is_external_manager(*, g, normalized_role):
    return g.user and normalized_role() == "external_manager"


def is_external_employee(*, g, normalized_role):
    return g.user and normalized_role() == "external_employee"


def normalized_role(role=None, *, g):
    role = role if role is not None else (g.user["role"] if g.user else "")
    if role == "internal":
        return "finance"
    if role == "user":
        return "employee"
    if role == "external":
        return "external_manager"
    return role


def requires_user_address(role, *, normalized_role):
    return normalized_role(role) not in {"external_manager", "external_employee"}


def is_internal_user(*, g, normalized_role):
    return g.user and normalized_role() in {"admin", "manager", "finance", "employee"}


def is_manager(*, g, normalized_role):
    return g.user and normalized_role() in {"admin", "manager"}
