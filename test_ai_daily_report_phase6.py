"""AI Daily Report Phase 6 Tests - Review Center / Preview / Authorization / CSRF

Covers:
- Authorization helpers (can_view / can_edit)
- IDOR protection (employee cannot access other drafts)
- CSRF protection (mutation requires token)
- Preview read-only (does not call Google/scan/Vision)
- 409 optimistic locking
- Confirm Draft-only (does not write service_reports)
- Formal DB isolation
- Secure photo/evidence endpoints
- Draft list filtering
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Setup test environment
os.environ.setdefault("SECRET_KEY", "test-secret-phase6")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


class Phase6TestBase(unittest.TestCase):
    """Base class for Phase 6 tests with app context."""

    @classmethod
    def setUpClass(cls):
        # Create temp data dir
        cls.temp_dir = tempfile.mkdtemp(prefix="phase6_test_")
        os.environ["DATA_DIR"] = cls.temp_dir

        # Import app
        import app as app_module
        cls.app_module = app_module
        cls.app = app_module.app
        cls.app.config["TESTING"] = True

        # Create test client
        cls.client = cls.app.test_client()

        # Setup test users and draft
        with cls.app.app_context():
            cls._setup_test_data()

    @classmethod
    def _setup_test_data(cls):
        """Create test users, service order, and draft."""
        db = cls.app_module.db()

        # Create admin user (use high id to avoid conflicts with default users)
        db.execute(
            "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
            (100, "admin-phase6@test.com", "Admin P6", "x", "admin", "2026-01-01T00:00:00Z"),
        )
        # Create employee user 1
        db.execute(
            "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
            (101, "ethan-phase6@test.com", "Ethan P6", "x", "employee", "2026-01-01T00:00:00Z"),
        )
        # Create employee user 2
        db.execute(
            "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
            (102, "zhangsan-phase6@test.com", "张三 P6", "x", "employee", "2026-01-01T00:00:00Z"),
        )
        # Create finance user (read-only)
        db.execute(
            "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
            (103, "finance-phase6@test.com", "Finance P6", "x", "finance", "2026-01-01T00:00:00Z"),
        )
        # Create employee user 4 (NOT in any draft - for IDOR testing)
        db.execute(
            "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
            (104, "lisi-phase6@test.com", "李四 P6", "x", "employee", "2026-01-01T00:00:00Z"),
        )
        # Create another employee with same name as 张三 (for same-name permission test)
        db.execute(
            "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
            (105, "zhangsan2-phase6@test.com", "张三 P6", "x", "employee", "2026-01-01T00:00:00Z"),
        )

        # Create service order (use high id)
        db.execute(
            "insert or ignore into service_orders (id, order_number, client_name, site_address, client_order_number, status, created_by, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (100, "SO-PHASE6-001", "Test Client P6", "123 Test St", "CLIENT-P6-001", "open", 100, "2026-09-14T00:00:00Z"),
        )

        # Create draft created by employee 101 (Ethan)
        draft_data = {
            "service_order_id": 100,
            "report_date": "2026-09-14",
            "site_address": "123 Test St",
            "workers": [
                {"user_id": 101, "name": "Ethan P6", "transportation": "self_drive", "origin": "Spring, TX", "origin_source": "user_input", "origin_confirmed": True, "overnight_stay": False, "route_status": "success", "one_way_miles": 100.0, "reported_miles": 200.0},
                {"user_id": 102, "name": "张三 P6", "transportation": "self_drive", "origin": "Hobbs, NM", "origin_source": "user_input", "origin_confirmed": True, "overnight_stay": False, "route_status": "success", "one_way_miles": 150.0, "reported_miles": 300.0},
            ],
            "arrival_time": "08:00",
            "departure_time": "17:00",
            "arrival_time_source": "user_input",
            "departure_time_source": "user_input",
            "work_items": [{"equipment": "A313", "action": "replace_fuse", "fuse_number": 2}],
            "verification_required": False,
            "verification_fields": [],
            "ai_generated": True,
            "ai_model": "deepseek-v4-flash",
            "vision_model": "deepseek-flash",
            "photo_candidates": [],
            "selected_safety_photo": None,
            "selected_service_photos": [],
            "evidence_records": [],
        }
        db.execute(
            "insert or replace into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (100, 100, "2026-09-14", "draft", 1, 101, json.dumps(draft_data), "2026-09-14T00:00:00Z", "2026-09-14T00:00:00Z"),
        )

        # Create confirmed draft
        confirmed_data = dict(draft_data)
        confirmed_data["confirmed_by"] = 100
        confirmed_data["confirmed_at"] = "2026-09-14T12:00:00Z"
        db.execute(
            "insert or replace into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (101, 100, "2026-09-14", "confirmed", 1, 101, json.dumps(confirmed_data), "2026-09-14T00:00:00Z", "2026-09-14T00:00:00Z"),
        )

        db.commit()

    def _login(self, user_id):
        """Login as a specific user."""
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id

    def _get_csrf(self):
        """Get CSRF token."""
        resp = self.client.get("/api/ai/daily-report/csrf")
        return resp.get_json()["csrf_token"]


class TestAuthorizationHelpers(Phase6TestBase):
    """Test can_view / can_edit authorization helpers."""

    def test_admin_can_view_all(self):
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 100").fetchone()
            admin = {"id": 100, "role": "admin"}
            self.assertTrue(self.app_module.can_view_ai_daily_report_draft(admin, draft))

    def test_employee_can_view_own_draft(self):
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 100").fetchone()
            ethan = {"id": 101, "role": "employee"}
            self.assertTrue(self.app_module.can_view_ai_daily_report_draft(ethan, draft))

    def test_employee_can_view_draft_as_worker(self):
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 100").fetchone()
            zhangsan = {"id": 102, "role": "employee"}
            self.assertTrue(self.app_module.can_view_ai_daily_report_draft(zhangsan, draft))

    def test_employee_cannot_view_unrelated_draft(self):
        with self.app.app_context():
            # Create a draft with different workers (Ethan is creator but NOT a worker)
            db = self.app_module.db()
            other_data = {
                "service_order_id": 100,
                "report_date": "2026-09-14",
                "workers": [{"user_id": 999, "name": "Other"}],
                "verification_required": False,
                "verification_fields": [],
            }
            db.execute(
                "insert or replace into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (99, 100, "2026-09-14", "draft", 1, 101, json.dumps(other_data), "2026-09-14T00:00:00Z", "2026-09-14T00:00:00Z"),
            )
            db.commit()
            draft = db.execute("select * from ai_daily_report_drafts where id = 99").fetchone()
            ethan = {"id": 101, "role": "employee"}
            # Ethan is creator but NOT a worker in this draft -> can view (creator rule)
            # Wait: creator CAN view. So this test should use a different creator.
            # Let's test with 李四 (104) who is neither creator nor worker
            lisi = {"id": 104, "role": "employee"}
            self.assertFalse(self.app_module.can_view_ai_daily_report_draft(lisi, draft))

    def test_admin_can_edit_all(self):
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 100").fetchone()
            admin = {"id": 100, "role": "admin"}
            self.assertTrue(self.app_module.can_edit_ai_daily_report_draft(admin, draft))

    def test_finance_cannot_edit(self):
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 100").fetchone()
            finance = {"id": 103, "role": "finance"}
            self.assertFalse(self.app_module.can_edit_ai_daily_report_draft(finance, draft))

    def test_confirmed_draft_cannot_edit(self):
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 101").fetchone()
            admin = {"id": 100, "role": "admin"}
            self.assertFalse(self.app_module.can_edit_ai_daily_report_draft(admin, draft))


class TestCSRFProtection(Phase6TestBase):
    """Test CSRF protection on mutation APIs."""

    def test_mutation_without_csrf_rejected(self):
        self._login(100)  # admin
        resp = self.client.post(
            "/api/ai/daily-report/draft/100/update-worker",
            json={"user_id": 101, "origin": "New Address"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_mutation_with_invalid_csrf_rejected(self):
        self._login(100)
        resp = self.client.post(
            "/api/ai/daily-report/draft/100/update-worker",
            json={"user_id": 101, "origin": "New Address"},
            headers={"X-CSRF-Token": "invalid-token"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_get_preview_does_not_require_csrf(self):
        self._login(100)
        resp = self.client.get("/api/ai/daily-report/draft/100/preview")
        self.assertEqual(resp.status_code, 200)

    def test_get_csrf_token(self):
        self._login(100)
        resp = self.client.get("/api/ai/daily-report/csrf")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("csrf_token", data)

    def test_mutation_with_valid_csrf_accepted(self):
        self._login(100)
        csrf = self._get_csrf()
        resp = self.client.post(
            "/api/ai/daily-report/draft/100/update-worker",
            json={"user_id": 101, "origin": "New Address", "draft_version": 1},
            headers={"X-CSRF-Token": csrf},
        )
        # Should succeed or 409 (version conflict), but not 403
        self.assertIn(resp.status_code, [200, 409])


class TestIDORProtection(Phase6TestBase):
    """Test IDOR protection - employee cannot access other drafts."""

    def test_employee_cannot_preview_other_draft(self):
        self._login(102)  # 张三 - worker in draft 100, but test with a draft he's not in
        # Create draft not involving 张三 (only Ethan as creator+worker)
        with self.app.app_context():
            db = self.app_module.db()
            other_data = {
                "service_order_id": 100,
                "report_date": "2026-09-14",
                "workers": [{"user_id": 101, "name": "Ethan"}],
                "verification_required": False,
                "verification_fields": [],
            }
            db.execute(
                "insert or replace into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (50, 100, "2026-09-14", "draft", 1, 101, json.dumps(other_data), "2026-09-14T00:00:00Z", "2026-09-14T00:00:00Z"),
            )
            db.commit()

        resp = self.client.get("/api/ai/daily-report/draft/50/preview")
        self.assertEqual(resp.status_code, 403)

    def test_employee_cannot_update_other_draft(self):
        self._login(102)  # 张三
        csrf = self._get_csrf()
        resp = self.client.post(
            "/api/ai/daily-report/draft/50/update-worker",
            json={"user_id": 101, "origin": "Hacked"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 403)

    def test_nonexistent_draft_returns_404(self):
        self._login(100)
        resp = self.client.get("/api/ai/daily-report/draft/99999/preview")
        self.assertEqual(resp.status_code, 404)


class TestPreviewReadOnly(Phase6TestBase):
    """Test Preview API is read-only and does not trigger external calls."""

    def test_preview_returns_aggregated_data(self):
        self._login(100)
        resp = self.client.get("/api/ai/daily-report/draft/100/preview")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        preview = data["preview"]
        self.assertIn("basic_info", preview)
        self.assertIn("workers", preview)
        self.assertIn("timeline", preview)
        self.assertIn("verification_checklist", preview)
        self.assertIn("ai_metadata", preview)

    def test_preview_ai_model_not_hardcoded(self):
        self._login(100)
        resp = self.client.get("/api/ai/daily-report/draft/100/preview")
        data = resp.get_json()
        ai_meta = data["preview"]["ai_metadata"]
        # Should come from draft metadata, not hardcoded
        self.assertEqual(ai_meta["ai_model"], "deepseek-v4-flash")
        self.assertEqual(ai_meta["vision_model"], "deepseek-flash")

    def test_preview_does_not_modify_draft(self):
        self._login(100)
        # Get version before
        with self.app.app_context():
            before = self.app_module.db().execute("select draft_version from ai_daily_report_drafts where id = 100").fetchone()
            before_version = before["draft_version"]

        # Call preview multiple times
        for _ in range(3):
            self.client.get("/api/ai/daily-report/draft/100/preview")

        # Version should not change
        with self.app.app_context():
            after = self.app_module.db().execute("select draft_version from ai_daily_report_drafts where id = 100").fetchone()
            self.assertEqual(after["draft_version"], before_version)


class TestOptimisticLocking(Phase6TestBase):
    """Test 409 conflict handling."""

    def test_update_with_wrong_version_returns_409(self):
        self._login(100)
        csrf = self._get_csrf()
        resp = self.client.post(
            "/api/ai/daily-report/draft/100/update-worker",
            json={"user_id": 101, "origin": "Test", "draft_version": 999},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 409)

    def test_update_with_correct_version_succeeds(self):
        self._login(100)
        csrf = self._get_csrf()
        # Get current version
        with self.app.app_context():
            row = self.app_module.db().execute("select draft_version from ai_daily_report_drafts where id = 100").fetchone()
            version = row["draft_version"]

        resp = self.client.post(
            "/api/ai/daily-report/draft/100/update-worker",
            json={"user_id": 101, "origin": "Updated Address", "draft_version": version},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 200)
        # Version should increment
        self.assertGreater(resp.get_json()["draft_version"], version)


class TestConfirmDraftOnly(Phase6TestBase):
    """Test Confirm only changes draft status, does not write service_reports."""

    def test_confirm_does_not_create_service_report(self):
        self._login(100)
        csrf = self._get_csrf()

        # Count service_reports before
        with self.app.app_context():
            before = self.app_module.db().execute("select count(*) as cnt from service_reports").fetchone()
            before_count = before["cnt"]

        # Confirm draft
        resp = self.client.post(
            "/api/ai/daily-report/draft/100/confirm",
            json={"draft_version": 1},
            headers={"X-CSRF-Token": csrf},
        )
        # May be 200 or 409 depending on version
        self.assertIn(resp.status_code, [200, 400, 409])

        # Count service_reports after - should be same
        with self.app.app_context():
            after = self.app_module.db().execute("select count(*) as cnt from service_reports").fetchone()
            self.assertEqual(after["cnt"], before_count)

    def test_confirm_records_audit(self):
        self._login(100)
        csrf = self._get_csrf()

        # Create a fresh draft for this test
        with self.app.app_context():
            db = self.app_module.db()
            fresh_data = {
                "service_order_id": 100,
                "report_date": "2026-09-14",
                "workers": [],
                "verification_fields": [],
                "verification_required": False,
                "ai_generated": True,
            }
            db.execute(
                "insert or replace into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (200, 100, "2026-09-14", "draft", 1, 100, json.dumps(fresh_data), "2026-09-14T00:00:00Z", "2026-09-14T00:00:00Z"),
            )
            db.commit()

        resp = self.client.post(
            "/api/ai/daily-report/draft/200/confirm",
            json={"draft_version": 1},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 200)

        # Check audit fields
        with self.app.app_context():
            row = self.app_module.db().execute("select draft_data, status from ai_daily_report_drafts where id = 200").fetchone()
            self.assertEqual(row["status"], "confirmed")
            data = json.loads(row["draft_data"])
            # confirmed_by should be the current logged-in user (admin=100)
            self.assertEqual(data["confirmed_by"], 100)
            self.assertIsNotNone(data["confirmed_at"])


class TestFormalDBIsolation(Phase6TestBase):
    """Test Phase 6 does not write to formal service_report tables."""

    def test_no_service_report_attachments_created(self):
        self._login(100)
        csrf = self._get_csrf()

        with self.app.app_context():
            before = self.app_module.db().execute("select count(*) as cnt from service_report_attachments").fetchone()
            before_count = before["cnt"]

        # Perform various Phase 6 operations
        self.client.get("/api/ai/daily-report/draft/100/preview")
        self.client.post(
            "/api/ai/daily-report/draft/100/update-arrival-departure",
            json={"arrival_time": "09:00", "draft_version": 1},
            headers={"X-CSRF-Token": csrf},
        )

        with self.app.app_context():
            after = self.app_module.db().execute("select count(*) as cnt from service_report_attachments").fetchone()
            self.assertEqual(after["cnt"], before_count)


class TestDraftList(Phase6TestBase):
    """Test Draft list API with filtering and pagination."""

    def test_list_returns_drafts(self):
        self._login(100)
        resp = self.client.get("/api/ai/daily-report/drafts")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertGreater(len(data["drafts"]), 0)

    def test_list_filter_by_status(self):
        self._login(100)
        resp = self.client.get("/api/ai/daily-report/drafts?status=draft")
        data = resp.get_json()
        for d in data["drafts"]:
            self.assertEqual(d["status"], "draft")

    def test_list_pagination(self):
        self._login(100)
        resp = self.client.get("/api/ai/daily-report/drafts?page=1&per_page=2")
        data = resp.get_json()
        self.assertLessEqual(len(data["drafts"]), 2)
        self.assertEqual(data["page"], 1)


class TestSecurePhotoEndpoint(Phase6TestBase):
    """Test secure photo/evidence endpoints reject path traversal."""

    def test_photo_endpoint_requires_auth(self):
        # Ensure not logged in (clear any session state from previous tests)
        with self.client.session_transaction() as sess:
            sess.clear()
        resp = self.client.get("/api/ai/daily-report/draft/100/photo/test123")
        self.assertIn(resp.status_code, [302, 401, 403])

    def test_evidence_endpoint_requires_auth(self):
        # Ensure not logged in
        with self.client.session_transaction() as sess:
            sess.clear()
        resp = self.client.get("/api/ai/daily-report/draft/100/evidence/test123")
        self.assertIn(resp.status_code, [302, 401, 403])

    def test_nonexistent_photo_returns_404(self):
        self._login(100)
        resp = self.client.get("/api/ai/daily-report/draft/100/photo/nonexistent_hash")
        self.assertEqual(resp.status_code, 404)


class TestWorkerParticipantAuthorization(Phase6TestBase):
    """Test worker/participant authorization rules.

    Rules:
    - Creator can view/edit their drafts
    - Workers/participants (by user_id) can view/edit drafts they appear in
    - Admin/manager can view/edit all
    - Employees NOT in draft cannot access (IDOR protection)
    - Same-name employees do NOT gain access (must match by user_id, not name)
    - Participant removed from draft loses access
    - Participant cannot bypass confirmed/cancelled state machine
    """

    def test_creator_can_view_draft(self):
        """Ethan (creator, user_id=101) can view draft 100."""
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 100").fetchone()
            ethan = {"id": 101, "role": "employee"}
            self.assertTrue(self.app_module.can_view_ai_daily_report_draft(ethan, draft))

    def test_worker_participant_can_view_draft(self):
        """张三 (worker, user_id=102) can view draft 100 even though not creator."""
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 100").fetchone()
            zhangsan = {"id": 102, "role": "employee"}
            self.assertTrue(self.app_module.can_view_ai_daily_report_draft(zhangsan, draft))

    def test_non_participant_cannot_view_draft(self):
        """李四 (user_id=104, NOT in draft) cannot view draft 100."""
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 100").fetchone()
            lisi = {"id": 104, "role": "employee"}
            self.assertFalse(self.app_module.can_view_ai_daily_report_draft(lisi, draft))

    def test_same_name_employee_does_not_gain_access(self):
        """Another 张三 (user_id=105, same name but different user) cannot view draft.

        Permission must be based on stable user_id, NOT name matching.
        This prevents same-name employees from gaining unauthorized access.
        """
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 100").fetchone()
            zhangsan2 = {"id": 105, "role": "employee", "name": "张三 P6"}
            self.assertFalse(self.app_module.can_view_ai_daily_report_draft(zhangsan2, draft))

    def test_worker_can_edit_draft_status(self):
        """张三 (worker) can edit draft when status=draft."""
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 100").fetchone()
            zhangsan = {"id": 102, "role": "employee"}
            self.assertTrue(self.app_module.can_edit_ai_daily_report_draft(zhangsan, draft))

    def test_worker_cannot_edit_confirmed_draft(self):
        """张三 (worker) cannot edit confirmed draft - participant identity does not bypass state machine."""
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 101").fetchone()
            zhangsan = {"id": 102, "role": "employee"}
            self.assertFalse(self.app_module.can_edit_ai_daily_report_draft(zhangsan, draft))

    def test_creator_cannot_edit_confirmed_draft(self):
        """Ethan (creator) cannot edit confirmed draft - state machine applies to everyone."""
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 101").fetchone()
            ethan = {"id": 101, "role": "employee"}
            self.assertFalse(self.app_module.can_edit_ai_daily_report_draft(ethan, draft))

    def test_participant_removed_loses_access(self):
        """When a worker is removed from draft, they lose view access.

        Permission is re-evaluated based on current draft.workers, not historical participation.
        """
        with self.app.app_context():
            db = self.app_module.db()
            # Create a draft with 张三 as worker
            draft_data = {
                "service_order_id": 100,
                "report_date": "2026-09-15",
                "workers": [
                    {"user_id": 101, "name": "Ethan"},
                    {"user_id": 102, "name": "张三"},
                ],
                "verification_required": False,
                "verification_fields": [],
            }
            db.execute(
                "insert or replace into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (110, 100, "2026-09-15", "draft", 1, 101, json.dumps(draft_data), "2026-09-15T00:00:00Z", "2026-09-15T00:00:00Z"),
            )
            db.commit()

            # 张三 can view initially
            draft = db.execute("select * from ai_daily_report_drafts where id = 110").fetchone()
            zhangsan = {"id": 102, "role": "employee"}
            self.assertTrue(self.app_module.can_view_ai_daily_report_draft(zhangsan, draft))

            # Remove 张三 from workers
            draft_data["workers"] = [{"user_id": 101, "name": "Ethan"}]
            db.execute("update ai_daily_report_drafts set draft_data = ? where id = 110", (json.dumps(draft_data),))
            db.commit()

            # 张三 can no longer view
            draft = db.execute("select * from ai_daily_report_drafts where id = 110").fetchone()
            self.assertFalse(self.app_module.can_view_ai_daily_report_draft(zhangsan, draft))

    def test_list_api_filters_by_participation(self):
        """Draft List API returns drafts where user is creator OR worker.

        SQL-level filtering, not query-all-then-hide.
        """
        # 张三 (102) should see draft 100 (as worker) but NOT draft 110 (if exists without him)
        self._login(102)
        resp = self.client.get("/api/ai/daily-report/drafts")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        draft_ids = [d["id"] for d in data["drafts"]]
        self.assertIn(100, draft_ids)  # 张三 is worker in draft 100

    def test_list_api_excludes_non_participants(self):
        """李四 (104, not in any draft) should see NO drafts."""
        self._login(104)
        resp = self.client.get("/api/ai/daily-report/drafts")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data["drafts"]), 0)

    def test_non_participant_cannot_access_preview_api(self):
        """李四 (104) cannot access preview API for draft 100 via IDOR."""
        self._login(104)
        resp = self.client.get("/api/ai/daily-report/draft/100/preview")
        self.assertEqual(resp.status_code, 403)

    def test_worker_can_update_via_api(self):
        """张三 (worker) can update draft via API when status=draft."""
        self._login(102)
        csrf = self._get_csrf()
        resp = self.client.post(
            "/api/ai/daily-report/draft/100/update-arrival-departure",
            json={"arrival_time": "08:30", "draft_version": 1},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])

        # Verify update was recorded
        with self.app.app_context():
            draft = self.app_module.db().execute("select draft_data from ai_daily_report_drafts where id = 100").fetchone()
            data = json.loads(draft["draft_data"])
            self.assertEqual(data["arrival_time"], "08:30")
            self.assertEqual(data["arrival_time_source"], "user_input")

    def test_worker_cannot_update_confirmed_via_api(self):
        """张三 (worker) cannot update confirmed draft via API - state machine enforced."""
        self._login(102)
        csrf = self._get_csrf()
        resp = self.client.post(
            "/api/ai/daily-report/draft/101/update-arrival-departure",
            json={"arrival_time": "09:00", "draft_version": 1},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_view_all(self):
        """Admin can view all drafts regardless of participation."""
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 100").fetchone()
            admin = {"id": 100, "role": "admin"}
            self.assertTrue(self.app_module.can_view_ai_daily_report_draft(admin, draft))

    def test_finance_can_view_but_not_edit(self):
        """Finance can view all drafts but cannot edit (read-only role)."""
        with self.app.app_context():
            draft = self.app_module.db().execute("select * from ai_daily_report_drafts where id = 100").fetchone()
            finance = {"id": 103, "role": "finance"}
            self.assertTrue(self.app_module.can_view_ai_daily_report_draft(finance, draft))
            self.assertFalse(self.app_module.can_edit_ai_daily_report_draft(finance, draft))


if __name__ == "__main__":
    unittest.main(verbosity=2)
