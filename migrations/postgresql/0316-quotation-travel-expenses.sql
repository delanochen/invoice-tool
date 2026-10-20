-- v0.1.404 configurable quotation travel/reimbursable expense rules
ALTER TABLE quotations ADD COLUMN IF NOT EXISTS travel_expenses text NOT NULL DEFAULT '[]';
INSERT INTO settings(key,value) VALUES ('postgresql_0316_quotation_travel_expenses','complete') ON CONFLICT (key) DO UPDATE SET value=excluded.value;
