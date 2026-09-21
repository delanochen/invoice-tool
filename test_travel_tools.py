"""Tests for travel_tools: route map evidence and hotel finder (unittest style).

All Google API calls are mocked. No real network requests. Images are
generated in memory via PIL. Flask wiring is smoke-tested against the real
app module imported into a temp directory (same pattern as the Phase 3B
tests for ai_daily_report).
"""
import io
import json
import importlib.util
import random
import shutil
import sys
import tempfile
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_DIR = Path(__file__).resolve().parent


def make_test_png(width=640, height=400, color=(100, 150, 200)):
    from PIL import Image

    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, payload: bytes, content_type="application/json", status=200):
        self._payload = payload
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self, amount=-1):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class GeoMathTest(unittest.TestCase):
    def test_destination_point_due_north_one_degree(self):
        from travel_tools.geo import destination_point, haversine_meters

        lat2, lng2 = destination_point(30.0, -97.0, 0.0, 111194.9)
        self.assertAlmostEqual(lat2, 31.0, places=2)
        self.assertAlmostEqual(lng2, -97.0, places=3)
        back = haversine_meters(30.0, -97.0, lat2, lng2)
        self.assertAlmostEqual(back, 111194.9, delta=600)

    def test_haversine_symmetry_and_zero(self):
        from travel_tools.geo import haversine_meters

        d1 = haversine_meters(30.0, -97.0, 31.0, -97.0)
        d2 = haversine_meters(31.0, -97.0, 30.0, -97.0)
        self.assertAlmostEqual(d1, d2)
        self.assertEqual(haversine_meters(10.0, 20.0, 10.0, 20.0), 0.0)

    def test_derive_origin_point_round_trip(self):
        from travel_tools.geo import derive_origin_point, haversine_meters, meters_to_miles

        origin = derive_origin_point(29.76, -95.36, 315.0, 350.0)
        distance_m = haversine_meters(29.76, -95.36, origin[0], origin[1])
        self.assertAlmostEqual(meters_to_miles(distance_m), 350.0, delta=2.5)
        # NW of destination -> further north (greater lat), further west (smaller lng)
        self.assertGreater(origin[0], 29.76)
        self.assertLess(origin[1], -95.36)

    def test_bearing_from_input(self):
        from travel_tools.geo import bearing_from_input

        self.assertEqual(bearing_from_input("NW"), 315.0)
        self.assertEqual(bearing_from_input("sw"), 225.0)
        self.assertEqual(bearing_from_input("", 100), 100.0)
        self.assertEqual(bearing_from_input("", 450), 90.0)
        self.assertIsNone(bearing_from_input("", None))
        self.assertIsNone(bearing_from_input("XX"))


class MultiStopRoutesServiceTest(unittest.TestCase):
    def _service(self):
        from travel_tools.routes_service import MultiStopRoutesService

        return MultiStopRoutesService("test-key")

    def test_missing_key_is_verification_required(self):
        from travel_tools.routes_service import MultiStopRoutesService

        svc = MultiStopRoutesService("")
        result = svc.get_route("a", "b")
        self.assertFalse(result.success)
        self.assertEqual(result.status, "verification_required")
        self.assertEqual(result.error, "routes_api_not_configured")

    def test_request_contains_intermediates_with_via(self):
        svc = self._service()
        payload = json.dumps(
            {
                "routes": [
                    {
                        "distanceMeters": 300000,
                        "duration": "10800s",
                        "polyline": {"encodedPolyline": "abc123"},
                        "legs": [
                            {"distanceMeters": 150000, "duration": "5400s"},
                            {"distanceMeters": 150000, "duration": "5400s"},
                        ],
                    }
                ]
            }
        ).encode("utf-8")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["fieldmask"] = req.get_header("X-goog-fieldmask")
            return FakeResponse(payload)

        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            result = svc.get_route("Origin A", "Dest B", ["Stop 1", "", "Stop 2"])

        self.assertTrue(result.success)
        self.assertEqual(captured["body"]["intermediates"], [
            {"address": "Stop 1", "via": True},
            {"address": "Stop 2", "via": True},
        ])
        self.assertIn("routes.legs.distanceMeters", captured["fieldmask"])
        self.assertEqual(result.total_distance_meters, 300000)
        self.assertEqual(result.total_duration_seconds, 10800)
        self.assertEqual(result.encoded_polyline, "abc123")
        self.assertEqual(len(result.legs), 2)

    def test_zero_results_is_verification_required(self):
        svc = self._service()
        with patch.object(urllib.request, "urlopen", return_value=FakeResponse(b"{}")):
            result = svc.get_route("a", "b")
        self.assertFalse(result.success)
        self.assertEqual(result.status, "verification_required")
        self.assertEqual(result.error, "routes_zero_results")

    def test_bad_request_no_retry(self):
        import urllib.error

        svc = self._service()
        error = urllib.error.HTTPError("url", 400, "bad", {}, io.BytesIO(b""))
        with patch.object(urllib.request, "urlopen", side_effect=error):
            result = svc.get_route("a", "b")
        self.assertEqual(result.error, "routes_bad_request")
        self.assertEqual(result.status, "verification_required")


