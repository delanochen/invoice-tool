"""Source popup amounts/reasons, including zero settlements and historical data."""
import unittest
from pathlib import Path
from unittest.mock import Mock
from jinja2 import Environment
from invoice_tool.customer_reimbursements.source_display import enrich_expense_source_details

ROOT=Path(__file__).resolve().parents[1]

class ExpenseSourceDetailsTest(unittest.TestCase):
    def render(self, **source):
        text=(ROOT/'templates/customer_reimbursement_form.html').read_text(encoding='utf-8')
        macro=text[text.index('  {% macro expense_amount_cell'):text.index('  {% endmacro %}')+len('  {% endmacro %}')]
        env=Environment(autoescape=True)
        env.filters['money']=lambda n: f'${float(n):,.2f}'
        module=env.from_string(macro).module
        return str(module.expense_amount_cell(dict(auto_airfare=source['amount'],airfare=0,expense_sources={'airfare':[source]}),'airfare'))

    def test_reduced_and_zero_amounts_show_original_amount_and_reason(self):
        for amount in (60,0):
            with self.subTest(amount=amount):
                html=self.render(amount=amount, source_amount=100, expense_number='EXP-1',line_number=1,expense_url='/expense/1',description='票据 <说明>',adjustment_reason='客户不承担 <私人行程>')
                for expected in ('expense-source-icon','单据金额','$100.00','结算金额',f'${amount:.2f}','核减金额',f'${100-amount:.2f}','核减原因','客户不承担 &lt;私人行程&gt;','href="/expense/1"','票据 &lt;说明&gt;'):
                    self.assertIn(expected,html)

    def test_full_amount_and_missing_history(self):
        html=self.render(amount=100,source_amount=100,description='正常')
        self.assertNotIn('核减原因',html)
        html=self.render(amount=60,description='旧数据')
        self.assertIn('历史快照未记录',html)
        html=self.render(amount=60,source_amount=100)
        self.assertIn('未记录核减原因，请核实',html)

    def test_source_merge_preserves_document_amount_and_reduction_reason(self):
        import inspect
        from decimal import Decimal
        from invoice_tool.customer_reimbursements.source_merge import merge_approved_expenses_into_customer_reimbursement as merge
        expense=dict(expense_id=1,expense_number='EXP-1',line_key='a',line_number=1,
                     worker_name='Test',project_name='机票',fuel_vehicle_type='',
                     item_description='票据说明',amount=0,full_amount=100,
                     adjustment_reason='客户不承担',expense_date='2026-10-28')
        dependencies={name:Mock() for name,param in inspect.signature(merge).parameters.items()
                      if param.kind==param.KEYWORD_ONLY}
        dependencies.update(_runtime_api=lambda:{},
            linked_approved_expense_rows=lambda *args:[expense],
            calculate_customer_reimbursement_item=lambda row,index:row,
            customer_reimbursement_expense_field=lambda *args:'airfare',
            money_decimal=lambda n:Decimal(str(n or 0)),money_float=float,
            normalized_project_name=lambda n:n or '',_reimbursement_date_key=str,
            _row_field=lambda row,key,default:row.get(key,default))
        rows=merge([dict(worker_name='Test',project_date='2026-10-28')],4,
                   reimbursement_id=11,selection_mode='manual_review',**dependencies)
        source=rows[0]['auto_expense_sources']['airfare'][0]
        self.assertEqual(source['amount'],0)
        self.assertEqual(source['source_amount'],100)
        self.assertEqual(source['adjustment_reason'],'客户不承担')

    def test_only_same_version_links_enrich_missing_fields_without_overwriting_snapshot(self):
        db=Mock()
        db.execute.return_value.fetchall.return_value=[dict(expense_id=1,line_key='a',amount_snapshot=100,adjustment_reason='客户核减')]
        sources=[dict(expense_id=1,line_key='a',amount=60),dict(expense_id=1,line_key='a',amount=50,source_amount=90,adjustment_reason='快照原因'),dict(expense_id=2,line_key='a',amount=20)]
        enrich_expense_source_details([{'expense_sources':{'airfare':sources}}],11,db)
        self.assertEqual(db.execute.call_args.args[1],(11,))
        self.assertIn('where links.customer_reimbursement_id = ?',db.execute.call_args.args[0])
        self.assertEqual(sources[0]['source_amount'],100)
        self.assertEqual(sources[0]['adjustment_reason'],'客户核减')
        self.assertEqual(sources[1]['source_amount'],90)
        self.assertEqual(sources[1]['adjustment_reason'],'快照原因')
        self.assertNotIn('source_amount',sources[2])

if __name__=='__main__': unittest.main()
