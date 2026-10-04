"""Payroll Cycle Correction —— Option C Allocation Layer 的迁移 + 读层契约测试。

这一版只验证「机制」：迁移本身、统一读层、批次/往来账闸门、P7 carry-forward、
纷歧ր Tax Review 联动、年度合计、Statement 闭合、回滚。它**不做**任何纠偏 ——
测试数据都是本文件自己造的最小等价场景，不含任何生产数据。

对应的design 决策（改代码前先读 .workbuddy/reports/payroll-cycle-correction-plan-final-2026-10-02.md）：
- Decision 1  批准创建 replacement SL，但它们只是 canonical payable container，
  frozen component 的 payment_order_id 永远不变；
- Decision 2/8 旧 draft SL 进终态（superseded / partially_superseded），
  但绝不 DELETE，详情页仍可看原始 snapshot；
- Decision 3  replacement SL 的金额 = allocation 层，不是 SUM(component where
  payment_order_id = 本单)；
- Decision 4  Annual / CPA / Statement / Batch / Ledger / Payroll 全部调用统一层，
  不得各自解释 allocation 表；
- Decision 5  工资侧不以 component.superseded_at 表达作废，仅以 allocation
  disposition 为准（expense 侧逻辑保留）；
- Decision 6  只有真漏算才新建 component，source_type =
  historical_payroll_correction_missing，金额走 Decimal + ROUND_HALF_UP；
- Decision 7  已付款且金额=canonical entitlement 时 Adjustment = 0，
  只补 period_label_corrected note，不产生新应付；
- Decision 9  pre-canonical 保持原样，不建 allocation 也照样按原 service_date 计入；
- Decision 10 orders 只加 4 列；14 天 CHECK 只约束真正带 canonical period 的行；
- Decision 12 执行后不允许出现「旧应付 + replacement 应付」双份 payable。
"""
import importlib.util
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import tests_pg

ROOT = Path(__file__).resolve().parent
MIGRATION = ROOT / "migrations" / "postgresql" / "0297-payroll-cycle-correction.sql"

NOW = "2026-10-02T10:00:00-05:00"

# canonical grid: 07-06 + 14d
P6 = ("2026-09-14", "2026-09-27")
P7 = ("2026-09-28", "2026-10-11")

TABLES = (
    "employee_payment_tax_reviews",
    # correction allocation 同时被 components / orders / groups 引用，
    # 必须最先删；groups 被 orders.correction_group_id 引用，必须在 orders 之后删。
    "payroll_component_correction_allocations",
    "employee_payment_components",
    "payment_order_events",
    "payment_order_sources",
    "bank_transactions",
    "employee_payment_orders",
    "payroll_correction_groups",
    "employee_payment_batches",
    "employee_advance_applications",
    "employee_advances",
    "worker_tax_status_history",
    "expense_attachments",
    "expense_items",
    "expenses",
    "service_orders",
    "messages",
    "audit_logs",
    "projects",
    "bank_accounts",
    "users",
)


class PayrollCycleCorrectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tests_pg.activate()
        # 迁移只在这里以幂等方式应用一次 —— 即使测试库被人从 schema 快照重建，
        # 本测试也能自己补上；生产侧的 begit execution 走 scripts/upgrade_postgresql_0297.py。
        with tests_pg.connection() as conn:
            conn.execute(MIGRATION.read_text(encoding="utf-8"))
            conn.execute(MIGRATION.read_text(encoding="utf-8"))  # 幂等回归：跑两遍不许报错
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("payroll_correction_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="payroll-correction-secret")
        cls.module.app.template_folder = str(ROOT / "templates")
        cls.module.app.static_folder = str(ROOT / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in TABLES:
                db.execute(f"delete from {table}")
            try:
                db.execute("ALTER SEQUENCE payroll_correction_groups_id_seq RESTART WITH 1")
                db.execute(
                    "ALTER SEQUENCE payroll_component_correction_allocations_id_seq"
                    " RESTART WITH 1")
            except Exception:
                db.rollback()
            self.admin_id = self._raw_user(db, "Admin", "admin")
            self.employee_id = self._raw_user(db, "Antonio", "employee")
            self.other_id = self._raw_user(db, "Andres", "employee")
            self.order_id = db.execute(
                "insert into service_orders (order_number,client_name,site_address,"
                "client_order_number,created_by,created_at)"
                " values ('SO-2609-0001','Client','Site','C1',?,?)",
                (self.admin_id, self.module.now()),
            ).lastrowid
            self.account_id = db.execute(
                "insert into bank_accounts (account_name,bank_name,account_type,currency,"
                "last_four,opening_balance,is_active,created_at,updated_at)"
                " values ('Main','Bank','checking','USD','0001',0,1,?,?)",
                (self.module.now(), self.module.now()),
            ).lastrowid
            db.commit()
        self.http = self.module.app.test_client()
        self._login(self.admin_id)

    def tearDown(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in TABLES:
                db.execute(f"delete from {table}")
            db.commit()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _raw_user(db, name, role):
        return db.execute(
            "insert into users (name,email,password_hash,role,is_active,created_at)"
            " values (?,?,?,?,1,?)",
            (name, f"{name.lower()}@test.invalid", "unused", role, NOW),
        ).lastrowid

    def _login(self, user_id):
        with self.http.session_transaction() as session:
            session["user_id"] = user_id

    def _order(self, employee_id, *, number, source_number, gross, status="draft",
               payment_type="salary", correction_status="none", superseded_by=None,
               group_id=None, reason="", paid_at=None, batch_id=None):
        with self.module.app.app_context():
            db = self.module.db()
            order_id = db.execute(
                """
                insert into employee_payment_orders
                (payment_number,employee_id,payment_type,status,currency,gross_amount,
                 advance_offset,other_adjustment,net_amount,taxable_compensation_total,
                 accountable_reimbursement_total,tax_review_required_total,source_type,
                 source_number,correction_status,superseded_by_id,correction_group_id,
                 correction_reason,paid_at,batch_id,created_at,updated_at,created_by)
                values (?,?,?,?,'USD',?,'0','0',?,'0','0','0','system_payroll',
                        ?,?,?,?,?,?,?,?,?,?)
                """,
                (number, employee_id, payment_type, status, gross, gross, source_number,
                 correction_status, superseded_by, group_id, reason,
                 paid_at, batch_id, self.module.now(), self.module.now(), self.admin_id),
            ).lastrowid
            db.commit()
            return order_id

    def _component(self, order_id, employee_id, *, code="regular", amount, service_date,
                   tax_category="taxable_compensation", source_type="system_payroll",
                   quantity=None, unit=None, unit_rate=None, review_status=None):
        with self.module.app.app_context():
            db = self.module.db()
            component_id = db.execute(
                """
                insert into employee_payment_components
                (payment_order_id,employee_id,component_code,component_name,amount,
                 quantity,unit,unit_rate,service_date,work_order_id,source_type,source_id,
                 daily_report_id,tax_category,tax_status_snapshot,substantiated,
                 review_status,created_at)
                values (?,?,?,?,?,?,?,?,?,?,'873',?,?,?,?,?,?,?)
                """.replace("'873'", "?"),
                (order_id, employee_id, code, code, amount, quantity, unit, unit_rate,
                 service_date, self.order_id, source_type, None, None, tax_category,
                 "1099", True,
                 review_status or ("review_required" if tax_category == "tax_review_required"
                                   else "confirmed"),
                 self.module.now()),
            ).lastrowid
            db.commit()
            return component_id

    def _group(self, *, code="payroll_cycle_realignment_2026", status="applied"):
        with self.module.app.app_context():
            db = self.module.db()
            group_id = db.execute(
                """
                insert into payroll_correction_groups
                (correction_code,correction_type,policy_version,canonical_cycle_start,
                 canonical_cycle_days,canonical_period_start,canonical_period_end,status,
                 reason,created_by,created_at,updated_at)
                values (?,'payroll_cycle_realignment','phase6d_company_policy_2026',
                        '2026-07-06',14,?,?,?,?,?,?,?)
                """,
                (code, P6[0], P6[1], status, "payroll cycle realignment", self.admin_id,
                 self.module.now(), self.module.now()),
            ).lastrowid
            db.commit()
            return group_id

    def _alloc(self, group_id, component_id, disposition, *, canonical_order_id=None,
               period=None, keeper_component_id=None, reason=""):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                """
                insert into payroll_component_correction_allocations
                (correction_group_id,component_id,canonical_payment_order_id,
                 canonical_period_start,canonical_period_end,disposition,
                 keeper_component_id,reason,created_by,created_at)
                values (?,?,?,?,?,?,?,?,?,?)
                """,
                (group_id, component_id, canonical_order_id,
                 period[0] if period else None, period[1] if period else None,
                 disposition, keeper_component_id, reason, self.admin_id,
                 self.module.now()),
            )
            db.commit()

    def _review(self, component_id, new_category, reason="policy phase6d"):
        with self.module.app.app_context():
            from flask import g
            g.user = {"id": self.admin_id, "name": "Admin"}
            self.module.record_tax_review(component_id, new_category, reason, self.admin_id)
            self.module.db().commit()

    def _summary(self, **filters):
        with self.module.app.app_context():
            filters.setdefault("year", "2026")
            filters.setdefault("year_basis", "service_date")
            return self.module.annual_tax_summary_rows(filters)

    def _tax(self, order_id):
        with self.module.app.app_context():
            return self.module.payment_tax_components(order_id)

    def _payable(self, **filters):
        with self.module.app.app_context():
            return self.module.payable_totals(filters or None)

    def _totals_row(self, employee_id=None):
        data = self._summary()
        rows = data["rows"]
        if employee_id is not None:
            rows = [row for row in rows if row["employee_id"] == employee_id]
        return rows, data["totals"]

    # ------------------------------------------------------------------
    # 0. migration
    # ------------------------------------------------------------------
    def test_payable_totals_accepts_no_filters_for_employee_ledger(self):
        """往来账默认的“全部员工”视图必须能直接取得统一应付汇总。"""
        totals = self._payable()
        self.assertEqual(totals["payable_count"], 0)
        self.assertEqual(str(totals["effective_net"]), "0.00")
        response = self.http.get("/finance/employee-ledger")
        self.assertEqual(response.status_code, 200)
        self.assertIn("员工往来账", response.get_data(as_text=True))

    def test_migration_is_additive_and_idempotent(self):
        with tests_pg.connection() as conn:
            names = {row[0] for row in conn.execute(
                "select table_name from information_schema.tables"
                " where table_schema='public'").fetchall()}
            self.assertIn("payroll_correction_groups", names)
            self.assertIn("payroll_component_correction_allocations", names)
            cols = {row[0] for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema='public' and table_name='employee_payment_orders'"
            ).fetchall()}
            for column in ("correction_status", "correction_group_id",
                           "superseded_by_id", "correction_reason"):
                self.assertIn(column, cols)
            # 既有行仍是 none：迁移本身不改变任何金额语义
            self.assertEqual(
                conn.execute("select count(*) from employee_payment_orders"
                             " where correction_status <> 'none'").fetchone()[0], 0)

    def test_canonical_period_check_only_bites_rows_with_a_period(self):
        """Decision 10：14 天 CHECK 必须允许 period 全空的行（legacy / metadata）。"""
        group_id = self._group()
        order_id = self._order(self.employee_id, number="SL-A",
                               source_number="2026-09-15~2026-09-28", gross="100.00")
        comp = self._component(order_id, self.employee_id, amount="100.00",
                               service_date="2026-09-16")
        self._alloc(group_id, comp, "retained")          # 无 period：必须被接受
        self._alloc(group_id, self._component(order_id, self.employee_id, amount="1.00",
                                              service_date="2026-09-17"),
                    "retained", period=P6)               # 14 天：接受
        with self.assertRaises(Exception) as ctx:
            self._alloc(group_id, self._component(order_id, self.employee_id, amount="2.00",
                                                  service_date="2026-09-18"),
                        "retained", period=("2026-09-14", "2026-09-28"))  # 15 天
        self.assertIn("constraint", str(ctx.exception).lower())

    # ------------------------------------------------------------------
    # 1. no allocation rows == legacy behaviour（迁移先于纠偏上线的前提）
    # ------------------------------------------------------------------
    def test_empty_allocations_behave_exactly_like_legacy(self):
        order_id = self._order(self.employee_id, number="SL-2609-0001",
                               source_number="2026-09-15~2026-09-28", gross="150.00")
        self._component(order_id, self.employee_id, amount="100.00", service_date="2026-09-16")
        self._component(order_id, self.employee_id, amount="50.00", service_date="2026-09-17")
        rows, totals = self._totals_row(self.employee_id)
        self.assertEqual(str(rows[0]["total_amount"]), "150.00")
        self.assertTrue(totals["closure_ok"])
        tax = self._tax(order_id)
        self.assertEqual(len(tax["rows"]), 2)
        self.assertEqual(str(tax["effective_totals"]["taxable_compensation"]), "150.00")

    # ------------------------------------------------------------------
    # 2. duplicate_superseded 从所有 effective 口径消失
    # ------------------------------------------------------------------
    def _duplicate_scenario(self):
        """09-15~09-28 vs 09-16~09-29 的最小等价：1 组重复 + 1 条 09-29 carry-forward。

        返回 (group, A, B, keeper, dup, carry, replacement)
        """
        group_id = self._group()
        off_a = self._order(self.employee_id, number="SL-A-0915",
                            source_number="2026-09-15~2026-09-28", gross="100.00",
                            correction_status="superseded", group_id=group_id)
        off_b = self._order(self.employee_id, number="SL-B-0916",
                            source_number="2026-09-16~2026-09-29", gross="150.00",
                            correction_status="partially_superseded", group_id=group_id)
        keeper = self._component(off_a, self.employee_id, amount="100.00",
                                 service_date="2026-09-16")
        dup = self._component(off_b, self.employee_id, amount="100.00",
                              service_date="2026-09-16")
        carry = self._component(off_b, self.employee_id, amount="50.00",
                                service_date="2026-09-29")
        replacement = self._order(
            self.employee_id, number="SL-R-P6", source_number=f"{P6[0]}~{P6[1]}",
            gross="125.00", status="approved", correction_status="replacement",
            group_id=group_id)
        # Decision 6：只有真漏算才新建 component，金额保持 Decimal + ROUND_HALF_UP。
        self._component(replacement, self.employee_id, amount="25.00",
                        service_date="2026-09-20",
                        source_type="historical_payroll_correction_missing")
        self._alloc(group_id, keeper, "retained", canonical_order_id=replacement, period=P6)
        self._alloc(group_id, dup, "duplicate_superseded", canonical_order_id=replacement,
                    period=P6, keeper_component_id=keeper)
        self._alloc(group_id, carry, "carry_forward", period=P7)
        for old, status in ((off_a, "superseded"), (off_b, "partially_superseded")):
            self._mark_superseded(old, replacement, status)
        return group_id, (off_a, off_b, keeper, dup, carry, replacement)

    def _mark_superseded(self, order_id, replacement_id, status):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "update employee_payment_orders set superseded_by_id=?, correction_status=?"
                " where id=?", (replacement_id, status, order_id))
            db.commit()

    def test_duplicate_superseded_is_excluded_everywhere(self):
        _, (off_a, off_b, keeper, dup, carry, replacement) = self._duplicate_scenario()
        # 年度合计：keeper 100 + missing 25 + carry 50 = 175（不是 275）
        rows, totals = self._totals_row(self.employee_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["total_amount"]), "175.00")
        self.assertTrue(totals["closure_ok"])
        # replacement SL 的 effective 组件 = keeper + missing（不是只有自己的新行）
        tax = self._tax(replacement)
        self.assertEqual(str(tax["effective_totals"]["taxable_compensation"]), "125.00")
        self.assertEqual({row["component_code"] for row in tax["rows"]},
                         {"regular", "regular"})
        self.assertEqual(len(tax["rows"]), 2)
        # 被 supersede 的旧单不再显示已搬走的组件（化合物 Fenwick 后仍保留其 carry-forward）
        tax_b = self._tax(off_b)
        self.assertEqual(len(tax_b["rows"]), 1)
        self.assertEqual(Decimal(str(tax_b["rows"][0]["amount"])), Decimal("50.00"))
        self.assertEqual(len(self._tax(off_a)["rows"]), 0)
        # Decision 5：工资侧不动 component.superseded_at，也不改 payment_order_id
        with tests_pg.connection() as conn:
            frozen = conn.execute(
                "select payment_order_id, superseded_at from employee_payment_components"
                " where id=%s", (keeper,)).fetchone()
            self.assertEqual(frozen[0], off_a)     # 仍是原始 parent
            self.assertIsNone(frozen[1])           # superseded_at 保持 NULL
            duplicate = conn.execute(
                "select payment_order_id from employee_payment_components where id=%s",
                (dup,)).fetchone()
            self.assertEqual(duplicate[0], off_b)  # duplicate 行也原样留着，没有 DELETE

    # ------------------------------------------------------------------
    # 3. Decision 1：keeper 选择规则（ROUND_HALF_UP 优先）
    # ------------------------------------------------------------------
    def test_keeper_rule_prefers_round_half_up_amount(self):
        with self.module.app.app_context():
            keeper, duplicates = self.module.choose_duplicate_keeper([
                {"id": 475, "amount": "118.12", "quantity": "2.25", "unit_rate": "52.5",
                 "daily_report_id": 1, "created_at": "2026-09-20T01:00:00"},
                {"id": 652, "amount": "118.13", "quantity": "2.25", "unit_rate": "52.5",
                 "daily_report_id": 1, "created_at": "2026-09-21T01:00:00"},
            ])
        self.assertEqual(keeper["id"], 652)
        self.assertEqual(str(keeper["amount"]), "118.13")
        self.assertEqual([row["id"] for row in duplicates], [475])
        # 规则 1 打平时回落到 created_at 早 / id 小
        with self.module.app.app_context():
            keeper2, dups2 = self.module.choose_duplicate_keeper([
                {"id": 900, "amount": "10.00", "quantity": "1", "unit_rate": "10",
                 "daily_report_id": None, "created_at": "2026-09-22T01:00:00"},
                {"id": 800, "amount": "10.00", "quantity": "1", "unit_rate": "10",
                 "daily_report_id": 1, "created_at": "2026-09-21T01:00:00"},
            ])
        self.assertEqual(keeper2["id"], 800)   # daily_report 非空优先于 created_at
        self.assertEqual([row["id"] for row in dups2], [900])

    def test_replacement_closure_and_integrity_gate(self):
        _, (_, _, _, _, _, replacement) = self._duplicate_scenario()
        with self.module.app.app_context():
            report = self.module.replacement_closure_report()
            check = self.module.payroll_correction_integrity_check("125.00")
        self.assertEqual(report["count"], 1)
        self.assertTrue(report["closure_ok"])
        self.assertEqual(str(report["component_total"]), "125.00")
        self.assertTrue(check["ok"], check["issues"])
        # 把 keeper 的金额改坏 —— Gate 必须 FAIL，而不是静默通过
        with tests_pg.connection() as conn:
            conn.execute("update employee_payment_components set amount=99.99 where id=%s",
                         (self._last_component_of(replacement),))
        with self.module.app.app_context():
            broken = self.module.payroll_correction_integrity_check("125.00")
        self.assertFalse(broken["ok"])
        self.assertTrue(any("!=" in issue for issue in broken["issues"]))

    def _last_component_of(self, order_id):
        with tests_pg.connection() as conn:
            return conn.execute(
                "select id from employee_payment_components where payment_order_id=%s"
                " order by id desc limit 1", (order_id,)).fetchone()[0]

    # ------------------------------------------------------------------
    # 4. Decision 12：Ledger 不允许双份 payable
    # ------------------------------------------------------------------
    def test_no_double_payable_after_correction(self):
        _, (off_a, off_b, keeper, dup, carry, replacement) = self._duplicate_scenario()
        before = self._payable(payment_type="salary")["payable_gross"]   # 100+150+125
        after = self._payable(payment_type="salary")
        self.assertEqual(str(before), "375.00")
        # off-grid 两张退出应付（100 + 150），replacement 125 成为唯一应付；
        # carry-forward 50 仍挂在 old B 上随 B 一起退出 —— 它会在 P7 生成时才重新出现。
        self.assertEqual(str(after["effective_gross"]), "125.00")
        self.assertEqual(after["excluded_count"], 2)
        self.assertEqual(str(after["excluded_gross"]), "250.00")
        # 等到 P7 生成、把 carry-forward 纳进去之后，总额才补齐
        p7 = self._order(self.employee_id, number="SL-R-P7",
                         source_number=f"{P7[0]}~{P7[1]}", gross="50.00",
                         status="approved", correction_status="replacement",
                         group_id=self._group_id())
        with self.module.app.app_context():
            self.module.attach_carry_forward(p7, [carry], reason="P7 generated")
            self.module.db().commit()
        self.assertEqual(str(self._payable(payment_type="salary")["effective_gross"]),
                         "175.00")
        tax = self._tax(p7)
        self.assertEqual(str(tax["effective_totals"]["taxable_compensation"]), "50.00")

    def _group_id(self):
        with tests_pg.connection() as conn:
            return conn.execute(
                "select id from payroll_correction_groups order by id desc limit 1"
            ).fetchone()[0]

    # ------------------------------------------------------------------
    # 5. Batch gate
    # ------------------------------------------------------------------
    def test_batch_gate_blocks_superseded_and_label_corrected(self):
        _, (off_a, off_b, keeper, dup, carry, replacement) = self._duplicate_scenario()
        paid = self._order(self.employee_id, number="SL-PAID",
                           source_number="2026-08-17~2026-08-30", gross="900.00",
                           status="paid", paid_at="2026-09-10T00:00:00",
                           correction_status="period_label_corrected")
        with self.module.app.app_context():
            rows = self.module.db().execute(
                "select * from employee_payment_orders where id in (?,?,?)",
                (off_a, replacement, paid)).fetchall()
            blocked = {row["payment_number"]: self.module.payment_batch_block_reason(row)
                       for row in rows}
        self.assertTrue(blocked["SL-A-0915"])
        self.assertTrue(blocked["SL-PAID"])
        self.assertIsNone(blocked["SL-R-P6"])   # replacement 可以正常发放

    def test_batch_creation_endpoint_rejects_superseded_orders(self):
        _, (off_a, _, _, _, _, replacement) = self._duplicate_scenario()
        clean = self._order(self.employee_id, number="SL-CLEAN",
                            source_number="2026-07-06~2026-07-19", gross="10.00",
                            status="approved")
        response = self.http.post(
            "/finance/payment-batches/create",
            data={"payment_id": [str(off_a)], "bank_account_id": str(self.account_id),
                  "payment_method": "check", "check_number": "9001"},
            follow_redirects=False)
        with self.module.app.app_context():
            rows = self.module.db().execute(
                "select id from employee_payment_batches").fetchall()
        self.assertEqual(len(rows), 0, "superseded 单据不得生成批次")
        self.assertEqual(response.status_code, 302)
        # 对照组：换成 replacement 就可以（证明上面是真被闸门拦下，不是路由坏了）
        response = self.http.post(
            "/finance/payment-batches/create",
            data={"payment_id": [str(clean)], "bank_account_id": str(self.account_id),
                  "payment_method": "check", "check_number": "9002"},
            follow_redirects=False)
        with self.module.app.app_context():
            rows = self.module.db().execute(
                "select id from employee_payment_batches").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(response.status_code, 302)

    # ------------------------------------------------------------------
    # 6. Decision 7：已付款金额 == canonical entitlement → Adjustment = 0
    # ------------------------------------------------------------------
    def test_paid_period_label_corrected_still_closes_statement(self):
        group_id = self._group()
        order_id = self._order(
            self.employee_id, number="SL-PAID-P34",
            source_number="2026-08-17~2026-08-30", gross="3843.15", status="paid",
            paid_at="2026-10-01T12:00:00", correction_status="period_label_corrected",
            group_id=group_id)
        c1 = self._component(order_id, self.employee_id, amount="940.00",
                             service_date="2026-08-05")
        c2 = self._component(order_id, self.employee_id, amount="2903.15",
                             service_date="2026-08-20")
        # canonical_payment_order_id 留 NULL =「这笔钱已经付过，不产生新应付」
        self._alloc(group_id, c1, "retained", period=("2026-08-03", "2026-08-16"))
        self._alloc(group_id, c2, "retained", period=("2026-08-17", "2026-08-30"))
        with self.module.app.app_context():
            db = self.module.db()
            batch_id = db.execute(
                "insert into employee_payment_batches"
                " (batch_number,employee_id,currency,bank_account_id,payment_method,"
                " check_number,total_amount,payment_count,status,issued_at,created_at,updated_at)"
                " values ('PB-2610-0001',?,'USD',?,'check','1001',3843.15,1,'issued',?,?,?)",
                (self.employee_id, self.account_id, self.module.now(),
                 self.module.now(), self.module.now()),
            ).lastrowid
            db.execute("update employee_payment_orders set batch_id=? where id=?",
                       (batch_id, order_id))
            db.commit()
            from employee_finance import payment_statement_payload
            payload = payment_statement_payload(
                {**{key: getattr(self.module, key) for key in ("db", "payment_tax_components")},
                 "abort": self.module.abort,
                 "normalized_role": (lambda: "admin")},
                batch_id)
        self.assertTrue(payload["batch_closure_ok"], payload)
        self.assertEqual(str(payload["batch"]["total_amount"]), "3843.15")
        entry = payload["salary_orders"][0]
        self.assertTrue(entry["component_closure_ok"])
        self.assertEqual(str(entry["component_sum"]), "3843.15")
        # 仍然计入 payable（钱确实付过），但不产生新的 replacement SL
        self.assertEqual(str(self._payable(payment_type="salary")["effective_gross"]),
                         "3843.15")
        with self.module.app.app_context():
            report = self.module.replacement_closure_report()
        self.assertEqual(report["count"], 0, "已付款纠偏不得生成 replacement SL")

    # ------------------------------------------------------------------
    # 7. Decision 9：pre-canonical 保持原样
    # ------------------------------------------------------------------
    def test_pre_canonical_legacy_stays_counted(self):
        order_id = self._order(self.employee_id, number="SL-2606-0001",
                               source_number="2026-06-01~2026-06-14", gross="80.00")
        self._component(order_id, self.employee_id, amount="80.00", service_date="2026-06-10")
        rows, totals = self._totals_row(self.employee_id)
        self.assertEqual(str(rows[0]["total_amount"]), "80.00")
        self.assertEqual(str(totals["total_amount"]), "80.00")
        # 即便写了 pre_canonical_legacy 的 metadata 行（period 全空），也照旧计入
        group_id = self._group()
        self._alloc(group_id, self._last_component_of(order_id), "pre_canonical_legacy")
        rows, totals = self._totals_row(self.employee_id)
        self.assertEqual(str(rows[0]["total_amount"]), "80.00")
        self.assertEqual(str(self._payable(payment_type="salary")["effective_gross"]), "80.00")

    # ------------------------------------------------------------------
    # 8. Tax Review 联动：171 条历史一条不丢
    # ------------------------------------------------------------------
    def test_review_history_stays_attached_to_keeper(self):
        _, (_, _, keeper, dup, _, replacement) = self._duplicate_scenario()
        self._review(keeper, "accountable_reimbursement")
        rows, totals = self._totals_row(self.employee_id)
        # keeper 100 被复核成 reimbursement，其余 75 仍是 compensation
        self.assertEqual(str(rows[0]["reimbursement_amount"]), "100.00")
        self.assertEqual(str(rows[0]["compensation_amount"]), "75.00")
        self.assertEqual(str(rows[0]["review_amount"]), "0.00")
        with tests_pg.connection() as conn:
            reviews = conn.execute(
                "select component_id, previous_tax_category, new_tax_category"
                " from employee_payment_tax_reviews order by id").fetchall()
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0][0], keeper)
        self.assertEqual(reviews[0][1], "taxable_compensation")
        self.assertEqual(reviews[0][2], "accountable_reimbursement")
        with self.module.app.app_context():
            detail = self.module.tax_review_detail(keeper)
        self.assertEqual(len(detail["history"]), 1)
        # duplicate 行没有 review，也不该出现在复核队列里
        with self.module.app.app_context():
            queue = self.module.tax_review_rows({"year": "2026"})
        self.assertEqual({row["component_id"] for row in queue["rows"]} & {dup}, set())

    # ------------------------------------------------------------------
    # 9. Decision 8：P7 生成时的 carry-forward 去重
    # ------------------------------------------------------------------
    def test_flag_carry_forward_marks_without_dropping_rows(self):
        _, (_, _, _, _, carry, _) = self._duplicate_scenario()
        components = [
            {"component_code": "regular", "service_date": "2026-09-29",
             "work_order_id": self.order_id, "amount": Decimal("50.00")},
            {"component_code": "regular", "service_date": "2026-10-02",
             "work_order_id": self.order_id, "amount": Decimal("20.00")},
        ]
        with self.module.app.app_context():
            flagged = self.module.flag_carry_forward_components(
                self.employee_id, components, P7[0], P7[1])
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["component_id"], carry)
        self.assertTrue(components[0]["correction_pre_existing"])
        self.assertNotIn("correction_pre_existing", components[1])
        # 标记行不删 → sum(components) 仍然等于 canonical gross
        self.assertEqual(sum((row["amount"] for row in components), Decimal("0")),
                         Decimal("70.00"))

    def test_carry_forward_not_returned_once_allocated(self):
        _, (_, _, _, _, carry, _) = self._duplicate_scenario()
        p7 = self._order(self.employee_id, number="SL-R-P7", source_number=f"{P7[0]}~{P7[1]}",
                         gross="50.00", status="approved", correction_status="replacement",
                         group_id=self._group_id())
        with self.module.app.app_context():
            self.assertEqual(len(self.module.carry_forward_identities(self.employee_id, *P7)), 1)
            self.assertEqual(self.module.attach_carry_forward(p7, [carry]), 1)
            self.module.db().commit()
            # 已挂到 P7 → 不能再排除（否则 P7 重算会少掉这部分钱）
            self.assertEqual(self.module.carry_forward_identities(self.employee_id, *P7), [])
            self.assertEqual(
                len(self.module.carry_forward_identities(self.employee_id, *P7,
                                                         only_unallocated=False)), 1)

    # ------------------------------------------------------------------
    # 10. rollback
    # ------------------------------------------------------------------
    def test_rollback_restores_baseline_exactly(self):
        # 基线 = 纠偏前的两份旧 SL（off_a / off_b），场景直接复用这两行，
        # 不再重复 INSERT（payment_number 唯一键 + frozen rows 本来就是同一批）。
        off_a = self._order(self.employee_id, number="SL-A-0915",
                            source_number="2026-09-15~2026-09-28", gross="100.00")
        off_b = self._order(self.employee_id, number="SL-B-0916",
                            source_number="2026-09-16~2026-09-29", gross="150.00")
        keeper = self._component(off_a, self.employee_id, amount="100.00",
                                 service_date="2026-09-16")
        dup = self._component(off_b, self.employee_id, amount="100.00",
                              service_date="2026-09-16")
        carry = self._component(off_b, self.employee_id, amount="50.00",
                                service_date="2026-09-29")
        before = self._summary()["totals"]
        # 施加纠偏：group + replacement + allocation + 状态标记
        group_id = self._group()
        replacement = self._order(
            self.employee_id, number="SL-R-P6", source_number=f"{P6[0]}~{P6[1]}",
            gross="125.00", status="approved", correction_status="replacement",
            group_id=group_id)
        self._component(replacement, self.employee_id, amount="25.00",
                        service_date="2026-09-20",
                        source_type="historical_payroll_correction_missing")
        self._alloc(group_id, keeper, "retained", canonical_order_id=replacement, period=P6)
        self._alloc(group_id, dup, "duplicate_superseded", canonical_order_id=replacement,
                    period=P6, keeper_component_id=keeper)
        self._alloc(group_id, carry, "carry_forward", period=P7)
        self._mark_superseded(off_a, replacement, "superseded")
        self._mark_superseded(off_b, replacement, "partially_superseded")
        self.assertNotEqual(str(self._summary()["totals"]["total_amount"]),
                            str(before["total_amount"]))
        # 回滚 = group 置 rolled_back + 旧单状态复位 + 删 replacement
        # （allocation 是元数据，先删引用 replacement 的 allocation 再删单；
        #   frozen rows 一行不删，replacement 的 missing comp 随 CASCADE 消失）
        with tests_pg.connection() as conn:
            conn.execute("update payroll_correction_groups set status='rolled_back'")
            conn.execute(
                "update employee_payment_orders set correction_status='none',"
                " superseded_by_id=null, correction_group_id=null where id in (%s,%s)"
                % (off_a, off_b))
            conn.execute("delete from payroll_component_correction_allocations"
                         " where correction_group_id=%s" % group_id)
            conn.execute("delete from employee_payment_orders where id=%s" % replacement)
        with tests_pg.connection() as conn:
            self.assertEqual(
                conn.execute("select count(*) from employee_payment_components").fetchone()[0], 3)
        # rolled_back group 对金额零影响：effective 回到基线 250
        rows, totals = self._totals_row(self.employee_id)
        self.assertEqual(str(rows[0]["total_amount"]), "250.00")


if __name__ == "__main__":
    tests_pg.reset_for_class()
    unittest.main(verbosity=2)
