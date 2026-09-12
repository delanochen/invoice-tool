import importlib.util
import os
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent


class AdminInitializationSecurityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        source = Path(self.temp.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", source)
        spec = importlib.util.spec_from_file_location(
            "admin_initialization_security_test",
            source,
        )
        self.app = importlib.util.module_from_spec(spec)
        with patch.dict(
            os.environ,
            {
                "ADMIN_EMAIL": "initial-admin@example.test",
                "ADMIN_PASSWORD": "initially-strong-password",
                "APP_VERSION": "0.0.0",
                "REQUIRE_DATA_DIRECTORY_IDENTITY": "0",
            },
            clear=False,
        ):
            spec.loader.exec_module(self.app)
        self.app.app.config.update(TESTING=True, SECRET_KEY="test")

    def count_admins(self):
        with self.app.app.app_context():
            return self.app.db().execute(
                "select count(*) from users where role = 'admin'"
            ).fetchone()[0]

    @contextmanager
    def credentials(self, email=None, password=None):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADMIN_EMAIL", None)
            os.environ.pop("ADMIN_PASSWORD", None)
            if email is not None:
                os.environ["ADMIN_EMAIL"] = email
            if password is not None:
                os.environ["ADMIN_PASSWORD"] = password
            yield

    def test_existing_admin_does_not_require_credentials_or_create_another_admin(self):
        self.assertEqual(self.count_admins(), 1)
        with self.credentials():
            self.app.init_db()
        self.assertEqual(self.count_admins(), 1)

    def test_existing_admin_ignores_changed_email(self):
        with self.credentials(email="different-admin@example.test"):
            self.app.init_db()
        self.assertEqual(self.count_admins(), 1)

    def reset_without_admin(self, email="new-admin@example.test", password="strong-password"):
        with self.app.app.app_context():
            connection = self.app.db()
            connection.execute("delete from users where role = 'admin'")
            connection.commit()
        return self.credentials(email=email, password=password)

    def assert_init_fails_without_creating_admin(self, email=None, password=None):
        with self.reset_without_admin(email=email, password=password):
            with self.assertRaises(RuntimeError):
                self.app.init_db()
        self.assertEqual(self.count_admins(), 0)

    def test_missing_password_fails_closed(self):
        self.assert_init_fails_without_creating_admin(password=None)

    def test_missing_email_fails_closed(self):
        self.assert_init_fails_without_creating_admin(email=None)

    def test_empty_password_fails_closed(self):
        self.assert_init_fails_without_creating_admin(password="")

    def test_default_password_fails_closed(self):
        self.assert_init_fails_without_creating_admin(password="change-me-now")

    def test_valid_credentials_create_one_hashed_admin(self):
        with self.reset_without_admin(
            email="New-Admin@Example.Test",
            password="strong-password",
        ):
            self.app.init_db()
        with self.app.app.app_context():
            row = self.app.db().execute(
                "select email, password_hash from users where role = 'admin'"
            ).fetchone()
        self.assertEqual(row["email"], "new-admin@example.test")
        self.assertEqual(self.count_admins(), 1)
        self.assertNotEqual(row["password_hash"], "strong-password")
        self.assertTrue(self.app.check_password_hash(row["password_hash"], "strong-password"))

    def test_existing_non_admin_email_fails_without_modifying_user(self):
        with self.app.app.app_context():
            connection = self.app.db()
            connection.execute(
                """
                insert into users (name, email, password_hash, role, created_at)
                values (?, ?, ?, ?, ?)
                """,
                ("Existing User", "existing@example.test", "unchanged", "employee", self.app.now()),
            )
            connection.commit()
        with self.reset_without_admin(
            email="EXISTING@example.test",
            password="strong-password",
        ):
            with self.assertRaises(RuntimeError):
                self.app.init_db()
        with self.app.app.app_context():
            row = self.app.db().execute(
                "select name, email, password_hash, role from users where lower(email) = ?",
                ("existing@example.test",),
            ).fetchone()
        self.assertEqual(
            tuple(row),
            ("Existing User", "existing@example.test", "unchanged", "employee"),
        )
        self.assertEqual(self.count_admins(), 0)


if __name__ == "__main__":
    unittest.main()
