-- v0.1.358 Phase 4A：Tax Review 工作台。
--
-- 复核表（employee_payment_tax_reviews）在 0291 建表时带了一个
-- 「previous_tax_category IS DISTINCT FROM new_tax_category」约束，语义是
-- 「复核必须改变分类」。Phase 4A 的三种复核结果里有一种是
-- **Keep Under Review**：分类保持 tax_review_required，但必须留下一条说明
-- （例如「无法取得原始 mileage evidence，继续待复核」）——这条记录本身就是
-- 审计证据，缺了它就无法证明「已经看过、仍缺材料」，所以该约束必须放开。
--
-- 放开后仍然成立的两条铁律（由应用代码保证，不在数据库层实现）：
--   1. 只追加不覆盖：复核永远是 INSERT，绝不 UPDATE / DELETE 既有复核行；
--   2. 原始快照只读：绝不 UPDATE employee_payment_components.tax_category，
--      「有效分类」= 最新一条复核的 new_tax_category，无复核回退原始快照。
-- previous_tax_category 取的是**当前有效分类**（不是原始快照值），
-- 保证第二次复核时审计链连续：review → accountable → taxable。
--
-- 幂等：DROP CONSTRAINT IF EXISTS，重复执行安全。
-- 明确不改：任何既有行数据、组件快照、付款单金额、往来账 / 批次 / 对账逻辑。

ALTER TABLE employee_payment_tax_reviews
    DROP CONSTRAINT IF EXISTS employee_payment_tax_reviews_category_changed;

INSERT INTO settings(key,value)
VALUES ('postgresql_0293_tax_review_keep','complete')
ON CONFLICT (key) DO UPDATE SET value=excluded.value;
