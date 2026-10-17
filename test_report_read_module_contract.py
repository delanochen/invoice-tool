"""Report reading/export/attachment regression checks against invoice_test."""
from pathlib import Path
import unittest
from unittest.mock import patch

from flask import abort, url_for
import test_expense_on_behalf as fixtures


class ReportReadModuleContractTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExpenseOnBehalfTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.module = self.fixture.app
        root = patch.object(self.module,'REPORT_ATTACHMENTS_DIR',str(Path(self.fixture.temp.name)/'report-files'))
        root.start()
        self.addCleanup(root.stop)
        self.http = self.fixture.http
        self.fixture.login('Manager')
        with self.module.app.app_context():
            connection = self.module.db()
            self.report_id = connection.execute("insert into service_reports (service_order_id,report_date,actual_work_date,service_description,created_by,created_at,updated_at) values (?,'2026-10-17','2026-10-17','Read module report',?,?,?)", (self.fixture.order,self.fixture.people['Submitter'],self.module.now(),self.module.now())).lastrowid
            connection.commit()

    def attachment(self, category='site', stored='sample.pdf', content=b'%PDF-1.7\nTest'):
        with self.module.app.app_context():
            connection = self.module.db()
            aid = connection.execute('insert into service_report_attachments (report_id,category,original_filename,stored_filename,content_type,uploaded_by,uploaded_at) values (?,?,?,?,?,?,?)', (self.report_id,category,Path(stored).name,stored,'application/pdf',self.fixture.people['Submitter'],self.module.now())).lastrowid
            connection.commit()
            row = connection.execute('select * from service_report_attachments where id=?', (aid,)).fetchone()
            path = Path(self.module.report_attachment_path(row))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return aid,path

    def test_route_urls_methods_and_legacy_exports(self):
        for name, url, methods, values in (
            ('view_service_report','/service-reports/17/view',{'GET'},{'report_id':17}),
            ('export_service_report','/service-reports/17/export',{'POST'},{'report_id':17}),
            ('preview_report_attachment','/service-report-attachments/17',{'GET'},{'attachment_id':17}),
            ('delete_report_attachment','/service-report-attachments/17/delete',{'POST'},{'attachment_id':17}),
        ):
            rules = list(self.module.app.url_map.iter_rules(name))
            self.assertEqual(len(rules),1)
            self.assertEqual(rules[0].methods-{'HEAD','OPTIONS'},methods)
            self.assertIs(getattr(self.module,name),self.module.app.view_functions[name])
            self.assertEqual(getattr(self.module,name).__module__,'invoice_tool.service_reports.report_read_routes')
            with self.module.app.test_request_context():
                self.assertEqual(url_for(name,**values),url)

    def test_grouped_attachments_preserve_categories_and_order(self):
        first,_ = self.attachment(stored='first.pdf')
        second,_ = self.attachment(stored='second.pdf')
        proof,_ = self.attachment(category='mileage_proof',stored='proof.pdf')
        with self.module.app.app_context():
            grouped = self.module.get_report_attachments(self.report_id)
        self.assertEqual([row['id'] for row in grouped['site']],[first,second])
        self.assertEqual([row['id'] for row in grouped['mileage_proof']],[proof])
        for category in ('self_check','arrival','departure'):
            self.assertEqual(grouped[category],[])

    def test_preview_uses_runtime_path_and_attachment_security(self):
        aid,path = self.attachment(stored='nested/sample.pdf')
        with patch.object(self.module,'report_attachment_path',return_value=str(path)) as resolver:
            response = self.http.get(f'/service-report-attachments/{aid}')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.mimetype,'application/pdf')
        resolver.assert_called_once()
        response.close()

    def test_attachment_access_requires_parent_report(self):
        aid,path = self.attachment()
        with patch.object(self.module,'require_service_report',side_effect=lambda identifier:abort(403)) as guard:
            self.assertEqual(self.http.get(f'/service-report-attachments/{aid}').status_code,403)
            self.assertEqual(self.http.post(f'/service-report-attachments/{aid}/delete').status_code,403)
            self.assertEqual(guard.call_count,2)
            guard.assert_called_with(self.report_id)
        self.assertTrue(path.is_file())

    def test_ajax_delete_removes_record_file_and_empty_folders(self):
        aid,path = self.attachment(stored='nested/sample.pdf')
        response = self.http.post(f'/service-report-attachments/{aid}/delete',headers={'X-Requested-With':'XMLHttpRequest'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json(),{'ok':True,'deleted':aid})
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())
        self.assertEqual(self.http.get(f'/service-report-attachments/{aid}').status_code,404)

    def test_missing_file_delete_preserves_anchor_validation(self):
        for name,anchor,expected in (('first.pdf','#photos','#photos'),('second.pdf','#bad anchor','')):
            aid,path = self.attachment(stored=name)
            path.unlink()
            response = self.http.post(f'/service-report-attachments/{aid}/delete',data={'redirect_anchor':anchor})
            self.assertEqual(response.status_code,302)
            self.assertTrue(response.location.endswith(f'/service-reports/{self.report_id}/edit'+expected))

    def test_export_resolves_current_builder_and_handles_failure(self):
        with patch.object(self.module,'build_service_report_docx',return_value=b'docx-content') as builder:
            response = self.http.post(f'/service-reports/{self.report_id}/export')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data,b'docx-content')
        self.assertIn('attachment',response.headers['Content-Disposition'])
        self.assertIn('.docx',response.headers['Content-Disposition'])
        self.assertEqual(builder.call_count,1)
        response.close()
        with patch.object(self.module,'build_service_report_docx',side_effect=ValueError('simulated')):
            response = self.http.post(f'/service-reports/{self.report_id}/export')
        self.assertEqual(response.status_code,302)
        self.assertTrue(response.location.endswith(f'/service-reports/{self.report_id}/edit'))

    def test_read_view_uses_runtime_navigation_and_attachment_helpers(self):
        with patch.object(self.module,'adjacent_service_report_ids',return_value={'previous':None,'next':None}) as navigation:
            with patch.object(self.module,'get_report_attachments',return_value={}) as attachments:
                response = self.http.get(f'/service-reports/{self.report_id}/view')
        self.assertEqual(response.status_code,200)
        self.assertIn('Read module report',response.get_data(as_text=True))
        navigation.assert_called_once_with(self.report_id)
        attachments.assert_called_once_with(self.report_id)
