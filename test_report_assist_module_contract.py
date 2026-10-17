"""HTTP assist/evidence contract tests; all map services are mocked."""
from pathlib import Path
import unittest
from unittest.mock import patch

from flask import url_for
from PIL import Image
import test_report_write_module_contract as fixtures


class ReportAssistModuleContractTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReportWriteModuleContractTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.module,self.http,self.report_id = self.fixture.module,self.fixture.http,self.fixture.report_id
        self.temp = Path(self.fixture.fixture.f.temp.name)
        root = patch.object(self.module,'DATA_DIR',str(self.temp/'runtime-data'))
        root.start()
        self.addCleanup(root.stop)
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute("update service_report_workers set origin_address='Hotel',trip_type='one_way' where report_id=?",(self.report_id,))
            connection.commit()
        for name,value in (('get_google_routes_api_key','test-routes-key'),('get_google_static_maps_api_key','test-static-key')):
            item = patch.object(self.module,name,return_value=value)
            item.start()
            self.addCleanup(item.stop)
        for name in ('GoogleRoutesService','GoogleStaticMapsService','MileageEvidenceService','PhotoDiscoveryService','PhotoMetadataService','EmployeeResolutionService'):
            item = patch.object(self.module,name)
            setattr(self,name,item.start())
            self.addCleanup(item.stop)

    def summary(self,results=()):
        return {'generated':sum(r['outcome']=='generated' for r in results),'reused':sum(r['outcome']=='reused' for r in results),'failed':sum(r['outcome']=='failed' for r in results),'results':list(results)}

    def generate(self,summary,**payload):
        with patch.object(self.module,'ServiceReportEvidenceService') as factory:
            factory.return_value.generate.return_value = summary
            response = self.http.post(f'/service-reports/{self.report_id}/mileage-evidence/generate',json=payload)
        return response,factory

    def attachment_id(self):
        source = self.temp/'source.png'
        Image.new('RGB',(4,4),'blue').save(source)
        with self.module.app.test_request_context():
            self.module.g.user = {'id':self.fixture.fixture.f.people['Manager']}
            identifier = self.module.save_generated_report_attachment(self.report_id,str(source),'evidence.png')
            self.module.db().commit()
        return identifier

    def result(self,attachment):
        return {'user_id':self.fixture.fixture.f.people['Submitter'],'outcome':'generated','route_fingerprint':'fingerprint-1','attachment_id':attachment,'trip_type':'one_way','one_way_miles':10,'reported_miles':10}

    def test_routes_keep_url_endpoint_and_post_method(self):
        for name,url,values in (
            ('generate_service_report_mileage_evidence','/service-reports/17/mileage-evidence/generate',{'report_id':17}),
            ('service_report_assist_plan','/api/service-reports/assist-plan',{}),
        ):
            rules = list(self.module.app.url_map.iter_rules(name))
            self.assertEqual(len(rules),1)
            self.assertEqual(rules[0].methods-{'HEAD','OPTIONS'},{'POST'})
            self.assertIs(getattr(self.module,name),self.module.app.view_functions[name])
            self.assertIn(getattr(self.module,name).__module__,(self.module.__name__,'invoice_tool.service_reports.assist_routes'))
            with self.module.app.test_request_context():
                self.assertEqual(url_for(name,**values),url)

    def test_generated_results_commit_and_upsert_same_worker(self):
        summary = self.summary([self.result(self.attachment_id())])
        response,factory = self.generate(summary,force=True)
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.get_json()['ok'])
        self.assertTrue(factory.return_value.generate.call_args.kwargs['force'])
        self.assertEqual(factory.call_args.kwargs['evidence_root'],str(self.temp/'runtime-data/service-report-mileage'))
        self.GoogleRoutesService.assert_called_with('test-routes-key',timeout=8,max_retries=1,prefer_fuel_efficient=True)
        self.assertEqual(self.generate(summary)[0].status_code,200)
        with self.module.app.app_context():
            rows = self.module.db().execute('select * from service_report_mileage_evidence where report_id=?',(self.report_id,)).fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['route_fingerprint'],'fingerprint-1')
            self.assertEqual(float(rows[0]['reported_miles']),10)

    def test_failed_or_reused_result_keeps_previous_evidence(self):
        result = self.result(self.attachment_id())
        self.assertEqual(self.generate(self.summary([result]))[0].status_code,200)
        for outcome in ('failed','reused'):
            response,factory = self.generate(self.summary([dict(result,outcome=outcome,route_fingerprint='different')]))
            self.assertEqual(response.status_code,200)
            existing = factory.return_value.generate.call_args.kwargs['existing_by_user']
            self.assertEqual(existing[result['user_id']]['route_fingerprint'],'fingerprint-1')
            with self.module.app.app_context():
                self.assertEqual(self.module.db().execute('select route_fingerprint from service_report_mileage_evidence where report_id=?',(self.report_id,)).fetchone()[0],'fingerprint-1')

    def test_generation_exception_rolls_back_database_writes(self):
        def failure(**kwargs):
            self.module.db().execute("update service_reports set service_description='rollback me' where id=?",(self.report_id,))
            raise RuntimeError('simulated')
        with patch.object(self.module,'ServiceReportEvidenceService') as factory:
            factory.return_value.generate.side_effect = failure
            response = self.http.post(f'/service-reports/{self.report_id}/mileage-evidence/generate',json={})
        self.assertEqual(response.status_code,500)
        self.assertFalse(response.get_json()['ok'])
        with self.module.app.app_context():
            self.assertEqual(self.module.db().execute('select service_description from service_reports where id=?',(self.report_id,)).fetchone()[0],'Original service')

    def test_external_manager_cannot_generate_evidence(self):
        with patch.object(self.module,'is_external_manager',return_value=True):
            with patch.object(self.module,'ServiceReportEvidenceService') as factory:
                response = self.http.post(f'/service-reports/{self.report_id}/mileage-evidence/generate',json={})
        self.assertEqual(response.status_code,403)
        factory.assert_not_called()

    def test_external_employee_cannot_generate_for_other_creator(self):
        with patch.object(self.module,'is_external_employee',return_value=True):
            with patch.object(self.module,'ServiceReportEvidenceService') as factory:
                response = self.http.post(f'/service-reports/{self.report_id}/mileage-evidence/generate',json={})
        self.assertEqual(response.status_code,403)
        factory.assert_not_called()

    def test_preview_filters_workers_uses_current_shared_root_and_does_not_persist(self):
        tables = ['service_reports','service_report_workers','service_report_attachments','service_report_save_tokens','service_report_mileage_evidence']
        with self.module.app.app_context():
            before = {table:self.module.db().execute(f'select count(*) from {table}').fetchone()[0] for table in tables}
        payload = {'order_id':self.fixture.fixture.f.order,'report_date':'2026-10-17','site_address':'Destination','workers':[{'user_id':self.fixture.fixture.f.people['Submitter'],'current_origin':'Hotel'},{'user_id':999999999}]}
        with patch.object(self.module,'SHARED_PHOTOS_DIR',str(self.temp/'new-shared')):
            with patch.object(self.module,'ServiceReportAssistService') as factory:
                factory.return_value.build_plan.return_value = {'ok':True,'workers':[]}
                response = self.http.post('/api/service-reports/assist-plan',json=payload)
        self.assertEqual(response.status_code,200)
        arguments = factory.return_value.build_plan.call_args.kwargs
        self.assertEqual(len(arguments['workers']),1)
        self.assertEqual(arguments['destination'],'Destination')
        self.PhotoDiscoveryService.assert_called_once()
        self.assertEqual(self.PhotoDiscoveryService.call_args.kwargs['shared_photos_root'],str(self.temp/'new-shared'))
        self.GoogleRoutesService.assert_called_with('test-routes-key',prefer_fuel_efficient=True)
        with self.module.app.app_context():
            after = {table:self.module.db().execute(f'select count(*) from {table}').fetchone()[0] for table in tables}
        self.assertEqual(before,after)

    def test_preview_invalid_order_or_date_returns_validation_error(self):
        with patch.object(self.module,'ServiceReportAssistService') as factory:
            for payload in ({},{'order_id':'bad'},{'order_id':self.fixture.fixture.f.order,'report_date':'bad'}):
                self.assertEqual(self.http.post('/api/service-reports/assist-plan',json=payload).status_code,400)
        factory.assert_not_called()
