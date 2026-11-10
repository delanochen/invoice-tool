"""Phase 1 回归：invoice_orders 关系表的读写、作废释放与读取回退。

用 sqlite3 内存库 + 一个 `dialect='postgresql'` 的连接包装器模拟 PostgreSQL，
这样 SQL 文本（标准 SQL + `?` 占位符）与真实链路一致，又不需要 psycopg。
跑法（仓库 conftest 需要 psycopg，本机没有）：pytest tests/test_invoice_orders.py -q --noconftest
"""
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from invoice_tool.customer_reimbursements.support_helpers import service_order_active_invoice
from invoice_tool.invoices import order_links


class _Row:
    def __init__(self, columns, values):
        self._data = dict(zip(columns, values))

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()


class _Cursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self._columns = [d[0] for d in (cursor.description or [])]

    def fetchone(self):
        values = self._cursor.fetchone()
        return None if values is None else _Row(self._columns, values)

    def fetchall(self):
        return [_Row(self._columns, values) for values in self._cursor.fetchall()]

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount


class _Connection:
    def __init__(self, dialect):
        self.raw = sqlite3.connect(':memory:')
        self.dialect = dialect

    def execute(self, sql, params=()):
        return _Cursor(self.raw.execute(sql, params))

    def close(self):
        self.raw.close()


def _schema(connection):
    connection.raw.executescript(
        """
        create table invoices (id integer primary key, service_order_id integer,
                               status text default 'submitted', issue_date text, client_id integer);
        create table invoice_items (id integer primary key, invoice_id integer, amount real);
        create table invoice_orders (id integer primary key autoincrement, invoice_id integer,
                                     service_order_id integer, customer_reimbursement_id integer,
                                     amount_cents integer not null default 0, released_at text,
                                     created_at text);
        create unique index invoice_orders_invoice_order_uidx on invoice_orders (invoice_id, service_order_id);
        """
    )


