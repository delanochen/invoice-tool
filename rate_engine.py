from __future__ import annotations

from datetime import date, timedelta

from flask import abort, flash, g, redirect, request, url_for


CONTRACT_RATE_TYPES = (
    ("regular_hours", "标准工时", "hour"),
    ("overtime_hours", "加班工时", "hour"),
    ("holiday_hours", "节假日工时", "hour"),
    ("travel_hours", "交通工时", "hour"),
    ("public_transport_hours", "公共交通工时", "hour"),
    ("mileage", "里程补贴", "mile"),
    ("lodging_cap", "住宿实报实销上限", "person_night"),
)

EMPLOYEE_RATE_TYPES = (
    ("regular_hours", "标准工时", "hour"),
    ("overtime_hours", "加班工时", "hour"),
    ("holiday_hours", "节假日工时", "hour"),
    ("travel_hours", "交通工时", "hour"),
    ("public_transport_hours", "公共交通工时", "hour"),
    ("mileage", "里程补贴", "mile"),
    ("rental_drive_hours", "租车驾驶工时", "hour"),
)

CONTRACT_RATE_LABELS = {key: label for key, label, _ in CONTRACT_RATE_TYPES}
EMPLOYEE_RATE_LABELS = {key: label for key, label, _ in EMPLOYEE_RATE_TYPES}
RATE_UNITS = {key: unit for key, _, unit in (*CONTRACT_RATE_TYPES, *EMPLOYEE_RATE_TYPES)}


