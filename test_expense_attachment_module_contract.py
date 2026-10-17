"""Attachment endpoint identity, authorization and response contracts."""
import ast
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from werkzeug.datastructures import FileStorage
import app as module
import test_expense_on_behalf as fixtures


class ExpenseAttachmentContractTest(unittest.TestCase):
    def fixture(self):
        fixture=fixtures.ExpenseOnBehalfTest()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        expense=fixture.create(action='draft')
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root=patch.object(fixture.app,'EXPENSE_ATTACHMENTS_DIR',temp.name)
        root.start()
        self.addCleanup(root.stop)
        with fixture.app.app.test_request_context('/'):
            fixture.app.g.user={'id':fixture.people['Submitter']}
            attachment_id=fixture.app.save_expense_attachment(expense['id'],FileStorage(stream=io.BytesIO(b'%PDF-1.4 synthetic receipt'),filename='receipt.pdf',content_type='application/pdf'),'line-test')
            fixture.app.db().commit()
        return fixture,attachment_id,temp.name

    def test_original_endpoint_paths_methods_aliases(self):
        source=subprocess.check_output(['git','show','4a2ad4f:app.py'],cwd=Path(__file__).parent,text=True,encoding='utf-8')
        names={'preview_expense_attachment','download_expense_attachment','get_expense_attachment_interpretation','interpret_expense_attachment'}
        rules={r.endpoint:r for r in module.app.url_map.iter_rules() if r.endpoint in names}
        self.assertEqual(set(rules),names)
        for node in ast.parse(source).body:
            if isinstance(node,ast.FunctionDef) and node.name in names:
                decorator=node.decorator_list[0]
                methods={'POST','OPTIONS'} if decorator.func.attr=='post' else {'GET','HEAD','OPTIONS'}
                self.assertEqual(rules[node.name].rule,ast.literal_eval(decorator.args[0]))
                self.assertEqual(rules[node.name].methods,methods)
                self.assertIs(module.app.view_functions[node.name],getattr(module,node.name))

    def test_preview_download_and_participant_access(self):
        fixture,id,_=self.fixture()
        for user in ('Submitter','Beneficiary'):
            fixture.login(user)
            preview=fixture.http.get(f'/expense-attachments/{id}')
            download=fixture.http.get(f'/expense-attachments/{id}/download')
            self.addCleanup(preview.close)
            self.addCleanup(download.close)
            self.assertEqual(preview.status_code,200)
            self.assertEqual(download.status_code,200)
            self.assertEqual(download.data,b'%PDF-1.4 synthetic receipt')
            self.assertIn('attachment;',download.headers['Content-Disposition'])
            self.assertIn('receipt.pdf',download.headers['Content-Disposition'])
        fixture.login('Unrelated')
        with patch.object(fixture.app,'run_expense_attachment_interpretation') as run:
            for suffix,method in (('', 'get'),('/download','get'),('/interpretation','get'),('/interpret','post')):
                self.assertEqual(getattr(fixture.http,method)(f'/expense-attachments/{id}{suffix}').status_code,403)
            run.assert_not_called()

    def test_interpretation_empty_failure_and_success_contracts(self):
        fixture,id,folder=self.fixture()
        get_url=f'/expense-attachments/{id}/interpretation'
        result=fixture.http.get(get_url)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json,dict(ok=True,status='none',content='',error='',model='',updated_at=''))
        for ok,expected in ((False,502),(True,200)):
            value=dict(ok=ok,status='done' if ok else 'failed',error='' if ok else 'synthetic unavailable')
            with patch.object(fixture.app,'run_expense_attachment_interpretation',return_value=value) as run:
                response=fixture.http.post(f'/expense-attachments/{id}/interpret')
                self.assertEqual(response.status_code,expected)
                self.assertEqual(response.json,value)
                self.assertEqual(run.call_args.args[1:],(id,folder))
                self.assertEqual(run.call_args.kwargs,{'force':True})
        with patch.object(fixture.app,'run_expense_attachment_interpretation') as run:
            for suffix,method in (('', 'get'),('/download','get'),('/interpretation','get'),('/interpret','post')):
                self.assertEqual(getattr(fixture.http,method)(f'/expense-attachments/{id+100000}{suffix}').status_code,404)
            run.assert_not_called()
