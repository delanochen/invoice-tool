-- v0.1.284 unified LLM provider configuration table.
-- Additive-only; existing per-scene settings remain as fallback.
CREATE TABLE IF NOT EXISTS llm_configs (
    id bigserial PRIMARY KEY,
    name text NOT NULL UNIQUE,
    base_url text NOT NULL,
    api_key text NOT NULL DEFAULT '',
    model text NOT NULL,
    supports_vision boolean NOT NULL DEFAULT false,
    timeout_seconds integer NOT NULL DEFAULT 300,
    enabled boolean NOT NULL DEFAULT true,
    notes text NOT NULL DEFAULT '',
    created_at text NOT NULL,
    updated_at text NOT NULL
);

-- Seed two providers if empty. DeepSeek key may stay blank; the app falls back
-- to the DEEPSEEK_API_KEY env var at call time.
INSERT INTO llm_configs (name, base_url, api_key, model, supports_vision, timeout_seconds, enabled, notes, created_at, updated_at)
SELECT v.name, v.base_url, v.api_key, v.model, v.supports_vision, v.timeout_seconds, v.enabled, v.notes,
       to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
       to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
FROM (VALUES
    ('本地 Ollama (Qwen)', 'http://host.docker.internal:11434/v1', '', 'qwen3.5:4b', true, 300, true, '本地 CPU 推理，慢但离线'),
    ('DeepSeek 云端', 'https://api.deepseek.com', '', 'deepseek-chat', true, 120, true, '云端，快，按 token 计费')
) AS v(name, base_url, api_key, model, supports_vision, timeout_seconds, enabled, notes)
WHERE NOT EXISTS (SELECT 1 FROM llm_configs);
