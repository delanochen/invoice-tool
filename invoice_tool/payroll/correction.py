"""Payroll Cycle Correction —— 冻结保全纠偏的统一读层（Option C）。

为什么存在(O)：
    2026-07-06 起每 14 天是唯一权威工资网格，历史 SL 的周期标签全部 off-grid。
    纠偏不能重算历史金额（`historical_replay_uncertain`），也不能
    UPDATE employee_payment_components.payment_order_id —— payment_order_id
    是「当时属于哪张 SL」的历史事实，改写等于伪造 audit trail。

于是 canonical 归属由 `payroll_component_correction_allocations` 单独表达：

    Original Parent (frozen component)
        vs
    Effective Canonical Allocation (allocation row)

铁律（改这个文件前必须重读一遍）：
  * employee_payment_components 一行都不改：id / payment_order_id / amount /
    service_date / source identity 全部保持生成当时的值；本模块**永不**
    UPDATE / DELETE 该表，也**不**使用它的 superseded_at 表达工资侧作废
    （superseded_at 继续只服务 expense 侧）。
  * `duplicate_superseded` = 该 frozen 行不再计入任何 effective 金额；
    其余 disposition 金额原样保留，归属到
    `coalesce(canonical_payment_order_id, payment_order_id)`。
  * 只有 `payroll_correction_groups.status = 'applied'` 的 group 影响计算；
    draft / rolled_back 的 group 是纯元数据，对金额零影响。
  * **空表即旧行为**：没有任何 allocation 行时，本层产出的结果与改造前逐分一致，
    这也是「先上迁移、后做纠偏」能安全分开的前提。

Decision 4 要求：Annual Summary / CPA Workpaper / Payment Statement /
Batch validation / Employee Ledger / Payroll totals / integrity checks 全部调用
本模块，任何页面都不得自己解释 allocation 表。
"""
from __future__ import annotations

import re
from decimal import Decimal

# ---------------------------------------------------------------------------
# Component allocation disposition
# ---------------------------------------------------------------------------
DISPOSITION_RETAINED = "retained"
DISPOSITION_DUPLICATE = "duplicate_superseded"
DISPOSITION_CARRY_FORWARD = "carry_forward"
DISPOSITION_PRE_CANONICAL = "pre_canonical_legacy"

DISPOSITIONS = (
    DISPOSITION_RETAINED,
    DISPOSITION_DUPLICATE,
    DISPOSITION_CARRY_FORWARD,
    DISPOSITION_PRE_CANONICAL,
)
# disposition 会以字符串字面量内联进 CTE（`?` 占位在 WITH 子句里会让调用方的
# 参数顺序极易写错），所以取值必须是纯 [a-z_]：这是那条内联的护栏。
assert all(re.fullmatch(r"[a-z_]+", item) for item in DISPOSITIONS), (
    "disposition 只能由 [a-z_] 组成，禁止内联进 SQL：%r" % (DISPOSITIONS,)
)
#: 不再计入 effective payroll 的 disposition（目前的唯一成员）。
EXCLUDED_DISPOSITIONS = frozenset({DISPOSITION_DUPLICATE})
#: 「这笔钱没有新的应付」—— 保持原 fall back 到原始 SL。
#: 已付款纠偏（李力 PB-2610-0001）与尚未生成的 carry_forward 都走这条路。
UNALLOCATED_DISPOSITIONS = frozenset({DISPOSITION_CARRY_FORWARD, DISPOSITION_PRE_CANONICAL})

DISPOSITION_LABELS = {
    DISPOSITION_RETAINED: "Retained (re-attributed)",
    DISPOSITION_DUPLICATE: "Duplicate (superseded)",
    DISPOSITION_CARRY_FORWARD: "Carry forward to open period",
    DISPOSITION_PRE_CANONICAL: "Pre-canonical legacy",
}

# ---------------------------------------------------------------------------
# Payment-order correction lifecycle
# ---------------------------------------------------------------------------
STATUS_NONE = "none"
STATUS_CORRECTION_PENDING = "correction_pending"
STATUS_PARTIALLY_SUPERSEDED = "partially_superseded"
STATUS_SUPERSEDED = "superseded"
STATUS_REPLACEMENT = "replacement"
STATUS_PERIOD_LABEL_CORRECTED = "period_label_corrected"

