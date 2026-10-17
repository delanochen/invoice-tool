"""Duplicate scoring equivalence and real review workflow in invoice_test."""
import ast
import inspect
import io
import itertools
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from werkzeug.datastructures import FileStorage
import app as module
import test_expense_on_behalf as fixtures

NAMES = ('image_hash_distance','expense_attachment_duplicate_context','ensure_expense_attachment_fingerprints','deepseek_duplicate_analysis','run_expense_duplicate_checks','expense_duplicate_checks')


class ExpenseDuplicatesContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = subprocess.check_output(['git','show','79b89d3:app.py'],cwd=Path(__file__).parent,text=True,encoding='utf-8')
        nodes = [n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
        cls.reference = dict(vars(module))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'baseline-expense-duplicates.py','exec'),cls.reference)

    def test_signatures_and_hash_distance_match(self):
        for name in NAMES:
            self.assertEqual(inspect.signature(getattr(module,name)),inspect.signature(self.reference[name]))
        for left,right in itertools.product(('',None,'00','ff','zz','0000','01'),repeat=2):
            self.assertEqual(module.image_hash_distance(left,right),self.reference['image_hash_distance'](left,right))

    def run_case(self,function,matched,sha='different',dhash='',count=1):
        current = dict(id=10,expense_id=7,expense_item_key='line-a',expense_date='2026-09-01',matched_amount=10,beneficiary_id=2,project_name='Fuel')
        database = Mock()
        def execute(sql,params):
            if 'coalesce(file_sha256' in sql:
                rows=[]
            elif 'select * from expense_attachments where expense_id = ?' in sql:
                rows=[{'id':10}]
            elif 'select ea.id' in sql:
                rows=[{'id':i+20} for i in range(count)]
            else:
                rows=[]
            return SimpleNamespace(fetchall=lambda:rows)
        database.execute.side_effect=execute
        analysis=Mock(return_value=('analysis',''))
        deps=dict(db=lambda:database,expense_attachment_duplicate_context=lambda id:current if id==10 else dict(matched,id=id),ensure_expense_attachment_fingerprints=lambda row:('current','00') if row['id']==10 else (sha,dhash),deepseek_duplicate_analysis=analysis,now=lambda:'2026-10-17T12:00:00')
        with patch.multiple(module,**deps),patch.dict(self.reference,deps):
            function(7,use_deepseek=True)
        return database.execute.call_args_list,analysis.call_count

    def test_scoring_boundaries_match_independent_baseline(self):
        for same_item,sha,dhash,date,amount in itertools.product((False,True),('current','different'),('00','1f','3f',''),('2026-09-01','2026-09-04','2026-09-05','bad'),(10,10.004,10.006)):
            matched=dict(id=20,expense_id=7 if same_item else 8,expense_item_key='line-a',expense_date=date,matched_amount=amount,beneficiary_id=2,project_name='Fuel')
            self.assertEqual(self.run_case(module.run_expense_duplicate_checks,matched,sha,dhash),self.run_case(self.reference['run_expense_duplicate_checks'],matched,sha,dhash))

    def test_ai_budget_and_same_line_without_content_evidence(self):
        matched=dict(id=20,expense_id=8,expense_item_key='line-b',expense_date='2026-09-01',matched_amount=10,beneficiary_id=2,project_name='Fuel')
        calls,ai_count=self.run_case(module.run_expense_duplicate_checks,matched,count=5)
        self.assertEqual(ai_count,3)
        self.assertEqual(sum('insert into expense_duplicate_checks' in c.args[0] for c in calls),5)
        matched.update(expense_id=7,expense_item_key='line-a')
        calls,ai_count=self.run_case(module.run_expense_duplicate_checks,matched)
        self.assertEqual(ai_count,0)
        self.assertFalse(any('insert into expense_duplicate_checks' in c.args[0] for c in calls))

    def test_missing_file_fallback_and_cached_fingerprints(self):
        row=dict(id=3,file_sha256='cached',image_dhash=None)
        with patch.object(module,'db') as database,patch.object(module,'expense_attachment_fingerprints',side_effect=FileNotFoundError):
            self.assertEqual(module.ensure_expense_attachment_fingerprints(row),('cached',''))
            row.update(file_sha256='',expense_id=7,stored_filename='missing.pdf')
            self.assertEqual(module.ensure_expense_attachment_fingerprints(row),('',''))
            database.assert_not_called()

    def test_identical_receipt_rerun_and_human_review(self):
        fixture=fixtures.ExpenseOnBehalfTest()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        first,second=fixture.create(),fixture.create()
        app=fixture.app
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'EXPENSE_ATTACHMENTS_DIR',folder),app.app.test_request_context('/'):
            app.g.user={'id':fixture.people['Submitter']}
            for expense in (first,second):
                app.save_expense_attachment(expense['id'],FileStorage(stream=io.BytesIO(b'%PDF-1.4 synthetic duplicate'),filename='receipt.pdf',content_type='application/pdf'),'line-test')
            app.run_expense_duplicate_checks(second['id'])
            app.run_expense_duplicate_checks(second['id'])
            app.db().commit()
            checks=app.expense_duplicate_checks(second['id'])
            self.assertEqual(len(checks),1)
            self.assertEqual(checks[0]['score'],100)
            self.assertEqual(checks[0]['risk_level'],'high')
            check_id=checks[0]['id']
        url=f'/expense-duplicate-checks/{check_id}/review'
        self.assertEqual(fixture.http.post(url,data={'review_status':'confirmed'}).status_code,403)
        fixture.login('Manager')
        self.assertEqual(fixture.http.post(url,data={'review_status':'bad'}).status_code,400)
        for status in ('confirmed','not_duplicate','legal_split'):
            self.assertEqual(fixture.http.post(url,data={'review_status':status}).status_code,302)
            with app.app.app_context():
                check=app.db().execute('select * from expense_duplicate_checks where id = ?',(check_id,)).fetchone()
                self.assertEqual(check['review_status'],status)
                self.assertEqual(check['reviewed_by'],fixture.people['Manager'])
