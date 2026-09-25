"""Unified LLM provider configuration.

Every feature that calls a chat/completions API resolves its provider through
get_config(db, scene). The admin maps each scene to a row in llm_configs from
the system settings page. Scenes must map to an enabled config row; there is no
legacy fallback — legacy DeepSeek / local-model settings were removed in 0.1.283.
"""
from __future__ import annotations

# scene key -> human label. Order matters for the settings page.
SCENES = (
    ("attachment_interpret", "报销附件智能解读（需视觉）"),
    ("expense_review", "报销 AI 审核（文本）"),
    ("daily_vision", "日报照片识图（需视觉）"),
    ("daily_intent", "日报意图 / AI 助手（文本）"),
)

SCENE_KEYS = {key for key, _label in SCENES}
SCENE_LABELS = dict(SCENES)


def _setting(db, key, default=""):
    row = db.execute("select value from settings where key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def list_configs(db):
    return db.execute("select * from llm_configs order by id").fetchall()


def get_row(db, config_id):
    """Return the raw llm_configs row for `config_id`, or None."""
    try:
        return db.execute(
            "select * from llm_configs where id = ?", (int(config_id),)
        ).fetchone()
    except (TypeError, ValueError):
        return None


def config_by_id(db, config_id):
    """Normalized provider dict for a config row (regardless of enabled)."""
    row = get_row(db, config_id)
    if row is None:
        return None
    return _from_row(row)


def get_config(db, scene):
    """Return a normalized provider dict for `scene` (must map to an enabled config).

    Shape: {name, base_url, api_key, model, supports_vision, timeout_seconds}.
    Raises ValueError when the scene has no mapping to an enabled config.
    """
    mapped = _setting(db, f"llm_scene_{scene}").strip()
    if mapped:
        row = get_row(db, mapped)
        if row and row["enabled"]:
            return _from_row(row)
    label = SCENE_LABELS.get(scene, scene)
    raise ValueError(
        f"场景「{label}」未配置可用的模型，请在系统设置 → 大模型配置中选择。"
    )


def _from_row(row):
    return {
        "name": row["name"],
        "base_url": row["base_url"].rstrip("/"),
        "api_key": row["api_key"] or "",
        "model": row["model"],
        "supports_vision": bool(row["supports_vision"]),
        "timeout_seconds": int(row["timeout_seconds"]),
    }
