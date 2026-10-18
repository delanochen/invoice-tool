"""Independent d14b2af dashboard/settings behavior and side-effect contracts."""
import ast
from pathlib import Path
import subprocess
from types import SimpleNamespace
from werkzeug.datastructures import MultiDict
from test_codebuddy_twenty_contract import Contract as PreviousContract

NAMES=['get_metrics','monthly_chart','available_projects_for_access','dashboard_project_options','report_project_options','monthly_project_chart','monthly_paid_chart','get_setting','set_setting','setting_float','setting_int','positive_int','payroll_subsidy_settings','validate_lodging_reimbursement','get_timezone_name','app_timezone','headquarters_coordinates','get_google_places_api_key','communication_languages_from_form','normalize_phone','report_location_filters']

class Contract(PreviousContract):
    @classmethod
    def setUpClass(cls):
        import app
        source=subprocess.check_output(['git','show','d14b2af:app.py'],cwd=Path(__file__).parent,text=True,encoding='utf-8')
        nodes=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
        cls.reference=dict(vars(app))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'independent-d14b2af','exec'),cls.reference)

class DashboardMetricsTest(Contract):
    def test_access_payroll_expense_and_invoice_summary(self):
        for external,permission,empty in ((False,False,False),(False,True,False),(True,True,False),(False,True,True)):
            def setup(tr,fn):
                def execute(sql,params=()):
                    tr.append(('sql',sql,params))
                    if 'from invoices' in sql:
                        rows=[] if empty else [{'id':1,'currency':'USD','paid_at':'2026-01-02','payment_amount':30},{'id':2,'currency':'USD','paid_at':None,'payment_amount':None}]
                        return SimpleNamespace(fetchall=lambda:rows)
                    if 'payout_status' in sql or 'employee_payment_orders' in sql:return SimpleNamespace(fetchone=lambda:{'total':0 if empty else 75})
                    return SimpleNamespace(fetchall=lambda:[] if empty else [{'status':'approved','amount':10},{'status':'submitted','amount':15},{'status':'returned','amount':None}])
                return dict(db=lambda:SimpleNamespace(execute=execute),client_filter_clause=fn('filter',('invoices.client_id = ?',[2])),invoice_totals=lambda identity:{'total':100 if identity==1 else 50},has_action_permission=fn('permission',permission),is_external_user=fn('external',external))
            with self.subTest(external=external,permission=permission,empty=empty):
                (kind,value),_=self.same('get_metrics',setup=setup)
                self.assertEqual(kind,'value');self.assertEqual(value['invoice_count'],0 if empty else 2)
                if external:self.assertEqual(value['pending_reimbursement'],0)

class DashboardMonthlyTest(Contract):
    def test_empty_zero_and_month_buckets(self):
        for rows,total in (([],0),([{'id':1,'issue_date':'2026-01-02'}],0),([{'id':1,'issue_date':'2026-01-02'},{'id':2,'issue_date':'2026-02-03'}],50)):
            with self.subTest(rows=rows,total=total):self.same('monthly_chart',setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn,rows=rows),client_filter_clause=fn('filter',('1=1',[])),invoice_totals=fn('totals',{'total':total})))

class DashboardProjectsTest(Contract):
    def test_visible_projects_and_empty_fallback(self):
        for first in ([],[{'id':1,'name':'Work'}]):
            def setup(tr,fn):
                calls=[0]
                def execute(sql,params=()):
                    tr.append(('sql',sql,params));calls[0]+=1
                    return SimpleNamespace(fetchall=lambda:first if calls[0]==1 else [{'id':2,'name':'Fallback'}])
                return dict(db=lambda:SimpleNamespace(execute=execute),client_filter_clause=fn('filter',('invoices.client_id=?',[2])))
            with self.subTest(first=first):self.same('available_projects_for_access',setup=setup)

