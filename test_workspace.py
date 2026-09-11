import unittest
from pathlib import Path
from test_expense_on_behalf import ExpenseOnBehalfTest


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.fixture = ExpenseOnBehalfTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.http = self.fixture.http

    def test_authenticated_shell_and_existing_page(self):
        page = self.http.get('/workspace')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'workspaceTabs', page.data)
        self.assertIn('no-store', page.headers['Cache-Control'])
        embedded = self.http.get('/service-orders', headers={'Sec-Fetch-Dest': 'iframe'})
        self.assertEqual(embedded.status_code, 200)
        self.assertIn(b'workspace-embedded', embedded.data)

    def test_login_required(self):
        with self.http.session_transaction() as session:
            session.clear()
        self.assertEqual(self.http.get('/workspace').status_code, 302)

    def test_redirect_bootstrap_preserves_flash(self):
        with self.http.session_transaction() as session:
            session['_flashes'] = [('success', 'workspace-flash-test')]
        self.http.get('/service-orders', headers={'Sec-Fetch-Dest': 'document'})
        page = self.http.get('/workspace', headers={'Sec-Fetch-Dest': 'document'})
        self.assertIn(b'workspace-flash-test', page.data)


if __name__ == '__main__':
    unittest.main()
