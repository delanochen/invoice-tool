ALTER TABLE quotations ADD COLUMN IF NOT EXISTS advance_payment_percent integer NOT NULL DEFAULT 0
    CHECK (advance_payment_percent IN (0, 30, 50));
