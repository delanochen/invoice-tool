"""Access boundaries compared with original implementations."""
import ast
import inspect
import itertools
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from werkzeug.exceptions import Forbidden, NotFound
import app as module

NAMES=('expense_beneficiary_id','expense_beneficiary_options','posted_expense_beneficiary','expense_access_filter','notify_expense_participants','can_access_expense','can_delete_expense','require_expense')


class ExpenseAccessContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=subprocess.check_output(['git','show','898a05f:app.py'],cwd=Path(__file__).parent,text=True,encoding='utf-8')
        nodes=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
        cls.reference=dict(vars(module))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'baseline-expense-access.py','exec'),cls.reference)

    def test_original_signatures(self):
        for name in NAMES:
            self.assertEqual(inspect.signature(getattr(module,name)),inspect.signature(self.reference[name]))

    def test_role_actor_beneficiary_status_matrix(self):
        for role,user_id,beneficiary,status in itertools.product(('admin','manager','finance','employee','external_employee'),(None,1,2,3),(None,1,2),('draft','returned','submitted','approved')):
            expense=dict(created_by=1,beneficiary_id=beneficiary,status=status)
            deps=dict(g=SimpleNamespace(user=None if user_id is None else {'id':user_id}),normalized_role=lambda:role,is_manager=lambda:role=='manager')
            with patch.multiple(module,**deps),patch.dict(self.reference,deps):
                for name in ('can_access_expense','can_delete_expense'):
                    self.assertEqual(getattr(module,name)(expense),self.reference[name](expense),(role,user_id,beneficiary,status,name))
                if user_id is not None:
                    self.assertEqual(module.expense_access_filter(),self.reference['expense_access_filter']())

    def test_participants_are_notified_once_each(self):
        for beneficiary in (None,1,2):
            expense=dict(created_by=1,beneficiary_id=beneficiary)
            message=Mock()
            with patch.object(module,'create_message',message):
                module.notify_expense_participants(expense,'title','body','/link')
            expected={1,beneficiary or 1}
            self.assertEqual(message.call_count,len(expected))
            self.assertEqual({c.args[0] for c in message.call_args_list},expected)
            self.assertTrue(all(c.args[1:]==('title','body','/link') for c in message.call_args_list))

    def test_posted_beneficiary_validation_matches_original(self):
        for raw in ('','bad','-1','1','2','3',' 2 '):
            outcomes=[]
            expense=dict(created_by=1,beneficiary_id=2)
            deps=dict(expense_beneficiary_options=lambda _: [{'id':1},{'id':2}])
            with module.app.test_request_context('/',method='POST',data={'beneficiary_id':raw}),patch.multiple(module,**deps),patch.dict(self.reference,deps):
                for function in (module.posted_expense_beneficiary,self.reference['posted_expense_beneficiary']):
                    try:
                        outcomes.append(('value',function(expense)))
                    except ValueError as error:
                        outcomes.append(('error',str(error)))
            self.assertEqual(*outcomes)

    def test_require_expense_checks_access_before_loading_order(self):
        database=Mock()
        order=Mock(return_value={'id':9})
        expense=dict(id=7,service_order_id=9)
        with patch.object(module,'db',return_value=database),patch.object(module,'require_service_order',order),patch.object(module,'can_access_expense',return_value=False) as access:
            database.execute.return_value.fetchone.return_value=None
            with self.assertRaises(NotFound):
                module.require_expense(7)
            access.assert_not_called()
            database.execute.return_value.fetchone.return_value=expense
            with self.assertRaises(Forbidden):
                module.require_expense(7)
            order.assert_not_called()
            access.return_value=True
            self.assertEqual(module.require_expense(7),(expense,{'id':9}))
            order.assert_called_once_with(9)
