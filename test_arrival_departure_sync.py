"""v0.1.257 - Arrival / Departure photo sync into the service report page.

User report: "从AI智能日报传到工单日报中的现场到达时间和离开现场时间照片没有同步传过来".

Root cause: FormalSaveService wrote the arrival/departure photos ONLY into the
provenance columns on service_reports (arrival_photo_relative_path /
departure_photo_relative_path), because the sealed Phase 9 boundary declared
them "provenance-only". But the service-report page renders those two sections
from service_report_attachments WHERE category IN ('arrival','departure'), so
the sections came out empty.

Fix: arrival/departure photos are now materialized as REAL formal attachments
(categories 'arrival' / 'departure') while the provenance columns are kept.

This file asserts the outcome from the report page's own perspective:
get_report_attachments() must return non-empty arrival/departure lists and the
stored files must resolve via report_attachment_path().
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-01257")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

sys.path.insert(0, str(PROJECT_ROOT / "ai_daily_report"))
from test_ai_daily_report_phase9 import (  # noqa: E402
    Phase9TestBase,
    _make_valid_draft,
)


class ArrivalDepartureSyncTest(Phase9TestBase):
    """Formal save must push arrival/departure photos into the report page."""

    def _save_default_draft(self, draft_id):
        manifest = self._prepare_ready(draft_id)
        draft_row = self._get_draft_row(draft_id)
        validation = self._validation_result(draft_row)
        result = self._formal_service().run(
            draft_row, 1, manifest["manifest_id"], validation, self._manifest_service()
        )
        self.assertEqual(result["status"], "created")
        return result["service_report_id"], manifest

    def test_01_report_page_reader_sees_arrival_and_departure(self):
        """THE BUG: get_report_attachments (what the page calls) returns the
        arrival/departure photos after a formal save."""
        did, _ = self._build_confirmed_draft(501)
        rid, _ = self._save_default_draft(did)
        grouped = self.app_module.get_report_attachments(rid)
        self.assertEqual(len(grouped["arrival"]), 1, "arrival photo missing from report page")
        self.assertEqual(len(grouped["departure"]), 1, "departure photo missing from report page")
        # the three original categories are unaffected
        self.assertEqual(len(grouped["self_check"]), 1)
        self.assertEqual(len(grouped["site"]), 1)

    def test_02_arrival_photo_files_resolve_on_disk(self):
        """The stored arrival/departure files must be readable through the
        report page's own path helper and hash-match the prepared asset."""
        did, _ = self._build_confirmed_draft(502)
        rid, manifest = self._save_default_draft(did)
        grouped = self.app_module.get_report_attachments(rid)
        db = self.app_module.db()
        prepared_shas = {
            r["prepared_sha256"]
            for r in db.execute(
                "select prepared_sha256 from ai_daily_report_prepared_assets where manifest_id = ?",
                (manifest["manifest_id"],),
            ).fetchall()
        }
        for category in ("arrival", "departure"):
            att = grouped[category][0]
            path = self.app_module.report_attachment_path(att)
            self.assertTrue(os.path.isfile(path), f"{category} file not found: {path}")
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            self.assertIn(digest, prepared_shas, f"{category} file hash not a prepared asset")

    def test_03_arrival_departure_in_folder_named_like_manual_reports(self):
        """Stored paths must use the same folder names a manually-filled report
        uses (现场到达时间照片 / 离开现场时间照片), so the page resolves them."""
        did, _ = self._build_confirmed_draft(503)
        rid, _ = self._save_default_draft(did)
        grouped = self.app_module.get_report_attachments(rid)
        self.assertIn("现场到达时间照片", grouped["arrival"][0]["stored_filename"])
        self.assertIn("离开现场时间照片", grouped["departure"][0]["stored_filename"])

    def test_04_provenance_columns_still_written(self):
        """Arrival/departure provenance (original AI-draft photo) is preserved
        alongside the new attachment rows."""
        did, _ = self._build_confirmed_draft(504)
        rid, _ = self._save_default_draft(did)
        db = self.app_module.db()
        report = db.execute(
            """
            select arrival_photo_relative_path, arrival_photo_hash,
                   departure_photo_relative_path, departure_photo_hash
            from service_reports where id = ?
            """,
            (rid,),
        ).fetchone()
        for col in (
            "arrival_photo_relative_path", "arrival_photo_hash",
            "departure_photo_relative_path", "departure_photo_hash",
        ):
            self.assertTrue(report[col], f"{col} should still be populated")

    def test_05_no_arrival_ref_means_no_arrival_attachment(self):
        """A draft without an arrival/departure ref must not fabricate rows."""
        did, data = self._build_confirmed_draft(505)
        data = json.loads(json.dumps(data))
        data["arrival_photo_ref"] = None
        data["departure_photo_ref"] = None
        self._insert_draft(did, data, status="confirmed", draft_version=1, created_by=401)
        rid, _ = self._save_default_draft(did)
        grouped = self.app_module.get_report_attachments(rid)
        self.assertEqual(grouped["arrival"], [])
        self.assertEqual(grouped["departure"], [])

    def test_06_departure_only_does_not_create_arrival(self):
        """Only the reference that exists gets a row."""
        did, data = self._build_confirmed_draft(506)
        data = json.loads(json.dumps(data))
        data["arrival_photo_ref"] = None  # departure stays set by default
        self._insert_draft(did, data, status="confirmed", draft_version=1, created_by=401)
        rid, _ = self._save_default_draft(did)
        grouped = self.app_module.get_report_attachments(rid)
        self.assertEqual(grouped["arrival"], [])
        self.assertEqual(len(grouped["departure"]), 1)

    def test_07_draft_without_evidence_records(self):
        """A draft whose mileage comes from a plain reported value (no
        evidence record) still syncs arrival and departure photos — the exact
        shape the user described (workers + photos, no mileage proof)."""
        did, data = self._build_confirmed_draft(507)
        data = json.loads(json.dumps(data))
        data["evidence_records"] = []
        data["workers"][0]["mileage_evidence_id"] = None
        data["workers"][0]["route_status"] = "skipped"
        data["workers"][0]["one_way_miles"] = 10.0
        data["workers"][0]["reported_miles"] = 20.0
        data["driving_miles"] = 20.0
        self._insert_draft(did, data, status="confirmed", draft_version=1, created_by=401)
        rid, _ = self._save_default_draft(did)
        grouped = self.app_module.get_report_attachments(rid)
        self.assertEqual(len(grouped["arrival"]), 1)
        self.assertEqual(len(grouped["departure"]), 1)
        self.assertEqual(grouped["mileage_proof"], [])


    def test_08_report_pages_render_arrival_departure_sections(self):
        """HTTP level: both the edit form and the read-only view must render
        the arrival/departure photo sections with a real <img> each."""
        did, _ = self._build_confirmed_draft(508)
        rid, _ = self._save_default_draft(did)
        with self.client.session_transaction() as sess:
            sess["user_id"] = 400
        # edit form uses 现场到达时间照片 / 离开现场时间照片;
        # the read-only view uses 到达现场时间照片 / 离开现场时间照片.
        expected = {
            f"/service-reports/{rid}/edit": ("现场到达时间照片", "离开现场时间照片"),
            f"/service-reports/{rid}/view": ("到达现场时间照片", "离开现场时间照片"),
        }
        for path, (arrival_label, departure_label) in expected.items():
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, f"{path} -> {resp.status_code}")
            html = resp.get_data(as_text=True)
            self.assertIn(arrival_label, html, f"arrival section missing in {path}")
            self.assertIn(departure_label, html, f"departure section missing in {path}")
        # both arrival and departure attachments are served as images
        grouped = self.app_module.get_report_attachments(rid)
        for category in ("arrival", "departure"):
            att_id = grouped[category][0]["id"]
            img = self.client.get(f"/service-report-attachments/{att_id}")
            self.assertEqual(img.status_code, 200, category)
            self.assertGreater(len(img.get_data()), 0, category)


if __name__ == "__main__":
    unittest.main()
