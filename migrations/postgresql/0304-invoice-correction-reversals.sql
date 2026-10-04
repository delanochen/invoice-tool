BEGIN;

ALTER TABLE invoice_accounting_corrections
    ADD COLUMN IF NOT EXISTS reversal_event_id bigint REFERENCES posting_events(id),
    ADD COLUMN IF NOT EXISTS reversal_voucher_id bigint REFERENCES vouchers(id),
    ADD COLUMN IF NOT EXISTS reversed_by bigint REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS reversed_at text,
    ADD COLUMN IF NOT EXISTS reversal_reason text NOT NULL DEFAULT '';

DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='invoice_app') THEN
    GRANT SELECT,INSERT,UPDATE ON invoice_accounting_corrections TO invoice_app;
  END IF;
END $do$;

INSERT INTO settings(key,value)
VALUES ('postgresql_0304_invoice_correction_reversals','complete')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;

COMMIT;
