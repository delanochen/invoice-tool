-- 人工确认「合理 0 英里」豁免：日报行里程为 0 时，由填报人显式确认，
-- 工资生成前的里程佐证拦截跳过这些行。与里程佐证表完全分离，
-- 不影响计薪口径（仍按 0 英里计发），仅避免重复索要并不存在的路线佐证。
--
-- 只授权在 invoice_test 上先行应用；生产库按部署流程单独授权后执行。
-- 幂等、可在应用运行时执行。
BEGIN;
SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.service_report_mileage_zero_waivers (
    report_id bigint NOT NULL,
    worker_user_id bigint NOT NULL,
    confirmed_by bigint,
    confirmed_at text NOT NULL,
    PRIMARY KEY (report_id, worker_user_id),
    FOREIGN KEY (report_id) REFERENCES public.service_reports(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sr_mileage_zero_waivers_worker
    ON public.service_report_mileage_zero_waivers (worker_user_id);

COMMIT;
