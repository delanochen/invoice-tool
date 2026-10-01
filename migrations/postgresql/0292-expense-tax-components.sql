-- v0.1.357 Phase 3A：ER 组件快照生命周期标记。
--
-- employee_payment_components 业务上 immutable：原始行永不 UPDATE/DELETE。
-- 报销被退回重审后 ensure_expense_payment_order 复用同一 ER（现行 reopen 语义），
-- 此时旧组件快照按「重开」退役：只置 superseded_at（审计可见的 lifecycle 标记，
-- 不触碰 amount / tax_category / tax_status_snapshot 等业务内容），再写入新快照。
--
-- 幂等：ADD COLUMN IF NOT EXISTS，重复执行安全。
-- 明确不改：任何既有行数据、SL 侧组件、金额/状态/批次/对账逻辑。

ALTER TABLE employee_payment_components
    ADD COLUMN IF NOT EXISTS superseded_at text;

INSERT INTO settings(key,value)
VALUES ('postgresql_0292_expense_tax_components','complete')
ON CONFLICT (key) DO UPDATE SET value=excluded.value;
