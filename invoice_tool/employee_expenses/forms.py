"""Legacy-compatible employee-expense forms; explicit dependencies only."""

def expense_defaults(expense=None, *, date, expense_beneficiary_id, g, next_expense_number):
    if expense:
        return dict(expense) | {"beneficiary_id": expense_beneficiary_id(expense)}
    return {
        "beneficiary_id": g.user["id"],
        "expense_number": next_expense_number(),
        "project_id": None,
        "project": "",
        "expense_date": date.today().isoformat(),
        "amount": "",
        "currency": "USD",
        "description": "",
        "business_purpose": "",
        "status": "draft",
    }


def expense_items(expense_id, *, db):
    return db().execute(
        """
        select expense_items.*, projects.is_active
        from expense_items
        left join projects on projects.id = expense_items.project_id
        where expense_items.expense_id = ?
        order by expense_items.sort_order, expense_items.id
        """,
        (expense_id,),
    ).fetchall()


def expense_items_from_form(expense_id=None, *, db, is_fuel_project_name, re, request, secrets, to_float):
    project_ids = request.form.getlist("project_id")
    line_keys = request.form.getlist("item_line_key")
    amounts = request.form.getlist("item_amount")
    descriptions = request.form.getlist("item_description")
    fuel_vehicle_types = request.form.getlist("fuel_vehicle_type")
    rows = []
    seen_line_keys = set()
    for index, project_id in enumerate(project_ids):
        if not project_id:
            continue
        project = db().execute(
            """
            select * from projects
            where id = ? and project_type = 'expense'
              and (is_active = 1 or id in (
                  select project_id from expense_items where expense_id = ?
              ))
            """,
            (project_id, expense_id or 0),
        ).fetchone()
        if not project:
            raise ValueError("选择的员工报销项目不存在或已停用，请重新选择。")
        amount = to_float(amounts[index] if index < len(amounts) else 0)
        if amount <= 0:
            raise ValueError("每个员工报销项目的金额必须大于 0。")
        fuel_vehicle_type = ""
        if is_fuel_project_name(project["name"]):
            fuel_vehicle_type = (
                fuel_vehicle_types[index].strip()
                if index < len(fuel_vehicle_types)
                else ""
            )
            if fuel_vehicle_type not in {"personal", "rental"}:
                raise ValueError("油费报销必须选择个人／自有车辆或租赁车辆。")
        description = descriptions[index].strip() if index < len(descriptions) else ""
        line_key = line_keys[index].strip() if index < len(line_keys) else ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", line_key) or line_key in seen_line_keys:
            line_key = f"line-{secrets.token_urlsafe(12)}"
        seen_line_keys.add(line_key)
        rows.append(
            {
                "line_key": line_key,
                "project": project,
                "amount": amount,
                "description": description,
                "fuel_vehicle_type": fuel_vehicle_type or None,
                "sort_order": len(rows),
            }
        )
    if not rows:
        raise ValueError("请至少添加一个员工报销项目。")
    return rows


def save_expense_items(expense_id, item_rows, *, db, expense_attachment_path, get_expense_attachments, os):
    retained_keys = {item["line_key"] for item in item_rows}
    existing_items = db().execute(
        "select * from expense_items where expense_id = ? order by id",
        (expense_id,),
    ).fetchall()
    existing_by_key = {item["line_key"]: item for item in existing_items}
    removed_items = [item for item in existing_items if item["line_key"] not in retained_keys]

    # Settlement links point at expense_items.id.  Replacing every row during an
    # edit breaks those links (and PostgreSQL correctly rejects the delete).
    # Keep retained rows in place; a linked row may not be removed from the form.
    for item in removed_items:
        linked = db().execute(
            "select 1 from customer_reimbursement_expense_links where expense_item_id = ? limit 1",
            (item["id"],),
        ).fetchone()
        if linked:
            raise ValueError(
                f"报销项目“{item['project']}”已被客户结算引用，不能删除；请保留该项目后再提交。"
            )

    existing_attachments = get_expense_attachments(expense_id)
    for attachment in existing_attachments:
        attachment_key = (attachment["expense_item_key"] or "").strip()
        if attachment_key and attachment_key not in retained_keys:
            try:
                os.remove(expense_attachment_path(attachment))
            except FileNotFoundError:
                pass
            db().execute("delete from expense_attachments where id = ?", (attachment["id"],))
    for item in removed_items:
        db().execute("delete from expense_items where id = ?", (item["id"],))
    for item in item_rows:
        project = item["project"]
        existing = existing_by_key.get(item["line_key"])
        values = (
            project["id"],
            project["name"],
            item["amount"],
            item["description"],
            item["fuel_vehicle_type"],
            item["sort_order"],
        )
        if existing:
            db().execute(
                """
                update expense_items
                set project_id = ?, project = ?, amount = ?, description = ?,
                    fuel_vehicle_type = ?, sort_order = ?
                where id = ?
                """,
                values + (existing["id"],),
            )
        else:
            db().execute(
                """
                insert into expense_items (
                    expense_id, line_key, project_id, project, amount, description,
                    fuel_vehicle_type, sort_order
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (expense_id, item["line_key"]) + values,
            )
