-- v0.1.317 Explicit bank-account initialization metadata.
-- Additive only: existing balances remain untouched and existing accounts are
-- shown as not explicitly initialized until a user confirms their as-of date.

ALTER TABLE bank_accounts ADD COLUMN IF NOT EXISTS opening_balance_date text;
ALTER TABLE bank_accounts ADD COLUMN IF NOT EXISTS initialized_at text;
ALTER TABLE bank_accounts ADD COLUMN IF NOT EXISTS initialized_by bigint REFERENCES users(id);

INSERT INTO settings(key,value)
VALUES ('postgresql_0287_bank_account_initialization','complete')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;
