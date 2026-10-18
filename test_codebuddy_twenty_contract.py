"""Independent d3ea404 helper behavior, signature and ordered side-effect contracts."""
import ast
import copy
from contextlib import ExitStack
from datetime import date
from decimal import Decimal
from io import BytesIO
import inspect
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from flask import g
import app as target

NAMES=['require_invoice_access','invoice_totals','recognize_invoice_if_accounting_enabled','uploaded_attachments_from_request','load_invoice','invoice_service_order','create_invoice_from_form','update_invoice_from_form','send_invoice_email','save_named_attachment','get_company_attachments','record_email_delivery','email_delivery_summary','is_image_attachment','is_customer_reimbursement_image_attachment','xlsx_column_name','build_simple_xlsx','build_multi_sheet_xlsx','parsed_iso_date','labor_report_entries','send_email']

class Contract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=subprocess.check_output(['git','show','d3ea404:app.py'],cwd=Path(__file__).parent,text=True,encoding='utf-8')
        nodes=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
        cls.reference=dict(vars(target));exec(compile(ast.Module(body=nodes,type_ignores=[]),'independent-d3ea404','exec'),cls.reference)

    def same(self,name,args=(),kwargs=None,form=None,files=None,setup=None):
        self.assertEqual(inspect.signature(self.reference[name]),inspect.signature(getattr(target,name)))
        def evaluate(old):
            trace=[]
            def fn(key,value=None,error=None):
                def call(*a,**kw):
                    trace.append((key,copy.deepcopy(a),copy.deepcopy(kw)))
                    if error:raise error
                    return copy.deepcopy(value)
                return call
            deps=setup(trace,fn) if setup else {}
            flask_app=target.app
            with ExitStack() as stack:
                if deps:stack.enter_context(patch.multiple(target,**deps));stack.enter_context(patch.dict(self.reference,deps))
                with flask_app.test_request_context('/contract',method='POST',data=files or form or {}):
                    g.user={'id':3,'name':'Synthetic user','role':'admin'}
                    try:
                        result=(self.reference[name] if old else getattr(target,name))(*copy.deepcopy(args),**copy.deepcopy(kwargs or {}))
                        if isinstance(result,BytesIO):
                            with zipfile.ZipFile(result) as z:result={n:z.read(n) for n in z.namelist()}
                        outcome=('value',result)
                    except Exception as error:outcome=('error',type(error).__name__,str(error))
            return outcome,trace
        expected=evaluate(True);actual=evaluate(False)
        self.assertEqual(expected,actual,(name,args,kwargs,form))
        if expected[0][0]=='error':self.assertIn(expected[0][1],{'Forbidden','NotFound','ValueError','RuntimeError'},expected)
        return expected

    def database(self,trace,fn,one=None,rows=None,execute_error=None):
        def execute(sql,params=()):
            trace.append(('sql',sql,copy.deepcopy(params)))
            if execute_error:raise execute_error
            return SimpleNamespace(fetchone=lambda:copy.deepcopy(one),fetchall=lambda:copy.deepcopy(rows or []),lastrowid=17)
        return SimpleNamespace(execute=execute,commit=fn('commit'),rollback=fn('rollback'))

class InvoiceAccessTest(Contract):
    def test_invoice_access_missing_menu_client_and_success(self):
        for menu,client,row in [(False,True,{'client_id':2}),(True,False,{'client_id':2}),(True,True,None),(True,True,{'id':7,'client_id':2})]:
            with self.subTest(menu=menu,client=client,row=row):
                self.same('require_invoice_access',(7,),setup=lambda tr,fn:dict(can_view_invoices=fn('menu',menu),can_access_client=fn('client',client),db=lambda:self.database(tr,fn,one=row)))

class InvoiceTotalsTest(Contract):
    def test_totals_empty_mixed_negative(self):
        for rows in ([],[{'amount':'100','tax_rate':'10'},{'amount':None,'tax_rate':None}],[{'amount':-100,'tax_rate':5}]):
            with self.subTest(rows=rows):self.same('invoice_totals',(7,),setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn,rows=rows)))