class DashboardOptionsTest(Contract):
    def test_project_colors_and_empty(self):
        for rows in ([],[{'id':1,'name':'Work'}]):
            with self.subTest(rows=rows):self.same('dashboard_project_options',setup=lambda tr,fn:dict(available_projects_for_access=fn('projects',rows),project_color=fn('color','#123456')))

class ReportOptionsTest(Contract):
    def test_external_filtered_internal_union(self):
        for external in (True,False):
            with self.subTest(external=external):self.same('report_project_options',setup=lambda tr,fn:dict(is_external_user=fn('external',external),client_filter_clause=fn('filter',('invoices.client_id=?',[2])),db=lambda:self.database(tr,fn,rows=[{'id':1,'name':'Work'}])))

class ProjectChartTest(Contract):
    def test_selected_projects_empty_and_zero_buckets(self):
        fixtures=[[],[{'month':'2026-01','project_id':1,'project_name':'Work','total':0}],[{'month':'2026-01','project_id':1,'project_name':'Work','total':100},{'month':'2026-01','project_id':2,'project_name':'Other','total':50},{'month':'2026-02','project_id':1,'project_name':'Work','total':25}]]
        for selected in ([],[1,2]):
            for rows in fixtures:
                with self.subTest(selected=selected,rows=rows):self.same('monthly_project_chart',(selected,),setup=lambda tr,fn:dict(client_filter_clause=fn('filter',('invoices.client_id=?',[2])),db=lambda:self.database(tr,fn,rows=rows),project_color=fn('color','#123456')))

class PaidChartTest(Contract):
    def test_payment_amount_and_total_fallback(self):
        for rows in ([],[{'id':1,'paid_at':'2026-01-02','payment_amount':0}],[{'id':1,'paid_at':'2026-01-02','payment_amount':25},{'id':2,'paid_at':'2026-02-02','payment_amount':None}]):
            with self.subTest(rows=rows):self.same('monthly_paid_chart',setup=lambda tr,fn:dict(client_filter_clause=fn('filter',('1=1',[])),db=lambda:self.database(tr,fn,rows=rows),invoice_totals=fn('totals',{'total':50})))

class SettingWriteTest(Contract):
    def test_setting_missing_and_present(self):
        for row in (None,{'value':''},{'value':'synthetic'}):
            with self.subTest(row=row):self.same('get_setting',('synthetic-key','fallback'),setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn,one=row)))
    def test_upsert_preserves_sql_and_does_not_commit(self):
        _,trace=self.same('set_setting',('synthetic-key','synthetic-value'),setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn)))
        self.assertFalse(any(t[0]=='commit' for t in trace))

class SettingFloatTest(Contract):
    def test_float_defaults_invalid_and_zero(self):
        for value in ('','bad',None,'0','3.25','-2'):
            with self.subTest(value=value):self.same('setting_float',('synthetic-key',7),setup=lambda tr,fn:dict(get_setting=fn('setting',value)))

class SettingIntTest(Contract):
    def test_integer_defaults_invalid_truncation_and_zero(self):
        for value in ('','bad',None,'0','3.9','-2.5'):
            with self.subTest(value=value):self.same('setting_int',('synthetic-key',7),setup=lambda tr,fn:dict(get_setting=fn('setting',value)))

class PositiveIntTest(Contract):
    def test_positive_integer_lower_bound_and_defaults(self):
        for value in (None,'bad',0,-3,'2.9',5):
            with self.subTest(value=value):self.same('positive_int',(value,),kwargs={'default':2})

class SubsidySettingsTest(Contract):
    def test_daily_mileage_and_invalid_method(self):
        for method in ('daily','mileage','invalid'):
            def setup(tr,fn):
                def get(key,default=''):
                    tr.append(('setting',key,default));return method if key=='payroll_car_allowance_method' else default
                return dict(get_setting=get,setting_float=lambda key,default:default)
            with self.subTest(method=method):self.same('payroll_subsidy_settings',setup=setup)

