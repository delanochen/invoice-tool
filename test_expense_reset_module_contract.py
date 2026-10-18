"""Workflow reset permissions, real ER cancellation and replay behavior."""
import unittest
from unittest.mock import patch
import app as module
import test_expense_on_behalf as fixtures


class ExpenseResetContractTest(unittest.TestCase):
    def fixture(self):
        fixture=fixtures.ExpenseOnBehalfTest()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        expense=fixture.create()
        fixture.login('Manager')
        self.assertEqual(fixture.http.post(f'/expenses/{expense["id"]}/approve').status_code,302)
        app=fixture.app
        with app.app.app_context():
            payment=app.db().execute('select * from employee_payment_orders where source_key=?',(f'expense:{expense["id"]}',)).fetchone()
            self.assertIsNotNone(payment)
            app.db().execute("update users set role='admin' where id=?",(fixture.people['Manager'],))
            app.db().commit()
        return fixture,expense['id'],payment['id']

    def test_original_post_endpoint(self):
        rule=next(r for r in module.app.url_map.iter_rules() if r.endpoint=='process_expense_action')
        self.assertEqual(rule.rule,'/expense-processing/action')
        self.assertEqual(rule.methods,{'POST','OPTIONS'})
        self.assertIs(module.app.view_functions['process_expense_action'],module.process_expense_action)

    def test_admin_reset_cancels_once_preserving_components(self):
        fixture,id,pid=self.fixture()
        app=fixture.app
        with app.app.app_context():
            before=[dict(r) for r in app.db().execute('select * from employee_payment_components where payment_order_id=? order by id',(pid,)).fetchall()]
            self.assertTrue(before)
        original=app.cancel_expense_payment_order
        captured=[]
        def cancel(api,expense_id):
            self.assertEqual(set(api),{'db','now','log_action'})
            for key in api:
                self.assertIs(api[key],getattr(app,key))
            captured.append(expense_id)
            return original(api,expense_id)
        with patch.object(app,'cancel_expense_payment_order',side_effect=cancel):
            for _ in range(2):
                self.assertEqual(fixture.http.post('/expense-processing/action',data={'expense_id':id,'action':'reset_workflow'}).status_code,302)
        self.assertEqual(captured,[id,id])
        with app.app.app_context():
            expense=app.db().execute('select * from expenses where id=?',(id,)).fetchone()
            self.assertEqual(expense['status'],'submitted')
            self.assertEqual(expense['payout_status'],'pending')
            for key in ('return_reason','reviewed_by','reviewed_at','reimbursed_by','reimbursed_at'):
                self.assertIsNone(expense[key])
            self.assertEqual(app.db().execute('select status from employee_payment_orders where id=?',(pid,)).fetchone()['status'],'cancelled')
            self.assertEqual(app.db().execute("select count(*) from payment_order_events where payment_order_id=? and event_type='cancel'",(pid,)).fetchone()[0],1)
            after=[dict(r) for r in app.db().execute('select * from employee_payment_components where payment_order_id=? order by id',(pid,)).fetchall()]
            self.assertEqual(before,after)

    def test_paid_and_reconciled_block_reset(self):
        fixture,id,pid=self.fixture()
        app=fixture.app
        for status in ('paid','reconciled'):
            with app.app.app_context():
                app.db().execute('update employee_payment_orders set status=? where id=?',(status,pid))
                app.db().commit()
            self.assertEqual(fixture.http.post('/expense-processing/action',data={'expense_id':id,'action':'reset_workflow'}).status_code,302)
            with app.app.app_context():
                self.assertEqual(app.db().execute('select status from expenses where id=?',(id,)).fetchone()['status'],'approved')
                self.assertEqual(app.db().execute('select status from employee_payment_orders where id=?',(pid,)).fetchone()['status'],status)

    def test_non_admin_legacy_action_invalid_id_and_missing_record(self):
        fixture,id,pid=self.fixture()
        app=fixture.app
        fixture.login('Submitter')
        with patch.object(app,'cancel_expense_payment_order') as cancel:
            self.assertEqual(fixture.http.post('/expense-processing/action',data={'expense_id':id,'action':'reset_workflow'}).status_code,403)
            cancel.assert_not_called()
        fixture.login('Manager')
        with app.app.app_context():
            app.db().execute("update users set role='manager' where id=?",(fixture.people['Manager'],))
            app.db().commit()
        self.assertEqual(fixture.http.post('/expense-processing/action',data={'expense_id':id,'action':'reset_workflow'}).status_code,403)
        with app.app.app_context():
            app.db().execute("update users set role='admin' where id=?",(fixture.people['Manager'],))
            app.db().commit()
        with patch.object(app,'cancel_expense_payment_order') as cancel:
            self.assertEqual(fixture.http.post('/expense-processing/action',data={'expense_id':id,'action':'reimburse'}).status_code,403)
            self.assertEqual(fixture.http.post('/expense-processing/action',data={'expense_id':'bad','action':'reset_workflow'}).status_code,302)
            self.assertEqual(fixture.http.post('/expense-processing/action',data={'expense_id':id+100000,'action':'reset_workflow'}).status_code,404)
            cancel.assert_not_called()
