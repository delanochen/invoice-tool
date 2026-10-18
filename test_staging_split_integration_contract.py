"""Parity between staging business changes and their extracted implementations."""
import ast,copy,subprocess,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BASE='b8d7ccacdad0d853aaf6c73473628b4ba4786bef'
SCOPES={
 'invoice_tool/customer_reimbursements/source_merge.py':['merge_approved_expenses_into_customer_reimbursement'],
 'invoice_tool/customer_reimbursements/workspace.py':['customer_reimbursement_gate_error','customer_reimbursement_workspace_data'],
 'invoice_tool/customer_reimbursements/invoice_items.py':['latest_customer_reimbursement'],
 'invoice_tool/customer_reimbursements/updates.py':['create_customer_reimbursement','update_customer_reimbursement_totals'],
 'invoice_tool/customer_reimbursements/documents.py':['archive_customer_reimbursement_pdf'],
 'invoice_tool/customer_reimbursements/form_routes.py':['customer_reimbursement_form'],
 'invoice_tool/customer_reimbursements/action_routes.py':['sync_customer_reimbursement_sources','ignore_customer_reimbursement_source','reset_customer_reimbursement'],
 'invoice_tool/query_pages/settlement_routes.py':['customer_reimbursement_query'],
}
class Restore(ast.NodeTransformer):
 def visit_Call(self,node):
  node=self.generic_visit(node)
  if isinstance(node.func,ast.Name) and not node.args and not node.keywords:
   if node.func.id.startswith('_cb_resolve_'):return ast.Name(id=node.func.id[len('_cb_resolve_'):],ctx=ast.Load())
   if node.func.id=='_runtime_api':return ast.Call(func=ast.Name(id='globals',ctx=ast.Load()),args=[],keywords=[])
  if isinstance(node.func,ast.Name) and node.func.id=='_merge_root_api':node.func.id='globals'
  return node
class StagingMergeContract(unittest.TestCase):
 def test_staging_bodies_are_preserved(self):
  source=subprocess.check_output(['git','show',BASE+':app.py'],cwd=ROOT,text=True,encoding='utf-8')
  originals={n.name:n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef)}
  for path,names in SCOPES.items():
   nodes={n.name:n for n in ast.walk(ast.parse((ROOT/path).read_text(encoding='utf-8'))) if isinstance(n,ast.FunctionDef)}
   for name in names:
    with self.subTest(function=name):
     actual=Restore().visit(copy.deepcopy(nodes[name]));expected=copy.deepcopy(originals[name])
     expected.decorator_list=[d for d in expected.decorator_list if not(isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and isinstance(d.func.value,ast.Name) and d.func.value.id=='app')]
     actual.args=copy.deepcopy(expected.args)
     self.assertEqual(ast.dump(actual),ast.dump(expected))
 def test_superseded_status_is_available(self):
  from invoice_tool.presentation.status_catalog import make_customer_reimbursement_status_labels
  self.assertEqual(make_customer_reimbursement_status_labels()['superseded'],'已被替代')
