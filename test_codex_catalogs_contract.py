"""Literal equivalence and independent mutable objects for extracted catalogs."""
import ast,copy,importlib,subprocess,unittest
from pathlib import Path
import app
ROOT=Path(app.__file__).resolve().parent
BASE='03a32935e1d804f2b1f47541b29d813dd8a67b30'
GROUPS=[('photo_catalog', 'invoice_tool/service_reports/photo_catalog.py', ['REPORT_PHOTO_FOLDERS']), ('region_catalog', 'invoice_tool/geocoding/region_catalog.py', ['US_STATE_CODES']), ('status_catalog', 'invoice_tool/presentation/status_catalog.py', ['STATUS_LABELS', 'EXPENSE_STATUS_LABELS', 'CUSTOMER_REIMBURSEMENT_STATUS_LABELS', 'CONTRACT_STATUS_LABELS', 'SERVICE_ORDER_STATIC_MAP_STATUS_COLORS']), ('action_catalog', 'invoice_tool/authorization/action_catalog.py', ['ROLE_OPTIONS', 'ACTION_LABELS', 'MANAGER_FINANCE_INHERITANCE_EXCEPTIONS', 'MENU_PERMISSION_GROUPS', 'ROLE_ACTION_PERMISSION_GROUPS', 'DEFAULT_ACTION_ROLES']), ('language_catalog', 'invoice_tool/user_preferences/language_catalog.py', ['SUPPORTED_LANGUAGES']), ('field_catalog', 'invoice_tool/customer_reimbursements/field_catalog.py', ['CUSTOMER_REIMBURSEMENT_PROJECTS', 'CUSTOMER_REIMBURSEMENT_RATE_FIELDS', 'SETTLEMENT_EXPENSE_FIELD_LABELS']), ('owner_catalog', 'invoice_tool/contacts/owner_catalog.py', ['DEFAULT_OWNER_NAMES', 'DEFAULT_SITE_OWNER_NAMES'])]
class BusinessCatalogContractTest(unittest.TestCase):
    def assert_independent(self,left,right):
        if isinstance(left,(dict,list,set)):self.assertIsNot(left,right)
        if isinstance(left,dict):
            for key in left:self.assert_independent(left[key],right[key])
        elif isinstance(left,(list,tuple)):
            for a,b in zip(left,right):self.assert_independent(a,b)
    def test_original_values_literal_structure_and_independent_app_values(self):
        root=Path(app.__file__).parent
        source=subprocess.check_output(['git','show',BASE+':app.py'],cwd=root,text=True,encoding='utf-8')
        nodes={n.targets[0].id:n for n in ast.parse(source).body if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name)}
        for _,path,names in GROUPS:
            module=importlib.import_module(path[:-3].replace('/','.')) if (root/path).exists() else None
            functions={n.name:n for n in ast.parse((root/path).read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef)} if module else {}
            for name in names:
                with self.subTest(catalog=name):
                    expected=eval(compile(ast.Expression(body=nodes[name].value),'independent-catalog-expression','eval'),dict(vars(app)))
                    final_expected=expected
                    if name=='DEFAULT_ACTION_ROLES':
                        loop=next(n for n in ast.parse(source).body if isinstance(n,ast.For) and any(isinstance(v,ast.Name) and v.id==name for v in ast.walk(n)))
                        namespace=dict(vars(app));namespace[name]=copy.deepcopy(expected)
                        exec(compile(ast.Module(body=[copy.deepcopy(loop)],type_ignores=[]),'original-permission-inheritance','exec'),namespace)
                        final_expected=namespace[name]
                    self.assertEqual(getattr(app,name),final_expected);self.assertIs(type(getattr(app,name)),type(final_expected))
                    if module:
                        fn=functions['make_'+name.lower()]
                        self.assertEqual(len(fn.body),1);self.assertIsInstance(fn.body[0],ast.Return)
                        self.assertEqual(ast.dump(fn.body[0].value),ast.dump(nodes[name].value))
                        deps={arg.arg:getattr(app,arg.arg) for arg in fn.args.kwonlyargs}
                        left=getattr(module,fn.name)(**deps);right=getattr(module,fn.name)(**deps)
                        self.assertEqual(left,expected);self.assertEqual(right,expected);self.assert_independent(left,right)
    def test_permission_inheritance_and_private_iteration_bindings(self):
        path=ROOT/'invoice_tool/authorization/action_catalog.py'
        if not path.exists():return
        module=importlib.import_module('invoice_tool.authorization.action_catalog')
        source=subprocess.check_output(['git','show',BASE+':app.py'],cwd=ROOT,text=True,encoding='utf-8')
        loop=next(n for n in ast.parse(source).body if isinstance(n,ast.For) and any(isinstance(v,ast.Name) and v.id=='DEFAULT_ACTION_ROLES' for v in ast.walk(n)))
        for roles in ({},{('a','edit'):{'finance'},('b','view'):{'finance'},('c','view'):{'admin'}}):
            with self.subTest(empty=not roles):
                exceptions={('b','view')};expected=copy.deepcopy(roles);actual=copy.deepcopy(roles)
                namespace={'DEFAULT_ACTION_ROLES':expected,'MANAGER_FINANCE_INHERITANCE_EXCEPTIONS':exceptions}
                exec(compile(ast.Module(body=[copy.deepcopy(loop)],type_ignores=[]),'original-inheritance','exec'),namespace)
                scratch={}
                module.apply_manager_finance_inheritance(actual,exceptions,_assign_iteration=lambda key,value:scratch.update(_permission_key=key,_roles=value))
                self.assertEqual(actual,expected)
                self.assertEqual(scratch,{key:namespace[key] for key in ('_permission_key','_roles') if key in namespace})