class InvoiceRecognitionTest(Contract):
    def test_accounting_disabled_enabled_and_failure(self):
        for enabled,error in ((False,None),(True,None),(True,ValueError('synthetic posting failure'))):
            def setup(tr,fn):
                connection=self.database(tr,fn)
                return dict(db=lambda:connection,accounting_base_enabled=lambda c:enabled,InvoiceRecognitionService=lambda c:SimpleNamespace(recognize=fn('recognize',{'id':1},error)))
            with self.subTest(enabled=enabled,error=error):self.same('recognize_invoice_if_accounting_enabled',(7,),kwargs={'source_version':2},setup=setup)

class InvoiceUploadsTest(Contract):
    def test_empty_and_both_upload_fields(self):
        self.same('uploaded_attachments_from_request')
        # FileStorage identity differs per request; compare filename/content shape explicitly.
        for old in (True,False):
            with target.app.test_request_context('/contract',method='POST',data={'attachments':(BytesIO(b'a'),'a.txt'),'attachment':(BytesIO(b'b'),'b.txt')}):
                result=(self.reference if old else vars(target))['uploaded_attachments_from_request']()
                shape=[(f.filename,f.read()) for f in result]
                if old:expected=shape
                else:self.assertEqual(expected,shape)

class InvoiceLoadingTest(Contract):
    def test_load_invoice_and_optional_order(self):
        self.same('load_invoice',(7,),setup=lambda tr,fn:dict(require_invoice_access=fn('access',{'client_id':2}),db=lambda:self.database(tr,fn,one={'id':2},rows=[{'id':1}])))
        for order in (None,9):
            with self.subTest(order=order):self.same('invoice_service_order',({'service_order_id':order},),setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn,one={'id':9})))

class InvoiceFormContract(Contract):
    def form_setup(self,options):
        def setup(tr,fn):
            attempt=[0]
            def execute(sql,params=()):
                tr.append(('sql',sql,copy.deepcopy(params)))
                if 'insert into invoices (' in sql:
                    attempt[0]+=1
                    if options.get('collision') and attempt[0]<=options['collision']:raise target.IntegrityError('synthetic collision')
                if 'customer_reimbursements where id' in sql:
                    row={'id':4,'service_order_id':9,'status':options.get('source_status','approved'),'invoice_id':options.get('linked')}
                elif 'select client_id from service_orders' in sql:row={'client_id':2}
                else:row={'exists':1} if options.get('posted') else None
                return SimpleNamespace(fetchone=lambda:row,lastrowid=17)
            connection=SimpleNamespace(execute=execute,commit=fn('commit'))
            return dict(db=lambda:connection,posted_client_id=fn('client',2),can_access_client=fn('access',not options.get('forbidden')),
                posted_service_order_id=fn('order',9),service_order_active_invoice=fn('active',options.get('active')),
                invoice_number_exists=fn('exists',False),next_invoice_number=fn('number','INV-SYNTHETIC'),
                invoice_items_from_form=fn('items',[({'id':1,'name':'Work','tax_rate':10},'25.50')]),
                finish_invoice_save_token=fn('token'),now=fn('now','2026-10-17T00:00:00'),
                recognize_invoice_if_accounting_enabled=fn('recognize'),uploaded_attachments_from_request=fn('uploads',[]),
                copy_customer_reimbursement_attachments_to_invoice=fn('copy_settlement'),copy_mileage_proofs_to_invoice=fn('copy_mileage'),
                invoice_totals=fn('totals',{'total':28.05}),log_action=fn('audit'),accounting_base_enabled=fn('accounting',bool(options.get('posted'))))
        return setup

class InvoiceCreateTest(InvoiceFormContract):
    def test_create_guards_duplicate_number_retry_and_source(self):
        for form,options in [({},{}),({},dict(forbidden=True)),({'customer_reimbursement_id':'bad'},{}),({'customer_reimbursement_id':'4'},{}),({'customer_reimbursement_id':'4'},dict(source_status='draft')),({'customer_reimbursement_id':'4'},dict(linked=10)),({'customer_reimbursement_id':'4'},dict(active={'id':10})),({},dict(collision=1)),({},dict(collision=30))]:
            with self.subTest(form=form,options=options):self.same('create_invoice_from_form',kwargs={'save_token':'synthetic-token'},form=form,setup=self.form_setup(options))

