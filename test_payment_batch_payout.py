"""员工付款「合并发放」契约（v0.1.350）。

用户诉求：「员工往来账审核后的发放，不是一张一张的发放，而是多笔合成一个支票发放哦」。

业务口径（本次与用户确认后拍板，改代码前先读）：
- **一张支票只有一名收款人** → 批次必须限定同一员工，跨员工直接拒绝；
- **一张支票在银行流水里是一笔支出** → 对账改为**批次级匹配**
  （bank_transactions.matched_batch_id），不再要求「一笔流水 = 一张付款单」。

回归要点（改这几处必须同步本文件）：
- employee_finance.create_payment_batch 的校验顺序与回写：
  状态白名单 / 同员工 / 同币种 / 未被别的批次占用 / 账户与币种 / 支票号唯一；
- 批次号前缀 PB-（与 SL- 工资、ER- 报销同属「一类单据一前缀」）；
- 报销来源付款单发放后 payout_status='paid'，批次作废后退回 'pending'；
- void_payment_batch 把付款单退回「待付款」并冲销借款抵扣；
- bank_reconciliation 的批次分支：按批次合计金额整批核销。
"""
import unittest

import tests_pg
from flask import Flask, g
from werkzeug.exceptions import Forbidden

from database import PostgreSQLConnection
from employee_finance import register_employee_finance_routes


NOW = "2026-09-30T10:00:00-05:00"


_OPEN_CONNECTIONS = []


def _app_connection():
    """按请求缓存连接 —— 与 app.py 的 db() 同构（`if "db" not in g`）。

    这一点是硬要求，不是优化：批次路由在同一个请求里先 insert 批次、再 select id
    拿回来写付款单。**每次调用都新建连接的话，未提交的 insert 在另一个连接里
    根本读不到**，fetchone() 返回 None → 路由报 `'NoneType' object is not
    subscriptable`，测试却只看到「批次没建成」，很容易误判成校验写错了。
    连接串（不是连接对象）传给 PostgreSQLConnection —— 视图里的 SQL 是 `?`
    占位符，靠它的 translate_sql() 转换。
    """
    tests_pg.activate()
    if "test_db" not in g:
        connection = PostgreSQLConnection(tests_pg.database_url())
        g.test_db = connection
        _OPEN_CONNECTIONS.append(connection)
    return g.test_db


def _close_connections():
    while _OPEN_CONNECTIONS:
        try:
            _OPEN_CONNECTIONS.pop().close()
        except Exception:      # 测试收尾阶段，连接已断也不该让用例失败
            pass


