"""Route registration and independent old/new branch contracts; no real business I/O."""
import ast
import copy
from datetime import datetime as RealDateTime
import inspect
from io import BytesIO
from pathlib import Path
import subprocess
import symtable
import types
import unittest
from unittest.mock import patch
from contextlib import ExitStack
from flask import Response,g,get_flashed_messages,make_response,session
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import HTTPException,Forbidden
import app as target

GROUPS=[('mobile_install',['mobile_app_install','mobile_app_manifest']),('mobile_package',['mobile_app_ipa']),('language_session',['logout','switch_language']),('messages_list',['messages']),('messages_mark',['mark_messages']),('messages_read',['mark_all_messages_read']),('message_detail',['message_detail']),('pwa_manifest',['app_manifest']),('service_worker',['app_service_worker']),('workspace_pages',['workspace','report_center']),('dashboard_page',['dashboard']),('company_page',['company_info']),('company_attachment_read',['download_company_attachment','preview_company_attachment']),('company_attachment_delete',['delete_company_attachment','legacy_company_settings']),('invoice_list',['invoices']),('invoice_query_page',['invoice_query']),('order_query_page',['service_order_query']),('buyer_query_page',['buyer_query']),('report_query_page',['service_report_query'])]

class Data(dict):
    def __getattr__(self,key):
        if key in {'model_dump','dict','to_dict'}:return lambda **kw:dict(self)
        return self.get(key)
    def __setattr__(self,key,value):self[key]=value
    def __getitem__(self,key):
        if isinstance(key,int):return list(self.values())[key]
        return super().__getitem__(key)

class FixedDateTime(RealDateTime):
    @classmethod
    def now(cls,tz=None):return cls(2026,10,17,12,0,0,tzinfo=tz)

def canonical(value):
    if isinstance(value,(str,int,float,bool,type(None),bytes)):return value
    if isinstance(value,BytesIO):return ('bytes',value.getvalue())
    if isinstance(value,dict):return tuple((str(k),canonical(v)) for k,v in value.items())
    if isinstance(value,(list,tuple)):return tuple(canonical(x) for x in value)
    if isinstance(value,set):return tuple(sorted((canonical(x) for x in value),key=repr))
    if isinstance(value,Path):return str(value)
    if callable(value):return ('callable',getattr(value,'__name__',type(value).__name__))
    if isinstance(value,FakeService):return ('service',value.label)
    return (type(value).__name__,str(value))

class FakeService:
    def __init__(self,label,trace,mode,conflict_methods=()):self.label=label;self.trace=trace;self.mode=mode;self.conflict_methods=conflict_methods
    def __getattr__(self,key):
        def call(*args,**kwargs):
            self.trace.append((self.label+'.'+key,canonical(args),canonical(kwargs)))
            if key=='get_draft':
                if self.mode=='missing':return None
                return Data(id=7,draft_id=7,status='saved' if self.mode=='readonly' else 'draft',created_by=1,updated_by=1,draft_version=1,service_order_id=7,report_id=None,report_number=None,report_date='2026-10-17',draft_data='{}',data_json='{}',last_action_id=None)
            if key.startswith('list') or key.startswith('find'):return []
            if key.startswith('is_') or key.startswith('can_'):return True
            if key=='get_active_draft':return Data(id=7) if self.mode=='reuse' else None
            if key=='create_draft':return Data(id=7)
            if key=='parse_draft_data':return Data(service_order_id=7,report_date='2026-10-17',workers=[],work_items=[],photo_candidates=[],construction_photo_paths=[],service_photo_paths=[],safety_photo_path=None,arrival_time=None,departure_time=None,verification_required=False,verification_fields=[],travel_verification_required=False,travel_verification_fields=[])
            if key in {'build_preview','build_review_preview'}:return {'synthetic':True}
            if self.mode=='conflict' and key=='reopen_draft':raise target.DraftStateError('synthetic state conflict')
            if self.mode=='conflict' and key in self.conflict_methods:
                try:error=target.DraftVersionConflict('synthetic conflict')
                except TypeError:error=target.DraftVersionConflict(1,2)
                raise error
            return Data(ok=True,status='success',draft_version=1,draft_id=7,selected_count=0,photos=[],steps=[],photo_candidates=[],verification_required=False,error=None)
        call.__name__=self.label+'.'+key
        return call

