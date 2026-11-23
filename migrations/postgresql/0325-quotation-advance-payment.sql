BEGIN;
ALTER TABLE quotations ADD COLUMN IF NOT EXISTS advance_payment_percent integer NOT NULL DEFAULT 0
    CHECK (advance_payment_percent IN (0, 30, 50));

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'quotations'::regclass
          AND conname = 'quotations_payment_terms_check'
          AND pg_get_constraintdef(oid) LIKE '%due_on_completion%'
    ) THEN
        ALTER TABLE quotations DROP CONSTRAINT IF EXISTS quotations_payment_terms_check;
        ALTER TABLE quotations ADD CONSTRAINT quotations_payment_terms_check
            CHECK (payment_terms IN ('net_15', 'net_30', 'net_45', 'other', 'due_on_completion'));
    END IF;
END $$;
COMMIT;
