import unittest
import test_expense_on_behalf as fixture


class StaffCertificateReportTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ExpenseOnBehalfTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.http = self.fixture.http
        self.module = self.fixture.app
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("update users set role = 'admin' where id = ?", (self.fixture.people['Manager'],))
            for person, filename in [('Submitter','Electrical certificate.pdf'),('Submitter','Safety training.jpg'),('Beneficiary','Driving licence.pdf')]:
                db.execute('''insert into user_attachments (user_id,original_filename,stored_filename,uploaded_by,uploaded_at)
                              values (?,?,?,?,?)''', (self.fixture.people[person],filename,filename,self.fixture.people[person],self.module.now()))
            db.commit()

    def test_admin_can_filter_certificate_list_and_find_missing_attachments(self):
        self.fixture.login('Manager')
        response = self.http.get('/reports/user-certificates')
        self.assertEqual(response.status_code,200)
        self.assertIn('Electrical certificate.pdf',response.text)
        self.assertIn('Driving licence.pdf',response.text)
        filtered = self.http.get('/reports/user-certificates?q=Electrical').text
        self.assertIn('Electrical certificate.pdf',filtered)
        self.assertNotIn('Driving licence.pdf',filtered)
        selected = self.http.get('/reports/user-certificates?user_id='+str(self.fixture.people['Beneficiary'])).text
        self.assertIn('Driving licence.pdf',selected)
        self.assertNotIn('Electrical certificate.pdf',selected)
        missing = self.http.get('/reports/user-certificates?presence=no').text
        self.assertNotIn('Electrical certificate.pdf',missing)
        self.assertIn('没有附件',missing)

    def test_employee_cannot_query_other_people_certificates(self):
        response = self.http.get('/reports/user-certificates')
        self.assertIn('Electrical certificate.pdf',response.text)
        self.assertNotIn('Driving licence.pdf',response.text)
        filtered = self.http.get('/reports/user-certificates?user_id='+str(self.fixture.people['Beneficiary']))
        self.assertNotIn('Driving licence.pdf',filtered.text)


if __name__ == '__main__':
    unittest.main()
