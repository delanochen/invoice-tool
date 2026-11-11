"""删除发票必须释放 invoice_orders 关联。

生产事故（0.1.474）：删除发票只清了结算的 invoice_id 与发票行，关联行仍「未释放」，
工单列表显示未开票、合并开票页却列不出这些工单。这个测试锁住删除入口的调用顺序。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ''))

from flask import Flask  # noqa: E402

from invoice_tool.invoices.delete_routes import register_invoice_delete_routes  # noqa: E402

SCHEMA = """
create table invoices (id integer primary key, invoice_number text, status text, service_order_id integer);
create table customer_reimbursements (id integer primary key, service_order_id integer, invoice_id integer);
create table messages (id integer primary key, link text);
create table posting_events (id integer primary key, source_type text, source_id integer, event_type text);
create table invoice_orders (
    id integer primary key, invoice_id integer, service_order_id integer,
    customer_reimbursement_id integer, amount_cents integer, released_at text
);
"""


def build_app(conn, calls):
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.secret_key = 'test'

    def require_invoice_access(invoice_id):
        row = conn.execute("select * from invoices where id = ?", (invoice_id,)).fetchone()
        if not row:
            raise SystemExit('missing invoice')
        return {'id': row[0], 'invoice_number': row[1], 'status': row[2]}

    def release_invoice_orders(invoice_id, **kwargs):
        calls.append(('release', invoice_id))
        conn.execute(
            "update invoice_orders set released_at = 'now' where invoice_id = ? and released_at is null",
            (invoice_id,),
        )
        conn.commit()
        return 2

    app.add_url_rule('/invoices/<int:invoice_id>', endpoint='invoice_detail', view_func=lambda invoice_id: '')
    app.add_url_rule('/invoices/<int:invoice_id>/edit', endpoint='edit_invoice', view_func=lambda invoice_id: '')
    app.add_url_rule('/invoices', endpoint='invoices', view_func=lambda: '')

    register_invoice_delete_routes(
        app,
        IntegrityError=sqlite3.IntegrityError,
        db=lambda: conn,
        has_action_permission=lambda *args, **kwargs: True,
        invoice_attachment_path=lambda invoice_id: '/tmp/none',
        log_action=lambda *args, **kwargs: calls.append(('log', args[4] if len(args) > 4 else '')),
        login_required=lambda view: view,
        release_invoice_orders=release_invoice_orders,
        require_invoice_access=require_invoice_access,
        resolve_shutil=lambda: __import__('shutil'),
    )
    return app


def test_delete_invoice_releases_order_links():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("insert into invoices (id, invoice_number, status, service_order_id) values (40, 'INV-40', 'completed', 35)")
    conn.execute("insert into customer_reimbursements (id, service_order_id, invoice_id) values (59, 35, 40)")
    conn.execute("insert into invoice_orders (id, invoice_id, service_order_id, customer_reimbursement_id, amount_cents, released_at) values (16, 40, 35, 59, 486000, null)")
    conn.commit()

    calls = []
    app = build_app(conn, calls)
    client = app.test_client()
    response = client.post('/invoices/40/delete')

    assert response.status_code == 302
    assert ('release', 40) in calls, 'release_invoice_orders 没被调用'
    assert calls.index(('release', 40)) < len(calls), '释放必须先于删除'
    assert conn.execute("select count(*) from invoices where id = 40").fetchone()[0] == 0
    assert conn.execute("select invoice_id from customer_reimbursements where id = 59").fetchone()[0] is None
    assert conn.execute("select released_at from invoice_orders where id = 16").fetchone()[0] is not None
    logged = [call[1] for call in calls if call[0] == 'log']
    assert logged and '释放' in logged[0], logged
