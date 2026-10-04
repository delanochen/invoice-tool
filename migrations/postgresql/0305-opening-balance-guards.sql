BEGIN;

CREATE OR REPLACE FUNCTION protect_accounting_opening_line()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  target_cutover_id bigint;
  cutover_status text;
BEGIN
  target_cutover_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.cutover_id ELSE NEW.cutover_id END;
  SELECT status INTO cutover_status FROM accounting_opening_cutover
   WHERE id = target_cutover_id FOR UPDATE;
  IF cutover_status IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'posted opening balances are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.cutover_id IS DISTINCT FROM OLD.cutover_id THEN
    RAISE EXCEPTION 'opening line cannot move between cutovers';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

DROP TRIGGER IF EXISTS accounting_opening_lines_guard ON accounting_opening_lines;
CREATE TRIGGER accounting_opening_lines_guard
BEFORE INSERT OR UPDATE OR DELETE ON accounting_opening_lines
FOR EACH ROW EXECUTE FUNCTION protect_accounting_opening_line();

CREATE OR REPLACE FUNCTION protect_posted_accounting_opening_cutover()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND OLD.status = 'posted' THEN
    RAISE EXCEPTION 'posted opening cutover is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'posted' AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION 'posted opening cutover is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'draft' AND NEW.status NOT IN ('draft','posted') THEN
    RAISE EXCEPTION 'invalid opening cutover transition';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

DROP TRIGGER IF EXISTS accounting_opening_cutover_guard ON accounting_opening_cutover;
CREATE TRIGGER accounting_opening_cutover_guard
BEFORE UPDATE OR DELETE ON accounting_opening_cutover
FOR EACH ROW EXECUTE FUNCTION protect_posted_accounting_opening_cutover();

INSERT INTO settings(key,value)
VALUES ('postgresql_0305_opening_balance_guards','complete')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;

COMMIT;
