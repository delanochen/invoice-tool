"""Customer-facing work-order settlement workbook."""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from invoice_tool.invoices.order_links import (
    current_invoice_clause, invoice_amount_cents, service_order_link_state,
    supports_invoice_orders, work_order_label,
)


def number(value):
    return Decimal(str(value or 0))


def cents(value):
    return int((number(value) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def export_context(connection, order_id, reimbursement_id=None):
    order = connection.execute('select * from service_orders where id = ?', (order_id,)).fetchone()
    if not order:
        raise ValueError(f'工单 {order_id} 不存在。')
    label = work_order_label(order)
    settlement = connection.execute(
        'select * from customer_reimbursements where service_order_id = ? and is_current = true',
        (order_id,),
    ).fetchone()
    if not settlement or settlement['status'] != 'approved' or (
        reimbursement_id is not None and settlement['id'] != reimbursement_id
    ):
        raise ValueError(f'{label}：请先完成当前工单结算审核。')
    active = current_invoice_clause(connection)
    state = service_order_link_state(order_id, db=lambda: connection)
    invoice_ids = state['active_invoice_ids']
    if state['has_rows']:
        placeholders = ', '.join('?' for _ in invoice_ids)
        invoices = connection.execute(
            f"select id, invoice_number from invoices where {active} and id in ({placeholders})",
            invoice_ids,
        ).fetchall() if invoice_ids else []
    else:
        invoices = connection.execute(
            f"select id, invoice_number from invoices where {active} and service_order_id = ?",
            (order_id,),
        ).fetchall()
    if len(invoices) != 1 or not str(invoices[0]['invoice_number'] or '').strip():
        raise ValueError(f'{label}：请先确认唯一的在用发票号。')
    invoice = invoices[0]
    link = None
    if supports_invoice_orders(connection):
        link = connection.execute(
            'select amount_cents from invoice_orders where invoice_id = ? and service_order_id = ? and released_at is null',
            (invoice['id'], order_id),
        ).fetchone()
    billed = link['amount_cents'] if link else invoice_amount_cents(invoice['id'], db=lambda: connection)
    if billed != cents(settlement['total_amount']):
        raise ValueError(f'{label}：在用发票对应金额与审核通过的工单结算不一致。')
    if link:
        allocation = connection.execute(
            'select sum(amount_cents) as total from invoice_orders where invoice_id = ? and released_at is null',
            (invoice['id'],),
        ).fetchone()['total']
        if allocation != invoice_amount_cents(invoice['id'], db=lambda: connection):
            raise ValueError(f'{label}：在用发票金额与关联工单金额合计不一致。')
    dates = connection.execute(
        'select min(report_date) as first_date, max(report_date) as last_date from service_reports where service_order_id = ?',
        (order_id,),
    ).fetchone()
    if cents(settlement['rental_fuel_total']):
        raise ValueError(f'{label}：历史结算有单列租车油费，请通过结算修订归到垫付员工后再导出。')
    return order, settlement, invoice['invoice_number'], dates['first_date'], dates['last_date']


def batch_contexts(connection, order_ids):
    contexts, errors = {}, []
    for order_id in order_ids:
        try:
            contexts[order_id] = export_context(connection, order_id)
        except ValueError as error:
            errors.append(str(error))
    if errors:
        raise ValueError(' '.join(errors))
    return contexts


HOURS = (
    ('Regular Hours', 'Regular Rate/Hour', (('standard_hours', 'standard_rate'),)),
    ('Travel Hours', 'Travel Rate/Hour', (('transport_hours', 'transport_rate'), ('public_transport_hours', 'public_transport_rate'))),
    ('Over Time', 'Over Time/Rate', (('overtime_hours', 'overtime_rate'), ('holiday_hours', 'holiday_rate'))),
)
EXPENSES = (('Hotel', 'lodging'), ('Flight', 'airfare'), ('Checked Baggage', 'baggage'),
            ('Car Rental', 'rental_car'), ('Gas', 'fuel'), ('Parking', 'parking'),
            ('Uber/Taxi', 'taxi'), ('Others', 'other'))
LABOR = [spec[0] for spec in HOURS] + [spec[1] for spec in HOURS] + ['Labor Total']
MILEAGE = ['Miles', 'Mileage Rate/Mi', 'Mileage Total']
DAILY = ['Date', 'Name', *LABOR, *MILEAGE, *(label for label, _ in EXPENSES), 'Total']
SUMMARY = ['Name', 'Service Period', *LABOR, 'Hotel', *MILEAGE,
           *(label for label, _ in EXPENSES[1:]), 'Total']
RATE_FIELDS = {spec[1] for spec in HOURS} | {'Mileage Rate/Mi'}


def display_rate(rates):
    if len(rates) > 1:
        return 'Varies'
    return next(iter(rates), None)


def amounts(rows, expense_amount):
    values = {}
    for hours_label, rate_label, sources in HOURS:
        values[hours_label] = sum(number(row.get(hours)) for row in rows for hours, _ in sources)
        values[rate_label] = display_rate({number(row.get(rate)) for row in rows
                                          for hours, rate in sources if number(row.get(hours))})
    for label, field in [('Miles', 'miles'), ('Mileage Total', 'mileage_total'),
                         ('Labor Total', 'labor_total'), ('Total', 'total')]:
        values[label] = sum(number(row.get(field)) for row in rows)
    values['Mileage Rate/Mi'] = display_rate({number(row.get('mileage_rate')) for row in rows if number(row.get('miles'))})
    for label, field in EXPENSES:
        values[label] = sum(number(expense_amount(row, field)) for row in rows)
    return values


def period(first, last):
    return f'From {first} to {last}' if first and last else ''


def people(items):
    groups = {}
    names_by_id = {}
    for item in items:
        row = dict(item)
        name = str(row.get('worker_name') or '').strip()
        worker_id = row.get('source_worker_user_id') or row.get('expense_worker_user_id')
        if worker_id is not None:
            name = names_by_id.setdefault(worker_id, name)
        groups.setdefault((worker_id, name), []).append(row)
    # Older expense-only rows carry a name without an employee ID.
    for key in list(groups):
        worker_id, name = key
        if worker_id is None:
            matches = [other for other in groups if other[0] is not None and other[1] == name]
            if len(matches) == 1:
                groups[matches[0]].extend(groups.pop(key))
    return groups


def add_row(sheet, columns, values, *, fill=None, bold=False, white=False):
    sheet.append([values.get(column) for column in columns])
    for cell, column in zip(sheet[sheet.max_row], columns):
        if isinstance(cell.value, str):
            cell.data_type = 's'
        if fill:
            cell.fill = PatternFill('solid', fgColor=fill)
        cell.font = Font(name='Calibri', bold=bold, color='FFFFFF' if white else '243229')
        cell.alignment = Alignment(vertical='center', wrap_text=True)
        if column not in ('Date', 'Name', 'Service Period', 'Work Order', 'Invoice Number'):
            cell.number_format = '0.00'


def heading(sheet, columns, color):
    groups = {'Labor Cost': LABOR, 'Travel Cost': [label for label, _ in EXPENSES[1:-1]]}
    group_row = sheet.max_row + 1
    for label, members in groups.items():
        start, end = columns.index(members[0]) + 1, columns.index(members[-1]) + 1
        sheet.merge_cells(start_row=group_row, start_column=start, end_row=group_row, end_column=end)
        cell = sheet.cell(group_row, start, label)
        cell.fill = PatternFill('solid', fgColor=color)
        cell.font = Font(name='Calibri', bold=True, color='FFFFFF')
        cell.alignment = Alignment(horizontal='center')
    add_row(sheet, columns, dict(zip(columns, columns)), fill=color, bold=True, white=True)
    sheet.row_dimensions[sheet.max_row].height = 34


def band(sheet, text, width, color):
    row = sheet.max_row + 1
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=width)
    cell = sheet.cell(row, 1, text)
    cell.data_type = 's'
    cell.fill = PatternFill('solid', fgColor=color)
    cell.font = Font(name='Calibri', bold=True, color='FFFFFF')
    cell.alignment = Alignment(vertical='center', wrap_text=True)
    sheet.row_dimensions[row].height = 23


def workbook_sheet(title, columns):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    for index, column in enumerate(columns, 1):
        sheet.column_dimensions[get_column_letter(index)].width = (
            24 if column in ('Name', 'Service Period', 'Work Order', 'Invoice Number', 'Date') else 13
        )
    return workbook, sheet


def save(workbook):
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def write_summary(sheet, columns, groups, settlement, expense_amount, metadata):
    for index, ((_, name), rows) in enumerate(groups.items()):
        report_dates = [str(row['project_date']) for row in rows
                        if row.get('project_date') and row.get('source_report_id')]
        dates = sorted(report_dates or [str(row['project_date']) for row in rows if row.get('project_date')])
        service_period = period(dates[0], dates[-1]) if dates else ''
        add_row(sheet, columns, dict(amounts(rows, expense_amount), **metadata,
                                     Name=name, **{'Service Period': service_period}),
                fill='BCE4F2' if index % 2 else '86CDE5')
    rows = [row for group in groups.values() for row in group]
    totals = amounts(rows, expense_amount)
    delta = (cents(settlement['total_amount']) - cents(totals['Total'])) / Decimal(100)
    if delta:
        add_row(sheet, columns, dict(metadata, Name='Settlement Adjustment', Total=delta), fill='D9ECF4')
    totals.update({key: None for key in RATE_FIELDS})
    totals.update(metadata, Name='Work Order Total', Total=number(settlement['total_amount']))
    add_row(sheet, columns, totals, fill='D9ECF4', bold=True)


def build_expense_report(context, items, *, expense_amount, company=None):
    order, settlement, invoice_number, first, last = context
    workbook, sheet = workbook_sheet('Expense Report', DAILY)
    company = company or {}
    for value in (company.get('name'), company.get('address'), company.get('email'), company.get('phone')):
        if value:
            band(sheet, str(value), len(DAILY), '7C854B')
    for row in [
        {1: 'Work Order', 3: work_order_label(order), 8: 'Customer', 10: 'Tongfei (同飞)',
         15: 'Service Period', 17: 'From', 18: first},
        {1: 'Invoice Number', 3: invoice_number, 17: 'To', 18: last},
    ]:
        add_row(sheet, list(range(1, len(DAILY) + 1)), row, fill='E2E5D4')
    groups = people(items)
    for (worker_id, name), rows in groups.items():
        band(sheet, 'Worker Information', len(DAILY), '7C854B')
        add_row(sheet, ['Label', 'Value', 'ID Label', 'ID'],
                {'Label': 'Name', 'Value': name, 'ID Label': 'Employee ID',
                 'ID': str(worker_id) if worker_id is not None else None})
        heading(sheet, DAILY, '7C854B')
        by_day = defaultdict(list)
        for row in rows:
            by_day[str(row.get('project_date') or '')].append(row)
        for day, daily_rows in sorted(by_day.items()):
            add_row(sheet, DAILY, dict(amounts(daily_rows, expense_amount), Date=day, Name=name), fill='E2E5D4')
    band(sheet, 'Expense Summary', len(SUMMARY), '12465B')
    heading(sheet, SUMMARY, '12465B')
    write_summary(sheet, SUMMARY, groups, settlement, expense_amount, {})
    return save(workbook)


def build_batch_summary(payloads, *, expense_amount):
    columns = ['Work Order', 'Invoice Number', *SUMMARY]
    workbook, sheet = workbook_sheet('Expense Summary', columns)
    heading(sheet, columns, '12465B')
    sheet.freeze_panes = 'C4'
    for context, items in payloads:
        order, settlement, invoice_number, _, _ = context
        write_summary(sheet, columns, people(items), settlement, expense_amount,
                      {'Work Order': work_order_label(order), 'Invoice Number': invoice_number})
    total = sum(number(context[1]['total_amount']) for context, _ in payloads)
    add_row(sheet, columns, {'Name': 'Grand Total', 'Total': total}, fill='D9ECF4', bold=True)
    return save(workbook)
