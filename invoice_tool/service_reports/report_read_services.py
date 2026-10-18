"""Service-report attachment grouping and navigation queries."""

def get_report_attachments(report_id, *, db):
    rows = db().execute(
        """
        select service_report_attachments.*, users.name as uploader_name
        from service_report_attachments left join users on users.id = service_report_attachments.uploaded_by
        where report_id = ?
        order by category asc, uploaded_at asc, id asc
        """,
        (report_id,),
    ).fetchall()
    grouped = {"self_check": [], "site": [], "arrival": [], "departure": [], "mileage_proof": []}
    for row in rows:
        grouped.setdefault(row["category"], []).append(row)
    return grouped


def adjacent_service_report_ids(report_id, *, db, service_order_access_filters):
    """上一个 / 下一个日报（按列表默认排序：日期 desc, id desc）。

    导航必须在当前用户有权访问的日报范围内移动：外部账号只能看到被授权工单
    的日报，所以这里复用 service_order_access_filters，避免通过「下一个」跳到
    无权查看的日报。列表顺序为「日期降序、id 降序」，因此「上一个」是列表里
    更靠上的一条（日期更大或同日 id 更大），「下一个」是更靠下的一条。
    """
    current = db().execute(
        """
        select id,
               coalesce(actual_work_date, report_date) as work_date
        from service_reports where id = ?
        """,
        (report_id,),
    ).fetchone()
    if not current:
        return {"previous": None, "next": None}

    clauses, params = service_order_access_filters("service_orders")
    if not clauses:
        clauses.append("1 = 1")
    access_where = " and ".join(clauses)

    def fetch(direction):
        # previous：列表中更靠上 = 日期更大，或同日 id 更大
        # previous：列表中更靠上 = 日期更大，或同日 id 更大。
        # v0.1.361 修复：previous 的候选集是「日期比当前大」的全体，要取其中
        # **最靠近当前**的一条（日期最小、同日 id 最小）才是紧邻的上一条；
        # 之前误用 desc 取了集合里最新的那条，导致除第 2 条外所有日报的
        # 「上一条」都直接跳到工单第 1 条。
        if direction == "previous":
            comparison = (
                "(coalesce(service_reports.actual_work_date, service_reports.report_date) > ? "
                "or (coalesce(service_reports.actual_work_date, service_reports.report_date) = ? "
                "and service_reports.id > ?))"
            )
            ordering = (
                "order by coalesce(service_reports.actual_work_date, service_reports.report_date) asc, "
                "service_reports.id asc limit 1"
            )
        else:
            comparison = (
                "(coalesce(service_reports.actual_work_date, service_reports.report_date) < ? "
                "or (coalesce(service_reports.actual_work_date, service_reports.report_date) = ? "
                "and service_reports.id < ?))"
            )
            ordering = (
                "order by coalesce(service_reports.actual_work_date, service_reports.report_date) desc, "
                "service_reports.id desc limit 1"
            )
        row = db().execute(
            f"""
            select service_reports.id
            from service_reports
            join service_orders on service_orders.id = service_reports.service_order_id
            where {access_where} and {comparison}
            {ordering}
            """,
            [*params, current["work_date"], current["work_date"], current["id"]],
        ).fetchone()
        return row["id"] if row else None

    return {"previous": fetch("previous"), "next": fetch("next")}


def same_order_adjacent_report_ids(report_id, *, db, g, is_external_employee):
    """同一工单下的上一个/下一个日报（按日期降序）。

    与 adjacent_service_report_ids 不同：这里限定 service_order_id 相同，
    方便用户在编辑界面连续切换同一工单的不同日报，不必返回工单详情页。
    排序与全局一致：日期降序、id 降序；上一个 = 日期更近（更大），下一个 = 日期更早。
    外部员工在编辑页只能打开自己创建的日报（edit_service_report 的守卫），
    导航必须收在同样边界内，否则会给出点了就 403 的链接并泄露他人工单日报存在。
    """
    current = db().execute(
        """
        select id, service_order_id,
               coalesce(actual_work_date, report_date) as work_date
        from service_reports where id = ?
        """,
        (report_id,),
    ).fetchone()
    if not current:
        return {"previous": None, "next": None}

    scope_sql = ""
    scope_params: list = []
    if is_external_employee():
        scope_sql = " and created_by = ?"
        scope_params = [g.user["id"]]

    def fetch(direction):
        # v0.1.361 修复：previous 必须在「日期比当前大」的候选集里取**最靠近当前**
        # 的一条（asc），desc 会取到工单最新一条，导致「上一条」永远是第 1 条。
        if direction == "previous":
            comparison = (
                "(coalesce(actual_work_date, report_date) > ? "
                "or (coalesce(actual_work_date, report_date) = ? and id > ?))"
            )
            ordering = "order by coalesce(actual_work_date, report_date) asc, id asc limit 1"
        else:
            comparison = (
                "(coalesce(actual_work_date, report_date) < ? "
                "or (coalesce(actual_work_date, report_date) = ? and id < ?))"
            )
            ordering = "order by coalesce(actual_work_date, report_date) desc, id desc limit 1"
        row = db().execute(
            """
            select id from service_reports
            where service_order_id = ?""" + scope_sql + """ and """ + comparison + """
            """ + ordering,
            (
                current["service_order_id"],
                *scope_params,
                current["work_date"],
                current["work_date"],
                current["id"],
            ),
        ).fetchone()
        return row["id"] if row else None

    return {"previous": fetch("previous"), "next": fetch("next")}
