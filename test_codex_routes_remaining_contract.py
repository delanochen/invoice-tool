"""Independently compare original route declarations to actual Flask registrations."""
import ast,inspect,subprocess,unittest
from pathlib import Path
import app
BASE='17c6a25561a8f32f2573eb2ac91f61fffa9e0cb1'
GROUPS=[('authentication', 'invoice_tool/authentication/routes.py', ['login', 'register']), ('system_settings', 'invoice_tool/app_settings/admin_routes.py', ['system_settings', 'menu_permissions', 'database_console']), ('assistant', 'invoice_tool/ai_assistant/web_routes.py', ['deepseek_connection_test', 'ai_assistant', 'ai_assistant_chat']), ('ai_confirmation', 'invoice_tool/ai_daily_report_web/confirmation_routes.py', ['ai_daily_report_chat', 'ai_daily_report_confirm']), ('ai_validation', 'invoice_tool/ai_daily_report_web/validation_routes.py', ['ai_daily_report_draft_preview', 'ai_daily_report_draft_validation', 'ai_daily_report_draft_acknowledge']), ('ai_evidence', 'invoice_tool/ai_daily_report_web/evidence_routes.py', ['ai_daily_report_draft_photo_preview', 'ai_daily_report_draft_evidence_preview', 'ai_daily_report_draft_prepare_attachments', 'ai_daily_report_draft_formal_save', 'ai_daily_report_manifest_compliance_review', 'ai_daily_report_draft_manifest', 'ai_daily_report_manifest_asset_preview']), ('ai_workers', 'invoice_tool/ai_daily_report_web/worker_routes.py', ['ai_daily_report_update_worker', 'ai_daily_report_staff_search', 'ai_daily_report_add_worker', 'ai_daily_report_remove_worker']), ('ai_work', 'invoice_tool/ai_daily_report_web/work_routes.py', ['ai_daily_report_recalculate_mileage', 'ai_daily_report_update_arrival_departure', 'ai_daily_report_update_work_item']), ('report_queries', 'invoice_tool/query_pages/settlement_routes.py', ['export_visible_report', 'customer_reimbursement_query', 'audit_log_report']), ('order_attachments', 'invoice_tool/service_orders/attachment_routes.py', ['import_google_photos_for_service_order', 'download_service_order_attachments']), ('settlement_form', 'invoice_tool/customer_reimbursements/form_routes.py', ['customer_reimbursement_form', 'download_customer_reimbursement', 'download_customer_reimbursement_excel', 'preview_customer_reimbursement']), ('settlement_actions', 'invoice_tool/customer_reimbursements/action_routes.py', ['approve_customer_reimbursement', 'sync_customer_reimbursement_sources', 'ignore_customer_reimbursement_source', 'unignore_customer_reimbursement_source', 'return_customer_reimbursement', 'reset_customer_reimbursement', 'delete_customer_reimbursement']), ('settlement_attachments', 'invoice_tool/customer_reimbursements/attachment_routes.py', ['download_customer_reimbursement_attachment', 'preview_customer_reimbursement_attachment', 'delete_customer_reimbursement_attachment']), ('invoice_editor', 'invoice_tool/invoices/editor_routes.py', ['new_invoice', 'edit_invoice'])]
class RemainingRouteRegistrationTest(unittest.TestCase):
    def test_original_endpoints_urls_methods_and_decorator_signatures(self):
        source=subprocess.check_output(['git','show',BASE+':app.py'],cwd=Path(app.__file__).parent,text=True,encoding='utf-8')
        nodes={n.name:n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef)}
        for _,_,names in GROUPS:
            for name in names:
                node=nodes[name]
                for decorator in node.decorator_list:
                    if not(isinstance(decorator,ast.Call) and isinstance(decorator.func,ast.Attribute) and isinstance(decorator.func.value,ast.Name) and decorator.func.value.id=='app' and decorator.func.attr in {'route','get','post'}):continue
                    with self.subTest(endpoint=name,url=ast.literal_eval(decorator.args[0])):
                        options={k.arg:ast.literal_eval(k.value) for k in decorator.keywords}
                        endpoint=options.get('endpoint',name);url=ast.literal_eval(decorator.args[0])
                        rules=[r for r in app.app.url_map.iter_rules(endpoint) if r.rule==url]
                        self.assertEqual(len(rules),1)
                        expected=set(options.get('methods',['POST'] if decorator.func.attr=='post' else ['GET']))
                        self.assertEqual(set(rules[0].methods)-{'HEAD','OPTIONS'},expected)
                        self.assertIs(app.app.view_functions[endpoint],getattr(app,name))
                        actual=inspect.signature(inspect.unwrap(getattr(app,name)))
                        self.assertEqual(list(actual.parameters),[a.arg for a in node.args.posonlyargs+node.args.args+node.args.kwonlyargs])
