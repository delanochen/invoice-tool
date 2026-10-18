"""Independent settlement-core contracts; synthetic inputs and no real business writes."""
import ast
from datetime import date,datetime
from decimal import Decimal
from pathlib import Path
import subprocess
from types import SimpleNamespace
from test_codebuddy_twenty_contract import Contract as PreviousContract

NAMES=['require_customer_reimbursement','resolve_contract_rates','contract_rate_missing_summary','used_customer_rate_fields','assert_contract_rates_covered','observed_us_holidays','_row_field','customer_reimbursement_item_expense_amount','calculate_customer_reimbursement_item','expense_settlement_invoice_mapping','expense_settlement_invoice_field','canonical_project_mapping_key','customer_reimbursement_expense_field','_reimbursement_date_key','approved_mro_supplies_total','approved_rental_vehicle_fuel_total','customer_reimbursement_totals','customer_reimbursement_column_totals','customer_reimbursement_items']

class Contract(PreviousContract):
    @classmethod
    def setUpClass(cls):
        import app
        source=subprocess.check_output(['git','show','ff94ef25c4a4b39bc6c8672fdf6fa20b18b29e95:app.py'],cwd=Path(app.__file__).parent,text=True,encoding='utf-8')
        nodes=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
        cls.reference=dict(vars(app));exec(compile(ast.Module(body=nodes,type_ignores=[]),'independent-settlement-base','exec'),cls.reference)

class SettlementAccessTest(Contract):
    def test_access_missing_and_order_resolution(self):
        for allowed,row in ((False,{'service_order_id':7}),(True,None),(True,{'id':1,'service_order_id':7})):
            with self.subTest(allowed=allowed,row=row):self.same('require_customer_reimbursement',(1,),setup=lambda tr,fn:dict(can_view_customer_reimbursement=fn('allowed',allowed),db=lambda:self.database(tr,fn,one=row),require_service_order=fn('order',{'id':7})))

class ContractRatesTest(Contract):
    def test_rates_and_missing_labels(self):
        for missing in (False,True):
            def setup(tr,fn):
                connection=self.database(tr,fn)
                def rate(db,order,day,kind,**kwargs):
                    self.assertIs(db,connection);tr.append(('rate',order,day,kind,kwargs))
                    return {'rate':Decimal('12.34'),'source':'missing' if missing else 'contract'}
                return dict(db=lambda:connection,contract_rate=rate,lodging_reimbursement_limit=fn('lodging',50),CUSTOMER_REIMBURSEMENT_RATE_FIELDS=[('standard_rate','standard','Standard'),('mileage_rate','mileage','Mileage')])
            with self.subTest(missing=missing):self.same('resolve_contract_rates',(7,'2026-10-17'),setup=setup)

class MissingRatesTest(Contract):
    def test_dates_deduplicate_sort_and_keep_missing_only(self):
        def setup(tr,fn):
            def rates(order,day):tr.append(('rates',order,day));return {},(['Mileage'] if day=='2026-10-17' else [])
            return dict(resolve_contract_rates=rates)
        self.same('contract_rate_missing_summary',(7,['2026-10-18','2026-10-17T08:00:00','2026-10-17',None]),setup=setup)

class UsedRatesTest(Contract):
    def test_only_positive_quantities_need_rates(self):
        for row in ({},{'standard_hours':'2','miles':10},{'standard_hours':0,'holiday_hours':-1,'transport_hours':'3'}):
            with self.subTest(row=row):self.same('used_customer_rate_fields',(row,))

class RateGuardTest(Contract):
    def test_no_used_fields_covered_and_missing(self):
        for used,missing in (([],['Standard']),(['standard_rate'],[]),(['standard_rate'],['Standard'])):
            with self.subTest(used=used,missing=missing):self.same('assert_contract_rates_covered',(7,[{'project_date':'2026-10-17'}]),setup=lambda tr,fn:dict(CUSTOMER_REIMBURSEMENT_RATE_FIELDS=[('standard_rate','standard','Standard')],used_customer_rate_fields=fn('used',used),resolve_contract_rates=fn('rates',({},missing))))

class HolidaysTest(Contract):
    def test_weekend_observance_and_year_boundaries(self):
        for year in (2021,2022,2026):
            with self.subTest(year=year):
                (kind,days),_=self.same('observed_us_holidays',(year,));self.assertEqual(kind,'value');self.assertTrue(days)

class RowFieldTest(Contract):
    def test_missing_null_and_explicit_zero(self):
        for row in ({},{'amount':None},{'amount':0},{'amount':'12.34'},None):
            with self.subTest(row=row):self.same('_row_field',(row,'amount',7))

class ExpenseAmountTest(Contract):
    def test_manual_override_does_not_add_auto_amount(self):
        for row,expected in (({'other':20,'auto_other':50},Decimal('20.00')),({'other':0,'auto_other':50},Decimal('50.00')),({},Decimal('0.00'))):
            with self.subTest(row=row):
                result,_=self.same('customer_reimbursement_item_expense_amount',(row,'other'));self.assertEqual(result,('value',expected))

