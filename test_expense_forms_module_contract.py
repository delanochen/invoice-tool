"""Expense parsing and retained settlement-reference behavior."""
import ast
import inspect
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

import app as module

NAMES = ('expense_defaults','expense_items','expense_items_from_form','save_expense_items')


class ExpenseFormsContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = subprocess.check_output(['git','show','1ee793d:app.py'],cwd=Path(__file__).parent,text=True,encoding='utf-8')
        nodes = [n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
        cls.reference = dict(vars(module))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'baseline-expense-forms.py','exec'),cls.reference)

    def test_signatures_preserved(self):
        for name in NAMES:
            self.assertEqual(inspect.signature(getattr(module,name)),inspect.signature(self.reference[name]))

    def test_parser_valid_invalid_and_fuel_choices_match_baseline(self):
        for amount in ('0','-1','bad','1','12.34'):
            for fuel in ('','personal','rental','invalid'):
                outcomes = []
                for function in (module.expense_items_from_form,self.reference['expense_items_from_form']):
                    database = Mock()
                    database.execute.return_value.fetchone.return_value = {'id':7,'name':'Fuel'}
                    with module.app.test_request_context('/',method='POST',data={'project_id':'7','item_amount':amount,'item_line_key':'retained','fuel_vehicle_type':fuel}):
                        with patch.object(module,'db',return_value=database), patch.object(module,'is_fuel_project_name',return_value=True), patch.dict(self.reference,db=lambda:database,is_fuel_project_name=lambda _:True):
                            try:
                                outcomes.append(('value',function(12)))
                            except ValueError as error:
                                outcomes.append(('error',str(error)))
                self.assertEqual(*outcomes)

    def save(self,function,items,linked=False):
        database = Mock()
        database.execute.return_value.fetchall.return_value = [{'id':81,'line_key':'keep','project':'Fuel'}]
        database.execute.return_value.fetchone.return_value = {'linked':1} if linked else None
        with patch.object(module,'db',return_value=database), patch.object(module,'get_expense_attachments',return_value=[]), patch.dict(self.reference,db=lambda:database,get_expense_attachments=lambda _:[]):
            function(12,items)
        return database

    def test_retained_row_updated_in_place_preserving_settlement_reference(self):
        item = dict(line_key='keep',project={'id':7,'name':'Fuel'},amount=20,description='receipt',fuel_vehicle_type='personal',sort_order=0)
        current = self.save(module.save_expense_items,[item])
        original = self.save(self.reference['save_expense_items'],[item])
        self.assertEqual(current.mock_calls,original.mock_calls)
        writes = [(call.args[0].strip().lower(),call.args[1]) for call in current.execute.call_args_list if call.args[0].strip().lower().startswith(('update','delete','insert'))]
        self.assertEqual(len(writes),1)
        self.assertTrue(writes[0][0].startswith('update expense_items'))
        self.assertEqual(writes[0][1][-1],81)

    def test_linked_row_removal_rejected_before_writes(self):
        for function in (module.save_expense_items,self.reference['save_expense_items']):
            with self.assertRaisesRegex(ValueError,'已被客户结算引用'):
                self.save(function,[],linked=True)