def _seed(conn):
    conn.execute(
        "insert into users(id,name,email,role,password_hash,is_active,created_at) "
        "values(1,'批次甲','a@example.invalid','admin','x',1,'2026-01-01T00:00:00-06:00')"
    )
    conn.execute(
        "insert into users(id,name,email,role,password_hash,is_active,created_at) "
        "values(2,'批次乙','b@example.invalid','employee','x',1,'2026-01-01T00:00:00-06:00')"
    )
    conn.execute(
        "insert into bank_accounts(id,account_name,bank_name,account_type,currency,last_four,"
        "opening_balance,is_active,created_at,updated_at) "
        "values(1,'主账户','First Bank','checking','USD','0001',0,1,%s,%s)", (NOW, NOW)
    )
    conn.execute(
        "insert into bank_accounts(id,account_name,bank_name,account_type,currency,last_four,"
        "opening_balance,is_active,created_at,updated_at) "
        "values(2,'欧元账户','Second Bank','checking','EUR','0002',0,1,%s,%s)", (NOW, NOW)
    )
    conn.execute(
        "insert into service_orders(id,order_number,client_name,site_address,client_order_number,"
        "created_by,created_at) values(1,'SO-2609-0001','客户','站点','CO-1',1,%s)", (NOW,)
    )
    conn.execute(
        "insert into expenses(id,service_order_id,expense_number,project,expense_date,amount,currency,"
        "status,payout_status,beneficiary_id,created_by,created_at,updated_at) "
        "values(1,1,'EX-2609-0001','','2026-09-01',200,'USD','approved','pending',2,2,%s,%s)", (NOW, NOW)
    )
    orders = [
        (1, "SL-2609-0001", 1, "salary", "approved", "USD", "1000.00", "manual", None, ""),
        (2, "ER-2609-0002", 1, "expense", "pending_payment", "USD", "200.00", "expense", 1, "报销单 EX-2609-0001"),
        (3, "SL-2609-0003", 2, "salary", "approved", "USD", "500.00", "manual", None, ""),
        (4, "SL-2609-0004", 1, "salary", "draft", "USD", "300.00", "manual", None, ""),
        (5, "SL-2609-0005", 1, "salary", "approved", "EUR", "100.00", "manual", None, ""),
        # 6 号是「同一员工、同币种、已批准」的第三张，用来验重复支票号/重开批次
        (6, "SL-2609-0006", 1, "salary", "approved", "USD", "50.00", "manual", None, ""),
    ]
    for pid, number, employee_id, kind, status, currency, amount, source_type, source_id, description in orders:
        conn.execute(
            "insert into employee_payment_orders"
            "(id,payment_number,employee_id,payment_type,source_type,source_id,description,status,currency,"
            " gross_amount,advance_offset,other_adjustment,net_amount,created_at,updated_at,created_by)"
            " values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'0','0',%s,%s,%s,1)",
            (pid, number, employee_id, kind, source_type, source_id, description, status, currency, amount, amount, NOW, NOW),
        )
    transactions = [
        (1, 1, "1200.00", "USD", "fp-batch-ok"),      # 与 1000+200 的批次合计一致
        (2, 1, "999.00", "USD", "fp-batch-mismatch"),
    ]
    for tid, account_id, amount, currency, fingerprint in transactions:
        conn.execute(
            "insert into bank_transactions(id,bank_account_id,transaction_date,direction,amount,currency,"
            "description,reference_number,import_fingerprint,sync_source,sync_status,created_by,created_at)"
            " values(%s,%s,'2026-09-30','debit',%s,%s,'支票 1001','',%s,'manual','imported',1,%s)",
            (tid, account_id, amount, currency, fingerprint, NOW),
        )


