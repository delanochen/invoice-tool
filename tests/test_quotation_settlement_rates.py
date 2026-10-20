from invoice_tool.customer_reimbursements.item_forms import customer_reimbursement_items_from_form
from invoice_tool.customer_reimbursements.rates import assert_contract_rates_covered


class FakeForm:
    def __init__(self, values):
        self.values = values

    def getlist(self, name):
        return list(self.values.get(name, []))


class FakeRequest:
    def __init__(self, values):
        self.form = FakeForm(values)


def _calculate(row, sort_order):
    row = dict(row)
    row["sort_order"] = sort_order
    row["labor_total"] = (
        row["standard_hours"] * row["standard_rate"]
        + row["transport_hours"] * row["transport_rate"]
        + row["public_transport_hours"] * row["public_transport_rate"]
        + row["overtime_hours"] * row["overtime_rate"]
        + row["holiday_hours"] * row["holiday_rate"]
    )
    row["mileage_total"] = row["miles"] * row["mileage_rate"]
    row["total"] = row["labor_total"] + row["mileage_total"]
    return row


def _form_values():
    return {
        "worker_name": ["Ethan"],
        "project_date": ["2026-10-06"],
        "standard_hours": ["8"],
        "transport_hours": ["0"],
        "public_transport_hours": ["0"],
        "overtime_hours": ["0"],
        "holiday_hours": ["0"],
        "lodging": ["0"],
        "airfare": ["0"],
        "baggage": ["0"],
        "rental_car": ["0"],
        "fuel": ["0"],
        "parking": ["0"],
        "taxi": ["0"],
        "miles": ["123"],
        "other": ["0"],
        "source_report_id": ["10"],
        "source_worker_user_id": ["20"],
    }


def test_quotation_form_preserves_snapshot_rates_and_skips_contract_guard():
    guard_calls = []
    resolve_calls = []
    existing = [{
        "source_report_id": 10,
        "source_worker_user_id": 20,
        "worker_name": "Ethan",
        "project_date": "2026-10-06",
        "standard_rate": 0,
        "transport_rate": 0,
        "public_transport_rate": 0,
        "overtime_rate": 0,
        "holiday_rate": 0,
        "mileage_rate": 0,
    }]
    rows = customer_reimbursement_items_from_form(
        1,
        assert_contract_rates_covered=lambda *_: guard_calls.append(True),
        calculate_customer_reimbursement_item=_calculate,
        existing_items=existing,
        money_float=lambda value: float(value or 0),
        request=FakeRequest(_form_values()),
        resolve_contract_rates=lambda *_: (resolve_calls.append(True) or ({}, [])),
        settlement_basis="quotation",
        to_float=lambda value: float(value or 0),
        validate_fuel_reimbursement_allowed=lambda *_: None,
    )
    assert guard_calls == []
    assert resolve_calls == []
    assert rows[0]["miles"] == 123
    assert rows[0]["mileage_rate"] == 0
    assert rows[0]["mileage_total"] == 0


def test_contract_form_still_requires_contract_rates():
    guard_calls = []
    resolve_calls = []
    rates = {
        "standard_rate": 10,
        "transport_rate": 0,
        "public_transport_rate": 0,
        "overtime_rate": 0,
        "holiday_rate": 0,
        "mileage_rate": 1,
    }
    customer_reimbursement_items_from_form(
        1,
        assert_contract_rates_covered=lambda *_: guard_calls.append(True),
        calculate_customer_reimbursement_item=_calculate,
        money_float=lambda value: float(value or 0),
        request=FakeRequest(_form_values()),
        resolve_contract_rates=lambda *_: (resolve_calls.append(True) or (rates, [])),
        settlement_basis="contract",
        to_float=lambda value: float(value or 0),
        validate_fuel_reimbursement_allowed=lambda *_: None,
    )
    assert guard_calls == [True]
    assert resolve_calls == [True]


def test_missing_rate_error_format_has_no_none_or_tuple():
    rows = [{"project_date": "2026-10-06", "miles": 10}]
    try:
        assert_contract_rates_covered(
            1,
            rows,
            CUSTOMER_REIMBURSEMENT_RATE_FIELDS=[("mileage_rate", "mileage", "里程费")],
            resolve_contract_rates=lambda *_: ({"mileage_rate": 0}, ["里程费"]),
            used_customer_rate_fields=lambda _row: ["mileage_rate"],
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError")
    assert "里程费（2026-10-06）" in message
    assert "None" not in message
    assert "('2026-10-06', '里程费')" not in message
