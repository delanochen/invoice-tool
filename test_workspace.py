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

    def test_redirect_bootstrap_consumes_success_flash(self):
        with self.http.session_transaction() as session:
            session['_flashes'] = [('success', 'workspace-flash-test')]
        self.http.get('/service-orders', headers={'Sec-Fetch-Dest': 'document'})
        page = self.http.get('/workspace', headers={'Sec-Fetch-Dest': 'document'})
        self.assertNotIn(b'workspace-flash-test', page.data)

    def test_browser_load_failures_get_friendly_retry_ui(self):
        js = (Path(__file__).resolve().parent / 'static' / 'workspace.js').read_text(encoding='utf-8')
        css = (Path(__file__).resolve().parent / 'static' / 'workspace.css').read_text(encoding='utf-8')

        self.assertIn('function pageLoadFailed(tab)', js)
        self.assertIn('const AUTO_RETRY_DELAYS = [1200, 3000, 7000]', js)
        self.assertIn('const LOAD_TIMEOUT = 15000', js)
        self.assertIn('setTimeout(() => pageLoadFailed(tab), LOAD_TIMEOUT)', js)
        self.assertIn('页面加载失败', js)
        self.assertIn('Cloudflare 隧道短暂中断', js)
        self.assertIn('data-workspace-retry', js)
        self.assertIn("frame.addEventListener('error'", js)
        self.assertIn("window.addEventListener('online', retryFailedTabs)", js)
        self.assertIn("document.addEventListener('visibilitychange'", js)
        self.assertIn('.workspace-load-failure[hidden]', css)


if __name__ == '__main__':
    unittest.main()