class InvoiceOrdersRelationTest(unittest.TestCase):
    def setUp(self):
        self.connection = _Connection('postgresql')
        _schema(self.connection)
        self.db = lambda: self.connection

    def tearDown(self):
        self.connection.close()

    def _invoice(self, invoice_id, service_order_id=None, status='submitted', issue_date='2026-01-01'):
        self.connection.execute(
            "insert into invoices (id, service_order_id, status, issue_date, client_id) values (?, ?, ?, ?, 1)",
            (invoice_id, service_order_id, status, issue_date),
        )

    def _item(self, invoice_id, amount):
        self.connection.execute(
            "insert into invoice_items (invoice_id, amount) values (?, ?)", (invoice_id, amount)
        )

    def test_amount_is_summed_into_exact_cents(self):
        self._invoice(1)
        self._item(1, 1000.10)
        self._item(1, 234.56)
        self.assertEqual(order_links.invoice_amount_cents(1, db=self.db), 123466)

    def test_record_is_idempotent_and_stores_amount(self):
        self._invoice(1)
        self._item(1, 1234.56)
        first = order_links.record_invoice_order(1, 900001, db=self.db)
        second = order_links.record_invoice_order(1, 900001, db=self.db)
        self.assertEqual(first, second)
        row = self.connection.execute(
            "select amount_cents from invoice_orders where invoice_id = 1 and service_order_id = 900001"
        ).fetchone()
        self.assertEqual(row['amount_cents'], 123456)

    def test_one_invoice_covers_several_orders(self):
        self._invoice(1, service_order_id=900001)
        for order_id in (900001, 900002, 900003):
            order_links.record_invoice_order(1, order_id, db=self.db)
        self.assertEqual(order_links.invoice_order_ids(1, db=self.db), [900001, 900002, 900003])
        # 每个工单读到的都应是同一张发票（这正是工单页/发票页要的结果）
        for order_id in (900001, 900002, 900003):
            self.assertEqual(order_links.invoice_ids_for_service_order(order_id, db=self.db), [1])
            invoice = service_order_active_invoice(order_id, db=self.db)
            self.assertIsNotNone(invoice)
            self.assertEqual(invoice['id'], 1)
        # 第二张工单上再开一张有效发票应由索引拦住（业务层看到的顺序不变）
        self.assertEqual(order_links.primary_order_id({'id': 1, 'service_order_id': None}, db=self.db), 900001)

    def test_void_releases_order_and_reading_does_not_fall_back(self):
        self._invoice(1, service_order_id=900001)
        order_links.record_invoice_order(1, 900001, db=self.db)
        released = order_links.release_invoice_orders(1, db=self.db)
        self.assertEqual(released, 1)
        self.assertEqual(order_links.invoice_order_ids(1, db=self.db), [])
        self.assertEqual(order_links.invoice_ids_for_service_order(900001, db=self.db), [])
        self.assertIsNone(service_order_active_invoice(900001, db=self.db))
        # 已释放的关联仍可追溯
        self.assertEqual(order_links.invoice_order_ids(1, db=self.db, include_released=True), [900001])
        self.assertEqual(order_links.invoice_ids_for_service_order(900001, db=self.db, include_released=True), [1])

    def test_reopen_after_void_keeps_history(self):
        self._invoice(1, service_order_id=900001)
        order_links.record_invoice_order(1, 900001, db=self.db)
        order_links.release_invoice_orders(1, db=self.db)
        self._invoice(2, service_order_id=900001, issue_date='2026-02-01')
        order_links.record_invoice_order(2, 900001, db=self.db)
        self.assertEqual(len(order_links.invoice_order_ids(1, db=self.db, include_released=True)), 1)
        self.assertEqual(order_links.invoice_ids_for_service_order(900001, db=self.db), [2])
        self.assertEqual(service_order_active_invoice(900001, db=self.db)['id'], 2)

    def test_unbackfilled_legacy_invoice_still_readable(self):
        self._invoice(7, service_order_id=900009)
        self.assertEqual(order_links.invoice_ids_for_service_order(900009, db=self.db), [7])
        self.assertEqual(service_order_active_invoice(900009, db=self.db)['id'], 7)

    def test_void_invoice_is_not_returned(self):
        self._invoice(8, service_order_id=900010, status='void')
        self.assertEqual(order_links.invoice_ids_for_service_order(900010, db=self.db), [])
        self.assertIsNone(service_order_active_invoice(900010, db=self.db))


class SqliteDegradationTest(unittest.TestCase):
    """SQLite 开发库没有 invoice_orders，写入必须静默退化为空操作。"""

    def setUp(self):
        self.connection = _Connection('sqlite')
        self.connection.raw.executescript(
            "create table invoices (id integer primary key, service_order_id integer,"
            " status text default 'submitted', issue_date text);"
        )
        self.db = lambda: self.connection

    def tearDown(self):
        self.connection.close()

    def test_writes_are_noop_and_reads_use_legacy_column(self):
        self.connection.execute(
            "insert into invoices (id, service_order_id, status, issue_date) values (1, 500, 'submitted', '2026-01-01')"
        )
        self.assertIsNone(order_links.record_invoice_order(1, 500, db=self.db))
        self.assertEqual(order_links.release_invoice_orders(1, db=self.db), 0)
        self.assertEqual(order_links.invoice_order_ids(1, db=self.db), [500])
        self.assertEqual(service_order_active_invoice(500, db=self.db)['id'], 1)


class VoidTimestampTest(unittest.TestCase):
    def test_released_at_is_timezone_aware(self):
        value = order_links._timestamp()
        self.assertIsInstance(value, datetime)
        self.assertIsNotNone(value.tzinfo)
        self.assertLess(abs((datetime.now(timezone.utc) - value).total_seconds()), 60)
        self.assertEqual(
            order_links._timestamp(lambda: 'fixed-value'), 'fixed-value'
        )
        self.assertIsInstance(order_links._timestamp(lambda: None), datetime)


if __name__ == '__main__':
    unittest.main()
