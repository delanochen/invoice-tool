"""Explicit payment dependency surface and approval transaction boundaries."""
import ast
from pathlib import Path
import unittest
from unittest.mock import patch
import app as module
import employee_finance
import test_expense_on_behalf as fixtures
from invoice_tool.employee_expenses import expense_approval_services as services

KEYS={'db','now','log_action','lock_number_allocation','worker_tax_status_lookup'}


class ExpenseApprovalContractTest(unittest.TestCase):
    def fixture(self):
        fixture=fixtures.ExpenseOnBehalfTest()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        expense=fixture.create()
        fixture.login('Manager')
        return fixture,expense['id']

    def test_transitive_payment_dependencies_are_bounded(self):
        tree=ast.parse(Path(employee_finance.__file__).read_text(encoding='utf-8'))
        functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
        todo=['ensure_expense_payment_order']
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
        self.assertIn('_write_expense_components',seen)
        self.assertIn('_next_number',seen)

    def test_approval_passes_only_current_explicit_dependencies(self):
        fixture,id=self.fixture()
        app=fixture.app
        captured=[]
        def ensure(api,expense_id):
            self.assertEqual(set(api),KEYS)
            for key in KEYS:
                self.assertIs(api[key],getattr(app,key))
            self.assertEqual(expense_id,id)
            captured.append(expense_id)
            return 42
        with patch.object(app,'ensure_expense_payment_order',side_effect=ensure):
            response=fixture.http.post(f'/expenses/{id}/approve')
        self.assertEqual(response.status_code,302)
        self.assertEqual(captured,[id])
        with app.app.app_context():
            expense=app.db().execute('select * from expenses where id=?',(id,)).fetchone()
            self.assertEqual(expense['status'],'approved')
        with patch.object(app,'ensure_expense_payment_order') as ensure:
            self.assertEqual(fixture.http.post(f'/expenses/{id}/approve').status_code,302)
            ensure.assert_not_called()

    def test_snapshot_failure_rolls_back_approval(self):
        fixture,id=self.fixture()
        app=fixture.app
        with patch.object(app,'ensure_expense_payment_order',side_effect=ValueError('synthetic snapshot mismatch')):
            self.assertEqual(fixture.http.post(f'/expenses/{id}/approve').status_code,302)
        with app.app.app_context():
            expense=app.db().execute('select * from expenses where id=?',(id,)).fetchone()
            self.assertEqual(expense['status'],'submitted')
            self.assertIsNone(expense['reviewed_by'])
            self.assertEqual(app.db().execute('select count(*) from employee_payment_orders').fetchone()[0],0)

    def test_original_post_endpoint_and_no_module_globals(self):
        rule=next(r for r in module.app.url_map.iter_rules() if r.endpoint=='approve_expense')
        self.assertEqual(rule.rule,'/expenses/<int:expense_id>/approve')
        self.assertEqual(rule.methods,{'POST','OPTIONS'})
        self.assertIs(module.app.view_functions['approve_expense'],module.approve_expense)
        for filename in (services.__file__,Path(services.__file__).with_name('expense_approval_routes.py')):
            tree=ast.parse(Path(filename).read_text(encoding='utf-8'))
            self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='globals' for n in ast.walk(tree)))
