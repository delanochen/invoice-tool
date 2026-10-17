"""Verify expense cancellation extraction against preceding commit df2f6e5."""
import ast
import inspect
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
    original = ast.parse(subprocess.check_output(['git', 'show', 'df2f6e5:app.py'], cwd=WT, text=True, encoding='utf-8'))
    current = ast.parse((WT / 'app.py').read_text(encoding='utf-8'))
    before = {n.name:n for n in original.body if isinstance(n, ast.FunctionDef)}
    after = {n.name:n for n in current.body if isinstance(n, ast.FunctionDef)}
    routes = ast.parse((WT / 'invoice_tool/employee_expenses/expense_cancel_routes.py').read_text(encoding='utf-8'))
    registrar = next(n for n in routes.body if isinstance(n, ast.FunctionDef))
    moved = {n.name:n for n in registrar.body if isinstance(n, ast.FunctionDef)}
    class Normalize(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body[0].value.value = inspect.cleandoc(node.body[0].value.value)
            return self.generic_visit(node)
        def visit_Constant(self, node):
            if isinstance(node.value, str) and '\n' in node.value:
                node.value = textwrap.dedent(node.value).strip()
            return node
        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == 'cancel_expense_payment_order' and len(node.args) == 1:
                node.args.insert(0, ast.Call(func=ast.Name(id='globals',ctx=ast.Load()),args=[],keywords=[]))
            if isinstance(node.func, ast.Name) and node.func.id.startswith('resolve_'):
                return ast.Name(id=node.func.id[len('resolve_'):], ctx=ast.Load())
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
