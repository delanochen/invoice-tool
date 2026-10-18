"""Independent baseline behavior for DELETE authorization and geocoder rate limiting."""
import ast,copy,inspect,json,subprocess,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError,URLError
from flask import g,make_response,jsonify,abort
from werkzeug.exceptions import HTTPException
import app as target
BASE='ce60c0c267ef4570727e090ca39ecaf98598372c'

class Contract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=subprocess.check_output(['git','show',BASE+':app.py'],cwd=Path(target.__file__).parent,text=True,encoding='utf-8')
        cls.nodes={n.name:n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef)}
        cls.reference=dict(vars(target));nodes=[]
        for name in ('ai_daily_report_manifest_cancel','fetch_geocoder_json'):
            node=copy.deepcopy(cls.nodes[name]);node.decorator_list=[];nodes.append(node)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'independent-tail-baseline','exec'),cls.reference)

class ManifestDeleteTest(Contract):
    def test_original_registration_and_permission_error_commit_branches(self):
        name='ai_daily_report_manifest_cancel';node=self.nodes[name]
        rules=[d for d in node.decorator_list if isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and d.func.attr=='delete']
        self.assertEqual(len(rules),1);url=ast.literal_eval(rules[0].args[0])
        actual=[r for r in target.app.url_map.iter_rules(name) if r.rule==url]
        self.assertEqual(len(actual),1);self.assertEqual(actual[0].methods-{'OPTIONS','HEAD'},{'DELETE'})
        self.assertIs(target.app.view_functions[name],getattr(target,name))
        class SyntheticManifestError(Exception):pass
        def invoke(old,mode):
            trace=[]
            def csrf():
                trace.append('csrf')
                if mode=='csrf':abort(400)
            def draft(value):trace.append(('draft',value));return None if mode=='missing' else {'id':7}
            def permission(user,row):trace.append(('permission',user['id'],row['id']));return mode!='manifest_denied'
            def cancel(a,b):
                trace.append(('cancel',a,b))
                if mode=='error':raise SyntheticManifestError('synthetic')
                return {'id':8,'status':'cancelled'}
            def error(exc,default_status):trace.append(('error',str(exc),default_status));return jsonify({'ok':False}),default_status
            deps=dict(is_internal_user=lambda:mode!='denied',require_ai_daily_report_csrf=csrf,_ai_daily_report_service=lambda:SimpleNamespace(get_draft=draft),can_prepare_attachment_manifest=permission,_attachment_manifest_service=lambda:SimpleNamespace(cancel=cancel),ManifestError=SyntheticManifestError,_manifest_error_response=error,db=lambda:SimpleNamespace(commit=lambda:trace.append('commit')))
            with patch.multiple(target,**deps),patch.dict(self.reference,deps),target.app.test_request_context('/synthetic',method='DELETE'):
                g.user={'id':1}
                fn=self.reference[name] if old else inspect.unwrap(getattr(target,name))
                try:
                    response=make_response(fn(7,8));outcome=(response.status_code,response.get_json());response.close()
                except HTTPException as exc:outcome=(exc.code,None)
            return outcome,trace
        for mode,code in [('denied',403),('csrf',400),('missing',404),('manifest_denied',403),('error',400),('ok',200)]:
            with self.subTest(mode=mode):
                expected=invoke(True,mode);actual=invoke(False,mode);self.assertEqual(expected,actual);self.assertEqual(actual[0][0],code)
                self.assertEqual(actual[1].count('commit'),int(mode=='ok'))

class GeocoderStateTest(Contract):
    def test_shared_state_lock_wait_headers_errors_and_finally(self):
        class TemporaryError(Exception):pass
        def invoke(old,mode):
            trace=[];ticks=iter((10.0,11.0))
            def monotonic():value=next(ticks);trace.append(('clock',value));return value
            class Lock:
                def __enter__(self):trace.append('lock-enter')
                def __exit__(self,kind,exc,tb):trace.append(('lock-exit',kind.__name__ if kind else None))
            class Response:
                def __enter__(self):trace.append('response-enter');return self
                def __exit__(self,kind,exc,tb):trace.append(('response-exit',kind.__name__ if kind else None))
                def read(self):trace.append('read');return b'invalid' if mode=='json' else b'{"lat":1}'
            def request(url,headers):trace.append(('request',url,headers));return 'synthetic-request'
            def urlopen(request,timeout):
                trace.append(('urlopen',request,timeout))
                if mode in {'429','503','400'}:raise HTTPError('https://example.invalid',int(mode),'synthetic',{},None)
                if mode=='url':raise URLError('synthetic')
                if mode=='timeout':raise TimeoutError('synthetic')
                if mode=='os':raise OSError('synthetic')
                return Response()
            deps=dict(_last_geocode_request_at=9.5,_geocode_lock=Lock(),time=SimpleNamespace(monotonic=monotonic,sleep=lambda value:trace.append(('sleep',value))),Request=request,urlopen=urlopen,NOMINATIM_USER_AGENT='synthetic-agent',TemporaryGeocodingError=TemporaryError)
            with patch.multiple(target,**deps),patch.dict(self.reference,deps):
                fn=self.reference['fetch_geocoder_json'] if old else target.fetch_geocoder_json
                try:outcome=('ok',fn('https://example.invalid/synthetic'))
                except Exception as error:outcome=('error',type(error).__name__,str(error))
                state=self.reference['_last_geocode_request_at'] if old else target._last_geocode_request_at
            return outcome,state,trace
        for mode in ('ok','429','503','400','url','timeout','os','json'):
            with self.subTest(mode=mode):
                expected=invoke(True,mode);actual=invoke(False,mode);self.assertEqual(expected,actual)
                self.assertEqual(actual[1],11.0);self.assertEqual(actual[2][0],'lock-enter')
                self.assertAlmostEqual(actual[2][2][1],0.55)
                expected_kind='TemporaryError' if mode in {'429','503','url','timeout','os'} else 'HTTPError' if mode=='400' else 'JSONDecodeError' if mode=='json' else None
                self.assertEqual(actual[0][1] if expected_kind else actual[0][0],expected_kind or 'ok')
