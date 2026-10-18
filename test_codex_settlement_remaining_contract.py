"""Exact extraction contracts plus independent settlement branch behavior."""
import ast,copy,subprocess
from pathlib import Path
import app as target
from test_codebuddy_twenty_contract import Contract as PreviousContract
BASE='71e89cdada254dcdd6796cb669fd883ca0bf59ce'
GROUPS=[('access', 'invoice_tool/customer_reimbursements/management_access.py', ['can_manage_customer_reimbursement']), ('quotation_rows', 'invoice_tool/customer_reimbursements/quotation_rows.py', ['build_quotation_settlement_rows', 'customer_reimbursement_seed_rows']), ('source_queries', 'invoice_tool/customer_reimbursements/source_queries.py', ['approved_customer_reimbursement_expense_rows', 'customer_reimbursement_pending_sources']), ('source_merge', 'invoice_tool/customer_reimbursements/source_merge.py', ['merge_service_reports_into_customer_reimbursement', 'merge_approved_expenses_into_customer_reimbursement']), ('workspace', 'invoice_tool/customer_reimbursements/workspace.py', ['customer_reimbursement_gate_error', 'customer_reimbursement_person_days', 'customer_reimbursement_lodging_range', 'customer_reimbursement_workspace_data']), ('attachment_files', 'invoice_tool/customer_reimbursements/attachment_files.py', ['get_customer_reimbursement_attachments', 'save_customer_reimbursement_attachment', 'unique_customer_reimbursement_attachment_filename', 'copy_file_to_customer_reimbursement_attachment', 'copy_mileage_proofs_to_customer_reimbursement']), ('item_forms', 'invoice_tool/customer_reimbursements/item_forms.py', ['customer_reimbursement_items_from_form', 'save_customer_reimbursement_items']), ('invoice_items', 'invoice_tool/customer_reimbursements/invoice_items.py', ['latest_customer_reimbursement', 'customer_reimbursement_linked_invoice', 'customer_reimbursement_other_invoice_breakdown', 'customer_reimbursement_invoice_items']), ('updates', 'invoice_tool/customer_reimbursements/updates.py', ['create_customer_reimbursement', 'sync_expense_attachments_to_settlement', '_quotation_totals_with_discount', 'update_customer_reimbursement_totals']), ('documents', 'invoice_tool/customer_reimbursements/documents.py', ['ensure_customer_reimbursement_pdf_record', 'build_customer_reimbursement_pdf', 'ensure_customer_reimbursement_pdf_file', 'customer_reimbursement_outgoing_attachments', 'deliver_customer_reimbursement_email'])]
NAMES=[name for _,_,names in GROUPS for name in names]
class Contract(PreviousContract):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(target.__file__).parent
        cls.original=ast.parse(subprocess.check_output(['git','show',BASE+':app.py'],cwd=cls.root,text=True,encoding='utf-8'))
        cls.nodes={n.name:n for n in cls.original.body if isinstance(n,ast.FunctionDef)}
        cls.reference=dict(vars(target))
        exec(compile(ast.Module(body=[copy.deepcopy(cls.nodes[n]) for n in NAMES],type_ignores=[]),'independent-settlement-remaining','exec'),cls.reference)
class StructureTest(Contract):
    def test_each_body_and_legacy_signature(self):
        current={n.name:n for n in ast.parse((self.root/'app.py').read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef)}
        for _,module,names in GROUPS:
            path=self.root/module
            extracted={n.name:n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef)} if path.exists() else {}
            for name in names:
                with self.subTest(function=name):
                    old=self.nodes[name];actual=copy.deepcopy(extracted.get(name,current[name]))
                    self.assertEqual(ast.dump(old.args),ast.dump(current[name].args))
                    actual.args=copy.deepcopy(old.args)
                    self.assertEqual(ast.dump(old),ast.dump(actual))
class BehaviorTest(Contract):
    def test_permission_short_circuit(self):
        for value in (False,True):
            with self.subTest(value=value):self.same('can_manage_customer_reimbursement',setup=lambda tr,fn:dict(has_action_permission=fn('permission',value)))
    def test_gate_prioritizes_includable_then_pending(self):
        for a,b in (([],[]),([7],[8]),([],[8])):
            with self.subTest(a=a,b=b):self.same('customer_reimbursement_gate_error',({'id':7},),setup=lambda tr,fn:dict(customer_reimbursement_pending_sources=fn('sources',dict(includable=a,pending=b,includable_total=10,pending_total=20)),money=fn('money','10.00')))
    def test_person_days_and_lodging_stays(self):
        self.same('customer_reimbursement_person_days',(7,),setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn,one={'count':3})))
        for rows in ([],[{'work_date':'2026-10-16','user_id':1},{'work_date':'2026-10-19','user_id':1}],[{'work_date':'2026-10-16','user_id':1},{'work_date':'2026-10-17','user_id':2},{'work_date':'2026-10-19','user_id':1}]):
            with self.subTest(rows=rows):self.same('customer_reimbursement_lodging_range',(7,),setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn,rows=rows)))
    def test_query_parameters_and_invoice_fallback(self):
        self.same('approved_customer_reimbursement_expense_rows',(7,'2026-10-17',8),setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn,rows=[{'id':9}])))
        self.same('latest_customer_reimbursement',(7,),setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn,one={'id':8})))
        for reimbursement in (None,{'invoice_id':None},{'invoice_id':9}):
            with self.subTest(reimbursement=reimbursement):self.same('customer_reimbursement_linked_invoice',(reimbursement,7),setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn,one={'id':9}),service_order_active_invoice=fn('fallback',{'id':10})))
    def test_nonquotation_does_not_discount(self):
        self.same('_quotation_totals_with_discount',({'settlement_basis':'actual'},{'total_amount':123.45}))
    def test_other_expenses_only_mapped_sources(self):
        self.same('customer_reimbursement_other_invoice_breakdown',(7,),setup=lambda tr,fn:dict(customer_reimbursement_items=fn('items',[{'auto_expense_sources':{'other':[{'project_name':'Supplies','amount':'12.34'}]}}]),expense_settlement_invoice_field=fn('mapping',('other','Materials'))))
