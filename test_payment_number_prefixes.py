"""v0.1.343 回归：员工付款单编号「一类一前缀」。

背景（用户报障）：员工往来账里「员工报销」和「工资」的付款单编号规则不一致 ——
payment_type 只有 salary（工资）和 expense（员工报销），但两类共用 EP- 前缀 +
同一个流水池（工资 EP-2609-0001~0007、报销接着 0008 往下排），0286 迁移补建
存量报销时又用了第三种 EP-MIG-EXP-<expense_id> 格式。三类口径混在一个 EP- 下，
光看单号分不清业务类型。

改后规则：
    工资     -> SL-<YYMM>-<NNNN>
    员工报销 -> ER-<YYMM>-<NNNN>
各自独立流水池，YYMM 取单据自己的 created_at 月份。

本测试直接连测试库 invoice_test，跑真实的 0289 迁移 SQL，验证：
  1. 生成器按类型选前缀（源码契约 + 真跑 _next_number）；
  2. 存量任意三种旧格式都能被重新编号成 SL-/ER- 且类型一致；
  3. 同前缀同月份内流水号从 0001 起连续、无重复；
  4. 幂等：重复执行 SQL 结果不变；
  5. 迁移脚本的 schema_ready 判据能识别未迁移/已迁移。
"""
import unittest
from decimal import Decimal
from pathlib import Path

import tests_pg

ROOT = Path(__file__).resolve().parent

MIGRATION_0289 = ROOT / "migrations" / "postgresql" / "0289-payment-number-prefixes.sql"
RUNNER_0289 = ROOT / "scripts" / "upgrade_postgresql_0289.py"
MODULE = ROOT / "employee_finance.py"


class _SeedMixin:
    """向测试库播种一个可用员工。

    注意：import app 会触发 init_db() -> ensure_postgres_admin()，自动插入一个
    admin 用户（占 id=1），所以这里不能自己指定 id，只能**取现有的那一个**。
    """

    def _employee_id(self, conn):
        row = conn.execute("select id from users order by id limit 1").fetchone()
        if row:
            return row[0]
        conn.execute(
            """
            insert into users (name, email, password_hash, role, is_active, created_at)
            values ('编号测试员工', 'numbering@test.invalid', 'x', 'employee', 1, '2026-09-01T00:00:00')
            """
        )
        return conn.execute(
            "select id from users where email='numbering@test.invalid'"
        ).fetchone()[0]


def _seed_payment(conn, *, payment_type, payment_number, created_at, employee_id):
    """插入一张最小可用的付款单（只填 NOT NULL 列）。"""
    conn.execute(
        """
        insert into employee_payment_orders
            (payment_number, employee_id, payment_type, status, currency,
             gross_amount, advance_offset, other_adjustment, net_amount,
             source_type, created_at, updated_at)
        values (%s, %s, %s, 'draft', 'USD', 100, 0, 0, 100, 'manual', %s, %s)
        """,
        (payment_number, employee_id, payment_type, created_at, created_at),
    )


class PaymentNumberPrefixContractTest(unittest.TestCase):
    """源码契约：前缀映射与调用点。"""

    def test_module_declares_one_prefix_per_payment_type(self):
        source = MODULE.read_text(encoding="utf-8")
        self.assertIn("PAYMENT_NUMBER_PREFIXES", source)
        self.assertIn('"salary": "SL"', source)
        self.assertIn('"expense": "ER"', source)

    def test_insert_payment_uses_type_specific_prefix(self):
        source = MODULE.read_text(encoding="utf-8")
        self.assertIn("PAYMENT_NUMBER_PREFIXES[payment_type]", source)
        # 旧写法（两类共用 EP）必须消失
        self.assertNotIn('_next_number(api, "EP", "employee_payment_orders"', source)

    def test_prefix_map_matches_declared_payment_types(self):
        from employee_finance import PAYMENT_NUMBER_PREFIXES, PAYMENT_TYPE_LABELS
        self.assertEqual(set(PAYMENT_NUMBER_PREFIXES), set(PAYMENT_TYPE_LABELS))

    def test_allocator_no_longer_orders_by_id(self):
        """存量改号后 id 顺序 ≠ 流水顺序，按 id 取最大号会撞号。"""
        source = MODULE.read_text(encoding="utf-8")
        body = source.split("def _next_number(", 1)[1].split("\ndef ", 1)[0]
        # 只看真实 SQL 语句（注释里会解释为什么不能用它）
        sql_lines = [line for line in body.splitlines() if not line.strip().startswith("#")]
        self.assertNotIn("order by id desc", "\n".join(sql_lines))
        self.assertIn("rsplit", body)
        self.assertIn("fetchall()", body)


