"""Unified LLM provider configuration.

Every feature that calls a chat/completions API resolves its provider through
get_config(db, scene). The admin maps each scene to a row in llm_configs from
the system settings page. When a scene has no explicit mapping (or the mapped
row is disabled), we fall back to the legacy per-feature settings so existing
deployments keep working unchanged.
"""
from __future__ import annotations

import os

# scene key -> human label. Order matters for the settings page.
SCENES = (
    ("attachment_interpret", "报销附件智能解读（需视觉）"),
    ("expense_review", "报销 AI 审核（文本）"),
    ("daily_vision", "日报照片识图（需视觉）"),
    ("daily_intent", "日报意图 / AI 助手（文本）"),
)

SCENE_KEYS = {key for key, _label in SCENES}
_DEEPSEEK_BASE = "https://api.deepseek.com"


def _setting(db, key, default=""):
    row = db.execute("select value from settings where key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def list_configs(db):
    return db.execute(
        "select * from llm_configs order by id"
    ).fetchall()


def get_config(db, scene):
    """Return a normalized provider dict for `scene`, with legacy fallback.

    Shape: {name, base_url, api_key, model, supports_vision, timeout_seconds}.
    """
    mapped = _setting(db, f"llm_scene_{scene}").strip()
    if mapped:
        row = db.execute(
            "select * from llm_configs where id = ?", (int(mapped),)
        ).fetchone()
        if row and row["enabled"]:
            return _from_row(row)
    # --- legacy fallback (keeps existing deployments working) ---
    if scene == "attachment_interpret":
        base = os.environ.get("AI_INTERPRET_BASE_URL",
                              "http://host.docker.internal:11434/v1")
        base = _setting(db, "ai_interpret_base_url", base).strip() or base
        choice = _setting(db, "ai_interpret_model_choice", "qwen4b")
        if choice == "custom":
            model = _setting(db, "ai_interpret_model_custom", "")
        elif choice == "qwen9b":
            model = _setting(db, "ai_interpret_model_qwen9b", "qwen3.5:9b")
        else:
            model = _setting(db, "ai_interpret_model_qwen4b", "qwen3.5:4b")
        return {
            "name": "本地 Ollama (legacy)", "base_url": base.rstrip("/"),
            "api_key": _setting(db, "ai_interpret_api_key", ""),
            "model": model, "supports_vision": True, "timeout_seconds": 300,
        }
    if scene == "expense_review":
        # review historically reused the attachment provider
        return get_config(db, "attachment_interpret")
    if scene == "daily_vision":
        key = _setting(db, "deepseek_api_key",
                       os.environ.get("DEEPSEEK_API_KEY", "")).strip()
        model = _setting(db, "deepseek_vision_model",
                         os.environ.get("DEEPSEEK_VISION_MODEL", "deepseek-flash"))
        return {
            "name": "DeepSeek vision (legacy)", "base_url": _DEEPSEEK_BASE,
            "api_key": key, "model": model, "supports_vision": True,
            "timeout_seconds": 120,
        }
    if scene == "daily_intent":
        key = _setting(db, "deepseek_api_key",
                       os.environ.get("DEEPSEEK_API_KEY", "")).strip()
        model = _setting(db, "deepseek_model", "deepseek-chat")
        return {
            "name": "DeepSeek chat (legacy)", "base_url": _DEEPSEEK_BASE,
            "api_key": key, "model": model, "supports_vision": False,
            "timeout_seconds": 120,
        }
    raise ValueError(f"unknown LLM scene: {scene}")


def _from_row(row):
    return {
        "name": row["name"],
        "base_url": row["base_url"].rstrip("/"),
        "api_key": row["api_key"] or "",
        "model": row["model"],
        "supports_vision": bool(row["supports_vision"]),
        "timeout_seconds": int(row["timeout_seconds"]),
    }
