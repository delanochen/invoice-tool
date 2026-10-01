"""Phase 3D：新 Expense 显式 business_purpose + 历史/新数据分类口径分离。

覆盖：草稿可空、提交必填、编辑可保存/更新、detail 展示（空显示 Not recorded）、
新数据只认 business_purpose（reviewed_by / description 不再替代）、
历史 legacy_backfill 口径固定不变、已有快照不被重写、查看历史单不强制补录、
审批不自动伪造业务用途。
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


class ExpenseBusinessPurposeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("expense_purpose_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="expense-purpose-secret")
        cls.module.app.template_folder = str(ROOT / "templates")
        cls.module.app.static_folder = str(ROOT / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in (
                "messages", "employee_payment_components", "payment_order_events",
                "payment_order_sources", "employee_payment_orders",
                "worker_tax_status_history", "expense_attachments", "expense_items",
                "expenses", "expense_save_tokens", "service_orders", "audit_logs",
                "users", "projects",
            ):
                db.execute(f"delete from {table}")
            self.admin_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,created_at)"
                " values ('Admin','bp-admin@test.invalid','unused','admin',1,?)",
                (self.module.now(),),
            ).lastrowid
            self.worker_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,created_at)"
                " values ('Claimant','bp-claimant@test.invalid','unused','employee',1,?)",
                (self.module.now(),),
            ).lastrowid
            self.order_id = db.execute(
                "insert into service_orders (order_number,client_name,site_address,client_order_number,"
                "start_date,created_by,created_at) values ('SO-BP','C','S','C1','2026-08-01',?,?)",
                (self.admin_id, self.module.now()),
            ).lastrowid
            self.project_id = db.execute(
                "insert into projects (name,default_amount,tax_rate,is_active,created_at,project_type)"
                " values ('Hotel',0,0,1,?,'expense')",
                (self.module.now(),),
            ).lastrowid
            # 默认权限矩阵对 admin 未开 expenses.create（已知缺口），此处按现有
            # 测试的惯例在运行时打开，聚焦业务规则本身。
            db.execute(
                "update role_action_permissions set is_enabled=1 where role='admin'"
                " and resource_key='expenses'")
            db.commit()
        self.http = self.module.app.test_client()
        with self.http.session_transaction() as session:
            session["user_id"] = self.admin_id
        self._token_seq = 0

    def tearDown(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in (
                "messages", "employee_payment_components", "payment_order_events",
                "payment_order_sources", "employee_payment_orders",
                "worker_tax_status_history", "expense_attachments", "expense_items",
                "expenses", "expense_save_tokens", "service_orders", "audit_logs",
                "users", "projects",
            ):
                db.execute(f"delete from {table}")
            db.commit()

    # -- helpers ---------------------------------------------------------
    def _token(self):
        self._token_seq += 1
        return f"bp-token-{self._token_seq}"

    def _form(self, action="save", business_purpose=None, description="备注：买了保险丝",
              amount="100.00"):
        data = {
            "save_token": self._token(),
            "beneficiary_id": str(self.worker_id),
            "expense_date": "2026-08-05",
            "project_id": [str(self.project_id)],
            "item_line_key": ["line-1"],
            "item_amount": [amount],
            "item_description": ["房间费"],
            "description": description,
            "action": action,
        }
        if business_purpose is not None:
            data["business_purpose"] = business_purpose
        return data

    def _create(self, **kwargs):
        return self.http.post(
            f"/service-orders/{self.order_id}/expenses/new", data=self._form(**kwargs))

    def _expense_row(self):
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select *,coalesce(beneficiary_id,created_by) employee_id"
                " from expenses order by id desc limit 1").fetchone()
            return dict(row) if row else None

    def _expense_count(self):
        with self.module.app.app_context():
            return self.module.db().execute("select count(*) from expenses").fetchone()[0]

    def _seed_tax_status(self, employee_id):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "insert into worker_tax_status_history (employee_id,tax_status,effective_from,"
                "effective_to,notes,created_by,created_at) values (?, '1099', '2026-01-01', null,"
                " 'test', ?, ?)", (employee_id, self.admin_id, self.module.now()))
            db.commit()

    def _receipt(self, expense_id, line_key="line-1"):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "insert into expense_attachments (expense_id,original_filename,stored_filename,"
                "content_type,uploaded_by,uploaded_at,expense_item_key) values (?,?,?,?,?,?,?)",
                (expense_id, "r.pdf", "r.pdf", "application/pdf", self.admin_id,
                 self.module.now(), line_key))
            db.commit()

    def _approve(self, expense_id):
        return self.http.post(f"/expenses/{expense_id}/approve")

    def _components(self, payment_id):
        with self.module.app.app_context():
            return [dict(r) for r in self.module.db().execute(
                "select * from employee_payment_components where payment_order_id=?"
                " and superseded_at is null order by id", (payment_id,)).fetchall()]

    def _payment(self):
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select * from employee_payment_orders order by id desc limit 1").fetchone()
            return dict(row) if row else None

    def _run_tool(self, *flags):
        result = subprocess.run(
            [sys.executable, str(TOOL), *flags],
            capture_output=True, text=True, cwd=str(ROOT), timeout=300,
            env=os.environ.copy())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    # -- tests -----------------------------------------------------------
    def test_draft_can_be_saved_without_business_purpose(self):
        """① 草稿允许 business_purpose 为空。"""
        response = self._create(action="save")
        self.assertEqual(response.status_code, 302)
        expense = self._expense_row()
        self.assertIsNotNone(expense)
        self.assertEqual(expense["status"], "draft")
        self.assertFalse(expense["business_purpose"])

    def test_submit_without_business_purpose_is_rejected(self):
        """② 提交审核缺业务用途 → 拒绝，整单不落库（不产生 submitted 数据）。"""
        response = self._create(action="submit")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._expense_count(), 0)

    def test_submit_with_business_purpose_succeeds(self):
        """③ 有业务用途 → 正常提交并持久化。"""
        response = self._create(action="submit", business_purpose="Wolf Tank 工单现场更换保险丝")
        self.assertEqual(response.status_code, 302)
        expense = self._expense_row()
        self.assertEqual(expense["status"], "submitted")
        self.assertEqual(expense["business_purpose"], "Wolf Tank 工单现场更换保险丝")

    def test_edit_saves_and_updates_business_purpose(self):
        """④ 编辑入口能保存并更新 business_purpose。"""
        self._create(action="save")
        expense = self._expense_row()
        self.assertFalse(expense["business_purpose"])
        response = self.http.post(
            f"/expenses/{expense['id']}/edit",
            data=self._form(action="save", business_purpose="第一次填写"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._expense_row()["business_purpose"], "第一次填写")
        response = self.http.post(
            f"/expenses/{expense['id']}/edit",
            data=self._form(action="submit", business_purpose="修改后重新提交"))
        self.assertEqual(response.status_code, 302)
        updated = self._expense_row()
        self.assertEqual(updated["business_purpose"], "修改后重新提交")
        self.assertEqual(updated["status"], "submitted")

    def test_edit_submit_without_purpose_does_not_change_status(self):
        """②b 历史/草稿单重新提交缺业务用途 → 保持草稿，不进入审核。"""
        self._create(action="save")
        expense = self._expense_row()
        self.http.post(f"/expenses/{expense['id']}/edit", data=self._form(action="submit"))
        self.assertEqual(self._expense_row()["status"], "draft")

    def test_detail_shows_purpose_and_not_recorded(self):
        """⑤ detail 展示业务用途；历史单为空显示 Not recorded（不伪造文本）。"""
        self._create(action="save", business_purpose="业务用途文本 A")
        expense = self._expense_row()
        page = self.http.get(f"/expenses/{expense['id']}")
        self.assertEqual(page.status_code, 200)
        body = page.data.decode("utf-8")
        self.assertIn("业务用途", body)
        self.assertIn("业务用途文本 A", body)
        # 历史单：business_purpose 为 NULL，仅查看不强制补录
        with self.module.app.app_context():
            self.module.db().execute(
                "update expenses set business_purpose=null where id=?", (expense["id"],))
            self.module.db().commit()
        page = self.http.get(f"/expenses/{expense['id']}")
        body = page.data.decode("utf-8")
        self.assertIn("Not recorded", body)
        self.assertFalse(self._expense_row()["business_purpose"])

    def test_new_expense_purpose_plus_receipt_is_accountable(self):
        """⑥ 新数据：purpose + 凭证 → accountable_reimbursement。"""
        self._seed_tax_status(self.worker_id)
        self._create(action="submit", business_purpose="现场维修必需采购")
        expense = self._expense_row()
        self._receipt(expense["id"])
        self._approve(expense["id"])
        payment = self._payment()
        lines = self._components(payment["id"])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["tax_category"], "accountable_reimbursement")
        self.assertTrue(lines[0]["substantiated"])
        self.assertEqual(lines[0]["tax_status_snapshot"], "1099")

    def test_reviewed_by_no_longer_substantiates_new_expense(self):
        """⑦ 审核通过（reviewed_by）不再让新数据自动 substantiated。"""
        self._seed_tax_status(self.worker_id)
        # 绕过提交校验直接构造历史形态的 submitted 单：无 business_purpose、
        # 有 description、审核通过后 reviewed_by 必然非空。
        with self.module.app.app_context():
            db = self.module.db()
            expense_id = db.execute(
                "insert into expenses (service_order_id,expense_number,project_id,project,"
                "expense_date,amount,currency,description,status,created_by,created_at,"
                "updated_at,beneficiary_id) values (?, 'EX-BP-1', ?, 'Hotel', '2026-08-05',"
                " 100.00, 'USD', '买了保险丝', 'submitted', ?, ?, ?, ?)",
                (self.order_id, self.project_id, self.worker_id, self.module.now(),
                 self.module.now(), self.worker_id)).lastrowid
            db.execute(
                "insert into expense_items (expense_id,line_key,project_id,project,amount,"
                "description,sort_order) values (?,?,?,?,?,?,?)",
                (expense_id, "line-1", self.project_id, "Hotel", "100.00", "保险丝", 0))
            db.commit()
        self._receipt(expense_id)
        self._approve(expense_id)
        expense = self._expense_row()
        self.assertIsNotNone(expense["reviewed_by"])  # 审核动作已发生
        self.assertFalse(expense["business_purpose"])
        lines = self._components(self._payment()["id"])
        self.assertEqual(lines[0]["tax_category"], "tax_review_required")
        self.assertFalse(lines[0]["substantiated"])

    def test_description_does_not_substitute_purpose(self):
        """⑧ description 只是备注，不能替代 business_purpose。"""
        from invoice_tool.payroll.tax import expense_purpose_ok
        expense = {"business_purpose": None, "description": "Bought replacement fuse",
                   "reviewed_by": 7}
        self.assertFalse(expense_purpose_ok(expense, "current"))
        self.assertTrue(expense_purpose_ok(expense, "legacy_backfill"))
        expense = {"business_purpose": "Replacement fuse for WT-001", "description": "",
                   "reviewed_by": None}
        self.assertTrue(expense_purpose_ok(expense, "current"))
        with self.assertRaises(ValueError):
            expense_purpose_ok(expense, "unknown-mode")

    def test_legacy_mode_keeps_backfill_rule_unchanged(self):
        """⑨⑪ 同一张历史单：legacy_backfill=accountable，current=review；
        回填工具固定走 legacy，重跑结果不变。"""
        self._seed_tax_status(self.worker_id)
        with self.module.app.app_context():
            db = self.module.db()
            expense_id = db.execute(
                "insert into expenses (service_order_id,expense_number,project_id,project,"
                "expense_date,amount,currency,description,status,created_by,created_at,"
                "updated_at,beneficiary_id,reviewed_by) values (?, 'EX-LEGACY', ?, 'Hotel',"
                " '2026-08-05', 100.00,'USD','历史备注','approved',?,?,?,?,?)",
                (self.order_id, self.project_id, self.worker_id, self.module.now(),
                 self.module.now(), self.worker_id, self.admin_id)).lastrowid
            db.execute(
                "insert into expense_items (expense_id,line_key,project_id,project,amount,"
                "description,sort_order) values (?,?,?,?,?,?,?)",
                (expense_id, "line-1", self.project_id, "Hotel", "100.00", "房间费", 0))
            db.commit()
        self._receipt(expense_id)
        with self.module.app.app_context():
            db = self.module.db()
            payment_id = db.execute(
                """
                insert into employee_payment_orders
                (payment_number,employee_id,payment_type,status,currency,gross_amount,
                 advance_offset,other_adjustment,net_amount,description,source_type,source_id,
                 source_number,source_key,sync_source,sync_status,created_by,created_at,updated_at)
                values ('ER-LEGACY-1',?,'expense','draft','USD',100.00,0,0,100.00,'历史 ER',
                        'expense',?,'EX-LEGACY','expense:?','manual','local',?,'t','t')
                """.replace("'expense:?'", "'expense:%s'" % expense_id),
                (self.worker_id, expense_id, self.admin_id)).lastrowid
            db.commit()
        with self.module.app.app_context():
            api = self.module.__dict__
            db = api["db"]()
            expense = db.execute(
                "select *,coalesce(beneficiary_id,created_by) employee_id from expenses"
                " where id=?", (expense_id,)).fetchone()
            import employee_finance as EF
            legacy_comps, _ = EF.expense_payment_components(
                api, expense, payment_id,
                classification_mode=EF.EXPENSE_CLASSIFICATION_LEGACY_BACKFILL)
            current_comps, _ = EF.expense_payment_components(api, expense, payment_id)
        self.assertEqual(legacy_comps[0]["tax_category"], "accountable_reimbursement")
        self.assertEqual(current_comps[0]["tax_category"], "tax_review_required")

        # 回填工具：走 legacy → accountable；重跑不产生第二套
        out = self._run_tool("--commit")
        self.assertIn("backfilled: 1", out)
        lines = self._components(payment_id)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["source_type"], "historical_expense_recompute")
        self.assertEqual(lines[0]["tax_category"], "accountable_reimbursement")
        out = self._run_tool("--commit")
        self.assertIn("candidate ER rows: 0", out)
        self.assertEqual(len(self._components(payment_id)), 1)
        self.assertEqual(dec(self._components(payment_id)[0]["amount"]), Decimal("100.00"))

    def test_existing_snapshot_is_never_rewritten(self):
        """⑫ 已有快照的 ER：回填工具与查看都不重写、不新增。"""
        self._seed_tax_status(self.worker_id)
        self._create(action="submit", business_purpose="新数据业务用途")
        expense = self._expense_row()
        self._receipt(expense["id"])
        self._approve(expense["id"])
        payment = self._payment()
        before = self._components(payment["id"])
        self.assertEqual(before[0]["source_type"], "expense_item")
        self.http.get(f"/expenses/{expense['id']}")
        out = self._run_tool("--commit")
        self.assertIn("candidate ER rows: 0", out)
        after = self._components(payment["id"])
        self.assertEqual(len(before), len(after))
        self.assertEqual([str(x) for x in before], [str(x) for x in after])

    def test_approval_never_fabricates_business_purpose(self):
        """⑭ 审批流程不会自动补写 business_purpose（no auto-fill）。"""
        self._seed_tax_status(self.worker_id)
        with self.module.app.app_context():
            db = self.module.db()
            expense_id = db.execute(
                "insert into expenses (service_order_id,expense_number,project_id,project,"
                "expense_date,amount,currency,description,status,created_by,created_at,"
                "updated_at,beneficiary_id) values (?, 'EX-NOFILL', ?, 'Hotel', '2026-08-05',"
                " 60.00,'USD','旧备注','submitted',?,?,?,?)",
                (self.order_id, self.project_id, self.worker_id, self.module.now(),
                 self.module.now(), self.worker_id)).lastrowid
            db.execute(
                "insert into expense_items (expense_id,line_key,project_id,project,amount,"
                "description,sort_order) values (?,?,?,?,?,?,?)",
                (expense_id, "line-1", self.project_id, "Hotel", "60.00", "房间费", 0))
            db.commit()
        self._receipt(expense_id)
        self._approve(expense_id)
        expense = self._expense_row()
        self.assertFalse(expense["business_purpose"])
        payment = self._payment()
        lines = self._components(payment["id"])
        self.assertEqual(lines[0]["tax_category"], "tax_review_required")
        # 金额/状态/批次关系不受分类影响
        self.assertEqual(dec(payment["gross_amount"]), Decimal("60.00"))
        self.assertEqual(dec(payment["net_amount"]), Decimal("60.00"))
        self.assertIsNone(payment["batch_id"])


if __name__ == "__main__":
    unittest.main()
