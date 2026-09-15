"""AI Daily Report - Intent Service (Phase 1)

Calls DeepSeek with response_format=json_object to get structured Action JSON.
Reuses existing deepseek_assistant_settings() for API key/model config.
Does NOT use tool_calls — we want pure JSON output, validated by Pydantic.

Empty-content fallback: DeepSeek can return HTTP 200 with an empty content
field (especially in json_object mode). We retry once WITHOUT response_format
before surfacing "AI 返回为空" to the user.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .prompts import SYSTEM_PROMPT, build_user_prompt
from .action_validator import parse_and_validate, ValidationResult
from .schemas import AIAction

logger = logging.getLogger(__name__)

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MAX_TOKENS = 1200
DEFAULT_TEMPERATURE = 0.1
REQUEST_TIMEOUT = 60
DEFAULT_MODEL = "deepseek-chat"


class IntentServiceError(Exception):
    """Raised when DeepSeek call fails or returns invalid output after retries."""


class AIIntentService:
    """Parses natural language into validated AIAction using DeepSeek JSON mode."""

    def __init__(self, settings: Dict[str, Any]):
        """
        Args:
            settings: dict from deepseek_assistant_settings() with keys:
                enabled, api_key, model
        """
        self.api_key = settings.get("api_key", "").strip()
        self.model = settings.get("model", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self.enabled = settings.get("enabled", False) and bool(self.api_key)

    def is_available(self) -> bool:
        return self.enabled

    def parse_intent(
        self,
        user_message: str,
        current_business_date: str,
        current_timezone: str,
        service_order_id: int,
        service_order_number: str,
        site_address: str,
        current_user_name: str,
        existing_draft_summary: str = "",
        conversation_history: Optional[list] = None,
    ) -> ValidationResult:
        """Call DeepSeek and return a validated AIAction.

        Strategy: first request uses response_format=json_object; if the model
        returns empty content, retry once without response_format (fallback).
        Only if both attempts fail do we surface an error to the user.

        Returns ValidationResult. If ok=False, caller should show error to user.
        Never raises — all failures captured in ValidationResult.
        """
        if not self.enabled:
            return ValidationResult(
                False, error="DeepSeek 未启用或未配置 API Key", error_code="not_configured"
            )

        user_content = build_user_prompt(
            user_message=user_message,
            current_business_date=current_business_date,
            current_timezone=current_timezone,
            service_order_id=service_order_id,
            service_order_number=service_order_number,
            site_address=site_address,
            current_user_name=current_user_name,
            existing_draft_summary=existing_draft_summary,
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
        # Include recent conversation history (already cleaned, max ~6 turns)
        if conversation_history:
            messages.extend(conversation_history[-6:])
        messages.append({"role": "user", "content": user_content})

        # Attempt 1: JSON mode (response_format=json_object)
        raw_text = self._call_deepseek_json(messages, json_mode=True)

        # Attempt 2 (fallback): drop response_format, retry once on empty/None.
        if raw_text is None or not raw_text.strip():
            logger.warning(
                "DeepSeek JSON mode returned empty/None (model=%s, json_mode=True); "
                "retrying without response_format",
                self.model,
            )
            raw_text = self._call_deepseek_json(messages, json_mode=False)

        if raw_text is None:
            logger.error(
                "DeepSeek API call failed after JSON + fallback attempts (model=%s)",
                self.model,
            )
            return ValidationResult(
                False, error="DeepSeek API 调用失败", error_code="api_error"
            )
        if not raw_text.strip():
            logger.error(
                "DeepSeek returned empty content in both JSON and fallback modes (model=%s)",
                self.model,
            )
            # Let action_validator produce the user-facing message (it already
            # describes the JSON + fallback attempts).
            return parse_and_validate(raw_text)

        return parse_and_validate(raw_text)

    def _call_deepseek_json(self, messages: list, json_mode: bool = True) -> Optional[str]:
        """Call DeepSeek. Returns raw text, or "" for empty content, or None on failure.

        Logs include the model name and json_mode flag so empty/HTTP failures are
        traceable per configuration.
        """
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": DEFAULT_TEMPERATURE,
            "max_tokens": DEFAULT_MAX_TOKENS,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            DEEPSEEK_API_URL,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            logger.error(
                "DeepSeek HTTP %s (model=%s, json_mode=%s): %s",
                exc.code, self.model, json_mode, detail,
            )
            return None
        except (URLError, TimeoutError, OSError) as exc:
            logger.error(
                "DeepSeek connection error (model=%s, json_mode=%s): %s",
                self.model, json_mode, exc,
            )
            return None
        except json.JSONDecodeError:
            logger.error(
                "DeepSeek returned non-JSON response (model=%s, json_mode=%s)",
                self.model, json_mode,
            )
            return None

        if not isinstance(result, dict):
            logger.error(
                "DeepSeek returned non-dict response (model=%s, json_mode=%s): %s",
                self.model, json_mode, str(result)[:300],
            )
            return None
        choices = result.get("choices") or []
        if not choices:
            logger.error(
                "DeepSeek returned empty choices (model=%s, json_mode=%s): %s",
                self.model, json_mode, str(result)[:500],
            )
            return None
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            # Diagnostics: truncated full API response so empty responses are traceable.
            finish_reason = choices[0].get("finish_reason")
            usage = result.get("usage")
            logger.warning(
                "DeepSeek returned empty content (model=%s, json_mode=%s, "
                "finish_reason=%s, usage=%s, response=%s)",
                self.model, json_mode, finish_reason, usage, str(result)[:1000],
            )
            return ""
        return content.strip()
