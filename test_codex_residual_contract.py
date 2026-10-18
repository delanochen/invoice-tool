"""Exact extraction contracts plus independent settlement branch behavior."""
import ast,copy,subprocess
from pathlib import Path
import app as target
from test_codebuddy_twenty_contract import Contract as PreviousContract
BASE='5f8f225eb980ea3d4377fb3642785d575559f47d'
GROUPS=[('residual_bootstrap', 'invoice_tool/app_bootstrap/startup_checks.py', ['verify_data_directory_identity', 'ensure_postgres_admin', 'init_db']), ('residual_language', 'invoice_tool/user_preferences/language_helpers.py', ['current_language', 'language_from_form', 'clear_session_preserving_language']), ('residual_settings', 'invoice_tool/app_settings/profile_helpers.py', ['inspection_cycle_days', 'inspection_warning_days', 'lodging_reimbursement_limit', 'get_company_profile', 'get_payment_instructions', 'get_invoice_terms', 'get_smtp_settings', 'get_google_maps_browser_api_key', 'get_google_geocoding_api_key', 'get_google_routes_api_key', 'get_google_static_maps_api_key']), ('residual_expense_policy', 'invoice_tool/employee_expenses/project_policy.py', ['is_lodging_project_name', 'is_fuel_project_name', 'validate_fuel_reimbursement_allowed']), ('residual_formatting', 'invoice_tool/presentation/value_helpers.py', ['project_color', 'pdf_text', 'to_float', 'decimal_value', 'money_decimal', 'money_float', 'nl2br_filter']), ('residual_sql', 'invoice_tool/database_console/statement_helpers.py', ['sql_statement_kind', 'is_read_sql']), ('residual_order_guard', 'invoice_tool/service_orders/start_date_helpers.py', ['require_service_order_start_date']), ('residual_photo_paths', 'invoice_tool/photo_imports/path_helpers.py', ['allowed_attachment', 'allowed_image', 'shared_photos_root', 'ensure_service_order_picture_folder', 'service_order_incoming_folder', 'is_google_photos_share_url', 'google_photo_url_key']), ('residual_settlement_paths', 'invoice_tool/customer_reimbursements/path_helpers.py', ['customer_reimbursement_dir', 'customer_reimbursement_attachment_dir', 'customer_reimbursement_file_path', 'customer_reimbursement_attachment_path', 'save_customer_reimbursement_uploads']), ('residual_settlement_values', 'invoice_tool/customer_reimbursements/value_helpers.py', ['is_us_weekend_or_holiday', 'reimbursement_number', 'reimbursement_paragraph']), ('residual_messages', 'invoice_tool/messages/content_helpers.py', ['create_message', 'review_message_body', 'return_message_body']), ('residual_audit', 'invoice_tool/audit_log/request_helpers.py', ['request_ip_address']), ('residual_geo_values', 'invoice_tool/geocoding/address_helpers.py', ['normalized_address', 'configured_country_codes', 'address_expectations']), ('residual_ai_values', 'invoice_tool/ai_daily_report_web/value_helpers.py', ['ai_search_like', '_ai_daily_report_business_date']), ('residual_hooks', 'invoice_tool/request_lifecycle/hooks.py', ['prevent_stale_business_pages', 'load_user', 'inject_globals']), ('residual_errors', 'invoice_tool/presentation/error_handlers.py', ['runtime_error', 'forbidden', 'not_found', 'internal_server_error', 'request_entity_too_large'])]
NAMES=[name for _,_,names in GROUPS for name in names]
class Contract(PreviousContract):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(target.__file__).parent
        cls.original=ast.parse(subprocess.check_output(['git','show',BASE+':app.py'],cwd=cls.root,text=True,encoding='utf-8'))
        cls.nodes={n.name:n for n in cls.original.body if isinstance(n,ast.FunctionDef)}
        cls.reference=dict(vars(target))
        nodes=[copy.deepcopy(cls.nodes[n]) for n in NAMES]
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'independent-residual','exec'),cls.reference)
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
                    actual.decorator_list=copy.deepcopy(old.decorator_list)
                    self.assertEqual(ast.dump(old),ast.dump(actual))
class BehaviorTest(Contract):
    def test_numeric_inputs_and_defaults(self):
        for name in ('to_float','decimal_value','money_decimal','money_float'):
            for value in (None,'','invalid','1.235',-2):
                with self.subTest(name=name,value=value):self.same(name,(value,))
    def test_statement_classification(self):
        for sql in ('',' SELECT 1','UPDATE t SET x=1','--comment\nSELECT 1',None):
            with self.subTest(sql=sql):self.same('sql_statement_kind',(sql,));self.same('is_read_sql',(sql,))
    def test_locale_form_defaults_and_invalid(self):
        for data in ({},{'default_language':'en'},{'default_language':'invalid'}):
            with self.subTest(data=data):self.same('language_from_form',form=data)
        self.same('current_language')
    def test_bootstrap_checks_stay_sequenced(self):
        self.same('init_db',setup=lambda tr,fn:dict(verify_postgres_schema=fn('schema'),ensure_postgres_admin=fn('admin')))
        self.same('verify_data_directory_identity',setup=lambda tr,fn:dict(REQUIRE_DATA_DIRECTORY_IDENTITY=False,PostgreSQLConnection=fn('unexpected connection',error=RuntimeError('must not connect'))))
    def test_url_and_filename_rules(self):
        for value in ('photo.jpg','document.pdf','unsafe.exe',''):
            with self.subTest(value=value):self.same('allowed_attachment',(value,));self.same('allowed_image',(value,))
        for value in ('https://photos.app.goo.gl/synthetic','https://example.invalid',''):
            with self.subTest(value=value):self.same('is_google_photos_share_url',(value,))
