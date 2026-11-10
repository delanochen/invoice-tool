"""合并开票选单页：路由注册、候选过滤（已开票工单/已生成发票的结算不出现）。"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ''))

from flask import Flask, g  # noqa: E402

from invoice_tool.query_pages.invoices import register_invoice_list_routes  # noqa: E402

SCHEMA = """
create table clients (id integer primary key, name text);
create table service_orders (
    id integer primary key, order_number text, client_order_number text, client_name text,
    client_id integer, status text
);
create table customer_reimbursements (
    id integer primary key, service_order_id integer, total_amount real, status text,
    invoice_id integer, created_at text
);
create table invoices (
    id integer primary key, invoice_number text, status text, service_order_id integer,
    issue_date text, client_id integer, paid_at text, created_by integer, currency text
);
"""


def build_app(conn):
    app = Flask(__name__)
    app.config['TESTING'] = True

    def login_required():
        return lambda view: view

    def render_template(name, **context):
        rows = [row for rows in context.get('groups', {}).values() for row in rows]
        return '%s|%s|%s' % (
            name,
            len(context.get('candidates', [])),
            ','.join(row['order_number'] for row in rows),
        )

    views = register_invoice_list_routes(
        app,
        _cb_resolve_STATUS_LABELS=lambda: {},
        _cb_resolve_abort=lambda code: (_ for _ in ()).throw(SystemExit(code)),
        _cb_resolve_can_view_invoices=lambda: (lambda: True),
        _cb_resolve_client_filter_clause=lambda: (lambda table: ('1=1', [])),
        _cb_resolve_db=lambda: (lambda: conn),
        _cb_resolve_invoice_totals=lambda: (lambda invoice_id: {'total': 0}),
        _cb_resolve_login_required=login_required,
        _cb_resolve_render_template=lambda: render_template,
        _cb_resolve_request=lambda: __import__('flask').request,
    )
    return app, views


def seed(conn):
    conn.executescript(SCHEMA)
    conn.execute("insert into clients (id, name) values (1, 'Acme')")
    conn.execute("insert into service_orders (id, order_number, client_order_number, client_name, client_id, status)"
                 " values (1, 'SO1', 'PO1', 'Acme', 1, 'settling')")
    conn.execute("insert into service_orders (id, order_number, client_order_number, client_name, client_id, status)"
                 " values (2, 'SO2', 'PO2', 'Acme', 1, 'settling')")
    conn.execute("insert into service_orders (id, order_number, client_order_number, client_name, client_id, status)"
                 " values (3, 'SO3', 'PO3', 'Acme', 1, 'settling')")
    conn.execute("insert into customer_reimbursements (id, service_order_id, total_amount, status, invoice_id, created_at)"
                 " values (11, 1, 100.0, 'approved', null, '2026-11-09')")
    conn.execute("insert into customer_reimbursements (id, service_order_id, total_amount, status, invoice_id, created_at)"
                 " values (12, 2, 200.0, 'approved', 7, '2026-11-09')")
    conn.execute("insert into customer_reimbursements (id, service_order_id, total_amount, status, invoice_id, created_at)"
                 " values (13, 3, 300.0, 'approved', null, '2026-11-09')")
    conn.commit()


def connect():
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    seed(conn)
    return conn


def test_merge_route_registered():
    conn = connect()
    app, views = build_app(conn)
    rules = {rule.rule: rule.endpoint for rule in app.url_map.iter_rules()}
    assert rules['/invoices/merge'] == 'invoice_merge'
    assert views['invoice_merge'] is app.view_functions['invoice_merge']
    assert views['invoices'] is app.view_functions['invoices']


def test_merge_candidates_exclude_billed_settlements_and_invoiced_orders():
    conn = connect()
    app, _views = build_app(conn)
    # 工单 3 已被另一张有效发票占用 → 它的结算也不该出现在候选里
    conn.execute("insert into invoices (id, invoice_number, status, service_order_id, issue_date, client_id, paid_at)"
                 " values (99, 'INV-9', 'completed', 3, '2026-11-09', 1, null)")
    conn.commit()
    with app.test_request_context('/invoices/merge'):
        g.user = {'id': 1}
        html = app.view_functions['invoice_merge']()
    assert html.startswith('invoice_merge.html')
    parts = html.split('|')
    assert parts[1] == '1', parts
    assert parts[2] == 'SO1', parts
