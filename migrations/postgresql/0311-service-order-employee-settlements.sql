-- 工单员工结算约定：客户结算与员工结算相互独立。
-- fixed 表示该员工在该工单采用全包一口价：工时、里程、补贴和报销均不再另计，
-- 但日报中的实际工时/里程仍完整保留，供利润和效率分析。

CREATE TABLE IF NOT EXISTS service_order_employee_settlements (
    id bigserial PRIMARY KEY,
    service_order_id bigint NOT NULL REFERENCES service_orders(id) ON DELETE CASCADE,
    employee_id bigint NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    settlement_mode text NOT NULL DEFAULT 'actual'
        CHECK (settlement_mode IN ('actual', 'fixed')),
    fixed_amount numeric(14,2) NOT NULL DEFAULT 0
        CHECK (fixed_amount >= 0),
    currency text NOT NULL DEFAULT 'USD',
    settlement_date date,
    status text NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('draft', 'confirmed', 'paid', 'cancelled')),
    notes text NOT NULL DEFAULT '',
    confirmed_by bigint REFERENCES users(id) ON DELETE SET NULL,
    confirmed_at timestamp,
    created_at timestamp NOT NULL,
    updated_at timestamp NOT NULL,
    UNIQUE (service_order_id, employee_id),
    CHECK (
        (settlement_mode = 'actual' AND fixed_amount = 0)
        OR (settlement_mode = 'fixed' AND fixed_amount > 0 AND settlement_date IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_order_employee_settlements_employee
    ON service_order_employee_settlements(employee_id, settlement_date, status);

INSERT INTO settings(key,value)
VALUES ('postgresql_0311_service_order_employee_settlements','complete')
ON CONFLICT (key) DO UPDATE SET value=excluded.value;
