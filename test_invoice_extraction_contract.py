"""Invoice helper behavior compared with independently compiled pre-extraction functions."""
import ast
import copy
from contextlib import nullcontext
from datetime import date
import inspect
import io
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile
from werkzeug.datastructures import FileStorage, MultiDict
import app as module

BASE='eca0599'
GROUPS={
 'forms':['invoice_items_from_form','posted_client_id','posted_service_order_id'],
 'tokens':['claim_invoice_save_token','finish_invoice_save_token'],
 'numbering':['next_invoice_number','invoice_number_exists'],
 'storage':['invoice_attachment_dir','attachment_file_path','invoice_attachment_path','get_invoice_attachments','save_uploaded_attachment'],
 'attachment_names':['normalized_attachment_filename','existing_attachment_names','duplicate_attachment_names','validate_attachment_uploads','unique_invoice_attachment_filename'],
 'copying':['copy_file_to_invoice_attachment','copy_customer_reimbursement_attachments_to_invoice','copy_mileage_proofs_to_invoice'],
 'exports':['build_invoice_zip','render_invoice_export_html'],
 'email_attachments':['customer_reimbursement_email_attachments','unique_email_attachment_filename','invoice_email_attachments'],
 'pdf':['render_invoice_pdf'],
}


class ContractBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=subprocess.check_output(['git','show',BASE+':app.py'],cwd=Path(__file__).parent,text=True,encoding='utf-8')
        names=set(sum(GROUPS.values(),[]))
        nodes=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in names]
        cls.reference=dict(vars(module))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'baseline-invoice-helpers.py','exec'),cls.reference)

    def signatures(self,group):
        for name in GROUPS[group]:
            self.assertEqual(inspect.signature(getattr(module,name)),inspect.signature(self.reference[name]))

    def same(self,name,*args,deps=None,mocks=()):
        deps=deps or {}
        def outcome(fn):
            try:
                return ('value',fn(*copy.deepcopy(args)))
            except Exception as error:
                return ('error',type(error).__name__,str(error))
        for mock in mocks:
            mock.reset_mock()
        with (patch.multiple(module,**deps) if deps else nullcontext()),patch.dict(self.reference,deps):
            left=outcome(getattr(module,name))
            left_calls=[list(m.mock_calls) for m in mocks]
            for mock in mocks:
                mock.reset_mock()
            right=outcome(self.reference[name])
            right_calls=[list(m.mock_calls) for m in mocks]
        self.assertEqual(left,right,(name,args))
        self.assertEqual(left_calls,right_calls,(name,args))
        return left


class FormsContractTest(ContractBase):
    def test_item_validation_and_order_identity(self):
        self.signatures('forms')
        for projects,amounts,available in [(['1'],['10'],True),(['1'],['0'],True),(['1'],['-1'],True),(['1','1'],['10','-2'],True),(['1'],['bad'],True),(['1'],['10'],False),([],[],True),(['1','2'],['10'],True)]:
            database=Mock()
            database.execute.return_value.fetchone.return_value={'id':1,'name':'Work'} if available else None
            with module.app.test_request_context('/',method='POST',data=MultiDict([('project_id',v) for v in projects]+[('amount',v) for v in amounts])):
                self.same('invoice_items_from_form',deps={'db':lambda:database},mocks=[database])
        for raw in ('','bad','0','-1','7'):
            with module.app.test_request_context('/',method='POST',data={'client_id':raw,'service_order_id':raw}):
                self.same('posted_client_id')
                for start in (None,'2026-10-17'):
                    for require_start in (True,False):
                        for required in (True,False):
                            self.same('posted_service_order_id',require_start,required,deps={'require_service_order':lambda _: {'start_date':start}})


class TokensContractTest(ContractBase):
    def test_token_claim_finish_and_replay(self):
        self.signatures('tokens')
        for rowcount in (0,1):
            database=Mock()
            database.execute.return_value.rowcount=rowcount
            deps={'db':lambda:database,'now':lambda:'2026-10-17T12:00:00'}
            for token in (None,'','  ','token',' token '):
                self.same('claim_invoice_save_token',token,7,deps=deps,mocks=[database])
            self.same('finish_invoice_save_token','token',7,deps=deps,mocks=[database])


class NumberingContractTest(ContractBase):
    def test_largest_sequence_and_excluded_invoice(self):
        self.signatures('numbering')
        database=Mock()
        database.execute.return_value.fetchall.return_value=[{'invoice_number':n} for n in ('IN-2610-0009','IN-2610-0002','IN-2610-bad','IN-2610-0100')]
        lock=Mock()
        deps={'db':lambda:database,'lock_number_allocation':lock,'date':SimpleNamespace(today=lambda:date(2026,10,17))}
        result=self.same('next_invoice_number',deps=deps,mocks=[database,lock])
        self.assertEqual(result,('value','IN-2610-0101'))
        for found in (None,{'id':7}):
            database.execute.return_value.fetchone.return_value=found
            for exclude in (None,7):
                self.same('invoice_number_exists','IN-2610-0001',exclude,deps={'db':lambda:database},mocks=[database])


