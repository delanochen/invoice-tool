"""AI Daily Report Phase 7 Tests - Validation Engine

Covers:
- ValidationEngine pure logic (no DB, no filesystem, no network)
- All 20 rule categories (DRFT, SRVC, WRKR, TRSP, ORIG, OVRN, ROUT, MILE,
  EVID, TIME, PTML, SAFE, SVCF, WORK, SDES, WAIT, AIVR, USRO, VISN, PROV)
- issue_key / issue_fingerprint design
- Warning acknowledgement (isolation, stale, ERROR/INFO rejection)
- ValidationContextBuilder (DB access for authoritative data)
- API endpoints (GET validation, POST acknowledge)
- Security (IDOR, CSRF, optimistic locking 409)
- Preview integration (read-only, 0 DB writes, 0 external, 0 fs scan)
- Determinism (same input → same fingerprint)
- Duplicate issue dedup (AIVR + specific rule, WORK-001 + SDES)
- Address normalization
- Phase 1-6 regression compatibility
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Setup test environment
os.environ.setdefault("SECRET_KEY", "test-secret-phase7")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


def _make_valid_draft(**overrides):
    """Build a minimal valid draft dict. Overrides merge on top."""
    draft = {
        "service_order_id": 100,
        "report_date": "2026-09-14",
        "workers": [
            {
                "user_id": 101, "name": "Ethan", "transportation": "self_drive",
                "origin": "100 Main St", "origin_source": "user_input",
                "origin_confirmed": True, "destination": "123 Site Ave, Spring, TX 77386",
                "destination_source": "service_order", "trip_type": "round_trip",
                "route_status": "success", "route_distance_meters": 16093.44,
                "one_way_miles": 10.0, "reported_miles": 20.0,
                "route_provider": "google_routes",
            }
        ],
        "work_items": [{"equipment": "Transformer A", "action": "repair", "description": "Replaced fuse"}],
        "arrival_time": "08:00", "departure_time": "17:00",
        "arrival_time_source": "manual", "departure_time_source": "manual",
        "selected_safety_photo": {"photo_id": "photo_safety_001", "confidence": 0.9, "classification": "safety_person"},
        "selected_service_photos": [{"photo_id": "photo_service_001", "classification": "equipment"}],
        "photo_candidates": [
            {"photo_id": "photo_safety_001", "photo_hash": "photo_safety_001"},
            {"photo_id": "photo_service_001", "photo_hash": "photo_service_001"},
        ],
        "service_description": "Replaced fuse on Transformer A",
        "waiting_hours": 0.0, "waiting_reason": "",
        "verification_required": False, "verification_fields": [],
        "ai_metadata": {"model": "deepseek-v4-flash", "created_by": "ai"},
        "photo_timeline_status": "ready",
        "photo_classification_status": "classified",
        "evidence_records": [],
    }
    draft.update(overrides)
    return draft


def _make_context(**overrides):
    """Build a minimal valid ValidationContext."""
    from ai_daily_report.validation_engine import ValidationContext
    ctx = ValidationContext(
        service_order_exists=True,
        service_order={"id": 100, "order_number": "SO-001", "site_address": "123 Site Ave, Spring, TX 77386"},
        workers={101: {"name": "Ethan", "role": "employee", "is_active": True}},
        site_address="123 Site Ave, Spring, TX 77386",
        site_name="Test Site",
        order_number="SO-001",
    )
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


# ─── Unit Tests: Pure Engine Logic ──────────────────────────────────────────

class TestValidationEnginePure(unittest.TestCase):
    """Test ValidationEngine as pure function — no DB, no filesystem, no network."""

    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_valid_draft_has_no_errors(self):
        draft = _make_valid_draft()
        ctx = _make_context()
        result = self.engine.validate(draft, ctx, draft_version=1)
        self.assertEqual(result.error_count, 0, f"Unexpected errors: {[i.rule_id for i in result.errors]}")
        self.assertTrue(result.is_valid)
        self.assertTrue(result.can_proceed)

    def test_engine_does_not_access_db(self):
        """Verify engine has no db attribute and doesn't accept db connection."""
        self.assertFalse(hasattr(self.engine, 'db'))
        self.assertFalse(hasattr(self.engine, 'connection'))

    def test_deterministic_same_input_same_fingerprint(self):
        draft = _make_valid_draft()
        ctx = _make_context()
        r1 = self.engine.validate(draft, ctx, draft_version=1)
        r2 = self.engine.validate(draft, ctx, draft_version=1)
        self.assertEqual(r1.validation_fingerprint, r2.validation_fingerprint)
        self.assertEqual(r1.draft_data_hash, r2.draft_data_hash)
        self.assertEqual(r1.context_hash, r2.context_hash)

    def test_draft_data_change_changes_fingerprint(self):
        draft1 = _make_valid_draft()
        draft2 = _make_valid_draft(service_description="Different description")
        ctx = _make_context()
        r1 = self.engine.validate(draft1, ctx, draft_version=1)
        r2 = self.engine.validate(draft2, ctx, draft_version=1)
        self.assertNotEqual(r1.draft_data_hash, r2.draft_data_hash)
        self.assertNotEqual(r1.validation_fingerprint, r2.validation_fingerprint)

    def test_context_change_changes_fingerprint(self):
        draft = _make_valid_draft()
        ctx1 = _make_context()
        ctx2 = _make_context(site_address="Different Address")
        r1 = self.engine.validate(draft, ctx1, draft_version=1)
        r2 = self.engine.validate(draft, ctx2, draft_version=1)
        self.assertNotEqual(r1.context_hash, r2.context_hash)

    def test_generated_at_not_in_fingerprint(self):
        """Adding generated_at fields should not change draft_data_hash."""
        draft1 = _make_valid_draft()
        draft2 = _make_valid_draft(
            confirmed_at="2026-09-14T12:00:00Z",
            photo_timeline_generated_at="2026-09-14T10:00:00Z",
            photo_classification_generated_at="2026-09-14T10:01:00Z",
        )
        ctx = _make_context()
        r1 = self.engine.validate(draft1, ctx, draft_version=1)
        r2 = self.engine.validate(draft2, ctx, draft_version=1)
        self.assertEqual(r1.draft_data_hash, r2.draft_data_hash)

    def test_empty_dict_draft_no_crash(self):
        result = self.engine.validate({}, _make_context(), draft_version=1)
        self.assertIsNotNone(result)
        self.assertGreater(result.error_count, 0)

    def test_none_draft_data_no_crash(self):
        result = self.engine.validate(None, _make_context(), draft_version=1)
        self.assertIsNotNone(result)


# ─── Rule Category Tests ────────────────────────────────────────────────────

class TestDraftStateRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_drft001_corrupted_draft(self):
        draft = _make_valid_draft(verification_required=True, verification_fields=["draft_data_corrupted"])
        result = self.engine.validate(draft, _make_context(), 1)
        drft = [i for i in result.issues if i.rule_id == "DRFT-001"]
        self.assertEqual(len(drft), 1)
        self.assertEqual(drft[0].severity, "error")
        self.assertTrue(drft[0].blocking)

    def test_drft002_invalid_date(self):
        draft = _make_valid_draft(report_date="not-a-date")
        result = self.engine.validate(draft, _make_context(), 1)
        drft = [i for i in result.issues if i.rule_id == "DRFT-002"]
        self.assertEqual(len(drft), 1)
        self.assertEqual(drft[0].severity, "error")

    def test_drft004_empty_draft(self):
        draft = {"service_order_id": 100, "report_date": "2026-09-14", "workers": [], "work_items": [], "service_description": ""}
        result = self.engine.validate(draft, _make_context(), 1)
        drft = [i for i in result.issues if i.rule_id == "DRFT-004"]
        self.assertEqual(len(drft), 1)
        self.assertEqual(drft[0].severity, "error")


class TestServiceOrderRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_srvc001_order_not_found(self):
        draft = _make_valid_draft()
        ctx = _make_context(service_order_exists=False, site_address="")
        result = self.engine.validate(draft, ctx, 1)
        srvc = [i for i in result.issues if i.rule_id == "SRVC-001"]
        self.assertEqual(len(srvc), 1)
        self.assertEqual(srvc[0].severity, "error")

    def test_srvc002_missing_site_address(self):
        draft = _make_valid_draft()
        ctx = _make_context(site_address="")
        result = self.engine.validate(draft, ctx, 1)
        srvc = [i for i in result.issues if i.rule_id == "SRVC-002"]
        self.assertEqual(len(srvc), 1)
        self.assertEqual(srvc[0].severity, "warning")


class TestWorkerRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_wrkr001_no_workers(self):
        draft = _make_valid_draft(workers=[])
        result = self.engine.validate(draft, _make_context(), 1)
        wrkr = [i for i in result.issues if i.rule_id == "WRKR-001"]
        self.assertEqual(len(wrkr), 1)
        self.assertEqual(wrkr[0].severity, "error")

    def test_wrkr002_worker_not_in_db(self):
        draft = _make_valid_draft(workers=[{
            "user_id": 999, "name": "Ghost", "transportation": "self_drive",
            "origin": "X", "origin_confirmed": True, "destination": "Y",
            "trip_type": "round_trip", "route_status": "success",
            "one_way_miles": 10, "reported_miles": 20,
        }])
        ctx = _make_context(workers={})
        result = self.engine.validate(draft, ctx, 1)
        wrkr = [i for i in result.issues if i.rule_id == "WRKR-002"]
        self.assertEqual(len(wrkr), 1)
        self.assertEqual(wrkr[0].severity, "error")

    def test_wrkr003_inactive_worker(self):
        draft = _make_valid_draft()
        ctx = _make_context(workers={101: {"name": "Ethan", "is_active": False}})
        result = self.engine.validate(draft, ctx, 1)
        wrkr = [i for i in result.issues if i.rule_id == "WRKR-003"]
        self.assertEqual(len(wrkr), 1)
        self.assertEqual(wrkr[0].severity, "error")

    def test_wrkr004_duplicate_worker(self):
        draft = _make_valid_draft(workers=[
            {"user_id": 101, "name": "Ethan", "transportation": "self_drive",
             "origin": "X", "origin_confirmed": True, "destination": "Y",
             "trip_type": "round_trip", "route_status": "success",
             "one_way_miles": 10, "reported_miles": 20},
            {"user_id": 101, "name": "Ethan Duplicate", "transportation": "self_drive",
             "origin": "X2", "origin_confirmed": True, "destination": "Y",
             "trip_type": "round_trip", "route_status": "success",
             "one_way_miles": 10, "reported_miles": 20},
        ])
        ctx = _make_context(workers={101: {"name": "Ethan", "is_active": True}})
        result = self.engine.validate(draft, ctx, 1)
        wrkr = [i for i in result.issues if i.rule_id == "WRKR-004"]
        self.assertEqual(len(wrkr), 1)


class TestTransportationRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_trsp001_invalid_transportation(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["transportation"] = "teleport"
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        trsp = [i for i in result.issues if i.rule_id == "TRSP-001"]
        self.assertEqual(len(trsp), 1)
        self.assertEqual(trsp[0].severity, "error")


class TestOriginDestinationRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_orig001_missing_origin(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["origin"] = None
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        orig = [i for i in result.issues if i.rule_id == "ORIG-001"]
        self.assertEqual(len(orig), 1)
        self.assertEqual(orig[0].severity, "error")

    def test_orig002_origin_unconfirmed(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["origin_confirmed"] = False
        workers[0]["origin_source"] = "employee_default"
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        orig = [i for i in result.issues if i.rule_id == "ORIG-002"]
        self.assertEqual(len(orig), 1)
        self.assertEqual(orig[0].severity, "warning")

    def test_orig003_normalized_same_address_no_error(self):
        """'Spring, TX 77386' vs formatted same address should NOT trigger ORIG-003."""
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["destination"] = "123 Site Ave, Spring, TX 77386"
        draft = _make_valid_draft(workers=workers)
        ctx = _make_context(site_address="123 SITE AVE,  SPRING,  TX 77386")
        result = self.engine.validate(draft, ctx, 1)
        orig = [i for i in result.issues if i.rule_id == "ORIG-003"]
        self.assertEqual(len(orig), 0)

    def test_orig003_truly_different_address_error(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["destination"] = "123 Site Ave, Spring, TX 77386"
        draft = _make_valid_draft(workers=workers)
        ctx = _make_context(site_address="456 Other St, Houston, TX 77001")
        result = self.engine.validate(draft, ctx, 1)
        orig = [i for i in result.issues if i.rule_id == "ORIG-003"]
        self.assertEqual(len(orig), 1)
        self.assertEqual(orig[0].severity, "error")

    def test_orig004_missing_destination(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["destination"] = None
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        orig = [i for i in result.issues if i.rule_id == "ORIG-004"]
        self.assertEqual(len(orig), 1)
        self.assertEqual(orig[0].severity, "error")


class TestTripTypeRules(unittest.TestCase):
    """住宿（OVRN-001）规则已移除，行程类型有默认值，不再产生阻塞性 ERROR。"""

    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_no_overnight_rule_any_more(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["overnight_stay"] = None
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        ovrn = [i for i in result.issues if i.rule_id == "OVRN-001"]
        self.assertEqual(len(ovrn), 0)

    def test_missing_trip_type_falls_back_to_round_trip(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0].pop("trip_type", None)
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        # 往返口径：reported 应为 one_way × 2，这里若不适用会给出 MILE-005 warning
        worker = draft["workers"][0]
        if worker.get("reported_miles") is not None and worker.get("one_way_miles") is not None:
            mile = [i for i in result.issues if i.rule_id == "MILE-005"]
            expected_warn = abs(worker["reported_miles"] - worker["one_way_miles"] * 2) > 0.01
            self.assertEqual(len(mile), 1 if expected_warn else 0)


class TestRouteStatusRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_rout001_route_failed_warning(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["route_status"] = "failed"
        workers[0]["route_error"] = "API timeout"
        workers[0]["reported_miles"] = None
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        rout = [i for i in result.issues if i.rule_id == "ROUT-001"]
        self.assertEqual(len(rout), 1)
        self.assertEqual(rout[0].severity, "warning")

    def test_rout002_verification_required_warning(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["route_status"] = "verification_required"
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        rout = [i for i in result.issues if i.rule_id == "ROUT-002"]
        self.assertEqual(len(rout), 1)
        self.assertEqual(rout[0].severity, "warning")

    def test_rout003_not_calculated_warning(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["route_status"] = "not_calculated"
        workers[0]["reported_miles"] = None
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        rout = [i for i in result.issues if i.rule_id == "ROUT-003"]
        self.assertEqual(len(rout), 1)


class TestMileageRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_mile001_no_mileage_route_failed_error(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["route_status"] = "failed"
        workers[0]["reported_miles"] = None
        workers[0]["one_way_miles"] = None
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        mile = [i for i in result.issues if i.rule_id == "MILE-001"]
        self.assertEqual(len(mile), 1)
        self.assertEqual(mile[0].severity, "error")

    def test_mile002_zero_mileage_error(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["reported_miles"] = 0
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        mile = [i for i in result.issues if i.rule_id == "MILE-002"]
        self.assertEqual(len(mile), 1)

    def test_mile003_high_mileage_warning_per_worker(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["reported_miles"] = 600.0
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        mile = [i for i in result.issues if i.rule_id == "MILE-003"]
        self.assertEqual(len(mile), 1)
        self.assertEqual(mile[0].severity, "warning")
        self.assertIn("worker", mile[0].issue_key)

    def test_mile003_two_workers_two_issue_keys(self):
        """Same rule MILE-003 on two workers → two different issue_keys."""
        w1 = dict(_make_valid_draft()["workers"][0])
        w1["reported_miles"] = 600.0
        w2 = dict(w1)
        w2["user_id"] = 102
        w2["name"] = "张三"
        w2["origin"] = "Different Origin"
        draft = _make_valid_draft(workers=[w1, w2])
        ctx = _make_context(workers={
            101: {"name": "Ethan", "is_active": True},
            102: {"name": "张三", "is_active": True},
        })
        result = self.engine.validate(draft, ctx, 1)
        mile = [i for i in result.issues if i.rule_id == "MILE-003"]
        self.assertEqual(len(mile), 2)
        self.assertNotEqual(mile[0].issue_key, mile[1].issue_key)
        self.assertIn("worker:101", mile[0].issue_key)
        self.assertIn("worker:102", mile[1].issue_key)

    def test_mile005_round_trip_inconsistency_warning(self):
        """reported_miles should be one_way*2 when trip_type=round_trip. Inconsistent → WARNING."""
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["trip_type"] = "round_trip"
        workers[0]["one_way_miles"] = 10.0
        workers[0]["reported_miles"] = 10.0  # should be 20.0
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        mile = [i for i in result.issues if i.rule_id == "MILE-005"]
        self.assertEqual(len(mile), 1)
        self.assertEqual(mile[0].severity, "warning")

    def test_mile005_one_way_consistent_no_warning(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["trip_type"] = "one_way"
        workers[0]["one_way_miles"] = 10.0
        workers[0]["reported_miles"] = 10.0  # 单程：报告里程 = 单程
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        mile = [i for i in result.issues if i.rule_id == "MILE-005"]
        self.assertEqual(len(mile), 0)

    def test_mile006_verification_required_warning(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["mileage_verification_required"] = True
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        mile = [i for i in result.issues if i.rule_id == "MILE-006"]
        self.assertEqual(len(mile), 1)


class TestEvidenceRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_evid001_failed_evidence_warning(self):
        draft = _make_valid_draft(evidence_records=[{
            "evidence_id": "ev-001", "evidence_status": "failed",
            "error": "generation error", "worker_user_id": 101,
        }])
        result = self.engine.validate(draft, _make_context(), 1)
        evid = [i for i in result.issues if i.rule_id == "EVID-001"]
        self.assertEqual(len(evid), 1)
        self.assertEqual(evid[0].severity, "warning")

    def test_evid002_stale_evidence_warning(self):
        draft = _make_valid_draft(evidence_records=[{
            "evidence_id": "ev-002", "evidence_status": "stale", "worker_user_id": 101,
        }])
        result = self.engine.validate(draft, _make_context(), 1)
        evid = [i for i in result.issues if i.rule_id == "EVID-002"]
        self.assertEqual(len(evid), 1)

    def test_evidence_does_not_access_filesystem(self):
        """Evidence validation only reads metadata — no os.path.exists, no file open."""
        draft = _make_valid_draft(evidence_records=[{
            "evidence_id": "ev-003", "evidence_status": "ready",
            "file_relative_path": "/nonexistent/path/should/not/be/checked.png",
            "file_sha256": "abc123", "worker_user_id": 101,
            "reported_miles": 20.0,
        }])
        # Should not raise even though file doesn't exist
        result = self.engine.validate(draft, _make_context(), 1)
        self.assertIsNotNone(result)


class TestTimeRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_time001_missing_arrival_error(self):
        draft = _make_valid_draft(arrival_time=None)
        result = self.engine.validate(draft, _make_context(), 1)
        time = [i for i in result.issues if i.rule_id == "TIME-001"]
        self.assertEqual(len(time), 1)
        self.assertEqual(time[0].severity, "error")

    def test_time002_missing_departure_error(self):
        draft = _make_valid_draft(departure_time=None)
        result = self.engine.validate(draft, _make_context(), 1)
        time = [i for i in result.issues if i.rule_id == "TIME-002"]
        self.assertEqual(len(time), 1)

    def test_time003_arrival_after_departure_error(self):
        draft = _make_valid_draft(arrival_time="17:00", departure_time="08:00")
        result = self.engine.validate(draft, _make_context(), 1)
        time = [i for i in result.issues if i.rule_id == "TIME-003"]
        self.assertEqual(len(time), 1)
        self.assertEqual(time[0].severity, "error")


class TestPhotoTimelineRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_ptml001_failed_warning(self):
        draft = _make_valid_draft(photo_timeline_status="failed")
        result = self.engine.validate(draft, _make_context(), 1)
        ptml = [i for i in result.issues if i.rule_id == "PTML-001"]
        self.assertEqual(len(ptml), 1)

    def test_ptml002_suspicious_warning(self):
        draft = _make_valid_draft(photo_timeline_status="suspicious")
        result = self.engine.validate(draft, _make_context(), 1)
        ptml = [i for i in result.issues if i.rule_id == "PTML-002"]
        self.assertEqual(len(ptml), 1)

    def test_ptml005_no_photos_but_photo_time_error(self):
        draft = _make_valid_draft(
            photo_candidates=[],
            arrival_time_source="photo_timeline_confirmed",
        )
        result = self.engine.validate(draft, _make_context(), 1)
        ptml = [i for i in result.issues if i.rule_id == "PTML-005"]
        self.assertGreaterEqual(len(ptml), 1)


class TestSafetyPhotoRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_safe001_no_safety_photo_error(self):
        draft = _make_valid_draft(selected_safety_photo=None)
        result = self.engine.validate(draft, _make_context(), 1)
        safe = [i for i in result.issues if i.rule_id == "SAFE-001"]
        self.assertEqual(len(safe), 1)
        self.assertEqual(safe[0].severity, "error")

    def test_safe002_verification_required_warning(self):
        draft = _make_valid_draft(safety_photo_verification_required=True)
        result = self.engine.validate(draft, _make_context(), 1)
        safe = [i for i in result.issues if i.rule_id == "SAFE-002"]
        self.assertEqual(len(safe), 1)

    def test_safe003_low_confidence_warning(self):
        sp = dict(_make_valid_draft()["selected_safety_photo"])
        sp["confidence"] = 0.3
        draft = _make_valid_draft(selected_safety_photo=sp)
        result = self.engine.validate(draft, _make_context(), 1)
        safe = [i for i in result.issues if i.rule_id == "SAFE-003"]
        self.assertEqual(len(safe), 1)


class TestServicePhotoRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_svcf001_no_service_photos_error(self):
        draft = _make_valid_draft(selected_service_photos=[])
        result = self.engine.validate(draft, _make_context(), 1)
        svcf = [i for i in result.issues if i.rule_id == "SVCF-001"]
        self.assertEqual(len(svcf), 1)
        self.assertEqual(svcf[0].severity, "error")

    def test_svcf002_too_many_photos_warning(self):
        photos = [{"photo_id": f"p{i}", "classification": "equipment"} for i in range(12)]
        candidates = [{"photo_id": f"p{i}", "photo_hash": f"p{i}"} for i in range(12)]
        draft = _make_valid_draft(selected_service_photos=photos, photo_candidates=candidates)
        result = self.engine.validate(draft, _make_context(), 1)
        svcf = [i for i in result.issues if i.rule_id == "SVCF-002"]
        self.assertEqual(len(svcf), 1)


class TestWorkItemRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_work001_canonical_empty_report_error(self):
        """Has workers but no work_items, no waiting, no description → WORK-001 only."""
        draft = _make_valid_draft(
            work_items=[], waiting_hours=0, service_description="",
        )
        result = self.engine.validate(draft, _make_context(), 1)
        work = [i for i in result.issues if i.rule_id == "WORK-001"]
        self.assertEqual(len(work), 1)
        self.assertEqual(work[0].severity, "error")

    def test_work001_and_sdes001_not_duplicate(self):
        """WORK-001 canonical empty → SDES-001 must NOT also fire."""
        draft = _make_valid_draft(
            work_items=[], waiting_hours=0, service_description="",
        )
        result = self.engine.validate(draft, _make_context(), 1)
        work = [i for i in result.issues if i.rule_id == "WORK-001"]
        sdes = [i for i in result.issues if i.rule_id == "SDES-001"]
        self.assertEqual(len(work), 1)
        self.assertEqual(len(sdes), 0)

    def test_work002_item_no_equipment_no_desc_warning(self):
        draft = _make_valid_draft(work_items=[{"equipment": "", "action": "", "description": ""}])
        result = self.engine.validate(draft, _make_context(), 1)
        work = [i for i in result.issues if i.rule_id == "WORK-002"]
        self.assertEqual(len(work), 1)

    def test_waiting_only_report_no_work001(self):
        """waiting_hours > 0 with reason → not WORK-001 (waiting-only report is valid)."""
        draft = _make_valid_draft(
            work_items=[], service_description="",
            waiting_hours=2.0, waiting_reason="Waiting for parts",
        )
        result = self.engine.validate(draft, _make_context(), 1)
        work = [i for i in result.issues if i.rule_id == "WORK-001"]
        self.assertEqual(len(work), 0)


class TestServiceDescriptionRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_sdes001_has_work_items_no_description_warning(self):
        draft = _make_valid_draft(service_description="")
        result = self.engine.validate(draft, _make_context(), 1)
        sdes = [i for i in result.issues if i.rule_id == "SDES-001"]
        self.assertEqual(len(sdes), 1)
        self.assertEqual(sdes[0].severity, "warning")

    def test_sdes002_short_description_info(self):
        draft = _make_valid_draft(service_description="Hi")
        result = self.engine.validate(draft, _make_context(), 1)
        sdes = [i for i in result.issues if i.rule_id == "SDES-002"]
        self.assertEqual(len(sdes), 1)
        self.assertEqual(sdes[0].severity, "info")


class TestWaitingTimeRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_wait001_waiting_no_reason_error(self):
        draft = _make_valid_draft(waiting_hours=3.0, waiting_reason="")
        result = self.engine.validate(draft, _make_context(), 1)
        wait = [i for i in result.issues if i.rule_id == "WAIT-001"]
        self.assertEqual(len(wait), 1)
        self.assertEqual(wait[0].severity, "error")

    def test_wait002_negative_waiting_error(self):
        draft = _make_valid_draft(waiting_hours=-1.0)
        result = self.engine.validate(draft, _make_context(), 1)
        wait = [i for i in result.issues if i.rule_id == "WAIT-002"]
        self.assertEqual(len(wait), 1)

    def test_wait003_excessive_waiting_warning(self):
        draft = _make_valid_draft(waiting_hours=15.0, waiting_reason="Long delay")
        result = self.engine.validate(draft, _make_context(), 1)
        wait = [i for i in result.issues if i.rule_id == "WAIT-003"]
        self.assertEqual(len(wait), 1)


class TestAIVRRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_aivr003_unknown_verification_field_safe_warning(self):
        draft = _make_valid_draft(
            verification_required=True,
            verification_fields=["completely_unknown_field_xyz"],
        )
        result = self.engine.validate(draft, _make_context(), 1)
        aivr = [i for i in result.issues if i.rule_id == "AIVR-003"]
        self.assertEqual(len(aivr), 1)
        self.assertEqual(aivr[0].severity, "warning")

    def test_aivr_dedup_with_specific_rule(self):
        """verification_fields=['origin'] + ORIG-002 already present → no duplicate AIVR."""
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["origin_confirmed"] = False
        workers[0]["origin_source"] = "employee_default"
        draft = _make_valid_draft(
            workers=workers,
            verification_required=True,
            verification_fields=["origin"],
        )
        result = self.engine.validate(draft, _make_context(), 1)
        orig2 = [i for i in result.issues if i.rule_id == "ORIG-002"]
        aivr = [i for i in result.issues if i.rule_id.startswith("AIVR")]
        self.assertEqual(len(orig2), 1)
        self.assertEqual(len(aivr), 0, f"AIVR should not duplicate ORIG-002: {[i.rule_id for i in aivr]}")

    def test_aivr_no_crash_on_non_list_fields(self):
        draft = _make_valid_draft(verification_required=True, verification_fields="not_a_list")
        result = self.engine.validate(draft, _make_context(), 1)
        self.assertIsNotNone(result)


class TestVisionRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_visn001_classification_failed_warning(self):
        draft = _make_valid_draft(photo_classification_status="failed")
        result = self.engine.validate(draft, _make_context(), 1)
        visn = [i for i in result.issues if i.rule_id == "VISN-001"]
        self.assertEqual(len(visn), 1)


class TestProvenanceRules(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_prov001_missing_ai_model_info(self):
        draft = _make_valid_draft(ai_metadata={})
        result = self.engine.validate(draft, _make_context(), 1)
        prov = [i for i in result.issues if i.rule_id == "PROV-001"]
        self.assertEqual(len(prov), 1)
        self.assertEqual(prov[0].severity, "info")


# ─── issue_key / issue_fingerprint Tests ────────────────────────────────────

class TestIssueKeyAndFingerprint(unittest.TestCase):
    def test_make_issue_key_format(self):
        from ai_daily_report.validation_engine import make_issue_key
        key = make_issue_key("MILE-003", "worker", 123)
        self.assertEqual(key, "MILE-003:worker:123")

    def test_issue_fingerprint_changes_with_relevant_values(self):
        from ai_daily_report.validation_engine import compute_issue_fingerprint
        fp1 = compute_issue_fingerprint("MILE-003", "worker", "101", {"reported_miles": 500})
        fp2 = compute_issue_fingerprint("MILE-003", "worker", "101", {"reported_miles": 900})
        self.assertNotEqual(fp1, fp2)

    def test_issue_fingerprint_stable_same_values(self):
        from ai_daily_report.validation_engine import compute_issue_fingerprint
        fp1 = compute_issue_fingerprint("MILE-003", "worker", "101", {"reported_miles": 500})
        fp2 = compute_issue_fingerprint("MILE-003", "worker", "101", {"reported_miles": 500})
        self.assertEqual(fp1, fp2)

    def test_different_subjects_different_keys(self):
        from ai_daily_report.validation_engine import make_issue_key
        k1 = make_issue_key("MILE-003", "worker", "101")
        k2 = make_issue_key("MILE-003", "worker", "102")
        self.assertNotEqual(k1, k2)

    def test_no_subject_uses_global(self):
        from ai_daily_report.validation_engine import ValidationIssue
        issue = ValidationIssue("DRFT-004", "error", "empty", subject_type="draft", subject_id="")
        self.assertIn("global", issue.issue_key)


# ─── Acknowledgement Tests ──────────────────────────────────────────────────

class TestAcknowledgement(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def _make_high_mileage_draft(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["reported_miles"] = 600.0
        return _make_valid_draft(workers=workers)

    def test_warning_acknowledged_can_proceed_still_true(self):
        draft = self._make_high_mileage_draft()
        ctx = _make_context()
        result = self.engine.validate(draft, ctx, 1)
        mile = [i for i in result.issues if i.rule_id == "MILE-003"][0]
        self.assertFalse(mile.acknowledged)

        from ai_daily_report.validation_engine import add_acknowledgement
        add_acknowledgement(draft, mile.issue_key, mile.rule_id,
            mile.issue_fingerprint, result.validation_fingerprint, 1, 101, "2026-09-14T00:00:00Z")
        result2 = self.engine.validate(draft, ctx, 1)
        mile2 = [i for i in result2.issues if i.rule_id == "MILE-003"][0]
        self.assertTrue(mile2.acknowledged)
        self.assertTrue(result2.can_proceed)  # WARNING doesn't block anyway

    def test_error_cannot_be_acknowledged(self):
        """ERROR acknowledgement should not remove the ERROR or change blocking."""
        draft = _make_valid_draft(arrival_time=None)  # TIME-001 ERROR
        ctx = _make_context()
        result = self.engine.validate(draft, ctx, 1)
        time_err = [i for i in result.issues if i.rule_id == "TIME-001"][0]
        self.assertEqual(time_err.severity, "error")
        self.assertTrue(time_err.blocking)
        self.assertFalse(time_err.acknowledgement_required)

        # Try to acknowledge (simulate what API would reject)
        from ai_daily_report.validation_engine import add_acknowledgement
        add_acknowledgement(draft, time_err.issue_key, time_err.rule_id,
            time_err.issue_fingerprint, result.validation_fingerprint, 1, 101, "2026-09-14T00:00:00Z")
        result2 = self.engine.validate(draft, ctx, 1)
        time_err2 = [i for i in result2.issues if i.rule_id == "TIME-001"][0]
        # Engine only applies acks to WARNINGs, so ERROR stays unacknowledged
        self.assertFalse(time_err2.acknowledged)
        self.assertFalse(result2.is_valid)
        self.assertFalse(result2.can_proceed)

    def test_acknowledgement_isolation_between_workers(self):
        """Acknowledge worker A's MILE-003 → worker B's MILE-003 unaffected."""
        w1 = dict(_make_valid_draft()["workers"][0])
        w1["reported_miles"] = 600.0
        w2 = dict(w1)
        w2["user_id"] = 102
        w2["name"] = "张三"
        w2["origin"] = "Other Origin"
        draft = _make_valid_draft(workers=[w1, w2])
        ctx = _make_context(workers={
            101: {"name": "Ethan", "is_active": True},
            102: {"name": "张三", "is_active": True},
        })
        result = self.engine.validate(draft, ctx, 1)
        miles = {i.issue_key: i for i in result.issues if i.rule_id == "MILE-003"}
        key_a = [k for k in miles if "101" in k][0]
        key_b = [k for k in miles if "102" in k][0]

        from ai_daily_report.validation_engine import add_acknowledgement
        add_acknowledgement(draft, key_a, "MILE-003",
            miles[key_a].issue_fingerprint, result.validation_fingerprint, 1, 101, "2026-09-14T00:00:00Z")
        result2 = self.engine.validate(draft, ctx, 1)
        miles2 = {i.issue_key: i for i in result2.issues if i.rule_id == "MILE-003"}
        self.assertTrue(miles2[key_a].acknowledged)
        self.assertFalse(miles2[key_b].acknowledged)

    def test_stale_acknowledgement_invalidated_on_value_change(self):
        """Change reported_miles after ack → old ack auto-invalidated."""
        draft = self._make_high_mileage_draft()
        ctx = _make_context()
        result = self.engine.validate(draft, ctx, 1)
        mile = [i for i in result.issues if i.rule_id == "MILE-003"][0]

        from ai_daily_report.validation_engine import add_acknowledgement
        add_acknowledgement(draft, mile.issue_key, mile.rule_id,
            mile.issue_fingerprint, result.validation_fingerprint, 1, 101, "2026-09-14T00:00:00Z")
        result2 = self.engine.validate(draft, ctx, 1)
        self.assertTrue([i for i in result2.issues if i.rule_id == "MILE-003"][0].acknowledged)

        # Change the relevant value
        draft["workers"][0]["reported_miles"] = 900.0
        result3 = self.engine.validate(draft, ctx, 1)
        mile3 = [i for i in result3.issues if i.rule_id == "MILE-003"][0]
        self.assertFalse(mile3.acknowledged, "Old ack should be invalidated after value change")

        # USRO-001 should report stale ack
        usro = [i for i in result3.issues if i.rule_id == "USRO-001"]
        self.assertEqual(len(usro), 1)

    def test_unrelated_mutation_preserves_acknowledgement(self):
        """Change service_description (unrelated to MILE-003) → ack preserved."""
        draft = self._make_high_mileage_draft()
        ctx = _make_context()
        result = self.engine.validate(draft, ctx, 1)
        mile = [i for i in result.issues if i.rule_id == "MILE-003"][0]

        from ai_daily_report.validation_engine import add_acknowledgement
        add_acknowledgement(draft, mile.issue_key, mile.rule_id,
            mile.issue_fingerprint, result.validation_fingerprint, 1, 101, "2026-09-14T00:00:00Z")

        # Unrelated change
        draft["service_description"] = "Updated description text"
        result2 = self.engine.validate(draft, ctx, 1)
        mile2 = [i for i in result2.issues if i.rule_id == "MILE-003"][0]
        self.assertTrue(mile2.acknowledged, "Unrelated mutation should preserve ack")

    def test_acknowledgement_records_required_fields(self):
        from ai_daily_report.validation_engine import add_acknowledgement, get_acknowledgements
        draft = self._make_high_mileage_draft()
        ctx = _make_context()
        result = self.engine.validate(draft, ctx, 1)
        mile = [i for i in result.issues if i.rule_id == "MILE-003"][0]
        add_acknowledgement(draft, mile.issue_key, mile.rule_id,
            mile.issue_fingerprint, result.validation_fingerprint, 5, 101, "2026-09-14T00:00:00Z")
        acks = get_acknowledgements(draft)
        self.assertEqual(len(acks), 1)
        ack = acks[0]
        self.assertIn("issue_key", ack)
        self.assertIn("rule_id", ack)
        self.assertIn("acknowledged_by", ack)
        self.assertIn("acknowledged_at", ack)
        self.assertIn("draft_version_at_ack", ack)
        self.assertIn("validation_fingerprint_at_ack", ack)
        self.assertIn("issue_fingerprint_at_ack", ack)


# ─── Duplicate Issue Dedup Tests ────────────────────────────────────────────

class TestDuplicateDedup(unittest.TestCase):
    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_no_duplicate_issue_keys_in_result(self):
        draft = _make_valid_draft()
        result = self.engine.validate(draft, _make_context(), 1)
        keys = [i.issue_key for i in result.issues]
        self.assertEqual(len(keys), len(set(keys)), f"Duplicate issue_keys found: {[k for k in keys if keys.count(k) > 1]}")

    def test_work001_sdes001_not_both_errors(self):
        draft = _make_valid_draft(work_items=[], waiting_hours=0, service_description="")
        result = self.engine.validate(draft, _make_context(), 1)
        rule_ids = [i.rule_id for i in result.issues]
        self.assertIn("WORK-001", rule_ids)
        self.assertNotIn("SDES-001", rule_ids)


# ─── ValidationContextBuilder Tests ─────────────────────────────────────────

class TestContextBuilder(unittest.TestCase):
    """Test that ContextBuilder queries DB for authoritative data."""

    def test_context_builder_reads_service_order(self):
        from ai_daily_report.validation_engine import ValidationContextBuilder
        mock_db = MagicMock()
        so_row = {"id": 100, "order_number": "SO-001", "client_name": "Client",
                  "site_address": "123 Test St", "status": "open"}
        mock_db.execute.return_value.fetchone.return_value = so_row
        mock_db.execute.return_value.fetchall.return_value = []

        builder = ValidationContextBuilder(mock_db)
        ctx = builder.build({"service_order_id": 100, "workers": []})
        self.assertTrue(ctx.service_order_exists)
        self.assertEqual(ctx.site_address, "123 Test St")

    def test_context_builder_missing_order_returns_exists_false(self):
        from ai_daily_report.validation_engine import ValidationContextBuilder
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = None
        mock_db.execute.return_value.fetchall.return_value = []
        builder = ValidationContextBuilder(mock_db)
        ctx = builder.build({"service_order_id": 999, "workers": []})
        self.assertFalse(ctx.service_order_exists)

    def test_engine_itself_has_no_db(self):
        from ai_daily_report.validation_engine import ValidationEngine
        engine = ValidationEngine()
        self.assertFalse(hasattr(engine, 'db'))
        self.assertFalse(hasattr(engine, 'connection'))


# ─── Integration Tests with Flask App ───────────────────────────────────────

class Phase7TestBase(unittest.TestCase):
    """Base class for Phase 7 integration tests with app context."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="phase7_test_")
        os.environ["DATA_DIR"] = cls.temp_dir

        import app as app_module
        cls.app_module = app_module
        cls.app = app_module.app
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            cls._setup_test_data()

    @classmethod
    def _setup_test_data(cls):
        db = cls.app_module.db()
        # Users
        for uid, email, name, role in [
            (200, "admin-p7@test.com", "Admin P7", "admin"),
            (201, "ethan-p7@test.com", "Ethan P7", "employee"),
            (202, "zhangsan-p7@test.com", "张三 P7", "employee"),
            (203, "finance-p7@test.com", "Finance P7", "finance"),
            (204, "outsider-p7@test.com", "Outsider P7", "employee"),
        ]:
            db.execute(
                "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
                (uid, email, name, "x", role, "2026-01-01T00:00:00Z"),
            )
        # Service order
        db.execute(
            "insert or ignore into service_orders (id, order_number, client_name, site_address, client_order_number, status, created_by, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (200, "SO-PHASE7-001", "Client P7", "123 Site Ave, Spring, TX 77386", "CLIENT-P7-001", "open", 200, "2026-09-14T00:00:00Z"),
        )
        # Valid draft
        draft_data = _make_valid_draft()
        draft_data["service_order_id"] = 200
        draft_data["workers"][0]["user_id"] = 201
        draft_data["workers"][0]["name"] = "Ethan P7"
        draft_data["workers"][0]["destination"] = "123 Site Ave, Spring, TX 77386"
        db.execute(
            "insert or replace into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (200, 200, "2026-09-14", "draft", 1, 201, json.dumps(draft_data), "2026-09-14T00:00:00Z", "2026-09-14T00:00:00Z"),
        )
        # Draft with WARNING (high mileage)
        warn_data = _make_valid_draft()
        warn_data["service_order_id"] = 200
        warn_data["workers"][0]["user_id"] = 201
        warn_data["workers"][0]["name"] = "Ethan P7"
        warn_data["workers"][0]["destination"] = "123 Site Ave, Spring, TX 77386"
        warn_data["workers"][0]["reported_miles"] = 600.0
        warn_data["workers"][0]["one_way_miles"] = 300.0
        db.execute(
            "insert or replace into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (201, 200, "2026-09-14", "draft", 1, 201, json.dumps(warn_data), "2026-09-14T00:00:00Z", "2026-09-14T00:00:00Z"),
        )
        # Confirmed draft
        confirmed_data = dict(warn_data)
        confirmed_data["confirmed_by"] = 200
        confirmed_data["confirmed_at"] = "2026-09-14T12:00:00Z"
        db.execute(
            "insert or replace into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (202, 200, "2026-09-14", "confirmed", 1, 201, json.dumps(confirmed_data), "2026-09-14T00:00:00Z", "2026-09-14T00:00:00Z"),
        )
        # Cancelled draft
        db.execute(
            "insert or replace into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (203, 200, "2026-09-14", "cancelled", 1, 201, json.dumps(warn_data), "2026-09-14T00:00:00Z", "2026-09-14T00:00:00Z"),
        )
        db.commit()

    def _login(self, user_id):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id

    def _get_csrf(self):
        resp = self.client.get("/api/ai/daily-report/csrf")
        return resp.get_json()["csrf_token"]


class TestValidationAPI(Phase7TestBase):
    """Test GET /api/ai/daily-report/draft/<id>/validation"""

    def test_get_validation_valid_draft(self):
        self._login(200)  # admin
        resp = self.client.get("/api/ai/daily-report/draft/200/validation")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("validation", data)
        v = data["validation"]
        self.assertEqual(v["error_count"], 0)
        self.assertTrue(v["is_valid"])
        self.assertTrue(v["can_proceed"])

    def test_get_validation_warning_draft(self):
        self._login(200)
        resp = self.client.get("/api/ai/daily-report/draft/201/validation")
        self.assertEqual(resp.status_code, 200)
        v = resp.get_json()["validation"]
        self.assertGreater(v["warning_count"], 0)
        self.assertTrue(v["is_valid"])  # WARNING only → valid

    def test_get_validation_nonexistent_draft_404(self):
        self._login(200)
        resp = self.client.get("/api/ai/daily-report/draft/99999/validation")
        self.assertEqual(resp.status_code, 404)

    def test_get_validation_idor_employee_denied(self):
        """Employee not in draft cannot view validation."""
        self._login(204)  # outsider
        resp = self.client.get("/api/ai/daily-report/draft/200/validation")
        self.assertEqual(resp.status_code, 403)

    def test_get_validation_finance_allowed(self):
        """Finance can view (read-only)."""
        self._login(203)  # finance
        resp = self.client.get("/api/ai/daily-report/draft/200/validation")
        self.assertEqual(resp.status_code, 200)

    def test_get_validation_returns_fingerprint(self):
        self._login(200)
        resp = self.client.get("/api/ai/daily-report/draft/200/validation")
        v = resp.get_json()["validation"]
        self.assertIn("validation_fingerprint", v)
        self.assertIn("draft_data_hash", v)
        self.assertIn("context_hash", v)
        self.assertTrue(len(v["validation_fingerprint"]) > 0)


class TestAcknowledgeAPI(Phase7TestBase):
    """Test POST /api/ai/daily-report/draft/<id>/acknowledge"""

    def test_acknowledge_warning_success(self):
        self._login(200)
        csrf = self._get_csrf()
        # First get validation to find a WARNING issue_key
        resp = self.client.get("/api/ai/daily-report/draft/201/validation")
        v = resp.get_json()["validation"]
        warning_issues = [i for i in v["issues"] if i["severity"] == "warning"]
        self.assertGreater(len(warning_issues), 0, "Test setup: draft 201 should have warnings")
        issue_key = warning_issues[0]["issue_key"]

        resp = self.client.post(
            "/api/ai/daily-report/draft/201/acknowledge",
            json={"issue_key": issue_key, "draft_version": 1},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["issue_key"], issue_key)
        # Verify the issue is now acknowledged
        updated_issues = {i["issue_key"]: i for i in data["validation"]["issues"]}
        self.assertTrue(updated_issues[issue_key]["acknowledged"])

    def test_acknowledge_without_csrf_rejected(self):
        self._login(200)
        resp = self.client.post(
            "/api/ai/daily-report/draft/201/acknowledge",
            json={"issue_key": "anything", "draft_version": 1},
        )
        self.assertEqual(resp.status_code, 403)

    def test_acknowledge_error_rejected(self):
        self._login(200)
        csrf = self._get_csrf()
        # Create a draft with an ERROR by removing arrival_time
        with self.app.app_context():
            db = self.app_module.db()
            err_data = _make_valid_draft()
            err_data["service_order_id"] = 200
            err_data["workers"][0]["user_id"] = 201
            err_data["arrival_time"] = None
            db.execute(
                "insert or replace into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (210, 200, "2026-09-14", "draft", 1, 201, json.dumps(err_data), "2026-09-14T00:00:00Z", "2026-09-14T00:00:00Z"),
            )
            db.commit()

        resp = self.client.get("/api/ai/daily-report/draft/210/validation")
        v = resp.get_json()["validation"]
        error_issues = [i for i in v["issues"] if i["severity"] == "error"]
        self.assertGreater(len(error_issues), 0)
        issue_key = error_issues[0]["issue_key"]

        resp = self.client.post(
            "/api/ai/daily-report/draft/210/acknowledge",
            json={"issue_key": issue_key, "draft_version": 1},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("只能确认 WARNING", resp.get_json()["error"])

    def test_acknowledge_nonexistent_issue_rejected(self):
        self._login(200)
        csrf = self._get_csrf()
        resp = self.client.post(
            "/api/ai/daily-report/draft/201/acknowledge",
            json={"issue_key": "NONEXISTENT-999:draft:fake", "draft_version": 1},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 404)

    def test_acknowledge_stale_version_409(self):
        self._login(200)
        csrf = self._get_csrf()
        resp = self.client.get("/api/ai/daily-report/draft/201/validation")
        v = resp.get_json()["validation"]
        warning_issues = [i for i in v["issues"] if i["severity"] == "warning"]
        issue_key = warning_issues[0]["issue_key"]

        resp = self.client.post(
            "/api/ai/daily-report/draft/201/acknowledge",
            json={"issue_key": issue_key, "draft_version": 999},  # stale
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 409)

    def test_acknowledge_confirmed_draft_rejected(self):
        self._login(200)
        csrf = self._get_csrf()
        resp = self.client.get("/api/ai/daily-report/draft/202/validation")
        v = resp.get_json()["validation"]
        warning_issues = [i for i in v["issues"] if i["severity"] == "warning"]
        if warning_issues:
            issue_key = warning_issues[0]["issue_key"]
            resp = self.client.post(
                "/api/ai/daily-report/draft/202/acknowledge",
                json={"issue_key": issue_key, "draft_version": 1},
                headers={"X-CSRF-Token": csrf},
            )
            self.assertEqual(resp.status_code, 403)

    def test_acknowledge_cancelled_draft_rejected(self):
        self._login(200)
        csrf = self._get_csrf()
        resp = self.client.get("/api/ai/daily-report/draft/203/validation")
        v = resp.get_json()["validation"]
        warning_issues = [i for i in v["issues"] if i["severity"] == "warning"]
        if warning_issues:
            issue_key = warning_issues[0]["issue_key"]
            resp = self.client.post(
                "/api/ai/daily-report/draft/203/acknowledge",
                json={"issue_key": issue_key, "draft_version": 1},
                headers={"X-CSRF-Token": csrf},
            )
            self.assertEqual(resp.status_code, 403)

    def test_acknowledge_finance_denied(self):
        self._login(203)  # finance
        csrf = self._get_csrf()
        resp = self.client.post(
            "/api/ai/daily-report/draft/201/acknowledge",
            json={"issue_key": "anything", "draft_version": 1},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 403)

    def test_acknowledge_idor_denied(self):
        self._login(204)  # outsider
        csrf = self._get_csrf()
        resp = self.client.post(
            "/api/ai/daily-report/draft/201/acknowledge",
            json={"issue_key": "anything", "draft_version": 1},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 403)

    def test_acknowledge_missing_issue_key_400(self):
        self._login(200)
        csrf = self._get_csrf()
        resp = self.client.post(
            "/api/ai/daily-report/draft/201/acknowledge",
            json={"draft_version": 1},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 400)


class TestPreviewIntegration(Phase7TestBase):
    """Test that Preview includes validation_result and remains read-only."""

    def test_preview_includes_validation_result(self):
        self._login(200)
        resp = self.client.get("/api/ai/daily-report/draft/200/preview")
        self.assertEqual(resp.status_code, 200)
        preview = resp.get_json()["preview"]
        self.assertIn("validation_result", preview)
        self.assertIn("is_valid", preview["validation_result"])
        self.assertIn("issues", preview["validation_result"])

    def test_preview_still_has_verification_checklist(self):
        """Old verification_checklist field preserved for backward compatibility."""
        self._login(200)
        resp = self.client.get("/api/ai/daily-report/draft/200/preview")
        preview = resp.get_json()["preview"]
        self.assertIn("verification_checklist", preview)

    def test_preview_validation_read_only_no_db_writes(self):
        """Preview GET should not increment draft_version or modify data."""
        self._login(200)
        resp1 = self.client.get("/api/ai/daily-report/draft/200/preview")
        v1 = resp1.get_json()["preview"]["draft_version"]
        resp2 = self.client.get("/api/ai/daily-report/draft/200/preview")
        v2 = resp2.get_json()["preview"]["draft_version"]
        self.assertEqual(v1, v2, "Preview should not modify draft_version")

    def test_preview_validation_no_external_calls(self):
        """Preview validation should not call Google Routes / Vision / DeepSeek."""
        self._login(200)
        with patch("ai_daily_report.google_routes.GoogleRoutesService") as mock_routes:
            resp = self.client.get("/api/ai/daily-report/draft/200/preview")
            self.assertEqual(resp.status_code, 200)
            mock_routes.assert_not_called()


class TestSeveritySemantics(unittest.TestCase):
    """Test ERROR/WARNING/INFO blocking semantics."""

    def setUp(self):
        from ai_daily_report.validation_engine import ValidationEngine
        self.engine = ValidationEngine()

    def test_all_errors_blocking(self):
        draft = _make_valid_draft(arrival_time=None)
        result = self.engine.validate(draft, _make_context(), 1)
        for err in result.errors:
            self.assertTrue(err.blocking)

    def test_all_warnings_non_blocking(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["route_status"] = "not_calculated"
        workers[0]["reported_miles"] = None
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        for warn in result.warnings:
            self.assertFalse(warn.blocking)

    def test_warning_only_is_valid(self):
        workers = [dict(_make_valid_draft()["workers"][0])]
        workers[0]["reported_miles"] = 600.0
        draft = _make_valid_draft(workers=workers)
        result = self.engine.validate(draft, _make_context(), 1)
        self.assertGreater(result.warning_count, 0)
        self.assertEqual(result.error_count, 0)
        self.assertTrue(result.is_valid)
        self.assertTrue(result.can_proceed)

    def test_error_makes_invalid(self):
        draft = _make_valid_draft(arrival_time=None)
        result = self.engine.validate(draft, _make_context(), 1)
        self.assertGreater(result.error_count, 0)
        self.assertFalse(result.is_valid)
        self.assertFalse(result.can_proceed)

    def test_info_only_display(self):
        draft = _make_valid_draft(service_description="Hi")
        result = self.engine.validate(draft, _make_context(), 1)
        for info in result.infos:
            self.assertFalse(info.blocking)
            self.assertFalse(info.acknowledgement_required)


class TestAddressNormalization(unittest.TestCase):
    def test_normalize_collapses_whitespace(self):
        from ai_daily_report.validation_engine import normalize_address
        self.assertEqual(normalize_address("  123   Main  St  "), "123 main st")

    def test_normalize_casefold(self):
        from ai_daily_report.validation_engine import normalize_address
        self.assertEqual(normalize_address("SPRING, TX"), "spring, tx")

    def test_addresses_equal_different_format(self):
        from ai_daily_report.validation_engine import addresses_equal
        self.assertTrue(addresses_equal("Spring, TX 77386", "spring,  tx   77386"))

    def test_addresses_not_equal(self):
        from ai_daily_report.validation_engine import addresses_equal
        self.assertFalse(addresses_equal("123 Main St", "456 Oak Ave"))

    def test_normalize_empty(self):
        from ai_daily_report.validation_engine import normalize_address
        self.assertEqual(normalize_address(None), "")
        self.assertEqual(normalize_address(""), "")


class TestNoSideEffects(unittest.TestCase):
    """Proof that ValidationEngine has no side effects."""

    def test_engine_does_not_import_external_clients(self):
        import ai_daily_report.validation_engine as ve
        # 源码含中文注释，必须显式 utf-8（Windows 默认 GBK 会 UnicodeDecodeError）
        with open(ve.__file__, encoding="utf-8") as f:
            source = f.read()
        # Check import lines only (not docstrings/comments)
        import_lines = [line for line in source.split("\n")
                        if line.strip().startswith("import ") or line.strip().startswith("from ")]
        import_text = "\n".join(import_lines)
        self.assertNotIn("requests", import_text)
        self.assertNotIn("google_routes", import_text)
        self.assertNotIn("vision_provider", import_text)
        self.assertNotIn("deepseek", import_text.lower())
        self.assertNotIn("os.path.exists", source)
        self.assertNotIn("os.listdir", source)
        self.assertNotIn("glob.", source)

    def test_engine_no_db_in_validate_signature(self):
        from ai_daily_report.validation_engine import ValidationEngine
        import inspect
        sig = inspect.signature(ValidationEngine.validate)
        self.assertNotIn("db", sig.parameters)
        self.assertNotIn("connection", sig.parameters)


if __name__ == "__main__":
    unittest.main()
