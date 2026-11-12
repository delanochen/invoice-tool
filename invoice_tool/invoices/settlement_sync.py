"""Apply an approved settlement revision to its existing invoice."""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from .order_links import invoice_amount_cents, supports_invoice_orders


def _cents(amount):
    return int((Decimal(str(amount or 0)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _project_amounts(rows):
    amounts = defaultdict(int)
    for row in rows:
        amounts[(row['project_id'], Decimal(str(row['tax_rate'] or 0)))] += _cents(row['amount'])
    return amounts


def sync_revised_settlement_invoice(api, invoice, previous, revision):
    connection = api['db']()
    invoice_id = invoice['id']
    postgres = supports_invoice_orders(connection)
    lock = ' for update' if postgres else ''
    current = connection.execute('select * from invoices where id = ?' + lock, (invoice_id,)).fetchone()
    if not current or current['status'] != 'completed':
        raise ValueError('在用发票已变更，请刷新后重试。')

    links = connection.execute(
        'select * from invoice_orders where invoice_id = ? and released_at is null order by id',
        (invoice_id,),
    ).fetchall() if postgres else []
    source = next((row for row in links if row['service_order_id'] == previous['service_order_id']), None)
    if links and not source:
        raise ValueError('发票已解除与本工单的关联，请刷新后重试。')
    before = _cents(previous['total_amount'])
    after = _cents(revision['total_amount'])
    invoice_before = invoice_amount_cents(invoice_id, db=api['db'])
    if (source and (source['amount_cents'] != before or
                    sum(row['amount_cents'] for row in links) != invoice_before)
            or not source and invoice_before != before):
        raise ValueError('原工单结算与在用发票金额不一致，请先核对原始金额。')

    if before != after:
        if current['paid_at'] or (api['accounting_base_enabled']() and connection.execute(
            "select 1 from posting_events where source_type = 'invoice' and source_id = ? "
            "and event_type = 'invoice.confirmed' limit 1", (invoice_id,),
        ).fetchone()):
            raise ValueError('发票已收款或入账，请先通过现有财务更正流程处理差额。')
        _apply_amount_delta(api, connection, invoice_id, previous, revision, after - before)

    if source:
        connection.execute(
            'update invoice_orders set customer_reimbursement_id = ?, amount_cents = ? where id = ?',
            (revision['id'], after, source['id']),
        )


def _apply_amount_delta(api, connection, invoice_id, previous, revision, expected_delta):
    existing_rows = connection.execute(
        'select * from invoice_items where invoice_id = ? order by id', (invoice_id,),
    ).fetchall()
    before = _project_amounts(api['customer_reimbursement_invoice_items'](previous))
    after = _project_amounts(api['customer_reimbursement_invoice_items'](revision))
    deltas = {key: after.get(key, 0) - before.get(key, 0) for key in before.keys() | after.keys()}
    if sum(deltas.values()) != expected_delta:
        raise ValueError('修订版的发票项目差额与工单结算差额不一致，请核对发票项目映射。')
    existing = defaultdict(list)
    for row in existing_rows:
        existing[(row['project_id'], Decimal(str(row['tax_rate'] or 0)))].append(row)
    for (project_id, tax_rate), delta in sorted(deltas.items()):
        if not delta:
            continue
        matches = existing[(project_id, tax_rate)]
        if len(matches) > 1:
            raise ValueError('发票存在重复的收费项目，请先核对该项目的金额。')
        amount = (_cents(matches[0]['amount']) if matches else 0) + delta
        if amount < 0:
            raise ValueError('修订差额超过原发票项目金额，请先核对发票项目。')
        if matches:
            connection.execute('update invoice_items set amount = ? where id = ?',
                               (float(Decimal(amount) / 100), matches[0]['id']))
        else:
            project = connection.execute('select name from projects where id = ?', (project_id,)).fetchone()
            connection.execute(
                'insert into invoice_items (invoice_id, project_id, description, amount, tax_rate) values (?, ?, ?, ?, ?)',
                (invoice_id, project_id, project['name'], float(Decimal(amount) / 100), float(tax_rate)),
            )
