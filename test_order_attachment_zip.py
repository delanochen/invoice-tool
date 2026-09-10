import unittest
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
from test_expense_attachment_transfer import ExpenseAttachmentTransferTest as Fixture

class OrderZipTest(unittest.TestCase):
    def setUp(self):
        self.f=Fixture();self.f.setUpClass();self.addCleanup(self.f.tearDownClass);self.f.setUp()
        self.m=self.f.module
        self.m.SHARED_PHOTOS_DIR=str(Path(self.f.temp_dir.name)/'shared')

    def test_six_categories_contents_and_other_order_isolation(self):
        with self.m.app.app_context():
            db=self.m.db()
            report=db.execute("""insert into service_reports(service_order_id,report_date,actual_work_date,created_by,created_at,updated_at)
                values (?,'2026-09-01','2026-09-01',?,'now','now')""",(self.f.order_id,self.f.user_id)).lastrowid
            for category in ('self_check','arrival','departure','site','mileage_proof'):
                db.execute("""insert into service_report_attachments(report_id,category,original_filename,stored_filename,content_type,uploaded_by,uploaded_at)
                    values (?,?,?,?,'image/jpeg',?,'now')""",(report,category,'same.jpg',category+'.jpg',self.f.user_id))
                path=Path(self.m.REPORT_ATTACHMENTS_DIR)/str(report)/(category+'.jpg');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(category.encode())
            reimbursement=db.execute('select * from customer_reimbursements where id=?',(self.f.reimbursement_id,)).fetchone()
            path=Path(self.m.customer_reimbursement_file_path(reimbursement));path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'%PDF-test')
            db.commit()
        for order in ('SO-TEST','OTHER-ORDER'):
            path=Path(self.m.SHARED_PHOTOS_DIR)/order/'pictures'/'2026-09-01'/'server.jpg';path.parent.mkdir(parents=True);path.write_bytes(order.encode())
        response=self.f.client.get(f'/service-orders/{self.f.order_id}/attachments.zip')
        self.assertEqual(response.status_code,200)
        with ZipFile(BytesIO(response.data)) as archive:
            names=archive.namelist()
            self.assertEqual({name for name in names if name.endswith('/')},{name+'/' for name in ('自检照片','进场照片','离场照片','现场工作照片','里程佐证','工单结算')})
            self.assertFalse(any(name.startswith('报销佐证/') for name in names))
            self.assertIn('工单结算/settlement.pdf',names)
            self.assertTrue(any(archive.read(name)==b'SO-TEST' for name in names if not name.endswith('/')))
            self.assertFalse(any(archive.read(name)==b'OTHER-ORDER' for name in names if not name.endswith('/')))
        response.close()

    def test_missing_files_are_reported_and_login_required(self):
        response=self.f.client.get(f'/service-orders/{self.f.order_id}/attachments.zip')
        with ZipFile(BytesIO(response.data)) as archive:
            self.assertIn('工单结算/缺失文件说明.txt',archive.namelist())
        response.close()
        with self.f.client.session_transaction() as session: session.clear()
        self.assertEqual(self.f.client.get(f'/service-orders/{self.f.order_id}/attachments.zip').status_code,302)

if __name__=='__main__':unittest.main()
