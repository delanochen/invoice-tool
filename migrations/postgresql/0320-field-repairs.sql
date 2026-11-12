BEGIN;
CREATE TABLE IF NOT EXISTS field_repairs (
    id text PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES service_orders(id) ON DELETE CASCADE,
    user_id bigint NOT NULL REFERENCES users(id),
    equipment_number text NOT NULL DEFAULT '',
    equipment_model text NOT NULL DEFAULT '',
    position_number text NOT NULL DEFAULT '',
    container_number text NOT NULL DEFAULT '',
    fault text NOT NULL DEFAULT '',
    work_content text NOT NULL DEFAULT '',
    result text NOT NULL DEFAULT '',
    completed_date date,
    created_at text NOT NULL
);
CREATE INDEX IF NOT EXISTS field_repairs_order_idx ON field_repairs(order_id, completed_date);
ALTER TABLE field_photos ADD COLUMN IF NOT EXISTS repair_phase text NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS field_photos_repair_session_idx ON field_photos(order_id, equipment_session, repair_phase);
DO $$
DECLARE app_role text;
BEGIN
    FOREACH app_role IN ARRAY ARRAY['invoice_app', 'invoice_test'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
            EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.field_repairs TO %I', app_role);
        END IF;
    END LOOP;
END $$;
COMMIT;
