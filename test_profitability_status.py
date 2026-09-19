"""项目利润三状态判定回归测试（v0.1.249）。

业务规则（2026-09-17 用户确认）：
- 工单已完成（service_orders.status = 'closed'）→ 客户已确认（client_confirmed）
- 工单进行中但已生成工单结算单（customer_reimbursements）或发票（invoices，非 void）→ 结算中（settlement_in_progress）
- 工单未生成结算单/发票 → 预计（estimated）

旧逻辑按结算单审核状态判定（approved → 客户已确认），与本业务规则不符：
甲方确认金额后我们才把工单改为"已完成"，结算单本身被审批通过不代表甲方确认。
"""

import sqlite3

import pytest

from profitability import _expense_lines, _labor_lines, _line_status


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        create table service_orders (
            id integer primary key autoincrement,
            order_number text not null,
            client_name text not null,
            contract_id integer,
            status text not null default 'open'
        );
        create table service_reports (
            id integer primary key autoincrement,
            service_order_id integer not null,
            report_date text,
            actual_work_date text,
            mileage_billing_method text default 'per_person',
            total_service_hours real default 0
        );
        create table service_report_workers (
            id integer primary key autoincrement,
            report_id integer not null,
            user_id integer not null,
            travel_mode text,
            driving_miles real default 0,
            travel_hours real default 0,
            public_transport_hours real default 0
        );
        create table users (id integer primary key, name text, employee_grade_id integer);
        create table employee_grades (id integer primary key, name text);
        create table customer_reimbursements (
            id integer primary key autoincrement,
            service_order_id integer not null,
            status text not null default 'draft',
            created_at text,
            lodging_person_nights integer not null default 0,
            lodging_cap_rate_snapshot real not null default 0
        );
        create table invoices (
            id integer primary key autoincrement,
            service_order_id integer,
            status text not null default 'submitted'
        );
        create table expenses (
            id integer primary key autoincrement,
            expense_date text,
            service_order_id integer,
            beneficiary_id integer,
            created_by integer,
            status text not null default 'approved'
        );
        create table expense_items (
            id integer primary key autoincrement,
            expense_id integer not null,
            amount real not null default 0,
            description text,
            project text,
            project_id integer,
            fuel_vehicle_type text,
            sort_order integer default 0
        );
        create table customer_reimbursement_expense_links (
            id integer primary key autoincrement,
            customer_reimbursement_id integer not null,
            expense_item_id integer not null,
            amount_snapshot real not null default 0
        );
        create table projects (id integer primary key, name text);
        """
    )
    return conn


def make_api(conn):
    return {
        "db": lambda: conn,
        "customer_reimbursement_rates": lambda: {"标准工时": 50},
        "lodging_reimbursement_limit": lambda: 0,
        "report_actual_date": lambda report: (report["actual_work_date"] or report["report_date"])[:10],
        "report_duration_hours": lambda report, worker_count: float(report["total_service_hours"] or 0) / max(worker_count, 1),
        "is_us_weekend_or_holiday": lambda parsed: False,
        "customer_reimbursement_expense_field": lambda project, fuel: "fuel",
        "normalized_project_name": lambda name: str(name or "").strip(),
    }


@pytest.fixture()
def seeded():
    conn = make_db()
    api = make_api(conn)
    conn.executemany(
        "insert into service_orders (id, order_number, client_name, status) values (?, ?, ?, ?)",
        [
            (1, "WO-001", "客户甲", "open"),    # 无结算无发票 → 预计
            (2, "WO-002", "客户乙", "open"),    # 有 draft 结算 → 结算中
            (3, "WO-003", "客户丙", "open"),    # 只有发票 → 结算中
            (4, "WO-004", "客户丁", "closed"),  # 已完成（且有 approved 结算）→ 客户已确认
            (5, "WO-005", "客户戊", "open"),    # 只有 void 发票 → 预计
            (6, "WO-006", "客户己", "closed"),  # 已完成且无结算 → 客户已确认
        ],
    )
    conn.execute("insert into users (id, name) values (1, '张三')")
    for order_id in range(1, 7):
        conn.execute(
            "insert into service_reports (id, service_order_id, report_date, actual_work_date, total_service_hours)"
            " values (?, ?, '2026-09-15', '2026-09-15', 8)",
            (order_id, order_id),
        )
        conn.execute(
            "insert into service_report_workers (report_id, user_id, travel_mode, driving_miles) values (?, 1, 'following', 0)",
            (order_id,),
        )
    conn.execute(
        "insert into customer_reimbursements (service_order_id, status, created_at) values (2, 'draft', '2026-09-16 10:00:00')"
    )
    conn.execute("insert into invoices (service_order_id, status) values (3, 'submitted')")
    conn.execute(
        "insert into customer_reimbursements (service_order_id, status, created_at) values (4, 'approved', '2026-09-16 10:00:00')"
    )
    conn.execute("insert into invoices (service_order_id, status) values (5, 'void')")
    conn.commit()
    yield api, conn
    conn.close()


def test_line_status_unit():
    assert _line_status("closed", 0, 0) == "client_confirmed"
    assert _line_status("closed", 1, 0) == "client_confirmed"
    assert _line_status("open", 1, 0) == "settlement_in_progress"
    assert _line_status("open", 0, 1) == "settlement_in_progress"
    assert _line_status("open", 1, 1) == "settlement_in_progress"
    assert _line_status("open", 0, 0) == "estimated"


def test_labor_line_status_by_order(seeded):
    api, _ = seeded
    lines = _labor_lines(api, "2026-09-01", "2026-09-30")
    assert lines, "应产出工时利润行"
    status_by_order = {}
    for line in lines:
        status_by_order.setdefault(line["service_order_id"], set()).add(line["status"])
    assert status_by_order[1] == {"estimated"}
    assert status_by_order[2] == {"settlement_in_progress"}
    assert status_by_order[3] == {"settlement_in_progress"}
    # 关键回归点：旧逻辑 approved 结算单会被判为"客户已确认"，
    # 新规则下工单未完成就必须是"结算中"
    assert status_by_order[4] == {"client_confirmed"}
    assert status_by_order[5] == {"estimated"}
    assert status_by_order[6] == {"client_confirmed"}


def test_labor_status_falls_back_after_settlement_deleted(seeded):
    api, conn = seeded
    conn.execute("delete from customer_reimbursements where service_order_id = 2")
    conn.commit()
    lines = _labor_lines(api, "2026-09-01", "2026-09-30")
    statuses = {line["status"] for line in lines if line["service_order_id"] == 2}
    assert statuses == {"estimated"}


def test_expense_line_status_by_order(seeded):
    api, conn = seeded
    for order_id, amount in ((1, 80), (2, 100), (4, 60), (5, 40)):
        cursor = conn.execute(
            "insert into expenses (expense_date, service_order_id, status) values ('2026-09-15', ?, 'approved')",
            (order_id,),
        )
        conn.execute(
            "insert into expense_items (expense_id, amount, fuel_vehicle_type) values (?, ?, 'company')",
            (cursor.lastrowid, amount),
        )
    # 工单 2 的费用已进入结算单（收入取快照）
    conn.execute(
        "insert into customer_reimbursement_expense_links (customer_reimbursement_id, expense_item_id, amount_snapshot)"
        " values (1, 2, 120)"
    )
    conn.commit()

    lines = _expense_lines(api, "2026-09-01", "2026-09-30")
    status_by_order = {}
    for line in lines:
        status_by_order.setdefault(line["service_order_id"], set()).add(line["status"])
    assert status_by_order[1] == {"estimated"}
    assert status_by_order[2] == {"settlement_in_progress"}
    assert status_by_order[4] == {"client_confirmed"}
    assert status_by_order[5] == {"estimated"}

    order2 = [line for line in lines if line["service_order_id"] == 2][0]
    assert order2["revenue"] == pytest.approx(120.0)
    assert order2["client_rate_source"] == "selected_settlement_expense"


def test_build_profit_lines_mixes_both_sources(seeded):
    api, conn = seeded
    cursor = conn.execute(
        "insert into expenses (expense_date, service_order_id, status) values ('2026-09-15', 2, 'approved')"
    )
    conn.execute(
        "insert into expense_items (expense_id, amount, fuel_vehicle_type) values (?, 30, 'company')",
        (cursor.lastrowid,),
    )
    conn.commit()
    lines = _labor_lines(api, "2026-09-01", "2026-09-30") + _expense_lines(api, "2026-09-01", "2026-09-30")
    order2 = [line["status"] for line in lines if line["service_order_id"] == 2]
    assert order2 and set(order2) == {"settlement_in_progress"}
