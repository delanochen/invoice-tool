"""Independent old/new invoice route outcomes and ordered side-effect comparison.

No real mail, invoice writes or attachment removals: domain dependencies are doubles.
Existing attachment-security integration tests run alongside this contract.
"""
import ast
import copy
from contextlib import ExitStack
from datetime import date
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import g, get_flashed_messages
from werkzeug.exceptions import HTTPException, Forbidden
import app as target

BASE='27f12df'
GROUPS={
 'attachment_read':['download_attachment','preview_attachment'],
 'attachment_write':['upload_attachment','delete_attachment'],
 'export':['export_invoice','export_invoice_pdf'],
 'detail':['invoice_detail'], 'send':['send_invoice'],
 'admin_status':['admin_update_invoice_status'],
 'payment':['mark_invoice_paid','unmark_invoice_paid'],
 'void':['void_invoice'], 'delete':['delete_invoice'],
}

class RouteContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=subprocess.check_output(['git','show',BASE+':app.py'],text=True,encoding='utf-8',cwd=Path(__file__).parent)
        wanted=set(sum(GROUPS.values(),[])); nodes=[]
        for n in ast.parse(source).body:
            if isinstance(n,ast.FunctionDef) and n.name in wanted:
                n.decorator_list=[];nodes.append(n)
        cls.reference=dict(vars(target))
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'independent_invoice_routes','exec'),cls.reference)

    def invoke(self,name,old,options):
        options=copy.deepcopy(options)
        invoice=dict(id=7,invoice_number='INV-TEST',status=options.get('status','completed'),created_by=3,paid_at=options.get('paid_at'))
        attachment=dict(id=7,invoice_id=7,original_filename='sample.txt',stored_filename='sample.txt')
        trace=[]
        def function(key,value=None,error=None):
            def call(*args,**kwargs):
                # Capture immutable snapshots: all collaborators return concrete values.
                trace.append((key,copy.deepcopy(args),copy.deepcopy(kwargs)))
                if error: raise error
                return copy.deepcopy(value)
            return call
        def execute(sql,params=()):
            trace.append(('sql',sql,copy.deepcopy(params)))
            if options.get('integrity') and sql.startswith('delete from invoices'):
                raise target.IntegrityError('synthetic reference in use')
            if 'invoice_attachments where id' in sql:
                result=None if options.get('missing') else attachment
            elif 'posting_events' in sql: result={'exists':1} if options.get('recognition') else None
            else: result=dict(name='Creator',email='test@example.invalid')
            return SimpleNamespace(fetchone=lambda:copy.deepcopy(result))
        database=SimpleNamespace(execute=execute,commit=function('commit'),rollback=function('rollback'))
        def make_service(connection):
            self.assertIs(connection,database)
            trace.append(('service',))
            return SimpleNamespace(void=function('service.void',error=ValueError('synthetic void failure') if options.get('void_error') else None))
        def access(identity):
            trace.append(('access',identity))
            if options.get('forbidden'):raise Forbidden()
            return copy.deepcopy(invoice)
        def remove(path):
            trace.append(('remove',path))
            if options.get('file_missing'):raise FileNotFoundError(path)
        deps={
            'db':lambda:database,'require_invoice_access':access,
            'load_invoice':function('load_invoice',(invoice,{'id':2},[{'amount':50}])),
            'attachment_file_path':function('attachment_path','synthetic-path'),
            'safe_attachment_response':function('safe_response','PREVIEW'),
            'uploaded_attachments_from_request':function('uploads',[] if options.get('empty') else ['file-A','file-B']),
            'validate_attachment_uploads':function('validate',not options.get('invalid')),
            'save_uploaded_attachment':function('save_upload'),
            'build_invoice_zip':function('zip',('test-archive',b'PK synthetic archive')),
            'render_invoice_pdf':function('pdf',b'%PDF synthetic test'),
            'invoice_totals':function('totals',{'total':50}),
            'invoice_service_order':function('order',{'id':9}),
            'get_invoice_attachments':function('attachments',[]),
            'get_company_profile':function('company',{'name':'Synthetic company'}),
            'accounting_base_enabled':function('accounting',True),
            'get_invoice_terms':function('terms','terms'),
            'get_payment_instructions':function('payment','payment'),
            'email_delivery_summary':function('delivery',{'sent':False}),
            'render_template':function('template','DETAIL'),
            'create_message':function('message'),'log_action':function('audit'),
            'is_internal_user':function('internal',not options.get('external')),
            'is_external_user':function('external',bool(options.get('external'))),
            'has_action_permission':function('permission',not options.get('no_permission')),
            'send_invoice_email':function('send_email',error=RuntimeError('synthetic SMTP failure') if options.get('send_error') else None),
            'InvoiceRecognitionService':make_service,
            'invoice_attachment_path':function('directory','synthetic-directory'),
            'os':SimpleNamespace(remove=remove),
            'shutil':SimpleNamespace(rmtree=function('rmtree')),
            'date':SimpleNamespace(today=lambda:date(2026,10,17)),
        }
        # A file response double avoids disk/network while retaining output byte assertions.
        def send_file(file,*args,**kwargs):
            data=file.getvalue() if hasattr(file,'getvalue') else file
            trace.append(('send_file',data,args,kwargs))
            return 'FILE'
        # Real Flask helpers remain active except send_file, which is patched on both surfaces.
        with ExitStack() as stack:
            stack.enter_context(patch.multiple(target,**deps))
            stack.enter_context(patch.object(target.app.logger,'exception',function('logger')))
            stack.enter_context(patch.dict(self.reference,deps|{'send_file':send_file}))
            stack.enter_context(patch.object(target,'send_file',send_file))
            # Migrated modules import Flask send_file directly; patch their local binding too.
            group=next(k for k,v in GROUPS.items() if name in v)
            module_name='invoice_tool.invoices.'+group+'_routes'
            import sys
            if module_name in sys.modules:
                stack.enter_context(patch.object(sys.modules[module_name],'send_file',send_file))
            with target.app.test_request_context('/synthetic',method='POST',data=options.get('form',{}),headers={'X-Requested-With':'XMLHttpRequest'} if options.get('ajax') else {}):
                g.user={'id':3,'role':options.get('role','admin')}
                fn=self.reference[name] if old else getattr(target,name).__wrapped__
                try:
                    result=fn(7)
                    if hasattr(result,'status_code'):
                        outcome=('response',result.status_code,result.headers.get('Location'),result.get_data(),result.mimetype)
                        result.close()
                    else:outcome=('value',result)
                except HTTPException as error:outcome=('http',error.code)
                except Exception as error:outcome=('error',type(error).__name__,str(error))
                flashes=get_flashed_messages(with_categories=True)
        return outcome,flashes,trace

    def compare(self,name,**options):
        expected=self.invoke(name,True,options)
        self.assertEqual(expected,self.invoke(name,False,options),(name,options))
        self.assertNotEqual(expected[0][0],'error',expected)
        return expected

    def routes(self,group):
        source=subprocess.check_output(['git','show',BASE+':app.py'],text=True,encoding='utf-8',cwd=Path(__file__).parent)
        old={n.name:n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef)}
        for name in GROUPS[group]:
            d=old[name].decorator_list[0]
            path=ast.literal_eval(d.args[0]);methods={'POST'} if d.func.attr=='post' else {'GET'}
            rules=[r for r in target.app.url_map.iter_rules() if r.endpoint==name]
            self.assertEqual(len(rules),1);self.assertEqual(rules[0].rule,path)
            self.assertEqual(rules[0].methods-{'HEAD','OPTIONS'},methods)
            self.assertIs(target.app.view_functions[name],getattr(target,name))

