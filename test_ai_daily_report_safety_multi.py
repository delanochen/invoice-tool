"""AI Daily Report - Multi Safety Photo + Hard Draft Delete Tests

Covers:
- Multiple safety self-check photos: mark append, toggle-off, role switch
  keeps other safety photos, legacy singular fallback.
- Validation / Preview / Manifest consume multi safety photos.
- Draft deletion is a physical delete (cascades child rows), not a soft cancel.
"""
import hashlib
import json
import os
import sqlite3
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-safety-multi")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from ai_daily_report.daily_report_service import DailyReportService  # noqa: E402
from ai_daily_report.schemas import collect_safety_photos  # noqa: E402
from ai_daily_report.validation_engine import ValidationEngine  # noqa: E402
from ai_daily_report.preview_aggregation import PreviewAggregationService  # noqa: E402


def _pid(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _photo(seed: str, classification="unknown", manual=None, capture="2026-09-14T08:00:00",
           rel="SO2609004/pictures/2026-09-14/x.jpg"):
    return {
        "photo_id": _pid(seed),
        "photo_hash": _pid(seed),
        "relative_path": rel,
        "classification": classification,
        "confidence": 0.0,
        "capture_time": capture,
        "capture_time_source": "filename",
        "capture_timezone": None,
        "capture_timezone_source": None,
        "file_modified_time": None,
        "mime": "image/jpeg",
        "source": "server_original",
        "time_verification_required": False,
        "timeline_eligible": True,
        "timeline_exclusion_reason": None,
        "metadata_unsupported": False,
        "user_modified_time": None,
        "manual_classification": manual,
    }


def _draft_data(photos=None, selected_service=None, safety=None, safety_list=None):
    return {
        "service_order_id": 31,
        "report_date": "2026-09-14",
        "site_address": "9181 County Rd 196, Liverpool, TX 77577",
        "workers": [],
        "work_items": [{"equipment": "A313", "action": "replace", "fuse_number": 2}],
        "photo_candidates": photos or [],
        "selected_service_photos": selected_service or [],
        "selected_safety_photos": safety_list or [],
        "selected_safety_photo": safety,
        "selected_safety_photo_source": "user_selected" if (safety or safety_list) else None,
        "photo_timeline_status": "ready",
        "photo_classification_status": "not_classified",
        "service_description": "Replaced fuse #2",
        "waiting_hours": 0.0,
        "waiting_reason": "",
        "verification_required": False,
        "verification_fields": [],
        "ai_metadata": {"model": "deepseek-chat", "created_by": "ai"},
        "evidence_records": [],
        "photo_set_fingerprint": "pset-fp",
        "warning_acknowledgements": [],
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("""create table ai_daily_report_drafts (
            id integer primary key,
            service_order_id integer,
            report_date text,
            status text,
            draft_version integer,
            created_by integer,
            draft_data text,
            created_at text,
            updated_at text,
            verification_required integer default 0,
            verification_fields text default ''
        )""")
        self.db.commit()
        self.svc = DailyReportService(self.db, lambda: datetime.now().isoformat(), 1, "Admin")

    def tearDown(self):
        self.db.close()

    def _insert_draft(self, data, draft_id=1):
        self.db.execute(
            "insert into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, 31, '2026-09-14', 'draft', 1, 1, ?, ?, ?)",
            (draft_id, json.dumps(data), datetime.now().isoformat(), datetime.now().isoformat()),
        )
        self.db.commit()

    def _draft_row(self, draft_id=1):
        return self.db.execute("select * from ai_daily_report_drafts where id = ?", (draft_id,)).fetchone()

    def _draft_json(self, draft_id=1):
        return json.loads(self._draft_row(draft_id)["draft_data"])


class TestMultiSafetyMark(Base):
    def test_01_mark_two_safety_photos(self):
        p1 = _photo("s1", capture="2026-09-14T09:00:00")
        p2 = _photo("s2", capture="2026-09-14T10:00:00")
        self._insert_draft(_draft_data(photos=[p1, p2]))
        r1 = self.svc.mark_photo_classification(1, _pid("s1"), "safety")
        self.assertTrue(r1["ok"])
        r2 = self.svc.mark_photo_classification(1, _pid("s2"), "safety")
        self.assertTrue(r2["ok"])
        d = self._draft_json()
        ids = [a["photo_id"] for a in d["selected_safety_photos"]]
        self.assertEqual(ids, [_pid("s1"), _pid("s2")])
        # legacy singular field points at the first photo (backward compatible)
        self.assertEqual(d["selected_safety_photo"]["photo_id"], _pid("s1"))
        self.assertEqual(d["selected_safety_photo_source"], "user_selected")

    def test_02_toggle_off_safety_photo(self):
        p1 = _photo("s1", capture="2026-09-14T09:00:00")
        p2 = _photo("s2", capture="2026-09-14T10:00:00")
        self._insert_draft(_draft_data(photos=[p1, p2]))
        self.svc.mark_photo_classification(1, _pid("s1"), "safety")
        self.svc.mark_photo_classification(1, _pid("s2"), "safety")
        r = self.svc.mark_photo_classification(1, _pid("s1"), "safety")
        self.assertTrue(r["ok"])
        self.assertTrue(r.get("removed"))
        d = self._draft_json()
        ids = [a["photo_id"] for a in d["selected_safety_photos"]]
        self.assertEqual(ids, [_pid("s2")])
        self.assertEqual(d["selected_safety_photo"]["photo_id"], _pid("s2"))

    def test_03_switch_one_safety_to_equipment_keeps_other(self):
        p1 = _photo("s1", capture="2026-09-14T09:00:00")
        p2 = _photo("s2", capture="2026-09-14T10:00:00")
        self._insert_draft(_draft_data(photos=[p1, p2]))
        self.svc.mark_photo_classification(1, _pid("s1"), "safety")
        self.svc.mark_photo_classification(1, _pid("s2"), "safety")
        r = self.svc.mark_photo_classification(1, _pid("s2"), "equipment")
        self.assertTrue(r["ok"])
        d = self._draft_json()
        safety_ids = [a["photo_id"] for a in d["selected_safety_photos"]]
        self.assertEqual(safety_ids, [_pid("s1")])
        self.assertEqual(d["selected_safety_photo"]["photo_id"], _pid("s1"))
        service_ids = [a["photo_id"] for a in d["selected_service_photos"]]
        self.assertIn(_pid("s2"), service_ids)

    def test_04_remove_all_safety_clears_singular(self):
        p1 = _photo("s1", capture="2026-09-14T09:00:00")
        self._insert_draft(_draft_data(photos=[p1]))
        self.svc.mark_photo_classification(1, _pid("s1"), "safety")
        self.svc.mark_photo_classification(1, _pid("s1"), "arrival")
        d = self._draft_json()
        self.assertEqual(d.get("selected_safety_photos") or [], [])
        self.assertIsNone(d.get("selected_safety_photo"))
        self.assertIsNone(d.get("safety_photo"))
        self.assertEqual(d["arrival_photo_ref"], _pid("s1"))


class TestCollectSafetyPhotos(Base):
    def test_05_legacy_singular_fallback(self):
        data = {"selected_safety_photo": {"photo_id": "abc"}}
        self.assertEqual([p["photo_id"] for p in collect_safety_photos(data)], ["abc"])

    def test_06_multi_list_wins(self):
        data = {
            "selected_safety_photos": [{"photo_id": "a"}, {"photo_id": "b"}],
            "selected_safety_photo": {"photo_id": "a"},
        }
        self.assertEqual([p["photo_id"] for p in collect_safety_photos(data)], ["a", "b"])

    def test_07_empty_is_empty(self):
        self.assertEqual(collect_safety_photos({}), [])
        self.assertEqual(collect_safety_photos({"selected_safety_photo": None}), [])

    def test_08_pydantic_model_compatible(self):
        from ai_daily_report.schemas import DailyReportDraft
        model = DailyReportDraft(service_order_id=31, report_date="2026-09-14",
                                 selected_safety_photos=[{"photo_id": "a"}])
        self.assertEqual([p.photo_id for p in collect_safety_photos(model)], ["a"])


class TestValidationSafetyMulti(Base):
    def test_09_multi_safety_passes_safe_001(self):
        data = _draft_data(
            photos=[_photo("s1"), _photo("s2")],
            safety_list=[{"photo_id": _pid("s1"), "classification": "safety_person", "confidence": 1.0},
                         {"photo_id": _pid("s2"), "classification": "safety_person", "confidence": 1.0}],
        )
        engine = ValidationEngine()
        issues = engine._check_safety_photo(data)
        self.assertFalse([i for i in issues if i.rule_id == "SAFE-001"])

    def test_10_no_safety_still_safe_001(self):
        data = _draft_data(photos=[_photo("s1")])
        engine = ValidationEngine()
        issues = engine._check_safety_photo(data)
        self.assertTrue([i for i in issues if i.rule_id == "SAFE-001"])

    def test_11_visn_004_uses_all_safety_ids(self):
        data = _draft_data(
            photos=[_photo("s1"), _photo("s2")],
            safety_list=[{"photo_id": _pid("s1"), "classification": "safety_person"},
                         {"photo_id": _pid("s2"), "classification": "safety_person"}],
        )
        data["photo_analysis_results"] = [
            {"photo_id": _pid("s1"), "verification_required": True},
            {"photo_id": _pid("s2"), "verification_required": True},
        ]
        engine = ValidationEngine()
        issues = engine._check_vision(data)
        self.assertFalse([i for i in issues if i.rule_id == "VISN-004"])


class TestPreviewSafetyMulti(Base):
    def test_12_preview_exposes_selected_photos(self):
        data = _draft_data(
            photos=[_photo("s1"), _photo("s2")],
            safety_list=[{"photo_id": _pid("s1"), "classification": "safety_person", "confidence": 0.9},
                         {"photo_id": _pid("s2"), "classification": "safety_person", "confidence": 0.8}],
        )
        svc = PreviewAggregationService(self.db, ".")
        out = svc._build_safety_photo(data)
        self.assertEqual(out["selected_count"], 2)
        self.assertEqual(out["selected_photo_ids"], [_pid("s1"), _pid("s2")])
        self.assertEqual(len(out["selected_photos"]), 2)
        self.assertEqual(out["selected_photo_id"], _pid("s1"))

    def test_13_preview_legacy_singular(self):
        data = _draft_data(
            photos=[_photo("s1")],
            safety={"photo_id": _pid("s1"), "classification": "safety_person", "confidence": 0.9},
        )
        svc = PreviewAggregationService(self.db, ".")
        out = svc._build_safety_photo(data)
        self.assertEqual(out["selected_count"], 1)
        self.assertEqual(out["selected_photo_id"], _pid("s1"))


class TestDraftHardDelete(Base):
    def _create_child_tables(self):
        self.db.execute("create table ai_daily_report_actions (id integer primary key, draft_id integer, action_id text)")
        self.db.execute("create table ai_daily_report_attachment_manifests (id integer primary key, manifest_id text, draft_id integer)")
        self.db.execute("create table ai_daily_report_prepared_assets (id integer primary key, manifest_id text)")
        self.db.execute("create table ai_daily_report_manifest_sources (id integer primary key, manifest_id text)")
        self.db.execute("create table ai_daily_report_manifest_roles (id integer primary key, manifest_id text)")
        self.db.execute("create table ai_daily_report_formal_commits (id integer primary key, draft_id integer)")
        self.db.commit()

    def test_14_physical_delete_removes_draft_and_children(self):
        self._create_child_tables()
        self._insert_draft(_draft_data(photos=[_photo("s1")]))
        self.db.execute("insert into ai_daily_report_actions (draft_id, action_id) values (1, 'a1')")
        self.db.execute("insert into ai_daily_report_attachment_manifests (manifest_id, draft_id) values ('m1', 1)")
        self.db.execute("insert into ai_daily_report_prepared_assets (manifest_id) values ('m1')")
        self.db.execute("insert into ai_daily_report_manifest_sources (manifest_id) values ('m1')")
        self.db.execute("insert into ai_daily_report_manifest_roles (manifest_id) values ('m1')")
        self.db.execute("insert into ai_daily_report_formal_commits (draft_id) values (1)")
        self.db.commit()

        self.svc.delete_draft(1)
        self.db.commit()

        self.assertIsNone(self._draft_row())
        self.assertEqual(self.db.execute("select count(*) from ai_daily_report_actions where draft_id=1").fetchone()[0], 0)
        self.assertEqual(self.db.execute("select count(*) from ai_daily_report_attachment_manifests where draft_id=1").fetchone()[0], 0)
        self.assertEqual(self.db.execute("select count(*) from ai_daily_report_prepared_assets where manifest_id='m1'").fetchone()[0], 0)
        self.assertEqual(self.db.execute("select count(*) from ai_daily_report_manifest_sources where manifest_id='m1'").fetchone()[0], 0)
        self.assertEqual(self.db.execute("select count(*) from ai_daily_report_manifest_roles where manifest_id='m1'").fetchone()[0], 0)
        self.assertEqual(self.db.execute("select count(*) from ai_daily_report_formal_commits where draft_id=1").fetchone()[0], 0)

    def test_15_physical_delete_works_without_child_tables(self):
        self._insert_draft(_draft_data(photos=[_photo("s1")]))
        self.svc.delete_draft(1)
        self.db.commit()
        self.assertIsNone(self._draft_row())

    def test_16_other_drafts_survive_delete(self):
        self._insert_draft(_draft_data(photos=[_photo("s1")]), draft_id=1)
        self._insert_draft(_draft_data(photos=[_photo("s2")]), draft_id=2)
        self.svc.delete_draft(1)
        self.db.commit()
        self.assertIsNotNone(self._draft_row(2))


if __name__ == "__main__":
    unittest.main()
