"""AI Daily Report - EmployeeResolutionService (Phase 2)

Resolves natural language employee names to real users.id.

Resolution priority (strict, no auto-guessing):
1. Self-reference ("我"/"我自己"/"本人"/"me"/"myself") -> current_user.id
   (validated: user exists, is_active, has worker-eligible role)
2. Exact name match (name = ?) -> that user (if unique)
3. Configured alias/nickname -> user_id (FUTURE: employee_aliases table)
   Not implemented in Phase 2; extension point reserved.

Partial/LIKE match is NEVER auto-resolved. It only produces clarification
candidates. Even if only one partial match exists, the user must confirm.

Rules:
- Multiple same-name exact matches -> clarification_required, return masked candidates
- Not found -> clarification_required, never auto-create
- Inactive users -> never resolvable
- Never sends full employee data (email/address) to DeepSeek
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SELF_REFERENCES = {"我", "我自己", "本人", "自己", "me", "myself", "self"}

# Roles eligible to be daily report workers.
# Must match real ROLE_OPTIONS keys in app.py (there is no role named "user").
# Internal field staff use role "employee"; without it, almost all name
# resolution (including self-reference "我") fails silently.
# external_manager / external_employee are included too: a daily report often
# records work performed by external staff (user decision 2026-09-16).
# Being resolvable as a worker is separate from UI/Review-Center permissions,
# which remain unchanged for external roles.
WORKER_ELIGIBLE_ROLES = {
    "admin", "manager", "finance", "employee",
    "external_manager", "external_employee",
}

# FUTURE: employee_aliases table will map alias -> user_id
# Phase 2 does not implement aliases; this constant documents the extension point.
ALIAS_TABLE_NOT_IMPLEMENTED = True


class EmployeeResolutionResult:
    """Result of resolving an employee name."""
    def __init__(
        self,
        resolved: bool,
        user_id: Optional[int] = None,
        name: Optional[str] = None,
        clarification_required: bool = False,
        clarification_question: Optional[str] = None,
        candidates: Optional[List[Dict[str, Any]]] = None,
        error: Optional[str] = None,
    ):
        self.resolved = resolved
        self.user_id = user_id
        self.name = name
        self.clarification_required = clarification_required
        self.clarification_question = clarification_question
        self.candidates = candidates or []
        self.error = error


class EmployeeResolutionService:
    """Resolve employee names to database users.

    Search scope: ALL active internal users (users table), not just
    workers already associated with the service order. A worker may
    join a site temporarily even if not pre-assigned to the order.
    """

    def __init__(self, db_conn, current_user_id: int, current_user_name: str):
        self.db = db_conn
        self.current_user_id = current_user_id
        self.current_user_name = current_user_name

    def resolve(self, name: str) -> EmployeeResolutionResult:
        """Resolve a natural language name to a user.

        Priority: self-reference -> exact match -> (future alias).
        Partial match only produces candidates, never auto-resolves.
        """
        name = (name or "").strip()
        if not name:
            return EmployeeResolutionResult(
                resolved=False,
                clarification_required=True,
                clarification_question="员工姓名不能为空。",
            )

        # 1. Self-reference -> current user (validated)
        if name.lower() in SELF_REFERENCES:
            return self._resolve_self()

        # 2. Exact name match
        rows = self.db.execute(
            "select id, name, email, role, is_active from users where name = ?",
            (name,),
        ).fetchall()

        # Filter to active, worker-eligible users
        eligible = [r for r in rows if r["is_active"] == 1 and r["role"] in WORKER_ELIGIBLE_ROLES]

        if len(eligible) == 1:
            return EmployeeResolutionResult(
                resolved=True,
                user_id=eligible[0]["id"],
                name=eligible[0]["name"],
            )

        if len(eligible) > 1:
            return self._ambiguous_result(name, eligible)

        # 3. Partial match -> candidates only, NEVER auto-resolve
        partial = self.db.execute(
            "select id, name, email, role, is_active from users where name like ?",
            (f"%{name}%",),
        ).fetchall()
        partial_eligible = [r for r in partial if r["is_active"] == 1 and r["role"] in WORKER_ELIGIBLE_ROLES]

        if partial_eligible:
            return EmployeeResolutionResult(
                resolved=False,
                clarification_required=True,
                clarification_question=(
                    f"未找到精确匹配「{name}」的员工。以下是可能的候选，请确认具体人员。"
                ),
                candidates=[
                    {"user_id": r["id"], "name": r["name"], "email_masked": self._mask_email(r["email"])}
                    for r in partial_eligible
                ],
                error="partial_match_requires_confirmation",
            )

        # Not found at all
        return EmployeeResolutionResult(
            resolved=False,
            clarification_required=True,
            clarification_question=f"系统中没有找到员工「{name}」，请确认姓名或先创建员工。",
            error="employee_not_found",
        )

    def _resolve_self(self) -> EmployeeResolutionResult:
        """Resolve self-reference with validation."""
        row = self.db.execute(
            "select id, name, role, is_active from users where id = ?",
            (self.current_user_id,),
        ).fetchone()

        if not row:
            return EmployeeResolutionResult(
                resolved=False,
                clarification_required=True,
                clarification_question="当前用户不存在，请重新登录。",
                error="current_user_not_found",
            )

        if row["is_active"] != 1:
            return EmployeeResolutionResult(
                resolved=False,
                clarification_required=True,
                clarification_question="当前用户已被停用，无法作为日报工作人员。",
                error="current_user_inactive",
            )

        if row["role"] not in WORKER_ELIGIBLE_ROLES:
            return EmployeeResolutionResult(
                resolved=False,
                clarification_required=True,
                clarification_question=f"当前用户角色（{row['role']}）无资格作为现场工作人员。",
                error="current_user_not_worker_eligible",
            )

        return EmployeeResolutionResult(
            resolved=True,
            user_id=row["id"],
            name=row["name"],
        )

    def _ambiguous_result(self, name: str, rows: List[Any]) -> EmployeeResolutionResult:
        """Build clarification result for ambiguous name matches."""
        candidates = []
        for r in rows:
            candidates.append({
                "user_id": r["id"],
                "name": r["name"],
                "email_masked": self._mask_email(r["email"]),
            })
        return EmployeeResolutionResult(
            resolved=False,
            clarification_required=True,
            clarification_question=(
                f"系统中找到 {len(rows)} 个叫「{name}」的员工，请选择具体人员。"
            ),
            candidates=candidates,
        )

    @staticmethod
    def _mask_email(email: Optional[str]) -> str:
        """Mask email for display: z***@company.com"""
        if not email or "@" not in email:
            return "***"
        local, domain = email.split("@", 1)
        if len(local) <= 1:
            return f"{local[0]}***@{domain}"
        return f"{local[0]}***@{domain}"

    def resolve_workers(
        self, worker_inputs: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[EmployeeResolutionResult]]:
        """Resolve a list of worker inputs from DeepSeek.

        Args:
            worker_inputs: list of {"name": ..., "origin": ..., "transportation": ...}

        Returns:
            (resolved_workers, failures)
            resolved_workers: list with user_id added
            failures: list of EmployeeResolutionResult that need clarification
        """
        resolved = []
        failures = []

        for wi in worker_inputs:
            result = self.resolve(wi.get("name", ""))
            if result.resolved:
                resolved.append({
                    "user_id": result.user_id,
                    "name": result.name,
                    # None = "not mentioned": pass through so update_worker
                    # keeps the existing mode (and create defaults to
                    # self_drive later in _apply_update_worker).
                    "transportation": wi.get("transportation"),
                    "origin": wi.get("origin"),
                })
            else:
                failures.append(result)

        return resolved, failures

    def get_employee_default_address(self, user_id: int) -> Optional[str]:
        """Get employee's default address (users.address).

        This is a SUGGESTION only, must be marked origin_confirmed=False.
        """
        row = self.db.execute(
            "select address from users where id = ?",
            (user_id,),
        ).fetchone()
        if row and row["address"]:
            return row["address"].strip()
        return None
