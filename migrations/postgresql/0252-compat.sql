-- Phase 1 keeps legacy text dates and double precision amounts so changing the
-- database does not silently change application rounding or display semantics.
-- Native DATE/TIMESTAMPTZ/NUMERIC are a separate, explicitly validated migration.
CREATE FUNCTION invoice_sqlite_datetime(value text) RETURNS text
LANGUAGE plpgsql STABLE STRICT AS $$
BEGIN
  IF value = 'now' THEN
    RETURN to_char(statement_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS');
  END IF;
  RETURN to_char(value::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS');
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN NULL;
END $$;
CREATE FUNCTION invoice_sqlite_date(value text) RETURNS text
LANGUAGE sql STABLE STRICT AS $$ SELECT left(invoice_sqlite_datetime(value),10) $$;
CREATE FUNCTION invoice_sqlite_julianday(value text) RETURNS double precision
LANGUAGE plpgsql STABLE STRICT AS $$
BEGIN
  RETURN extract(epoch FROM value::timestamptz)::double precision / 86400.0 + 2440587.5;
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN NULL;
END $$;
CREATE TABLE invoice_schema_version(singleton integer PRIMARY KEY CHECK(singleton=1),version text NOT NULL,source_sha256 text NOT NULL,imported_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE invoice_sqlite_columns(table_name text NOT NULL,cid integer NOT NULL,name text NOT NULL,type text NOT NULL,"notnull" integer NOT NULL,dflt_value text,pk integer NOT NULL,PRIMARY KEY(table_name,cid));
CREATE VIEW invoice_sqlite_master AS
 SELECT table_name AS name,CASE table_type WHEN 'VIEW' THEN 'view' ELSE 'table' END AS type
 FROM information_schema.tables WHERE table_schema='public'
 AND table_name NOT IN ('invoice_schema_version','invoice_sqlite_columns','invoice_sqlite_master');
