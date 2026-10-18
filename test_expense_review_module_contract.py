"""Review endpoint identity and return-state/permission integration."""
import ast
from pathlib import Path
import subprocess
import unittest
import app as module
import test_expense_on_behalf as fixtures


class ExpenseReviewContractTest(unittest.TestCase):
    def fixture(self):
        fixture=fixtures.ExpenseOnBehalfTest()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def test_original_post_endpoints_and_aliases(self):
        source=subprocess.check_output(['git','show','2ba704d:app.py'],cwd=Path(__file__).parent,text=True,encoding='utf-8')
        names={'review_expense_duplicate_check','return_expense'}
        rules={r.endpoint:r for r in module.app.url_map.iter_rules() if r.endpoint in names}
        self.assertEqual(set(rules),names)
        for node in ast.parse(source).body:
            if isinstance(node,ast.FunctionDef) and node.name in names:
                self.assertEqual(rules[node.name].rule,ast.literal_eval(node.decorator_list[0].args[0]))
                self.assertEqual(rules[node.name].methods,{'POST','OPTIONS'})
                self.assertIs(module.app.view_functions[node.name],getattr(module,node.name))

    def test_return_requires_reason_preserves_actor_and_notifies_both(self):
        fixture=self.fixture()
        expense=fixture.create()
        app=fixture.app
        fixture.login('Manager')
        url=f'/expenses/{expense["id"]}/return'
        self.assertEqual(fixture.http.post(url,data={'return_reason':'  '}).status_code,302)
        with app.app.app_context():
            row=app.db().execute('select * from expenses where id=?',(expense['id'],)).fetchone()
            self.assertEqual(row['status'],'submitted')
        self.assertEqual(fixture.http.post(url,data={'return_reason':'  verify receipt  '}).status_code,302)
        with app.app.app_context():
            row=app.db().execute('select * from expenses where id=?',(expense['id'],)).fetchone()
            self.assertEqual(row['status'],'returned')
            self.assertEqual(row['return_reason'],'verify receipt')
            self.assertEqual(row['reviewed_by'],fixture.people['Manager'])
            self.assertEqual(row['created_by'],fixture.people['Submitter'])
            self.assertEqual(row['beneficiary_id'],fixture.people['Beneficiary'])
            messages=app.db().execute("select user_id from messages where title = '报销已被退回'").fetchall()
            self.assertEqual({r['user_id'] for r in messages},{fixture.people['Submitter'],fixture.people['Beneficiary']})
        self.assertEqual(fixture.http.post(url,data={'return_reason':'second'}).status_code,302)
        with app.app.app_context():
            row=app.db().execute('select * from expenses where id=?',(expense['id'],)).fetchone()
            self.assertEqual(row['return_reason'],'verify receipt')

    def test_employee_return_denied_and_draft_not_returned(self):
        fixture=self.fixture()
        expense=fixture.create()
        url=f'/expenses/{expense["id"]}/return'
        self.assertEqual(fixture.http.post(url,data={'return_reason':'not authorized'}).status_code,403)
        draft=fixture.create(action='draft')
        fixture.login('Manager')
        self.assertEqual(fixture.http.post(f'/expenses/{draft["id"]}/return',data={'return_reason':'wrong state'}).status_code,302)
        with fixture.app.app.app_context():
            row=fixture.app.db().execute('select * from expenses where id=?',(draft['id'],)).fetchone()
            self.assertEqual(row['status'],'draft')
