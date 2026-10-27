"""Database-free route tests for optional settlement dates and inclusive filters."""
import inspect
import unittest
from unittest.mock import MagicMock
from flask import Flask, g
from werkzeug.exceptions import BadRequest
from invoice_tool.service_orders.settlement_dates import optional_settlement_date, settlement_date_filters
from invoice_tool.service_orders.routes import register_service_order_routes
from invoice_tool.query_pages.orders import register_order_query_page_routes


class SettlementDateTests(unittest.TestCase):
    def test_optional_dates(self):
        self.assertIsNone(optional_settlement_date(''))
        self.assertEqual('2028-02-29', optional_settlement_date('2028-02-29'))
        for value in ['2026-02-29', '2026-13-01', '20261026', '2026-W43-1', "x' or 1=1"]:
            with self.subTest(value=value), self.assertRaises(BadRequest):
                optional_settlement_date(value)

    def test_range_boundaries(self):
        for args, expected in [({}, []), ({'expected_settlement_from':'2026-10-26'}, ['2026-10-26']), ({'expected_settlement_to':'2026-10-26'}, ['2026-10-26']), ({'expected_settlement_from':'2026-10-26','expected_settlement_to':'2026-10-26'}, ['2026-10-26','2026-10-26'])]:
            with self.subTest(args=args):
                clauses=[];params=[]
                settlement_date_filters(args,clauses,params)
                self.assertEqual(params,expected)
                self.assertEqual(len(clauses),len(expected))
        with self.assertRaises(BadRequest):
            settlement_date_filters({'expected_settlement_from':'2026-11-01','expected_settlement_to':'2026-10-01'},[],[])

    def test_list_retains_access_scope_and_filters(self):
        app=Flask(__name__)
        deps={name:MagicMock() for name in inspect.signature(register_service_order_routes).parameters if name!='app'}
        deps['login_required']=lambda fn:fn
        deps['is_external_manager'].return_value=True
        deps['is_external_employee'].return_value=False
        deps['db'].return_value.execute.return_value.fetchall.return_value=[]
        views=register_service_order_routes(app,**deps)
        with app.test_request_context('/service-orders?expected_settlement_from=2026-10-01&expected_settlement_to=2026-10-31'):
            g.user={'id':3,'client_id':7}
            # Patch the imported rendering function so no unrelated app globals are needed.
            from unittest.mock import patch
            with patch('invoice_tool.service_orders.routes.render_template', return_value='ok') as render:
                self.assertEqual('ok',views['service_orders']())
            sql,params=next(call.args for call in deps['db'].return_value.execute.call_args_list if 'expected_settlement_date >= ?' in call.args[0])
            self.assertIn("service_orders.status != 'closed'",sql)
            self.assertIn('service_orders.client_id = ?',sql)
            self.assertIn('expected_settlement_date >= ?',sql)
            self.assertIn('expected_settlement_date <= ?',sql)
            self.assertEqual([7,'2026-10-01','2026-10-31'],params)
            self.assertEqual('2026-10-31',render.call_args.kwargs['expected_settlement_to'])

    def test_create_edit_and_clear_date(self):
        for action in ['new_service_order', 'edit_service_order']:
            for value in ['2026-12-15', '']:
                with self.subTest(action=action, value=value):
                    app=Flask(__name__);app.secret_key='test'
                    deps={name:MagicMock() for name in inspect.signature(register_service_order_routes).parameters if name!='app'}
                    deps['login_required']=lambda fn:fn
                    deps['is_external_user'].return_value=False
                    deps['country_from_form'].return_value={'region_code':'americas','code':'US'}
                    deps['next_service_order_number'].return_value='SO-TEST'
                    row={'id':1,'name':'Test','manufacturer_id':1,'contact_name':'Test','contact_details':'test@example.invalid'}
                    db=deps['db'].return_value
                    db.execute.return_value.fetchone.return_value=row
                    db.execute.return_value.fetchall.return_value=[row]
                    db.execute.return_value.lastrowid=5
                    deps['require_service_order'].return_value={'id':5,'status':'open','order_number':'SO-TEST','work_order_type_id':1,'contract_id':None,'quotation_id':None,'country_code':'US'}
                    views=register_service_order_routes(app,**deps)
                    with app.test_request_context('/service-orders/new',method='POST',data={'buyer_id':'1','client_id':'1','work_order_type_id':'1','site_address':'Test address','client_order_number':'PO-TEST','expected_settlement_date':value}):
                        g.user={'id':1}
                        response=views[action](5) if action=='edit_service_order' else views[action]()
                        self.assertEqual(302,response.status_code)
                    writes=[call.args for call in db.execute.call_args_list if 'expected_settlement_date' in call.args[0]]
                    self.assertEqual(1,len(writes))
                    sql,params=writes[0]
                    self.assertEqual(sql.count('?'),len(params))
                    self.assertIn(value or None,params)
                    db.commit.assert_called_once()

    def test_report_combines_start_and_settlement_dates(self):
        from flask import request
        app=Flask(__name__);db=MagicMock();render=MagicMock(return_value='ok')
        views=register_order_query_page_routes(app,
            _cb_resolve_abort=lambda:__import__('flask').abort,
            _cb_resolve_current_language=lambda:lambda:'zh-CN',
            _cb_resolve_db=lambda:lambda:db,
            _cb_resolve_is_internal_user=lambda:lambda:True,
            _cb_resolve_login_required=lambda:lambda fn:fn,
            _cb_resolve_render_template=lambda:render,
            _cb_resolve_request=lambda:request)
        with app.test_request_context('/reports/service-orders?date_from=2026-09-01&status=closed&expected_settlement_from=2026-10-01&expected_settlement_to=2026-10-31'):
            self.assertEqual('ok',views['service_order_query']())
        sql,params=db.execute.call_args.args
        self.assertIn('service_orders.start_date >= ?',sql)
        self.assertIn('expected_settlement_date >= ?',sql)
        self.assertIn('expected_settlement_date <= ?',sql)
        self.assertEqual(['zh-CN','2026-09-01','2026-10-01','2026-10-31','closed'],params)
        self.assertEqual('2026-10-01',render.call_args.kwargs['expected_settlement_from'])


if __name__=='__main__':
    unittest.main()