class PolylineCodecTest(unittest.TestCase):
    """Encoded-polyline codec + simplification (URL budget fix, v0.1.264)."""

    def test_roundtrip_google_documented_sample(self):
        from travel_tools.polyline import decode_polyline, encode_polyline

        sample = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
        points = decode_polyline(sample)
        self.assertEqual(
            [(round(lat, 6), round(lng, 6)) for lat, lng in points],
            [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)],
        )
        self.assertEqual(encode_polyline(points), sample)

    def test_roundtrip_synthetic_long_route(self):
        import random as _random
        from travel_tools.polyline import decode_polyline, encode_polyline

        rng = _random.Random(7)
        points = []
        lat, lng = 30.05, -95.4
        for _ in range(4600):
            lat += rng.uniform(-0.02, 0.025)
            lng += rng.uniform(-0.028, 0.02)
            points.append((lat, lng))
        encoded = encode_polyline(points)
        self.assertGreater(len(encoded), 8000)
        self.assertEqual(encode_polyline(decode_polyline(encoded)), encoded)

    def test_douglas_peucker_preserves_endpoints_and_shrinks(self):
        import math as _math
        from travel_tools.polyline import douglas_peucker

        points = [(30.0 + i * 0.001, -97.0 + 0.5 * _math.sin(i / 20.0)) for i in range(2000)]
        simplified = douglas_peucker(points, 250.0)
        self.assertLess(len(simplified), len(points))
        self.assertEqual(simplified[0], points[0])
        self.assertEqual(simplified[-1], points[-1])


class FlexStaticMapsMarkersTest(unittest.TestCase):
    def test_build_markers_order_and_labels(self):
        from travel_tools.static_maps import FlexStaticMapsService

        markers = FlexStaticMapsService.build_markers("Origin", "Dest", ["S1", "S2"])
        self.assertEqual(
            [(m["label"], m["color"]) for m in markers],
            [("A", "green"), ("1", "blue"), ("2", "blue"), ("B", "red")],
        )

    def test_get_map_requires_key(self):
        from travel_tools.static_maps import FlexStaticMapsService

        svc = FlexStaticMapsService("")
        result = svc.get_map([{"label": "A", "color": "green", "position": "x"}], None)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "static_maps_api_not_configured")

    def test_get_map_success_rejects_non_image(self):
        from travel_tools.static_maps import FlexStaticMapsService

        svc = FlexStaticMapsService("test-key")
        with patch.object(
            urllib.request, "urlopen", return_value=FakeResponse(b"nope", content_type="text/html")
        ):
            result = svc.get_map(
                [{"label": "A", "color": "green", "position": "x"}], "poly"
            )
        self.assertFalse(result.success)
        self.assertEqual(result.error, "static_maps_invalid_content_type")


