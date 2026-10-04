-- Accounting period integrity guards.
-- Periods are complete calendar months and may never overlap.

CREATE OR REPLACE FUNCTION guard_accounting_period() RETURNS trigger AS $$
DECLARE expected_end date;
BEGIN
    IF NEW.period_start <> date_trunc('month',NEW.period_start)::date THEN
        RAISE EXCEPTION 'accounting period must start on the first day of a month'
            USING ERRCODE = 'check_violation';
    END IF;
    expected_end := (NEW.period_start + interval '1 month - 1 day')::date;
    IF NEW.period_end <> expected_end THEN
        RAISE EXCEPTION 'accounting period must end on %', expected_end
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM accounting_periods p
        WHERE p.id <> COALESCE(NEW.id,0)
          AND daterange(p.period_start,p.period_end,'[]') &&
              daterange(NEW.period_start,NEW.period_end,'[]')
    ) THEN
        RAISE EXCEPTION 'accounting period overlaps an existing period'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS accounting_periods_integrity_guard ON accounting_periods;
CREATE TRIGGER accounting_periods_integrity_guard
    BEFORE INSERT OR UPDATE OF period_start,period_end ON accounting_periods
    FOR EACH ROW EXECUTE FUNCTION guard_accounting_period();

INSERT INTO settings(key,value)
VALUES ('postgresql_0300_accounting_period_guards','complete')
ON CONFLICT (key) DO UPDATE SET value=excluded.value;
