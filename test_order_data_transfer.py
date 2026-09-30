"""工单间数据转移（日报 / 报销 / 附件 / 现场照片 / AI 留痕）—— v0.1.327。

覆盖最容易出错、也是以前纯手工最容易漏的三件事：
  1. 日报附件相对路径里嵌了工单号 → 只改库不改磁盘就会 404；
  2. 现场照片要 pictures + thumbnails 两份一起搬（缩略图路径由 relative_path 推导）；
  3. 已被工单结算单引用的日报/报销必须拒绝转移（否则结算单对不上账）；
  4. 工单号支持模糊输入（只记得后几位也能定位），但命中多条时**必须报错列候选、不能猜**
     —— 猜错工单会把日报/报销搬到别的工单去。

本测试连 PostgreSQL invoice_test（见 conftest.py / tests_pg.py）。磁盘相关的
DATA / shared-photos 根目录指向临时目录（通过环境变量在 import app 之前切换）。
"""

import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import order_data_transfer

REPO_DIR = Path(__file__).resolve().parent
SOURCE_NUMBER = "SO-TRANSFER-A"
TARGET_NUMBER = "SO-TRANSFER-B"
DAY = "2026-09-10"
DAY_STAMP = "20260910"
NOW = "2026-09-10T00:00:00"


class OrderDataTransferTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        root = Path(cls.temp_dir.name)
        cls.data_dir = root / "data"
        cls.photos_dir = root / "shared-photos"
        (cls.data_dir / "service-report-attachments").mkdir(parents=True)
        cls.photos_dir.mkdir(parents=True)

        os.environ["INVOICE_DATA_DIR"] = str(cls.data_dir)
        os.environ["SHARED_PHOTOS_DIR"] = str(cls.photos_dir)

        module_path = root / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_transfer_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.api = vars(cls.module)

        with cls.module.app.app_context():
            db = cls.module.db()
            name = db.execute("select current_database()").fetchone()[0]
            if name != "invoice_test":
                raise RuntimeError(f"refusing to run against {name!r}")
            cls.report_attachments_dir = cls.module.REPORT_ATTACHMENTS_DIR
            cls.shared_photos_dir = cls.module.SHARED_PHOTOS_DIR

    @classmethod
    def _restore_env(cls):
        os.environ.pop("INVOICE_DATA_DIR", None)
        os.environ.pop("SHARED_PHOTOS_DIR", None)

    @classmethod
    def tearDownClass(cls):
        # 必须先清环境变量再删临时目录：后面的用例可能再 import 一次 app。
        cls._restore_env()
        cls.temp_dir.cleanup()

    # ─────────────────────────────── 播种 ───────────────────────────────

    def seed(self):
        """每次用例一套全新数据（conftest 会把库恢复到快照，这里只造行和文件）。"""
        with self.module.app.app_context():
            db = self.module.db()
            uid = db.execute(
                "insert into users (name, email, password_hash, role, created_at, "
                "is_active, region_code, country_code, address, default_language, "
                "phone, phone_verified, preferred_communication_language, "
                "communication_languages) values "
                "('Transfer','transfer-admin@example.invalid','x','admin',?,1,'US','US',"
                "'','zh','',0,'zh','zh') returning id",
                (NOW,),
            ).fetchone()["id"]
            self.user_id = uid

            def add_order(number):
                return db.execute(
                    "insert into service_orders (order_number, client_name, site_address, "
                    "client_order_number, status, created_by, created_at, geocode_status, "
                    "region_code, country_code) values (?,?,?,?,'in_progress',?,?,"
                    "'pending','US','US') returning id",
                    (number, "Transfer Client", "1 T St", "PO-T", uid, NOW),
                ).fetchone()["id"]

            self.source_id = add_order(SOURCE_NUMBER)
            self.target_id = add_order(TARGET_NUMBER)

            self.report_id = db.execute(
                "insert into service_reports (service_order_id, report_date, "
                "actual_work_date, created_by, created_at, updated_at) "
                "values (?,?,?,?,?,?) returning id",
                (self.source_id, DAY, DAY, uid, NOW, NOW),
            ).fetchone()["id"]

            # ── 日报附件：磁盘文件放在「源工单号/日期/分类」下
            stored = f"{SOURCE_NUMBER}/{DAY_STAMP}/现场服务照片/site-1.jpg"
            self.attachment_id = db.execute(
                "insert into service_report_attachments (report_id, category, "
                "original_filename, stored_filename, uploaded_by, uploaded_at) "
                "values (?,'site','site-1.jpg',?,?,?) returning id",
                (self.report_id, stored, uid, NOW),
            ).fetchone()["id"]
            self.old_attachment_path = Path(self.report_attachments_dir) / SOURCE_NUMBER / DAY_STAMP / "现场服务照片" / "site-1.jpg"
            self.old_attachment_path.parent.mkdir(parents=True, exist_ok=True)
            self.old_attachment_path.write_bytes(b"attachment-bytes")

            # ── 现场照片：pictures + thumbnails 两份
            self.photo_id = db.execute(
                "insert into field_photos (client_id, order_id, user_id, captured_at, "
                "received_at, capture_date, timezone_name, latitude, longitude, accuracy, "
                "location_note, note, source, relative_path, content_hash, bytes, "
                "equipment_number, position_number, equipment_session) values "
                "('c1',?,?,?,?,?,'UTC',0,0,0,'','','mobile',?,'hash',10,'','','') returning id",
                (
                    self.source_id,
                    uid,
                    f"{DAY}T09:00:00",
                    f"{DAY}T09:00:00",
                    DAY,
                    f"{SOURCE_NUMBER}/pictures/{DAY}/p1.jpg",
                ),
            ).fetchone()["id"]
            self.old_picture = Path(self.shared_photos_dir) / SOURCE_NUMBER / "pictures" / DAY / "p1.jpg"
            self.old_thumbnail = Path(self.shared_photos_dir) / SOURCE_NUMBER / "thumbnails" / DAY / "p1.jpg"
            for path in (self.old_picture, self.old_thumbnail):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"photo-bytes")

            # ── AI 日报留痕
            self.draft_id = db.execute(
                "insert into ai_daily_report_drafts (service_order_id, report_date, "
                "draft_data, status, draft_version, created_by, created_at, updated_at, "
                "saved_report_id) values (?,?,'{}','draft',1,?,?,?,?) returning id",
                (self.source_id, DAY, uid, NOW, NOW, self.report_id),
            ).fetchone()["id"]
            self.manifest_id = db.execute(
                "insert into ai_daily_report_attachment_manifests (manifest_id, draft_id, "
                "draft_version, service_order_id, report_date, manifest_version, "
                "validation_fingerprint, draft_data_hash, status, manifest_fingerprint, "
                "expected_plan, created_by, created_at, updated_at) values "
                "('m-transfer',?,1,?,?,1,'v','d','preparing','f','[]',?,?,?) returning id",
                (self.draft_id, self.source_id, DAY, uid, NOW, NOW),
            ).fetchone()["id"]

            # ── 报销（整单转移，附件目录按 expense_id）
            self.expense_id = db.execute(
                "insert into expenses (service_order_id, expense_number, project, "
                "expense_date, amount, currency, status, created_by, created_at, "
                "updated_at, payout_status, beneficiary_id) "
                "values (?,?,'住宿费',?,100,'USD','approved',?,?,?,'pending',?) returning id",
                (self.source_id, "EX-TRANSFER-1", DAY, uid, NOW, NOW, uid),
            ).fetchone()["id"]
            self.expense_attachment_id = db.execute(
                "insert into expense_attachments (expense_id, original_filename, "
                "stored_filename, uploaded_by, uploaded_at) values (?,'r.pdf',?,?,?) returning id",
                (self.expense_id, f"{self.expense_id}/r.pdf", uid, NOW),
            ).fetchone()["id"]
            self.expense_file = Path(self.data_dir) / "expense-attachments" / str(self.expense_id) / "r.pdf"
            self.expense_file.parent.mkdir(parents=True, exist_ok=True)
            self.expense_file.write_bytes(b"receipt-bytes")
            db.commit()

    # ─────────────────────────────── 用例 ───────────────────────────────

    def transfer(self, report_ids=None, expense_ids=None, target=None):
        with self.module.app.app_context():
            db = self.module.db()
            source = db.execute(
                "select * from service_orders where id = ?", (self.source_id,)
            ).fetchone()
            target_row = db.execute(
                "select * from service_orders where id = ?", (target or self.target_id,)
            ).fetchone()
            summary = order_data_transfer.transfer_order_data(
                self.api,
                source,
                target_row,
                report_ids if report_ids is not None else [self.report_id],
                expense_ids if expense_ids is not None else [self.expense_id],
            )
            db.commit()
            order_data_transfer.finalize_transfer(self.api, summary)
            return summary

    def test_report_moves_with_attachments_photos_and_ai_trail(self):
        self.seed()
        self.transfer(report_ids=[self.report_id], expense_ids=[])

        with self.module.app.app_context():
            db = self.module.db()
            report = db.execute(
                "select service_order_id from service_reports where id = ?", (self.report_id,)
            ).fetchone()
            self.assertEqual(report["service_order_id"], self.target_id)

            stored = db.execute(
                "select stored_filename from service_report_attachments where id = ?",
                (self.attachment_id,),
            ).fetchone()["stored_filename"]
            self.assertTrue(
                stored.startswith(f"{TARGET_NUMBER}/{DAY_STAMP}/"),
                f"附件相对路径应挂到目标工单：{stored}",
            )
            new_path = Path(self.report_attachments_dir) / stored
            self.assertTrue(new_path.is_file(), f"附件文件应已搬到 {new_path}")
            self.assertEqual(new_path.read_bytes(), b"attachment-bytes")
            self.assertFalse(self.old_attachment_path.exists(), "旧附件文件应被清理")

            photo = db.execute(
                "select order_id, relative_path from field_photos where id = ?", (self.photo_id,)
            ).fetchone()
            self.assertEqual(photo["order_id"], self.target_id)
            self.assertEqual(photo["relative_path"], f"{TARGET_NUMBER}/pictures/{DAY}/p1.jpg")
            new_picture = Path(self.shared_photos_dir) / TARGET_NUMBER / "pictures" / DAY / "p1.jpg"
            new_thumbnail = Path(self.shared_photos_dir) / TARGET_NUMBER / "thumbnails" / DAY / "p1.jpg"
            self.assertTrue(new_picture.is_file(), "照片应已搬到目标工单")
            self.assertTrue(new_thumbnail.is_file(), "缩略图必须一起搬，否则列表页 404")
            self.assertFalse(self.old_picture.exists())
            self.assertFalse(self.old_thumbnail.exists())

            draft = db.execute(
                "select service_order_id from ai_daily_report_drafts where id = ?", (self.draft_id,)
            ).fetchone()
            self.assertEqual(draft["service_order_id"], self.target_id)
            manifest = db.execute(
                "select service_order_id from ai_daily_report_attachment_manifests where id = ?",
                (self.manifest_id,),
            ).fetchone()
            self.assertEqual(manifest["service_order_id"], self.target_id)

    def test_expense_moves_without_touching_its_attachment_files(self):
        self.seed()
        self.transfer(report_ids=[], expense_ids=[self.expense_id])

        with self.module.app.app_context():
            db = self.module.db()
            expense = db.execute(
                "select service_order_id from expenses where id = ?", (self.expense_id,)
            ).fetchone()
            self.assertEqual(expense["service_order_id"], self.target_id)
        self.assertTrue(self.expense_file.is_file(), "报销附件目录按 expense_id，不该被搬动")

    def test_settlement_linked_expense_is_refused(self):
        self.seed()
        with self.module.app.app_context():
            db = self.module.db()
            project_id = db.execute(
                "insert into projects (name, created_at) values ('Transfer 住宿费', ?) returning id",
                (NOW,),
            ).fetchone()["id"]
            item_id = db.execute(
                "insert into expense_items (expense_id, project_id, project, amount, "
                "description, sort_order, line_key) values (?,?,'住宿费',100,'t',0,'lk-1') returning id",
                (self.expense_id, project_id),
            ).fetchone()["id"]
            cr_id = db.execute(
                "insert into customer_reimbursements (service_order_id, file_name, "
                "stored_filename, created_by, created_at, status, expense_selection_mode) "
                "values (?,'f','f',?,?,'draft','manual_review') returning id",
                (self.source_id, self.user_id, NOW),
            ).fetchone()["id"]
            db.execute(
                "insert into customer_reimbursement_expense_links "
                "(customer_reimbursement_id, expense_item_id, amount_snapshot, "
                "project_snapshot, expense_status_snapshot, selected_by, selected_at) "
                "values (?,?,100,'住宿费','approved',?,?)",
                (cr_id, item_id, self.user_id, NOW),
            )
            db.commit()

        with self.assertRaises(ValueError) as ctx:
            self.transfer(report_ids=[], expense_ids=[self.expense_id])
        self.assertIn("已被工单结算单引用", str(ctx.exception))

        with self.module.app.app_context():
            db = self.module.db()
            expense = db.execute(
                "select service_order_id from expenses where id = ?", (self.expense_id,)
            ).fetchone()
            self.assertEqual(expense["service_order_id"], self.source_id, "拒绝后不应改动数据")

    def test_settlement_linked_report_is_refused(self):
        self.seed()
        with self.module.app.app_context():
            db = self.module.db()
            cr_id = db.execute(
                "insert into customer_reimbursements (service_order_id, file_name, "
                "stored_filename, created_by, created_at, status, expense_selection_mode) "
                "values (?,'f','f',?,?,'draft','manual_review') returning id",
                (self.source_id, self.user_id, NOW),
            ).fetchone()["id"]
            db.execute(
                "insert into customer_reimbursement_items (customer_reimbursement_id, "
                "worker_name, project_date, source_report_id) values (?,?,?,?)",
                (cr_id, "Transfer", DAY, self.report_id),
            )
            db.commit()

        with self.assertRaises(ValueError) as ctx:
            self.transfer(report_ids=[self.report_id], expense_ids=[])
        self.assertIn("已被工单结算单引用", str(ctx.exception))

        with self.module.app.app_context():
            db = self.module.db()
            report = db.execute(
                "select service_order_id from service_reports where id = ?", (self.report_id,)
            ).fetchone()
            self.assertEqual(report["service_order_id"], self.source_id)

    def test_same_order_is_refused(self):
        self.seed()
        with self.assertRaises(ValueError) as ctx:
            self.transfer(report_ids=[self.report_id], expense_ids=[], target=self.source_id)
        self.assertIn("同一个", str(ctx.exception))

    def test_mileage_evidence_is_flagged_for_recompute(self):
        """里程佐证的终点是源工单地址，换工单后必须提示人工重算。"""
        self.seed()
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "insert into service_report_mileage_evidence (report_id, worker_user_id, "
                "route_fingerprint, origin_address, destination_address, generated_by, "
                "generated_at) values (?,?,'fp','A','源工单地址',?,?)",
                (self.report_id, self.user_id, self.user_id, NOW),
            )
            db.commit()
        summary = self.transfer(report_ids=[self.report_id], expense_ids=[])
        self.assertEqual(summary["mileage_reports"], 1)

    def test_page_requires_login_and_admin(self):
        self.seed()
        client = self.module.app.test_client()
        response = client.get("/tools/order-data-transfer")
        self.assertEqual(response.status_code, 302, "未登录应跳登录页")

        with client.session_transaction() as sess:
            sess["user_id"] = self.user_id
        response = client.get(f"/tools/order-data-transfer?source={SOURCE_NUMBER}&target={TARGET_NUMBER}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(SOURCE_NUMBER, body)
        self.assertIn("EX-TRANSFER-1", body, "页面应列出源工单的报销")

    def test_page_transfers_selected_rows(self):
        self.seed()
        client = self.module.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self.user_id
        response = client.post(
            "/tools/order-data-transfer",
            data={
                "source": SOURCE_NUMBER,
                "target": TARGET_NUMBER,
                "report_ids": [str(self.report_id)],
                "expense_ids": [str(self.expense_id)],
                "confirm": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        with self.module.app.app_context():
            db = self.module.db()
            report = db.execute(
                "select service_order_id from service_reports where id = ?", (self.report_id,)
            ).fetchone()
            expense = db.execute(
                "select service_order_id from expenses where id = ?", (self.expense_id,)
            ).fetchone()
            logs = db.execute(
                "select count(*) as count from audit_logs where entity_type in "
                "('service_report','expense') and summary like ?",
                ("%到工单%",),
            ).fetchone()["count"]
        self.assertEqual(report["service_order_id"], self.target_id)
        self.assertEqual(expense["service_order_id"], self.target_id)
        self.assertEqual(logs, 2, "日报和报销都应留下操作日志")


    # ───────────────────── 工单号模糊输入（只记得后几位） ─────────────────────

    def _seeded_client(self):
        self.seed()
        client = self.module.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self.user_id
        return client

    def _open_page(self, client, source, target=""):
        query = f"source={source}" + (f"&target={target}" if target else "")
        response = client.get(f"/tools/order-data-transfer?{query}")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_partial_order_number_resolves_the_order(self):
        """SO-TRANSFER-A 只输 TRANSFER-A（不带前缀）也要能定位到。"""
        body = self._open_page(self._seeded_client(), "TRANSFER-A")
        self.assertIn(SOURCE_NUMBER, body)
        self.assertIn("EX-TRANSFER-1", body, "片段唯一命中时应正常列出可转移数据")
        # 注意别断言「匹配到多个工单」这类提示文案：页面说明里就有这句话，断言会永真。
        self.assertNotIn('class="flash error"', body, "唯一命中时不应报错")

    def test_lowercase_partial_also_resolves(self):
        """大小写不敏感：用户不会特意去大写 SO 前缀。"""
        body = self._open_page(self._seeded_client(), "transfer-a")
        self.assertIn(SOURCE_NUMBER, body)
        self.assertIn("EX-TRANSFER-1", body)

    def test_ambiguous_partial_lists_candidates_without_guessing(self):
        """TRANSFER 同时命中 A 和 B：必须报错列候选，绝不能挑一个就搬。"""
        body = self._open_page(self._seeded_client(), "TRANSFER")
        self.assertIn('class="flash error"', body)
        # 候选是「、」连接的工单号列表，只有错误消息里会出现
        self.assertIn(f"{SOURCE_NUMBER}、{TARGET_NUMBER}", body)
        self.assertNotIn("EX-TRANSFER-1", body, "多个候选时不能猜一个工单去列数据")

    def test_unknown_number_still_reports_not_found(self):
        body = self._open_page(self._seeded_client(), "NOPE-9999")
        self.assertIn("找不到源工单", body)

    def test_like_fragment_escapes_wildcards(self):
        """ORDER_NUMBER_PATTERN 允许 `_`，不转义会被 like 当成单字符通配符。"""
        self.assertEqual(order_data_transfer._like_fragment("A_B"), "%A\\_B%")
        self.assertEqual(order_data_transfer._like_fragment("A%B"), "%A\\%B%")


if __name__ == "__main__":
    unittest.main()