class FlexStaticMapsUrlBudgetTest(unittest.TestCase):
    """Static Maps URLs are capped at 8192 chars; long route polylines must be
    simplified locally instead of failing with HTTP 400 (prod incident)."""

    @staticmethod
    def _long_polyline():
        import math as _math
        from travel_tools.polyline import encode_polyline

        return encode_polyline(
            [(30.0 + i * 0.001, -97.0 + 0.5 * _math.sin(i / 20.0)) for i in range(4000)]
        )

    def test_long_polyline_is_simplified_to_fit_url(self):
        from travel_tools.polyline import decode_polyline
        from travel_tools.static_maps import MAX_URL_LENGTH, FlexStaticMapsService

        svc = FlexStaticMapsService("test-key")
        polyline = self._long_polyline()
        self.assertGreater(len(urllib.parse.quote(polyline)), MAX_URL_LENGTH)
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return FakeResponse(make_test_png(), content_type="image/png")

        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            result = svc.get_map(
                [
                    {"label": "A", "color": "green", "position": "Origin"},
                    {"label": "B", "color": "red", "position": "Dest"},
                ],
                polyline,
            )

        self.assertTrue(result.success)
        url = captured["url"]
        self.assertIn("path=enc:", url)
        self.assertLessEqual(len(url), MAX_URL_LENGTH)

        sent = urllib.parse.unquote(url.split("path=enc:", 1)[1].split("&", 1)[0])
        simplified = decode_polyline(sent)
        original = decode_polyline(polyline)
        self.assertLess(len(simplified), len(original))
        self.assertEqual(simplified[0], original[0])
        self.assertEqual(simplified[-1], original[-1])

    def test_short_polyline_sent_unchanged(self):
        from travel_tools.static_maps import FlexStaticMapsService

        svc = FlexStaticMapsService("test-key")
        polyline = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return FakeResponse(make_test_png(), content_type="image/png")

        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            result = svc.get_map([{"label": "A", "color": "green", "position": "x"}], polyline)

        self.assertTrue(result.success)
        self.assertIn("path=enc:" + urllib.parse.quote(polyline), captured["url"])

    def test_fit_path_returns_none_when_budget_impossible(self):
        from travel_tools.static_maps import FlexStaticMapsService

        svc = FlexStaticMapsService("test-key")
        self.assertIsNone(svc._fit_path(self._long_polyline(), 10))

    def test_get_map_fails_without_http_when_url_cannot_fit(self):
        from travel_tools.static_maps import FlexStaticMapsService

        svc = FlexStaticMapsService("test-key")
        with patch("travel_tools.static_maps.MAX_URL_LENGTH", 100), patch.object(
            urllib.request,
            "urlopen",
            side_effect=AssertionError("Static Maps HTTP call must not happen"),
        ):
            result = svc.get_map(
                [{"label": "A", "color": "green", "position": "x"}], self._long_polyline()
            )
        self.assertFalse(result.success)
        self.assertEqual(result.error, "static_maps_url_too_long")


