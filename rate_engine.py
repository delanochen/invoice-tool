from __future__ import annotations

from datetime import date, timedelta

from flask import abort, flash, g, redirect, render_template, request, url_for


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

# 员工等级没有生效费率版本时，回退到 employee_grades 表上的静态费率列。
# 工资计算（employee_rate）与「员工等级」页面的费率展示共用这张表，
# 避免页面显示的费率跟真正参与计算的费率出现两套口径。
EMPLOYEE_STATIC_RATE_COLUMNS = {
    "regular_hours": "standard_hourly_rate",
    "overtime_hours": "overtime_hourly_rate",
    "holiday_hours": "holiday_hourly_rate",
    "travel_hours": "transport_hourly_rate",
    "public_transport_hours": "transport_hourly_rate",
    "mileage": "car_mileage_rate",
    "rental_drive_hours": "rental_driving_hourly_rate",
}


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

    fallback_columns = EMPLOYEE_STATIC_RATE_COLUMNS
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


def employee_grade_rate_snapshot(connection, grade_id, work_date):
    """员工等级在指定日期实际生效的费率快照。

    口径与 employee_rate() 完全一致：先取当天生效的费率版本条目，
    没有版本（或版本里缺该条目）时回退到等级资料里的静态费率列。

    返回 {"version": row|None, "rates": {rate_type: {"rate", "unit", "source"}}}，
    source ∈ {"version", "static", "missing"}；"missing" 表示没有版本条目、
    静态列也没值（调用方负责提示缺费率，不要用默认价兜底）。
    """
    grade = connection.execute("select * from employee_grades where id = ?", (grade_id,)).fetchone()
    if not grade:
        return {"version": None, "rates": {}}
    version = _version_for_day(
        connection, "employee_rate_versions", "employee_grade_id", grade_id, work_date
    )
    items = {}
    if version:
        for item in connection.execute(
            "select * from employee_rate_items where version_id = ?", (version["id"],)
        ).fetchall():
            items[item["rate_type"]] = item
    grade_keys = set(grade.keys())
    rates = {}
    for key, _label, unit in EMPLOYEE_RATE_TYPES:
        item = items.get(key)
        if item:
            rates[key] = {"rate": float(item["rate"] or 0), "unit": item["unit"] or unit, "source": "version"}
            continue
        column = EMPLOYEE_STATIC_RATE_COLUMNS.get(key)
        if column and column in grade_keys:
            rates[key] = {"rate": float(grade[column] or 0), "unit": unit, "source": "static"}
        else:
            rates[key] = {"rate": 0.0, "unit": unit, "source": "missing"}
    return {"version": version, "rates": rates}


def current_rate_values(connection, grade_id, work_date):
    """把「当前生效费率」取成 7 项数值，供「按当前费率建版本」一键固化。

    "当前生效费率" 的口径完全复用 employee_grade_rate_snapshot()（版本优先、静态兜底），
    也就是「员工等级」页面上那一栏数字、以及工资计算实际取的值 —— 三者同一口径，
    不在这里另立一套默认价。

    任一项 source == "missing"（既没有版本条目也没有等级静态值）时直接拒绝：
    固化出一条按 0 计薪的版本，比「没有版本」更危险 —— 没有版本时页面会明确
    提示「未设置」，而一条全 0 的版本看起来是「已配置」，会静默把工资算成 0。
    """
    snapshot = employee_grade_rate_snapshot(connection, grade_id, _iso_day(work_date))
    values = {}
    missing = []
    for key, label, _unit in EMPLOYEE_RATE_TYPES:
        entry = snapshot["rates"].get(key, {})
        if entry.get("source") == "missing":
            missing.append(label)
        values[key] = float(entry.get("rate") or 0)
    if missing:
        raise ValueError(
            "以下费率既没有生效版本也没有等级静态值，无法固化："
            + "、".join(missing)
            + "。请在下方表单里手动填写。"
        )
    if not any(values.values()):
        raise ValueError(
            "当前 7 项费率全是 0，固化成版本后工资会按 0 计算；请在下方表单里填写真实费率。"
        )
    return values


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