class RouteContract(unittest.TestCase):
    BASELINE='7bba31c56cfa564040dd91b29fe86b935e63e465'
    FUNCTIONS=()
    CASE_KIND='query'
    @classmethod
    def setUpClass(cls):
        cls.source=subprocess.check_output(['git','show',cls.BASELINE+':app.py'],cwd=Path(target.__file__).parent,text=True,encoding='utf-8')
        cls.nodes={n.name:n for n in ast.parse(cls.source).body if isinstance(n,ast.FunctionDef)}
        cls.tables={t.get_name():t for t in symtable.symtable(cls.source,'app.py','exec').get_children()}
        cls.reference=dict(vars(target));nodes=[]
        for name in cls.FUNCTIONS:
            n=copy.deepcopy(cls.nodes[name]);n.decorator_list=[];nodes.append(n)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'independent-route-baseline','exec'),cls.reference)
    def global_names(self,name):
        def visit(table):
            found={s.get_name() for s in table.get_symbols() if s.is_global() and s.is_referenced()}
            for child in table.get_children():found.update(visit(child))
            return found
        return visit(self.tables[name])
    def rules(self,name):
        expected=[]
        for d in self.nodes[name].decorator_list:
            if not (isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and isinstance(d.func.value,ast.Name) and d.func.value.id=='app' and d.func.attr in {'get','post','route'}):continue
            options={k.arg:ast.literal_eval(k.value) for k in d.keywords}
            methods=['POST'] if d.func.attr=='post' else ['GET'] if d.func.attr=='get' else options.get('methods',['GET'])
            expected.append((ast.literal_eval(d.args[0]),options.get('endpoint',name),set(methods)))
        return expected
    def invoke(self,name,old,mode,method):
        trace=[]
        conflict_methods=set()
        for node in ast.walk(self.nodes[name]):
            if isinstance(node,ast.Try) and any(handler.type and any(isinstance(n,ast.Name) and n.id=='DraftVersionConflict' for n in ast.walk(handler.type)) for handler in node.handlers):
                conflict_methods.update(call.func.attr for statement in node.body for call in ast.walk(statement) if isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and isinstance(call.func.value,ast.Name) and call.func.value.id=='svc')
        def record(key,args=(),kwargs=None):trace.append((key,canonical(args),canonical(kwargs or {})))
        def execute(sql,params=()):
            record('sql',(sql,params))
            def one():
                if any(token in sql.lower() for token in ('count(','sum(','coalesce(')):return Data(count=0,total=0)
                if mode=='missing':return None
                return Data(id=7,user_id=1,created_by=1,client_id=2,service_order_id=7,name='Synthetic',email='synthetic@example.invalid',title='Synthetic message',body='Synthetic body',link='/messages',is_read=0,stored_filename='sample.txt',original_filename='sample.txt',content_type='text/plain',status='completed',invoice_number='INV-SYNTHETIC',is_active=1)
            return Data(fetchone=one,fetchall=lambda:[],lastrowid=7,rowcount=1)
        connection=Data(execute=execute,commit=lambda:record('commit'),rollback=lambda:record('rollback'))
        def service(label):return FakeService(label,trace,mode,conflict_methods)
        def collaborator(key):
            def call(*args,**kwargs):
                safe_args=args
                if key=='get_setting' and args and any(word in str(args[0]).lower() for word in ('key','password','secret','token')):safe_args=(args[0],'<redacted-default>')
                record(key,safe_args,kwargs)
                denied=mode=='forbidden'
                if key.startswith('require_'):
                    if denied:raise Forbidden()
                    if key=='require_ai_daily_report_csrf':return None
                    return Data(id=7,service_order_id=7,client_id=2,created_by=1,order_number='SO-SYNTHETIC',client_order_number='SYNTHETIC',site_address='Synthetic site',start_date='2026-10-17',status='draft')
                if key in {'is_external_user','is_external_employee','is_external_manager'}:return False
                if key=='can_edit_ai_daily_report_draft':return mode not in {'forbidden','readonly'}
                if key.startswith(('can_','has_','is_internal')):return not denied
                if key=='normalized_role':return 'employee' if denied else 'admin'
                if key=='db':return connection
                if key in {'_ai_daily_report_service','_make_photo_classification_service','_attachment_manifest_service'}:return service(key)
                if key.endswith('Service'):return service(key)
                if key=='_auto_prepare_draft_media':return []
                if key=='_build_review_center_preview':return {'synthetic':True}
                if key=='ai_daily_report_draft_list_filters':return ([],[])
                if key=='_ai_daily_report_business_date':return '2026-10-17'
                if key in {'vision_settings','deepseek_assistant_settings'}:return {'enabled':True,'api_key':'synthetic','model':'synthetic','photo_classification_enabled':True}
                if key=='ai_daily_report_csrf_token':return 'synthetic-csrf'
                if key=='current_language':return 'en'
                if key=='get_timezone_name':return 'UTC'
                if key=='client_filter_clause':return ('invoices.client_id = ?',[2])
                if key=='service_order_access_filters':return ([],[])
                if key=='report_location_filters':return ('','',[],[],[],[])
                if key in {'dashboard_project_options','report_project_options'}:return [{'id':7,'name':'Synthetic','color':'#123456'}]
                if key in {'monthly_chart','monthly_paid_chart','monthly_project_chart','buyer_map_rows','get_company_attachments','uploaded_attachments_from_request','country_rows'}:return []
                if key=='_message_ids_for_user':return [7]
                if key=='get_metrics':return {k:0 for k in ('invoice_count','completed','paid','unpaid','total','pending_reimbursement','reimbursed_expenses','pending_expenses','unpaid_payroll')}
                if key=='invoice_totals':return {'subtotal':10,'tax':1,'total':11}
                if key=='get_company_profile':return {'name':'Synthetic company','email':'synthetic@example.invalid'}
                if key=='get_payment_instructions':return {'bank_name':'Synthetic bank'}
                if key=='get_invoice_terms':return 'Synthetic terms'
                if key=='get_setting':return 'synthetic'
                if key.endswith('_path') or key.endswith('_dir'):return '/synthetic/file'
                if key=='render_template':return 'SYNTHETIC TEMPLATE'
                if key=='safe_attachment_response':return Response('SYNTHETIC FILE',mimetype='text/plain')
                if key=='send_file':
                    response=Response('SYNTHETIC FILE',mimetype=kwargs.get('mimetype','application/octet-stream'))
                    if kwargs.get('as_attachment'):response.headers['Content-Disposition']='attachment; filename='+kwargs.get('download_name','synthetic.bin')
                    return response
                return None
            call.__name__=key
            return call
        names=self.global_names(name)
        keep={'app','g','request','session','abort','jsonify','redirect','url_for','flash','make_response','urlencode','urlsplit','urlunsplit','parse_qsl','secure_filename'}
        deps={}
        for key in names:
            if key in keep or key not in vars(target):continue
            value=vars(target)[key]
            if isinstance(value,types.FunctionType):deps[key]=collaborator(key)
            elif inspect.isclass(value) and key.endswith('Service'):deps[key]=collaborator(key)
        deps['datetime']=FixedDateTime
        if 'os' in names:
            import os
            deps['os']=types.SimpleNamespace(path=types.SimpleNamespace(isfile=lambda path:mode!='missing',getsize=lambda path:128,exists=lambda path:mode!='missing',join=os.path.join,basename=os.path.basename),remove=collaborator('os.remove'))
        for key,value in {'IOS_IPA_PATH':'/synthetic/package.ipa','COMPANY_ATTACHMENT_DIR':'/synthetic/company','SHARED_PHOTOS_DIR':'/synthetic/photos'}.items():
            if key in names:deps[key]=value
        user={'id':2 if mode=='forbidden' else 1,'name':'Synthetic user','email':'synthetic@example.invalid','role':'employee' if mode=='forbidden' else 'admin','client_id':2}
        form=MultiDict([('action','read'),('message_id','7'),('message_ids','7'),('language','en'),('next','/messages?lang=zh-CN&x=1'),('app_timezone','UTC')])
        json_data={'service_order_id':7,'draft_version':1,'force':True,'photo_id':'synthetic-photo','classification':'safety','time':'08:00','photo_path':'synthetic.jpg','relative_path':'synthetic.jpg','photo_type':'safety','role':'safety','capture_time':'2026-10-17T08:00:00','business_date':'2026-10-17'}
        flask_app=target.app
        with ExitStack() as stack:
            stack.enter_context(patch.multiple(target,**deps))
            stack.enter_context(patch.dict(self.reference,deps))
            stack.enter_context(patch.object(flask_app,'send_static_file',lambda filename:Response('SYNTHETIC WORKER',mimetype='application/javascript')))
            stack.enter_context(patch.object(flask_app.logger,'exception',collaborator('logger.exception')))
            options={'json':json_data} if self.CASE_KIND=='ai' and method=='POST' else {'data':form} if method=='POST' else {}
            with flask_app.test_request_context('/synthetic',method=method,**options):
                g.user=user;session['language']='zh-CN';session['user_id']=user['id']
                fn=self.reference[name] if old else inspect.unwrap(getattr(target,name))
                args=[]
                for parameter in inspect.signature(fn).parameters.values():
                    if parameter.default is inspect.Parameter.empty:args.append('synthetic-photo' if parameter.name=='photo_id' else 7)
                try:
                    response=make_response(fn(*args))
                    outcome=('response',response.status_code,response.headers.get('Location'),response.mimetype,response.get_data(),response.headers.get('Cache-Control'),response.headers.get('Content-Disposition'),response.headers.get('Service-Worker-Allowed'))
                    response.close()
                except HTTPException as error:outcome=('http',error.code)
                except Exception as error:outcome=('unexpected',type(error).__name__,str(error))
                flashes=get_flashed_messages(with_categories=True);state=dict(session)
        return outcome,flashes,state,trace
    def check_contract(self):
        for name in self.FUNCTIONS:
            expected=self.rules(name)
            actual=[(r.rule,r.endpoint,r.methods-{'OPTIONS','HEAD'}) for r in target.app.url_map.iter_rules() if r.endpoint in {x[1] for x in expected}]
            self.assertEqual(actual,expected,name)
            self.assertIs(target.app.view_functions[expected[0][1]],getattr(target,name))
            self.assertEqual(inspect.signature(self.reference[name]),inspect.signature(inspect.unwrap(getattr(target,name))))
            methods=sorted(set().union(*(x[2] for x in expected)))
            modes=('normal','forbidden','missing','readonly','reuse','conflict') if self.CASE_KIND=='ai' else ('normal','forbidden','missing')
            for method in methods:
                for mode in modes:
                    with self.subTest(name=name,method=method,mode=mode):
                        old=self.invoke(name,True,mode,method);new=self.invoke(name,False,mode,method)
                        self.assertEqual(old,new)
                        self.assertNotEqual(old[0][0],'unexpected',old)
                        if mode=='normal':self.assertTrue(old[0][0]=='response' and old[0][1] in {200,201,202,302,303},old)

for _key,_names in GROUPS:
    globals()[''.join(s.title() for s in _key.split('_'))+'Test']=type(''.join(s.title() for s in _key.split('_'))+'Test',(RouteContract,),{'__module__':__name__,'FUNCTIONS':tuple(_names),'test_registration_and_branches':RouteContract.check_contract})
