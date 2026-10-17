"""回归：工单结算修订版本（v0.1.398，迁移 0313）。

规则：
  * 一份结算、多个修订版本；同工单只有一个当前有效版本（is_current）；
  * 创建修订版 = 从当前有效版本复制完整快照（明细 / 费率 / 报销关联 /
    原报销金额 / 客户结算金额 / 不计入确认 / 附件），不重读日报 / 报销来源；
  * 修订版草稿或审核期间，原版本继续有效；
  * 修订版审核通过并确认替代后，原版本标记 superseded 只读留档；
  * 结算统计 / 开票 / 利润表只读当前有效版本，各版本金额不累加；
  * 同一报销明细可出现在同一系列不同版本，禁止跨系列重复计费；
  * 已开票版本必须先处理发票与应收差额，才能创建修订版 / 确认替代。

连接 PostgreSQL invoice_test（见 tests_pg.py）。
"""

import importlib.util
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import settlement_revisions
import settlement_review
import tests_pg

REPO_DIR = Path(__file__).resolve().parent
ORDER_NUMBER = "SO-SETTLE-REV"
DAY = "2026-09-10"
NOW = "2026-09-10T00:00:00"
EMAIL = "settle-rev@example.invalid"
MAPPING = [
    ("MRO Supplies配件及耗材费", "other", "MRO Supplies配件及耗材费"),
    ("Parking Charge停车费", "parking", "Travel Expenses Reimbursement"),
]


class SettlementRevisionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tests_pg.activate()
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_settle_rev_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.api = vars(cls.module)

        with cls.module.app.app_context():
            db = cls.module.db()
            name = db.execute("select current_database()").fetchone()[0]
            if name != "invoice_test":
                raise RuntimeError(f"refusing to run against {name!r}")
            cls._purge(db)
            cls.uid = db.execute(
                "insert into users (name, email, password_hash, role, created_at, "
                "is_active, region_code, country_code, address, default_language, "
                "phone, phone_verified, preferred_communication_language, "
                "communication_languages) values "
                "('Settle Rev', ?, 'x', 'admin', ?, 1, 'US', 'US', '', 'zh', '', 0, 'zh', 'zh') returning id",
                (EMAIL, NOW),
            ).fetchone()["id"]
            cls.project_ids = {}
            for project_name, field, invoice_project in MAPPING:
                db.execute(
                    "insert into expense_settlement_invoice_map "
                    "(expense_project_name, settlement_field, invoice_project_name, created_at) "
                    "values (?,?,?,?) on conflict (expense_project_name) do nothing",
                    (project_name, field, invoice_project, NOW),
                )
                row = db.execute("select id from projects where name = ?", (project_name,)).fetchone()
                cls.project_ids[project_name] = (
                    row["id"] if row
                    else db.execute(
                        "insert into projects (name, created_at) values (?,?) returning id",
                        (project_name, NOW),
                    ).fetchone()["id"]
                )
            cls.order_id = db.execute(
                "insert into service_orders (order_number, client_name, site_address, "
                "client_order_number, status, created_by, created_at, geocode_status, "
                "region_code, country_code) values (?,?,?,?,'in_progress',?,?,"
                "'pending','US','US') returning id",
                (ORDER_NUMBER, "Settle Rev Client", "1 Rev St", "PO-REV", cls.uid, NOW),
            ).fetchone()["id"]
            db.commit()

    @classmethod
    def _purge(cls, db):
        old = db.execute(
            "select id from service_orders where order_number = ?", (ORDER_NUMBER,)
        ).fetchone()
        if old:
            order_id = old["id"]
            db.execute(
                "delete from customer_reimbursement_expense_ignores where "
                "customer_reimbursement_id in (select id from customer_reimbursements "
                "where series_id in (select id from customer_reimbursement_series "
                "where service_order_id = ?))",
                (order_id,),
            )
            db.execute(
                "delete from customer_reimbursement_expense_links where "
                "customer_reimbursement_id in (select id from customer_reimbursements "
                "where series_id in (select id from customer_reimbursement_series "
                "where service_order_id = ?))",
                (order_id,),
            )
            db.execute(
                "delete from customer_reimbursement_attachments where customer_reimbursement_id in "
                "(select id from customer_reimbursements where series_id in "
                "(select id from customer_reimbursement_series where service_order_id = ?))",
                (order_id,),
            )
            db.execute(
                "delete from customer_reimbursement_items where customer_reimbursement_id in "
                "(select id from customer_reimbursements where series_id in "
                "(select id from customer_reimbursement_series where service_order_id = ?))",
                (order_id,),
            )
            db.execute(
                "delete from customer_reimbursements where series_id in "
                "(select id from customer_reimbursement_series where service_order_id = ?)",
                (order_id,),
            )
            db.execute(
                "delete from customer_reimbursement_series where service_order_id = ?",
                (order_id,),
            )
            db.execute(
                "delete from expense_items where expense_id in "
                "(select id from expenses where service_order_id = ?)",
                (order_id,),
            )
            db.execute("delete from expenses where service_order_id = ?", (order_id,))
            db.execute("delete from service_orders where id = ?", (order_id,))
        db.execute("delete from users where email = ?", (EMAIL,))
        db.commit()

    @classmethod
    def tearDownClass(cls):
        with cls.module.app.app_context():
            cls._purge(cls.module.db())
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "delete from customer_reimbursement_expense_ignores where "
                "customer_reimbursement_id in (select id from customer_reimbursements "
                "where service_order_id = ?)",
                (self.order_id,),
            )
            db.execute(
                "delete from customer_reimbursement_expense_links where "
                "customer_reimbursement_id in (select id from customer_reimbursements "
                "where service_order_id = ?)",
                (self.order_id,),
            )
            db.execute(
                "delete from customer_reimbursement_attachments where customer_reimbursement_id in "
                "(select id from customer_reimbursements where service_order_id = ?)",
                (self.order_id,),
            )
            db.execute(
                "delete from customer_reimbursement_items where customer_reimbursement_id in "
                "(select id from customer_reimbursements where service_order_id = ?)",
                (self.order_id,),
            )
            db.execute("delete from customer_reimbursements where service_order_id = ?", (self.order_id,))
            db.execute(
                "delete from customer_reimbursement_series where service_order_id = ?",
                (self.order_id,),
            )
            db.execute("delete from expenses where service_order_id = ?", (self.order_id,))
            db.commit()

    # ───────────────────────────── 工具 ─────────────────────────────

    def add_expense(self, number, project_name, amount, vehicle=None):
        with self.module.app.app_context():
            db = self.module.db()
            expense_id = db.execute(
                "insert into expenses (service_order_id, expense_number, project, "
                "expense_date, amount, currency, status, created_by, created_at, "
                "updated_at, project_id, payout_status, beneficiary_id) "
                "values (?,?,?,?,?,'USD','approved',?,?,?,?,'paid',?) returning id",
                (self.order_id, number, project_name, DAY, amount, self.uid, NOW, NOW,
                 self.project_ids[project_name], self.uid),
            ).fetchone()["id"]
            item_id = db.execute(
                "insert into expense_items (expense_id, project_id, project, amount, "
                "description, sort_order, fuel_vehicle_type, line_key) "
                "values (?,?,?,?,'rev',0,?,?) returning id",
                (expense_id, self.project_ids[project_name], project_name, amount,
                 vehicle, f"k-{number}"),
            ).fetchone()["id"]
            db.commit()
            return item_id

    def make_settlement(self, **overrides):
        with self.module.app.app_context():
            db = self.module.db()
            series_id = db.execute(
                "insert into customer_reimbursement_series (service_order_id, created_at) "
                "values (?,?) returning id",
                (self.order_id, NOW),
            ).fetchone()["id"]
            fields = {
                "series_id": series_id, "service_order_id": self.order_id,
                "file_name": "f.pdf", "stored_filename": "f.pdf",
                "created_by": self.uid, "created_at": NOW, "status": "approved",
                "is_current": True, "version_no": 1,
                "expense_selection_mode": "manual_review",
            }
            fields.update(overrides)
            columns = ", ".join(fields)
            placeholders = ", ".join("?" for _ in fields)
            settlement_id = db.execute(
                f"insert into customer_reimbursements ({columns}) values ({placeholders}) returning id",
                tuple(fields.values()),
            ).fetchone()["id"]
            db.commit()
            return settlement_id

    def link_expense(self, settlement_id, expense_item_id, settled=None, reason=""):
        with self.module.app.app_context():
            db = self.module.db()
            full = db.execute(
                "select amount from expense_items where id = ?", (expense_item_id,)
            ).fetchone()["amount"]
            db.execute(
                "insert into customer_reimbursement_expense_links "
                "(customer_reimbursement_id, expense_item_id, amount_snapshot, project_snapshot, "
                "expense_status_snapshot, settled_amount, adjustment_reason, selected_by, selected_at) "
                "values (?,?,?,'pt','approved',?,?,?,?)",
                (settlement_id, expense_item_id, float(full), settled, reason, self.uid, NOW),
            )
            db.commit()

    def ignore_expense(self, settlement_id, expense_item_id, reason="甲方不认"):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "insert into customer_reimbursement_expense_ignores "
                "(customer_reimbursement_id, expense_item_id, reason, ignored_by, ignored_at) "
                "values (?,?,?,?,?)",
                (settlement_id, expense_item_id, reason, self.uid, NOW),
            )
            db.commit()

    def row(self, settlement_id):
        with self.module.app.app_context():
            return self.module.db().execute(
                "select * from customer_reimbursements where id = ?", (settlement_id,)
            ).fetchone()

    def create_revision(self, settlement_id, reason="甲方要求调减"):
        with self.module.app.test_request_context():
            self.module.g.user = {"id": self.uid, "name": "Settle Rev", "role": "admin"}
            revision = settlement_revisions.create_customer_reimbursement_revision(
                self.api, settlement_id, reason
            )
            self.module.db().commit()
            return revision

    def promote(self, settlement_id):
        with self.module.app.test_request_context():
            self.module.g.user = {"id": self.uid, "name": "Settle Rev", "role": "admin"}
            revision = settlement_revisions.promote_customer_reimbursement_revision(
                self.api, settlement_id
            )
            self.module.db().commit()
            return revision

    # ───────────────────── 1. 创建修订版复制快照 ─────────────────────

    def test_revision_copies_full_snapshot_without_rereading_sources(self):
        expense_item_id = self.add_expense("EX-REV-1", "MRO Supplies配件及耗材费", 9800.0)
        other_item_id = self.add_expense("EX-REV-2", "Parking Charge停车费", 300.0)
        v1 = self.make_settlement(total_amount=9800.0, other_total=9800.0)
        self.link_expense(v1, expense_item_id, settled=8000.0, reason="甲方不承担工具服装")
        self.link_expense(v1, other_item_id)
        self.ignore_expense(v1, other_item_id, "整笔不进入")

        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "insert into customer_reimbursement_items (customer_reimbursement_id, worker_name, "
                "project_date, other, auto_other, total, sort_order) values (?,?,?,?,?,?,?)",
                (v1, "W", DAY, 8000.0, 9800.0, 8000.0, 0),
            )
            db.commit()

        # 来源变更：改报销金额 —— 修订版不得跟随。
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("update expense_items set amount = 20000 where id = ?", (expense_item_id,))
            db.commit()

        revision = self.create_revision(v1, "甲方审核后调减")

        self.assertEqual(revision["version_no"], 2)
        self.assertFalse(revision["is_current"])
        self.assertEqual(revision["status"], "draft")
        self.assertTrue(revision["sources_frozen"])
        self.assertEqual(revision["supersedes_id"], v1)
        self.assertEqual(revision["revision_reason"], "甲方审核后调减")
        self.assertEqual(revision["series_id"], self.row(v1)["series_id"])

        # 快照复制：明细 / 关联（含客户结算金额与原因）/ 忽略确认
        with self.module.app.app_context():
            db = self.module.db()
            items = db.execute(
                "select * from customer_reimbursement_items where customer_reimbursement_id = ?",
                (revision["id"],),
            ).fetchall()
            links = db.execute(
                "select * from customer_reimbursement_expense_links where customer_reimbursement_id = ? "
                "order by expense_item_id",
                (revision["id"],),
            ).fetchall()
            ignores = db.execute(
                "select * from customer_reimbursement_expense_ignores where customer_reimbursement_id = ?",
                (revision["id"],),
            ).fetchall()
        self.assertEqual(len(items), 1, "结算明细必须照抄")
        self.assertEqual(len(links), 2, "报销关联必须照抄")
        self.assertEqual(len(ignores), 1, "不计入确认必须照抄")
        link_by_item = {row["expense_item_id"]: row for row in links}
        self.assertEqual(float(link_by_item[expense_item_id]["settled_amount"]), 8000.0)
        self.assertEqual(link_by_item[expense_item_id]["adjustment_reason"], "甲方不承担工具服装")

        # 不重读来源：来源金额已改成 20000，修订版合计仍按快照重算（8000），
        # 既不跟随最新来源（20000），也不回到原报销额（9800）。
        with self.module.app.app_context():
            self.module.update_customer_reimbursement_totals(revision["id"])
            row = self.module.db().execute(
                "select total_amount, mro_supplies_total from customer_reimbursements where id = ?",
                (revision["id"],),
            ).fetchone()
        self.assertEqual(float(row["total_amount"]), 8000.0, "修订版不得跟随最新来源金额")
        self.assertEqual(float(row["mro_supplies_total"]), 8000.0, "MRO 快照列按客户结算金额")

    # ───────────────────── 2. 原版本在草稿期间继续有效 ─────────────────────

    def test_original_stays_current_until_promoted(self):
        v1 = self.make_settlement()
        revision = self.create_revision(v1)
        self.assertTrue(self.row(v1)["is_current"])
        self.assertFalse(self.row(revision["id"])["is_current"])

        with self.module.app.app_context():
            current = self.module.latest_customer_reimbursement(self.order_id)
        self.assertEqual(current["id"], v1, "草稿修订版不得抢位成为有效版本")

    # ───────────────────── 3. 校验 ─────────────────────

    def test_revision_requires_reason_and_approved_current(self):
        v1 = self.make_settlement()
        with self.assertRaises(ValueError):
            self.create_revision(v1, "  ")
        # 草稿（仍是当前有效版本）不能建修订版：直接编辑即可
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "update customer_reimbursements set status = 'draft' where id = ?", (v1,)
            )
            db.commit()
        with self.assertRaises(ValueError):
            self.create_revision(v1, "草稿不能建修订版")
        # 恢复审核通过，后续用例不受影响
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "update customer_reimbursements set status = 'approved' where id = ?", (v1,)
            )
            db.commit()

    def test_frozen_revision_skips_source_gate(self):
        """修订版（sources_frozen）提交 / 开票不再被快照后新审核的报销硬拦。"""
        v1 = self.make_settlement()
        revision = self.create_revision(v1, "调减")
        # 快照后新审核一笔报销：不在修订版的 links / auto_expense_sources 里
        self.add_expense("EX-REV-GATE", "Parking Charge停车费", 500.0)
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select * from customer_reimbursements where id = ?", (revision["id"],)
            ).fetchone()
            self.assertTrue(row["sources_frozen"])
            self.assertIsNone(
                self.module.customer_reimbursement_gate_error(row),
                "修订版不得因快照外的新报销被硬拦",
            )
            current = self.module.db().execute(
                "select * from customer_reimbursements where id = ?", (v1,)
            ).fetchone()
            self.assertIsNotNone(
                self.module.customer_reimbursement_gate_error(current),
                "普通结算仍必须硬拦未计入的已审核报销",
            )

    def test_frozen_revision_blocks_source_sync_and_ignore(self):
        """当前有效版本若是冻结修订版（草稿状态），不能「计入并重算」，
        也不能把快照外新审核的报销标记为无需计入。"""
        v1 = self.make_settlement()
        revision = self.create_revision(v1, "调减")
        gate_item_id = self.add_expense("EX-REV-GATE", "Parking Charge停车费", 500.0)
        # 让修订版成为当前有效版本并回到草稿（替代后重置的场景）
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "update customer_reimbursements set is_current = false where id = ?", (v1,)
            )
            db.execute(
                "update customer_reimbursements set is_current = true, status = 'draft' "
                "where id = ?",
                (revision["id"],),
            )
            db.commit()

        with self.module.app.test_request_context():
            self.module.g.user = {"id": self.uid, "name": "Settle Rev", "role": "admin"}
            self.module.sync_customer_reimbursement_sources(self.order_id)
            self.module.db().rollback()
            links = self.module.db().execute(
                "select count(*) as count from customer_reimbursement_expense_links "
                "where customer_reimbursement_id = ?",
                (revision["id"],),
            ).fetchone()["count"]
            ignores = self.module.db().execute(
                "select count(*) as count from customer_reimbursement_expense_ignores "
                "where customer_reimbursement_id = ?",
                (revision["id"],),
            ).fetchone()["count"]
        self.assertEqual(links, 0, "修订版不得自动补链快照外报销")
        self.assertEqual(ignores, 0, "修订版不得把快照外报销标记为无需计入")

        # 「标记无需计入」路由同样被拦（flash + redirect，不落库）
        with self.module.app.test_request_context(
            data={"expense_item_id": str(gate_item_id), "reason": "试探"}
        ):
            self.module.g.user = {"id": self.uid, "name": "Settle Rev", "role": "admin"}
            self.module.ignore_customer_reimbursement_source(self.order_id)
            self.module.db().rollback()
            ignores_after = self.module.db().execute(
                "select count(*) as count from customer_reimbursement_expense_ignores "
                "where customer_reimbursement_id = ?",
                (revision["id"],),
            ).fetchone()["count"]
        self.assertEqual(ignores_after, 0, "标记无需计入必须被冻结语义拦下")

    # ───────────────────── 4. 替代生效 ─────────────────────

    def test_promote_marks_original_superseded_and_read_only(self):
        v1 = self.make_settlement()
        revision = self.create_revision(v1, "调减")
        with self.assertRaises(ValueError):
            self.promote(revision["id"])  # 未审核通过不能替代

        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "update customer_reimbursements set status = 'approved' where id = ?",
                (revision["id"],),
            )
            db.commit()
        promoted = self.promote(revision["id"])

        self.assertTrue(promoted["is_current"])
        original = self.row(v1)
        self.assertFalse(original["is_current"])
        self.assertEqual(original["status"], "superseded")
        self.assertTrue(original["superseded_at"])
        self.assertEqual(promoted["superseded_at"], None)

        with self.module.app.app_context():
            current = self.module.latest_customer_reimbursement(self.order_id)
        self.assertEqual(current["id"], revision["id"])
        # 只读：被替代版本不再出现在可编辑集合里
        self.assertNotIn(original["status"], {"draft", "returned"})

    # ───────────────────── 5. 同系列可重复，跨系列禁止 ─────────────────────

    def test_same_series_allows_duplicate_expense_across_versions(self):
        expense_item_id = self.add_expense("EX-REV-3", "Parking Charge停车费", 120.0)
        v1 = self.make_settlement()
        self.link_expense(v1, expense_item_id)
        revision = self.create_revision(v1)
        candidates = {
            expense_item_id: {
                "expense_item_id": expense_item_id,
                "amount": 120.0,
                "selectable": True,
                "status": "approved",
                "project_name": "Parking Charge停车费",
                "expense_number": "EX-REV-3",
            }
        }
        with self.module.app.test_request_context():
            self.module.g.user = {"id": self.uid, "name": "Settle Rev", "role": "admin"}
            # 同一系列的另一版本重复链接同一明细：允许
            settlement_review.replace_expense_links(
                self.api, revision["id"], candidates, {expense_item_id}
            )
            self.module.db().commit()
            count = self.module.db().execute(
                "select count(*) as count from customer_reimbursement_expense_links "
                "where expense_item_id = ? and customer_reimbursement_id = ?",
                (expense_item_id, revision["id"]),
            ).fetchone()["count"]
        self.assertEqual(count, 1)

        # 另一条工单的独立结算：禁止重复计费
        with self.module.app.app_context():
            db = self.module.db()
            other_order = db.execute(
                "insert into service_orders (order_number, client_name, site_address, "
                "client_order_number, status, created_by, created_at, geocode_status, "
                "region_code, country_code) values ('SO-REV-OTHER','c','a','p','in_progress',?,?,"
                "'pending','US','US') returning id",
                (self.uid, NOW),
            ).fetchone()["id"]
            other_series = db.execute(
                "insert into customer_reimbursement_series (service_order_id, created_at) "
                "values (?,?) returning id",
                (other_order, NOW),
            ).fetchone()["id"]
            other_settlement = db.execute(
                "insert into customer_reimbursements (series_id, service_order_id, file_name, "
                "stored_filename, created_by, created_at, status, is_current, version_no) "
                "values (?,?,'o.pdf','o.pdf',?,?,'draft',true,1) returning id",
                (other_series, other_order, self.uid, NOW),
            ).fetchone()["id"]
            db.commit()
        with self.module.app.test_request_context():
            self.module.g.user = {"id": self.uid, "name": "Settle Rev", "role": "admin"}
            with self.assertRaises(ValueError):
                settlement_review.replace_expense_links(
                    self.api, other_settlement, candidates, {expense_item_id}
                )
            self.module.db().rollback()

    # ───────────────────── 6. 版本列表 ─────────────────────

    def test_settlement_versions_lists_series_in_order(self):
        v1 = self.make_settlement()
        revision = self.create_revision(v1, "第一次调减")
        with self.module.app.app_context():
            versions = settlement_revisions.settlement_versions(self.api, self.order_id)
        self.assertEqual([row["version_no"] for row in versions], [1, 2])
        self.assertEqual(versions[0]["id"], v1)
        self.assertEqual(versions[1]["id"], revision["id"])
        self.assertTrue(versions[0]["is_current"])


if __name__ == "__main__":
    unittest.main()
