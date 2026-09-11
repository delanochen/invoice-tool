import unittest
from io import BytesIO
from zipfile import ZipFile
from test_expense_attachment_transfer import ExpenseAttachmentTransferTest as Fixture


class SettlementExcelTest(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(); self.f.setUpClass(); self.addCleanup(self.f.tearDownClass); self.f.setUp()

    def test_values_include_transferred_expenses(self):
        with self.f.module.app.app_context():
            db = self.f.module.db()
            db.execute("delete from customer_reimbursement_items where customer_reimbursement_id=?", (self.f.reimbursement_id,))
            db.execute("insert into customer_reimbursement_items(customer_reimbursement_id,worker_name,project_date,lodging,auto_lodging,total) values (?,'Excel Worker','2026-09-10',12,123,135)", (self.f.reimbursement_id,))
            db.commit()
        response = self.f.client.get(f'/customer-reimbursements/{self.f.reimbursement_id}/download.xlsx')
        self.assertEqual(response.status_code, 200)
        with ZipFile(BytesIO(response.data)) as archive:
            sheet = archive.read('xl/worksheets/sheet1.xml').decode()
            self.assertIn('Excel Worker', sheet)
            self.assertIn('2026-09-10', sheet)
            self.assertIn('135', sheet)
        with self.f.client.session_transaction() as session: session.clear()
        self.assertEqual(self.f.client.get(f'/customer-reimbursements/{self.f.reimbursement_id}/download.xlsx').status_code,302)
