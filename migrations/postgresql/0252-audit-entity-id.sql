-- Upgrade the initial rehearsal schema only. Fresh imports include this type.
BEGIN;
DO $$ BEGIN
  IF current_database() NOT LIKE '%\_rehearsal' THEN
    RAISE EXCEPTION 'This rehearsal upgrade must not run against production';
  END IF;
END $$;
ALTER TABLE audit_logs ALTER COLUMN entity_id TYPE text USING entity_id::text;
UPDATE invoice_schema_version SET version='0252-compat-v2' WHERE singleton=1 AND version='0252-compat-v1';
COMMIT;
