-- v0.1.359 Phase 5C：Payment Statement Email Delivery Log。
--
-- email_delivery_logs 原本只在「发送成功后」由 record_email_delivery() 记
-- 一行（无 status / error 字段 —— 不成功根本不会有行）。Phase 5C 要求
-- 每次发送尝试都留痕（SMTP 失败也要记 failed + error_message），并且
-- 记录能自含 employee_id（不需要 join 才知道发给哪个员工）。
--
-- 只做加列，既有行不受影响：
--   status        默认 'sent' —— 历史「有行即成功」语义被显式化；
--   error_message 默认 ''    —— 失败原因；
--   employee_id   可空       —— 历史行不回填，新行由应用写入。
--
-- 幂等：ADD COLUMN IF NOT EXISTS，重复执行安全。
-- 明确不改：任何既有行数据、批次 / 付款单 / 组件 / 往来账 / 对账逻辑。

ALTER TABLE email_delivery_logs
    ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'sent';

ALTER TABLE email_delivery_logs
    ADD COLUMN IF NOT EXISTS error_message text NOT NULL DEFAULT '';

ALTER TABLE email_delivery_logs
    ADD COLUMN IF NOT EXISTS employee_id integer;

INSERT INTO settings(key,value)
VALUES ('postgresql_0294_email_delivery_status','complete')
ON CONFLICT (key) DO UPDATE SET value=excluded.value;