class PlacesServiceTest(unittest.TestCase):
    def _service(self):
        from travel_tools.places_service import PlacesService

        return PlacesService("places-key", "geo-key")

    def test_search_lodging_parse(self):
        svc = self._service()
        payload = json.dumps(
            {
                "places": [
                    {
                        "id": "p1",
                        "displayName": {"text": "Hotel Alpha"},
                        "formattedAddress": "1 Main St",
                        "rating": 4.2,
                        "userRatingCount": 120,
                        "location": {"latitude": 30.0, "longitude": -97.0},
                    },
                    {"id": "p2", "displayName": {"text": "Motel Beta"}},
                ]
            }
        ).encode("utf-8")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse(payload)

        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            result = svc.search_lodging(30.0, -97.0, radius_meters=40000)

        self.assertTrue(result.success)
        self.assertEqual(captured["body"]["includedTypes"], ["lodging"])
        self.assertEqual(captured["body"]["maxResultCount"], 20)
        self.assertEqual(captured["body"]["locationRestriction"]["circle"]["center"]["latitude"], 30.0)
        self.assertEqual(len(result.places), 2)
        self.assertEqual(result.places[0]["name"], "Hotel Alpha")
        self.assertEqual(result.places[0]["rating"], 4.2)

    def test_search_lodging_missing_key(self):
        from travel_tools.places_service import PlacesService

        svc = PlacesService("")
        result = svc.search_lodging(30.0, -97.0)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "places_api_not_configured")

    def test_pick_random_excludes_previous(self):
        from travel_tools.places_service import PlacesService

        places = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
        rng = random.Random(42)
        picked = PlacesService.pick_random_hotel(places, ["A", "B"], rng=rng)
        self.assertEqual(picked["name"], "C")
        self.assertIsNone(PlacesService.pick_random_hotel(places, ["A", "B", "C"], rng=rng))

    def test_geocode_address_ok_and_zero_results(self):
        svc = self._service()
        ok_payload = json.dumps(
            {
                "status": "OK",
                "results": [{"geometry": {"location": {"lat": 29.76, "lng": -95.36}}}],
            }
        ).encode("utf-8")
        with patch.object(urllib.request, "urlopen", return_value=FakeResponse(ok_payload)):
            ok, coords, err = svc.geocode_address("Houston, TX")
        self.assertTrue(ok)
        self.assertEqual(coords, (29.76, -95.36))
        self.assertIsNone(err)

        zero = json.dumps({"status": "ZERO_RESULTS", "results": []}).encode("utf-8")
        with patch.object(urllib.request, "urlopen", return_value=FakeResponse(zero)):
            ok, coords, err = svc.geocode_address("nowhere")
        self.assertFalse(ok)
        self.assertEqual(err, "geocoding_no_result")

    def test_geocode_address_passes_through_raw_status(self):
        """非 ZERO_RESULTS 的失败（key 被拒/限流等）原样透传状态码，便于前端区分原因。"""
        svc = self._service()
        denied = json.dumps({"status": "REQUEST_DENIED", "results": []}).encode("utf-8")
        with patch.object(urllib.request, "urlopen", return_value=FakeResponse(denied)):
            ok, coords, err = svc.geocode_address("somewhere")
        self.assertFalse(ok)
        self.assertIsNone(coords)
        self.assertEqual(err, "REQUEST_DENIED")

    def test_geocode_error_text_mapping(self):
        from travel_tools.service import _geocode_error_text

        self.assertIn("查无此地址", _geocode_error_text("geocoding_no_result"))
        self.assertIn("门牌号", _geocode_error_text("geocoding_no_result"))
        self.assertIn("未配置", _geocode_error_text("geocoding_api_not_configured"))
        self.assertIn("REQUEST_DENIED", _geocode_error_text("REQUEST_DENIED"))
        self.assertIn("OVER_QUERY_LIMIT", _geocode_error_text("OVER_QUERY_LIMIT"))
        self.assertIn("无法解析", _geocode_error_text(None))