class InvoiceUpdateTest(InvoiceFormContract):
    def test_update_success_access_and_accounting_guard(self):
        for options in ({},{'forbidden':True},{'posted':True}):
            with self.subTest(options=options):self.same('update_invoice_from_form',(7,),form={'notes':' note ','currency':'USD'},setup=self.form_setup(options))

class InvoiceMailTest(Contract):
    def test_mail_missing_recipient_success_and_transport_failure(self):
        for email,error in (('',None),('synthetic@example.invalid',None),('synthetic@example.invalid',RuntimeError('synthetic transport failure'))):
            def setup(tr,fn):
                return dict(load_invoice=fn('load',({'invoice_number':'INV-TEST'},{'email':email},[])),
                    invoice_email_attachments=fn('attachments',[{'filename':'test.pdf'}]),render_template=fn('template','HTML'),invoice_service_order=fn('order',None),
                    invoice_totals=fn('totals',{'total':10}),get_company_profile=fn('company',{'name':'Test'}),get_invoice_terms=fn('terms',''),get_payment_instructions=fn('payment',''),
                    send_email=fn('send',error=error),record_email_delivery=fn('record'),log_action=fn('audit'),now=fn('now','2026-10-17'),db=lambda:self.database(tr,fn))
            with self.subTest(email=email,error=error):self.same('send_invoice_email',(7,),setup=setup)

class NamedAttachmentTest(Contract):
    def test_named_attachment_missing_invalid_owned_unowned(self):
        for filename,allowed,owned in [('',True,False),('bad.exe',False,False),('sample.txt',True,False),('../sample.txt',True,True)]:
            def setup(tr,fn):
                return dict(allowed_attachment=fn('allowed',allowed),secrets=SimpleNamespace(token_hex=fn('token','synthetic')),now=fn('now','2026-10-17'),
                    os=SimpleNamespace(path=target.os.path,makedirs=fn('mkdir')),db=lambda:self.database(tr,fn))
            def execute(old):
                tr=[]
                def fn(k,value=None,error=None):
                    def call(*a,**kw):tr.append((k,a,kw));return value
                    return call
                deps=setup(tr,fn);upload=SimpleNamespace(filename=filename,content_type='text/plain',save=fn('save'))
                with patch.multiple(target,**deps),patch.dict(self.reference,deps),target.app.test_request_context('/'):
                    g.user={'id':3}
                    try:value=(self.reference['save_named_attachment'] if old else target.save_named_attachment)(upload,'synthetic-dir','company_attachments','owner_id' if owned else None,2 if owned else None);outcome=('value',value)
                    except Exception as error:outcome=('error',type(error).__name__,str(error))
                return outcome,tr
            with self.subTest(filename=filename,owned=owned):self.assertEqual(execute(True),execute(False))

class CompanyAttachmentsTest(Contract):
    def test_company_attachment_query(self):
        self.same('get_company_attachments',setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn,rows=[{'id':7}])))

class DeliveryRecordTest(Contract):
    def test_optional_columns_and_null_recipient(self):
        for kwargs in ({},{'status':'failed','error_message':'synthetic','employee_id':4}):
            with self.subTest(kwargs=kwargs):self.same('record_email_delivery',('invoice',7,None,'subject'),kwargs=kwargs,setup=lambda tr,fn:dict(now=fn('now','2026-10-17'),db=lambda:self.database(tr,fn)))

class DeliverySummaryTest(Contract):
    def test_empty_and_legacy_summary(self):
        for row in ({'send_count':None,'last_sent_at':None,'has_legacy':None},{'send_count':3,'last_sent_at':'2026-10-17','has_legacy':1}):
            with self.subTest(row=row):self.same('email_delivery_summary',('invoice',7),setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn,one=row)))