class ItemCalculationTest(Contract):
    def test_labor_mileage_driving_override_and_expense_override(self):
        for row in ({},{'standard_hours':2,'standard_rate':30,'miles':10,'mileage_rate':2,'other':20,'auto_other':50},{'miles':10,'mileage_rate':2,'driving_amount':7,'auto_fuel':5}):
            with self.subTest(row=row):self.same('calculate_customer_reimbursement_item',(row,),kwargs={'sort_order':3})

class ProjectMappingTest(Contract):
    def test_mapping_normalizes_mro_without_writing(self):
        rows=[{'expense_project_name':'MRO Supplies','settlement_field':'other','invoice_project_name':'MRO Supplies'},{'expense_project_name':'Fuel','settlement_field':'fuel','invoice_project_name':'Fuel'}]
        _,trace=self.same('expense_settlement_invoice_mapping',setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn,rows=rows),is_mro_project_name=lambda value:value=='MRO Supplies',MRO_CANONICAL_PROJECT_NAME='MRO Supplies synthetic',project_name_key=lambda value:value.casefold()))
        self.assertFalse(any(t[0]=='commit' for t in trace))

class ProjectFieldTest(Contract):
    def test_exact_longest_prefix_delimiter_and_unknown(self):
        mapping={'express delivery':('other','Express'),'express':('other','Short'),'fuel油费':('fuel','Fuel')}
        for name in ('express delivery','express delivery 快递费','express deliveryplus','fuel油费','unknown'):
            with self.subTest(name=name):self.same('expense_settlement_invoice_field',(name,),setup=lambda tr,fn:dict(expense_settlement_invoice_mapping=fn('mapping',mapping),project_name_key=lambda value:value.casefold()))

class CanonicalProjectTest(Contract):
    def test_canonical_prefix_boundaries(self):
        for name in ('fuel','fuel油费','fuel extra','fuelplus','other'):
            with self.subTest(name=name):self.same('canonical_project_mapping_key',(name,['fuel']),setup=lambda tr,fn:dict(project_name_key=lambda value:value.casefold()))

class ExpenseFieldTest(Contract):
    def test_only_rental_fuel_maps_to_settlement(self):
        for field,vehicle in (('fuel','rental'),('fuel','self_owned'),('other',None),(None,None)):
            with self.subTest(field=field,vehicle=vehicle):self.same('customer_reimbursement_expense_field',('Synthetic',),kwargs={'fuel_vehicle_type':vehicle},setup=lambda tr,fn:dict(expense_settlement_invoice_field=fn('mapping',(field,'Synthetic'))))

class SettlementDateTest(Contract):
    def test_date_datetime_blank_and_iso_strings(self):
        for value in (None,'',date(2026,10,17),datetime(2026,10,17,12,30),'2026-10-17T12:30:00',' 2026-10-17 12:30:00 '):
            with self.subTest(value=value):self.same('_reimbursement_date_key',(value,))

class MroTotalTest(Contract):
    def test_mro_only_and_source_filters_forwarded(self):
        rows=[{'project_name':'MRO Supplies synthetic','amount':20},{'project_name':'Fuel','amount':30}]
        self.same('approved_mro_supplies_total',(7,),kwargs={'cutoff_at':'2026-10-17','exclude_ignored_for_reimbursement_id':1},setup=lambda tr,fn:dict(approved_customer_reimbursement_expense_rows=fn('rows',rows),customer_reimbursement_expense_field=lambda name:'other' if name.startswith('MRO') else 'fuel',project_name_key=lambda value:value.casefold()))

class RentalFuelTotalTest(Contract):
    def test_sum_null_and_missing(self):
        for row in (None,{'total':None},{'total':Decimal('12.34')}):
            with self.subTest(row=row):self.same('approved_rental_vehicle_fuel_total',(7,),setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn,one=row)))

class SettlementTotalsTest(Contract):
    def test_mro_is_not_counted_twice_and_rental_fuel_is_additional(self):
        item={'labor_total':50,'mileage_total':10,'total':100,'other':20,'auto_other':50}
        result,_=self.same('customer_reimbursement_totals',([item],),kwargs={'mro_supplies_total':50,'rental_fuel_total':7})
        self.assertEqual(result[1]['total_amount'],107)
        self.same('customer_reimbursement_totals',([],))

class ColumnTotalsTest(Contract):
    def test_numeric_expense_overrides_and_empty_rows(self):
        row={field:0 for field in ('standard_hours','transport_hours','public_transport_hours','overtime_hours','holiday_hours','miles','labor_total','mileage_total','total')}
        row.update(standard_hours=2,other=20,auto_other=50,total=20)
        for rows in ([],[row]):
            with self.subTest(rows=rows):self.same('customer_reimbursement_column_totals',(rows,))

class SettlementItemsTest(Contract):
    def test_ordered_item_query(self):
        self.same('customer_reimbursement_items',(7,),setup=lambda tr,fn:dict(db=lambda:self.database(tr,fn,rows=[{'id':1,'sort_order':0}])))
