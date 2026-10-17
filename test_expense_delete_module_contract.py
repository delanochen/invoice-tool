"""Expense deletion with real ER children and filesystem boundary checks."""
from pathlib import Path
import unittest
from unittest.mock import patch
import app as module
import test_expense_attachment_module_contract as fixtures


class ExpenseDeleteContractTest(unittest.TestCase):
    def fixture(self,payment=True):
        helper=fixtures.ExpenseAttachmentContractTest()
        self.addCleanup(helper.doCleanups)
        fixture,attachment_id,_=helper.fixture()
        app=fixture.app
        with app.app.app_context():
            attachment=app.db().execute('select * from expense_attachments where id=?',(attachment_id,)).fetchone()
            id=attachment['expense_id']
            path=Path(app.expense_attachment_path(attachment))
            if payment:
                app.db().execute("update expenses set status='submitted' where id=?",(id,))
                app.db().commit()
        fixture.login('Manager')
        pid=None
        if payment:
            self.assertEqual(fixture.http.post(f'/expenses/{id}/approve').status_code,302)
            with app.app.app_context():
                row=app.db().execute('select * from employee_payment_orders where source_key=?',(f'expense:{id}',)).fetchone()
                self.assertIsNotNone(row)
                pid=row['id']
                app.db().execute("update expenses set status='returned' where id=?",(id,))
                app.db().commit()
        return fixture,id,pid,path

    def test_original_endpoint_identity(self):
        rule=next(r for r in module.app.url_map.iter_rules() if r.endpoint=='delete_expense')
        self.assertEqual(rule.rule,'/expenses/<int:expense_id>/delete')
        self.assertEqual(rule.methods,{'POST','OPTIONS'})
        self.assertIs(module.app.view_functions['delete_expense'],module.delete_expense)

    def test_unpaid_er_components_sources_events_and_files_are_removed(self):
        fixture,id,pid,path=self.fixture()
        app=fixture.app
        with app.app.app_context():
            self.assertGreater(app.db().execute('select count(*) from employee_payment_components where payment_order_id=?',(pid,)).fetchone()[0],0)
        original=app.cancel_expense_payment_order
        seen=[]
        def cancel(api,expense_id):
            self.assertEqual(set(api),{'db','now','log_action'})
            for key in api:
                self.assertIs(api[key],getattr(app,key))
            seen.append(expense_id)
            return original(api,expense_id)
        with patch.object(app,'cancel_expense_payment_order',side_effect=cancel):
            self.assertEqual(fixture.http.post(f'/expenses/{id}/delete').status_code,302)
        self.assertEqual(seen,[id])
        self.assertFalse(path.exists())
        with app.app.app_context():
            self.assertIsNone(app.db().execute('select * from expenses where id=?',(id,)).fetchone())
            self.assertIsNone(app.db().execute('select * from employee_payment_orders where id=?',(pid,)).fetchone())
            for table in ('employee_payment_components','payment_order_sources','payment_order_events','employee_advance_applications'):
                self.assertEqual(app.db().execute(f'select count(*) from {table} where payment_order_id=?',(pid,)).fetchone()[0],0)

    def test_paid_and_reconciled_payments_preserve_records_and_files(self):
        fixture,id,pid,path=self.fixture()
        app=fixture.app
        for status in ('paid','reconciled'):
            with app.app.app_context():
                app.db().execute('update employee_payment_orders set status=? where id=?',(status,pid))
                app.db().commit()
            with patch.object(app,'cancel_expense_payment_order') as cancel:
                self.assertEqual(fixture.http.post(f'/expenses/{id}/delete').status_code,302)
                cancel.assert_not_called()
            self.assertTrue(path.exists())
            with app.app.app_context():
                self.assertIsNotNone(app.db().execute('select * from expenses where id=?',(id,)).fetchone())
                self.assertEqual(app.db().execute('select status from employee_payment_orders where id=?',(pid,)).fetchone()['status'],status)

    def test_rollback_keeps_database_record_but_existing_file_removal_is_not_reversed(self):
        fixture,id,_,path=self.fixture(payment=False)
        app=fixture.app
        with patch.object(app,'log_action',side_effect=RuntimeError('synthetic audit failure')):
            self.assertEqual(fixture.http.post(f'/expenses/{id}/delete').status_code,302)
        with app.app.app_context():
            self.assertIsNotNone(app.db().execute('select * from expenses where id=?',(id,)).fetchone())
            self.assertGreater(len(app.get_expense_attachments(id)),0)
        self.assertFalse(path.exists())