class ImageDetectionTest(Contract):
    def test_content_type_extension_and_file_fallback(self):
        for mime,name in [('image/png','x.bin'),('','photo.JPG'),(None,None),('text/plain','x.txt')]:
            with self.subTest(mime=mime,name=name):self.same('is_image_attachment',(mime,name))
        for image,valid in ((True,False),(False,True),(False,False)):
            with self.subTest(image=image,valid=valid):self.same('is_customer_reimbursement_image_attachment',({'content_type':'','original_filename':'test.bin'},),setup=lambda tr,fn:dict(is_image_attachment=fn('image',image),valid_image_file=fn('valid',valid),customer_reimbursement_attachment_path=fn('path','synthetic')))

class XlsxColumnsTest(Contract):
    def test_column_boundaries(self):
        for index in (0,25,26,701,702,16383):
            with self.subTest(index=index):self.same('xlsx_column_name',(index,))

class XlsxSingleTest(Contract):
    def test_zip_members_numeric_text_and_escaping(self):
        for rows in ([],[[Decimal('12.345'),True,None,'<&中文']]):
            with self.subTest(rows=rows):self.same('build_simple_xlsx',(['A','B','C','D'],rows),kwargs={'sheet_name':'Test & sample'})

class XlsxMultiTest(Contract):
    def test_multi_sheet_and_empty_contract(self):
        for sheets in ([],[('Bad/?Name',['A','B'],[[1,'<&']]),('中文',['C'],[[True]])]):
            with self.subTest(sheets=sheets):self.same('build_multi_sheet_xlsx',(sheets,))

class IsoDateTest(Contract):
    def test_date_empty_invalid_and_leap_day(self):
        for value in (None,'','bad','2024-02-29','2023-02-29',date(2026,10,17)):
            with self.subTest(value=value):self.same('parsed_iso_date',(value,))

class LaborEntriesTest(Contract):
    def test_date_modes_worker_filter_and_hour_combination(self):
        for mode,worker in [('actual',''),('attendance','3'),('report','bad')]:
            def setup(tr,fn):
                return dict(db=lambda:self.database(tr,fn,rows=[{'worker_count':2,'id':7}]),split_report_labor_hours=fn('hours',{'standard_hours':4,'overtime_hours':1,'holiday_hours':2}))
            with self.subTest(mode=mode,worker=worker):self.same('labor_report_entries',('2026-01-01','2026-02-01',worker,mode),setup=setup)

class SmtpTransportTest(Contract):
    def test_test_environment_missing_config_tls_login_and_errors(self):
        for case in ('test','host_missing','password_missing','success','no_tls','auth_error','transport_error'):
            def setup(tr,fn):
                settings=dict(host='' if case=='host_missing' else 'smtp.invalid',user='' if case=='no_tls' else 'test',password='' if case=='password_missing' else 'synthetic',port='587',tls='false' if case=='no_tls' else 'true',**{'from':'synthetic@example.invalid'})
                class Message:
                    def __setitem__(self,k,v):tr.append(('header',k,v))
                    def set_content(self,*a,**kw):tr.append(('text',a,kw))
                    def add_alternative(self,*a,**kw):tr.append(('html',a,kw))
                    def add_attachment(self,*a,**kw):tr.append(('attachment',a,kw))
                class SMTP:
                    def __init__(self,*a,**kw):tr.append(('connect',a,kw))
                    def __enter__(self):return self
                    def __exit__(self,*a):return False
                    def starttls(self):tr.append(('tls',))
                    def login(self,*a):
                        tr.append(('login',a))
                        if case=='auth_error':raise target.smtplib.SMTPAuthenticationError(535,b'synthetic')
                    def send_message(self,m):
                        tr.append(('send_message',))
                        if case=='transport_error':raise OSError('synthetic transport failure')
                smtp=SimpleNamespace(SMTP=SMTP,SMTPAuthenticationError=target.smtplib.SMTPAuthenticationError,SMTPException=target.smtplib.SMTPException)
                return dict(app=SimpleNamespace(config={'DEPLOYMENT_ENV':'test' if case=='test' else 'synthetic'}),get_smtp_settings=fn('settings',settings),EmailMessage=Message,smtplib=smtp)
            with self.subTest(case=case):self.same('send_email',('recipient@example.invalid','subject','<p>test</p>'),kwargs={'attachments':[dict(content=b'test',maintype='text',subtype='plain',filename='sample.txt')]},setup=setup)
