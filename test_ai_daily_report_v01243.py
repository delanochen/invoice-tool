"""v0.1.243 regression tests.

1. ISO photo-timeline timestamps ('2026-09-17T08:50:00') were saved verbatim
   into service_reports.arrival_time/departure_time. The report form splits on
   ':' expecting 'HH:MM', so the hour dropdown never matched (screenshot showed
   blank hour + minute '50'). formal_save must normalize to 'HH:MM'.
2. Worker travel_hours were never derived from the Google route duration, so
   the report's 交通时长 / 合计用时 stayed 0. MileageService must auto-fill
   travel_hours from route_duration_seconds (with 1.15 uplift, round UP to
   15 min, doubled for same-day return) unless manually entered.
3. total_service_hours = (departure - arrival) x worker count must work with
   normalized times; 交通时长 = sum of per-worker travel hours.
"""
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from test_ai_daily_report_phase9 import Phase9TestBase  # noqa: E402

from ai_daily_report.formal_save import (  # noqa: E402
    normalize_report_time,
    rounded_report_service_hours,
)
from ai_daily_report.mileage_service import MileageService  # noqa: E402
from ai_daily_report.schemas import WorkerTravel  # noqa: E402


class TestNormalizeReportTime(unittest.TestCase):
    def test_iso_datetime_to_hhmm(self):
        self.assertEqual(normalize_report_time("2026-09-17T08:50:00"), "08:50")
        self.assertEqual(normalize_report_time("2026-09-17T17:04:00"), "17:04")

    def test_space_datetime_to_hhmm(self):
        self.assertEqual(normalize_report_time("2026-09-17 17:04:00"), "17:04")

    def test_hhmm_passthrough(self):
        self.assertEqual(normalize_report_time("08:50"), "08:50")
        self.assertEqual(normalize_report_time("17:04"), "17:04")

    def test_hhmmss(self):
        self.assertEqual(normalize_report_time("08:50:00"), "08:50")

    def test_invalid(self):
        self.assertIsNone(normalize_report_time(""))
        self.assertIsNone(normalize_report_time(None))
        self.assertIsNone(normalize_report_time("8点50"))
        self.assertIsNone(normalize_report_time("25:00"))
        self.assertIsNone(normalize_report_time("08:75"))

    def test_rounded_service_hours_with_iso(self):
        # 08:50 -> 17:04 = 494 min -> rounds to 495 -> 8.25 h
        self.assertEqual(rounded_report_service_hours("2026-09-17T08:50:00", "2026-09-17T17:04:00"), 8.25)


class TestTravelHoursFromRoute(unittest.TestCase):
    def _svc(self):
        return MileageService(routes_service=None)

    def test_same_day_return_doubles_duration(self):
        # 1h one-way, same-day return: 60 * 2 * 1.15 = 138 -> ceil 150 min = 2.5 h
        self.assertEqual(self._svc().duration_to_travel_hours(3600, False), 2.5)

    def test_round_trip_doubles_duration(self):
        self.assertEqual(self._svc().duration_to_travel_hours(3600, "round_trip"), 2.5)

    def test_one_way_single_trip(self):
        self.assertEqual(self._svc().duration_to_travel_hours(3600, "one_way"), 1.25)

    def test_short_trip_rounds_up_to_quarter(self):
        # 10 min * 2 * 1.15 = 23 -> ceil to 30 min = 0.5 h
        self.assertEqual(self._svc().duration_to_travel_hours(600, False), 0.5)

    def test_zero_or_none(self):
        self.assertEqual(self._svc().duration_to_travel_hours(None, False), 0.0)
        self.assertEqual(self._svc().duration_to_travel_hours(0, True), 0.0)

    def test_ensure_fills_auto_route(self):
        worker = WorkerTravel(
            user_id=1, name="A", transportation="self_drive",
            route_status="success", route_duration_seconds=3600,
            trip_type="round_trip",
        )
        self._svc().ensure_travel_hours(worker)
        self.assertEqual(worker.travel_hours, 2.5)
        self.assertEqual(worker.travel_hours_source, "auto_route")

    def test_ensure_never_overwrites_user_input(self):
        worker = WorkerTravel(
            user_id=1, name="A", transportation="self_drive",
            route_status="success", route_duration_seconds=3600,
            trip_type="round_trip",
            travel_hours=9.99, travel_hours_source="user_input",
        )
        self._svc().ensure_travel_hours(worker)
        self.assertEqual(worker.travel_hours, 9.99)
        self.assertEqual(worker.travel_hours_source, "user_input")

    def test_cached_success_path_fills_travel_hours(self):
        svc = self._svc()
        worker = WorkerTravel(
            user_id=1, name="A", transportation="self_drive",
            origin="a", origin_confirmed=True, destination="b",
            trip_type="round_trip", route_status="success",
            route_distance_meters=1000.0, route_duration_seconds=3600,
        )
        svc.calculate_for_worker(worker)
        self.assertEqual(worker.travel_hours, 2.5)


class TestFormalSaveTimesAndHours(Phase9TestBase):
    """Confirm must store HH:MM and derive totals from per-worker hours."""

    def test_confirm_normalizes_iso_times_and_sums_travel_hours(self):
        draft_id, data = self._build_confirmed_draft(482)
        data["arrival_time"] = "2026-09-17T08:50:00"
        data["departure_time"] = "2026-09-17T17:04:00"
        data["workers"][0]["travel_hours"] = 2.5
        data["workers"][0]["travel_hours_source"] = "auto_route"
        self._insert_draft(draft_id, data, status="draft", draft_version=1)

        self._login(400)
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/confirm",
            json={"draft_version": 1},
        )
        self.assertEqual(resp.status_code, 200, resp.get_json())

        db = self.app_module.db()
        row = db.execute(
            "select * from service_reports where ai_draft_id = ?", (draft_id,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["arrival_time"], "08:50")
        self.assertEqual(row["departure_time"], "17:04")
        # (17:04 - 08:50) rounds to 8.25 h x 1 worker
        self.assertEqual(float(row["total_service_hours"]), 8.25)
        # 交通时长 = 每个人员交通时长之和
        self.assertEqual(float(row["travel_hours"]), 2.5)
        self.assertEqual(str(row["total_time"]), "2.5")


class TestLegacyFormRendering(Phase9TestBase):
    """report_form_defaults must normalize legacy ISO times for the form."""

    def test_form_defaults_normalize_iso(self):
        data = self.app_module.report_form_defaults(
            report={"arrival_time": "2026-09-17T08:50:00",
                    "departure_time": "2026-09-17T17:04:00",
                    "report_date": "2026-09-17"}
        )
        self.assertEqual(data["arrival_time"], "08:50")
        self.assertEqual(data["departure_time"], "17:04")


if __name__ == "__main__":
    unittest.main()
