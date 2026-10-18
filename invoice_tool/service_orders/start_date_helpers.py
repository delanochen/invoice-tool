"""Legacy-compatible residual_order_guard helpers; explicit runtime dependencies."""

def require_service_order_start_date(order, *, flash, redirect, url_for):
    if order["start_date"]:
        return None
    flash("请先编辑工单并维护开始日期，再新增发票、工作日报或报销。", "error")
    return redirect(url_for("edit_service_order", order_id=order["id"]))
