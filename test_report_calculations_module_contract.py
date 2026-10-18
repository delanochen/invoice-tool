"""Compare report form/time/mileage results with the preceding implementation."""
import ast
import builtins
from decimal import Decimal
import inspect
import itertools
from pathlib import Path
import subprocess
import symtable
import unittest
from unittest.mock import patch

import app as module

ROOT = Path(__file__).resolve().parent
BASE = '29b690c'
NAMES = ['posted_report_time','report_worker_ids_from_form','report_worker_rows_from_form','calculated_report_total_time','calculated_report_total_service_hours','calculated_report_driving_miles','report_billing_mileage','parse_report_minutes','rounded_report_service_hours','format_report_hours','report_duration_hours','report_actual_date']


class ReportCalculationsModuleContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = subprocess.check_output(['git','show',BASE+':app.py'],cwd=ROOT,text=True,encoding='utf-8')
        nodes = {node.name:node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef)}
        tables = {table.get_name():table for table in symtable.symtable(source,'app.py','exec').get_children()}
        def dependencies(table):
            result = {s.get_name() for s in table.get_symbols() if s.is_global() and s.is_referenced()}
            for child in table.get_children():
                result |= dependencies(child)
            return result-set(vars(builtins))
        external = set().union(*(dependencies(tables[name]) for name in NAMES))-set(NAMES)
        cls.reference = {name:getattr(module,name) for name in external}
        exec(compile(ast.Module(body=[nodes[name] for name in NAMES],type_ignores=[]),'baseline-report-calculations.py','exec'),cls.reference)

    def outcome(self,function,*args):
        try:
            return ('value',function(*args))
        except Exception as error:
            return ('error',type(error).__name__,str(error))

    def same(self,name,*args):
        self.assertEqual(self.outcome(getattr(module,name),*args),self.outcome(self.reference[name],*args),(name,args))

    def form(self,**extra):
        values = dict(worker_user_id='7',worker_travel_mode='self_drive',worker_driving_miles='10',worker_travel_hours='1',worker_public_transport_hours='0',worker_work_description='Work',worker_origin='Hotel',worker_trip_type='one_way')
        values.update(extra)
        return values

    def test_minute_rounding_grid_matches_original(self):
        for start in range(0,1440,120):
            for duration in range(0,361):
                arrival = f'{start//60:02d}:{start%60:02d}'
                end = start+duration
                departure = f'{end//60:02d}:{end%60:02d}'
                self.same('rounded_report_service_hours',arrival,departure)
        for value in (None,'','bad','08:00:30','-1:15','25:01','08:07','08:08'):
            self.same('parse_report_minutes',value)

    def test_duration_fallback_and_worker_count_matches_original(self):
        for arrival,departure,total,count in itertools.product((None,'bad','08:00','08:07'),('', '07:00','08:08','16:00'),(None,'0','9.5',Decimal('13')),(-1,0,1,2,3)):
            self.same('report_duration_hours',dict(arrival_time=arrival,departure_time=departure,total_service_hours=total),count)

    def test_mileage_modes_and_vehicle_billing_match_original(self):
        modes = ('self_drive','following','rental_drive','flight')
        for left,right,amount,method in itertools.product(modes,modes,(0,1.234,50),('per_person','per_vehicle','unknown')):
            rows = [dict(worker_travel_mode=left,driving_miles=amount),dict(worker_travel_mode=right,driving_miles=amount+0.123)]
            self.same('report_billing_mileage',rows,method)

    def test_worker_form_valid_invalid_and_duplicate_rows_match_original(self):
        for mode,miles,hours,public in itertools.product(('self_drive','following','rental_drive','flight'),('','-1','0','10'),('','-1','0','1'),('','0','2')):
            with module.app.test_request_context(method='POST',data=self.form(worker_travel_mode=mode,worker_driving_miles=miles,worker_travel_hours=hours,worker_public_transport_hours=public)):
                self.same('report_worker_rows_from_form')
                self.same('calculated_report_total_time')
                self.same('calculated_report_total_service_hours','08:00','16:08')
                self.same('calculated_report_driving_miles')
        for identifiers in (['7','7'],['','7','8'],['7',' 7 '],[]):
            with module.app.test_request_context(method='POST',data=self.form(worker_user_id=identifiers)):
                self.same('report_worker_ids_from_form')
                self.same('report_worker_rows_from_form')

    def test_posted_time_fields_and_legacy_fallback_match_original(self):
        for hour,minute in itertools.product(('','0','08','23','24','bad'),('','0','07','59','60','bad')):
            with module.app.test_request_context(method='POST',data={'arrival_time':'09:22','arrival_time_hour':hour,'arrival_time_minute':minute}):
                self.same('posted_report_time','arrival_time')

    def test_formatted_hours_and_actual_date_match_original(self):
        for value in (0,0.25,2.25,-1.5,'3.50',Decimal('12.125'),None,'bad'):
            self.same('format_report_hours',value)
        for actual in (None,'','2026-10-17'):
            self.same('report_actual_date',{'actual_work_date':actual,'report_date':'2026-10-16'})

    def test_calculations_resolve_current_legacy_helper_overrides(self):
        rows = [dict(travel_hours=1,public_transport_hours=0,driving_miles=10,worker_travel_mode='self_drive')]*2
        with module.app.test_request_context(method='POST',data={'mileage_billing_method':'per_vehicle'}):
            with patch.object(module,'report_worker_rows_from_form',return_value=rows):
                with patch.object(module,'rounded_report_service_hours',return_value=2.25):
                    self.assertEqual(module.calculated_report_total_service_hours('08:00','10:00'),4.5)
                    self.assertEqual(module.report_duration_hours({'arrival_time':'08:00','departure_time':'10:00','total_service_hours':99},2),2.25)
                with patch.object(module,'format_report_hours',return_value='runtime'):
                    self.assertEqual(module.calculated_report_total_time(),'runtime')
                with patch.object(module,'report_billing_mileage',return_value=123) as mileage:
                    self.assertEqual(module.calculated_report_driving_miles(),123)
                    mileage.assert_called_once_with(rows,'per_vehicle')

    def test_root_signatures_remain_compatible(self):
        for name in NAMES:
            self.assertEqual(inspect.signature(getattr(module,name)),inspect.signature(self.reference[name]),name)