class StorageContractTest(ContractBase):
    def test_paths_upload_bytes_and_database_metadata(self):
        self.signatures('storage')
        with tempfile.TemporaryDirectory() as folder:
            database=Mock()
            database.execute.return_value.fetchall.return_value=[]
            deps={'ATTACHMENTS_DIR':folder,'db':lambda:database,'g':SimpleNamespace(user={'id':3}),'now':lambda:'2026-10-17T12:00:00','secrets':SimpleNamespace(token_hex=lambda _: 'fixed-file'),'existing_attachment_names':lambda _:set()}
            for name,args in [('invoice_attachment_dir',(7,)),('invoice_attachment_path',(7,)),('attachment_file_path',({'invoice_id':7,'stored_filename':'stored.pdf'},)),('get_invoice_attachments',(7,))]:
                self.same(name,*args,deps=deps,mocks=[database])
            for function in (module.save_uploaded_attachment,self.reference['save_uploaded_attachment']):
                with (patch.multiple(module,**deps) if deps else nullcontext()),patch.dict(self.reference,deps):
                    function(7,FileStorage(stream=io.BytesIO(b'%PDF synthetic'),filename='receipt.pdf',content_type='application/pdf'))
                self.assertEqual((Path(folder)/'7/fixed-file.pdf').read_bytes(),b'%PDF synthetic')
                self.assertEqual(database.execute.call_args.args[1],(7,'receipt.pdf','fixed-file.pdf','application/pdf',3,'2026-10-17T12:00:00'))


class AttachmentNamesContractTest(ContractBase):
    def test_duplicate_and_unique_names_and_upload_validation(self):
        self.signatures('attachment_names')
        uploads=[SimpleNamespace(filename=n) for n in ('Receipt.pdf',' receipt.PDF ','other.pdf','')]
        database=Mock()
        database.execute.return_value.fetchall.return_value=[{'original_filename':'Receipt.pdf'}]
        self.same('existing_attachment_names',7,deps={'db':lambda:database},mocks=[database])
        for existing in (set(),{'receipt.pdf','receipt-2.pdf'}):
            deps={'existing_attachment_names':lambda _:set(existing)}
            self.same('duplicate_attachment_names',uploads,7,deps=deps)
            for name in ('Receipt.pdf',' ../Receipt.pdf ','','.PDF'):
                self.same('unique_invoice_attachment_filename',7,name,deps=deps)
                self.same('normalized_attachment_filename',name)
            flash=Mock()
            for allowed in (False,True):
                self.same('validate_attachment_uploads',uploads,7,deps={**deps,'allowed_attachment':lambda _:allowed,'flash':flash},mocks=[flash])


class CopyingContractTest(ContractBase):
    def test_real_copy_and_missing_source(self):
        self.signatures('copying')
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf'
            source.write_bytes(b'synthetic copied receipt')
            database=Mock()
            deps={'ATTACHMENTS_DIR':folder,'db':lambda:database,'g':SimpleNamespace(user={'id':3}),'now':lambda:'2026-10-17T12:00:00','secrets':SimpleNamespace(token_hex=lambda _: 'copied'),'existing_attachment_names':lambda _:set()}
            for function in (module.copy_file_to_invoice_attachment,self.reference['copy_file_to_invoice_attachment']):
                with (patch.multiple(module,**deps) if deps else nullcontext()),patch.dict(self.reference,deps):
                    self.assertTrue(function(7,str(source),'receipt.pdf','application/pdf',3))
                    self.assertFalse(function(7,str(source)+'-missing','missing.pdf'))
                self.assertEqual((Path(folder)/'7/copied.pdf').read_bytes(),source.read_bytes())

    def test_source_copy_counts_and_mileage_name_deduplication(self):
        rows=[dict(id=1,original_filename='receipt.pdf',content_type='application/pdf',uploaded_by=3,report_date='2026-10-17'),dict(id=2,original_filename='receipt.pdf',content_type='application/pdf',uploaded_by=3,report_date='2026-10-17')]
        database=Mock()
        database.execute.return_value.fetchall.return_value=rows
        copy_file=Mock(return_value=True)
        deps={'db':lambda:database,'get_customer_reimbursement_attachments':lambda _:rows,'customer_reimbursement_attachment_path':lambda r:str(r['id']),'report_attachment_path':lambda r:str(r['id']),'existing_attachment_names':lambda _:set(),'copy_file_to_invoice_attachment':copy_file}
        self.same('copy_customer_reimbursement_attachments_to_invoice',7,8,deps=deps,mocks=[database,copy_file])
        result=self.same('copy_mileage_proofs_to_invoice',7,9,deps=deps,mocks=[database,copy_file])
        self.assertEqual(result,('value',1))


