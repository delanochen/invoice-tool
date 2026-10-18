"""Legacy-compatible ai_settings helpers; explicit runtime dependencies."""

def accounting_base_enabled(connection=None, *, db):
    connection = connection or db()
    row = connection.execute(
        "select value from settings where key='accounting_base_enabled'"
    ).fetchone()
    return bool(row and row[0] == "1")


def deepseek_assistant_settings(*, DEEPSEEK_API_KEY_ENV, db, llm_config):
    try:
        cfg = llm_config.get_config(db(), "daily_intent")
    except ValueError:
        return {"enabled": False, "api_key": "", "model": "", "base_url": ""}
    return {
        "enabled": True,
        "api_key": cfg["api_key"] or DEEPSEEK_API_KEY_ENV,
        "model": cfg["model"],
        "base_url": cfg["base_url"],
    }


def vision_settings(*, VISION_MAX_IMAGE_BYTES_ENV, db, get_setting, llm_config):
    """Vision API settings for Phase 5 photo classification.

    Privacy: VISION_EXTERNAL_API_ENABLED controls whether photos are sent to external API.
    If disabled, classification_status = disabled, no photo uploaded externally.
    Default model: deepseek-flash (from DEEPSEEK_VISION_MODEL env, not hardcoded).
    """
    try:
        cfg = llm_config.get_config(db(), "daily_vision")
    except ValueError:
        cfg = {"api_key": "", "model": "", "base_url": ""}
    return {
        "enabled": get_setting("vision_external_api_enabled", "false") == "true" and bool(cfg["model"]),
        "api_key": cfg["api_key"],
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "safety_auto_select_confidence": float(get_setting("safety_auto_select_confidence", "0.80")),
        "safety_verify_confidence": float(get_setting("safety_verify_confidence", "0.60")),
        "max_service_photos": int(get_setting("max_service_photos", "10")),
        "vision_max_image_size": int(get_setting("vision_max_image_size", "1280")),
        "vision_max_image_bytes": int(get_setting("vision_max_image_bytes", str(VISION_MAX_IMAGE_BYTES_ENV))),
    }
