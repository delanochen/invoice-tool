"""v0.1.241 regression tests.

1. Stale travel verification entries (e.g. "Antonio.origin",
   "worker_4_origin_unconfirmed") kept the confirm override dialog popping up
   even after the origins had been confirmed. verification_fields must be
   reconciled with the current worker state before display and confirm.
2. Draft "取消" (cancel) must be a SOFT cancel (status=cancelled, row kept
   with audit trail); "删除" (delete) must be a HARD delete (row removed).
   Before v0.1.241 the /cancel endpoint physically deleted the draft, which
   the user perceived as "取消" — now the two actions are separate endpoints.
"""
import json
import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from test_ai_daily_report_phase9 import Phase9TestBase  # noqa: E402

from ai_daily_report.travel_service import (  # noqa: E402
    reconcile_travel_verification_fields,
)


class TestReconcileTravelVerificationFields(unittest.TestCase):
    """Pure unit tests for the reconciliation helper."""

    def _w(self, uid=1, name="Antonio", transportation="self_drive",
           origin="100 Main St", origin_confirmed=True, trip_type="round_trip"):
        return {
            "user_id": uid, "name": name, "transportation": transportation,
            "origin": origin, "origin_confirmed": origin_confirmed,
            "trip_type": trip_type,
        }

    def test_stale_entries_dropped_when_origin_confirmed(self):
        workers = [self._w(uid=4, name="Antonio")]
        fields = [
            "Antonio.origin",
            "worker_4_origin_unconfirmed",
            "高阳.origin",
            "worker_2_origin_unconfirmed",
            "worker_22_origin_unconfirmed",
        ]
        # 高阳/worker_2/worker_22 are no longer in the draft -> dropped too.
        self.assertEqual(reconcile_travel_verification_fields(workers, fields), [])

    def test_pending_entries_kept_when_unconfirmed(self):
        workers = [
            self._w(uid=2, name="陈亦珹", origin_confirmed=False),
            self._w(uid=3, name="高阳", origin=None, origin_confirmed=False),
        ]
        fields = [
            "陈亦珹.origin",
            "worker_2_origin_unconfirmed",
            "高阳.origin",
            "worker_3_origin",
        ]
        self.assertEqual(
            sorted(reconcile_travel_verification_fields(workers, fields)),
            sorted(["陈亦珹.origin", "worker_2_origin_unconfirmed", "高阳.origin", "worker_3_origin"]),
        )

    def test_legacy_overnight_stay_fields_dropped(self):
        """行程类型有默认值后，存量「是否住宿」待确认项一律丢弃，不再弹窗追问。"""
        workers = [
            self._w(uid=4, name="Antonio"),
            self._w(uid=5, name="Bob", trip_type="one_way"),
        ]
        fields = ["worker_4_overnight_stay", "worker_5_overnight_stay", "no_photos"]
        self.assertEqual(
            reconcile_travel_verification_fields(workers, fields),
            ["no_photos"],
        )

    def test_non_travel_fields_pass_through(self):
        workers = [self._w(uid=4, name="Antonio")]
        fields = ["timeline_too_short", "no_photos", "work_items.equipment"]
        self.assertEqual(
            reconcile_travel_verification_fields(workers, fields),
            ["timeline_too_short", "no_photos", "work_items.equipment"],
        )

    def test_non_self_drive_worker_entries_dropped(self):
        workers = [self._w(uid=4, name="Antonio", transportation="carpool")]
        fields = ["Antonio.origin", "worker_4_origin_unconfirmed"]
        self.assertEqual(reconcile_travel_verification_fields(workers, fields), [])

    def test_empty_fields_short_circuit(self):
        self.assertEqual(reconcile_travel_verification_fields([], []), [])
        self.assertEqual(reconcile_travel_verification_fields([], None), [])


