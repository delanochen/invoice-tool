"""Independent original menu construction, inheritance, and iteration state."""
import ast,copy,importlib,subprocess,unittest
from pathlib import Path
import app
ROOT=Path(app.__file__).parent
BASE='2dbc5664f7b9aeaf56eb24423411dd03187ab4bc'
class MenuDefaultsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree=ast.parse(subprocess.check_output(['git','show',BASE+':app.py'],cwd=ROOT,text=True,encoding='utf-8'))
        cls.assignment=next(n for n in tree.body if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='DEFAULT_MENU_ROLES')
        cls.loop=next(n for n in tree.body if isinstance(n,ast.For) and any(isinstance(v,ast.Name) and v.id=='DEFAULT_MENU_ROLES' for v in ast.walk(n)))
    def test_actual_defaults_match_original_construction_and_loop(self):
        namespace={'MENU_PERMISSION_GROUPS':copy.deepcopy(app.MENU_PERMISSION_GROUPS)}
        exec(compile(ast.Module(body=[copy.deepcopy(self.assignment),copy.deepcopy(self.loop)],type_ignores=[]),'original-menu-defaults','exec'),namespace)
        self.assertEqual(app.DEFAULT_MENU_ROLES,namespace['DEFAULT_MENU_ROLES'])
    def test_constructor_body_and_independent_objects(self):
        path=ROOT/'invoice_tool/authorization/menu_defaults.py'
        if not path.exists():return
        module=importlib.import_module('invoice_tool.authorization.menu_defaults')
        functions={n.name:n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef)}
        self.assertEqual(ast.dump(functions['make_default_menu_roles'].body[0].value),ast.dump(self.assignment.value))
        candidate=copy.deepcopy(functions['apply_manager_inheritance'].body[0]);candidate.body=candidate.body[1:]
        self.assertEqual(ast.dump(candidate),ast.dump(self.loop))
        a=module.make_default_menu_roles(app.MENU_PERMISSION_GROUPS);b=module.make_default_menu_roles(app.MENU_PERMISSION_GROUPS)
        self.assertEqual(a,b);self.assertIsNot(a,b)
        for key in a:self.assertIsNot(a[key],b[key])
    def test_manager_rule_and_original_private_binding(self):
        path=ROOT/'invoice_tool/authorization/menu_defaults.py'
        if not path.exists():return
        module=importlib.import_module('invoice_tool.authorization.menu_defaults')
        for values in ({},{'finance-page':{'finance'},'admin-page':{'admin'}}):
            with self.subTest(empty=not values):
                expected=copy.deepcopy(values);actual=copy.deepcopy(values);namespace={'DEFAULT_MENU_ROLES':expected}
                exec(compile(ast.Module(body=[copy.deepcopy(self.loop)],type_ignores=[]),'original-menu-loop','exec'),namespace)
                scratch={};module.apply_manager_inheritance(actual,_assign_iteration=lambda value:scratch.update(_roles=value))
                self.assertEqual(actual,expected);self.assertEqual(scratch,{key:namespace[key] for key in ('_roles',) if key in namespace})