class BuildRouteMapServiceTest(unittest.TestCase):
    def test_missing_endpoints(self):
        from travel_tools.service import TravelToolConfig, build_route_map

        outcome = build_route_map(TravelToolConfig(routes_api_key="k"), " ", "Dest")
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error_code, "missing_endpoints")

    def test_success_composes_image(self):
        from travel_tools.routes_service import MultiStopRouteResult
        from travel_tools.service import TravelToolConfig, build_route_map
        from travel_tools.static_maps import FlexStaticMapsService, StaticMapResult

        png = make_test_png()
        route_result = MultiStopRouteResult(
            success=True,
            total_distance_meters=300000,
            total_duration_seconds=10800,
            legs=[
                {"distance_meters": 150000, "duration_seconds": 5400},
                {"distance_meters": 150000, "duration_seconds": 5400},
            ],
            encoded_polyline="poly123",
            status="success",
        )
        map_result = StaticMapResult(success=True, image_bytes=png, content_type="image/png", status="success")

        with patch("travel_tools.service.MultiStopRoutesService") as routes_cls, patch(
            "travel_tools.service.FlexStaticMapsService"
        ) as maps_cls:
            routes_cls.return_value.get_route.return_value = route_result
            maps_cls.return_value.get_map.return_value = map_result
            maps_cls.build_markers = FlexStaticMapsService.build_markers
            outcome = build_route_map(
                TravelToolConfig(routes_api_key="k", static_maps_api_key="k"),
                "Origin",
                "Dest",
                ["Stop 1"],
            )

        self.assertTrue(outcome.success)
        self.assertAlmostEqual(outcome.total_miles, 186.41, delta=0.2)
        self.assertEqual(len(outcome.leg_miles), 2)
        self.assertTrue(outcome.image_data_url.startswith("data:image/png;base64,"))
        markers_arg = maps_cls.return_value.get_map.call_args[0][0]
        self.assertEqual([m["label"] for m in markers_arg], ["A", "1", "B"])

    def test_route_failure_maps_to_friendly_error(self):
        from travel_tools.routes_service import MultiStopRouteResult
        from travel_tools.service import TravelToolConfig, build_route_map

        route_result = MultiStopRouteResult(success=False, status="failed", error="routes_auth_failed")
        with patch("travel_tools.service.MultiStopRoutesService") as routes_cls:
            routes_cls.return_value.get_route.return_value = route_result
            outcome = build_route_map(
                TravelToolConfig(routes_api_key="k"), "Origin", "Dest"
            )
        self.assertFalse(outcome.success)
        self.assertIn("鉴权失败", outcome.error)

    def test_static_error_text_mapping(self):
        from travel_tools.service import _static_error_text

        self.assertIn("路线跨度过大", _static_error_text("static_maps_url_too_long"))
        self.assertNotIn("过旧", _static_error_text("static_maps_bad_request"))
        self.assertIn("地址写法", _static_error_text("static_maps_bad_request"))


