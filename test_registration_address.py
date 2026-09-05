import unittest
import test_expense_on_behalf as fixture


class RegistrationAddressTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ExpenseOnBehalfTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        with self.fixture.app.app.app_context():
            self.fixture.app.db().execute("update users set role = 'admin' where id = ?",
                                          (self.fixture.people['Manager'],))
            self.fixture.app.db().commit()
        self.http = self.fixture.http
        with self.http.session_transaction() as session:
            session.clear()

    def register(self, role, **extra):
        data = dict(name='New Person', registration_email=role+'@new.invalid',
                    registration_password='test-password-123',
                    registration_password_confirm='test-password-123',
                    account_type=role, country_code='US', phone='+1 713 555 1234',
                    preferred_communication_language='en',
                    communication_language=['en', 'es'])
        data.update(extra)
        return self.http.post('/register', data=data)

    def test_missing_and_whitespace_addresses_rejected_for_both_account_types(self):
        for role in ('employee', 'external_employee'):
            for values in ({}, {'address': ''}, {'address': '  \n '}):
                with self.subTest(role=role, values=values):
                    response = self.register(role, **values)
                    self.assertTrue(response.location.endswith('/register'))
                    with self.fixture.app.app.app_context():
                        self.assertIsNone(self.fixture.app.db().execute(
                            'select id from users where email = ?', (role+'@new.invalid',)
                        ).fetchone())

    def test_address_saved_and_available_on_profile_edit(self):
        for role in ('employee', 'external_employee'):
            response = self.register(role, address='  123 Test Street\nApartment 4  ')
            self.assertTrue(response.location.endswith('/login'))
            with self.fixture.app.app.app_context():
                user = self.fixture.app.db().execute(
                    'select * from users where email = ?', (role+'@new.invalid',)
                ).fetchone()
                self.assertEqual(user['address'], '123 Test Street\nApartment 4')
                self.assertEqual(user['is_active'], 0)
                self.assertEqual(user['role'], role)
                self.assertEqual(user['phone'], '+17135551234')
                self.assertEqual(user['phone_verified'], 0)
                self.assertEqual(user['preferred_communication_language'], 'en')
                self.assertEqual(user['communication_languages'], 'en,es')
                user_id = user['id']
            self.fixture.login('Manager')
            page = self.http.get(f'/users/{user_id}/edit')
            self.assertEqual(page.status_code, 200)
            self.assertIn('123 Test Street\nApartment 4', page.text)
            with self.http.session_transaction() as session:
                session.clear()

    def test_phone_is_required_and_normalized(self):
        for phone in ('', '1234'):
            with self.subTest(phone=phone):
                response = self.register('employee', address='123 Test Street', phone=phone)
                self.assertTrue(response.location.endswith('/register'))
        response = self.register('employee', address='123 Test Street', phone='(713) 555-9876')
        self.assertTrue(response.location.endswith('/login'))
        with self.fixture.app.app.app_context():
            user = self.fixture.app.db().execute(
                "select * from users where email = 'employee@new.invalid'"
            ).fetchone()
            self.assertEqual(user['phone'], '+17135559876')


if __name__ == '__main__':
    unittest.main()
