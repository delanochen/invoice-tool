"""Literal business catalogs; fresh mutable values for each app load."""

def make_customer_reimbursement_projects():
    return ('标准工时', '交通工时', '加班工时', '节假日工时', '里程费')

def make_customer_reimbursement_rate_fields():
    return (('standard_rate', 'regular_hours', '标准工时'), ('transport_rate', 'travel_hours', '交通工时'), ('public_transport_rate', 'public_transport_hours', '公共交通工时'), ('overtime_rate', 'overtime_hours', '加班工时'), ('holiday_rate', 'holiday_hours', '节假日工时'), ('mileage_rate', 'mileage', '里程费'))

def make_settlement_expense_field_labels():
    return {'lodging': '住宿费', 'airfare': '机票费', 'baggage': '行李费', 'rental_car': '租车费用', 'fuel': '燃油费', 'parking': '停车费', 'taxi': '打车费', 'other': '其他'}

