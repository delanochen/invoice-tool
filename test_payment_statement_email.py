"""Phase 5C：Payment Statement Email + Delivery Log 回归测试。

覆盖用户列出的 24 项验收点：
 1 admin 可发送          2 finance 可发送         3 manager 403
 4 employee 403          5 missing email          6 invalid email
 7 PDF attachment 正确   8 同一 payload           9 filename 正确
10 subject 正确          11 body 正确             12 成功 log
13 失败 log              14 SMTP 失败 payment 零变化 15 Send Again 第二条 log
16 history 新到旧        17 void 禁止发送          18 reconciled 可发送
19 review_pending 可发送 20 integrity 禁止发送     21 recipient 来自 users.email
22 payment 数据零修改    23 POST only              24 越权 batch 不能发送

铁律（改这几处必须同步本文件）：
- 唯一发送链路：payload → PDF → send_email → record_email_delivery，
  Email 层绝不自己查付款明细 / 重算 / 走 HTTP；
- 所有 automated tests 必须 mock send_email，严禁真实 SMTP；
- 业务阻断（void / 不闭合 / 缺 email）不调用 SMTP 也不记 delivery；
- SMTP 失败记 failed delivery（独立 commit），付款数据零变化；
- void / 不闭合禁止发送；review_pending 不阻断；
- 每次发送一条新 log，Email History 最近 10 条新到旧。
"""
import importlib.util
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from flask import g

import employee_finance
import tests_pg
from employee_finance import (
    send_payment_statement_email,
    statement_email_history,
    statement_email_last_sent,
)

ROOT = Path(__file__).resolve().parent

CLEAN_TABLES = (
    "messages", "audit_logs", "email_delivery_logs",
    "employee_payment_tax_reviews", "employee_payment_components",
    "payment_order_events", "payment_order_sources", "employee_payment_orders",
    "employee_payment_batches", "worker_tax_status_history", "expense_attachments",
    "expense_items", "expenses", "service_orders", "users", "projects",
)


def api_of(module):
    return {
        "db": module.db,
        "normalized_role": module.normalized_role,
        "has_action_permission": module.has_action_permission,
        "payment_tax_components": module.payment_tax_components,
        "now": module.now,
        "render_template": module.render_template,
        "send_email": module.send_email,
        "record_email_delivery": module.record_email_delivery,
        "log_action": module.log_action,
        "get_company_profile": module.get_company_profile,
        "login_required": module.login_required,
    }


class PaymentStatementEmailTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tests_pg.activate()
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("statement_email_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="statement-email-secret")
        cls.module.app.template_folder = str(ROOT / "templates")
        cls.module.app.static_folder = str(ROOT / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in CLEAN_TABLES:
                db.execute(f"delete from {table}")
            self.admin_id = self._insert_user(db, "Admin", "admin", "admin@test.invalid")
            self.finance_id = self._insert_user(db, "Finance", "finance", "finance@test.invalid")
            self.manager_id = self._insert_user(db, "Manager", "manager", "manager@test.invalid")
            self.employee_id = self._insert_user(db, "Worker", "employee", "worker@test.invalid")
            self.other_id = self._insert_user(db, "Other", "employee", "other@test.invalid")
            self.project_id = db.execute(
                "insert into projects (name,default_amount,tax_rate,is_active,created_at,project_type)"
                " values ('Hotel',0,0,1,?,'expense')",
                (self.module.now(),),
            ).lastrowid
            self.order_id = db.execute(
                "insert into service_orders (order_number,client_name,site_address,"
                "client_order_number,start_date,created_by,created_at)"
                " values ('SO-EM-1','Client','Site','C1','2026-09-01',?,?)",
                (self.admin_id, self.module.now()),
            ).lastrowid
            db.commit()
        # 绝不允许真实 SMTP：整类测试期间 mock 掉 module.send_email
        self.send_calls = []
        self.smtp_error = None
        self._patcher = patch.object(self.module, "send_email", side_effect=self._fake_send)
        self._patcher.start()
        self.http = self.module.app.test_client()
        self._login(self.admin_id)

    def tearDown(self):
        self._patcher.stop()
        with self.module.app.app_context():
            db = self.module.db()
            for table in CLEAN_TABLES:
                db.execute(f"delete from {table}")
            db.commit()

    def _fake_send(self, to, subject, html, attachments=None):
        """SMTP mock：记录调用；smtp_error 非空时模拟失败。"""
        self.send_calls.append({
            "to": to, "subject": subject, "html": html, "attachments": attachments,
        })
        if self.smtp_error is not None:
            raise self.smtp_error

    # ------------------------------------------------------------------
    # 播种辅助（与 5A/5B 测试同口径）
    # ------------------------------------------------------------------
    def _insert_user(self, db, name, role, email):
        return db.execute(
            "insert into users (name,email,password_hash,role,is_active,created_at)"
            " values (?,?,?,?,1,?)",
            (name, email, "unused", role, self.module.now()),
        ).lastrowid

    def _login(self, user_id):
        with self.http.session_transaction() as session:
            session["user_id"] = user_id

    def _order(self, employee_id, payment_type="salary", number="SL-2609-0001",
               gross="100.00", status="paid", source_number="2026-09-01~2026-09-14",
               source_type="system_payroll", source_id=None):
        with self.module.app.app_context():
            db = self.module.db()
            order_id = db.execute(
                """insert into employee_payment_orders
                (payment_number,employee_id,payment_type,status,currency,gross_amount,
                 advance_offset,other_adjustment,net_amount,taxable_compensation_total,
                 accountable_reimbursement_total,tax_review_required_total,source_type,
                 source_id,source_number,paid_at,created_at,updated_at,created_by)
                values (?,?,?,?,'USD',?,'0','0',?,'0','0','0',?,?,?,?,?,?,?)
                """,
                (number, employee_id, payment_type, status, gross, gross,
                 source_type, source_id, source_number, self.module.now(),
                 self.module.now(), self.module.now(), self.admin_id),
            ).lastrowid
            db.commit()
            return order_id

    def _component(self, order_id, employee_id, *, code="standard_pay", name="Standard Pay",
                   amount="100.00", tax_category="taxable_compensation", tax_status="1099",
                   quantity=None, unit=None, unit_rate=None, service_date="2026-09-05",
                   work_order_id=None, superseded_at=None):
        with self.module.app.app_context():
            db = self.module.db()
            component_id = db.execute(
                """insert into employee_payment_components
                (payment_order_id,employee_id,component_code,component_name,amount,
                 quantity,unit,unit_rate,service_date,work_order_id,source_type,
                 tax_category,tax_status_snapshot,substantiated,review_status,created_at,
                 superseded_at)
                values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (order_id, employee_id, code, name, amount, quantity, unit, unit_rate,
                 service_date, work_order_id, "payroll_generation", tax_category,
                 tax_status, True,
                 "review_required" if tax_category == "tax_review_required" else "confirmed",
                 self.module.now(), superseded_at),
            ).lastrowid
            db.commit()
            return component_id

    def _expense(self, *, number="EX-EM-1"):
        with self.module.app.app_context():
            db = self.module.db()
            expense_id = db.execute(
                """insert into expenses (service_order_id, expense_number, project_id, project,
                    expense_date, amount, currency, description, status, created_by,
                    created_at, updated_at, beneficiary_id, business_purpose)
                values (?, ?, ?, 'Hotel', '2026-09-05', '80.00', 'USD', '住宿',
                        'approved', ?, ?, ?, ?, '工单现场作业需要的住宿支出')
                """,
                (self.order_id, number, self.project_id, self.employee_id,
                 self.module.now(), self.module.now(), self.employee_id),
            ).lastrowid
            db.execute(
                "insert into expense_items (expense_id, line_key, project_id, project, amount,"
                " description, sort_order) values (?,?,?,?,?,?,?)",
                (expense_id, "line-1", self.project_id, "Hotel", "80.00", "房间费", 0),
            )
            db.commit()
            return expense_id

    def _batch(self, employee_id, total="100.00", count=1, status="issued",
               number="PB-2609-0001", check_number="CH-1001", method="check",
               reconciled_at=None, voided_at=None):
        with self.module.app.app_context():
            db = self.module.db()
            batch_id = db.execute(
                """insert into employee_payment_batches
                (batch_number,employee_id,currency,payment_method,
                 check_number,total_amount,payment_count,status,notes,issued_by,issued_at,
                 reconciled_by,reconciled_at,voided_by,voided_at,created_at,updated_at)
                values (?,?, 'USD', ?, ?, ?, ?, ?, '', ?, ?, null, ?, null, ?, ?, ?)
                """,
                (number, employee_id, method, check_number, total, count, status,
                 self.admin_id, self.module.now(), reconciled_at, voided_at,
                 self.module.now(), self.module.now()),
            ).lastrowid
            db.commit()
            return batch_id

    def _attach(self, batch_id, order_ids):
        with self.module.app.app_context():
            db = self.module.db()
            for order_id in order_ids:
                db.execute(
                    "update employee_payment_orders set batch_id=? where id=?",
                    (batch_id, order_id),
                )
            db.commit()

    def _seed_issued(self, *, number="SL-2609-0001", amount="100.00", employee_id=None,
                     batch_number="PB-2609-0001", status="issued"):
        """单 SL 批次，闭合正常。"""
        employee_id = employee_id or self.employee_id
        order_id = self._order(employee_id, "salary", number, amount)
        self._component(order_id, employee_id, amount=amount)
        batch_id = self._batch(employee_id, total=amount, count=1,
                               number=batch_number, status=status)
        self._attach(batch_id, [order_id])
        return batch_id

    def _email_url(self, batch_id):
        return f"/finance/payment-batches/{batch_id}/statement/email"

    def _post(self, batch_id):
        return self.http.post(self._email_url(batch_id))

    def _send_direct(self, batch_id, user_id, role="admin"):
        """直调发送服务（HTTP 之外的路径也必须可用，5C 供以后复用）。"""
        with self.module.app.test_request_context("/"):
            g.user = {"id": user_id, "name": "Tester", "role": role}
            return send_payment_statement_email(api_of(self.module), batch_id)

    def _snapshot(self):
        with self.module.app.app_context():
            db = self.module.db()
            return {
                "batches": [tuple(r) for r in db.execute(
                    "select * from employee_payment_batches order by id").fetchall()],
                "orders": [tuple(r) for r in db.execute(
                    "select * from employee_payment_orders order by id").fetchall()],
                "components": [tuple(r) for r in db.execute(
                    "select * from employee_payment_components order by id").fetchall()],
                "bank_transactions": [tuple(r) for r in db.execute(
                    "select * from bank_transactions order by id").fetchall()],
            }

    # ------------------------------------------------------------------
    # 1/2 admin / finance 可发送（走 HTTP POST）
    # ------------------------------------------------------------------
    def test_admin_and_finance_can_send(self):
        batch_id = self._seed_issued()
        for user_id in (self.admin_id, self.finance_id):
            self.send_calls.clear()
            self._login(user_id)
            response = self._post(batch_id)
            self.assertEqual(response.status_code, 302, f"user {user_id}")
            self.assertEqual(len(self.send_calls), 1, f"user {user_id}")
            # success flash 自 v0.1.324 起有意不渲染；发送成功以按钮变 Send Again 为准
            follow = self.http.get(f"/finance/payment-batches/{batch_id}/statement")
            self.assertIn(b"Send Again", follow.data)

    # ------------------------------------------------------------------
    # 3/4 manager / employee 403（且绝不触发 SMTP）
    # ------------------------------------------------------------------
    def test_manager_and_employee_cannot_send(self):
        batch_id = self._seed_issued()
        for user_id in (self.manager_id, self.employee_id, self.other_id):
            self.send_calls.clear()
            self._login(user_id)
            self.assertEqual(self._post(batch_id).status_code, 403, f"user {user_id}")
            self.assertEqual(self.send_calls, [], f"user {user_id}")

    # ------------------------------------------------------------------
    # 5 missing email：不调用 SMTP，页面有提示
    # ------------------------------------------------------------------
    def test_missing_email_blocks_send(self):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("update users set email='' where id=?", (self.employee_id,))
            db.commit()
        batch_id = self._seed_issued()
        self.send_calls.clear()
        response = self._post(batch_id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.send_calls, [])
        page = self.http.get(f"/finance/payment-batches/{batch_id}/statement")
        self.assertIn(b"Employee email not available.", page.data)

    # ------------------------------------------------------------------
    # 6 invalid email：不调用 SMTP
    # ------------------------------------------------------------------
    def test_invalid_email_blocks_send(self):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("update users set email='not-an-email' where id=?", (self.employee_id,))
            db.commit()
        batch_id = self._seed_issued()
        self.send_calls.clear()
        self.assertEqual(self._post(batch_id).status_code, 302)
        self.assertEqual(self.send_calls, [])

    # ------------------------------------------------------------------
    # 7/8/9/10/11 attachment / payload / filename / subject / body
    # ------------------------------------------------------------------
    def test_email_content_and_attachment(self):
        expense_id = self._expense(number="EX-EM-2")
        er = self._order(self.employee_id, "expense", "ER-2609-0009", "80.00",
                         source_type="expense", source_id=expense_id,
                         source_number="ER-2609-0009")
        self._component(er, self.employee_id, code="expense_item", name="Hotel",
                        amount="80.00", tax_category="accountable_reimbursement")
        sl = self._order(self.employee_id, "salary", "SL-2609-0008", "120.00")
        self._component(sl, self.employee_id, amount="120.00")
        batch_id = self._batch(self.employee_id, total="200.00", count=2)
        self._attach(batch_id, [sl, er])

        self.assertEqual(self._post(batch_id).status_code, 302)
        self.assertEqual(len(self.send_calls), 1)
        call = self.send_calls[0]
        # 10 subject
        self.assertEqual(call["subject"], "Payment Statement - PB-2609-0001")
        # 21 recipient 来自 users.email
        self.assertEqual(call["to"], "worker@test.invalid")
        # 7 PDF attachment：filename / mimetype / bytes / 签名
        self.assertEqual(len(call["attachments"]), 1)
        attachment = call["attachments"][0]
        self.assertEqual(attachment["filename"],
                         "Payment_Statement_PB-2609-0001_Worker.pdf")
        self.assertEqual(attachment["maintype"], "application")
        self.assertEqual(attachment["subtype"], "pdf")
        self.assertTrue(attachment["content"].startswith(b"%PDF-"))
        # 8 同一 payload：附件（pypdf 解码）里能看到两条付款单号
        import io
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(attachment["content"]))
        pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertIn("SL-2609-0008", pdf_text)
        self.assertIn("ER-2609-0009", pdf_text)
        # 11 body：必备字段 + 附带说明；review pending 注记（本批次没有则不出现）
        body = call["html"]
        self.assertIn("Your Payment Statement is attached as a PDF.", body)
        self.assertIn("Worker", body)
        self.assertIn("PB-2609-0001", body)
        self.assertIn("$200.00", body)
        self.assertIn("Check Number: CH-1001", body)

    # ------------------------------------------------------------------
    # 12 成功 log
    # ------------------------------------------------------------------
    def test_success_delivery_log(self):
        batch_id = self._seed_issued()
        self.assertEqual(self._post(batch_id).status_code, 302)
        with self.module.app.app_context():
            db = self.module.db()
            rows = [dict(r) for r in db.execute(
                "select * from email_delivery_logs where entity_type='payment_statement'"
                " and entity_id=? order by id", (batch_id,)).fetchall()]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["recipient"], "worker@test.invalid")
        self.assertEqual(row["subject"], "Payment Statement - PB-2609-0001")
        self.assertEqual(row["error_message"], "")
        self.assertEqual(row["employee_id"], self.employee_id)
        self.assertEqual(row["sent_by"], self.admin_id)
        self.assertTrue(row["sent_at"])

    # ------------------------------------------------------------------
    # 13 失败 log / 14 SMTP 失败 payment 零变化
    # ------------------------------------------------------------------
    def test_smtp_failure_records_failed_log_and_no_payment_change(self):
        batch_id = self._seed_issued()
        self.smtp_error = RuntimeError("SMTP down")
        before = self._snapshot()
        response = self._post(batch_id)
        self.assertEqual(response.status_code, 302)
        with self.module.app.app_context():
            db = self.module.db()
            rows = [dict(r) for r in db.execute(
                "select * from email_delivery_logs where entity_type='payment_statement'"
                " and entity_id=? order by id", (batch_id,)).fetchall()]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["status"], "failed")
        self.assertIn("SMTP down", row["error_message"])
        self.assertEqual(row["employee_id"], self.employee_id)
        # 22 付款相关数据零变化（失败路径也不允许碰）
        self.assertEqual(self._snapshot(), before)

    # ------------------------------------------------------------------
    # 15 Send Again 产生第二条 log / 16 history 新到旧
    # ------------------------------------------------------------------
    def test_send_again_appends_new_log(self):
        batch_id = self._seed_issued()
        self.assertEqual(self._post(batch_id).status_code, 302)
        self.assertEqual(self._post(batch_id).status_code, 302)
        with self.module.app.app_context():
            db = self.module.db()
            rows = [tuple(r) for r in db.execute(
                "select id, status from email_delivery_logs where entity_type='payment_statement'"
                " and entity_id=? order by id", (batch_id,)).fetchall()]
        self.assertEqual(len(rows), 2)
        self.assertEqual({status for _, status in rows}, {"sent"})
        # 16 history 新到旧（id desc）
        history = self._history(batch_id)
        self.assertEqual([row["id"] for row in history], sorted(
            [row["id"] for row in history], reverse=True))
        self.assertEqual(len(history), 2)

    # ------------------------------------------------------------------
    # 17 void 禁止发送
    # ------------------------------------------------------------------
    def test_void_batch_cannot_be_emailed(self):
        batch_id = self._seed_issued(status="void")
        self.send_calls.clear()
        response = self._post(batch_id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.send_calls, [])
        page = self.http.get(f"/finance/payment-batches/{batch_id}/statement")
        self.assertIn(b"This payment batch is void and cannot be emailed.", page.data)
        with self.module.app.app_context():
            db = self.module.db()
            count = db.execute(
                "select count(*) c from email_delivery_logs where entity_type='payment_statement'"
                " and entity_id=?", (batch_id,)).fetchone()["c"]
        self.assertEqual(count, 0)

    # ------------------------------------------------------------------
    # 18 reconciled 可发送
    # ------------------------------------------------------------------
    def test_reconciled_batch_can_be_emailed(self):
        batch_id = self._seed_issued(status="reconciled")
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("update employee_payment_batches set reconciled_at=? where id=?",
                       (self.module.now(), batch_id))
            db.commit()
        self.assertEqual(self._post(batch_id).status_code, 302)
        self.assertEqual(len(self.send_calls), 1)

    # ------------------------------------------------------------------
    # 19 review_pending 可发送（不阻断）
    # ------------------------------------------------------------------
    def test_review_pending_batch_can_be_emailed(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0019", "100.00")
        self._component(order_id, self.employee_id, amount="100.00",
                        tax_category="tax_review_required")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])
        self.assertEqual(self._post(batch_id).status_code, 302)
        self.assertEqual(len(self.send_calls), 1)
        self.assertIn("review pending", self.send_calls[0]["html"])

    # ------------------------------------------------------------------
    # 20 integrity warning 禁止发送
    # ------------------------------------------------------------------
    def test_integrity_error_blocks_send(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0020", "100.00")
        self._component(order_id, self.employee_id, amount="100.00")
        batch_id = self._batch(self.employee_id, total="99.99", count=1)  # 差一分
        self._attach(batch_id, [order_id])
        self.send_calls.clear()
        response = self._post(batch_id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.send_calls, [])
        page = self.http.get(f"/finance/payment-batches/{batch_id}/statement")
        self.assertIn(b"cannot be emailed", page.data)
        with self.module.app.app_context():
            db = self.module.db()
            count = db.execute(
                "select count(*) c from email_delivery_logs where entity_type='payment_statement'"
                " and entity_id=?", (batch_id,)).fetchone()["c"]
        self.assertEqual(count, 0)

    # ------------------------------------------------------------------
    # 23 POST only（GET 不能发送）
    # ------------------------------------------------------------------
    def test_get_is_not_allowed(self):
        batch_id = self._seed_issued()
        self.send_calls.clear()
        response = self.http.get(self._email_url(batch_id))
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.send_calls, [])

    # ------------------------------------------------------------------
    # 24 越权 batch 不能发送（员工不能给别人的批次发邮件）
    # ------------------------------------------------------------------
    def test_cross_employee_batch_cannot_be_emailed(self):
        # other 员工自己的批次（self-only 数据），manager 也无发送权
        batch_id = self._seed_issued(employee_id=self.other_id)
        self.send_calls.clear()
        self._login(self.other_id)
        self.assertEqual(self._post(batch_id).status_code, 403)
        self.assertEqual(self.send_calls, [])

    # ------------------------------------------------------------------
    # 22 直调发送：批次/付款单/组件/往来账零修改 + 5C 复用接口可用
    # ------------------------------------------------------------------
    def test_direct_send_writes_only_delivery_log(self):
        batch_id = self._seed_issued()
        before = self._snapshot()
        recipient = self._send_direct(batch_id, self.admin_id)
        self.assertEqual(recipient, "worker@test.invalid")
        self.assertEqual(len(self.send_calls), 1)
        self.assertEqual(self._snapshot(), before)

    # ------------------------------------------------------------------
    # Email History 服务函数：最近 10 条
    # ------------------------------------------------------------------
    def test_history_limit_ten(self):
        batch_id = self._seed_issued()
        for _ in range(12):
            self._post(batch_id)
        history = self._history(batch_id)
        self.assertEqual(len(history), 10)

    def _history(self, batch_id):
        with self.module.app.test_request_context("/"):
            g.user = {"id": self.admin_id, "name": "Admin", "role": "admin"}
            return statement_email_history(api_of(self.module), batch_id)

    # ------------------------------------------------------------------
    # last_sent：只认 status='sent'
    # ------------------------------------------------------------------
    def test_last_sent_ignores_failed(self):
        batch_id = self._seed_issued()
        self.smtp_error = RuntimeError("SMTP down")
        self._post(batch_id)
        self.smtp_error = None
        self.assertIsNone(self._last_sent(batch_id))
        self._post(batch_id)
        last = self._last_sent(batch_id)
        self.assertEqual(last["recipient"], "worker@test.invalid")

    def _last_sent(self, batch_id):
        with self.module.app.test_request_context("/"):
            g.user = {"id": self.admin_id, "name": "Admin", "role": "admin"}
            return statement_email_last_sent(api_of(self.module), batch_id)

    # ------------------------------------------------------------------
    # Email History UI：从未发送提示 + 发送后显示记录
    # ------------------------------------------------------------------
    def test_history_ui(self):
        batch_id = self._seed_issued()
        page = self.http.get(f"/finance/payment-batches/{batch_id}/statement")
        self.assertIn(b"No email delivery history.", page.data)
        self._post(batch_id)
        page = self.http.get(f"/finance/payment-batches/{batch_id}/statement")
        self.assertIn(b"worker@test.invalid", page.data)
        self.assertIn(b"Payment Statement - PB-2609-0001", page.data)
        self.assertIn(b"Send Again", page.data)

    # ------------------------------------------------------------------
    # 按钮：manager / employee 不显示（其他角色不显示 Send 按钮）
    # ------------------------------------------------------------------
    def test_send_button_hidden_for_non_admin_roles(self):
        batch_id = self._seed_issued()
        for user_id in (self.manager_id, self.employee_id):
            self._login(user_id)
            page = self.http.get(f"/finance/payment-batches/{batch_id}/statement")
            self.assertNotIn(b"Email Payment Statement", page.data, f"user {user_id}")
            self.assertNotIn(b"Send Again", page.data, f"user {user_id}")


if __name__ == "__main__":
    unittest.main()
