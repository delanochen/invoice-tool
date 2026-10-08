BEGIN;

-- 0307：工单结算快照补「其他」小计（other_total）。
--
-- 背景：结算快照列只有 labor_total / lodging_total / travel_total /
-- mileage_total / mro_supplies_total / rental_fuel_total / total_amount，
-- 而合计口径 total_amount = Σ item.total + rental_fuel_total 已包含明细行
-- 「其他」列金额（MRO 也经 auto_other 转入 other → item.total 计入合计）。
-- 结果：详情页结算面板、工单结算报表及其 Excel 导出都只显示
-- 工时费 / 差旅费 / 里程费三个小计，合计却含「其他」，出现
-- 合计 ≠ 小计之和，差额恰为其他列合计。
--
-- 本迁移新增 other_total 快照列，并按与页面一致的「手改优先」口径回填存量：
--   生效值 = COALESCE(NULLIF(other, 0), auto_other)
-- 与 customer_reimbursement_item_expense_amount()（人工值非 0 取人工值，
-- 否则取报销来源 auto_other）完全一致；历史结算补列后即可对账自洽。

ALTER TABLE public.customer_reimbursements
    ADD COLUMN other_total double precision NOT NULL DEFAULT 0;

UPDATE public.customer_reimbursements cr
SET other_total = COALESCE((
    SELECT SUM(
        CASE WHEN ci.other IS NULL OR ci.other = 0
             THEN COALESCE(ci.auto_other, 0)
             ELSE ci.other END
    )
    FROM public.customer_reimbursement_items ci
    WHERE ci.customer_reimbursement_id = cr.id
), 0);

INSERT INTO settings(key,value)
VALUES ('postgresql_0307_customer_reimbursement_other_total','complete')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;

COMMIT;