class TestConfirmReconcilesVerificationFields(Phase9TestBase):
    """Confirm must not ask for override when only stale origin entries remain."""

    def _set_draft_status(self, draft_id, status):
        db = self.app_module.db()
        db.execute(
            "update ai_daily_report_drafts set status = ? where id = ?",
            (status, draft_id),
        )
        db.commit()

    def test_confirm_skips_override_for_stale_origin_entries(self):
        draft_id, data = self._build_confirmed_draft(480)
        data["verification_required"] = True
        data["verification_fields"] = ["Ethan P9.origin", "worker_401_origin_unconfirmed"]
        self._insert_draft(draft_id, data, status="draft", draft_version=1)

        self._login(400)
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/confirm",
            json={"draft_version": 1},
        )
        self.assertEqual(resp.status_code, 200, resp.get_json())
        result = resp.get_json()
        self.assertTrue(result["ok"])
        # Auto formal save chained (nothing actually blocked).
        self.assertTrue(result["auto_formal_save"]["formal_saved"], result)

        # Persisted: reconciled to empty, verification_required cleared.
        row = self._get_draft_row(draft_id)
        stored = json.loads(row["draft_data"])
        self.assertEqual(stored["verification_fields"], [])
        self.assertFalse(stored["verification_required"])

    def test_preview_no_longer_shows_stale_fields(self):
        draft_id, data = self._build_confirmed_draft(481)
        data["verification_required"] = True
        data["verification_fields"] = ["Ethan P9.origin", "worker_401_origin_unconfirmed"]
        self._insert_draft(draft_id, data, status="draft", draft_version=1)

        self._login(400)
        resp = self.client.get(f"/api/ai/daily-report/draft/{draft_id}/preview")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        ai = (resp.get_json().get("preview") or {}).get("ai_metadata") or {}
        self.assertFalse(ai.get("has_verification_required"))
        self.assertEqual(ai.get("verification_required_fields"), [])


class TestCancelVsDelete(Phase9TestBase):
    """取消 = soft cancel (row kept); 删除 = hard delete (row removed)."""

    def _set_draft_status(self, draft_id, status):
        db = self.app_module.db()
        db.execute(
            "update ai_daily_report_drafts set status = ? where id = ?",
            (status, draft_id),
        )
        db.commit()

    def _csrf(self):
        resp = self.client.get("/api/ai/daily-report/csrf")
        return (resp.get_json(silent=True) or {}).get("csrf_token")

    def _draft_exists(self, draft_id):
        db = self.app_module.db()
        row = db.execute(
            "select count(*) as c from ai_daily_report_drafts where id = ?", (draft_id,)
        ).fetchone()
        return bool(row and int(row["c"]))

    def _post(self, path, draft_id, user_id=400):
        self._login(user_id)
        return self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/{path}",
            headers={"X-CSRF-Token": self._csrf()},
            json={},
        )

    def test_cancel_is_soft_row_kept(self):
        draft_id, _ = self._build_confirmed_draft(482)
        self._set_draft_status(draft_id, "draft")

        resp = self._post("cancel", draft_id)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["status"], "cancelled")

        # Row still exists with cancelled status + audit trail.
        row = self._get_draft_row(draft_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "cancelled")
        stored = json.loads(row["draft_data"])
        self.assertEqual(stored.get("cancelled_by"), 400)
        self.assertTrue(stored.get("cancelled_at"))

    def test_cancel_of_saved_draft_rejected(self):
        draft_id, _ = self._build_confirmed_draft(483)
        self._set_draft_status(draft_id, "saved")
        resp = self._post("cancel", draft_id)
        self.assertEqual(resp.status_code, 409)

    def test_delete_is_hard_row_removed(self):
        draft_id, _ = self._build_confirmed_draft(484)
        self._set_draft_status(draft_id, "draft")

        resp = self._post("delete", draft_id)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["status"], "deleted")
        self.assertFalse(self._draft_exists(draft_id))

    def test_delete_of_cancelled_draft_allowed(self):
        draft_id, _ = self._build_confirmed_draft(485)
        self._set_draft_status(draft_id, "cancelled")
        resp = self._post("delete", draft_id)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertFalse(self._draft_exists(draft_id))

    def test_delete_of_saved_draft_rejected(self):
        draft_id, _ = self._build_confirmed_draft(486)
        self._set_draft_status(draft_id, "saved")
        resp = self._post("delete", draft_id)
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(self._draft_exists(draft_id))


if __name__ == "__main__":
    unittest.main(verbosity=2)
