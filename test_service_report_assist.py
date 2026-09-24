"""工作日报「辅助填写」与里程佐证：口径测试 + 表单保存集成测试。

重点验证四件事：
1. 照片归类与「施工照片取 10 张」的确定性（按拍摄时间排序）；
2. 进场取最早、离场取最晚，时间读不出来时留空并标记（绝不猜）；
3. 出发地取值链、往返/单程倍数、随行不调用 Google（口径与 AI 智能日报一致）；
4. 员工清单新增的出发地、行程类型能正确落库并回填表单。
"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from trip_policy import DEFAULT_TRIP_TYPE, ONE_WAY, ROUND_TRIP

from service_report_assist import (
    SITE_PHOTO_LIMIT,
    ServiceReportAssistService,
    ServiceReportEvidenceService,
)

REPO_DIR = Path(__file__).resolve().parent

# 一段有代表性的路线：单程 10 英里，60 分钟
ROUTE_DISTANCE_METERS = 16093.44
ROUTE_DURATION_SECONDS = 3600


def make_photo(relative_path, classification, capture_time=None):
    from ai_daily_report.schemas import PhotoRef

    return PhotoRef(
        photo_id=relative_path,
        photo_hash=relative_path,
        relative_path=relative_path,
        classification=classification,
        manual_classification=classification if classification != "unknown" else None,
        capture_time=capture_time,
        capture_time_source="exif_original" if capture_time else None,
    )


class FakePhotoDiscovery:
    def __init__(self, photos):
        self.photos = photos

    def discover_photos(self, order_number, report_date, photo_type_lookup=None):
        return list(self.photos), "discovered", None


class FakePhotoMetadata:
    """测试替身：时间已经写在 PhotoRef 上，这里原样返回。"""

    def enrich_all_photos(self, photos, report_date):
        return photos


class FakeRoutes:
    def __init__(self, success=True):
        self.calls = []
        self.success = success

    def get_driving_route(self, origin, destination):
        self.calls.append((origin, destination))
        return SimpleNamespace(
            success=self.success,
            distance_meters=ROUTE_DISTANCE_METERS,
            duration_seconds=ROUTE_DURATION_SECONDS,
            encoded_polyline="poly123",
            origin_normalized=origin,
            destination_normalized=destination,
            status="success" if self.success else "verification_required",
            error=None if self.success else "routes_error",
            provider="google_routes",
            query_time=None,
        )


def build_assist(photos=(), routes=None, employee_addresses=None, **kwargs):
    return ServiceReportAssistService(
        photo_discovery=FakePhotoDiscovery(list(photos)),
        photo_metadata=FakePhotoMetadata(),
        routes_service=routes if routes is not None else FakeRoutes(),
        employee_address_lookup=(
            (lambda uid: (employee_addresses or {}).get(uid)) if employee_addresses is not None else None
        ),
        **kwargs,
    )


class AssistPhotoClassificationTest(unittest.TestCase):
    def test_photo_types_map_to_report_categories(self):
        assist = build_assist()
        buckets = assist.classify_photos(
            [
                make_photo("a1.jpg", "arrival"),
                make_photo("d1.jpg", "departure"),
                make_photo("s1.jpg", "safety"),
                make_photo("e1.jpg", "equipment"),
                make_photo("g1.jpg", "general"),
                make_photo("u1.jpg", "unknown"),
            ]
        )
        self.assertEqual([photo.relative_path for photo in buckets["arrival"]], ["a1.jpg"])
        self.assertEqual([photo.relative_path for photo in buckets["departure"]], ["d1.jpg"])
        self.assertEqual([photo.relative_path for photo in buckets["self_check"]], ["s1.jpg"])
        self.assertEqual(
            sorted(photo.relative_path for photo in buckets["site"]),
            ["e1.jpg", "g1.jpg", "u1.jpg"],
        )

    def test_site_photos_limited_to_ten_by_capture_time(self):
        photos = [
            make_photo(f"e{i}.jpg", "equipment", f"2026-09-14T{8 + i // 60:02d}:{i % 60:02d}:00")
            for i in range(14)
        ]
        photos.append(make_photo("no-time.jpg", "equipment"))
        assist = build_assist(photos=photos)
        plan = assist.build_photo_plan("SO-1", "2026-09-14")
        self.assertEqual(plan["site_total"], 15)
        self.assertEqual(plan["site_selected"], SITE_PHOTO_LIMIT)
        self.assertTrue(plan["site_truncated"])
        self.assertEqual(len(plan["photos"]["site"]), SITE_PHOTO_LIMIT)
        self.assertTrue(all(item["capture_time"] for item in plan["photos"]["site"]))

    def test_arrival_earliest_and_departure_latest(self):
        photos = [
            make_photo("a-late.jpg", "arrival", "2026-09-14T09:05:00"),
            make_photo("a-early.jpg", "arrival", "2026-09-14T08:12:00"),
            make_photo("d-early.jpg", "departure", "2026-09-14T15:00:00"),
            make_photo("d-late.jpg", "departure", "2026-09-14T17:30:00"),
        ]
        plan = build_assist(photos=photos).build_photo_plan("SO-1", "2026-09-14")
        self.assertEqual(plan["arrival_time"], "08:12")
        self.assertEqual(plan["departure_time"], "17:30")

    def test_unknown_times_are_flagged_not_guessed(self):
        photos = [make_photo("a1.jpg", "arrival"), make_photo("d1.jpg", "departure")]
        plan = build_assist(photos=photos).build_photo_plan("SO-1", "2026-09-14")
        self.assertIsNone(plan["arrival_time"], "取不到水印时间不能瞎填")
        self.assertIsNone(plan["departure_time"])
        self.assertIn("arrival_time_unknown", plan["warnings"])
        self.assertIn("departure_time_unknown", plan["warnings"])

    def test_photo_urls_are_built_when_builder_given(self):
        assist = build_assist(
            photos=[make_photo("e1.jpg", "equipment", "2026-09-14T10:00:00")],
            photo_url_builder=lambda path: {"thumbnail": f"/t/{path}", "preview": f"/p/{path}"},
        )
        plan = assist.build_photo_plan("SO-1", "2026-09-14")
        self.assertEqual(plan["photos"]["site"][0]["thumbnail"], "/t/e1.jpg")
        self.assertEqual(plan["photos"]["site"][0]["preview"], "/p/e1.jpg")


class AssistWorkerTravelTest(unittest.TestCase):
    def test_origin_chain_is_form_then_employee_then_report(self):
        assist = build_assist(employee_addresses={7: "1 Main St"})
        self.assertEqual(
            assist.resolve_origin({"user_id": 7, "current_origin": "Manual Ave"}, "Fallback Rd"),
            ("Manual Ave", "user_input"),
        )
        self.assertEqual(
            assist.resolve_origin({"user_id": 7}, "Fallback Rd"),
            ("1 Main St", "employee_default"),
        )
        self.assertEqual(
            assist.resolve_origin({"user_id": 8}, "Fallback Rd"),
            ("Fallback Rd", "report_fallback"),
        )
        self.assertEqual(assist.resolve_origin({"user_id": 8}), ("", "none"))

    def test_round_trip_doubles_miles_and_hours(self):
        destination = "900 Site Rd"
        worker = {
            "user_id": 7,
            "name": "Ann",
            "travel_mode": "self_drive",
            "current_origin": "1 Main St",
        }
        round_plan = build_assist().plan_worker({**worker, "trip_type": ROUND_TRIP}, destination)
        one_way_plan = build_assist().plan_worker({**worker, "trip_type": ONE_WAY}, destination)
        self.assertEqual(round_plan["trip_type"], ROUND_TRIP)
        self.assertAlmostEqual(round_plan["reported_miles"], 20.0, places=2)
        self.assertAlmostEqual(one_way_plan["reported_miles"], 10.0, places=2)
        self.assertAlmostEqual(round_plan["travel_hours"], one_way_plan["travel_hours"] * 2, places=2)

    def test_missing_trip_type_defaults_to_round_trip(self):
        plan = build_assist().plan_worker(
            {"user_id": 7, "travel_mode": "self_drive", "trip_type": "", "current_origin": "1 Main St"},
            "900 Site Rd",
        )
        self.assertEqual(plan["trip_type"], DEFAULT_TRIP_TYPE)
        self.assertEqual(plan["trip_type"], ROUND_TRIP)

    def test_following_never_calls_google(self):
        routes = FakeRoutes()
        plan = build_assist(routes=routes).plan_worker(
            {"user_id": 7, "travel_mode": "following", "current_origin": "1 Main St"},
            "900 Site Rd",
        )
        self.assertEqual(routes.calls, [])
        self.assertEqual(plan["route_status"], "not_applicable")
        self.assertEqual(plan["reason"], "following_or_flight")

    def test_missing_fields_block_route(self):
        assist = build_assist(employee_addresses={})
        no_origin = assist.plan_worker({"user_id": 7, "travel_mode": "self_drive"}, "900 Site Rd")
        self.assertEqual(no_origin["reason"], "missing_origin")
        no_destination = assist.plan_worker(
            {"user_id": 7, "travel_mode": "self_drive", "current_origin": "1 Main St"}, ""
        )
        self.assertEqual(no_destination["reason"], "missing_destination")

    def test_route_cache_prevents_duplicate_google_calls(self):
        routes = FakeRoutes()
        assist = build_assist(routes=routes)
        assist.plan_worker(
            {"user_id": 7, "travel_mode": "self_drive", "current_origin": "1 Main St"}, "900 Site Rd"
        )
        assist.plan_worker(
            {"user_id": 8, "travel_mode": "self_drive", "current_origin": "1 Main St"}, "900 Site Rd"
        )
        self.assertEqual(len(routes.calls), 1, "同起终点不应重复调用付费接口")


class FakeEvidenceGenerator:
    def __init__(self):
        self.calls = []

    def compute_route_fingerprint(self, worker):
        from service_report_assist import route_fingerprint_parts

        return route_fingerprint_parts(
            worker.origin,
            worker.destination,
            worker.trip_type,
            worker.route_distance_meters,
            worker.route_polyline,
        )

    def generate_evidence(
        self, worker, draft_id, service_order_id, report_date, generated_by, draft_evidence_records=None
    ):
        self.calls.append(worker)
        return SimpleNamespace(evidence_status="ready", file_relative_path="evidence.png", error=None)


class MileageEvidenceFlowTest(unittest.TestCase):
    def setUp(self):
        self.saved = []

        def saver(report_id, source_path, original_filename, replace_attachment_id=None):
            self.saved.append((original_filename, replace_attachment_id))
            return 99

        self.evidence = FakeEvidenceGenerator()
        self.service = ServiceReportEvidenceService(
            routes_service=FakeRoutes(),
            evidence_service=self.evidence,
            attachment_saver=saver,
            evidence_root="/tmp/evidence",
            uploaded_by=1,
            now_fn=lambda: "2026-09-24T00:00:00",
        )

    def _worker(self, **overrides):
        worker = {
            "user_id": 7,
            "name": "Ann",
            "travel_mode": "self_drive",
            "trip_type": ROUND_TRIP,
            "origin": "1 Main St",
        }
        worker.update(overrides)
        return worker

    def _run(self, workers, force=False, existing=None):
        return self.service.generate(
            report_id=5,
            order_id=3,
            report_date="2026-09-24",
            workers=workers,
            destination="900 Site Rd",
            existing_by_user=existing or {},
            force=force,
        )

    def test_following_skipped(self):
        summary = self._run([self._worker(travel_mode="following")])
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["results"][0]["reason"], "following_or_flight")
        self.assertEqual(self.evidence.calls, [])

    def test_missing_origin_skipped(self):
        summary = self._run([self._worker(origin="")])
        self.assertEqual(summary["results"][0]["reason"], "missing_origin")

    def test_unchanged_fingerprint_is_reused(self):
        first = self._run([self._worker()])
        self.assertEqual(first["generated"], 1)
        existing = {7: {"route_fingerprint": first["results"][0]["route_fingerprint"], "attachment_id": 99}}
        second = self._run([self._worker()], existing=existing)
        self.assertEqual(second["reused"], 1)
        self.assertEqual(len(self.saved), 1)

    def test_changed_origin_regenerates(self):
        first = self._run([self._worker()])
        existing = {7: {"route_fingerprint": first["results"][0]["route_fingerprint"], "attachment_id": 99}}
        second = self._run([self._worker(origin="99 New Ave")], existing=existing)
        self.assertEqual(second["generated"], 1)
        self.assertEqual(len(self.saved), 2, "地址变了应重新生成并替换附件")

    def test_trip_type_change_regenerates(self):
        first = self._run([self._worker()])
        existing = {7: {"route_fingerprint": first["results"][0]["route_fingerprint"], "attachment_id": 99}}
        second = self._run([self._worker(trip_type=ONE_WAY)], existing=existing)
        self.assertEqual(second["generated"], 1)

    def test_force_regenerates_even_when_unchanged(self):
        first = self._run([self._worker()])
        existing = {7: {"route_fingerprint": first["results"][0]["route_fingerprint"], "attachment_id": 99}}
        second = self._run([self._worker()], force=True, existing=existing)
        self.assertEqual(second["generated"], 1)


class ServiceReportWorkerFieldsTest(unittest.TestCase):
    """员工清单的出发地 / 行程类型能正确落库并回填表单。"""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_assist_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            connection = self.module.db()
            for table in (
                "service_report_workers",
                "service_reports",
                "service_orders",
                "clients",
                "users",
            ):
                connection.execute(f"delete from {table}")
            self.user_id = connection.execute(
                "insert into users (name, email, password_hash, role, is_active, created_at) "
                "values ('Admin', 'assist-admin@example.com', 'unused', 'admin', 1, '2026-09-24T00:00:00')"
            ).lastrowid
            self.employee_id = connection.execute(
                "insert into users (name, email, password_hash, role, is_active, country_code, created_at) "
                "values ('Technician', 'assist-tech@example.com', 'unused', 'employee', 1, 'US', '2026-09-24T00:00:00')"
            ).lastrowid
            client_id = connection.execute(
                "insert into clients (client_number, name, short_name, created_at) "
                "values ('99997', 'Assist Client', 'A', '2026-09-24T00:00:00')"
            ).lastrowid
            self.order_id = connection.execute(
                "insert into service_orders (order_number, client_id, client_name, site_address, "
                "client_order_number, status, start_date, created_by, created_at) "
                "values ('SO-ASSIST', ?, 'Assist Client', 'Test Site', 'ORD-A', 'open', '2026-09-24', ?, '2026-09-24T00:00:00')",
                (client_id, self.user_id),
            ).lastrowid
            connection.commit()
        self.http = self.module.app.test_client()
        with self.http.session_transaction() as session:
            session["user_id"] = self.user_id

    def _submit(self, **overrides):
        data = {
            "save_token": "assist-token",
            "report_date": "2026-09-24",
            "actual_work_date": "2026-09-24",
            "arrival_time_hour": "08",
            "arrival_time_minute": "00",
            "departure_time_hour": "16",
            "departure_time_minute": "00",
            "mileage_billing_method": "per_person",
            "site_address": "Test Site",
            "worker_user_id": [str(self.employee_id)],
            "worker_origin": ["1 Main St"],
            "worker_travel_mode": ["self_drive"],
            "worker_trip_type": [ONE_WAY],
            "worker_driving_miles": ["10"],
            "worker_travel_hours": ["1.5"],
            "worker_public_transport_hours": [""],
            "worker_work_description": ["现场协助"],
        }
        data.update(overrides)
        return self.http.post(f"/service-orders/{self.order_id}/reports/new", data=data)

    def _worker_row(self):
        with self.module.app.app_context():
            return self.module.db().execute("select * from service_report_workers").fetchone()

    def test_origin_and_trip_type_are_saved(self):
        self.assertEqual(self._submit().status_code, 302)
        row = self._worker_row()
        self.assertEqual(row["origin_address"], "1 Main St")
        self.assertEqual(row["trip_type"], ONE_WAY)

    def test_missing_trip_type_falls_back_to_round_trip(self):
        self._submit(worker_trip_type=[""])
        row = self._worker_row()
        self.assertEqual(row["trip_type"], DEFAULT_TRIP_TYPE)

    def test_saved_values_return_to_the_form(self):
        self.assertEqual(self._submit().status_code, 302)
        with self.module.app.app_context():
            report = self.module.db().execute("select id from service_reports").fetchone()
        page = self.http.get(f"/service-reports/{report['id']}/edit")
        html = page.get_data(as_text=True)
        self.assertIn('name="worker_origin"', html)
        self.assertIn('name="worker_trip_type"', html)
        self.assertIn("往返", html)
        self.assertIn("单程", html)
        self.assertIn('value="1 Main St"', html)

    def test_assist_plan_endpoint_rejects_bad_date(self):
        response = self.http.post(
            "/api/service-reports/assist-plan",
            json={"order_id": self.order_id, "report_date": "not-a-date", "workers": []},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["ok"])

    def test_assist_plan_endpoint_reports_round_trip_default(self):
        response = self.http.post(
            "/api/service-reports/assist-plan",
            json={
                "order_id": self.order_id,
                "report_date": "2026-09-24",
                "workers": [
                    {
                        "user_id": self.employee_id,
                        "travel_mode": "following",
                        "trip_type": ONE_WAY,
                        "current_origin": "1 Main St",
                    }
                ],
                "include_routes": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["default_trip_type"], DEFAULT_TRIP_TYPE)
        self.assertEqual(payload["workers"][0]["user_id"], self.employee_id)
        self.assertEqual(payload["workers"][0]["reason"], "following_or_flight")


if __name__ == "__main__":
    unittest.main()
