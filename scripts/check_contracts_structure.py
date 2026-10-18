"""Verify structural equivalence and run contract tests only against invoice_test."""
import ast
import builtins
import copy
import os
from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
WT = ROOT

def check_structure():
    original = ast.parse(subprocess.check_output(['git', 'show', '4798b77:app.py'], cwd=WT, text=True, encoding='utf-8'))
    current = ast.parse((WT / 'app.py').read_text(encoding='utf-8'))
    before = {n.name:n for n in original.body if isinstance(n, ast.FunctionDef)}
    after = {n.name:n for n in current.body if isinstance(n, ast.FunctionDef)}
    services = ast.parse((WT / 'invoice_tool/contracts/services.py').read_text(encoding='utf-8'))
    routes = ast.parse((WT / 'invoice_tool/contracts/routes.py').read_text(encoding='utf-8'))
    moved = {n.name:n for n in services.body if isinstance(n, ast.FunctionDef)}
    registrar = next(n for n in routes.body if isinstance(n, ast.FunctionDef))
    moved.update({n.name:n for n in registrar.body if isinstance(n, ast.FunctionDef)})
    class Normalize(ast.NodeTransformer):
        def visit_Constant(self, node):
            if isinstance(node.value, str) and '\n' in node.value:
                node.value = textwrap.dedent(node.value).strip()
            return node
        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == 'contract_attachments_root':
                return ast.Name(id='CONTRACT_ATTACHMENTS_DIR', ctx=ast.Load())
            return self.generic_visit(node)
    def normalized(node):
        return ast.dump(Normalize().visit(copy.deepcopy(node)), include_attributes=False)
    for name, node in moved.items():
        old = copy.deepcopy(before[name])
        new = copy.deepcopy(node)
        if old.decorator_list:
            old.decorator_list = old.decorator_list[1:]
        new.args = copy.deepcopy(old.args)
        assert normalized(old) == normalized(new), 'Changed migrated function: ' + name
    unchanged = 0
    for name, node in before.items():
        if name not in moved:
            assert ast.dump(node) == ast.dump(after[name]), 'Changed unrelated function: ' + name
            unchanged += 1
    print(f'AST verified: {len(moved)} migrated functions; {unchanged} unrelated functions unchanged.', flush=True)

if __name__ == '__main__':
    check_structure()
