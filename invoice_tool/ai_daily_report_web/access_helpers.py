"""Legacy-compatible ai_access helpers; explicit runtime dependencies."""

def ai_daily_report_csrf_token(*, secrets, session):
    """Generate/retrieve CSRF token for AI Daily Report Review Center.

    Stored in session, returned to frontend, validated via X-CSRF-Token header.
    Separate from field_token to avoid scope confusion.
    """
    if "ai_daily_report_csrf" not in session:
        session["ai_daily_report_csrf"] = secrets.token_urlsafe(32)
    return session["ai_daily_report_csrf"]


def require_ai_daily_report_csrf(*, abort, request, secrets, session):
    """Validate CSRF token for AI Daily Report mutation APIs.

    Checks X-CSRF-Token header against session token.
    Aborts 403 on mismatch.
    GET requests do not require CSRF.
    """
    token = request.headers.get("X-CSRF-Token", "")
    expected = session.get("ai_daily_report_csrf", "")
    if not expected or not secrets.compare_digest(token, expected):
        abort(403, description="CSRF token missing or invalid")


def can_view_ai_daily_report_draft(user, draft_row, *, json):
    """Check if user can view an AI Daily Report Draft.

    Rules:
    - admin/manager/finance: can view all drafts
    - employee: can view drafts where they are a worker, or drafts they created
    - external users: cannot view

    Args:
        user: current database row or dict
        draft_row: draft database row (must include created_by, draft_data)

    Returns:
        bool: True if user can view
    """
    if not user or not draft_row:
        return False

    # Convert database row to dict if needed
    if hasattr(user, "keys"):
        user = dict(user)
    if hasattr(draft_row, "keys"):
        draft_row = dict(draft_row)

    role = user.get("role", "")
    user_id = user.get("id", "")

    # Admin/manager/finance can view all
    if role in {"admin", "manager", "finance"}:
        return True

    # Employee can view drafts they created
    if role == "employee" and draft_row.get("created_by") == user_id:
        return True

    # Employee can view drafts where they are listed as a worker
    if role == "employee":
        try:
            draft_data = json.loads(draft_row.get("draft_data", "{}") or "{}")
            workers = draft_data.get("workers", [])
            for w in workers:
                if w.get("user_id") == user_id:
                    return True
        except (json.JSONDecodeError, TypeError):
            pass

    return False


def can_edit_ai_daily_report_draft(user, draft_row, *, can_view_ai_daily_report_draft):
    """Check if user can edit an AI Daily Report Draft.

    Rules:
    - admin/manager: can edit all drafts
    - finance: can view but NOT edit (read-only for finance)
    - employee: can edit drafts they created or where they are a worker
    - Draft must be in 'draft' status (confirmed/cancelled/saved are read-only)

    Args:
        user: current database row or dict
        draft_row: draft database row

    Returns:
        bool: True if user can edit
    """
    if not user or not draft_row:
        return False

    # Convert database row to dict if needed
    if hasattr(user, "keys"):
        user = dict(user)
    if hasattr(draft_row, "keys"):
        draft_row = dict(draft_row)

    # Status check: only 'draft' status can be edited
    status = draft_row.get("status", "")
    if status != "draft":
        return False

    role = user.get("role", "")

    # Admin/manager can edit all
    if role in {"admin", "manager"}:
        return True

    # Finance is read-only
    if role == "finance":
        return False

    # Employee can edit drafts they created or where they are a worker
    if role == "employee":
        return can_view_ai_daily_report_draft(user, draft_row)

    return False


def can_prepare_attachment_manifest(user, draft_row, *, can_view_ai_daily_report_draft):
    """Phase 8-specific authorization for Prepare Attachments (mutation).

    Does NOT reuse can_edit_ai_daily_report_draft (confirmed drafts are not
    editable there). Rules:
    - must satisfy can_view_ai_daily_report_draft (participant/admin/manager/
      external-admin client scope inherited from Phase 6)
    - finance is read-only: never allowed to prepare
    - draft must be status == 'confirmed' (chain B state machine)
    """
    if not user or not draft_row:
        return False
    if hasattr(user, "keys"):
        user = dict(user)
    if hasattr(draft_row, "keys"):
        draft_row = dict(draft_row)
    role = user.get("role", "")
    if role == "finance":
        return False
    if not can_view_ai_daily_report_draft(user, draft_row):
        return False
    if draft_row.get("status") != "confirmed":
        return False
    return True


def can_formal_save_draft(user, draft_row, *, can_view_ai_daily_report_draft):
    """Phase 9-specific authorization for Formal Save (mutation).

    Rules (sealed design):
    - must satisfy can_view_ai_daily_report_draft (Phase 6 client scope)
    - finance read-only: never allowed to formal save
    - external_manager / external_employee: keep current Phase 6-8 boundary
    - draft must be status == 'confirmed'
    """
    if not user or not draft_row:
        return False
    if hasattr(user, "keys"):
        user = dict(user)
    if hasattr(draft_row, "keys"):
        draft_row = dict(draft_row)
    role = user.get("role", "")
    if role == "finance":
        return False
    if not can_view_ai_daily_report_draft(user, draft_row):
        return False
    if draft_row.get("status") != "confirmed":
        return False
    return True


def ai_daily_report_draft_list_filters(user):
    """Return SQL WHERE clauses and params for draft list, filtered by user permission.

    Does NOT query all then filter in Python - filters at SQL level.

    Permission rules (same as can_view_ai_daily_report_draft):
    - admin/manager/finance: see all drafts
    - employee: see drafts they created OR where they are listed as a worker (by user_id)
    - external users: see nothing

    Worker matching uses PostgreSQL jsonb_array_elements to check
    draft_data.workers[].user_id.
    This is stable identity matching (user_id), NOT name matching, to prevent
    same-name employees from gaining unauthorized access.
    """
    if not user:
        return ["1=0"], []

    # Convert database row to dict (row objects do not support attribute access)
    if hasattr(user, "keys"):
        user = dict(user)

    role = user.get("role", "")
    user_id = user.get("id", "")

    # Admin/manager/finance see all
    if role in {"admin", "manager", "finance"}:
        return ["1=1"], []

    # Employee sees drafts they created OR where they are a worker (by stable user_id)
    if role == "employee":
        # PostgreSQL jsonb: expand workers[] and match user_id as text.
        # This prevents same-name employees from gaining access (name-based matching is unsafe)
        worker_clause = """EXISTS (
            SELECT 1 FROM jsonb_array_elements(ai_daily_report_drafts.draft_data::jsonb -> 'workers') w
            WHERE w.value ->> 'user_id' = ?::text
        )"""
        return [f"(created_by = ? OR {worker_clause})"], [user_id, user_id]

    return ["1=0"], []
