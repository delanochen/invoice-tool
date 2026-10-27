"""Apply the bounded expected settlement date feature to compatible deployments."""
from pathlib import Path


def replace(path, old, new, count=1):
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    assert text.count(old) == count, (path, old[:80], text.count(old))
    p.write_text(text.replace(old, new), encoding='utf-8')


def apply():
    routes = 'invoice_tool/service_orders/routes.py'
    replace(routes, 'import shutil\n', 'import shutil\nfrom .settlement_dates import optional_settlement_date, settlement_date_filters\n')
    replace(routes, '        if buyer_id.isdigit():', '        settlement_filters = settlement_date_filters(request.args, clauses, params)\n        if buyer_id.isdigit():')
    if 'buyer_name=buyer_name)' in Path(routes).read_text(encoding='utf-8'):
        replace(routes, 'buyer_name=buyer_name)', 'buyer_name=buyer_name, **settlement_filters)')
    else:
        replace(routes, '            buyer_name=buyer_name,', '            buyer_name=buyer_name,\n            **settlement_filters,')
    replace(routes, 'client_order_number, start_date, work_order_type_id,', 'client_order_number, start_date, expected_settlement_date, work_order_type_id,')
    replace(routes, ') values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', ') values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')
    replace(routes, '                    request.form.get("start_date") or None,\n                    work_order_type["id"],', '                    request.form.get("start_date") or None,\n                    optional_settlement_date(request.form.get("expected_settlement_date")),\n                    work_order_type["id"],', 2)
    replace(routes, 'client_order_number = ?, start_date = ?,', 'client_order_number = ?, start_date = ?, expected_settlement_date = ?,')
    query = 'invoice_tool/query_pages/orders.py'
    replace(query, '"""Business routes with explicit current-app resolvers; no reverse import."""', '"""Business routes with explicit current-app resolvers; no reverse import."""\nfrom invoice_tool.service_orders.settlement_dates import settlement_date_filters')
    replace(query, '        if status:\n', '        settlement_filters = settlement_date_filters(_cb_resolve_request().args, clauses, params)\n        if status:\n')
    replace(query, '            status=status,\n', '            status=status,\n            **settlement_filters,\n')
    replace('templates/service_order_form.html', '        <label>开始日期 <input type="date" name="start_date" value="{{ order.start_date if order and order.start_date else \'\' }}"></label>', '        <label>开始日期 <input type="date" name="start_date" value="{{ order.start_date if order and order.start_date else \'\' }}"></label>\n        <label>预计结算日 <input type="date" name="expected_settlement_date" value="{{ order.expected_settlement_date if order and order.expected_settlement_date else \'\' }}"></label>')
    replace('templates/service_order_detail.html', '          <p><strong>开始日期</strong><span>{{ order.start_date or "未维护" }}</span></p>', '          <p><strong>开始日期</strong><span>{{ order.start_date or "未维护" }}</span></p>\n          <p><strong>预计结算日</strong><span>{{ order.expected_settlement_date or "未维护" }}</span></p>')
    for name in ['templates/service_orders.html', 'templates/service_order_query.html']:
        replace(name, '<th>开始日期</th>', '<th>开始日期</th><th>预计结算日</th>')
        replace(name, '<td>{{ order.start_date or "-" }}</td>', '<td>{{ order.start_date or "-" }}</td>\n                <td>{{ order.expected_settlement_date or "-" }}</td>')
        replace(name, '            <div class="erp-card-metrics">', '            <div class="erp-card-metrics">\n              <div class="erp-card-metric"><span>预计结算日</span><strong>{{ order.expected_settlement_date or \'-\' }}</strong></div>')
    p=Path('templates/service_orders.html');s=p.read_text(encoding='utf-8')
    import re
    s,n=re.subn(r'grid_columns = (\d+) \+', lambda m:'grid_columns = '+str(int(m[1])+1)+' +',s,count=1)
    assert n==1
    # The deployed list already has a keyword filter; retain it and its ERP classes.
    inputs='''      <label class="erp-field">预计结算日起 <input type="date" name="expected_settlement_from" value="{{ expected_settlement_from }}"></label>
      <label class="erp-field">预计结算日止 <input type="date" name="expected_settlement_to" value="{{ expected_settlement_to }}"></label>
'''
    existing=next((match for match in re.finditer(r'<form\b[^>]*>.*?</form>',s,re.S) if 'name="q"' in match[0]),None)
    if existing:
        form=existing[0]
        if 'data-erp-filter' not in form:
            form=form.replace('method="get"','method="get" data-erp-filter',1)
        form=form.replace('</form>',inputs+'</form>',1)
        s=s[:existing.start()]+form+s[existing.end():]
    else:
        marker='    <section class="erp-panel active" data-erp-panel="list">'
        form='''    <form class="erp-filter erp-filterbar no-print" method="get" data-erp-filter>
      <label class="erp-field">关键词 <input type="search" name="q" value="{{ q }}"></label>
      {% if buyer_id %}<input type="hidden" name="buyer_id" value="{{ buyer_id }}">{% endif %}
'''+inputs+'''      <button type="submit" class="erp-btn primary">查询</button>
      <button type="button" class="erp-btn" data-erp-action="reset">重置</button>
    </form>
'''
        assert s.count(marker)==1;s=s.replace(marker,form+marker)
    p.write_text(s,encoding='utf-8')
    replace('templates/service_order_query.html', '      <label class="erp-field">状态', inputs+'      <label class="erp-field">状态')
    replace('templates/service_order_query.html', 'can_view_invoices() %}12{% else %}11', 'can_view_invoices() %}13{% else %}12')

    p=Path('static/ui-i18n-supplement.js');s=p.read_text(encoding='utf-8');assert '"预计结算日":' not in s
    s=s.replace('  "en": {','  "en": {\n    "预计结算日": "Expected settlement date",\n    "预计结算日起": "Expected settlement from",\n    "预计结算日止": "Expected settlement to",',1);p.write_text(s,encoding='utf-8')
    p=Path('tests/schema_postgresql.sql');s=p.read_text(encoding='utf-8');start=s.index('CREATE TABLE public.service_orders (');end=s.index('\n);',start);block=s[start:end];assert 'expected_settlement_date' not in block;s=s[:start]+block.replace('    start_date text,','    start_date text,\n    expected_settlement_date text,')+s[end:];p.write_text(s,encoding='utf-8')


if __name__ == '__main__':
    apply()
