-- Additive optional date; existing orders remain unset.
-- Apply only to invoice_test unless separately authorized by the user.
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE public.service_orders
    ADD COLUMN IF NOT EXISTS expected_settlement_date text;
COMMIT;
