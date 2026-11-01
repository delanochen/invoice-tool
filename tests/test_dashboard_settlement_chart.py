"""Dashboard settlement ordering, access and template regression tests (no DB)."""
import inspect
from pathlib import Path
import unittest
from unittest.mock import MagicMock
from flask import Flask, request
from jinja2 import ChoiceLoader, DictLoader
from invoice_tool.dashboard.charts import settlement_by_date_chart
from invoice_tool.dashboard.page_routes import register_dashboard_page_routes

class SettlementChartTest(unittest.TestCase):
    def chart(self, rows):
        db=MagicMock()
        db.execute.return_value.fetchall.return_value=rows
        points=settlement_by_date_chart(db=lambda:db,
            client_filter_clause=lambda alias:(alias+'.client_id = ?', [7]),
            can_view_customer_reimbursement=lambda:True)
        return points,db

    def test_date_sort_amounts_and_missing_date_last(self):
        points,db=self.chart([
            dict(expected_date=None, total=200, settlement_count=1),
            dict(expected_date='2026-12-01', total=100, settlement_count=1),
            dict(expected_date='2026-11-01', total=0, settlement_count=1)])
        self.assertEqual(['2026-11-01','2026-12-01',''],[p['expected_date'] for p in points])
        self.assertEqual([0,50,100],[p['height'] for p in points])
        sql,params=db.execute.call_args.args
        self.assertEqual([7],params)
        self.assertIn('service_orders.client_id = ?',sql)
        self.assertIn("status != 'superseded'",sql)
        self.assertIn('current_row.is_current',sql)
        self.assertIn('coalesce(service_orders.expected_settlement_date',sql)
        self.assertIn('sum(customer_reimbursements.total_amount)',sql)
        self.assertIn('group by',sql)
        # The whole current settlement set is aggregated: no latest-ten sampling.
        self.assertNotIn('limit 10',sql)
        self.assertNotIn('order by customer_reimbursements.created_at',sql)

    def test_all_settlements_for_one_date_are_aggregated(self):
        points,_=self.chart([
            dict(expected_date='2026-10-31', total=252218.64, settlement_count=13)])
        self.assertEqual(['2026-10-31'],[p['expected_date'] for p in points])
        self.assertEqual([252218.64],[p['value'] for p in points])
        self.assertEqual([13],[p['count'] for p in points])

    def test_same_dates_and_undated_settlements_are_merged(self):
        points,_=self.chart([
            dict(expected_date='2026-11-20', total=0.10, settlement_count=1),
            dict(expected_date=' 2026-11-20 ', total=0.20, settlement_count=2),
            dict(expected_date='2026-11-19', total=100, settlement_count=1),
            dict(expected_date=None, total=200, settlement_count=1),
            dict(expected_date='', total=300, settlement_count=1)])
        self.assertEqual(['2026-11-19','2026-11-20',''],[p['expected_date'] for p in points])
        self.assertEqual([100,0.30,500],[p['value'] for p in points])
        self.assertEqual([1,3,2],[p['count'] for p in points])
        self.assertTrue(all('order_number' not in p for p in points))
        self.assertEqual(6,sum(p['count'] for p in points))

    def test_denied_does_not_query(self):
        db=MagicMock()
        self.assertEqual([],settlement_by_date_chart(db=db,client_filter_clause=MagicMock(),can_view_customer_reimbursement=lambda:False))
        db.assert_not_called()

    def test_empty_and_zero_values(self):
        self.assertEqual([],self.chart([])[0])
        self.assertEqual(0,self.chart([dict(expected_date='',total=None,settlement_count=0)])[0][0]['height'])

    def test_route_and_real_template_show_settlement_fields(self):
        root=Path(__file__).resolve().parents[1]
        app=Flask(__name__,template_folder=str(root/'templates'))
        app.jinja_loader=ChoiceLoader([DictLoader({'base.html':'{% block head %}{% endblock %}{% block content %}{% endblock %}'}),app.jinja_loader])
        app.jinja_env.filters['money']=lambda v:f'{float(v):.2f}'
        app.jinja_env.filters['payment_label']=lambda v:''
        db=MagicMock()
        def execute(sql,params):
            result=MagicMock()
            result.fetchall.return_value=([dict(expected_date='2026-11-20',total=1234.5,settlement_count=1)] if 'from customer_reimbursements' in sql else [])
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
        self.assertNotIn('SO-TEST',html)
        self.assertIn('1笔结算',html)
        self.assertIn('2026-11-20',html)
        self.assertIn('1234.50',html)
        self.assertIn('工单结算到期分布',html)
        self.assertNotIn('最新10笔工单结算',html)
        self.assertNotIn('paid_chart',captured)
        values['monthly_paid_chart'].assert_not_called()

if __name__=='__main__':
    unittest.main()