class AttachmentReadRoutesTest(RouteContract):
    def test_download_preview_missing_and_access(self):
        self.routes('attachment_read')
        for name in GROUPS['attachment_read']:
            for options in ({},{'missing':True},{'forbidden':True}):
                with self.subTest(name=name,options=options):self.compare(name,**options)

class AttachmentWriteRoutesTest(RouteContract):
    def test_upload_branches(self):
        self.routes('attachment_write')
        for options in ({},{'empty':True},{'invalid':True},{'forbidden':True}):
            with self.subTest(options=options):self.compare('upload_attachment',**options)
    def test_delete_branches_and_order(self):
        for options in ({},{'ajax':True},{'file_missing':True},{'missing':True},{'forbidden':True}):
            with self.subTest(options=options):
                outcome,flashes,trace=self.compare('delete_attachment',**options)
                labels=[t[0] for t in trace]
                if 'remove' in labels:self.assertLess(labels.index('remove'),labels.index('commit'))

class ExportRoutesTest(RouteContract):
    def test_zip_pdf_contract(self):
        self.routes('export')
        for name in GROUPS['export']:
            with self.subTest(name=name):
                outcome,_,trace=self.compare(name)
                self.assertEqual(outcome,('value','FILE'))
                self.assertTrue(any(t[0]=='send_file' and isinstance(t[1],bytes) for t in trace))

class DetailRoutesTest(RouteContract):
    def test_detail_and_owner_draft_redirect(self):
        self.routes('detail')
        for options in ({},{'status':'draft'},{'recognition':True}):
            with self.subTest(options=options):self.compare('invoice_detail',**options)

class SendRoutesTest(RouteContract):
    def test_send_access_status_success_failure(self):
        self.routes('send')
        for options in ({},{'external':True},{'status':'draft'},{'send_error':True},{'forbidden':True}):
            with self.subTest(options=options):self.compare('send_invoice',**options)

class AdminStatusRoutesTest(RouteContract):
    def test_admin_status_guards_and_update(self):
        self.routes('admin_status')
        for options in ({},{'role':'employee'},{'paid_at':'2026-10-17'},{'form':{'status':'draft'}},{'forbidden':True}):
            with self.subTest(options=options):self.compare('admin_update_invoice_status',**options)

class PaymentRoutesTest(RouteContract):
    def test_mark_paid_guards_defaults_explicit_fields(self):
        self.routes('payment')
        for options in ({},{'external':True},{'status':'draft'},{'form':{'paid_at':'2026-01-02','payment_amount':'12.34','payment_note':' note '}},{'forbidden':True}):
            with self.subTest(options=options):self.compare('mark_invoice_paid',**options)
    def test_unmark_paid(self):
        self.compare('unmark_invoice_paid');self.compare('unmark_invoice_paid',forbidden=True)

class VoidRoutesTest(RouteContract):
    def test_void_guards_commit_and_rollback(self):
        self.routes('void')
        for options in ({},{'recognition':True},{'recognition':True,'form':{'reason_code':' test '}},{'recognition':True,'form':{'reason_code':'test'},'void_error':True},{'status':'draft'},{'status':'draft','recognition':True},{'forbidden':True}):
            with self.subTest(options=options):self.compare('void_invoice',**options)

class DeleteRoutesTest(RouteContract):
    def test_delete_guards_commit_rollback_and_cleanup(self):
        self.routes('delete')
        for options in ({},{'no_permission':True},{'recognition':True},{'integrity':True},{'forbidden':True}):
            with self.subTest(options=options):
                _,_,trace=self.compare('delete_invoice',**options)
                labels=[t[0] for t in trace]
                if 'rmtree' in labels:self.assertLess(labels.index('commit'),labels.index('rmtree'))
