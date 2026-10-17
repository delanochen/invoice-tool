"""Expense endpoint identity and transaction/idempotency integration tests."""
import ast
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import app as module
import test_expense_on_behalf as fixtures


class ExpenseWriteContractTest(unittest.TestCase):
    def fixture(self):
        fixture=fixtures.ExpenseOnBehalfTest()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def test_original_url_methods_endpoints_and_aliases(self):
        source=subprocess.check_output(['git','show','11878e0:app.py'],cwd=Path(__file__).parent,text=True,encoding='utf-8')
        names={'new_expense','edit_expense','expense_detail'}
        rules={rule.endpoint:rule for rule in module.app.url_map.iter_rules() if rule.endpoint in names}
        self.assertEqual(set(rules),names)
        for node in ast.parse(source).body:
            if isinstance(node,ast.FunctionDef) and node.name in names:
                decorator=node.decorator_list[0]
                methods=next((ast.literal_eval(k.value) for k in decorator.keywords if k.arg=='methods'),['GET'])
                self.assertEqual(rules[node.name].rule,ast.literal_eval(decorator.args[0]))
                self.assertEqual(rules[node.name].methods,set(methods)|{'HEAD','OPTIONS'})
                self.assertIs(module.app.view_functions[node.name],getattr(module,node.name))

    def test_repeated_new_and_edit_tokens_do_not_duplicate_or_overwrite(self):
        fixture=self.fixture()
        app=fixture.app
        data=fixture.form(action='draft')
        url=f'/service-orders/{fixture.order}/expenses/new'
        for _ in range(2):
            self.assertEqual(fixture.http.post(url,data=data).status_code,302)
        with app.app.app_context():
            rows=app.db().execute('select * from expenses').fetchall()
            self.assertEqual(len(rows),1)
            expense_id=rows[0]['id']
            item_id=app.expense_items(expense_id)[0]['id']
        edit=fixture.form(action='draft',item_amount='75')
        url=f'/expenses/{expense_id}/edit'
        self.assertEqual(fixture.http.post(url,data=edit).status_code,302)
        edit['item_amount']='999'
        self.assertEqual(fixture.http.post(url,data=edit).status_code,302)
        with app.app.app_context():
            expense=app.db().execute('select * from expenses where id=?',(expense_id,)).fetchone()
            self.assertEqual(float(expense['amount']),75)
            items=app.expense_items(expense_id)
            self.assertEqual(len(items),1)
            self.assertEqual(items[0]['id'],item_id)
            self.assertEqual(float(items[0]['amount']),75)

    def test_validation_failure_rolls_back_token_and_allows_retry(self):
        fixture=self.fixture()
        app=fixture.app
        data=fixture.form(business_purpose='')
        url=f'/service-orders/{fixture.order}/expenses/new'
        self.assertEqual(fixture.http.post(url,data=data).status_code,302)
        with app.app.app_context():
            self.assertEqual(app.db().execute('select count(*) from expenses').fetchone()[0],0)
            self.assertIsNone(app.db().execute('select * from expense_save_tokens where token=?',(data['save_token'],)).fetchone())
        data.update(action='draft',business_purpose='synthetic test')
        self.assertEqual(fixture.http.post(url,data=data).status_code,302)
        with app.app.app_context():
            self.assertEqual(app.db().execute('select count(*) from expenses').fetchone()[0],1)

    def test_unexpected_save_error_rolls_back_and_reuses_token(self):
        fixture=self.fixture()
        app=fixture.app
        data=fixture.form(action='draft')
        url=f'/service-orders/{fixture.order}/expenses/new'
        with patch.object(app,'save_expense_items',side_effect=RuntimeError('synthetic save failure')):
            self.assertEqual(fixture.http.post(url,data=data).status_code,302)
        with app.app.app_context():
            self.assertEqual(app.db().execute('select count(*) from expenses').fetchone()[0],0)
            self.assertIsNone(app.db().execute('select * from expense_save_tokens where token=?',(data['save_token'],)).fetchone())
        self.assertEqual(fixture.http.post(url,data=data).status_code,302)
        with app.app.app_context():
            self.assertEqual(app.db().execute('select count(*) from expenses').fetchone()[0],1)
