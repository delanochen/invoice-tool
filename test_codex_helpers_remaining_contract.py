"""Exact extraction contracts plus independent settlement branch behavior."""
import ast,copy,subprocess
from pathlib import Path
import app as target
from test_codebuddy_twenty_contract import Contract as PreviousContract
BASE='234203ed57d6a4be18f2709b9c1e17cffa761400'
GROUPS=[('authorization', 'invoice_tool/authorization/policy_helpers.py', ['permission_tree_groups', 'can_approve_users', 'can_view_invoices', 'can_view_contracts', 'can_manage_contracts', 'can_create_invoice', 'can_create_service_order', 'can_delete_service_order', 'can_create_expense', 'can_approve_customer_reimbursement', 'can_reset_customer_reimbursement', 'can_delete_customer_reimbursement', 'can_transfer_expense_attachment', 'can_manage_employee_grades', 'can_view_labor_payroll_reports', 'can_view_customer_reimbursement', 'can_create_service_report', 'can_manage_users', 'can_manage_company_info', 'can_assign_external_employees', 'can_manage_user_record', 'can_manage_buyers', 'can_manage_owners', 'can_manage_manufacturers', 'can_access_buyer', 'can_view_audit_logs', 'can_use_database_console', 'menu_permission_overrides', 'has_menu_permission', 'action_permission_overrides', 'has_action_permission', 'required_action_for_request', 'can_access_client', 'require_contract_access', 'client_filter_clause', 'can_access_service_order', 'require_service_order', 'require_service_report']), ('ai_workflow', 'invoice_tool/ai_daily_report_web/workflow_helpers.py', ['reset_ai_drafts_for_deleted_service_report', '_auto_prepare_draft_media', '_build_review_center_preview', '_formal_save_preview_summary', '_run_phase7_validation', '_manifest_error_response', '_fold_staff_query']), ('identity', 'invoice_tool/authentication/identity_helpers.py', ['current_user', 'login_required', 'is_safe_redirect_target', 'admin_required', 'is_external_user', 'is_external_manager', 'is_external_employee', 'normalized_role', 'requires_user_address', 'is_internal_user', 'is_manager']), ('ai_access', 'invoice_tool/ai_daily_report_web/access_helpers.py', ['ai_daily_report_csrf_token', 'require_ai_daily_report_csrf', 'can_view_ai_daily_report_draft', 'can_edit_ai_daily_report_draft', 'can_prepare_attachment_manifest', 'can_formal_save_draft', 'ai_daily_report_draft_list_filters']), ('report_locks', 'invoice_tool/customer_reimbursements/report_locks.py', ['order_has_approved_reimbursement', 'report_locked_by_approved_reimbursement']), ('formatting', 'invoice_tool/presentation/formatting.py', ['role_label', 'state_label', 'money', 'hours', 'payment_label', 'local_datetime']), ('ai_settings', 'invoice_tool/app_settings/ai_helpers.py', ['accounting_base_enabled', 'deepseek_assistant_settings', 'vision_settings']), ('order_validation', 'invoice_tool/service_orders/validation_helpers.py', ['client_order_number_warning']), ('expense_numbering', 'invoice_tool/employee_expenses/numbering.py', ['next_expense_number']), ('photo_storage', 'invoice_tool/photo_imports/storage_helpers.py', ['file_sha256', 'service_order_photo_folder', 'service_order_photo_summary', 'valid_image_file']), ('google_photos', 'invoice_tool/photo_imports/google_helpers.py', ['google_photos_page_html', 'normalize_embedded_google_url', 'google_image_score', 'google_image_dimensions', 'is_likely_google_account_image', 'google_photo_download_candidates', 'extract_google_photo_urls', 'downloaded_image_extension', 'unique_download_path', 'download_google_photo_image', 'import_google_photos_share_to_incoming', 'send_google_photos_import_result', 'run_google_photos_import_job', 'start_google_photos_import_job']), ('attachment_response', 'invoice_tool/attachments/response_helpers.py', ['safe_attachment_response']), ('settlement_support', 'invoice_tool/customer_reimbursements/support_helpers.py', ['_parse_quotation_revision_snapshot', 'excessive_following_mileage_rows', '_fallback_row_for_worker', 'service_order_active_invoice', 'remove_customer_reimbursement_pdf']), ('audit', 'invoice_tool/audit_log/record_helpers.py', ['log_action', 'log_login_action']), ('geocoding', 'invoice_tool/geocoding/lookup_helpers.py', ['parse_coordinate_pair', 'census_result_matches_address', 'geocode_with_google', 'geocode_with_census', 'nominatim_address_candidates', 'geocode_with_nominatim', 'geocode_address', 'geocode_service_order', 'geocode_buyer']), ('messages', 'invoice_tool/messages/query_helpers.py', ['notify_role', 'unread_message_count', '_message_ids_for_user']), ('assistant_helpers', 'invoice_tool/ai_assistant/query_helpers.py', ['ai_normalize_status', 'ai_search_business_records', 'deepseek_api_error_message', 'call_deepseek_chat'])]
NAMES=[name for _,_,names in GROUPS for name in names]
class Contract(PreviousContract):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(target.__file__).parent
        cls.original=ast.parse(subprocess.check_output(['git','show',BASE+':app.py'],cwd=cls.root,text=True,encoding='utf-8'))
        cls.nodes={n.name:n for n in cls.original.body if isinstance(n,ast.FunctionDef)}
        cls.reference=dict(vars(target))
        exec(compile(ast.Module(body=[copy.deepcopy(cls.nodes[n]) for n in NAMES],type_ignores=[]),'independent-settlement-remaining','exec'),cls.reference)
class StructureTest(Contract):
    def test_each_body_and_legacy_signature(self):
        current={n.name:n for n in ast.parse((self.root/'app.py').read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef)}
        for _,module,names in GROUPS:
            path=self.root/module
            extracted={n.name:n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef)} if path.exists() else {}
            for name in names:
                with self.subTest(function=name):
                    old=self.nodes[name];actual=copy.deepcopy(extracted.get(name,current[name]))
                    self.assertEqual(ast.dump(old.args),ast.dump(current[name].args))
                    actual.args=copy.deepcopy(old.args)
                    self.assertEqual(ast.dump(old),ast.dump(actual))
class BehaviorTest(Contract):
    def test_presentation_money_and_hours(self):
        for name in ('money','hours'):
            for value in (None,0,'1.25',-2.5):
                with self.subTest(name=name,value=value):self.same(name,(value,))
    def test_role_and_safe_redirect(self):
        self.same('normalized_role')
        for value in ('/messages','https://example.invalid/path','//example.invalid/path',''):
            with self.subTest(value=value):self.same('is_safe_redirect_target',(value,))
    def test_manage_permissions_short_circuit(self):
        for value in (False,True):
            with self.subTest(value=value):self.same('can_manage_contracts',setup=lambda tr,fn:dict(has_action_permission=fn('permission',value)))
    def test_coordinate_parsing(self):
        for value in ('1,2','bad','',None):
            with self.subTest(value=value):self.same('parse_coordinate_pair',(value,))
    def test_staff_search_normalization(self):
        for value in (' Alice ','ALICE','张三',''):
            with self.subTest(value=value):self.same('_fold_staff_query',(value,))
