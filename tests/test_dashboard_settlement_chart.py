"""Dashboard settlement ordering, access and template regression tests (no DB)."""
import inspect
from pathlib import Path
import unittest
from unittest.mock import MagicMock
from flask import Flask, request
from jinja2 import ChoiceLoader, DictLoader
from invoice_tool.dashboard.charts import latest_settlement_chart
from invoice_tool.dashboard.page_routes import register_dashboard_page_routes

class SettlementChartTest(unittest.TestCase):
    def chart(self, rows):
        db=MagicMock()
        db.execute.return_value.fetchall.return_value=rows
        points=latest_settlement_chart(db=lambda:db,
            client_filter_clause=lambda alias:(alias+'.client_id = ?', [7]),
            can_view_customer_reimbursement=lambda:True)
        return points,db

    def test_date_sort_amounts_and_missing_date_last(self):
        points,db=self.chart([
            dict(id=12,order_number='SO12',total_amount=200,expected_settlement_date=None),
            dict(id=11,order_number='SO11',total_amount=100,expected_settlement_date='2026-12-01'),
            dict(id=10,order_number='SO10',total_amount=0,expected_settlement_date='2026-11-01')])
        self.assertEqual([10,11,12],[p['id'] for p in points])
        self.assertEqual([0,50,100],[p['height'] for p in points])
        sql,params=db.execute.call_args.args
        self.assertEqual([7],params)
        self.assertIn('service_orders.client_id = ?',sql)
        self.assertIn("status != 'superseded'",sql)
        self.assertIn('current_row.is_current',sql)
        self.assertIn('created_at desc, customer_reimbursements.id desc',sql)
        self.assertIn('limit 10',sql)

    def test_denied_does_not_query(self):
        db=MagicMock()
        self.assertEqual([],latest_settlement_chart(db=db,client_filter_clause=MagicMock(),can_view_customer_reimbursement=lambda:False))
        db.assert_not_called()

    def test_empty_and_zero_values(self):
        self.assertEqual([],self.chart([])[0])
        self.assertEqual(0,self.chart([dict(id=1,order_number='SO1',total_amount=None,expected_settlement_date='')])[0][0]['height'])

    def test_route_and_real_template_show_settlement_fields(self):
        root=Path(__file__).resolve().parents[1]
        app=Flask(__name__,template_folder=str(root/'templates'))
        app.jinja_loader=ChoiceLoader([DictLoader({'base.html':'{% block head %}{% endblock %}{% block content %}{% endblock %}'}),app.jinja_loader])
        app.jinja_env.filters['money']=lambda v:f'{float(v):.2f}'
        app.jinja_env.filters['payment_label']=lambda v:''
        db=MagicMock()
        def execute(sql,params):
            result=MagicMock()
            result.fetchall.return_value=([dict(id=1,order_number='SO-TEST',total_amount=1234.5,expected_settlement_date='2026-11-20')] if 'from customer_reimbursements' in sql else [])
            return result
        db.execute.side_effect=execute
        metrics=dict.fromkeys(['invoice_count','unpaid_payroll','completed','paid','unpaid','total','pending_reimbursement','pending_expenses','reimbursed_expenses'],0)
        captured={}
        def render(template,**values):
            captured.update(values)
            return app.jinja_env.get_template(template).render(**values,can_create_invoice=lambda:False,url_for=lambda *a,**k:'#')
        values=dict(STATUS_LABELS={},can_view_invoices=lambda:True,client_filter_clause=lambda alias:('1=1',[]),dashboard_project_options=lambda:[],db=lambda:db,get_metrics=lambda:metrics,is_external_user=lambda:False,login_required=lambda f:f,monthly_paid_chart=MagicMock(),monthly_project_chart=lambda ids:[],redirect=lambda u:u,render_template=render,request=request,url_for=lambda *a,**k:'#',can_view_customer_reimbursement=lambda:True)
        deps={name:(lambda value=values[name.removeprefix('_cb_resolve_')]:value) for name in inspect.signature(register_dashboard_page_routes).parameters if name!='app'}
        views=register_dashboard_page_routes(app,**deps)
        with app.test_request_context('/'):
            html=views['dashboard']()
        self.assertIn('SO-TEST',html)
        self.assertIn('2026-11-20',html)
        self.assertIn('1234.50',html)
        self.assertNotIn('paid_chart',captured)
        values['monthly_paid_chart'].assert_not_called()

if __name__=='__main__':
    unittest.main()
