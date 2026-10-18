"""Legacy-compatible invoice helpers with explicit runtime dependencies."""

def invoice_items_from_form(*, db, request, to_float):
    rows = []
    for project_id, amount in zip(request.form.getlist("project_id"), request.form.getlist("amount")):
        if not project_id:
            continue
        project = db().execute(
            "select * from projects where id = ? and project_type = 'invoice' and is_active = 1",
            (project_id,),
        ).fetchone()
        if not project:
            raise ValueError("选择的项目不存在或已停用，请重新选择项目。")
        rows.append((project, to_float(amount)))
    if not rows:
        raise ValueError("请至少选择一个项目明细。")
    if sum(amount for _, amount in rows) <= 0:
        raise ValueError("发票金额必须大于 0。")
    return rows


def posted_client_id(*, request):
    try:
        return int(request.form.get("client_id"))
    except (TypeError, ValueError):
        raise ValueError("请选择客户。")


def posted_service_order_id(require_start_date=False, required=False, *, request, require_service_order):
    value = request.form.get("service_order_id")
    if not value:
        if required:
            raise ValueError("请选择关联工单。")
        return None
    try:
        order_id = int(value)
    except (TypeError, ValueError):
        raise ValueError("请选择有效工单。")
    order = require_service_order(order_id)
    if require_start_date and not order["start_date"]:
        raise ValueError("所选工单还没有开始日期，请先编辑工单并维护开始日期。")
    return order_id
