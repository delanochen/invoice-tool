"""Save-token, transactional rollback and payroll-reference regressions."""
from pathlib import Path
import secrets
import unittest
from unittest.mock import patch

from flask import url_for
import test_report_copy as fixtures


class ReportWriteModuleContractTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReportCopyTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.module, self.http = self.fixture.m,self.fixture.http
        self.report_id = self.fixture.source['id']
        self.fixture.f.login('Manager')
        root = patch.object(self.module,'REPORT_ATTACHMENTS_DIR',str(Path(self.fixture.f.temp.name)/'report-files'))
        root.start()
        self.addCleanup(root.stop)

    def form(self, **extra):
        data = dict(self.fixture.data,save_token=secrets.token_urlsafe(24))
        data.update(extra)
        return data

    def count(self,table):
        with self.module.app.app_context():
            return self.module.db().execute(f'select count(*) from {table}').fetchone()[0]

    def test_routes_preserve_endpoints_methods_and_legacy_exports(self):
        for name,url,methods,values in (
            ('new_service_report','/service-orders/17/reports/new',{'GET','POST'},{'order_id':17}),
            ('edit_service_report','/service-reports/17/edit',{'GET','POST'},{'report_id':17}),
            ('delete_service_report','/service-reports/17/delete',{'POST'},{'report_id':17}),
        ):
            rules = list(self.module.app.url_map.iter_rules(name))
            self.assertEqual(len(rules),1)
            self.assertEqual(rules[0].methods-{'HEAD','OPTIONS'},methods)
            self.assertIs(getattr(self.module,name),self.module.app.view_functions[name])
            self.assertEqual(getattr(self.module,name).__module__,'invoice_tool.service_reports.report_write_routes')
            with self.module.app.test_request_context():
                self.assertEqual(url_for(name,**values),url)

    def test_repeated_edit_token_cannot_overwrite_saved_report(self):
        data = self.form(service_description='First edit')
        url = f'/service-reports/{self.report_id}/edit'
        self.assertEqual(self.http.post(url,data=data).status_code,302)
        self.assertEqual(self.http.post(url,data=dict(data,service_description='Duplicate edit')).status_code,302)
        with self.module.app.app_context():
            row = self.module.db().execute('select service_description from service_reports where id=?',(self.report_id,)).fetchone()
            self.assertEqual(row['service_description'],'First edit')
        for table in ('service_reports','service_report_workers','service_report_saved_parts','service_report_replaced_parts'):
            self.assertEqual(self.count(table),1)

    def test_validation_rollback_releases_token_for_corrected_retry(self):
        data = self.form(worker_user_id='999999999')
        self.assertEqual(self.http.post(self.fixture.url,data=data).status_code,302)
        self.assertEqual(self.count('service_reports'),1)
        with self.module.app.app_context():
            self.assertIsNone(self.module.db().execute('select token from service_report_save_tokens where token=?',(data['save_token'],)).fetchone())
        data['worker_user_id'] = str(self.fixture.f.people['Submitter'])
        self.http.post(self.fixture.url,data=data)
        self.http.post(self.fixture.url,data=data)
        self.assertEqual(self.count('service_reports'),2)
        self.assertEqual(self.count('service_report_workers'),2)

    def test_attachment_failure_rolls_back_parent_details_and_token(self):
        data = self.form()
        with patch.object(self.module,'save_report_uploads',side_effect=ValueError('simulated upload failure')) as upload:
            self.assertEqual(self.http.post(self.fixture.url,data=data).status_code,302)
        upload.assert_called_once()
        for table in ('service_reports','service_report_workers','service_report_saved_parts','service_report_replaced_parts'):
            self.assertEqual(self.count(table),1)
        with self.module.app.app_context():
            self.assertIsNone(self.module.db().execute('select token from service_report_save_tokens where token=?',(data['save_token'],)).fetchone())
        self.assertEqual(self.http.post(self.fixture.url,data=data).status_code,302)
        self.assertEqual(self.count('service_reports'),2)

    def test_live_payroll_component_blocks_report_deletion(self):
        with self.module.app.app_context():
            connection = self.module.db()
            employee = self.fixture.f.people['Submitter']
            payment = connection.execute("insert into employee_payment_orders (payment_number,employee_id,payment_type,status,gross_amount,net_amount,source_type,created_by,created_at,updated_at) values ('SL-REPORT-TEST',?,'salary','draft',100,100,'manual',?,?,?)",(employee,self.fixture.f.people['Manager'],self.module.now(),self.module.now())).lastrowid
            connection.execute("insert into employee_payment_components (payment_order_id,employee_id,component_code,amount,source_type,daily_report_id,tax_category,created_at) values (?,?,'base_pay',100,'payroll_generation',?,'taxable_compensation',?)",(payment,employee,self.report_id,self.module.now()))
            connection.commit()
        self.assertEqual(self.http.post(f'/service-reports/{self.report_id}/delete').status_code,302)
        self.assertEqual(self.count('service_reports'),1)
        self.assertEqual(self.count('employee_payment_components'),1)
        with self.http.session_transaction() as session:
            self.assertTrue(any('员工工资组件引用' in message for category,message in session.get('_flashes',[])))

    def test_delete_removes_report_details_and_attachment(self):
        with self.module.app.app_context():
            connection = self.module.db()
            aid = connection.execute("insert into service_report_attachments (report_id,category,original_filename,stored_filename,content_type,uploaded_by,uploaded_at) values (?,'site','sample.pdf','nested/sample.pdf','application/pdf',?,?)",(self.report_id,self.fixture.f.people['Submitter'],self.module.now())).lastrowid
            attachment = connection.execute('select * from service_report_attachments where id=?',(aid,)).fetchone()
            path = Path(self.module.report_attachment_path(attachment))
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'%PDF-1.7\nTest')
            connection.commit()
        self.assertEqual(self.http.post(f'/service-reports/{self.report_id}/delete').status_code,302)
        self.assertFalse(path.exists())
        for table in ('service_reports','service_report_workers','service_report_saved_parts','service_report_replaced_parts','service_report_attachments'):
            self.assertEqual(self.count(table),0)

    def test_approved_settlement_guard_prevents_edit_and_delete(self):
        with patch.object(self.module,'report_locked_by_approved_reimbursement',return_value=True):
            self.assertEqual(self.http.post(f'/service-reports/{self.report_id}/edit',data=self.form(service_description='Blocked')).status_code,302)
            self.assertEqual(self.http.post(f'/service-reports/{self.report_id}/delete').status_code,302)
        self.assertEqual(self.count('service_reports'),1)
        with self.module.app.app_context():
            self.assertEqual(self.module.db().execute('select service_description from service_reports where id=?',(self.report_id,)).fetchone()['service_description'],'Original service')

    def test_detail_service_resolves_current_worker_parser(self):
        with self.module.app.test_request_context(method='POST',data=self.form()):
            with patch.object(self.module,'report_worker_rows_from_form',side_effect=ValueError('current parser')) as parser:
                with self.assertRaisesRegex(ValueError,'current parser'):
                    self.module.save_report_detail_rows(self.report_id)
        parser.assert_called_once_with()
