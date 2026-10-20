from io import BytesIO
from openpyxl import load_workbook
from invoice_tool.batch_exports.service import build_master_ledger, settlement_rows


def expense_amount(item, key):
    return item.get(key, 0)


def test_rental_fuel_is_visible_detail_row():
    reimbursement = {"rental_fuel_total": 25.5, "total_amount": 125.5}
    items = [{
        "worker_name": "Ethan", "project_date": "2026-10-20",
        "standard_hours": 1, "transport_hours": 0, "public_transport_hours": 0,
        "overtime_hours": 0, "holiday_hours": 0, "labor_total": 100,
        "lodging": 0, "airfare": 0, "baggage": 0, "rental_car": 0,
        "fuel": 0, "parking": 0, "taxi": 0, "mileage_total": 0,
        "other": 0, "total": 100,
    }]
    rows = settlement_rows(reimbursement, items, expense_amount)
    assert len(rows) == 2
    assert rows[1][1] == "租车油费"
    assert rows[1][13] == 25.5
    assert rows[1][18] == 25.5


def test_master_ledger_matches_reference_columns_and_fuel():
    reimbursement = {"rental_fuel_total": 25.5, "total_amount": 125.5}
    order = {"order_number": "WO-1001"}
    items = [{
        "worker_name": "Ethan", "project_date": "2026-10-20",
        "standard_hours": 1, "transport_hours": 2, "overtime_hours": 0, "holiday_hours": 0,
        "standard_rate": 70, "transport_rate": 35, "overtime_rate": 105, "holiday_rate": 105,
        "labor_total": 140, "lodging": 0, "airfare": 0, "baggage": 0, "rental_car": 0,
        "fuel": 0, "parking": 0, "taxi": 0, "miles": 10, "mileage_rate": 1,
        "mileage_total": 10, "other": 0, "total": 150,
    }]
    output = build_master_ledger([(reimbursement, order, items)], expense_amount=expense_amount)
    wb = load_workbook(BytesIO(output.getvalue()), data_only=True)
    ws = wb["工单结算"]
    assert ws.max_column == 25
    assert ws["A1"].value == "派工单号"
    assert ws["R2"].value == "加油"
    assert ws["A3"].value == "WO-1001"
    assert ws["R4"].value == 25.5
    assert ws["Y4"].value == 25.5
    assert "租车油费已在" in ws.cell(ws.max_row, 1).value
