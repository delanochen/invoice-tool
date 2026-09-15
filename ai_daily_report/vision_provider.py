"""AI Daily Report - Vision Classification Service (Phase 5)

Vision Provider abstraction layer. Business logic does NOT depend on specific model names.

Providers:
- DeepSeekVisionProvider: Uses DeepSeek Vision API (chat/completions with image input)
- Future: OllamaVisionProvider, LocalVisionProvider, etc.

CRITICAL PRINCIPLES:
- Vision only answers "what is in this photo?"
- Vision does NOT know/trust: path, timestamp, worker id, service order, mileage
- API key never logged, never in Draft, never returned to frontend
- Photos are normalized before sending: JPEG, EXIF orientation corrected, EXIF/GPS stripped, size limited
- VISION_EXTERNAL_API_ENABLED=false -> no photo uploaded, classification_status=disabled
- Logs never contain: full base64, full image URL, full server path, EXIF GPS, API key
- Retry: timeout/429/5xx max 2 retries; 400/401/403 no retry
"""
from __future__ import annotations

import base64
import io
import logging
import time
from pathlib import Path
from typing import Optional, Tuple

from .schemas import PhotoAnalysis

logger = logging.getLogger(__name__)

# Analysis version - bump when prompt/schema/ranking changes significantly
PHOTO_ANALYSIS_VERSION = 1
# Backward compatibility alias
VISION_ANALYSIS_VERSION = PHOTO_ANALYSIS_VERSION

# Default max image bytes for Vision (after JPEG compression)
DEFAULT_MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2MB

# Vision System Prompt - looks at actual image, NOT filename
VISION_SYSTEM_PROMPT = """You are a photo classification assistant for field service daily reports.

You analyze photos of construction/equipment maintenance work sites.

You MUST classify each photo into exactly one category:
- safety_person: Photo where the MAIN subject is a field worker, preferably front-facing, standing, full/most body visible. This is a safety self-check / arrival photo.
- equipment: Photo where the MAIN subject is equipment, machinery, nameplate, fault location, repair process, parts, wiring, fuses, startup state, final state.
- other: Photo that does not fit above (vehicle, landscape, screenshot, document, blurry, etc.)
- unknown: Cannot reliably classify.

For equipment photos, also identify sub_category if possible:
- equipment_overview: wide shot of equipment
- nameplate: equipment nameplate/serial number
- fault_location: where the problem is
- before_repair: before disassembly
- during_disassembly: taking apart
- replacement: replacing parts
- fuse_wiring: fuses, wiring, electrical connections
- after_repair: after reassembly
- startup: equipment starting/running
- final_state: final completed state
- other/unknown: cannot determine

If you can see an equipment ID/number (like A313, 3A6), return it as equipment_id.
Return equipment_id_confidence: how confident you are about the equipment ID (0.0-1.0).

Return ONLY valid JSON with this exact structure:
{
  "classification": "safety_person|equipment|other|unknown",
  "sub_category": "front_standing_worker|equipment_overview|nameplate|fault_location|before_repair|during_disassembly|replacement|fuse_wiring|after_repair|startup|final_state|other|unknown",
  "confidence": 0.0-1.0,
  "description": "brief description of what is visible",
  "equipment_id": "A313 or null",
  "equipment_id_confidence": 0.0-1.0
}

IMPORTANT:
- Look at the ACTUAL IMAGE CONTENT, not the filename.
- Do NOT guess. If unsure, use "unknown" classification and low confidence.
- A photo of a hand/tool only is NOT safety_person (need full/most body of a person).
- A photo of equipment with a person in background is equipment (equipment is main subject).
- Return JSON only. No markdown. No explanation.
"""


class VisionProviderError(Exception):
    """Raised when Vision API call fails."""
    def __init__(self, error_code: str, message: str = ""):
        self.error_code = error_code
        super().__init__(message or error_code)


