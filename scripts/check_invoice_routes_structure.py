"""Exact route-body, decorator, URL/method and unrelated-function verification."""
import ast, copy, json, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
m=json.loads((ROOT/'docs/invoice-route-extraction-series.json').read_text(encoding='utf-8'))
oldtree=ast.parse(subprocess.check_output(['git','show',m['base']+':app.py'],cwd=ROOT,text=True,encoding='utf-8'))
tree=ast.parse((ROOT/'app.py').read_text(encoding='utf-8'))
old={n.name:n for n in oldtree.body if isinstance(n,ast.FunctionDef)}
new={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
class Restore(ast.NodeTransformer):
    def visit_Call(self,n):
        n=self.generic_visit(n)
        if isinstance(n.func,ast.Name) and n.func.id.startswith('resolve_') and not n.args and not n.keywords:
            return ast.Name(id=n.func.id[8:],ctx=ast.Load())
        return n
moved=set()
for p in m['phases']:
    t=ast.parse((ROOT/p['module']).read_text(encoding='utf-8'))
    assert not any(isinstance(n,ast.Import) and any(a.name=='app' for a in n.names) for n in ast.walk(t))
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in {'globals','eval','exec'} for n in ast.walk(t))
    reg=next(n for n in t.body if isinstance(n,ast.FunctionDef))
    nested={n.name:n for n in reg.body if isinstance(n,ast.FunctionDef)}
    assert set(nested)==set(p['functions'])
    rules={n.value.keywords[0].value.value:n.value for n in reg.body if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and n.value.func.attr=='add_url_rule'}
    for name,n in nested.items():
        expected=copy.deepcopy(old[name]); d=expected.decorator_list.pop(0)
        assert ast.dump(Restore().visit(copy.deepcopy(n)))==ast.dump(expected),name+' body/decorators changed'
        methods=['POST'] if d.func.attr=='post' else next((ast.literal_eval(k.value) for k in d.keywords if k.arg=='methods'),['GET'])
        rule=rules[name]
        assert ast.literal_eval(rule.args[0])==ast.literal_eval(d.args[0])
        assert next(ast.literal_eval(k.value) for k in rule.keywords if k.arg=='methods')==methods
        moved.add(name)
for name,n in old.items():
    if name not in moved: assert ast.dump(n)==ast.dump(new[name]),name+' unrelated change'
assert set(old)-set(new)==moved
assert not set(new)-set(old)
print(f'Exact AST: {len(moved)} routes and URLs/methods preserved; {len(new)} other functions unchanged.')
