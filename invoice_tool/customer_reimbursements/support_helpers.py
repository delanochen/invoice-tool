"""Legacy-compatible settlement_support helpers; explicit runtime dependencies."""

def _parse_quotation_revision_snapshot(quotation_revision):
    import json as _json
    try:
        data = _json.loads(quotation_revision["snapshot_json"] or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def excessive_following_mileage_rows(order_id, threshold=500, *, db):
    return db().execute(
        """
        select service_reports.id as report_id, service_reports.report_date,
               users.name as worker_name, service_report_workers.driving_miles
        from service_report_workers
        join service_reports on service_reports.id = service_report_workers.report_id
        join users on users.id = service_report_workers.user_id
        where service_reports.service_order_id = ?
          and service_report_workers.travel_mode = 'following'
          and service_report_workers.driving_miles > ?
        order by service_reports.report_date, users.name
        """,
        (order_id, threshold),
    ).fetchall()


def _fallback_row_for_worker(candidates_by_worker, worker_key, *, _reimbursement_date_key):
    """报销精确（人名+日期）匹配落空时回落到同一员工的已有明细行，避免
    『同人异日拆行』导致同一来源被计入两次（翻倍根因②）。
    - 该员工只有一行 → 直接复用
    - 多行 → 取 project_date 最近的一行（'YYYY-MM-DD' 字符串比较有效）
    - 该员工无任何行 → 返回 None（由调用方新建行）
    """
    rows = candidates_by_worker.get(worker_key)
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    return max(rows, key=lambda r: _reimbursement_date_key(r.get("project_date")))


def service_order_active_invoice(order_id, *, db):
    return db().execute(
        """
        select *
        from invoices
        where service_order_id = ? and status != 'void'
        order by issue_date desc, id desc
        limit 1
        """,
        (order_id,),
    ).fetchone()


def remove_customer_reimbursement_pdf(reimbursement, *, customer_reimbursement_file_path, os):
    path = customer_reimbursement_file_path(reimbursement)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
