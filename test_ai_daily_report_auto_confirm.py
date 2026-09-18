"""Auto formal save on confirm (v0.1.233).

Confirm of an AI daily report draft should automatically prepare the
attachment manifest, auto-review compliance, and run the Phase 9 formal
save so the draft lands in the formal service_reports table. When any
gate blocks (Phase 7 ERROR etc.), the draft stays 'confirmed' and the
response carries the blocked reason.
"""
import json
import os
import unittest

from test_ai_daily_report_phase9 import Phase9TestBase


class TestConfirmAutoFormalSave(Phase9TestBase):
    """Confirm -> prepare -> auto compliance review -> formal save."""

    def _set_draft_status(self, draft_id, status):
        db = self.app_module.db()
        db.execute(
            "update ai_daily_report_drafts set status = ? where id = ?",
            (status, draft_id),
        )
        db.commit()

    def _confirm(self, draft_id, user_id=400, body=None):
        self._login(user_id)
        return self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/confirm",
            json=body if body is not None else {"draft_version": 1},
        )

    def test_confirm_auto_creates_service_report(self):
        draft_id, _ = self._build_confirmed_draft(470)
        self._set_draft_status(draft_id, "draft")
        before = self._count_rows("service_reports")

        resp = self._confirm(draft_id)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertTrue(data["ok"])
        auto = data["auto_formal_save"]
        self.assertTrue(auto["formal_saved"], auto)
        self.assertIsNotNone(auto["service_report_id"])
        self.assertIsNotNone(auto["report_url"])

        # Draft is now formally saved and points at the report.
        row = self._get_draft_row(draft_id)
        self.assertEqual(row["status"], "saved")
        self.assertEqual(int(row["saved_report_id"]), auto["service_report_id"])

        # Exactly one formal report was created, with the draft's worker.
        self.assertEqual(self._count_rows("service_reports"), before + 1)
        workers = self.app_module.db().execute(
            "select user_id from service_report_workers where report_id = ?",
            (auto["service_report_id"],),
        ).fetchall()
        self.assertEqual([w["user_id"] for w in workers], [401])

        # Re-confirm is idempotent: returns the existing report, no second row.
        resp2 = self._confirm(draft_id)
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.get_json()
        self.assertEqual(data2["status"], "saved")
        self.assertEqual(
            data2["auto_formal_save"]["service_report_id"],
            auto["service_report_id"],
        )
        self.assertEqual(self._count_rows("service_reports"), before + 1)

    def test_confirm_with_bare_filename_evidence_path(self):
        """Regression (v0.1.237): MileageEvidenceService stores a BARE filename
        in evidence_records.file_relative_path; the manifest must normalize it
        (same as the evidence download route) instead of rejecting with
        '佐证路径不在 Draft 佐证目录中'."""
        draft_id, _ = self._build_confirmed_draft(473)
        row = self._get_draft_row(draft_id)
        draft_data = json.loads(row["draft_data"])
        ev = draft_data["evidence_records"][0]
        self.assertIn("/", ev["file_relative_path"])  # fixture uses full path
        # Rewrite to the bare-filename form the real generator persists.
        ev["file_relative_path"] = ev["file_relative_path"].rsplit("/", 1)[-1]
        db = self.app_module.db()
        db.execute(
            "update ai_daily_report_drafts set draft_data = ?, status = 'draft' where id = ?",
            (json.dumps(draft_data), draft_id),
        )
        db.commit()

        resp = self._confirm(draft_id)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        auto = resp.get_json()["auto_formal_save"]
        self.assertTrue(auto["formal_saved"], auto)
        self.assertIsNotNone(auto["service_report_id"])

    def test_confirm_blocked_by_validation_stays_confirmed(self):
        draft_id, _ = self._build_confirmed_draft(
            471,
            selected_safety_photo=None,
            selected_service_photos=[],
            photo_candidates=[],
        )
        self._set_draft_status(draft_id, "draft")
        before = self._count_rows("service_reports")

        resp = self._confirm(draft_id)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertTrue(data["ok"])
        auto = data["auto_formal_save"]
        self.assertFalse(auto["formal_saved"])
        self.assertEqual(auto["blocked_code"], "validation_cannot_proceed")
        self.assertEqual(data["status"], "confirmed")

        row = self._get_draft_row(draft_id)
        self.assertEqual(row["status"], "confirmed")
        self.assertIsNone(row["saved_report_id"])
        self.assertEqual(self._count_rows("service_reports"), before)

    def test_confirm_of_saved_draft_returns_existing_report(self):
        draft_id, _ = self._build_confirmed_draft(472)  # already 'confirmed'
        # Simulate an already-saved draft.
        db = self.app_module.db()
        db.execute(
            "update ai_daily_report_drafts set status = 'saved', saved_report_id = 9999 where id = ?",
            (draft_id,),
        )
        db.commit()

        resp = self._confirm(draft_id)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "saved")
        self.assertEqual(data["auto_formal_save"]["service_report_id"], 9999)


if __name__ == "__main__":
    unittest.main(verbosity=2)