class LodgingLimitTest(Contract):
    def test_lodging_disabled_exact_limit_and_excess(self):
        for amount,limit in ((150,0),(100,100),(101,100)):
            with self.subTest(amount=amount,limit=limit):self.same('validate_lodging_reimbursement',(amount,),kwargs={'label':'Synthetic lodging'},setup=lambda tr,fn:dict(lodging_reimbursement_limit=fn('limit',limit)))

class TimezoneTest(Contract):
    def test_timezone_name_fallback(self):
        for name in ('','Synthetic/Zone'):
            with self.subTest(name=name):self.same('get_timezone_name',setup=lambda tr,fn:dict(DEFAULT_TIMEZONE='Etc/UTC',get_setting=fn('setting',name)))
    def test_timezone_object_and_invalid_fallback(self):
        for name in ('Synthetic/Zone','bad'):
            def setup(tr,fn):
                class MissingZone(Exception):pass
                def zone(value):
                    tr.append(('zone',value))
                    if value=='bad':raise MissingZone()
                    return 'zone:'+value
                return dict(get_timezone_name=fn('name',name),ZoneInfo=zone,ZoneInfoNotFoundError=MissingZone,DEFAULT_TIMEZONE='Etc/UTC')
            with self.subTest(name=name):self.same('app_timezone',setup=setup)

class HeadquartersTest(Contract):
    def test_coordinates_finite_bounds_and_invalid_values(self):
        for lat,lon in (('0','0'),('90','180'),('-90','-180'),('91','0'),('0','181'),('nan','0'),('inf','0'),('bad','0'),(None,0)):
            with self.subTest(lat=lat,lon=lon):self.same('headquarters_coordinates',setup=lambda tr,fn:dict(HEADQUARTERS_LATITUDE=lat,HEADQUARTERS_LONGITUDE=lon))

class PlacesKeyTest(Contract):
    def test_explicit_key_and_geocoding_fallback_use_only_synthetic_values(self):
        for value in (' synthetic-explicit ','','  '):
            with self.subTest(value=value):self.same('get_google_places_api_key',setup=lambda tr,fn:dict(GOOGLE_PLACES_API_KEY_ENV='synthetic-env',get_setting=fn('setting',value),get_google_geocoding_api_key=fn('geocoding','synthetic-fallback')))

class CommunicationTest(Contract):
    def test_default_selected_preferred_invalid_and_duplicates(self):
        forms=[MultiDict(),MultiDict([('communication_language','en'),('communication_language','en')]),MultiDict([('communication_language','invalid'),('preferred_communication_language','en')]),MultiDict([('communication_language','en'),('preferred_communication_language','zh-CN')])]
        for form in forms:
            with self.subTest(form=list(form.items(multi=True))):self.same('communication_languages_from_form',form=form,setup=lambda tr,fn:dict(COMMUNICATION_LANGUAGES={'zh-CN':'Chinese','en':'English'}))

class PhoneTest(Contract):
    def test_us_international_formatting_and_invalid(self):
        for value,country in (('(202) 555-0123','US'),('+44 20 7946 0123','GB'),('12025550123','US'),('abc','US'),(None,'US'),('12345678','CN')):
            with self.subTest(value=value,country=country):self.same('normalize_phone',(value,country))

class ReportLocationTest(Contract):
    def test_region_country_validation_and_region_deduplication(self):
        countries=[{'code':'US','region_code':'na','region_name':'North America'},{'code':'CA','region_code':'na','region_name':'North America'},{'code':'CN','region_code':'asia','region_name':'Asia'}]
        for query in ({},{'region_code':' NA ','country_code':'us'},{'region_code':'bad','country_code':'bad'}):
            with self.subTest(query=query):self.same('report_location_filters',('orders',),setup=lambda tr,fn:dict(request=SimpleNamespace(args=query),country_rows=fn('countries',countries)))
