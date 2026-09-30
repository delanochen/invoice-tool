-- v0.1.343：员工付款单编号统一为「一类一前缀」。
--
-- 背景：员工付款单的 payment_type 只有 salary（工资）和 expense（员工报销）
-- 两种，但历史上两类共用 EP- 前缀 + 同一个流水池（工资 EP-2609-0001~0007、
-- 报销接着 0008 往下排），另外 0286 迁移补建存量报销时又用了第三种
-- EP-MIG-EXP-<expense_id> 格式（号段稀疏：1-9、18-30、33-43…）。
-- 结果同一个「EP-」前缀下混着三种口径，光看单号分不清业务类型。
--
-- 本迁移把存量单据按业务类型重新编号：
--   工资     -> SL-<YYMM>-<NNNN>
--   员工报销 -> ER-<YYMM>-<NNNN>
-- YYMM 取单据自己的 created_at（保留「哪个月的单」这一信息），
-- NNNN 在同前缀同月份内按 (created_at, id) 升序从 0001 起连续编号。
--
-- 注意：created_at / updated_at 在本表里是 **text**（ISO-8601 字符串，
-- 如 '2026-09-26T21:09:40-05:00'），不是 timestamp —— 直接用
-- substring(created_at, 3, 2) || substring(created_at, 6, 2) 取 YYMM，
-- 不要写 to_char(coalesce(created_at, now()), ...)，会报类型不匹配。
--
-- 只改 payment_number 一个列。审计日志 audit_logs.entity_label 里也存了旧单号，
-- 但那是**历史留痕**（记录当时发生的事），故意不改写；页面展示一律读
-- employee_payment_orders.payment_number 实时取，所以改完自动全局生效。

WITH ranked AS (
    SELECT
        id,
        CASE payment_type
            WHEN 'salary' THEN 'SL'
            ELSE 'ER'
        END
        || '-'
        || CASE
               WHEN created_at ~ '^\d{4}-\d{2}'
               THEN substring(created_at, 3, 2) || substring(created_at, 6, 2)
               ELSE to_char(now(), 'YYMM')
           END
        || '-' || lpad(
            row_number() OVER (
                PARTITION BY payment_type,
                             CASE
                                 WHEN created_at ~ '^\d{4}-\d{2}'
                                 THEN substring(created_at, 3, 2) || substring(created_at, 6, 2)
                                 ELSE to_char(now(), 'YYMM')
                             END
                ORDER BY created_at, id
            )::text, 4, '0'
        ) AS new_number
    FROM employee_payment_orders
)
UPDATE employee_payment_orders target
SET payment_number = ranked.new_number
FROM ranked
WHERE target.id = ranked.id
  AND target.payment_number IS DISTINCT FROM ranked.new_number;

INSERT INTO settings(key,value)
VALUES ('postgresql_0289_payment_number_prefixes','complete')
ON CONFLICT (key) DO UPDATE SET value=excluded.value;
