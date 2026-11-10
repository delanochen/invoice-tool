"""Phase 1 兼容层：invoice_orders 关系表的读写。

约束与取舍：
* 关系表只存在于 PostgreSQL（`migrations/postgresql/0319-invoice-orders.sql`），SQLite 开发库没有该表，
  所以这些函数在 SQLite 上退化为"读旧列 / 不写关系表"，现有单测不受影响。
* 关系表写入用 SAVEPOINT 包住：表缺失或写入失败时只回滚该保存点，发票本身的创建/作废照旧成功，
  由 `scripts/backfill_invoice_orders.py` 幂等补回填兜底。
* Phase 1 不改读取路径：这里只提供双写与释放，读取仍走 `invoices.service_order_id`。
"""
from datetime import datetime, timezone


def _timestamp(now=None):
    if callable(now):
        value = now()
        return value if value is not None else datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


def supports_invoice_orders(connection):
    return getattr(connection, 'dialect', 'sqlite') == 'postgresql'


def invoice_amount_cents(invoice_id, *, db):
    row = db().execute(
        "select coalesce(sum(amount), 0) as total from invoice_items where invoice_id = ?",
        (invoice_id,),
    ).fetchone()
    total = row['total'] if row else 0
    return int(round(float(total or 0) * 100))


def record_invoice_order(invoice_id, service_order_id, *, db, customer_reimbursement_id=None,
                         amount_cents=None, released_at=None, now=None):
    """把"这张发票覆盖这个工单"写进关系表；重复调用幂等。返回关系行 id 或 None。"""
    if not invoice_id or not service_order_id:
        return None
    connection = db()
    if not supports_invoice_orders(connection):
        return None
    if amount_cents is None:
        amount_cents = invoice_amount_cents(invoice_id, db=db)
    existing = connection.execute(
        "select id from invoice_orders where invoice_id = ? and service_order_id = ?",
        (invoice_id, service_order_id),
    ).fetchone()
    if existing:
        return existing['id']
    try:
        cursor = connection.execute(
            "insert into invoice_orders (invoice_id, service_order_id, customer_reimbursement_id, amount_cents, released_at, created_at)"
            " values (?, ?, ?, ?, ?, ?)",
            (invoice_id, service_order_id, customer_reimbursement_id, int(amount_cents or 0), released_at, _timestamp(now)),
        )
    except Exception:
        # 数据库层每条语句自带保存点，失败只会回滚这条 insert，外层发票事务继续可用；
        # 关系表缺失（迁移未执行）时同样落在这里，由回填脚本事后补齐。
        return None
    return cursor.lastrowid


def release_invoice_orders(invoice_id, *, db, now=None):
    """作废时释放该发票占用的工单开票资格（released_at 有空值即占用）。返回释放行数。"""
    if not invoice_id:
        return 0
    connection = db()
    if not supports_invoice_orders(connection):
        return 0
    try:
        cursor = connection.execute(
            "update invoice_orders set released_at = ? where invoice_id = ? and released_at is null",
            (_timestamp(now), invoice_id),
        )
    except Exception:
        return 0
    return getattr(cursor, 'rowcount', 0) or 0


def invoice_order_ids(invoice_id, *, db, include_released=False):
    """发票覆盖的工单 id 列表（默认只返回仍有效的关联）。"""
    if not invoice_id:
        return []
    connection = db()
    if supports_invoice_orders(connection):
        try:
            rows = connection.execute(
                "select service_order_id, released_at from invoice_orders where invoice_id = ? order by id",
                (invoice_id,),
            ).fetchall()
        except Exception:
            rows = []
        if rows:
            # 表里已有该发票的关联记录（包括已释放的），就以表为准：
            # 已释放不该再回退到旧列，否则作废后的工单会被误判为仍被占用。
            return [
                row['service_order_id'] for row in rows
                if include_released or row['released_at'] is None
            ]
    row = connection.execute(
        "select service_order_id from invoices where id = ?", (invoice_id,)
    ).fetchone()
    legacy = row['service_order_id'] if row else None
    return [legacy] if legacy else []


def primary_order_id(invoice, *, db):
    """读取侧过渡用：优先关系表，其次旧列。invoice 是行对象（sqlite3.Row / 本仓库的 Row）。"""
    if not invoice:
        return None
    try:
        invoice_id = invoice['id']
    except Exception:
        invoice_id = None
    if invoice_id:
        order_ids = invoice_order_ids(invoice_id, db=db)
        if order_ids:
            return order_ids[0]
    try:
        return invoice['service_order_id'] or None
    except Exception:
        return None