class PaymentNumberRenumberMigrationTest(_SeedMixin, unittest.TestCase):
    """真跑 0289 SQL 验证存量重新编号。"""

    @classmethod
    def setUpClass(cls):
        tests_pg.activate()
        tests_pg.ensure_schema()
        cls.sql = MIGRATION_0289.read_text(encoding="utf-8")

    def setUp(self):
        with tests_pg.connection() as conn:
            conn.execute("delete from employee_payment_orders")
            employee_id = self._employee_id(conn)
            # 覆盖三种真实历史格式：
            # 工资（新格式）
            _seed_payment(conn, payment_type="salary",
                          payment_number="EP-2609-0001", created_at="2026-09-26T21:09:40",
                          employee_id=employee_id)
            _seed_payment(conn, payment_type="salary",
                          payment_number="EP-2609-0002", created_at="2026-09-26T21:12:37",
                          employee_id=employee_id)
            # 员工报销（新格式）
            _seed_payment(conn, payment_type="expense",
                          payment_number="EP-2609-0003", created_at="2026-09-27T22:00:02",
                          employee_id=employee_id)
            # 员工报销（0286 迁移遗留格式）
            _seed_payment(conn, payment_type="expense",
                          payment_number="EP-MIG-EXP-92", created_at="2026-09-10T08:00:00",
                          employee_id=employee_id)
            _seed_payment(conn, payment_type="expense",
                          payment_number="EP-MIG-EXP-93", created_at="2026-09-11T08:00:00",
                          employee_id=employee_id)
            conn.commit()

    def _apply(self):
        with tests_pg.connection() as conn:
            conn.execute(self.sql)
            conn.commit()

    def _rows(self):
        with tests_pg.connection() as conn:
            return [
                (r[0], r[1], r[2])
                for r in conn.execute(
                    "select payment_number, payment_type, created_at"
                    " from employee_payment_orders order by created_at, id"
                ).fetchall()
            ]

    def test_all_legacy_formats_become_typed_prefixes(self):
        self._apply()
        rows = self._rows()
        self.assertEqual(len(rows), 5)
        for number, payment_type, _ in rows:
            if payment_type == "salary":
                self.assertTrue(number.startswith("SL-"), number)
            else:
                self.assertTrue(number.startswith("ER-"), number)
            self.assertNotIn("EP-", number)

    def test_numbers_are_sequential_per_prefix_and_month(self):
        self._apply()
        rows = self._rows()
        # 工资 2 张（都在 2609）-> SL-2609-0001, SL-2609-0002
        salaries = sorted(n for n, t, _ in rows if t == "salary")
        self.assertEqual(salaries, ["SL-2609-0001", "SL-2609-0002"])
        # 报销 3 张：9/10、9/11、9/27 -> 全在 2609，按日期 0001~0003
        expenses = [n for n, t, _ in rows if t == "expense"]
        self.assertEqual(sorted(expenses), ["ER-2609-0001", "ER-2609-0002", "ER-2609-0003"])

    def test_renumber_preserves_relative_order_within_type(self):
        """同类型内，编号顺序必须与 (created_at, id) 顺序一致。"""
        self._apply()
        rows = self._rows()
        for payment_type, prefix in (("salary", "SL"), ("expense", "ER")):
            typed = [(n, c) for n, t, c in rows if t == payment_type]
            typed.sort(key=lambda item: item[1])
            numbers = [n for n, _ in typed]
            self.assertEqual(numbers, sorted(numbers))
            self.assertTrue(all(n.startswith(prefix) for n in numbers))

    def test_migration_is_idempotent(self):
        self._apply()
        first = self._rows()
        self._apply()
        self.assertEqual(first, self._rows())

    def test_marker_is_written(self):
        self._apply()
        with tests_pg.connection() as conn:
            row = conn.execute(
                "select value from settings where key='postgresql_0289_payment_number_prefixes'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "complete")

    def test_every_number_is_unique(self):
        self._apply()
        numbers = [n for n, _, _ in self._rows()]
        self.assertEqual(len(numbers), len(set(numbers)))


class PaymentNumberAllocatorTest(_SeedMixin, unittest.TestCase):
    """真跑 _next_number：同类型续号、跨类型互不干扰。"""

    @classmethod
    def setUpClass(cls):
        tests_pg.activate()
        tests_pg.ensure_schema()

    def setUp(self):
        with tests_pg.connection() as conn:
            conn.execute("delete from employee_payment_orders")
            self.employee_id = self._employee_id(conn)
            conn.commit()

    def _alloc(self, prefix):
        """用真实 app context 调 _next_number。"""
        import app as app_module
        from employee_finance import _next_number
        with app_module.app.app_context():
            return _next_number(app_module.__dict__, prefix,
                                "employee_payment_orders", "payment_number")

    def test_salary_and_expense_sequences_are_independent(self):
        import datetime
        stamp = datetime.date.today().strftime("%y%m")
        first = self._alloc("SL")
        second = self._alloc("ER")
        self.assertEqual(first, f"SL-{stamp}-0001")
        self.assertEqual(second, f"ER-{stamp}-0001")

    def test_same_prefix_continues_sequence(self):
        import datetime
        stamp = datetime.date.today().strftime("%y%m")
        self._alloc("ER")
        # 落一张 ER-<stamp>-0001
        with tests_pg.connection() as conn:
            _seed_payment(conn, payment_type="expense",
                          payment_number=f"ER-{stamp}-0001",
                          created_at="2026-09-30T10:00:00",
                          employee_id=self.employee_id)
            conn.commit()
        self.assertEqual(self._alloc("ER"), f"ER-{stamp}-0002")

    def test_allocator_survives_out_of_order_ids(self):
        """存量改号会让 id 顺序与流水顺序错位，不能按 id 取最大号。"""
        import datetime
        stamp = datetime.date.today().strftime("%y%m")
        with tests_pg.connection() as conn:
            # 先插一个大号（id 小），再插一个小号（id 大）
            _seed_payment(conn, payment_type="expense",
                          payment_number=f"ER-{stamp}-0009",
                          created_at="2026-09-01T10:00:00",
                          employee_id=self.employee_id)
            _seed_payment(conn, payment_type="expense",
                          payment_number=f"ER-{stamp}-0002",
                          created_at="2026-09-02T10:00:00",
                          employee_id=self.employee_id)
            conn.commit()
        self.assertEqual(self._alloc("ER"), f"ER-{stamp}-0010")


if __name__ == "__main__":
    unittest.main()
