"""Final boundary: app owns assembly and compatibility, not business implementations."""
import ast,subprocess,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent
RETAINED={
 'db':'request-scoped connection construction and cache',
 'now':'application clock adapter',
 'close_db':'request connection teardown',
 '_new_formal_save_service':'service composition and live application callbacks',
 '_ai_daily_report_service':'request-scoped service construction',
 '_make_photo_classification_service':'provider and callback composition',
 '_attachment_manifest_service':'attachment service construction',
}
class AppAssemblyBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.tree=ast.parse((ROOT/'app.py').read_text(encoding='utf-8'))
    def test_remaining_functions_are_assembly_or_thin_forwarders(self):
        for node in self.tree.body:
            if not isinstance(node,ast.FunctionDef) or node.name in RETAINED:continue
            body=[n for n in node.body if not(isinstance(n,ast.Expr) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str))]
            with self.subTest(function=node.name):
                self.assertEqual(len(body),1)
                self.assertIsInstance(body[0],ast.Return)
                self.assertIsInstance(body[0].value,ast.Call)
    def test_business_routes_are_registered_in_domains(self):
        for node in self.tree.body:
            if not isinstance(node,ast.FunctionDef):continue
            for decorator in node.decorator_list:
                with self.subTest(function=node.name):
                    if isinstance(decorator,ast.Call) and isinstance(decorator.func,ast.Attribute) and isinstance(decorator.func.value,ast.Name) and decorator.func.value.id=='app':
                        self.assertNotIn(decorator.func.attr,{'route','get','post','put','patch','delete'})
    def test_no_module_level_business_loops_remain(self):
        self.assertFalse(any(isinstance(n,(ast.For,ast.While)) for n in self.tree.body))
    def test_legacy_function_signatures_survive(self):
        original=ast.parse(subprocess.check_output(['git','show','d14b2af:app.py'],cwd=ROOT,text=True,encoding='utf-8'))
        old={n.name:n for n in original.body if isinstance(n,ast.FunctionDef)}
        for node in self.tree.body:
            if isinstance(node,ast.FunctionDef) and node.name in old:
                with self.subTest(function=node.name):self.assertEqual(ast.dump(node.args),ast.dump(old[node.name].args))
    def test_new_domain_modules_do_not_import_root_app(self):
        paths=subprocess.check_output(['git','diff','--name-only','--diff-filter=A','d14b2af','HEAD','--','invoice_tool'],cwd=ROOT,text=True,encoding='utf-8').splitlines()
        for path in paths:
            if not path.endswith('.py'):continue
            tree=ast.parse((ROOT/path).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node,(ast.ImportFrom,ast.Import)):continue
                with self.subTest(module=path):
                    if isinstance(node,ast.ImportFrom):self.assertNotEqual(node.module,'app')
                    elif isinstance(node,ast.Import):self.assertNotIn('app',{n.name for n in node.names})
    def test_retained_composition_is_unchanged(self):
        original=ast.parse(subprocess.check_output(['git','show','d14b2af:app.py'],cwd=ROOT,text=True,encoding='utf-8'))
        old={n.name:n for n in original.body if isinstance(n,ast.FunctionDef)}
        for node in self.tree.body:
            if isinstance(node,ast.FunctionDef) and node.name in RETAINED and node.name in old:
                with self.subTest(adapter=node.name):self.assertEqual(ast.dump(node),ast.dump(old[node.name]))
