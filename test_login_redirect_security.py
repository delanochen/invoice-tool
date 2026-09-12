import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent


class LoginRedirectSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        module_path = Path(cls.temp.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location(
            "login_redirect_security_test",
            module_path,
        )
        cls.module = importlib.util.module_from_spec(spec)
        with patch.dict(
            os.environ,
            {
                "ADMIN_EMAIL": "login-admin@example.test",
                "ADMIN_PASSWORD": "login-test-password",
                "APP_VERSION": "0.0.0",
                "REQUIRE_DATA_DIRECTORY_IDENTITY": "0",
            },
            clear=False,
        ):
            spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test")

    def setUp(self):
        self.client = self.module.app.test_client()
        with self.module.app.app_context():
            self.module.db().execute("delete from users where role != 'admin'")
            self.module.db().commit()

    def login(self, next_target=None, password="login-test-password"):
        path = "/login"
        if next_target is not None:
            path = f"/login?next={next_target}"
        return self.client.post(
            path,
            data={
                "email": "login-admin@example.test",
                "password": password,
            },
            follow_redirects=False,
        )

    def test_safe_local_targets_are_preserved(self):
        for target in ("/invoices/123", "/expenses", "/field/orders/456?tab=photos"):
            with self.subTest(target=target):
                response = self.login(target)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], target)

    def test_missing_next_uses_dashboard(self):
        response = self.login()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_external_targets_use_dashboard(self):
        targets = (
            "https://evil.example",
            "http://evil.example",
            "//evil.example",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            r"\evil.example",
            r"\\evil.example",
            "%2F%2Fevil.example",
            "%5C%5Cevil.example",
            "%252F%252Fevil.example",
        )
        for target in targets:
            with self.subTest(target=target):
                response = self.login(target)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], "/")

    def test_invalid_control_character_target_uses_dashboard(self):
        response = self.login("/invoices/\x00/123")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_failed_login_does_not_redirect(self):
        response = self.login("https://evil.example", password="wrong-password")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("evil.example", response.headers.get("Location", ""))

    def test_safe_redirect_helper_rejects_non_strings_and_whitespace(self):
        self.assertFalse(self.module.is_safe_redirect_target(None))
        self.assertFalse(self.module.is_safe_redirect_target(""))
        self.assertTrue(self.module.is_safe_redirect_target("  /expenses  "))
        self.assertFalse(self.module.is_safe_redirect_target("/\n/evil.example"))


if __name__ == "__main__":
    unittest.main()
