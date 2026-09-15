"""AI Daily Report - Action Validator + Allowlist (Phase 1)

DeepSeek output -> JSON parse -> Pydantic validate -> Allowlist check -> idempotency check.
Any failure at any stage is rejected and logged; no partial execution.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional, Tuple

from pydantic import ValidationError

from .schemas import AIAction, ACTION_VERSION

# ─── Allowlist: intents and the fields they are allowed to touch ───────────

ALLOWED_INTENTS = {
    "create_daily_report",
    "update_daily_report",
    "update_worker",
    "add_work_item",
    "update_work_item",
    "remove_work_item",
    "add_waiting_time",
    "update_arrival_time",
    "update_departure_time",
    "change_safety_photo",
    "add_service_photo",
    "remove_service_photo",
    "recalculate_mileage",
    "submit_daily_report",
    "clarify",
}

# Intents that are implemented (Phase 1 + Phase 3A)
PHASE1_IMPLEMENTED_INTENTS = {
    "create_daily_report",
    "update_daily_report",
    "update_worker",
    "add_work_item",
    "update_work_item",
    "remove_work_item",
    "add_waiting_time",
    "update_arrival_time",
    "update_departure_time",
    "clarify",
    "recalculate_mileage",
}


class ValidationResult:
    """Result of action validation."""
    def __init__(
        self,
        ok: bool,
        action: Optional[AIAction] = None,
        error: Optional[str] = None,
        error_code: Optional[str] = None,
    ):
        self.ok = ok
        self.action = action
        self.error = error
        self.error_code = error_code

    def __bool__(self) -> bool:
        return self.ok


def parse_and_validate(raw_text: str) -> ValidationResult:
    """Parse DeepSeek output text into a validated AIAction.

    Steps:
    1. Strip markdown code fences if present (defensive; should not happen).
    2. JSON parse.
    3. Pydantic validate (this enforces action_version, intent enum, field types).
    4. Allowlist check (redundant with Pydantic enum, but explicit).
    """
    if not raw_text or not raw_text.strip():
        return ValidationResult(False, error="AI 返回为空", error_code="empty_response")

    text = raw_text.strip()
    # Defensive: strip ```json ... ``` fences
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return ValidationResult(
            False, error=f"AI 返回了非法 JSON: {exc}", error_code="invalid_json"
        )

    if not isinstance(payload, dict):
        return ValidationResult(
            False, error="AI 返回的 JSON 不是对象", error_code="not_an_object"
        )

    try:
        action = AIAction(**payload)
    except ValidationError as exc:
        first_error = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first_error.get("loc", []))
        msg = first_error.get("msg", "unknown")
        return ValidationResult(
            False,
            error=f"Action 校验失败 [{loc}]: {msg}",
            error_code="validation_error",
        )

    if action.intent not in ALLOWED_INTENTS:
        return ValidationResult(
            False,
            error=f"Intent 不在允许列表中: {action.intent}",
            error_code="intent_not_allowed",
        )

    return ValidationResult(True, action=action)


def is_phase1_implemented(action: AIAction) -> bool:
    """Check if this intent is implemented in Phase 1."""
    return action.intent in PHASE1_IMPLEMENTED_INTENTS


def generate_action_id() -> str:
    """Generate a unique action ID for idempotency."""
    return uuid.uuid4().hex


def check_idempotency(
    draft_id: int, action_id: str, existing_action_ids: set
) -> Tuple[bool, Optional[str]]:
    """Check if this action has already been executed.

    Returns (is_duplicate, error_message).
    """
    if action_id in existing_action_ids:
        return True, f"Action {action_id} 已经执行过，跳过（幂等）"
    return False, None