def _ensure_column(connection, table, column, definition):
    columns = {row["name"] for row in connection.execute(f"pragma table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"alter table {table} add column {column} {definition}")


def init_rate_schema(connection):
    connection.executescript(
        """
        create table if not exists contract_rate_versions (
            id integer primary key autoincrement,
            contract_id integer not null,
            version_no integer not null,
            effective_from text not null,
            effective_to text,
            status text not null default 'active',
            notes text not null default '',
            created_by integer,
            created_at text not null,
            updated_at text not null,
            foreign key(contract_id) references contracts(id) on delete cascade,
            foreign key(created_by) references users(id),
            unique(contract_id, version_no)
        );
        create index if not exists idx_contract_rate_versions_lookup
            on contract_rate_versions(contract_id, status, effective_from, effective_to);

        create table if not exists contract_rate_items (
            id integer primary key autoincrement,
            version_id integer not null,
            rate_type text not null,
            unit text not null,
            rate real not null default 0,
            billing_model text not null default 'rate',
            foreign key(version_id) references contract_rate_versions(id) on delete cascade,
            unique(version_id, rate_type)
        );

        create table if not exists employee_rate_versions (
            id integer primary key autoincrement,
            employee_grade_id integer not null,
            version_no integer not null,
            effective_from text not null,
            effective_to text,
            status text not null default 'active',
            notes text not null default '',
            created_by integer,
            created_at text not null,
            updated_at text not null,
            foreign key(employee_grade_id) references employee_grades(id) on delete cascade,
            foreign key(created_by) references users(id),
            unique(employee_grade_id, version_no)
        );
        create index if not exists idx_employee_rate_versions_lookup
            on employee_rate_versions(employee_grade_id, status, effective_from, effective_to);

        create table if not exists employee_rate_items (
            id integer primary key autoincrement,
            version_id integer not null,
            rate_type text not null,
            unit text not null,
            rate real not null default 0,
            foreign key(version_id) references employee_rate_versions(id) on delete cascade,
            unique(version_id, rate_type)
        );
        """
    )
    _ensure_column(connection, "customer_reimbursements", "contract_rate_version_id", "integer")
    _ensure_column(connection, "customer_reimbursements", "lodging_person_nights", "integer not null default 0")
    _ensure_column(connection, "customer_reimbursements", "lodging_cap_rate_snapshot", "real not null default 0")


def _iso_day(value):
    text = str(value or "").strip()
    if not text:
        return date.today().isoformat()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return date.today().isoformat()


def _version_for_day(connection, table, owner_column, owner_id, work_date):
    work_date = _iso_day(work_date)
    return connection.execute(
        f"""
        select * from {table}
        where {owner_column} = ? and status = 'active'
          and effective_from <= ?
          and (effective_to is null or effective_to = '' or effective_to >= ?)
        order by effective_from desc, version_no desc, id desc
        limit 1
        """,
        (owner_id, work_date, work_date),
    ).fetchone()


def contract_rate(connection, order_id, work_date, rate_type, lodging_fallback=0):
    """客户费率一律以数据库（合同费率版本）为准。

    查不到生效版本或对应条目时返回 source="missing"、rate=0 —— 调用方必须
    显式提示/拦截，禁止在代码里用默认价兜底。
    lodging_fallback 来自 settings 全局配置（住宿实报实销上限），不是硬编码费率。
    """
    order = connection.execute(
        "select id, contract_id from service_orders where id = ?",
        (order_id,),
    ).fetchone()
    if order and order["contract_id"]:
        version = _version_for_day(
            connection,
            "contract_rate_versions",
            "contract_id",
            order["contract_id"],
            work_date,
        )
        if version:
            item = connection.execute(
                "select * from contract_rate_items where version_id = ? and rate_type = ?",
                (version["id"], rate_type),
            ).fetchone()
            if item:
                return {
                    "rate": float(item["rate"] or 0),
                    "unit": item["unit"],
                    "source": "contract_version",
                    "version_id": version["id"],
                    "version_no": version["version_no"],
                }

    if rate_type == "lodging_cap":
        return {
            "rate": float(lodging_fallback or 0),
            "unit": "person_night",
            "source": "legacy_lodging_limit",
            "version_id": None,
            "version_no": None,
        }
    return {"rate": 0.0, "unit": RATE_UNITS.get(rate_type, "hour"), "source": "missing", "version_id": None, "version_no": None}


def employee_rate(connection, user_id, work_date, rate_type):
    row = connection.execute(
        """
        select users.employee_grade_id, employee_grades.*
        from users
        left join employee_grades on employee_grades.id = users.employee_grade_id
        where users.id = ?
        """,
        (user_id,),
    ).fetchone()
    if not row or not row["employee_grade_id"]:
        return {"rate": 0.0, "unit": RATE_UNITS.get(rate_type, "hour"), "source": "missing_grade", "version_id": None, "version_no": None}

    version = _version_for_day(
        connection,
        "employee_rate_versions",
        "employee_grade_id",
        row["employee_grade_id"],
        work_date,
    )
    if version:
        item = connection.execute(
            "select * from employee_rate_items where version_id = ? and rate_type = ?",
            (version["id"], rate_type),
        ).fetchone()
        if item:
            return {
                "rate": float(item["rate"] or 0),
                "unit": item["unit"],
                "source": "employee_rate_version",
                "version_id": version["id"],
                "version_no": version["version_no"],
            }

    fallback_columns = {
        "regular_hours": "standard_hourly_rate",
        "overtime_hours": "overtime_hourly_rate",
        "holiday_hours": "holiday_hourly_rate",
        "travel_hours": "transport_hourly_rate",
        "public_transport_hours": "transport_hourly_rate",
        "mileage": "car_mileage_rate",
        "rental_drive_hours": "rental_driving_hourly_rate",
    }
    column = fallback_columns.get(rate_type)
    if column and column in row.keys():
        return {
            "rate": float(row[column] or 0),
            "unit": RATE_UNITS.get(rate_type, "hour"),
            "source": "legacy_employee_grade",
            "version_id": None,
            "version_no": None,
        }
    return {"rate": 0.0, "unit": RATE_UNITS.get(rate_type, "hour"), "source": "missing", "version_id": None, "version_no": None}


def _float_form(name):
    raw = request.form.get(name, "").strip()
    if raw == "":
        return 0.0
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字。") from exc
    if value < 0:
        raise ValueError(f"{name} 不能小于 0。")
    return value


def _validate_dates(effective_from, effective_to):
    try:
        start = date.fromisoformat(effective_from)
    except ValueError as exc:
        raise ValueError("生效日期格式不正确。") from exc
    if effective_to:
        try:
            end = date.fromisoformat(effective_to)
        except ValueError as exc:
            raise ValueError("结束日期格式不正确。") from exc
        if end < start:
            raise ValueError("结束日期不能早于生效日期。")


def _prepare_new_version_range(connection, table, owner_column, owner_id, effective_from, effective_to):
    """Insert a new effective-dated version without destroying historical lookup.

    If the prior version is open-ended (or overlaps the new start), close it on
    the day before the new version. If a future version already exists and the
    new end is blank, end the new version the day before that future version.
    """
    start_day = date.fromisoformat(effective_from)
    versions = connection.execute(
        f"select * from {table} where {owner_column} = ? and status = 'active' order by effective_from, version_no, id",
        (owner_id,),
    ).fetchall()
    for version in versions:
        if version["effective_from"] == effective_from:
            raise ValueError("同一个生效日期已经存在有效费率版本。")

    previous = None
    following = None
    for version in versions:
        version_start = date.fromisoformat(version["effective_from"])
        if version_start < start_day:
            previous = version
        elif version_start > start_day and following is None:
            following = version

    if previous:
        previous_end = date.fromisoformat(previous["effective_to"]) if previous["effective_to"] else None
        if previous_end is None or previous_end >= start_day:
            connection.execute(
                f"update {table} set effective_to = ? where id = ?",
                ((start_day - timedelta(days=1)).isoformat(), previous["id"]),
            )

    if following:
        following_start = date.fromisoformat(following["effective_from"])
        maximum_end = following_start - timedelta(days=1)
        if effective_to:
            if date.fromisoformat(effective_to) > maximum_end:
                raise ValueError(f"结束日期必须早于下一版本生效日 {following['effective_from']}。")
        else:
            effective_to = maximum_end.isoformat()

    # Any remaining overlap means the requested range would cover an existing
    # middle version and must be corrected instead of silently rewriting it.
    end_text = effective_to or "9999-12-31"
    overlap = connection.execute(
        f"""
        select 1 from {table}
        where {owner_column} = ? and status = 'active'
          and effective_from >= ? and effective_from <= ?
        limit 1
        """,
        (owner_id, effective_from, end_text),
    ).fetchone()
    if overlap:
        raise ValueError("新版本日期区间与现有后续版本重叠。")
    return effective_to


def register_rate_routes(app, api):
    @app.route("/contracts/<int:contract_id>/rates", methods=["GET", "POST"])
    @api["login_required"]
    def contract_rate_versions(contract_id):
        contract = api["require_contract_access"](contract_id)
        if request.method == "POST":
            if not api["can_manage_contracts"]():
                abort(403)
            try:
                effective_from = request.form.get("effective_from", "").strip()
                effective_to = request.form.get("effective_to", "").strip() or None
                _validate_dates(effective_from, effective_to)
                effective_to = _prepare_new_version_range(
                    api["db"](), "contract_rate_versions", "contract_id", contract_id, effective_from, effective_to
                )
                next_no = api["db"]().execute(
                    "select coalesce(max(version_no), 0) + 1 as value from contract_rate_versions where contract_id = ?",
                    (contract_id,),
                ).fetchone()["value"]
                cursor = api["db"]().execute(
                    """
                    insert into contract_rate_versions
                        (contract_id, version_no, effective_from, effective_to, status, notes, created_by, created_at, updated_at)
                    values (?, ?, ?, ?, 'active', ?, ?, ?, ?)
                    """,
                    (contract_id, next_no, effective_from, effective_to, request.form.get("notes", "").strip(), g.user["id"], api["now"](), api["now"]()),
                )
                version_id = cursor.lastrowid
                for key, _label, unit in CONTRACT_RATE_TYPES:
                    api["db"]().execute(
                        "insert into contract_rate_items (version_id, rate_type, unit, rate, billing_model) values (?, ?, ?, ?, ?)",
                        (version_id, key, unit, _float_form(key), "actual_with_cap" if key == "lodging_cap" else "rate"),
                    )
                api["log_action"]("create", "contract_rate_version", version_id, f"{contract['contract_number']} v{next_no}", f"生效：{effective_from} 至 {effective_to or '长期'}")
                api["db"]().commit()
                flash("合同费率版本已创建。", "success")
                return redirect(url_for("contract_rate_versions", contract_id=contract_id))
            except ValueError as error:
                api["db"]().rollback()
                flash(str(error), "error")
        versions = api["db"]().execute(
            "select * from contract_rate_versions where contract_id = ? order by effective_from desc, version_no desc",
            (contract_id,),
        ).fetchall()
        items = api["db"]().execute(
            """
            select contract_rate_items.* from contract_rate_items
            join contract_rate_versions on contract_rate_versions.id = contract_rate_items.version_id
            where contract_rate_versions.contract_id = ?
            order by contract_rate_versions.version_no desc, contract_rate_items.id
            """,
            (contract_id,),
        ).fetchall()
        item_map = {}
        for item in items:
            item_map.setdefault(item["version_id"], {})[item["rate_type"]] = item
        return render_template(
            "contract_rate_versions.html",
            contract=contract,
            versions=versions,
            item_map=item_map,
            rate_types=CONTRACT_RATE_TYPES,
            can_edit=api["can_manage_contracts"](),
        )

    @app.post("/contracts/<int:contract_id>/rates/<int:version_id>/deactivate")
    @api["login_required"]
    def deactivate_contract_rate_version(contract_id, version_id):
        contract = api["require_contract_access"](contract_id)
        if not api["can_manage_contracts"]():
            abort(403)
        row = api["db"]().execute(
            "select * from contract_rate_versions where id = ? and contract_id = ?",
            (version_id, contract_id),
        ).fetchone()
        if not row:
            abort(404)
        api["db"]().execute("update contract_rate_versions set status = 'inactive', updated_at = ? where id = ?", (api["now"](), version_id))
        api["log_action"]("update", "contract_rate_version", version_id, f"{contract['contract_number']} v{row['version_no']}", "停用费率版本")
        api["db"]().commit()
        flash("费率版本已停用。", "success")
        return redirect(url_for("contract_rate_versions", contract_id=contract_id))

    @app.route("/employee-grades/<int:grade_id>/rates", methods=["GET", "POST"])
    @api["login_required"]
    def employee_rate_versions(grade_id):
        if not api["has_action_permission"]("employee_grades", "view"):
            abort(403)
        grade = api["db"]().execute("select * from employee_grades where id = ?", (grade_id,)).fetchone()
        if not grade:
            abort(404)
        if request.method == "GET":
            # 独立费率版本页已并入员工等级工作台（左等级列表 / 右费率版本）
            return redirect(url_for("employee_grades", grade_id=grade_id))
        if not api["has_action_permission"]("employee_grades", "edit"):
            abort(403)
        try:
            effective_from = request.form.get("effective_from", "").strip()
            effective_to = request.form.get("effective_to", "").strip() or None
            _validate_dates(effective_from, effective_to)
            effective_to = _prepare_new_version_range(
                api["db"](), "employee_rate_versions", "employee_grade_id", grade_id, effective_from, effective_to
            )
            next_no = api["db"]().execute(
                "select coalesce(max(version_no), 0) + 1 as value from employee_rate_versions where employee_grade_id = ?",
                (grade_id,),
            ).fetchone()["value"]
            cursor = api["db"]().execute(
                """
                insert into employee_rate_versions
                    (employee_grade_id, version_no, effective_from, effective_to, status, notes, created_by, created_at, updated_at)
                values (?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (grade_id, next_no, effective_from, effective_to, request.form.get("notes", "").strip(), g.user["id"], api["now"](), api["now"]()),
            )
            version_id = cursor.lastrowid
            for key, _label, unit in EMPLOYEE_RATE_TYPES:
                api["db"]().execute(
                    "insert into employee_rate_items (version_id, rate_type, unit, rate) values (?, ?, ?, ?)",
                    (version_id, key, unit, _float_form(key)),
                )
            api["log_action"]("create", "employee_rate_version", version_id, f"{grade['grade_name']} v{next_no}", f"生效：{effective_from} 至 {effective_to or '长期'}")
            api["db"]().commit()
            flash("员工结算费率版本已创建。", "success")
        except ValueError as error:
            api["db"]().rollback()
            flash(str(error), "error")
        return redirect(url_for("employee_grades", grade_id=grade_id))
