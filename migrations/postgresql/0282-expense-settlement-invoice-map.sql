-- v0.1.282 mapping table: expense project -> settlement field -> invoice project (additive-only).
-- Applied by scripts/upgrade_postgresql_0282.py; seed rows use ON CONFLICT DO NOTHING.

CREATE TABLE IF NOT EXISTS expense_settlement_invoice_map (
    id bigserial PRIMARY KEY,
    expense_project_name text NOT NULL UNIQUE,
    settlement_field text NOT NULL,
    invoice_project_name text NOT NULL,
    created_at text NOT NULL
);

INSERT INTO expense_settlement_invoice_map
    (expense_project_name, settlement_field, invoice_project_name, created_at)
SELECT v.expense_project_name, v.settlement_field, v.invoice_project_name,
       to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
FROM (VALUES
    ('MRO Supplies配件及耗材费', 'other', 'MRO Supplies配件及耗材费'),
    ('Client Entertainment Expenses客户招待费', 'other', 'Other'),
    ('Express Delivery Fees快递费', 'other', 'Express Delivery Fees'),
    ('Accommodation/Lodging住宿费', 'lodging', 'Travel Expenses Reimbursement'),
    ('Airfare机票费', 'airfare', 'Travel Expenses Reimbursement'),
    ('Car Rental Fee租车费用', 'rental_car', 'Travel Expenses Reimbursement'),
    ('Checked Baggage Fee行李费', 'baggage', 'Travel Expenses Reimbursement'),
    ('Fuel Expenses燃油费', 'fuel', 'Travel Expenses Reimbursement'),
    ('Parking Charge停车费', 'parking', 'Travel Expenses Reimbursement'),
    ('Taxi Fare / Ride-Hailing Fare打车费', 'taxi', 'Travel Expenses Reimbursement')
) AS v(expense_project_name, settlement_field, invoice_project_name)
WHERE NOT EXISTS (
    SELECT 1 FROM expense_settlement_invoice_map m
    WHERE m.expense_project_name = v.expense_project_name
);