class HotelFinderServiceTest(unittest.TestCase):
    def _setup_places(self, places_cls, hotels):
        from travel_tools.places_service import PlacesService

        svc = places_cls.return_value
        svc.geocode_address.return_value = (True, (29.76, -95.36), None)
        svc.reverse_geocode_area.return_value = "Rosenberg, TX, USA"
        svc.search_lodging.return_value = MagicMock(success=True, places=hotels, error=None, status="success")
        # service.py calls the staticmethod on the (mocked) class, not the instance
        places_cls.pick_random_hotel.side_effect = (
            lambda places_, exclude, rng=None: PlacesService.pick_random_hotel(places_, exclude, rng)
        )

    def test_success_derives_point_and_verifies_drive(self):
        from travel_tools.places_service import PlaceResult
        from travel_tools.routes_service import MultiStopRouteResult
        from travel_tools.service import TravelToolConfig, find_hotel_near_origin
        from travel_tools.static_maps import StaticMapResult

        hotels = [
            {"name": "Hotel A", "address": "addr a", "rating": 4.0, "lat": 28.9, "lng": -96.5},
            {"name": "Hotel B", "address": "addr b", "rating": 3.0, "lat": 28.8, "lng": -96.6},
            {"name": "Hotel C", "address": "addr c", "rating": None, "lat": 28.7, "lng": -96.7},
        ]
        route_result = MultiStopRouteResult(
            success=True,
            total_distance_meters=500000,
            total_duration_seconds=18000,
            legs=[{"distance_meters": 500000, "duration_seconds": 18000}],
            encoded_polyline="drive-poly",
            status="success",
        )
        map_result = StaticMapResult(success=True, image_bytes=make_test_png(), content_type="image/png", status="success")

        config = TravelToolConfig(routes_api_key="k", static_maps_api_key="k", places_api_key="k")
        with patch("travel_tools.service.PlacesService") as places_cls, patch(
            "travel_tools.service.MultiStopRoutesService"
        ) as routes_cls, patch("travel_tools.service.FlexStaticMapsService") as maps_cls:
            self._setup_places(places_cls, hotels)
            routes_cls.return_value.get_route.return_value = route_result
            maps_cls.return_value.get_map.return_value = map_result
            outcome = find_hotel_near_origin(
                config,
                destination="Site X, TX",
                trip_distance_miles=350,
                bearing_label="NW",
                rng=random.Random(7),
            )

        self.assertTrue(outcome.success)
        self.assertIn(outcome.hotel["name"], {"Hotel A", "Hotel B", "Hotel C"})
        # Derived origin point sits ~350 mi NW of the destination
        self.assertAlmostEqual(outcome.target_miles, 350.0)
        self.assertIsNotNone(outcome.derived_lat)
        # Driving verification used the picked hotel
        route_call = routes_cls.return_value.get_route.call_args
        self.assertIn(",", route_call[0][0])  # hotel passed as lat,lng
        self.assertEqual(route_call[0][1], "Site X, TX")
        self.assertAlmostEqual(outcome.actual_miles, 310.69, delta=0.5)
        self.assertTrue(outcome.image_data_url.startswith("data:image/png;base64,"))
        self.assertEqual(outcome.bearing_label, "NW (315 deg)")
        # Static map got hotel + destination markers
        markers_arg = maps_cls.return_value.get_map.call_args[0][0]
        self.assertEqual([m["label"] for m in markers_arg], ["A", "B"])

    def test_geocode_failure_surfaces_specific_reason(self):
        """终点 geocode 失败时，用户看到的是具体原因而非笼统的"无法解析"。"""
        from travel_tools.service import TravelToolConfig, find_hotel_near_origin

        config = TravelToolConfig(routes_api_key="k", static_maps_api_key="k", places_api_key="k")
        for code, expect in (
            ("geocoding_no_result", "查无此地址"),
            ("geocoding_api_not_configured", "未配置"),
            ("REQUEST_DENIED", "REQUEST_DENIED"),
        ):
            with self.subTest(code=code):
                with patch("travel_tools.service.PlacesService") as places_cls:
                    places_cls.return_value.is_available.return_value = True
                    places_cls.return_value.geocode_address.return_value = (False, None, code)
                    outcome = find_hotel_near_origin(
                        config,
                        destination="Site X, TX",
                        trip_distance_miles=350,
                        bearing_label="NW",
                    )
                self.assertFalse(outcome.success)
                self.assertEqual(outcome.error_code, code)
                self.assertIn(expect, outcome.error)

    def test_exclude_reroll_can_exhaust_candidates(self):
        from travel_tools.service import TravelToolConfig, find_hotel_near_origin

        hotels = [{"name": "Only Hotel", "address": "x", "rating": 3.0, "lat": 28.9, "lng": -96.5}]
        config = TravelToolConfig(routes_api_key="k", static_maps_api_key="k", places_api_key="k")
        with patch("travel_tools.service.PlacesService") as places_cls, patch(
            "travel_tools.service.MultiStopRoutesService"
        ) as routes_cls, patch("travel_tools.service.FlexStaticMapsService") as maps_cls:
            self._setup_places(places_cls, hotels)
            outcome = find_hotel_near_origin(
                config,
                destination="Site X",
                trip_distance_miles=100,
                bearing_label="N",
                exclude_names=["Only Hotel"],
                rng=random.Random(1),
            )
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error_code, "all_candidates_excluded")

    def test_invalid_bearing_rejected(self):
        from travel_tools.service import TravelToolConfig, find_hotel_near_origin

        outcome = find_hotel_near_origin(
            TravelToolConfig(places_api_key="k"),
            destination="Site X",
            trip_distance_miles=100,
            bearing_label="ZZ",
        )
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error_code, "invalid_bearing")

    def test_distance_out_of_range(self):
        from travel_tools.service import TravelToolConfig, find_hotel_near_origin

        outcome = find_hotel_near_origin(
            TravelToolConfig(places_api_key="k"),
            destination="Site X",
            trip_distance_miles=9999,
            bearing_label="N",
        )
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error_code, "distance_out_of_range")


