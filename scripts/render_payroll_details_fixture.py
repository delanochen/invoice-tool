"""Render synthetic payroll detail fixture with the actual bundled grid scripts."""
from pathlib import Path
import sys
from flask import Flask,render_template
from jinja2 import ChoiceLoader,DictLoader
root=Path(__file__).resolve().parents[1]
app=Flask(__name__,template_folder=str(root/'templates'))
base='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>__CSS__</style>{% block head %}{% endblock %}</head><body data-grid-user="fixture">{% block content %}{% endblock %}<script>__TABULATOR__</script><script>__GRID__</script>{% block scripts %}{% endblock %}</body></html>'''
base=base.replace('__CSS__','\n'.join((root/'static'/p).read_text() for p in ['styles.css','erp-ui.css','system-grid.css','vendor/tabulator/tabulator.min.css']))
base=base.replace('__TABULATOR__',(root/'static/vendor/tabulator/tabulator.min.js').read_text())
base=base.replace('__GRID__',(root/'static/system-grid.js').read_text())
app.jinja_loader=ChoiceLoader([DictLoader({'base.html':base}),app.jinja_loader])
app.jinja_env.globals['url_for']=lambda endpoint,**kwargs: ('#'+endpoint if endpoint!='static' else 'fixture-static/'+kwargs['filename'])
app.jinja_env.filters['money']=lambda value:'USD %.2f'%float(value)
rows=[]
for index,(name,amount) in enumerate([('WORKER_A',195),('WORKER_B',99)],1):
 rows.append(dict(employee_id=index,employee_name=name,period_start='2026-10-12',period_end='2026-10-25',
 expected=800+amount,recorded=800,paid=800,difference=amount,orders=[],can_create=False,
 blocked_reason='Fixture only',changes=[dict(service_date='2026-10-15',work_order_id=101+index,
 component_name='DETAIL_ONLY_'+name,amount=amount,daily_report_id=20+index)]))
with app.test_request_context('/employee-payments/payroll-reconciliation'):
 html=render_template('payroll_reconciliation.html',reconciliation_rows=rows,unlinked_manual=[],app_version='fixture',has_action_permission=lambda *args:False)
html=html.replace('<script src="fixture-static/modal-forms.js"></script>',
                 '<script>'+(root/'static/modal-forms.js').read_text()+'</script>')
html=html.replace('<link rel="stylesheet" href="fixture-static/erp-ui.css">','')
Path(sys.argv[1]).write_text(html)
print('Rendered synthetic browser fixture')