class ExportsContractTest(ContractBase):
    def test_zip_contains_pdf_and_original_attachment_bytes(self):
        self.signatures('exports')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'receipt.pdf'
            path.write_bytes(b'receipt bytes')
            deps={'render_invoice_pdf':lambda *args:b'%PDF invoice','get_invoice_attachments':lambda _:[{'original_filename':'receipt.pdf'}],'attachment_file_path':lambda _:str(path)}
            results=[]
            with (patch.multiple(module,**deps) if deps else nullcontext()),patch.dict(self.reference,deps):
                for function in (module.build_invoice_zip,self.reference['build_invoice_zip']):
                    name,payload=function({'invoice_number':'IN-TEST','id':7},{},[])
                    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                        results.append((name,{n:archive.read(n) for n in archive.namelist()}))
            self.assertEqual(*results)
            self.assertEqual(results[0][1],{'IN-TEST/IN-TEST.pdf':b'%PDF invoice','IN-TEST/receipt.pdf':b'receipt bytes'})

    def test_html_template_context_resolves_current_dependencies(self):
        deps={'render_template':lambda *a,**k:(a,k),'invoice_service_order':lambda _: {'id':9},'invoice_totals':lambda _: {'total':42},'get_company_profile':lambda: {'name':'Synthetic'},'get_invoice_terms':lambda:'Terms','get_payment_instructions':lambda: {'method':'Test'},'STATUS_LABELS':{'draft':'Draft test'}}
        self.same('render_invoice_export_html',{'id':7},{'name':'Client'},[],deps=deps)


class EmailAttachmentsContractTest(ContractBase):
    def test_filename_collisions_missing_files_and_settlement_attachments(self):
        self.signatures('email_attachments')
        for name in ('','Receipt.pdf','/folder/Receipt.pdf','RECEIPT.PDF'):
            self.same('unique_email_attachment_filename',name,{'receipt.pdf','receipt-2.pdf'})
        for order in (None,9):
            self.same('customer_reimbursement_email_attachments',{'service_order_id':order},deps={'latest_customer_reimbursement':lambda _:None})
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'receipt.pdf'
            path.write_bytes(b'original receipt')
            rows=[{'id':1,'original_filename':'IN-TEST.pdf','content_type':'application/pdf'},{'id':2,'original_filename':'missing.pdf','content_type':None}]
            deps={'render_invoice_pdf':lambda *args:b'%PDF invoice','get_invoice_attachments':lambda _:rows,'attachment_file_path':lambda row:str(path) if row['id']==1 else str(path)+'-missing','customer_reimbursement_email_attachments':lambda _:[dict(filename='in-test.PDF',content=b'settlement',maintype='application',subtype='pdf')]}
            result=self.same('invoice_email_attachments',{'id':7,'invoice_number':'IN-TEST','service_order_id':9},{},[],deps=deps)
            self.assertEqual([a['filename'] for a in result[1]],['IN-TEST.pdf','IN-TEST-2.pdf','in-test-3.PDF'])


class PdfContractTest(ContractBase):
    def test_real_pdf_bytes_match_with_invariant_clock_and_page_breaks(self):
        self.signatures('pdf')
        real_canvas=module.canvas.Canvas
        deps={'canvas':SimpleNamespace(Canvas=lambda *a,**k:real_canvas(*a,**k,invariant=1)),'get_company_profile':lambda:dict(name='Synthetic 公司',address='Test address',tax_note='Synthetic tax note'),'get_payment_instructions':lambda:dict(method='Test',beneficiary='Synthetic',bank_name='Test bank',account_number='TEST',routing_number='TEST',swift_bic='TEST'),'get_invoice_terms':lambda:'Test terms','invoice_totals':lambda _:dict(subtotal=100,tax=5,total=105),'invoice_service_order':lambda _:dict(client_order_number='SO-TEST')}
        client=dict(name='Synthetic Client',address='Synthetic Address',country='US')
        items=[dict(description='Synthetic 工作',amount=100,tax_rate=5)]
        for notes in ('Test notes','Long synthetic notes\n'*120):
            invoice=dict(id=7,invoice_number='IN-TEST',issue_date='2026-10-17',due_date='2026-11-17',currency='USD',notes=notes)
            with module.app.test_request_context('/'),patch.multiple(module,**deps),patch.dict(self.reference,deps):
                current=module.render_invoice_pdf(invoice,client,items)
                original=self.reference['render_invoice_pdf'](invoice,client,items)
            self.assertEqual(current,original)
            self.assertTrue(current.startswith(b'%PDF-'))
            self.assertIn(b'%%EOF',current)
            pages=len(re.findall(rb'/Type /Page\b',current))
            self.assertGreaterEqual(pages,2 if '\n' in notes else 1)
