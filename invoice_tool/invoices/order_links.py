"""Phase 1 兼容层：invoice_orders 关系表的读写。

约束与取舍：
* 关系表只存在于 PostgreSQL（`migrations/postgresql/0319-invoice-orders.sql`），SQLite 开发库没有该表，
  所以这些函数在 SQLite 上退化为"读旧列 / 不写关系表"，现有单测不受影响。
* 关系表写入用 SAVEPOINT 包住：表缺失或写入失败时只回滚该保存点，发票本身的创建/作废照旧成功，
  由 `scripts/backfill_invoice_orders.py` 幂等补回填兜底。
* Phase 1 只做写入兼容（双写 + 作废释放）；读取仍以旧列为主，`invoice_ids_for_service_order`
  用于把"工单 → 发票"的读取逐步切到关系表。
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


def invoice_order_sql(connection, invoice_alias='invoices', order_expr='service_orders.id'):
    """布尔 SQL 表达式：这张发票属于该工单（旧列命中，或关系表里有未释放的关联）。

    用于列表/统计/导出里"按工单筛发票"的地方。SQLite 上没有关系表，退化为只看旧列。
    `order_expr` 可以是列引用，也可以是 `?` 占位符（调用方按 clause.count('?') 补参数）。
    """
    legacy = "%s.service_order_id = %s" % (invoice_alias, order_expr)
    if not supports_invoice_orders(connection):
        return legacy
    link_alias = "io_%s" % invoice_alias.replace('.', '_')
    return (
        "(%s or exists (select 1 from invoice_orders %s"
        " where %s.invoice_id = %s.id and %s.service_order_id = %s and %s.released_at is null))"
        % (legacy, link_alias, link_alias, invoice_alias, link_alias, order_expr, link_alias)
    )


def invoice_order_params(clause, order_id):
    """按子句里 `?` 的个数重复工单 id，避免调用方手数占位符。"""
    return (order_id,) * str(clause).count('?')


def orders_for_invoices(invoice_ids, *, db, include_released=False):
    """发票 → 覆盖的工单列表（多工单展示用）。

    关系表里有该发票的记录（含已释放）就以表为准；没有记录（尚未回填）才回退旧列。
    返回 {invoice_id: [{'service_order_id': .., 'order_number': ..}, ...]}。
    """
    ids = []
    for value in invoice_ids or []:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number and number not in ids:
            ids.append(number)
    if not ids:
        return {}
    connection = db()
    result = {}
    if supports_invoice_orders(connection):
        try:
            rows = connection.execute(
                "select io.invoice_id, io.service_order_id, io.released_at, so.order_number,"
                " so.client_order_number"
                " from invoice_orders io left join service_orders so on so.id = io.service_order_id"
                " where io.invoice_id in (%s) order by io.id" % ", ".join("?" for _ in ids),
                tuple(ids),
            ).fetchall()
        except Exception:
            rows = []
        seen = set()
        for row in rows:
            seen.add(row['invoice_id'])
            if row['released_at'] is not None and not include_released:
                continue
            result.setdefault(row['invoice_id'], []).append({
                'service_order_id': row['service_order_id'],
                'order_number': row['order_number'],
                'client_order_number': row['client_order_number'],
            })
        if seen:
            # 表里已有记录的发票不再回退旧列，避免已释放的关联被"复活"
            ids = [number for number in ids if number not in seen]
            if not ids:
                return result
    rows = connection.execute(
        "select invoices.id, invoices.service_order_id, so.order_number, so.client_order_number"
        " from invoices left join service_orders so on so.id = invoices.service_order_id"
        " where invoices.id in (%s)" % ", ".join("?" for _ in ids),
        tuple(ids),
    ).fetchall()
    for row in rows:
        if not row['service_order_id']:
            continue
        result[row['id']] = [{
            'service_order_id': row['service_order_id'],
            'order_number': row['order_number'],
            'client_order_number': row['client_order_number'],
        }]
    return result


def service_order_link_state(service_order_id, *, db):
    """工单在关系表里的状态。

    返回 {'has_rows': bool, 'active_invoice_ids': [...]}。
    has_rows=True 表示关系表已经覆盖这个工单（含已释放），此时调用方必须以表为准，
    不能再回退旧列——否则已经释放开票资格的工单会被旧列"复活"成仍有发票。
    """
    if not service_order_id:
        return {'has_rows': False, 'active_invoice_ids': []}
    connection = db()
    if not supports_invoice_orders(connection):
        return {'has_rows': False, 'active_invoice_ids': []}
    try:
        rows = connection.execute(
            "select invoice_id, released_at from invoice_orders where service_order_id = ? order by id",
            (service_order_id,),
        ).fetchall()
    except Exception:
        return {'has_rows': False, 'active_invoice_ids': []}
    active = [row['invoice_id'] for row in rows if row['released_at'] is None]
    return {'has_rows': bool(rows), 'active_invoice_ids': active}


def invoice_ids_for_service_order(service_order_id, *, db, include_released=False):
    """工单被哪些发票覆盖（一张发票可覆盖多个工单）。

    关系表里有该工单的记录（含已释放）就以表为准；表缺失或没有记录时回退旧列，
    保证回填之前创建的老发票仍能被查到。
    """
    if not service_order_id:
        return []
    connection = db()
    state = service_order_link_state(service_order_id, db=db)
    if state['has_rows'] and not include_released:
        return state['active_invoice_ids']
    if state['has_rows']:
        try:
            rows = connection.execute(
                "select invoice_id from invoice_orders where service_order_id = ? order by id",
                (service_order_id,),
            ).fetchall()
        except Exception:
            rows = []
        return [row['invoice_id'] for row in rows]
    row = connection.execute(
        "select id from invoices where service_order_id = ? and status != 'void'"
        " order by issue_date desc, id desc limit 1",
        (service_order_id,),
    ).fetchone()
    return [row['id']] if row else []


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


def work_order_label(order):
    external = order["client_order_number"]
    internal = order["order_number"]
    return f"{external}（{internal}）" if external and internal else external or internal or ""


def current_invoice_clause(connection, alias="invoices"):
    """Exclude void invoices and invoices whose work-order links were all released."""
    clause = f"{alias}.status = 'completed'"
    if supports_invoice_orders(connection):
        clause += (
            f" and (not exists (select 1 from invoice_orders historical where historical.invoice_id = {alias}.id)"
            f" or exists (select 1 from invoice_orders current_link where current_link.invoice_id = {alias}.id"
            " and current_link.released_at is null))"
        )
    return clause
