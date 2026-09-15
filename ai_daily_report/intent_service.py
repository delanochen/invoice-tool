"""AI Daily Report - Intent Service (Phase 1)

Calls DeepSeek with response_format=json_object to get structured Action JSON.
Reuses existing deepseek_assistant_settings() for API key/model config.
Does NOT use tool_calls — we want pure JSON output, validated by Pydantic.
"""
from __future__ import annotations

import json
import logging
import time
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
# DeepSeek can occasionally return HTTP 200 with an empty content field.
# Retry before surfacing "AI 返回为空" to the user.
EMPTY_RESPONSE_RETRIES = 2
EMPTY_RESPONSE_RETRY_DELAY = 1.5


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
        self.model = settings.get("model", "deepseek-v4-flash").strip() or "deepseek-v4-flash"
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

        raw_text = None
        last_code = "api_error"
        for attempt in range(EMPTY_RESPONSE_RETRIES + 1):
            raw_text = self._call_deepseek_json(messages)
            if raw_text is not None and raw_text.strip():
                break
            if raw_text is None:
                last_code = "api_error"
                logger.warning("DeepSeek call returned None (attempt %d/%d)", attempt + 1, EMPTY_RESPONSE_RETRIES + 1)
            else:
                last_code = "empty_response"
                logger.warning("DeepSeek returned empty content (attempt %d/%d)", attempt + 1, EMPTY_RESPONSE_RETRIES + 1)
            if attempt < EMPTY_RESPONSE_RETRIES:
                time.sleep(EMPTY_RESPONSE_RETRY_DELAY)

        if raw_text is None:
            return ValidationResult(
                False, error="DeepSeek API 调用失败", error_code="api_error"
            )
        if not raw_text.strip():
            return ValidationResult(
                False, error="AI 返回为空，请重试", error_code=last_code
            )

        return parse_and_validate(raw_text)

    def _call_deepseek_json(self, messages: list) -> Optional[str]:
        """Call DeepSeek with response_format=json_object. Returns raw text or None."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": DEFAULT_TEMPERATURE,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "response_format": {"type": "json_object"},
        }
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
            logger.error("DeepSeek HTTP %s: %s", exc.code, detail)
            return None
        except (URLError, TimeoutError, OSError) as exc:
            logger.error("DeepSeek connection error: %s", exc)
            return None
        except json.JSONDecodeError:
            logger.error("DeepSeek returned non-JSON response")
            return None

        if not isinstance(result, dict):
            return None
        choices = result.get("choices") or []
        if not choices:
            logger.error("DeepSeek returned empty choices: %s", str(result)[:300])
            return None
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            # Log diagnostics so empty responses are traceable (finish_reason/usage).
            finish_reason = choices[0].get("finish_reason")
            usage = result.get("usage")
            logger.warning(
                "DeepSeek returned empty content (finish_reason=%s, usage=%s, model=%s)",
                finish_reason, usage, self.model,
            )
            return ""
        return content.strip()
