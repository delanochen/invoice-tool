-- 0.1.509: keep the unwatermarked photo so a later correction can redraw the group.
-- Additive and idempotent. No existing column or row is rewritten.
BEGIN;
ALTER TABLE field_photos ADD COLUMN IF NOT EXISTS original_relative_path text NOT NULL DEFAULT '';
ALTER TABLE field_photos ADD COLUMN IF NOT EXISTS photo_role text NOT NULL DEFAULT '';
ALTER TABLE field_photos ADD COLUMN IF NOT EXISTS photo_model text NOT NULL DEFAULT '';
ALTER TABLE field_photos ADD COLUMN IF NOT EXISTS fault_note text NOT NULL DEFAULT '';
COMMIT;
