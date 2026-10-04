BEGIN;

CREATE OR REPLACE FUNCTION guard_posted_invoice() RETURNS trigger AS $$
DECLARE recognition_exists boolean;
BEGIN
    SELECT EXISTS(
        SELECT 1 FROM posting_events
        WHERE source_type='invoice' AND source_id=OLD.id
          AND event_type='invoice.confirmed'
    ) INTO recognition_exists;
    IF NOT recognition_exists THEN
        RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'posted invoice cannot be deleted' USING ERRCODE='integrity_constraint_violation';
    END IF;
    IF NEW.invoice_number IS DISTINCT FROM OLD.invoice_number OR
       NEW.client_id IS DISTINCT FROM OLD.client_id OR
       NEW.issue_date IS DISTINCT FROM OLD.issue_date OR
       NEW.currency IS DISTINCT FROM OLD.currency THEN
        RAISE EXCEPTION 'posted invoice accounting fields are immutable'
            USING ERRCODE='integrity_constraint_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (OLD.status='completed' AND NEW.status='void' AND EXISTS(
            SELECT 1 FROM posting_events
            WHERE source_type='invoice' AND source_id=OLD.id
              AND event_type='invoice.voided'
        )) THEN
            RAISE EXCEPTION 'illegal posted invoice status transition'
                USING ERRCODE='integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS invoices_posted_guard ON invoices;
CREATE TRIGGER invoices_posted_guard
    BEFORE UPDATE OR DELETE ON invoices
    FOR EACH ROW EXECUTE FUNCTION guard_posted_invoice();

CREATE OR REPLACE FUNCTION guard_posted_invoice_item() RETURNS trigger AS $$
DECLARE target_invoice_id bigint;
BEGIN
    target_invoice_id := CASE WHEN TG_OP='INSERT' THEN NEW.invoice_id ELSE OLD.invoice_id END;
    IF EXISTS(
        SELECT 1 FROM posting_events
        WHERE source_type='invoice' AND source_id=target_invoice_id
          AND event_type='invoice.confirmed'
    ) THEN
        RAISE EXCEPTION 'posted invoice items are immutable'
            USING ERRCODE='integrity_constraint_violation';
    END IF;
    RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS invoice_items_posted_guard ON invoice_items;
CREATE TRIGGER invoice_items_posted_guard
    BEFORE INSERT OR UPDATE OR DELETE ON invoice_items
    FOR EACH ROW EXECUTE FUNCTION guard_posted_invoice_item();

INSERT INTO settings(key,value)
VALUES ('postgresql_0302_posted_invoice_guards','complete')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;

COMMIT;