CORRECTION_STATUSES = (
    STATUS_NONE,
    STATUS_CORRECTION_PENDING,
    STATUS_PARTIALLY_SUPERSEDED,
    STATUS_SUPERSEDED,
    STATUS_REPLACEMENT,
    STATUS_PERIOD_LABEL_CORRECTED,
)
#: 已不再代表应付义务：不能进批次、不能进往来账应付、不能进年度 effective 合计。
NON_PAYABLE_CORRECTION_STATUSES = frozenset(
    {STATUS_CORRECTION_PENDING, STATUS_PARTIALLY_SUPERSEDED, STATUS_SUPERSEDED}
)
#: 不允许再被合并发放的历史单据（含已纠偏标签的已付款单）。
BATCH_BLOCKED_CORRECTION_STATUSES = NON_PAYABLE_CORRECTION_STATUSES | {
    STATUS_PERIOD_LABEL_CORRECTED
}

CORRECTION_STATUS_LABELS = {
    STATUS_NONE: "Original",
    STATUS_CORRECTION_PENDING: "Correction pending (HOLD)",
    STATUS_PARTIALLY_SUPERSEDED: "Partially superseded",
    STATUS_SUPERSEDED: "Superseded by Payroll Cycle Correction",
    STATUS_REPLACEMENT: "Canonical replacement",
    STATUS_PERIOD_LABEL_CORRECTED: "Period label corrected (amount unchanged)",
}

#: 组件业务身份：判定「同一份工作是否被两张单重复计入」的唯一键。
IDENTITY_COLUMNS = (
    "c.employee_id",
    "c.service_date",
    "c.component_code",
    "c.work_order_id",
)


def _field(row, key, default=None):
    """同时适配 database.Row 与 dict —— Row 不支持 .get()，缺列抛 KeyError 子类。"""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError, ValueError):
        return default
    return default if value is None else value