def _rows(sql, params=()):
    """直连查询并按列名转成 dict（裸 psycopg 返回的是元组，dict(row) 会炸）。"""
    with tests_pg.connection() as conn:
        cursor = conn.execute(sql, params)
        columns = [column.name for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


class PaymentBatchPayoutTest(unittest.TestCase):
    """合并发放 / 作废 / 批次级对账的行为契约。"""

    @classmethod
    def setUpClass(cls):
        cls.conn = tests_pg.connection()
        _seed(cls.conn)

    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "payment-batch-test"   # flash() 需要 session
        self.app.jinja_env.filters["money"] = lambda value: "" if value is None else str(value)
        self.app.jinja_env.filters["local_datetime"] = lambda value: "" if value is None else str(value)
        self.app.jinja_env.globals.update(
            has_action_permission=lambda *a, **k: True,
            has_menu_permission=lambda *a, **k: True,
            app_version="test",
        )
        self.api = {
            "login_required": lambda fn: fn,
            "db": _app_connection,
            "now": lambda: NOW,
            "log_action": lambda *a, **k: None,
            "normalized_role": lambda: g.user["role"],
            "has_menu_permission": lambda *a, **k: True,
            "has_action_permission": lambda *a, **k: True,
            "lock_number_allocation": lambda conn: None,
            "render_template": None,
        }
        register_employee_finance_routes(self.app, self.api)
        self.client = self.app.test_client()

    def tearDown(self):
        _close_connections()

    # ────────────────────────────── 调用辅助 ──────────────────────────────
    def _post(self, endpoint, data, role="admin", **path_args):
        with self.app.test_request_context("/x", method="POST", data=data):
            g.user = {"id": 1, "name": "批次甲", "email": "a@example.invalid", "role": role}
            return self.app.view_functions[endpoint](**path_args)

    def _payable(self, **overrides):
        data = {
            "payment_id": ["1", "2"],
            "bank_account_id": "1",
            "payment_method": "check",
            "check_number": "1001",
            "notes": "",
        }
        data.update(overrides)
        return data

    def batches(self):
        return _rows("select * from employee_payment_batches order by id")

    def orders(self):
        return _rows("select * from employee_payment_orders order by id")

    def order(self, order_id):
        return next(row for row in self.orders() if row["id"] == order_id)

    def expense(self):
        return _rows("select * from expenses where id=1")[0]

    def events(self, order_id):
        return _rows("select * from payment_order_events where payment_order_id=%s order by id", (order_id,))

    # ────────────────────────────── 合并发放 ──────────────────────────────
    def test_batch_pays_selected_orders_with_one_check(self):
        response = self._post("create_payment_batch", self._payable())
        self.assertEqual(response.status_code, 302)
        batches = self.batches()
        self.assertEqual(len(batches), 1)
        batch = batches[0]
        self.assertTrue(batch["batch_number"].startswith("PB-"), batch["batch_number"])
        self.assertEqual(batch["employee_id"], 1)
        self.assertEqual(str(batch["total_amount"]), "1200.00")
        self.assertEqual(batch["payment_count"], 2)
        self.assertEqual(batch["check_number"], "1001")
        self.assertEqual(batch["status"], "issued")
        for order_id in (1, 2):
            order = self.order(order_id)
            self.assertEqual(order["status"], "paid")
            self.assertEqual(order["batch_id"], batch["id"])
            self.assertEqual(order["payment_method"], "check")
            self.assertEqual(order["external_transaction_id"], "1001")
        # 每件一笔 batch_pay 事件，且明细里带批次号与支票号
        for order_id in (1, 2):
            kinds = [event["event_type"] for event in self.events(order_id)]
            self.assertIn("batch_pay", kinds)
            details = " ".join(event["details"] for event in self.events(order_id))
            self.assertIn(batch["batch_number"], details)
            self.assertIn("1001", details)

    def test_expense_source_is_marked_paid(self):
        self._post("create_payment_batch", self._payable())
        expense = self.expense()
        self.assertEqual(expense["payout_status"], "paid")

    def test_approved_order_skips_manual_ready_step(self):
        """从「已批准」直接合并发放：不必先点「进入待付款」。"""
        self._post("create_payment_batch", self._payable())
        self.assertEqual(self.order(1)["status"], "paid")

    def test_cross_employee_selection_is_rejected(self):
        """一张支票只能开给一名员工。"""
        self._post("create_payment_batch", self._payable(payment_id=["1", "3"]))
        self.assertEqual(self.batches(), [])
        self.assertEqual(self.order(1)["status"], "approved")
        self.assertEqual(self.order(3)["status"], "approved")

    def test_mixed_currency_is_rejected(self):
        self._post("create_payment_batch", self._payable(payment_id=["1", "5"]))
        self.assertEqual(self.batches(), [])

    def test_draft_order_cannot_be_batched(self):
        self._post("create_payment_batch", self._payable(payment_id=["1", "4"]))
        self.assertEqual(self.batches(), [])
        self.assertEqual(self.order(4)["status"], "draft")

    def test_empty_selection_is_rejected(self):
        self._post("create_payment_batch", self._payable(payment_id=[]))
        self.assertEqual(self.batches(), [])

    def test_check_number_is_required(self):
        self._post("create_payment_batch", self._payable(check_number="  "))
        self.assertEqual(self.batches(), [])

    def test_duplicate_check_number_on_same_account_is_rejected(self):
        self._post("create_payment_batch", self._payable())
        # 6 号同样是「员工 1 · USD · 已批准」，只有支票号撞车 → 必须被挡下
        self._post("create_payment_batch", self._payable(payment_id=["6"]))
        self.assertEqual(len(self.batches()), 1)
        self.assertEqual(self.order(6)["status"], "approved")
        # 换个支票号就能发
        self._post("create_payment_batch", self._payable(payment_id=["6"], check_number="1002"))
        self.assertEqual(len(self.batches()), 2)

    def test_account_currency_must_match(self):
        self._post("create_payment_batch", self._payable(bank_account_id="2"))
        self.assertEqual(self.batches(), [])

    def test_order_already_in_another_batch_is_rejected(self):
        self._post("create_payment_batch", self._payable())
        # 2 号已经在批次里了，再拿它 + 6 号开一张新支票 → 拒绝
        self._post("create_payment_batch", self._payable(payment_id=["2", "6"], check_number="1002"))
        self.assertEqual(len(self.batches()), 1)

    def test_pay_permission_is_required(self):
        self.api["has_action_permission"] = lambda resource, action: action != "pay"
        with self.assertRaises(Forbidden):
            self._post("create_payment_batch", self._payable())
        self.assertEqual(self.batches(), [])

    # ────────────────────────────── 作废 ──────────────────────────────
    def _create_batch(self):
        self._post("create_payment_batch", self._payable())
        return self.batches()[0]

    def test_issued_batch_with_paid_members_cannot_be_voided(self):
        batch = self._create_batch()
        self._post("void_payment_batch", {"reason": "支票填错"}, batch_id=batch["id"])
        batch_after = self.batches()[0]
        self.assertEqual(batch_after["status"], "issued")
        for order_id in (1, 2):
            order = self.order(order_id)
            self.assertEqual(order["status"], "paid")
            self.assertIsNotNone(order["paid_at"])
            self.assertEqual(order["payment_method"], "check")
        self.assertEqual(self.expense()["payout_status"], "paid")
        kinds = [event["event_type"] for event in self.events(2)]
        self.assertNotIn("void", kinds)

    def test_paid_batch_cannot_be_reissued_through_void(self):
        batch = self._create_batch()
        self._post("void_payment_batch", {"reason": "重开"}, batch_id=batch["id"])
        self._post("create_payment_batch", self._payable(check_number="1002"))
        self.assertEqual(len(self.batches()), 1)
        self.assertEqual(self.order(1)["status"], "paid")
        self.assertEqual(self.order(1)["batch_id"], batch["id"])

    def test_void_requires_reason(self):
        batch = self._create_batch()
        self._post("void_payment_batch", {"reason": "  "}, batch_id=batch["id"])
        self.assertEqual(self.batches()[0]["status"], "issued")

    def test_repeated_void_attempts_do_not_change_paid_fact(self):
        batch = self._create_batch()
        self._post("void_payment_batch", {"reason": "第一次"}, batch_id=batch["id"])
        self._post("void_payment_batch", {"reason": "第二次"}, batch_id=batch["id"])
        self.assertEqual(self.batches()[0]["status"], "issued")
        self.assertEqual(self.order(1)["status"], "paid")

    # ────────────────────────────── 批次级对账 ──────────────────────────────
    def test_batch_reconciliation_matches_one_transaction(self):
        batch = self._create_batch()
        self._post("bank_reconciliation", {"transaction_id": "1", "batch_id": str(batch["id"])})
        tx = _rows("select * from bank_transactions where id=1")[0]
        self.assertEqual(tx["matched_batch_id"], batch["id"])
        self.assertIsNone(tx["matched_payment_order_id"])
        self.assertEqual(tx["sync_status"], "matched")
        self.assertEqual(self.batches()[0]["status"], "reconciled")
        for order_id in (1, 2):
            self.assertEqual(self.order(order_id)["status"], "reconciled")

    def test_batch_reconciliation_requires_equal_amount(self):
        batch = self._create_batch()
        self._post("bank_reconciliation", {"transaction_id": "2", "batch_id": str(batch["id"])})
        tx = _rows("select * from bank_transactions where id=2")[0]
        self.assertIsNone(tx["matched_batch_id"])
        self.assertEqual(self.batches()[0]["status"], "issued")

    def test_reconciled_batch_cannot_be_voided(self):
        batch = self._create_batch()
        self._post("bank_reconciliation", {"transaction_id": "1", "batch_id": str(batch["id"])})
        self._post("void_payment_batch", {"reason": "反悔"}, batch_id=batch["id"])
        self.assertEqual(self.batches()[0]["status"], "reconciled")

    def test_unmatch_preserves_paid_fact_and_creates_no_voucher(self):
        batch = self._create_batch()
        self._post("bank_reconciliation", {"transaction_id": "1", "batch_id": str(batch["id"])})
        before_vouchers = len(_rows("select id from vouchers"))
        self._post("bank_reconciliation_unmatch", {}, transaction_id=1)
        tx = _rows("select * from bank_transactions where id=1")[0]
        self.assertIsNone(tx["matched_batch_id"])
        self.assertIsNone(tx["matched_payment_order_id"])
        self.assertEqual(tx["sync_status"], "imported")
        self.assertEqual(self.batches()[0]["status"], "issued")
        for order_id in (1, 2):
            order = self.order(order_id)
            self.assertEqual(order["status"], "paid")
            self.assertIsNotNone(order["paid_at"])
            self.assertIn("unmatch", [event["event_type"] for event in self.events(order_id)])
        self.assertEqual(len(_rows("select id from vouchers")), before_vouchers)

    def test_unmatched_paid_batch_still_cannot_be_voided(self):
        batch = self._create_batch()
        self._post("bank_reconciliation", {"transaction_id": "1", "batch_id": str(batch["id"])})
        self._post("bank_reconciliation_unmatch", {}, transaction_id=1)
        self._post("void_payment_batch", {"reason": "解除匹配后尝试作废"}, batch_id=batch["id"])
        self.assertEqual(self.batches()[0]["status"], "issued")
        self.assertEqual(self.order(1)["status"], "paid")

    def test_enabled_accounting_records_unmatch_as_non_gl_event(self):
        batch = self._create_batch()
        self._post("bank_reconciliation", {"transaction_id": "1", "batch_id": str(batch["id"])})
        with tests_pg.connection() as conn:
            conn.execute(
                "insert into settings(key,value) values('accounting_base_enabled','1') "
                "on conflict(key) do update set value='1'"
            )
        self._post("bank_reconciliation_unmatch", {}, transaction_id=1)
        events = _rows("select * from posting_events where event_type='bank.unmatched'")
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["requires_gl"])
        self.assertIsNone(events[0]["voucher_id"])
        self.assertEqual(len(_rows("select id from vouchers")), 0)
        self.assertEqual(len(_rows("select id from posting_audit where action='recorded'")), 1)

    def test_enabled_accounting_records_match_and_unmatch_without_gl(self):
        with tests_pg.connection() as conn:
            conn.execute(
                "insert into settings(key,value) values('accounting_base_enabled','1') "
                "on conflict(key) do update set value='1'"
            )
        batch = self._create_batch()
        self._post("bank_reconciliation", {"transaction_id": "1", "batch_id": str(batch["id"])})
        self._post("bank_reconciliation_unmatch", {}, transaction_id=1)
        events = _rows("select event_type,requires_gl,voucher_id from posting_events order by id")
        self.assertEqual([event["event_type"] for event in events],
                         ["bank.matched", "bank.unmatched"])
        self.assertTrue(all(not event["requires_gl"] for event in events))
        self.assertTrue(all(event["voucher_id"] is None for event in events))
        self.assertEqual(len(_rows("select id from vouchers")), 0)
        self.assertEqual(len(_rows("select id from posting_audit where action='recorded'")), 2)

    def test_single_payment_unmatch_returns_to_paid_without_clearing_paid_at(self):
        with tests_pg.connection() as conn:
            conn.execute(
                "update employee_payment_orders set status='reconciled',bank_account_id=1,"
                "payment_method='check',paid_by=1,paid_at=%s,reconciled_by=1,reconciled_at=%s "
                "where id=1", (NOW, NOW),
            )
            conn.execute(
                "update bank_transactions set matched_payment_order_id=1,matched_by=1,"
                "matched_at=%s,sync_status='matched' where id=1", (NOW,),
            )
        self._post("bank_reconciliation_unmatch", {}, transaction_id=1)
        tx = _rows("select * from bank_transactions where id=1")[0]
        order = self.order(1)
        self.assertIsNone(tx["matched_payment_order_id"])
        self.assertEqual(tx["sync_status"], "imported")
        self.assertEqual(order["status"], "paid")
        self.assertEqual(order["paid_at"], NOW)
        self.assertEqual(order["payment_method"], "check")
        self.assertIn("unmatch", [event["event_type"] for event in self.events(1)])


