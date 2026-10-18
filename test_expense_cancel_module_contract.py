"""Explicit cancellation dependencies and unpaid/paid boundary integration."""
import ast
from pathlib import Path
import subprocess
import copy
import unittest
from unittest.mock import Mock, patch
import app as module
import employee_finance
import test_expense_on_behalf as fixtures

KEYS={'db','now','log_action'}


class ExpenseCancelContractTest(unittest.TestCase):
    def fixture(self):
        fixture=fixtures.ExpenseOnBehalfTest()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        expense=fixture.create()
        fixture.login('Manager')
        self.assertEqual(fixture.http.post(f'/expenses/{expense["id"]}/approve').status_code,302)
        return fixture,expense['id']

    def test_transitive_cancellation_api_is_three_keys(self):
        tree=ast.parse(Path(employee_finance.__file__).read_text(encoding='utf-8'))
        functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
        todo=['cancel_expense_payment_order']
        seen=set()
        keys=set()
        while todo:
            name=todo.pop()
            if name in seen:
                continue
            seen.add(name)
            for node in ast.walk(functions[name]):
                if isinstance(node,ast.Subscript) and isinstance(node.value,ast.Name) and node.value.id=='api':
                    self.assertIsInstance(node.slice,ast.Constant)
                    keys.add(node.slice.value)
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in functions:
                    todo.append(node.func.id)
        self.assertEqual(keys,KEYS)
        self.assertIn('_reverse_advance_application',seen)

    @classmethod
    def setUpClass(cls):
        source=subprocess.check_output(['git','show','df2f6e5:app.py'],cwd=Path(__file__).parent,text=True,encoding='utf-8')
        cls.original=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='cancel_expense')
        cls.original.decorator_list=[]
        cls.reference=dict(vars(module))
        exec(compile(ast.Module(body=[copy.deepcopy(cls.original)],type_ignores=[]),'baseline-expense-cancel.py','exec'),cls.reference)

    def run_case(self,function,status,payment_status,reason,fail=False):
        expense=dict(id=7,status=status,expense_number='EX-TEST')
        payment=None if payment_status is None else dict(id=8,status=payment_status,payment_number='ER-TEST')
        database=Mock()
        database.execute.return_value.fetchone.return_value=payment
        called=[]
        def cancel(api,expense_id):
            self.assertEqual(expense_id,7)
            self.assertTrue(KEYS <= set(api))
            called.append(7)
            if fail:
                raise RuntimeError('synthetic cancellation failure')
            return 8
        log=Mock()
        deps=dict(db=lambda:database,require_expense=lambda _: (expense,{'id':9,'order_number':'SO-TEST'}),now=lambda:'2026-10-17T12:00:00',log_action=log,cancel_expense_payment_order=cancel)
        with module.app.test_request_context('/',method='POST',data={'cancel_reason':reason}),patch.multiple(module,**deps),patch.dict(self.reference,deps):
            module.g.user={'id':4}
            response=function(7)
            result=(response.status_code,response.location,database.mock_calls,log.mock_calls,called)
        return result

    def test_state_reason_payment_matrix_matches_original(self):
        for status in ('draft','submitted','returned','approved','cancelled','other'):
            for payment in (None,'draft','approved','pending_payment','paid','reconciled','cancelled'):
                for reason in (' ','synthetic cancellation'):
                    self.assertEqual(self.run_case(module.cancel_expense.__wrapped__,status,payment,reason),self.run_case(self.reference['cancel_expense'],status,payment,reason),(status,payment,reason))

    def test_callback_failure_still_commits_exactly_as_original(self):
        current=self.run_case(module.cancel_expense.__wrapped__,'approved','draft','synthetic failure',True)
        self.assertEqual(current,self.run_case(self.reference['cancel_expense'],'approved','draft','synthetic failure',True))
        self.assertEqual(current[-1],[7])
        self.assertTrue(any(c[0]=='commit' for c in current[2]))

    def test_schema_gap_blocks_original_and_moved_route(self):
        fixture,id=self.fixture()
        app=fixture.app
        reference=dict(vars(app))
        exec(compile(ast.Module(body=[copy.deepcopy(self.original)],type_ignores=[]),'baseline-expense-cancel.py','exec'),reference)
        for function in (reference['cancel_expense'],app.cancel_expense.__wrapped__):
            with app.app.test_request_context('/',method='POST',data={'cancel_reason':'synthetic test'}):
                app.g.user={'id':fixture.people['Manager'],'role':'manager','name':'Manager'}
                with self.assertRaisesRegex(app.DatabaseError,'cancel_reason'):
                    function(id)
                app.db().rollback()
        with app.app.app_context():
            self.assertEqual(app.db().execute('select status from expenses where id=?',(id,)).fetchone()['status'],'approved')

    def test_original_endpoint(self):
        rule=next(r for r in module.app.url_map.iter_rules() if r.endpoint=='cancel_expense')
        self.assertEqual(rule.rule,'/expenses/<int:expense_id>/cancel')
        self.assertEqual(rule.methods,{'POST','OPTIONS'})
        self.assertIs(module.app.view_functions['cancel_expense'],module.cancel_expense)
