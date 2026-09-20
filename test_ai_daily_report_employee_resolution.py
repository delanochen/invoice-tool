"""AI Daily Report - Employee Resolution Tolerant Matching Tests (v0.1.256)

User report (2026-09-19): "我和高阳和antonio自驾从家出发……" still failed to
find Antonio via the AI chat parsing path, even after v0.1.255 fixed the
manual search. Root cause in EmployeeResolutionService.resolve():

- exact match used `name = ?` — SQLite `=` is case-sensitive, so the typed
  "antonio" never matched a stored "Antonio";
- the LIKE fallback does no Unicode folding, so a stored "António" was
  unreachable;
- role eligibility used the raw stored role, so legacy role names
  ('user', 'external') were silently excluded.

Fix: fold (casefold + strip diacritics) both sides for exact and partial
matching; map legacy role aliases like app.normalized_role().

These tests drive the service directly (no app import besides init_db).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-resolution")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


class EmployeeResolutionFoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="employee_resolution_test_")
        os.environ["DATA_DIR"] = cls.temp_dir
        os.environ["SHARED_PHOTOS_DIR"] = os.path.join(cls.temp_dir, "shared-photos")
        Path(os.environ["SHARED_PHOTOS_DIR"]).mkdir(parents=True, exist_ok=True)

        import app as app_module
        cls.app_module = app_module
        app_module.DATA_DIR = cls.temp_dir
        app_module.DB_PATH = os.path.join(cls.temp_dir, "invoices.db")
        app_module.ATTACHMENTS_DIR = os.path.join(cls.temp_dir, "attachments")
        app_module.REPORT_ATTACHMENTS_DIR = os.path.join(
            cls.temp_dir, "service-report-attachments"
        )
        app_module.SHARED_PHOTOS_DIR = os.environ["SHARED_PHOTOS_DIR"]
        cls.app = app_module.app

        from ai_daily_report.employee_resolution import EmployeeResolutionService
        cls.svc_cls = EmployeeResolutionService

        with cls.app.app_context():
            app_module.init_db()
            db = app_module.db()
            users = [
                # id, email, name, role, active
                (800, "admin-res@test.com", "Admin Res", "admin", 1),
                (801, "antonio@test.com", "Antonio", "external_manager", 1),
                (802, "antonio.silva@test.com", "António Silva", "employee", 1),
                (803, "antonio.mendez@test.com", "antonio mendez", "employee", 1),
                (804, "legacy.user@test.com", "Legacy User", "user", 1),
                (805, "legacy.ext@test.com", "Legacy External", "external", 1),
                (806, "antonio.old@test.com", "Antonio Retired", "employee", 0),
                (807, "self.legacy@test.com", "Self Legacy", "user", 1),
            ]
            for uid, email, name, role, active in users:
                db.execute(
                    "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, 'x', ?, ?, ?)",
                    (uid, email, name, role, active, "2026-01-01T00:00:00Z"),
                )
            db.commit()

    @classmethod
    def tearDownClass(cls):
        # The shared database factory must remain callable for subsequent tests.
        pass

    def setUp(self):
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.svc = self.svc_cls(self.app_module.db(), 800, "Admin Res")

    def tearDown(self):
        self._ctx.pop()

    def test_01_lowercase_matches_stored_capitalized(self):
        """THE reported bug: typed 'antonio' resolves stored 'Antonio'."""
        result = self.svc.resolve("antonio")
        self.assertTrue(result.resolved, f"error={result.error}")
        self.assertEqual(result.user_id, 801)
        self.assertEqual(result.name, "Antonio")

    def test_02_uppercase_input(self):
        result = self.svc.resolve("ANTONIO")
        self.assertTrue(result.resolved)
        self.assertEqual(result.user_id, 801)

    def test_03_diacritics_input_matches_plain_stored(self):
        result = self.svc.resolve("António Silva")
        self.assertTrue(result.resolved)
        self.assertEqual(result.user_id, 802)

    def test_04_plain_input_matches_diacritics_stored(self):
        result = self.svc.resolve("Antonio Silva")
        self.assertTrue(result.resolved)
        self.assertEqual(result.user_id, 802)

    def test_05_partial_match_yields_candidates(self):
        """'anton' is not exact -> candidates, never auto-resolve."""
        result = self.svc.resolve("anton")
        self.assertFalse(result.resolved)
        self.assertTrue(result.clarification_required)
        cand_ids = {c["user_id"] for c in result.candidates}
        self.assertEqual(cand_ids, {801, 802, 803})  # inactive 806 excluded

    def test_06_legacy_role_user_resolvable(self):
        """Stored role 'user' behaves like employee (normalized)."""
        result = self.svc.resolve("Legacy User")
        self.assertTrue(result.resolved, f"error={result.error}")
        self.assertEqual(result.user_id, 804)

    def test_07_legacy_role_external_resolvable(self):
        """Stored role 'external' behaves like external_manager."""
        result = self.svc.resolve("Legacy External")
        self.assertTrue(result.resolved, f"error={result.error}")
        self.assertEqual(result.user_id, 805)

    def test_08_inactive_unresolvable(self):
        result = self.svc.resolve("Antonio Retired")
        self.assertFalse(result.resolved)
        self.assertEqual(result.error, "employee_not_found")

    def test_09_resolve_workers_batch(self):
        resolved, failures = self.svc.resolve_workers([
            {"name": "antonio", "transportation": "self_drive", "origin": None},
            {"name": "Admin Res", "transportation": "self_drive", "origin": None},
        ])
        self.assertEqual(len(resolved), 2, f"failures={[f.error for f in failures]}")
        self.assertEqual({r["user_id"] for r in resolved}, {800, 801})
        self.assertEqual(len(failures), 0)

    def test_10_self_reference_with_legacy_role(self):
        """'我' works even when the current user's stored role is legacy 'user'."""
        svc = self.svc_cls(self.app_module.db(), 807, "Self Legacy")
        result = svc.resolve("我")
        self.assertTrue(result.resolved, f"error={result.error}")
        self.assertEqual(result.user_id, 807)


if __name__ == "__main__":
    unittest.main()
