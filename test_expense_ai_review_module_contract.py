"""AI review read/write boundaries and HTTP contract."""
import tempfile
import unittest
from unittest.mock import patch
import app as module
import test_expense_on_behalf as fixtures


class ExpenseAiReviewContractTest(unittest.TestCase):
    def fixture(self):
        fixture=fixtures.ExpenseOnBehalfTest()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        expense=fixture.create()
        fixture.login('Manager')
        return fixture,expense['id']

    def test_endpoint_identity(self):
        rules=[r for r in module.app.url_map.iter_rules() if r.endpoint=='expense_ai_review']
        self.assertEqual(len(rules),1)
        self.assertEqual(rules[0].rule,'/expenses/<int:expense_id>/ai_review')
        self.assertEqual(rules[0].methods,{'GET','POST','HEAD','OPTIONS'})
        self.assertIs(module.app.view_functions['expense_ai_review'],module.expense_ai_review)

    def test_get_without_record_never_invokes_model(self):
        fixture,id=self.fixture()
        with patch.object(fixture.app,'run_expense_ai_review_task') as run:
            response=fixture.http.get(f'/expenses/{id}/ai_review')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json,dict(ok=True,status='none',conclusion='',content='',model='',error='',updated_at=''))
            run.assert_not_called()

    def test_post_forces_review_and_resolves_current_attachment_root(self):
        fixture,id=self.fixture()
        with tempfile.TemporaryDirectory() as folder,patch.object(fixture.app,'EXPENSE_ATTACHMENTS_DIR',folder):
            for ok,code in ((True,200),(False,502)):
                value=dict(ok=ok,status='done' if ok else 'failed',conclusion='synthetic',error='' if ok else 'synthetic unavailable',content='test',model='mock',updated_at='test')
                with patch.object(fixture.app,'run_expense_ai_review_task',return_value=value) as run:
                    response=fixture.http.post(f'/expenses/{id}/ai_review')
                    self.assertEqual(response.status_code,code)
                    self.assertEqual(response.json,value)
                    self.assertEqual(run.call_args.args[1:],(id,folder))
                    self.assertEqual(run.call_args.kwargs,{'force':True})

    def test_denied_and_missing_expenses_do_not_invoke_model(self):
        fixture,id=self.fixture()
        with patch.object(fixture.app,'run_expense_ai_review_task') as run:
            fixture.login('Submitter')
            for method in ('get','post'):
                self.assertEqual(getattr(fixture.http,method)(f'/expenses/{id}/ai_review').status_code,403)
            fixture.login('Manager')
            for method in ('get','post'):
                self.assertEqual(getattr(fixture.http,method)(f'/expenses/{id+100000}/ai_review').status_code,404)
            run.assert_not_called()
