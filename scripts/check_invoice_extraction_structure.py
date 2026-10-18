"""Cumulative exact helper-body comparison for invoice extraction series."""
import ast
import copy
import json
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'docs/invoice-extraction-series.json').read_text(encoding='utf-8'))
before=ast.parse(subprocess.check_output(['git','show',manifest['base']+':app.py'],cwd=ROOT,text=True,encoding='utf-8'))
after=ast.parse((ROOT/'app.py').read_text(encoding='utf-8'))
old={n.name:n for n in before.body if isinstance(n,ast.FunctionDef)}
current={n.name:n for n in after.body if isinstance(n,ast.FunctionDef)}
moved={}
for phase in manifest['phases']:
    tree=ast.parse((ROOT/phase['module']).read_text(encoding='utf-8'))
    functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
    assert set(functions)==set(phase['functions'])
    for name,node in functions.items():
        assert ast.dump(old[name].args)==ast.dump(current[name].args),name+' signature changed'
        node=copy.deepcopy(node)
        node.args=copy.deepcopy(old[name].args)
        assert ast.dump(node)==ast.dump(old[name]),name+' body changed'
        moved[name]=node
for name,node in old.items():
    if name not in moved:
        assert ast.dump(node)==ast.dump(current[name]),name+' unrelated change'
assert set(current)==set(old)
print(f'Exact AST: {len(moved)} moved helpers, {len(old)-len(moved)} other functions unchanged.')