def _ensure_version_is_current(version):
    """历史版本（有效期已整体结束）不允许修改或停用。

    历史版本是过往工资的取价依据，改它等于改历史口径；需要纠正时新建一条版本，
    把错的那段有效期收掉即可。
    """
    if version["effective_to"] and version["effective_to"] < date.today().isoformat():
        raise ValueError("该版本的有效期已结束，属于历史版本，不允许修改或停用；需要纠正请新建版本。")


def _validate_range_against_versions(connection, table, owner_column, owner_id, exclude_id, effective_from, effective_to):
    """编辑 / 重新启用已有版本时的有效期校验：除自身外不得与任何有效版本重叠。

    与 _prepare_new_version_range 的区别：那条是「新建时自动收拢上一版本」，
    这条是「改历史」——只能拒绝，绝不能顺手改别人的有效期。
    返回归一化后的 effective_to（供更新语句复用）。
    """
    start_day = date.fromisoformat(effective_from)
    end_day = date.fromisoformat(effective_to) if effective_to else None
    versions = connection.execute(
        f"select * from {table} where {owner_column} = ? and status = 'active' and id != ?",
        (owner_id, exclude_id),
    ).fetchall()
    for version in versions:
        other_start = date.fromisoformat(version["effective_from"])
        other_end = date.fromisoformat(version["effective_to"]) if version["effective_to"] else date.max
        if other_start <= (end_day or date.max) and start_day <= other_end:
            raise ValueError(
                f"有效期与版本 v{version['version_no']}"
                f"（{version['effective_from']} ～ {version['effective_to'] or '长期'}）重叠。"
            )
    return effective_to


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
            notes = request.form.get("notes", "").strip()
            copy_version_id = request.form.get("copy_version_id", "").strip()
            if request.form.get("rate_source") == "current":
                # Backward-compatible endpoint for older clients; the current UI uses
                # explicit new/copy dialogs and no longer exposes this as a section.
                today = date.today().isoformat()
                rate_values = current_rate_values(api["db"](), grade_id, today)
                notes = notes or f"沿用 {today} 的当前费率"
            elif copy_version_id.isdigit():
                source = api["db"]().execute(
                    "select * from employee_rate_versions where id=? and employee_grade_id=?",
                    (int(copy_version_id), grade_id),
                ).fetchone()
                if not source:
                    raise ValueError("要复制的费率版本不存在。")
                source_items = {
                    row["rate_type"]: float(row["rate"] or 0)
                    for row in api["db"]().execute(
                        "select rate_type,rate from employee_rate_items where version_id=?",
                        (source["id"],),
                    ).fetchall()
                }
                rate_values = {key: source_items.get(key, 0) for key, _label, _unit in EMPLOYEE_RATE_TYPES}
                notes = notes or f"复制自 v{source['version_no']}"
            else:
                rate_values = {key: _float_form(key) for key, _label, _unit in EMPLOYEE_RATE_TYPES}
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
                (grade_id, next_no, effective_from, effective_to, notes, g.user["id"], api["now"](), api["now"]()),
            )
            version_id = cursor.lastrowid
            for key, _label, unit in EMPLOYEE_RATE_TYPES:
                api["db"]().execute(
                    "insert into employee_rate_items (version_id, rate_type, unit, rate) values (?, ?, ?, ?)",
                    (version_id, key, unit, rate_values[key]),
                )
            api["log_action"]("create", "employee_rate_version", version_id, f"{grade['grade_name']} v{next_no}", f"生效：{effective_from} 至 {effective_to or '长期'}")
            api["db"]().commit()
            flash("员工结算费率版本已创建。", "success")
        except ValueError as error:
            api["db"]().rollback()
            flash(str(error), "error")
        return redirect(url_for("employee_grades", grade_id=grade_id))

    @app.post("/employee-grades/<int:grade_id>/rates/<int:version_id>/delete")
    @api["login_required"]
    def delete_employee_rate_version(grade_id, version_id):
        """Delete a version while preserving already-posted payroll snapshot amounts."""
        grade, version = _load_grade_version(grade_id, version_id)
        api["db"]().execute(
            "update profit_ledger set employee_rate_version_id=null where employee_rate_version_id=?",
            (version_id,),
        )
        api["db"]().execute("delete from employee_rate_items where version_id=?", (version_id,))
        api["db"]().execute("delete from employee_rate_versions where id=?", (version_id,))
        api["log_action"](
            "delete", "employee_rate_version", version_id,
            f"{grade['grade_name']} v{version['version_no']}",
            f"删除费率版本；原生效期 {version['effective_from']} 至 {version['effective_to'] or '长期'}",
        )
        api["db"]().commit()
        flash(f"费率版本 v{version['version_no']} 已删除；已落账工资金额不受影响。", "success")
        return redirect(url_for("employee_grades", grade_id=grade_id))

    def _load_grade_version(grade_id, version_id):
        """取属于该等级的一条版本；不存在或不属于该等级一律 404。"""
        if not api["has_action_permission"]("employee_grades", "view"):
            abort(403)
        grade = api["db"]().execute("select * from employee_grades where id = ?", (grade_id,)).fetchone()
        if not grade:
            abort(404)
        version = api["db"]().execute(
            "select * from employee_rate_versions where id = ? and employee_grade_id = ?",
            (version_id, grade_id),
        ).fetchone()
        if not version:
            abort(404)
        if not api["has_action_permission"]("employee_grades", "edit"):
            abort(403)
        return grade, version

    @app.post("/employee-grades/<int:grade_id>/rates/<int:version_id>/edit")
    @api["login_required"]
    def edit_employee_rate_version(grade_id, version_id):
        """编辑已有费率版本的生效期 / 费率 / 备注。

        profit_ledger 落账时存的是当时的计算结果（cost/profit 数值），版本 id 只是
        溯源标注，所以修正版本不会改写历史工资记录；修改会进操作日志。
        """
        grade, version = _load_grade_version(grade_id, version_id)
        try:
            _ensure_version_is_current(version)
            effective_from = request.form.get("effective_from", "").strip()
            effective_to = request.form.get("effective_to", "").strip() or None
            _validate_dates(effective_from, effective_to)
            rates = {key: _float_form(key) for key, _label, _unit in EMPLOYEE_RATE_TYPES}
            effective_to = _validate_range_against_versions(
                api["db"](), "employee_rate_versions", "employee_grade_id", grade_id,
                version_id, effective_from, effective_to,
            )
            api["db"]().execute(
                "update employee_rate_versions set effective_from = ?, effective_to = ?, notes = ?, updated_at = ? where id = ?",
                (effective_from, effective_to, request.form.get("notes", "").strip(), api["now"](), version_id),
            )
            for key, _label, _unit in EMPLOYEE_RATE_TYPES:
                api["db"]().execute(
                    "update employee_rate_items set rate = ? where version_id = ? and rate_type = ?",
                    (rates[key], version_id, key),
                )
            api["log_action"](
                "update", "employee_rate_version", version_id, f"{grade['grade_name']} v{version['version_no']}",
                f"编辑：生效 {effective_from} 至 {effective_to or '长期'}",
            )
            api["db"]().commit()
            flash(f"费率版本 v{version['version_no']} 已更新。", "success")
        except ValueError as error:
            api["db"]().rollback()
            flash(str(error), "error")
        return redirect(url_for("employee_grades", grade_id=grade_id))

    @app.post("/employee-grades/<int:grade_id>/rates/<int:version_id>/state")
    @api["login_required"]
    def employee_rate_version_state(grade_id, version_id):
        """停用 / 启用费率版本：填错的版本停用后可以重建，不必带着错数据算工资。"""
        grade, version = _load_grade_version(grade_id, version_id)
        target = "inactive" if version["status"] == "active" else "active"
        try:
            _ensure_version_is_current(version)
            if target == "active":
                # 重新启用等同放回有效期表，必须先确认与其它有效版本不重叠
                _validate_range_against_versions(
                    api["db"](), "employee_rate_versions", "employee_grade_id", grade_id,
                    version_id, version["effective_from"], version["effective_to"],
                )
            api["db"]().execute(
                "update employee_rate_versions set status = ?, updated_at = ? where id = ?",
                (target, api["now"](), version_id),
            )
            action = "停用" if target == "inactive" else "启用"
            api["log_action"](
                "update", "employee_rate_version", version_id,
                f"{grade['grade_name']} v{version['version_no']}", f"{action}费率版本",
            )
            api["db"]().commit()
            flash(f"费率版本 v{version['version_no']} 已{action}。", "success")
        except ValueError as error:
            api["db"]().rollback()
            flash(str(error), "error")
        return redirect(url_for("employee_grades", grade_id=grade_id))
