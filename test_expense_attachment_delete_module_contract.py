"""Attachment deletion endpoint and file/permission boundaries."""
from pathlib import Path
import unittest
import app as module
import test_expense_attachment_module_contract as attachment_fixtures


class ExpenseAttachmentDeleteContractTest(unittest.TestCase):
    def fixture(self):
        helper=attachment_fixtures.ExpenseAttachmentContractTest()
        self.addCleanup(helper.doCleanups)
        return helper.fixture()

    def test_endpoint_identity_and_post_only(self):
        rules=[r for r in module.app.url_map.iter_rules() if r.endpoint=='delete_expense_attachment']
        self.assertEqual(len(rules),1)
        self.assertEqual(rules[0].rule,'/expense-attachments/<int:attachment_id>/delete')
        self.assertEqual(rules[0].methods,{'POST','OPTIONS'})
        self.assertIs(module.app.view_functions['delete_expense_attachment'],module.delete_expense_attachment)

    def test_beneficiary_and_submitted_denied_returned_manager_can_delete(self):
        fixture,id,folder=self.fixture()
        app=fixture.app
        url=f'/expense-attachments/{id}/delete'
        with app.app.app_context():
            attachment=app.db().execute('select * from expense_attachments where id=?',(id,)).fetchone()
            expense_id=attachment['expense_id']
            path=Path(app.expense_attachment_path(attachment))
        fixture.login('Beneficiary')
        self.assertEqual(fixture.http.post(url).status_code,403)
        self.assertTrue(path.exists())
        fixture.login('Submitter')
        with app.app.app_context():
            app.db().execute("update expenses set status='submitted' where id=?",(expense_id,))
            app.db().commit()
        self.assertEqual(fixture.http.post(url).status_code,403)
        self.assertTrue(path.exists())
        with app.app.app_context():
            app.db().execute("update expenses set status='returned' where id=?",(expense_id,))
            app.db().commit()
        fixture.login('Manager')
        self.assertEqual(fixture.http.post(url).status_code,302)
        self.assertFalse(path.exists())
        with app.app.app_context():
            self.assertIsNone(app.db().execute('select * from expense_attachments where id=?',(id,)).fetchone())

    def test_missing_file_still_removes_record_and_missing_id_is_404(self):
        fixture,id,_=self.fixture()
        app=fixture.app
        with app.app.app_context():
            attachment=app.db().execute('select * from expense_attachments where id=?',(id,)).fetchone()
            Path(app.expense_attachment_path(attachment)).unlink()
        self.assertEqual(fixture.http.post(f'/expense-attachments/{id}/delete').status_code,302)
        with app.app.app_context():
            self.assertIsNone(app.db().execute('select * from expense_attachments where id=?',(id,)).fetchone())
        self.assertEqual(fixture.http.post(f'/expense-attachments/{id}/delete').status_code,404)