class EvidenceCompositionTest(unittest.TestCase):
    def test_compose_adds_panel_below_map(self):
        from travel_tools import evidence

        png = make_test_png(width=640, height=400)
        lines = ["Line 1", "Line 2", "Line 3"]
        composed = evidence.compose_map_with_panel(png, "Route Map", lines)
        self.assertIsNotNone(composed)
        width, height = evidence.image_size(composed)
        self.assertEqual(width, 640)
        # 400 map + 2*20 padding + 28 title + 3*22 lines
        self.assertEqual(height, 400 + 40 + 28 + 66)

    def test_compose_rejects_corrupt_image(self):
        from travel_tools import evidence

        self.assertIsNone(evidence.compose_map_with_panel(b"not-a-png", "T", ["x"]))

    def test_hotel_route_lines_are_plain_route_evidence(self):
        """The hotel-finder PNG must read as a normal route, never as a hotel search."""
        from travel_tools import evidence

        lines = evidence.build_hotel_route_lines(
            "Comfort Inn Somewhere",
            "1234 Highway 36, Rosenberg, TX 77471",
            "Site X, Houston, TX",
            341.7,
            "5h 06m",
            "google_routes",
        )
        joined = " | ".join(lines)
        # Route-evidence essentials are present
        self.assertIn("Origin: Comfort Inn Somewhere", joined)
        self.assertIn("Destination: Site X, Houston, TX", joined)
        self.assertIn("One-way Distance: 341.70 mi", joined)
        self.assertIn("Driving Time: 5h 06m", joined)
        # No trace of the hotel-search process
        for leaked in ("Hotel:", "Rating", "Bearing", "Target", "delta", "Area"):
            self.assertNotIn(leaked, joined)

    def test_hotel_route_lines_missing_drive_falls_back(self):
        from travel_tools import evidence

        lines = evidence.build_hotel_route_lines(
            "Hotel A", "addr", "Dest", None, "-", "google_routes"
        )
        self.assertIn("One-way Distance: n/a", " | ".join(lines))


class TravelToolsWebWiringTest(unittest.TestCase):
    """Smoke tests: routes registered, guards redirect anonymous users."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        shutil.copytree(REPO_DIR / "ai_daily_report", Path(cls.temp_dir.name) / "ai_daily_report")
        shutil.copytree(REPO_DIR / "travel_tools", Path(cls.temp_dir.name) / "travel_tools")
        sys.path.insert(0, str(cls.temp_dir.name))
        spec = importlib.util.spec_from_file_location("invoice_tool_travel_tools_test", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")
        with cls.module.app.app_context():
            cls.module.init_db()

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(cls.temp_dir.name))
        cls.temp_dir.cleanup()

    def test_travel_tools_menu_seeded_for_internal_roles(self):
        with self.module.app.app_context():
            rows = self.module.db().execute(
                "select role, is_enabled from role_menu_permissions where menu_key = 'travel_tools' order by role"
            ).fetchall()
        roles = {row["role"] for row in rows if row["is_enabled"]}
        self.assertEqual(roles, {"admin", "manager", "finance", "employee"})

    def test_page_requires_login(self):
        client = self.module.app.test_client()
        resp = client.get("/travel-tools")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_api_requires_login(self):
        client = self.module.app.test_client()
        for url in ("/travel-tools/api/route-map", "/travel-tools/api/hotel"):
            resp = client.post(url, json={})
            self.assertEqual(resp.status_code, 302, url)

    def test_page_renders_for_internal_user(self):
        client = self.module.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self._admin_id()
        resp = client.get("/travel-tools")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("出行工具", body)
        self.assertIn("travel-tools/api/route-map", body)

    def test_page_forbidden_for_external_role(self):
        client = self.module.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self._external_id()
        resp = client.get("/travel-tools")
        self.assertEqual(resp.status_code, 403)

    def _admin_id(self):
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select id from users where role = 'admin' order by id limit 1"
            ).fetchone()
        return row["id"] if row else self._create_user("tt-admin", "admin")

    def _external_id(self):
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select id from users where role in ('external_manager','external_employee') order by id limit 1"
            ).fetchone()
        return row["id"] if row else self._create_user("tt-ext", "external_employee")

    def _create_user(self, name, role):
        from werkzeug.security import generate_password_hash

        with self.module.app.app_context():
            cursor = self.module.db().execute(
                "insert into users (name, email, password_hash, role, is_active, created_at) values (?, ?, ?, ?, 1, datetime('now'))",
                (name, f"{name}@example.invalid", generate_password_hash("pw-123456"), role),
            )
            self.module.db().commit()
            return cursor.lastrowid


if __name__ == "__main__":
    unittest.main()
