create table if not exists batch_export_jobs (
    id text primary key,
    user_id bigint not null,
    export_type text not null,
    order_ids text not null,
    status text not null,
    progress integer not null default 0,
    file_name text,
    stored_path text,
    error text,
    created_at text not null,
    started_at text,
    completed_at text
);
create index if not exists idx_batch_export_jobs_user_created on batch_export_jobs(user_id, created_at desc);
create index if not exists idx_batch_export_jobs_status on batch_export_jobs(status);

DO $do$
BEGIN
  IF current_database() = 'invoice_test'
     AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname='invoice_test') THEN
    ALTER TABLE batch_export_jobs OWNER TO invoice_test;
  END IF;
END $do$;

DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='invoice_app') THEN
    GRANT SELECT,INSERT,UPDATE,DELETE ON batch_export_jobs TO invoice_app;
  END IF;
END $do$;

INSERT INTO settings(key,value)
VALUES ('postgresql_0317_batch_export_jobs','complete')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;

COMMIT;
