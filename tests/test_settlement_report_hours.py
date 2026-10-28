"""Settlement report/export regression checks without a database connection."""
import inspect
import re
import unittest
from pathlib import Path
from unittest.mock import Mock

from flask import Flask, request

from invoice_tool.query_pages.settlement_routes import register_report_queries_routes


class SettlementReportHoursTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
        self.connection = Mock()
        self.context = {}
        dependencies = {name: (lambda: None) for name in inspect.signature(register_report_queries_routes).parameters if name != 'app'}
        dependencies.update({
            '_cb_resolve_login_required': lambda: lambda fn: fn,
            '_cb_resolve_can_view_customer_reimbursement': lambda: lambda: True,
            '_cb_resolve_is_external_manager': lambda: lambda: False,
            '_cb_resolve_request': lambda: request,
            '_cb_resolve_db': lambda: lambda: self.connection,
            '_cb_resolve_current_language': lambda: lambda: 'zh-CN',
            '_cb_resolve_CUSTOMER_REIMBURSEMENT_STATUS_LABELS': lambda: {'draft': '草稿'},
            '_cb_resolve_render_template': lambda: self.capture,
        })
        self.routes = register_report_queries_routes(self.app, **dependencies)

    def capture(self, name, **context):
        self.context = context
        return ''

    def test_hours_reach_report_and_export_table(self):
        rows = [dict(transport_hours=hours, labor_total=10, travel_total=0,
                     mileage_total=0, other_total=0, total_amount=10,
                     status='draft', created_at='2026-10-28', service_order_id=i,
                     order_number=f'SO-{i}', client_name='Test', reviewed_at=None)
                for i, hours in enumerate((2.75, 0), 1)]
        self.connection.execute.return_value.fetchall.side_effect = [[], rows]
        with self.app.test_request_context('/?order_status=all'):
            self.routes['customer_reimbursement_query']()
        sql = self.connection.execute.call_args.args[0]
        self.assertIn('sum(items.transport_hours)', sql)
        self.assertIn('items.customer_reimbursement_id = customer_reimbursements.id', sql)
        self.assertEqual(self.context['totals']['transport_hours'], 2.75)
        # Render the actual report content with a minimal base; the exporter reads this table.
        from jinja2 import ChoiceLoader, DictLoader
        self.app.jinja_loader = ChoiceLoader([DictLoader({'base.html': '{% block content %}{% endblock %}'}), self.app.jinja_loader])
        self.app.jinja_env.filters.update(money=lambda value: str(value), hours=lambda value: str(value))
        self.app.jinja_env.globals['url_for'] = lambda *args, **kwargs: '#'
        with self.app.app_context():
            html = self.app.jinja_env.get_template('customer_reimbursement_query.html').render(**self.context)
        table = re.search(r'<table\b.*?</table>', html, re.S).group()
        headers = re.findall(r'<th\b[^>]*>(.*?)</th>', table, re.S)
        column = headers.index('交通工时')
        body = re.search(r'<tbody>(.*?)</tbody>', table, re.S).group(1)
        values = [re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.S)[column].strip()
                  for row in re.findall(r'<tr>(.*?)</tr>', body, re.S)]
        self.assertEqual(values, ['2.75', '0'])
        self.assertEqual(len(headers), 18)

    def test_empty_report_total_is_zero(self):
        self.connection.execute.return_value.fetchall.side_effect = [[], []]
        with self.app.test_request_context('/'):
            self.routes['customer_reimbursement_query']()
        self.assertEqual(self.context['totals']['transport_hours'], 0)


if __name__ == '__main__':
    unittest.main()
