# -*- coding: utf-8 -*-
"""Generate a sample quotation PDF to verify the template-aligned layout."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from invoice_tool.quotations.documents import build_quotation_pdf  # noqa: E402

quote = {
    "quotation_number": "PP-Q-26001",
    "quotation_date": "2026-10-01",
    "valid_until": "2026-10-31",
    "prepared_by": "John Smith",
    "customer": "Acme Manufacturing",
    "contact": "Jane Doe",
    "project_name": "Control Panel Retrofit",
    "project_no": "PRJ-2026-0142",
    "project_location": "Houston, TX",
    "po_no": "PO-88231",
    "currency": "USD",
    "status": "draft",
    "project_description": ("Retrofit of existing motor control center, including "
                            "breaker replacement and commissioning support."),
    "scope_1": "On-site labor for breaker replacement",
    "scope_2": "Supervision and commissioning",
    "scope_3": "",
    "pricing_lines": json.dumps([
        {"key": "regular_labor", "qty": 40, "rate": 120, "amount": 4800.00},
        {"key": "overtime_labor", "qty": 0, "rate": 180, "amount": ""},
        {"key": "travel_time", "qty": 6, "rate": 90, "amount": 540.00},
        {"key": "waiting_standby", "qty": "", "rate": "", "amount": ""},
        {"key": "mileage", "qty": 650, "rate": 0.67, "amount": 435.50},
        {"key": "lodging", "qty": "", "rate": "", "amount": ""},
        {"key": "materials_parts", "qty": "", "rate": "", "amount": ""},
        {"key": "other", "qty": "", "rate": "", "amount": ""},
    ]),
    "subtotal": 5775.50,
    "tax": 462.04,
    "total": 6237.54,
    "rate_schedule": json.dumps([
        {"key": "regular_labor", "rate": 120},
        {"key": "overtime_labor", "rate": 180},
        {"key": "holiday_labor", "rate": 240},
        {"key": "travel_time", "rate": 90},
        {"key": "waiting_standby", "rate": 90},
        {"key": "technical_support", "rate": 135},
        {"key": "mileage", "rate": 0.67},
        {"key": "lodging", "rate": 250},
        {"key": "per_diem", "rate": 75},
    ]),
    "crew_size": "2 persons",
    "workdays": "3 days",
    "hours_per_day": "10 hours",
    "expected_start_date": "2026-11-02",
    "expected_completion": "2026-11-04",
    "normal_working_hours": "7:00 AM - 5:00 PM",
    "customer_provides": "access,escort",
    "assumptions_other": "Site badge required for all contractor personnel.",
    "payment_terms": "net_30",
    "quotation_validity": "30 calendar days",
    "invoice_frequency": "upon_completion",
    "pricing_type": "estimated",
    "tax_note": "Excluded unless stated",
    "notes": "Sample quotation for layout verification.",
}

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quotation_layout_v2.pdf")
build_quotation_pdf(quote, out)
print("PDF generated at", out, os.path.getsize(out), "bytes")
