-- Consolidate employee payment orders into two business categories and
-- backfill approved expenses that predate automatic payment-order creation.

UPDATE employee_payment_orders
SET payment_type = 'salary'
WHERE payment_type <> 'expense';

ALTER TABLE employee_payment_orders
    DROP CONSTRAINT IF EXISTS employee_payment_orders_payment_type_check;
ALTER TABLE employee_payment_orders
    ADD CONSTRAINT employee_payment_orders_payment_type_check
    CHECK (payment_type IN ('salary','expense'));

INSERT INTO employee_payment_orders (
    payment_number, employee_id, payment_type, status, currency,
    gross_amount, advance_offset, other_adjustment, net_amount,
    description, source_type, source_id, source_number, source_key,
    sync_source, sync_status, paid_by, paid_at, created_by, created_at, updated_at
)
SELECT
    'EP-MIG-EXP-' || expenses.id::text,
    coalesce(expenses.beneficiary_id, expenses.created_by),
    'expense', CASE WHEN expenses.payout_status = 'paid' THEN 'paid' ELSE 'draft' END,
    coalesce(expenses.currency, 'USD'),
    expenses.amount, 0, 0, expenses.amount,
    '报销单 ' || expenses.expense_number,
    'expense', expenses.id, expenses.expense_number, 'expense:' || expenses.id::text,
    'migration', 'local',
    CASE WHEN expenses.payout_status = 'paid' THEN expenses.reimbursed_by ELSE NULL END,
    CASE WHEN expenses.payout_status = 'paid' THEN expenses.reimbursed_at ELSE NULL END,
    coalesce(expenses.reviewed_by, expenses.created_by),
    coalesce(expenses.reviewed_at, expenses.updated_at, to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"')),
    coalesce(expenses.reviewed_at, expenses.updated_at, to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'))
FROM expenses
WHERE expenses.status = 'approved'
  AND NOT EXISTS (
      SELECT 1 FROM employee_payment_orders payment
      WHERE payment.source_key = 'expense:' || expenses.id::text
  );

INSERT INTO payment_order_sources (
    payment_order_id, source_type, source_id, source_number, amount, created_at
)
SELECT payment.id, 'expense', expense.id, expense.expense_number, expense.amount, payment.created_at
FROM employee_payment_orders payment
JOIN expenses expense ON payment.source_key = 'expense:' || expense.id::text
WHERE NOT EXISTS (
    SELECT 1 FROM payment_order_sources source
    WHERE source.payment_order_id = payment.id AND source.source_type = 'expense' AND source.source_id = expense.id
);

INSERT INTO payment_order_events (
    payment_order_id, event_type, from_status, to_status, details, created_by, created_at
)
SELECT payment.id, 'created', '', payment.status,
       CASE WHEN payment.status = 'paid' THEN '历史已发放报销补建付款单' ELSE '已审核报销自动生成付款单' END,
       payment.created_by, payment.created_at
FROM employee_payment_orders payment
WHERE payment.source_type = 'expense'
  AND NOT EXISTS (
      SELECT 1 FROM payment_order_events event
      WHERE event.payment_order_id = payment.id AND event.event_type = 'created'
  );

INSERT INTO settings(key,value)
VALUES ('postgresql_0286_payment_order_consolidation','complete')
ON CONFLICT (key) DO UPDATE SET value=excluded.value;