def build_payroll_correction_services(api):
    """构造工资周期纠偏的统一读层（依赖一律在调用时从 api 解析）。"""
    from .tax import money

    def _applied_allocations_cte():
        """生效纠偏组里每个组件最新一条 allocation。

        一个组件在同一 group 内只允许一行（表上有 UNIQUE）；不同 group 都写同一
        组件属于 misuse，这里按 id desc 取最后一条，保证读结果是确定值而不是
        随机重复行。
        """
        return """
            correction_alloc as (
                select distinct on (a.component_id)
                       a.id as allocation_id, a.component_id, a.correction_group_id,
                       a.disposition, a.canonical_payment_order_id,
                       a.canonical_period_start, a.canonical_period_end,
                       a.keeper_component_id, a.reason as allocation_reason,
                       a.created_at as allocation_created_at
                from payroll_component_correction_allocations a
                join payroll_correction_groups g on g.id = a.correction_group_id
                where g.status = 'applied'
                order by a.component_id, a.id desc
            )
        """

    def correction_component_cte(alias="c"):
        """可嵌进既有 WITH 列表的两个 CTE 定义。

        用法：
            with {_latest_review_cte()},
            {api["correction_component_cte"]()}
            base as (select ... from effective_components c ...)

        `duplicate_superseded` 作为字符串字面量内联：它是本模块的常量，且
        CTE 里带 `?` 占位会让调用方的参数顺序（业务参数排在 CTE 之后）极易写错，
        下面这行 assert 就是这条内联的护栏。
        """
        del alias
        return _applied_allocations_cte() + """,
            effective_components as (
                select c.*,
                       coalesce(ca.canonical_payment_order_id, c.payment_order_id)
                           as effective_payment_order_id,
                       coalesce(ca.disposition, 'legacy') as allocation_disposition,
                       ca.allocation_id,
                       ca.correction_group_id as allocation_group_id,
                       ca.canonical_period_start as allocation_period_start,
                       ca.canonical_period_end as allocation_period_end,
                       ca.keeper_component_id as allocation_keeper_component_id,
                       ca.allocation_reason
                from employee_payment_components c
                left join correction_alloc ca on ca.component_id = c.id
                where c.superseded_at is null
                  and coalesce(ca.disposition, 'legacy') <> '{dup}'
            )
        """.format(dup=DISPOSITION_DUPLICATE)

    def standalone_component_cte():
        """独立 WITH 前缀（自带 with 关键字），供单条查询直接使用。"""
        return f"""
            with {correction_component_cte()}
        """

    def excluded_component_clause(component_alias="c"):
        """「该组件是否还在 effective payroll 里」的布尔条件（不入时才为假）。

        给那些不方便整体替换 FROM 的老 SQL 用（例如复核队列），避免在每一处
        重新发明 allocation 语义。注意：MySQL 语义上的 NOT EXISTS 在这里依赖
        distinct on，因此多 group 写同一组件时仍只算一次。
        """
        return f"""
            not exists (
                select 1 from payroll_component_correction_allocations a
                join payroll_correction_groups g on g.id = a.correction_group_id
                where a.component_id = {component_alias}.id
                  and g.status = ?
                  and a.disposition = ?
            )
        """

    def excluded_component_params():
        return ["applied", DISPOSITION_DUPLICATE]

    def component_correction_state(component_ids):
        """批量取组件的allocation 状态 {component_id: {...}}（无 allocation 返回 {}）。"""
        ids = [int(cid) for cid in component_ids if cid]
        if not ids:
            return {}
        marks = ",".join(["?"] * len(ids))
        rows = api["db"]().execute(
            f"""
            select a.component_id, a.disposition, a.canonical_payment_order_id,
                   a.canonical_period_start, a.canonical_period_end,
                   a.keeper_component_id, a.reason, g.correction_code, g.status as group_status
            from payroll_component_correction_allocations a
            join payroll_correction_groups g on g.id = a.correction_group_id
            where a.component_id in ({marks})
            """,
            ids,
        ).fetchall()
        return {int(row["component_id"]): dict(row) for row in rows}

    def payable_order_clause(order_alias="p"):
        """仍然代表应付义务的付款单条件（往来账 / 批次 / payable 合计通用）。"""
        marks = ",".join(["?"] * len(NON_PAYABLE_CORRECTION_STATUSES))
        return f"coalesce({order_alias}.correction_status, ?) not in ({marks})"

    def payable_order_params():
        return [STATUS_NONE, *sorted(NON_PAYABLE_CORRECTION_STATUSES)]

    def payable_totals(filters=None):
        """correction 之后仍然存在的应付合计（往来账 Gate 用）。

        Before/After 对账口径：
            before = 所有工资类付款单 net 之和
            after  = 排除 superseded / correction_pending / partially_superseded 之后
        要求 after == canonical 重算结果，绝不允许「旧应付 + 新应付」双份存在。
        """
        # 员工往来账默认展示全部员工，此时调用方不带任何筛选条件。
        # 将 None 归一化为空字典，避免页面首次打开时在 filters.get() 处报错。
        filters = filters or {}
        # 由 SQL 侧的 CASE WHEN 换成 Python 侧聚合：美元赔付的计算不需要在 SQL 里重复
        # 四次占位符（每重复一次就多一份参数），累积 bijection 出错的机会。
        clauses = ["1 = 1"]
        params = []
        if filters.get("employee_id"):
            clauses.append("p.employee_id = ?")
            params.append(int(filters["employee_id"]))
        if filters.get("payment_type"):
            clauses.append("p.payment_type = ?")
            params.append(str(filters["payment_type"]))
        rows = api["db"]().execute(
            "select p.id, p.gross_amount, p.net_amount, p.correction_status"
            f" from employee_payment_orders p where {' and '.join(clauses)}",
            params,
        ).fetchall()
        excluded = frozenset(NON_PAYABLE_CORRECTION_STATUSES)
        gross_all = money(sum((Decimal(str(r["gross_amount"])) for r in rows), Decimal("0")))
        net_all = money(sum((Decimal(str(r["net_amount"])) for r in rows), Decimal("0")))
        effective_gross = Decimal("0")
        effective_net = Decimal("0")
        excluded_gross = Decimal("0")
        excluded_count = 0
        for row in rows:
            # Row 不支持 .get()（INVOICE 约定），缺列会抛 MissingRowColumnError。
            status = str(row["correction_status"] or STATUS_NONE)
            if status in excluded:
                excluded_count += 1
                excluded_gross += Decimal(str(row["gross_amount"]))
            else:
                effective_gross += Decimal(str(row["gross_amount"]))
                effective_net += Decimal(str(row["net_amount"]))
        return {
            "payable_count": len(rows),
            "payable_gross": gross_all,
            "payable_net": net_all,
            "effective_gross": money(effective_gross),
            "effective_net": money(effective_net),
            "excluded_count": excluded_count,
            "excluded_gross": money(excluded_gross),
        }

    def payment_batch_block_reason(order):
        """付款单能否进发放批次；不能则返回原因文案，可以则返回 None。"""
        status = str(_field(order, "correction_status") or STATUS_NONE)
        payment_number = _field(order, "payment_number", "")
        if status in BATCH_BLOCKED_CORRECTION_STATUSES:
            label = CORRECTION_STATUS_LABELS.get(status, status)
            if status == STATUS_PERIOD_LABEL_CORRECTED:
                return (f"{payment_number}：单据只做周期标签纠偏，"
                        f"金额已在原批次支付，不得再次合并发放（{label}）。")
            return (f"{payment_number}：已被工资周期纠偏作废，"
                    f"不得进入发放批次（{label}）。")
        return None

    def correction_banner(order):
        """付款单详情页要显示的纠偏提示（没有纠偏则返回 None）。"""
        status = str(_field(order, "correction_status") or STATUS_NONE)
        if status in (STATUS_NONE, STATUS_REPLACEMENT):
            return None
        replacement = None
        if _field(order, "superseded_by_id"):
            replacement = api["db"]().execute(
                "select id, payment_number, source_number from employee_payment_orders"
                " where id = ?",
                (int(order["superseded_by_id"]),),
            ).fetchone()
        group = None
        if _field(order, "correction_group_id"):
            group = api["db"]().execute(
                "select correction_code, policy_version, canonical_cycle_start, status"
                " from payroll_correction_groups where id = ?",
                (int(order["correction_group_id"]),),
            ).fetchone()
        return {
            "status": status,
            "status_label": CORRECTION_STATUS_LABELS.get(status, status),
            "reason": _field(order, "correction_reason") or "",
            "replacement": dict(replacement) if replacement else None,
            "group": dict(group) if group else None,
        }

    def applied_correction_group(correction_code=None):
        """当前处于 applied 的纠偏组（没有则返回 None）。

        draft / rolled_back 的 group 一律不返回 —— 金额世界里只有 applied 存在。
        """
        clauses = ["status = ?"]
        params = ["applied"]
        if correction_code:
            clauses.append("correction_code = ?")
            params.append(str(correction_code))
        row = api["db"]().execute(
            "select * from payroll_correction_groups"
            f" where {' and '.join(clauses)} order by id desc limit 1",
            params,
        ).fetchone()
        return dict(row) if row else None

    def choose_duplicate_keeper(rows):
        """同一业务 identity 的重复实例里选 keeper（Decision 1 + §11）。

        优先级（全部 deterministic，随机 catalog order 不影响结果）：
          1. amount == ROUND_HALF_UP(Decimal(quantity) x Decimal(unit_rate))
             —— Joshua 2.25 x 52.5 = 118.125 -> $118.13 就是靠这条定案；
          2. daily_report_id 非空（source identity 完整）；
          3. created_at 早；
          4. id 小。
        返回 (keeper, duplicates)；duplicates 里的行只会被写成
        duplicate_superseded，**绝不**出现「两份都作废再生成第三份」。
        """
        rows = list(rows)
        if len(rows) < 2:
            return (rows[0] if rows else None), []

        def canonical(row):
            quantity = row["quantity"]
            unit_rate = row["unit_rate"]
            if quantity is None or unit_rate is None:
                return None
            return money(Decimal(str(quantity)) * Decimal(str(unit_rate)))

        def rank(row):
            expected = canonical(row)
            matches_rule = 0 if expected is not None and expected == money(row["amount"]) else 1
            return (
                matches_rule,
                0 if row["daily_report_id"] is not None else 1,
                str(row["created_at"] or ""),
                int(row["id"]),
            )

        ordered = sorted(rows, key=rank)
        return ordered[0], ordered[1:]

    def attach_carry_forward(payment_order_id, component_ids, reason=""):
        """把 carry-forward 冻结组件纳入这张 canonical SL 的 payable。

        只写 allocation 行的 canonical_payment_order_id；
        employee_payment_components 一行都不动（Decision 5 / 8）。
        已经挂到某张 SL 的行不会被改（canonical_payment_order_id is null 条件）。
        """
        ids = sorted({int(cid) for cid in component_ids if cid})
        if not ids:
            return 0
        marks = ",".join(["?"] * len(ids))
        cursor = api["db"]().execute(
            f"""
            update payroll_component_correction_allocations
               set canonical_payment_order_id = ?,
                   reason = case when ? = '' then reason else ? end
             where component_id in ({marks})
               and disposition = ?
               and canonical_payment_order_id is null
            """,
            [int(payment_order_id), str(reason or ""), str(reason or ""),
             *ids, DISPOSITION_CARRY_FORWARD],
        )
        return int(getattr(cursor, "rowcount", 0) or 0)

    def flag_carry_forward_components(worker_id, components, period_start, period_end):
        """在生成的组件列表上标出「这段周期里已经存在的 carry-forward」行。

        只打 `correction_pre_existing` 标记，**不删行**：被标记的行继续参与
        sum(components) == gross 的闭合校验，只是不重复 INSERT —— 它们的钱由
        已有的冻结组件（稍后通过 allocation 纳入本单）代表。
        返回 carry-forward 行列表（空列表代表这段没有历史遗留）。
        """
        carry = carry_forward_identities(worker_id, period_start, period_end)
        if not carry:
            return carry
        known = {item["identity"] for item in carry}
        for line in components:
            work_order = line.get("work_order_id")
            identity = (
                int(worker_id), str(line.get("service_date") or ""),
                str(line.get("component_code") or ""),
                int(work_order) if work_order else None,
            )
            if identity in known:
                line["correction_pre_existing"] = True
        return carry

    def carry_forward_identities(employee_id=None, period_start=None, period_end=None,
                                 only_unallocated=True):
        """carry_forward 组件的业务身份集合 —— P7 生成时必须排除，防止二次生成。

        only_unallocated=True 时只返回还没挂到某个 canonical SL 的行：一旦 P7
        工资单已经生成并写好了 canonical_payment_order_id，这些组件已经被计入，
        不能再排除（否则 P7 重新算时会少了这部分金额）。
        """
        clauses = ["g.status = ?", "a.disposition = ?"]
        params = ["applied", DISPOSITION_CARRY_FORWARD]
        if only_unallocated:
            clauses.append("a.canonical_payment_order_id is null")
        if employee_id:
            clauses.append("c.employee_id = ?")
            params.append(int(employee_id))
        if period_start and period_end:
            clauses.append("c.service_date >= ? and c.service_date <= ?")
            params.extend([str(period_start), str(period_end)])
        rows = api["db"]().execute(
            f"""
            select distinct {', '.join(IDENTITY_COLUMNS)}, c.id as component_id,
                   c.amount, c.payment_order_id
            from payroll_component_correction_allocations a
            join payroll_correction_groups g on g.id = a.correction_group_id
            join employee_payment_components c on c.id = a.component_id
            where {' and '.join(clauses)}
            """,
            params,
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["identity"] = (
                int(row["employee_id"]), str(row["service_date"]),
                str(row["component_code"]),
                int(row["work_order_id"]) if row["work_order_id"] is not None else None,
            )
            out.append(item)
        return out

    def carry_forward_amount(employee_id=None, period_start=None, period_end=None):
        rows = carry_forward_identities(employee_id, period_start, period_end)
        total = money(sum((Decimal(str(row["amount"])) for row in rows), Decimal("0")))
        return {"count": len(rows), "amount": total, "rows": rows}

    def _replacement_component_total(order_id):
        row = api["db"]().execute(
            f"""
            {standalone_component_cte()}
            select count(*) as n, coalesce(sum(amount), 0) as total
            from effective_components c
            where c.effective_payment_order_id = ?
            """,
            [int(order_id)],
        ).fetchone()
        return int(row["n"]), money(row["total"])

    def replacement_closure_report(order_ids=None):
        """逐张 replacement SL 校验：effective 组件合计 == gross（一分不差）。

        返回值里 closure_ok=False 时不许签发 Statement —— 与现有
        Payment Statement 的 Data integrity error 语义保持一致。
        """
        clauses = ["correction_status = ?"]
        params = [STATUS_REPLACEMENT]
        if order_ids:
            ids = [int(i) for i in order_ids]
            clauses.append(f"id in ({','.join(['?'] * len(ids))})")
            params.extend(ids)
        orders = api["db"]().execute(
            "select id, payment_number, employee_id, gross_amount, net_amount, source_number"
            f" from employee_payment_orders where {' and '.join(clauses)} order by id",
            params,
        ).fetchall()
        rows = []
        for order in orders:
            count, total = _replacement_component_total(int(order["id"]))
            gross = money(order["gross_amount"])
            rows.append({
                "payment_order_id": int(order["id"]),
                "payment_number": order["payment_number"],
                "employee_id": int(order["employee_id"]),
                "source_number": order["source_number"],
                "component_count": count,
                "component_total": total,
                "gross_amount": gross,
                "closure_ok": count > 0 and total == gross,
            })
        ok = bool(rows) and all(row["closure_ok"] for row in rows)
        return {
            "rows": rows,
            "closure_ok": ok,
            "count": len(rows),
            "component_total": money(sum((r["component_total"] for r in rows), Decimal("0"))),
            "gross_total": money(sum((r["gross_amount"] for r in rows), Decimal("0"))),
        }

    def integrity_check(expected_payable=None):
        """纠偏上线 Gate 的完整自检（只读）。

        1. 每张 replacement SL：effective 组件合计 == gross；
        2. 财务Double-payable 检查：effective payable == expected_payable（若给定）；
        3. duplicate_superseded 的 keeper 必须存在且在同一 group 内登记为 retained；
        4. 不允许两只 applied group 同时登记同一组件（跨组重复会让读层失真）。
        """
        issues = []
        closure = replacement_closure_report()
        for row in closure["rows"]:
            if not row["closure_ok"]:
                issues.append(
                    f"replacement {row['payment_number']} 组件合计 "
                    f"{row['component_total']} != gross {row['gross_amount']}"
                )
        keeper = api["db"]().execute(
            """
            select count(*) as n from payroll_component_correction_allocations dup
            join payroll_correction_groups g on g.id = dup.correction_group_id
            where g.status = ? and dup.disposition = ?
              and not exists (
                  select 1 from payroll_component_correction_allocations k
                  where k.correction_group_id = dup.correction_group_id
                    and k.component_id = dup.keeper_component_id
                    and k.disposition = ?
              )
            """,
            ["applied", DISPOSITION_DUPLICATE, DISPOSITION_RETAINED],
        ).fetchone()
        orphan_duplicates = int(keeper["n"])
        if orphan_duplicates:
            issues.append(f"{orphan_duplicates} 条 duplicate_superseded 的 keeper 不是 retained")
        cross = api["db"]().execute(
            """
            select count(*) as n from (
                select component_id from payroll_component_correction_allocations a
                join payroll_correction_groups g on g.id = a.correction_group_id
                where g.status = ?
                group by component_id having count(*) > 1
            ) x
            """,
            ["applied"],
        ).fetchone()
        cross_group = int(cross["n"])
        if cross_group:
            issues.append(f"{cross_group} 个组件被多个 applied group 重复登记")
        payable = payable_totals({"payment_type": "salary"})
        expected = None if expected_payable is None else money(expected_payable)
        payable_ok = expected is None or payable["effective_gross"] == expected
        if not payable_ok:
            issues.append(
                f"effective salary payable {payable['effective_gross']} "
                f"!= expected {expected}")
        return {
            "issues": issues,
            "ok": not issues,
            "replacement_closure": closure,
            "payable": payable,
            "expected_payable": expected,
            "payable_ok": payable_ok,
            "orphan_duplicates": orphan_duplicates,
            "cross_group_allocations": cross_group,
        }

    return {
        # Decision 4：统一层的全部出口，任何页面不得自己解释 allocation 表。
        "correction_component_cte": correction_component_cte,
        "standalone_component_cte": standalone_component_cte,
        "choose_duplicate_keeper": choose_duplicate_keeper,
        "excluded_component_clause": excluded_component_clause,
        "excluded_component_params": excluded_component_params,
        "component_correction_state": component_correction_state,
        "payable_order_clause": payable_order_clause,
        "payable_order_params": payable_order_params,
        "payable_totals": payable_totals,
        "payment_batch_block_reason": payment_batch_block_reason,
        "correction_banner": correction_banner,
        "applied_correction_group": applied_correction_group,
        "attach_carry_forward": attach_carry_forward,
        "carry_forward_identities": carry_forward_identities,
        "flag_carry_forward_components": flag_carry_forward_components,
        "carry_forward_amount": carry_forward_amount,
        "replacement_closure_report": replacement_closure_report,
        "payroll_correction_integrity_check": integrity_check,
        # 常量也要导出：调用方不许再硬编码字符串。
        "correction_dispositions": DISPOSITIONS,
        "excluded_dispositions": EXCLUDED_DISPOSITIONS,
        "correction_statuses": CORRECTION_STATUSES,
        "non_payable_correction_statuses": NON_PAYABLE_CORRECTION_STATUSES,
        "batch_blocked_correction_statuses": BATCH_BLOCKED_CORRECTION_STATUSES,
        "correction_status_labels": CORRECTION_STATUS_LABELS,
        "correction_disposition_labels": DISPOSITION_LABELS,
    }