def prepare_vision_image(
    image_path: Path,
    max_dimension: int = 1280,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> Tuple[Optional[str], Optional[str]]:
    """Prepare image for Vision API: decode, EXIF orientation, resize, RGB, JPEG, strip EXIF.

    Returns (base64_jpeg, error_code).
    On success: (base64_string, None)
    On failure: (None, error_code)

    Steps:
    1. Open image safely
    2. EXIF orientation correction (ImageOps.exif_transpose)
    3. Convert to RGB
    4. Resize if max dimension > max_dimension
    5. JPEG encode with quality=85
    6. If still > max_bytes, reduce quality/size until <= max_bytes
    7. Strip all EXIF metadata (especially GPS)
    8. Base64 encode

    Never modifies original file. Only creates temporary representation.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None, "vision_image_decode_failed"

    try:
        with Image.open(image_path) as img:
            # EXIF orientation correction
            img = ImageOps.exif_transpose(img)
            # Convert to RGB (handles RGBA, P, etc.)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")

            # Resize if too large
            w, h = img.size
            max_dim = max(w, h)
            if max_dim > max_dimension:
                ratio = max_dimension / max_dim
                new_w = int(w * ratio)
                new_h = int(h * ratio)
                img = img.resize((new_w, new_h), Image.LANCZOS)

            # JPEG encode with progressive quality reduction
            quality = 85
            while quality >= 30:
                buf = io.BytesIO()
                # JPEG does not preserve EXIF by default when saving from PIL
                # but explicitly ensure no metadata
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                img_bytes = buf.getvalue()
                if len(img_bytes) <= max_bytes:
                    return base64.b64encode(img_bytes).decode("ascii"), None
                quality -= 15

            # If still too large, resize further
            current_max = max(img.size)
            while current_max > 320:
                current_max = int(current_max * 0.7)
                ratio = current_max / max(img.size)
                new_w = int(img.size[0] * ratio)
                new_h = int(img.size[1] * ratio)
                resized = img.resize((new_w, new_h), Image.LANCZOS)
                buf = io.BytesIO()
                resized.save(buf, format="JPEG", quality=70, optimize=True)
                img_bytes = buf.getvalue()
                if len(img_bytes) <= max_bytes:
                    return base64.b64encode(img_bytes).decode("ascii"), None

            return None, "vision_image_too_large"

    except Exception as e:
        logger.debug("Vision image prepare failed: %s", type(e).__name__)
        return None, "vision_image_decode_failed"


class VisionClassificationService:
    """Abstract vision classification service.

    Business layer uses this interface, not specific provider.
    """

    def __init__(
        self,
        provider,
        enabled: bool = True,
        max_image_size: int = 1280,
        max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    ):
        self.provider = provider
        self.enabled = enabled
        self.max_image_size = max_image_size
        self.max_image_bytes = max_image_bytes

    def analyze_photo(
        self,
        photo_path: Path,
        photo_hash: str,
        photo_id: str = "",
        analysis_model: str = "",
    ) -> PhotoAnalysis:
        """Analyze a single photo. Returns PhotoAnalysis.

        If disabled, returns PhotoAnalysis with analysis_status=disabled.
        Never raises - errors are captured in analysis_status.
        """
        base = PhotoAnalysis(
            photo_id=photo_id or photo_hash,
            photo_path=str(photo_path),
            photo_hash=photo_hash,
            analysis_model=analysis_model,
            analysis_version=PHOTO_ANALYSIS_VERSION,
        )

        if not self.enabled:
            base.analysis_status = "disabled"
            base.classification = "unknown"
            base.verification_required = True
            base.verification_reason = "vision_disabled"
            return base

        # Prepare image (normalize, strip EXIF/GPS, size control)
        image_b64, error_code = prepare_vision_image(
            photo_path,
            max_dimension=self.max_image_size,
            max_bytes=self.max_image_bytes,
        )
        if not image_b64:
            base.analysis_status = "failed"
            base.classification = "unknown"
            base.verification_required = True
            base.verification_reason = error_code or "vision_image_prepare_failed"
            return base

        # Call provider with retry
        result = None
        last_error = None
        for attempt in range(3):  # 1 initial + 2 retries
            try:
                result = self.provider.classify(image_b64)
                break
            except VisionProviderError as e:
                last_error = e
                # 4xx errors (except 429) should NOT retry
                if e.error_code in ("vision_http_400", "vision_http_401", "vision_http_403",
                                     "vision_invalid_json", "vision_schema_invalid"):
                    break
                # 429/5xx/timeout -> retry with backoff
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                break

        if result is None:
            error_code = last_error.error_code if last_error else "vision_api_error"
            base.analysis_status = "failed"
            base.classification = "unknown"
            base.verification_required = True
            base.verification_reason = error_code
            return base

        # Parse and validate result
        try:
            base.classification = result.get("classification", "unknown")
            base.sub_category = result.get("sub_category")
            base.confidence = float(result.get("confidence", 0.0))
            base.description = result.get("description")
            base.equipment_id = result.get("equipment_id")
            base.equipment_id_confidence = float(result.get("equipment_id_confidence", 0.0))
            base.analysis_status = "success"
            from datetime import datetime, timezone
            base.analyzed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Validate classification enum
            valid_classifications = {"safety_person", "equipment", "other", "unknown"}
            if base.classification not in valid_classifications:
                base.classification = "unknown"
                base.confidence = 0.0
                base.verification_required = True
                base.verification_reason = "vision_schema_invalid"

            # Low confidence -> verification required
            elif base.confidence < 0.60:
                base.verification_required = True
                base.verification_reason = "low_confidence"

        except (ValueError, TypeError) as e:
            base.analysis_status = "failed"
            base.classification = "unknown"
            base.verification_required = True
            base.verification_reason = "vision_schema_invalid"

        return base


class DeepSeekVisionProvider:
    """DeepSeek Vision API provider.

    Uses DeepSeek chat/completions with image content.
    Model name from config (DEEPSEEK_VISION_MODEL), not hardcoded.
    API key from existing secure config.
    Uses response_format=json_object for JSON Output.
    """

    def __init__(self, api_key: str, model: str, api_url: str = "https://api.deepseek.com/chat/completions"):
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.api_url = api_url

    def classify(self, image_b64: str) -> dict:
        """Classify an image. Returns dict with classification fields.

        Uses response_format=json_object.
        Raises VisionProviderError on API failure with safe error codes.
        Never logs API key, full base64, or full URL.
        """
        if not self.api_key:
            raise VisionProviderError("vision_api_not_configured")

        import json
        import urllib.request
        import urllib.error

        messages = [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Classify this photo. Return JSON only."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                ],
            },
        ]

        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 500,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")

        req = urllib.request.Request(
            self.api_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            code = e.code
            if code == 429:
                raise VisionProviderError("vision_rate_limit")
            elif code in (400, 401, 403):
                raise VisionProviderError(f"vision_http_{code}")
            elif code >= 500:
                raise VisionProviderError("vision_http_5xx")
            else:
                raise VisionProviderError(f"vision_http_{code}")
        except urllib.error.URLError as e:
            if "timed out" in str(e).lower():
                raise VisionProviderError("vision_timeout")
            raise VisionProviderError("vision_connection_error")
        except Exception as e:
            raise VisionProviderError("vision_unexpected_error")

        try:
            data = json.loads(body)
            content = data["choices"][0]["message"]["content"]
            result = json.loads(content)
            return result
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            raise VisionProviderError("vision_invalid_json")
