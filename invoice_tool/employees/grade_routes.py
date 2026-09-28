"""Explicit registration for employee-grade management endpoints."""

from datetime import date

from flask import abort, flash, jsonify, redirect, render_template, request, url_for

from database import IntegrityError


def register_employee_grade_routes(
    app,
    *,
    db,
    now,
    login_required,
    can_manage_employee_grades,
    has_action_permission,
    employee_grade_options,
    employee_grade_rate_snapshot,
    employee_rate_labels,
    employee_rate_types,
    to_float,
    log_action,
):
    def employee_grade_usage(grade_id):
        """返回 (员工总数, 其中在职数)：等级删除/停用校验的数据依据。"""
        row = db().execute(
            """
            select count(*) as total,
                   coalesce(sum(case when is_active = 1 then 1 else 0 end), 0) as active
            from users
            where employee_grade_id = ?
            """,
            (grade_id,),
        ).fetchone()
        return int(row["total"] or 0), int(row["active"] or 0)

    def employee_grade_panel(grade):
        """单个等级的面板数据：员工分配 / 费率版本 / 当前生效费率 / 等级资料。

        等级切换是纯页面内行为（前端切面板），所以这里一次性把每个等级的数据都备好，
        不依赖逐个等级各自一个 URL —— 否则点一次等级就等于打开一个新页面（工作区里表现为新标签）。
        """
        grade_id = grade["id"]
        total, active = employee_grade_usage(grade_id)
        employees = db().execute(
            """
            select id, name, email, role, is_active, phone
            from users
            where employee_grade_id = ?
            order by is_active desc, name
            """,
            (grade_id,),
        ).fetchall()
        # 可加入本等级的员工：未分配等级的排在前面，其次是当前挂在别的等级上的
        candidates = db().execute(
            """
            select users.id, users.name, users.email, users.role, users.employee_grade_id,
                   employee_grades.grade_name as current_grade_name
            from users
            left join employee_grades on employee_grades.id = users.employee_grade_id
            where users.employee_grade_id is null or users.employee_grade_id != ?
            order by (users.employee_grade_id is not null), users.name
            """,
            (grade_id,),
        ).fetchall()
        versions = db().execute(
            "select * from employee_rate_versions where employee_grade_id = ? order by effective_from desc, version_no desc",
            (grade_id,),
        ).fetchall()
        items = db().execute(
            """
            select employee_rate_items.* from employee_rate_items
            join employee_rate_versions on employee_rate_versions.id = employee_rate_items.version_id
            where employee_rate_versions.employee_grade_id = ?
            order by employee_rate_versions.version_no desc, employee_rate_items.id
            """,
            (grade_id,),
        ).fetchall()
        item_map = {}
        for item in items:
            item_map.setdefault(item["version_id"], {})[item["rate_type"]] = item
        # 现存数据里可能仍有有效期互相重叠的版本（重叠校验上线前的遗留），直接摆到页面上
        # 让用户用「编辑」修掉 —— 编辑保存时会被重叠校验拦住，直到改到不重叠为止。
        # 取价规则：重叠期间按生效日期较晚的版本算（_version_for_day 的 order by）。
        active_versions = [v for v in versions if v["status"] == "active"]
        overlapping_versions = []
        for index, left in enumerate(active_versions):
            for right in active_versions[index + 1:]:
                left_end = left["effective_to"] or "9999-12-31"
                right_end = right["effective_to"] or "9999-12-31"
                if left["effective_from"] <= right_end and right["effective_from"] <= left_end:
                    overlapping_versions.append(
                        f"v{left['version_no']}（{left['effective_from']} ～ {left['effective_to'] or '长期'}）"
                        f"与 v{right['version_no']}（{right['effective_from']} ～ {right['effective_to'] or '长期'}）"
                    )
        snapshot = employee_grade_rate_snapshot(db(), grade_id, date.today().isoformat())
        missing = [key for key, value in snapshot["rates"].items() if value["source"] == "missing"]
        # 费率为 0 有两种来源：列里就是 0（新建等级默认值），或版本里明确写了 0。
        # 两者都不会落进 missing，但固化进版本后会明确按 0 计薪，必须让用户看见。
        zero_labels = [
            employee_rate_labels.get(key, key)
            for key, value in snapshot["rates"].items()
            if not value["rate"]
        ]
        return {
            "grade": grade,
            "total": total,
            "active": active,
            "employees": employees,
            "candidates": candidates,
            "versions": versions,
            "item_map": item_map,
            "rates": snapshot["rates"],
            "current_version": snapshot["version"],
            "missing_rates": missing,
            "zero_rate_labels": zero_labels,
            "overlapping_versions": overlapping_versions,
            "rates_all_zero": len(zero_labels) == len(snapshot["rates"]),
        }

    def render_employee_grades_page(selected_id=""):
        """员工等级工作台（ERP 风格）：左侧等级树 + 右侧「员工分配 / 费率版本 / 等级资料」页签。

        等级切换在页面内完成：左侧是按钮 + 面板显隐，不再是逐个等级的 <a href>，
        因此不会在工作区里打开新标签。
        """
        grades = employee_grade_options(include_inactive=True)
        panels = [employee_grade_panel(grade) for grade in grades]
        if not str(selected_id).isdigit() and panels:
            selected_id = str(panels[0]["grade"]["id"])
        selected = next((panel for panel in panels if str(panel["grade"]["id"]) == str(selected_id)), None)
        if selected is None and panels:
            selected = panels[0]
        unassigned = db().execute(
            "select count(*) as total from users where employee_grade_id is null"
        ).fetchone()["total"]
        return render_template(
            "employee_grades.html",
            grades=grades,
            panels=panels,
            selected=selected,
            unassigned=int(unassigned or 0),
            rate_types=employee_rate_types,
            today=date.today().isoformat(),
            can_edit=has_action_permission("employee_grades", "edit"),
            can_delete=has_action_permission("employee_grades", "delete"),
        )

    def is_member_ajax_request():
        """「员工分配」的这次提交是否要求局部刷新。

        只有带 X-Requested-With 的 XHR 才拿片段；普通表单提交（既有测试、不支持 fetch
        的环境）仍旧 302 跳转 —— 保留这条路径，页面功能不依赖前端脚本。
        """
        return request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def employee_grade_member_fragments(grade_ids, message="", category="success"):
        """「加入/移出该等级」局部刷新的回包：受影响等级的成员行 / 候选下拉 / 各计数点。

        回「受影响的两个等级」而不是只回目标等级：页面里所有等级同处一页，把员工从
        等级 A 改挂到等级 B 会同时改变 A 的成员表与候选下拉；只刷 B 的话，用户切回 A
        会看到「表格里还挂着他、下拉里又能把他加进来」的旧数据。

        只回片段、不回整页 HTML：整页替换会把 Tabulator 的镜像 shell 一起换掉
        （镜像与源表是兄弟节点），重建网格要丢列宽 / 排序 / 表内搜索词 —— 那是
        「整页白一下」之外另一笔不该付的代价。
        """
        grades = {int(grade["id"]): grade for grade in employee_grade_options(include_inactive=True)}
        can_edit = has_action_permission("employee_grades", "edit")
        panels = []
        seen = set()
        for raw_id in grade_ids:
            try:
                member_grade_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if member_grade_id in seen:
                continue
            seen.add(member_grade_id)
            grade = grades.get(member_grade_id)
            if grade is None:
                continue
            panel = employee_grade_panel(grade)
            panels.append({
                "id": member_grade_id,
                "total": panel["total"],
                "active": panel["active"],
                "headcount": f"{panel['total']} 名（在职 {panel['active']}）",
                "headline": f"{panel['total']} 名员工（在职 {panel['active']}）",
                "nav_title": f"{grade['grade_name']}{'' if grade['is_active'] else '（已停用）'} · {panel['total']} 名员工",
                "rows_html": render_template(
                    "employee_grade_member_frag.html", part="rows", panel=panel, can_edit=can_edit
                ),
                "options_html": render_template(
                    "employee_grade_member_frag.html", part="options", panel=panel
                ),
            })
        unassigned = int(db().execute(
            "select count(*) as total from users where employee_grade_id is null"
        ).fetchone()["total"] or 0)
        return jsonify({
            "ok": True,
            "message": message,
            "category": category,
            "panels": panels,
            "unassigned": unassigned,
            "unassigned_text": f"{unassigned} 名员工未分配等级",
        })

    @login_required
    def employee_grades():
        if not can_manage_employee_grades():
            abort(403)
        if request.method == "POST":
            grade_id = request.form.get("grade_id")
            grade_name = request.form.get("grade_name", "").strip()
            if not grade_name:
                flash("请填写员工等级。", "error")
                return redirect(url_for("employee_grades"))
            existing = None
            if grade_id and str(grade_id).isdigit():
                existing = db().execute(
                    "select * from employee_grades where id = ?", (int(grade_id),)
                ).fetchone()
                if existing is None:
                    flash("该员工等级不存在，可能已被删除。", "error")
                    return redirect(url_for("employee_grades"))

            def grade_number(name, absent=0, empty=None):
                """取一个数值列，**表单没提交就保留库里的现值**。

                「编辑等级」弹窗已不再维护费率字段（各时薪、里程单价、租车驾驶补贴统一到
                「费率版本」页签按生效日期维护），所以这些列不再随表单提交。若沿用
                「缺省即 0」（to_float(None) == 0）的写法，保存一次等级就会把等级静态费率
                全部清零 —— 而没有费率版本的等级（含全部历史数据）正是靠这些列兜底计算工资，
                等于静默把工资算成 0。

                absent：新增等级且表单没提交时用的值（对齐建表默认值）。
                empty：表单提交了该字段但为空时的值，默认与 absent 相同。
                """
                if name in request.form:
                    raw = str(request.form.get(name) or "").strip()
                    if raw:
                        return to_float(raw)
                    return float(absent if empty is None else empty)
                if existing is not None:
                    return to_float(existing[name], absent)
                return float(absent)

            car_method = request.form.get("car_allowance_method", "").strip()
            if car_method not in {"mileage", "hourly"}:
                car_method = existing["car_allowance_method"] if existing is not None else "mileage"
            # car_hourly_rate 历史上就是跟随交通时薪写入的（test_payment_terms 有断言钉住），保持口径不变
            transport_hourly_rate = grade_number("transport_hourly_rate")
            values = (
                grade_name,
                request.form.get("description", "").strip(),
                grade_number("base_salary"),
                max(grade_number("meal_daily_amount"), 0),
                car_method,
                max(grade_number("car_mileage_rate", absent=0.5, empty=0), 0),
                max(transport_hourly_rate, 0),
                max(grade_number("rental_driving_hourly_rate", absent=15), 0),
                grade_number("standard_hourly_rate"),
                transport_hourly_rate,
                grade_number("overtime_hourly_rate"),
                grade_number("holiday_hourly_rate"),
            )
            try:
                if grade_id and str(grade_id).isdigit():
                    # 启用/停用走列表页的专用按钮（带在职员工校验），编辑只改资料不改状态
                    db().execute(
                        """
                        update employee_grades
                        set grade_name = ?, description = ?, base_salary = ?, meal_daily_amount = ?,
                            car_allowance_method = ?, car_mileage_rate = ?, car_hourly_rate = ?, rental_driving_hourly_rate = ?, standard_hourly_rate = ?, transport_hourly_rate = ?,
                            overtime_hourly_rate = ?, holiday_hourly_rate = ?
                        where id = ?
                        """,
                        (*values, int(grade_id)),
                    )
                    log_action("update", "employee_grade", int(grade_id), grade_name, "修改员工等级")
                else:
                    cursor = db().execute(
                        """
                        insert into employee_grades (
                            grade_name, description, base_salary, meal_daily_amount, car_allowance_method,
                            car_mileage_rate, car_hourly_rate, rental_driving_hourly_rate, standard_hourly_rate, transport_hourly_rate,
                            overtime_hourly_rate, holiday_hourly_rate, is_active, created_at
                        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                        """,
                        (*values, now()),
                    )
                    log_action("create", "employee_grade", cursor.lastrowid, grade_name, "新增员工等级")
                db().commit()
                flash("员工等级已保存。", "success")
            except IntegrityError:
                db().rollback()
                flash("员工等级名称已存在。", "error")
            return redirect(url_for("employee_grades", grade_id=grade_id or ""))
        # 必须把 ?grade_id 传进去：等级切换是用 history.replaceState 写地址栏的（不导航），
        # 页面刷新、以及 erp-report.js 的「局部刷新」（fetch(location.href)）都要靠它还原选中等级；
        # 上面各 POST 分支也都是 redirect(..., grade_id=...) 回到原等级。
        return render_employee_grades_page(request.args.get("grade_id", ""))

    @login_required
    def set_employee_grade_state(grade_id):
        if not has_action_permission("employee_grades", "edit"):
            abort(403)
        grade = db().execute("select * from employee_grades where id = ?", (grade_id,)).fetchone()
        if not grade:
            abort(404)
        target_active = request.form.get("is_active", "1") == "1"
        if grade["is_active"] == target_active:
            return redirect(url_for("employee_grades", grade_id=grade_id))
        if target_active:
            db().execute("update employee_grades set is_active = 1 where id = ?", (grade_id,))
            log_action("update", "employee_grade", grade_id, grade["grade_name"], "启用员工等级")
            db().commit()
            flash("员工等级已启用。", "success")
        else:
            # 停用校验：只要还有在职员工使用该等级，就不能停用（员工全部停用后才允许）
            total, active = employee_grade_usage(grade_id)
            if active:
                flash(f"该等级仍有 {active} 名在职员工使用，不能停用；请先在员工管理中停用这些员工或调整其等级。", "error")
                return redirect(url_for("employee_grades", grade_id=grade_id))
            db().execute("update employee_grades set is_active = 0 where id = ?", (grade_id,))
            log_action("update", "employee_grade", grade_id, grade["grade_name"], f"停用员工等级（{total} 名员工均已停用）")
            db().commit()
            flash("员工等级已停用。", "success")
        return redirect(url_for("employee_grades", grade_id=grade_id))

    @login_required
    def delete_employee_grade(grade_id):
        if not has_action_permission("employee_grades", "delete"):
            abort(403)
        grade = db().execute("select * from employee_grades where id = ?", (grade_id,)).fetchone()
        if not grade:
            abort(404)
        total, active = employee_grade_usage(grade_id)
        if total:
            detail = f"其中 {active} 名在职" if active else "对应员工均已停用"
            flash(f"该等级仍被 {total} 名员工使用（{detail}），不能删除；请先把这些员工调整到其它等级。", "error")
            return redirect(url_for("employee_grades", grade_id=grade_id))
        # 显式清理费率版本与明细，不依赖外键级联。
        db().execute(
            "delete from employee_rate_items where version_id in (select id from employee_rate_versions where employee_grade_id = ?)",
            (grade_id,),
        )
        db().execute("delete from employee_rate_versions where employee_grade_id = ?", (grade_id,))
        db().execute("delete from employee_grades where id = ?", (grade_id,))
        log_action("delete", "employee_grade", grade_id, grade["grade_name"], "删除员工等级（含费率版本）")
        db().commit()
        flash("员工等级已删除。", "success")
        return redirect(url_for("employee_grades"))

    @login_required
    def update_employee_grade_members(grade_id):
        """调整「该等级下的员工分配」：加入 / 移出。只改 users.employee_grade_id，不触碰其它资料。

        两个出口：XHR 提交只回局部刷新片段（页面不重载，见 employee_grade_member_fragments），
        普通提交仍是 flash + 302 回该等级 —— 后者是既有契约（Location 要带 grade_id）。
        """
        if not has_action_permission("employee_grades", "edit"):
            abort(403)
        grade = db().execute("select * from employee_grades where id = ?", (grade_id,)).fetchone()
        if not grade:
            abort(404)

        def finish(ok, message, affected=()):
            if is_member_ajax_request():
                if not ok:
                    return jsonify({"ok": False, "message": message, "category": "error"})
                return employee_grade_member_fragments(affected, message=message, category="success")
            flash(message, "success" if ok else "error")
            return redirect(url_for("employee_grades", grade_id=grade_id))

        action = request.form.get("action", "add")
        raw_user_id = (request.form.get("user_id") or "").strip()
        if not raw_user_id.isdigit():
            return finish(False, "请先选择一名员工。")
        user_id = int(raw_user_id)
        user = db().execute("select * from users where id = ?", (user_id,)).fetchone()
        if not user:
            return finish(False, "员工不存在。")
        if action == "remove":
            if user["employee_grade_id"] != grade_id:
                return finish(False, f"{user['name']} 当前不属于「{grade['grade_name']}」，无需移出。")
            db().execute("update users set employee_grade_id = null where id = ?", (user_id,))
            log_action("update", "employee_grade", grade_id, grade["grade_name"], f"将 {user['name']} 移出该等级")
            db().commit()
            return finish(True, f"已将 {user['name']} 移出「{grade['grade_name']}」。", [grade_id])

        if user["employee_grade_id"] == grade_id:
            return finish(False, f"{user['name']} 已经在「{grade['grade_name']}」里。")
        previous_grade_id = user["employee_grade_id"]
        previous = db().execute(
            "select grade_name from employee_grades where id = ?", (previous_grade_id,)
        ).fetchone() if previous_grade_id else None
        db().execute("update users set employee_grade_id = ? where id = ?", (grade_id, user_id))
        detail = f"将 {user['name']} 从「{previous['grade_name']}」调整到该等级" if previous else f"将 {user['name']} 加入该等级"
        log_action("update", "employee_grade", grade_id, grade["grade_name"], detail)
        db().commit()
        # 改挂过来的员工同时改变了原等级的成员表与候选下拉，两个等级都要回片段
        return finish(True, f"已将 {user['name']} 加入「{grade['grade_name']}」。",
                      [grade_id, previous_grade_id])

    app.add_url_rule('/employee-grades', endpoint='employee_grades', view_func=employee_grades, methods=['GET', 'POST'])
    app.add_url_rule('/employee-grades/<int:grade_id>/state', endpoint='set_employee_grade_state', view_func=set_employee_grade_state, methods=['POST'])
    app.add_url_rule('/employee-grades/<int:grade_id>/delete', endpoint='delete_employee_grade', view_func=delete_employee_grade, methods=['POST'])
    app.add_url_rule('/employee-grades/<int:grade_id>/members', endpoint='update_employee_grade_members', view_func=update_employee_grade_members, methods=['POST'])
    return {
        "employee_grade_usage": employee_grade_usage,
        "employee_grade_panel": employee_grade_panel,
        "render_employee_grades_page": render_employee_grades_page,
        "is_member_ajax_request": is_member_ajax_request,
        "employee_grade_member_fragments": employee_grade_member_fragments,
        "employee_grades": employee_grades,
        "set_employee_grade_state": set_employee_grade_state,
        "delete_employee_grade": delete_employee_grade,
        "update_employee_grade_members": update_employee_grade_members,
    }
