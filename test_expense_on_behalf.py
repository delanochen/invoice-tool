import importlib.util
import secrets
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class ExpenseOnBehalfTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        source = Path(self.temp.name) / 'app.py'
        shutil.copyfile(ROOT / 'app.py', source)
        spec = importlib.util.spec_from_file_location('expense_delegate_test', source)
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)
        self.app.app.config.update(TESTING=True, SECRET_KEY='test')
        self.app.app.template_folder = str(ROOT / 'templates')
        self.http = self.app.app.test_client()
        with self.app.app.app_context():
            db = self.app.db()
            self.people = {}
            for name, role, active in [('Submitter', 'employee', 1), ('Beneficiary', 'employee', 1),
                                       ('Unrelated', 'employee', 1), ('Manager', 'manager', 1),
                                       ('Disabled', 'employee', 0), ('External', 'external_employee', 1)]:
                self.people[name] = db.execute(
                    'insert into users (name,email,password_hash,role,is_active,created_at) values (?,?,?,?,?,?)',
                    (name, name.lower()+'@test.invalid', 'unused', role, active, self.app.now()),
                ).lastrowid
            self.order = db.execute(
                """insert into service_orders (order_number,client_name,site_address,client_order_number,
                   start_date,created_by,created_at) values ('SO-DELEGATE','Site','Address','ORDER','2026-09-01',?,?)""",
                (self.people['Submitter'], self.app.now()),
            ).lastrowid
            self.project = db.execute(
                """insert into projects (name,project_type,is_active,created_at)
                   values ('Accommodation/Lodging','expense',1,?)""", (self.app.now(),),
            ).lastrowid
            db.commit()
        self.login('Submitter')

    def login(self, name):
        with self.http.session_transaction() as session:
            session['user_id'] = self.people[name]

    def form(self, **overrides):
        data = {'save_token': secrets.token_urlsafe(24), 'action': 'submit',
                'project_id': str(self.project), 'item_amount': '50', 'item_description': 'Delegate receipt',
                'item_line_key': 'line-test', 'expense_date': '2026-09-01',
                'beneficiary_id': str(self.people['Beneficiary'])}
        data.update(overrides)
        return data

    def create(self, **overrides):
        response = self.http.post(f'/service-orders/{self.order}/expenses/new', data=self.form(**overrides))
        self.assertEqual(response.status_code, 302)
        with self.app.app.app_context():
            return dict(self.app.db().execute('select * from expenses order by id desc').fetchone())

    def test_create_preserves_actor_and_defaults_to_self(self):
        page = self.http.get(f'/service-orders/{self.order}/expenses/new')
        self.assertIn(f'value="{self.people["Submitter"]}" selected', page.get_data(as_text=True))
        expense = self.create(created_by=str(self.people['Unrelated']))
        self.assertEqual(expense['created_by'], self.people['Submitter'])
        self.assertEqual(expense['beneficiary_id'], self.people['Beneficiary'])
        data = self.form()
        del data['beneficiary_id']
        self.http.post(f'/service-orders/{self.order}/expenses/new', data=data)
        with self.app.app.app_context():
            row = self.app.db().execute('select * from expenses order by id desc').fetchone()
            self.assertEqual(row['beneficiary_id'], self.people['Submitter'])

    def test_invalid_or_external_recipient_is_rejected(self):
        for value in ('', 'garbage', '999999', str(self.people['Disabled']), str(self.people['External'])):
            with self.subTest(value=value):
                self.http.post(f'/service-orders/{self.order}/expenses/new', data=self.form(beneficiary_id=value))
                with self.app.app.app_context():
                    self.assertEqual(self.app.db().execute('select count(*) from expenses').fetchone()[0], 0)

    def test_both_participants_can_view_but_unrelated_cannot(self):
        expense = self.create()
        for person in ('Submitter', 'Beneficiary'):
            self.login(person)
            for url in (f'/expenses/{expense["id"]}', '/reports/expenses', '/expense-processing', f'/service-orders/{self.order}'):
                response = self.http.get(url)
                self.assertEqual(response.status_code, 200, url)
                self.assertIn(expense['expense_number'], response.get_data(as_text=True))
        self.login('Unrelated')
        self.assertEqual(self.http.get(f'/expenses/{expense["id"]}').status_code, 403)
        for url in ('/reports/expenses', '/expense-processing', f'/service-orders/{self.order}'):
            self.assertNotIn(expense['expense_number'], self.http.get(url).get_data(as_text=True))

    def test_person_filter_means_beneficiary_and_ai_respects_visibility(self):
        expense = self.create()
        for person in ('Submitter', 'Beneficiary', 'Unrelated'):
            self.login(person)
            page = self.http.get('/reports/expenses?person_id='+str(self.people['Beneficiary']))
            self.assertEqual(expense['expense_number'] in page.get_data(as_text=True), person != 'Unrelated')
            with self.app.app.test_request_context():
                self.app.g.user = self.app.db().execute('select * from users where id=?', (self.people[person],)).fetchone()
                result = self.app.ai_search_business_records({'domain':'expenses'})
                self.assertEqual(result['summary']['count'], 0 if person == 'Unrelated' else 1)
                if person != 'Unrelated':
                    self.assertEqual(result['records'][0]['beneficiary'], 'Beneficiary')
                    self.assertEqual(result['records'][0]['submitter'], 'Submitter')
        self.login('Manager')
        self.assertNotIn(expense['expense_number'], self.http.get('/reports/expenses?person_id='+str(self.people['Submitter'])).get_data(as_text=True))

    def test_approved_expense_merges_under_beneficiary_and_not_submitter(self):
        expense = self.create()
        self.login('Manager')
        self.assertEqual(self.http.post(f'/expenses/{expense["id"]}/approve').status_code, 302)
        with self.app.app.app_context():
            rows = self.app.merge_approved_expenses_into_customer_reimbursement([], self.order)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['worker_name'], 'Beneficiary')
            self.assertEqual(rows[0]['auto_lodging'], 50)
            totals = self.app.customer_reimbursement_totals(rows)
            self.assertEqual(totals['total_amount'], 50)

    def test_workflow_notifications_reach_both_without_duplicates(self):
        expense = self.create()
        self.login('Manager')
        self.http.post(f'/expenses/{expense["id"]}/return', data={'return_reason':'Check receipt'})
        self.login('Submitter')
        self.http.post(f'/expenses/{expense["id"]}/edit', data=self.form())
        self.login('Manager')
        self.http.post(f'/expenses/{expense["id"]}/approve')
        self.http.post('/expense-processing/action', data={'expense_id':expense['id'], 'action':'reimburse'})
        with self.app.app.app_context():
            for title in ('报销已被退回', '报销已审核通过', '报销已发放'):
                ids = [r['user_id'] for r in self.app.db().execute('select user_id from messages where title=?', (title,))]
                for name in ('Submitter','Beneficiary'):
                    self.assertEqual(ids.count(self.people[name]),1)
        # The same recipient/submitter is only notified once.
        self.login('Submitter')
        own = self.create(beneficiary_id=str(self.people['Submitter']))
        with self.app.app.app_context():
            self.app.notify_expense_participants(own, 'deduplicate', 'body', '/')
            self.assertEqual(self.app.db().execute("select count(*) from messages where title='deduplicate'").fetchone()[0],1)

    def test_edit_can_change_attribution_but_not_original_actor(self):
        expense = self.create(action='save')
        self.http.post(f'/expenses/{expense["id"]}/edit', data=self.form(action='save', beneficiary_id=str(self.people['Unrelated'])))
        with self.app.app.app_context():
            row = self.app.db().execute('select * from expenses where id=?',(expense['id'],)).fetchone()
            self.assertEqual(row['beneficiary_id'],self.people['Unrelated'])
            self.assertEqual(row['created_by'],self.people['Submitter'])
        self.login('Beneficiary')
        self.assertEqual(self.http.get(f'/expenses/{expense["id"]}').status_code,403)
        self.login('Unrelated')
        self.assertEqual(self.http.post(f'/expenses/{expense["id"]}/edit',data=self.form()).status_code,403)

    def test_recipient_read_access_does_not_grant_edit_or_delete(self):
        expense = self.create(action='save')
        with self.app.app.app_context():
            db=self.app.db()
            aid=db.execute("""insert into expense_attachments (expense_id,original_filename,stored_filename,uploaded_by,uploaded_at)
                values (?,'receipt.pdf','receipt.pdf',?,?)""",(expense['id'],self.people['Submitter'],self.app.now())).lastrowid
            (Path(self.app.expense_attachment_dir(expense['id']))/'receipt.pdf').write_bytes(b'%PDF-test')
            db.commit()
        self.login('Beneficiary')
        with self.http.get(f'/expense-attachments/{aid}/download') as response:
            self.assertEqual(response.status_code,200)
        self.assertEqual(self.http.post(f'/expense-attachments/{aid}/delete').status_code,403)
        self.assertEqual(self.http.post(f'/expenses/{expense["id"]}/delete').status_code,403)
        self.assertEqual(self.http.post(f'/expenses/{expense["id"]}/approve').status_code,403)

    def test_legacy_migration_is_repeatable_and_preserves_delegation(self):
        expense=self.create(action='save')
        with self.app.app.app_context():
            db=self.app.db()
            db.execute('drop index idx_expenses_beneficiary')
            db.execute('alter table expenses drop column beneficiary_id')
            db.commit()
        self.app.init_db()
        with self.app.app.app_context():
            db=self.app.db()
            self.assertEqual(db.execute('select beneficiary_id from expenses').fetchone()[0],self.people['Submitter'])
            db.execute('update expenses set beneficiary_id=?',(self.people['Beneficiary'],))
            db.commit()
        self.app.init_db()
        with self.app.app.app_context():
            self.assertEqual(self.app.db().execute('select beneficiary_id from expenses').fetchone()[0],self.people['Beneficiary'])

    def test_disabled_existing_recipient_can_be_retained_and_locked_expense_cannot_change(self):
        expense=self.create(action='save')
        with self.app.app.app_context():
            self.app.db().execute('update users set is_active=0 where id=?',(self.people['Beneficiary'],))
            self.app.db().commit()
        self.assertEqual(self.http.get(f'/expenses/{expense["id"]}/edit').status_code,200)
        self.http.post(f'/expenses/{expense["id"]}/edit',data=self.form())
        self.http.post(f'/expenses/{expense["id"]}/edit',data=self.form(beneficiary_id=str(self.people['Unrelated'])))
        with self.app.app.app_context():
            row=self.app.db().execute('select * from expenses').fetchone()
            self.assertEqual(row['status'],'submitted')
            self.assertEqual(row['beneficiary_id'],self.people['Beneficiary'])


if __name__ == '__main__':
    unittest.main()
