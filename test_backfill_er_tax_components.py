"""Phase 3C：历史 ER 组件回填工具回归（在 invoice_test 上完整模拟）。

覆盖：dry-run 不写数据 / --commit 写入 provenance=historical_expense_recompute /
三项合计落库且 sum==gross / 重复运行幂等跳过 / 排除项（源 expense 缺失）永不
生成组件 / 金额与状态批次关系不变。
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOL = ROOT / "scripts" / "backfill_er_tax_components.py"


def dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


class BackfillErTaxComponentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("backfill_er_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="backfill-er-secret")
        cls.module.app.template_folder = str(ROOT / "templates")
        cls.module.app.static_folder = str(ROOT / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in (
                "employee_payment_components", "payment_order_events", "payment_order_sources",
                "employee_payment_orders", "worker_tax_status_history",
                "expense_attachments", "expense_items", "expenses",
                "service_orders", "audit_logs", "users", "projects",
            ):
                db.execute(f"delete from {table}")
            self.admin_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,created_at)"
                " values ('Admin','bf-er-admin@test.invalid','unused','admin',1,?)",
                (self.module.now(),),
            ).lastrowid
            self.worker_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,created_at)"
                " values ('Claimant','bf-er-claimant@test.invalid','unused','employee',1,?)",
                (self.module.now(),),
            ).lastrowid
            self.order_id = db.execute(
                "insert into service_orders (order_number,client_name,site_address,client_order_number,"
                "start_date,created_by,created_at) values ('SO-BF-ER','C','S','C1','2026-08-01',?,?)",
                (self.admin_id, self.module.now()),
            ).lastrowid
            self.project_id = db.execute(
                "insert into projects (name,default_amount,tax_rate,is_active,created_at,project_type)"
                " values ('Hotel',0,0,1,?,'expense')",
                (self.module.now(),),
            ).lastrowid
            db.execute(
                "insert into worker_tax_status_history (employee_id,tax_status,effective_from,"
                "effective_to,notes,created_by,created_at) values (?, '1099', '2026-01-01', null,"
                " 'test', ?, ?)", (self.worker_id, self.admin_id, self.module.now()))
            db.commit()

    def tearDown(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in (
                "employee_payment_components", "payment_order_events", "payment_order_sources",
                "employee_payment_orders", "worker_tax_status_history",
                "expense_attachments", "expense_items", "expenses",
                "service_orders", "audit_logs", "users", "projects",
            ):
                db.execute(f"delete from {table}")
            db.commit()

    # -- helpers ---------------------------------------------------------
    def _legacy_expense(self, items, *, expense_number="EX-BF-1", with_receipt=True):
        """构造一张「0292 之前」样式的历史报销：有 items、有凭证、已审核。"""
        with self.module.app.app_context():
            db = self.module.db()
            expense_id = db.execute(
                """
                insert into expenses (service_order_id, expense_number, project_id, project,
                    expense_date, amount, currency, description, status, created_by,
                    created_at, updated_at, beneficiary_id, reviewed_by)
                values (?, ?, ?, 'Hotel', '2026-08-05', 0, 'USD', '历史报销', 'approved',
                        ?, ?, ?, ?, ?)
                """,
                (self.order_id, expense_number, self.project_id, self.worker_id,
                 self.module.now(), self.module.now(), self.worker_id, self.admin_id),
            ).lastrowid
            total = Decimal("0")
            for index, (line_key, amount) in enumerate(items):
                db.execute(
                    "insert into expense_items (expense_id, line_key, project_id, project,"
                    " amount, description, sort_order) values (?,?,?,?,?,?,?)",
                    (expense_id, line_key, self.project_id, "Hotel", amount, "房间费", index),
                )
                total += Decimal(str(amount))
                if with_receipt:
                    db.execute(
                        "insert into expense_attachments (expense_id,original_filename,stored_filename,"
                        "uploaded_by,uploaded_at,expense_item_key) values (?,?,?,?,?,?)",
                        (expense_id, "r.png", "r.png", self.admin_id, self.module.now(), line_key),
                    )
            db.execute("update expenses set amount=? where id=?", (total, expense_id))
            db.commit()
            return expense_id

    def _legacy_er(self, expense_id, gross, *, number="ER-BF-0001", status="draft",
                   source_id=None):
        """直接插入历史 ER（无组件快照），绕开 3A 路由。"""
        with self.module.app.app_context():
            db = self.module.db()
            payment_id = db.execute(
                """
                insert into employee_payment_orders
                (payment_number,employee_id,payment_type,status,currency,gross_amount,
                 advance_offset,other_adjustment,net_amount,description,source_type,source_id,
                 source_number,source_key,sync_source,sync_status,created_by,created_at,updated_at)
                values (?,?, 'expense',?, 'USD',?,0,0,?,'历史 ER','expense',?,
                        'EX-BF-1','expense:?','manual','local',?,'t','t')
                """.replace("'expense:?'", "'expense:%s'" % expense_id),
                (number, self.worker_id, status, gross, gross,
                 expense_id if source_id is None else source_id, self.admin_id),
            ).lastrowid
            db.commit()
            return payment_id

    def _run_tool(self, *flags):
        result = subprocess.run(
            [sys.executable, str(TOOL), *flags],
            capture_output=True, text=True, cwd=str(ROOT), timeout=300,
            env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def _components(self):
        with self.module.app.app_context():
            return [dict(row) for row in self.module.db().execute(
                "select * from employee_payment_components order by id").fetchall()]

    def _payment(self, payment_id):
        with self.module.app.app_context():
            return dict(self.module.db().execute(
                "select * from employee_payment_orders where id=?", (payment_id,)).fetchone())

    # -- tests -----------------------------------------------------------
    def test_dry_run_then_commit_then_idempotent(self):
        expense_id = self._legacy_expense([("line-1", "120.00"), ("line-2", "80.00")])
        payment_id = self._legacy_er(expense_id, "200.00")
        gross_before = Decimal("200.00")
        self.assertEqual(self._components(), [])

        out = self._run_tool()
        self.assertIn("DRY-RUN", out)
        self.assertIn("verified: 1", out)
        self.assertEqual(self._components(), [])  # dry-run 绝不写
        self.assertEqual(dec(self._payment(payment_id)["gross_amount"]), gross_before)

        out = self._run_tool("--commit")
        self.assertIn("backfilled: 1", out)
        lines = self._components()
        self.assertEqual(len(lines), 2)
        self.assertEqual(sum(dec(line["amount"]) for line in lines), gross_before)
        for line in lines:
            self.assertEqual(line["source_type"], "historical_expense_recompute")
            self.assertEqual(line["component_code"], "expense_item")
            self.assertEqual(line["tax_status_snapshot"], "1099")
            self.assertEqual(line["tax_category"], "accountable_reimbursement")
            self.assertTrue(line["substantiated"])
        payment_after = self._payment(payment_id)
        self.assertEqual(dec(payment_after["accountable_reimbursement_total"]), gross_before)
        self.assertEqual(dec(payment_after["taxable_compensation_total"]), Decimal("0"))
        self.assertEqual(dec(payment_after["tax_review_required_total"]), Decimal("0"))
        # 金额 / 状态 / 来源完全不变
        self.assertEqual(dec(payment_after["gross_amount"]), gross_before)
        self.assertEqual(payment_after["status"], "draft")
        self.assertEqual(payment_after["source_type"], "expense")
        self.assertEqual(payment_after["source_id"], expense_id)

        out = self._run_tool("--commit")
        self.assertIn("candidate ER rows: 0", out)
        self.assertIn("skipped_existing (已有生效快照): 1", out)
        self.assertEqual(len(self._components()), 2)

    def test_excluded_er_with_missing_expense_never_gets_components(self):
        """源 expense 已删除的 ER（如 cancelled 的 ER-2609-0098）永不生成组件。"""
        payment_id = self._legacy_er(None, "3894.70", number="ER-BF-0098",
                                     status="cancelled", source_id=999999)
        out = self._run_tool("--commit")
        self.assertIn("excluded metadata_error: 1", out)
        self.assertIn("[EXCLUDED] ER-BF-0098", out)
        self.assertEqual(self._components(), [])
        payment = self._payment(payment_id)
        self.assertEqual(dec(payment["gross_amount"]), Decimal("3894.70"))
        self.assertEqual(payment["status"], "cancelled")
        self.assertEqual(payment["source_id"], 999999)  # 不修 source，不补伪造 expense
        self.assertEqual(dec(payment["taxable_compensation_total"] or 0), Decimal("0"))
        self.assertEqual(dec(payment["accountable_reimbursement_total"] or 0), Decimal("0"))
        self.assertEqual(dec(payment["tax_review_required_total"] or 0), Decimal("0"))

    def test_amount_mismatch_is_reported_and_not_written(self):
        """组件合计 != gross 时该张跳过并计 mismatch，绝不为了闭合改金额。"""
        expense_id = self._legacy_expense([("line-1", "120.00")])
        payment_id = self._legacy_er(expense_id, "150.00")
        out = self._run_tool("--commit")
        self.assertIn("mismatch: 1", out)
        self.assertIn("backfilled: 0", out)
        self.assertEqual(self._components(), [])
        self.assertEqual(dec(self._payment(payment_id)["gross_amount"]), Decimal("150.00"))


if __name__ == "__main__":
    unittest.main()
