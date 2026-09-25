-- v0.1.282 expense AI review opinions (additive-only).
-- Applied by scripts/upgrade_postgresql_0283.py; additive on the 0252-compat-v2 baseline.

CREATE TABLE IF NOT EXISTS expense_ai_reviews (
    id bigserial PRIMARY KEY,
    expense_id integer NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'pending',
    conclusion text NOT NULL DEFAULT '',
    content text NOT NULL DEFAULT '',
    model text NOT NULL DEFAULT '',
    error text NOT NULL DEFAULT '',
    created_at text NOT NULL,
    updated_at text NOT NULL,
    FOREIGN KEY (expense_id) REFERENCES expenses(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_expense_ai_reviews_status
    ON expense_ai_reviews(status);
