"""Auto media preparation for AI daily report drafts (v0.1.234).

发现照片、确认时间线、筛选施工照片、重新计算里程 should run
automatically — no manual clicks required. The /auto-prepare endpoint
runs the whole pipeline idempotently; manual user overrides (times set
by hand, photo selections) are preserved.
"""
import unittest

from test_ai_daily_report_phase9 import Phase9TestBase


class TestAutoPrepare(Phase9TestBase):
    """POST /auto-prepare runs discover -> confirm timeline (-> classify ->
    mileage) in one shot, best-effort."""

    def _auto_prepare(self, draft_id, user_id=400):
        self._login(user_id)
        headers = {"X-CSRF-Token": self._get_csrf()}
        return self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/auto-prepare",
            headers=headers,
            json={},
        )

    def test_auto_prepare_discovers_photos_and_confirms_timeline(self):
        # Simulate a draft whose photos were never scanned.
        draft_id, data = self._build_confirmed_draft(480)
        data["photo_candidates"] = []
        data["photo_timeline_status"] = "not_scanned"
        data["selected_safety_photo"] = None
        data["selected_safety_photos"] = []
        data["selected_service_photos"] = []
        data["arrival_photo_ref"] = None
        data["departure_photo_ref"] = None
        data["arrival_time"] = None
        data["departure_time"] = None
        data["arrival_time_source"] = None
        data["departure_time_source"] = None
        self._insert_draft(draft_id, data, status="draft")

        resp = self._auto_prepare(draft_id)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        body = resp.get_json()
        self.assertTrue(body["ok"])

        steps = {s["step"]: s for s in body["steps"]}
        self.assertIn("discover_photos", steps)
        self.assertTrue(steps["discover_photos"]["ok"], steps)
        self.assertGreaterEqual(steps["discover_photos"].get("photo_count", 0), 1)

        # Timeline should be confirmed automatically (no manual override yet).
        self.assertIn("confirm_timeline", steps)
        self.assertTrue(steps["confirm_timeline"]["ok"], steps)

        # Draft now carries discovered photo candidates and confirmed times.
        row = self._get_draft_row(draft_id)
        self.assertEqual(row["status"], "draft")
        import json as _json
        refreshed = _json.loads(row["draft_data"])
        self.assertTrue(refreshed.get("photo_candidates"))

    def test_auto_prepare_is_idempotent(self):
        draft_id, data = self._build_confirmed_draft(481)
        self._insert_draft(draft_id, data, status="draft")

        resp1 = self._auto_prepare(draft_id)
        self.assertEqual(resp1.status_code, 200)
        resp2 = self._auto_prepare(draft_id)
        self.assertEqual(resp2.status_code, 200)
        self.assertTrue(resp2.get_json()["ok"])

    def test_auto_prepare_preserves_manual_times(self):
        draft_id, data = self._build_confirmed_draft(482)
        data["photo_candidates"] = []
        data["photo_timeline_status"] = "not_scanned"
        data["arrival_time"] = "2026-09-14T08:30:00"
        data["arrival_time_source"] = "user_input"
        data["departure_time"] = "2026-09-14T17:00:00"
        data["departure_time_source"] = "user_input"
        data["selected_safety_photo"] = None
        data["selected_service_photos"] = []
        self._insert_draft(draft_id, data, status="draft")

        resp = self._auto_prepare(draft_id)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        body = resp.get_json()
        steps = {s["step"] for s in body["steps"]}

        # Discovery may run, but the manual times must NOT be overwritten.
        row = self._get_draft_row(draft_id)
        import json as _json
        refreshed = _json.loads(row["draft_data"])
        self.assertEqual(refreshed["arrival_time"], "2026-09-14T08:30:00")
        self.assertEqual(refreshed["arrival_time_source"], "user_input")
        self.assertEqual(refreshed["departure_time"], "2026-09-14T17:00:00")
        self.assertNotIn("confirm_timeline", steps)

    def test_auto_prepare_skips_saved_draft(self):
        draft_id, data = self._build_confirmed_draft(483)
        self._insert_draft(draft_id, data, status="saved", draft_version=1)

        resp = self._auto_prepare(draft_id)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["steps"], [])

    def test_auto_prepare_requires_auth(self):
        draft_id, _ = self._build_confirmed_draft(484)
        # Not logged in.
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/auto-prepare", json={})
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