class PaymentBatchPageRenderTest(unittest.TestCase):
    """新建的两个模板至少要能真渲染（模板写错在这里就炸，别等线上）。"""

    @classmethod
    def setUpClass(cls):
        cls.conn = tests_pg.connection()
        _seed(cls.conn)

    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "payment-batch-render"
        self.app.jinja_env.filters["money"] = lambda value: "" if value is None else str(value)
        self.app.jinja_env.filters["local_datetime"] = lambda value: "" if value is None else str(value)
        self.app.jinja_env.globals.update(
            has_action_permission=lambda *a, **k: True,
            has_menu_permission=lambda *a, **k: True,
            app_version="test",
        )
        api = {
            "login_required": lambda fn: fn,
            "db": _app_connection,
            "now": lambda: NOW,
            "log_action": lambda *a, **k: None,
            "normalized_role": lambda: g.user["role"],
            "has_menu_permission": lambda *a, **k: True,
            "has_action_permission": lambda *a, **k: True,
            "lock_number_allocation": lambda conn: None,
            "render_template": None,
        }
        register_employee_finance_routes(self.app, api)
        self.api = api

    def tearDown(self):
        _close_connections()

    def _render_real(self, endpoint, url, template_marker, **path_args):
        """借真 app 的 URL map 与 Jinja 环境渲染（裸 Flask 会 BuildError）。"""
        import app as real_app_module
        flask_app = real_app_module.app
        original = flask_app.view_functions.get(endpoint)
        flask_app.view_functions[endpoint] = self.app.view_functions[endpoint]
        try:
            with flask_app.test_request_context(url):
                g.user = {"id": 1, "name": "批次甲", "email": "a@example.invalid", "role": "admin"}
                body = flask_app.view_functions[endpoint](**path_args)
        finally:
            if original is not None:
                flask_app.view_functions[endpoint] = original
            else:
                flask_app.view_functions.pop(endpoint, None)
        self.assertIn(template_marker, body)
        return body

    def test_batch_list_page_renders(self):
        self._render_real("payment_batches", "/finance/payment-batches", "付款批次")

    def test_batch_detail_page_renders(self):
        with self.app.test_request_context("/x", method="POST", data={
            "payment_id": ["1", "2"], "bank_account_id": "1",
            "payment_method": "check", "check_number": "1001", "notes": "",
        }):
            g.user = {"id": 1, "name": "批次甲", "email": "a@example.invalid", "role": "admin"}
            self.app.view_functions["create_payment_batch"]()
        with tests_pg.connection() as conn:
            batch_id = conn.execute("select id from employee_payment_batches order by id limit 1").fetchone()[0]
        body = self._render_real("payment_batch_detail", f"/finance/payment-batches/{batch_id}",
                                 "支票信息", batch_id=batch_id)
        self.assertIn("SL-2609-0001", body)
        self.assertIn("ER-2609-0002", body)
