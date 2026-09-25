-- v0.1.281 报销附件智能解读：结果表（additive-only）。
-- 在 0252-compat-v2 基线上追加，不修改既有对象；由 scripts/upgrade_postgresql_0281.py 执行。

CREATE TABLE IF NOT EXISTS expense_attachment_interpretations (
    id bigserial PRIMARY KEY,
    attachment_id integer NOT NULL UNIQUE,
    model text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'pending',
    content text NOT NULL DEFAULT '',
    error text NOT NULL DEFAULT '',
    created_at text NOT NULL,
    updated_at text NOT NULL,
    FOREIGN KEY (attachment_id) REFERENCES expense_attachments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_expense_interpretations_status
    ON expense_attachment_interpretations(status);
