"""Transfer response/transaction equivalence without legacy settlement fixtures."""
import ast
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch
import app as module
import test_expense_attachment_module_contract as fixtures


class ExpenseTransferContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=subprocess.check_output(['git','show','8443d0e:app.py'],cwd=Path(__file__).parent,text=True,encoding='utf-8')
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='transfer_expense_attachment')
        node.decorator_list=[]
        cls.reference=dict(vars(module))
        exec(compile(ast.Module(body=[node],type_ignores=[]),'baseline-expense-transfer.py','exec'),cls.reference)

    def run_case(self,function,case,history,detail):
        attachment=dict(id=1,expense_id=7,original_filename='receipt.pdf',content_type='application/pdf')
        expense=dict(id=7,expense_number='EX-TEST')
        database=Mock()
        database.execute.return_value.fetchone.side_effect=[attachment,{'id':9} if case=='existing' else None]
        reimbursement=None if case=='missing_settlement' else dict(id=8,status='approved' if case=='locked' else 'draft',file_name='settlement.pdf')
        copy=Mock(return_value=case!='missing_file')
        if case in ('race','database','io','invalid'):
            error={'race':module.IntegrityError,'database':module.DatabaseError,'io':OSError,'invalid':ValueError}[case]
            copy.side_effect=error('synthetic transfer failure')
        log=Mock()
        deps=dict(db=lambda:database,require_expense=lambda _: (expense,{'id':3}),can_transfer_expense_attachment=lambda _:True,latest_customer_reimbursement=lambda _:reimbursement,copy_file_to_customer_reimbursement_attachment=copy,expense_attachment_path=lambda _:'synthetic-receipt.pdf',log_action=log)
        with module.app.test_request_context('/',method='POST',data={'return_to':'detail' if detail else 'edit'},headers={'X-History-Replace':'1' if history else '0'}),patch.multiple(module,**deps),patch.dict(self.reference,deps):
            module.g.user={'id':4}
            response=function(1)
            output=(response.status_code,response.get_json() if response.is_json else response.location)
        return output,database.mock_calls,copy.mock_calls,log.mock_calls

    def test_response_and_transaction_branches_match_original(self):
        for case in ('success','existing','missing_settlement','locked','missing_file','race','database','io','invalid'):
            for history in (False,True):
                for detail in (False,True):
                    self.assertEqual(self.run_case(module.transfer_expense_attachment.__wrapped__,case,history,detail),self.run_case(self.reference['transfer_expense_attachment'],case,history,detail),(case,history,detail))

    def test_endpoint_identity_and_transfer_guards(self):
        rules=[r for r in module.app.url_map.iter_rules() if r.endpoint=='transfer_expense_attachment']
        self.assertEqual(len(rules),1)
        self.assertEqual(rules[0].rule,'/expense-attachments/<int:attachment_id>/transfer')
        self.assertEqual(rules[0].methods,{'POST','OPTIONS'})
        self.assertIs(module.app.view_functions['transfer_expense_attachment'],module.transfer_expense_attachment)
        for case in ('existing','missing_settlement','locked'):
            response,db,copy,_=self.run_case(module.transfer_expense_attachment.__wrapped__,case,True,True)
            self.assertEqual(copy,[])
            self.assertEqual(response[1]['ok'],case=='existing')
        response,db,copy,_=self.run_case(module.transfer_expense_attachment.__wrapped__,'race',True,True)
        self.assertTrue(response[1]['ok'])
        self.assertIn('rollback',str(db))

    def test_http_denial_and_missing_attachment_do_not_copy(self):
        helper=fixtures.ExpenseAttachmentContractTest()
        self.addCleanup(helper.doCleanups)
        fixture,id,_=helper.fixture()
        with patch.object(fixture.app,'can_transfer_expense_attachment',return_value=False),patch.object(fixture.app,'copy_file_to_customer_reimbursement_attachment') as copy:
            self.assertEqual(fixture.http.post(f'/expense-attachments/{id}/transfer').status_code,403)
            fixture.login('Manager')
            self.assertEqual(fixture.http.post(f'/expense-attachments/{id+100000}/transfer').status_code,404)
            copy.assert_not_called()
