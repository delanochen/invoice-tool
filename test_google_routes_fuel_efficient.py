# -*- coding: utf-8 -*-
"""Google Routes 省油路线（FUEL_EFFICIENT）选路测试。

目标：工作日报里程佐证与辅助填写预览应采用与 Google Maps 手机端
「绿色叶子」一致的省油路线，而不是 TRAFFIC_UNAWARE 默认路线；
但**不得人为增加里程**，也不得简单选择最长路线。

全部使用 mock，绝不调用真实收费 API。

覆盖：
A. 请求体：开启时 travelMode/routingPreference/requestedReferenceRoutes/
   routeModifiers.vehicleInfo.emissionType/computeAlternativeRoutes
B. 请求体：emissionType 可配置，默认 GASOLINE
C. Field Mask：开启时含 routes.routeLabels；关闭时不含
D. 选路：同时返回 DEFAULT_ROUTE + FUEL_EFFICIENT -> 选 FUEL_EFFICIENT
E. 选路：省油路线里程更长也必须选它（不挑最短）
F. 选路：省油路线在末尾也必须选中（不能只取 routes[0]）
G. 选路：没有 FUEL_EFFICIENT -> 回退 DEFAULT_ROUTE
H. 选路：完全没有 routeLabels -> 兜底 routes[0]
I. 关闭 prefer_fuel_efficient -> 保持历史行为（routes[0]，请求体逐字不变）
J. 距离换算：仍使用 Google 原始 distanceMeters / 1609.344，无系数放大
K. 往返里程仍按 trip_type 规则（往返 ×2）
L. routeLabels 保存进 RouteResult 与 worker（审计）
M. 端到端：Mock HTTP 响应两条路线时，走省油路线且里程与原始值一致
"""
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_DIR = Path(__file__).resolve().parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from ai_daily_report.google_routes import (  # noqa: E402
    DEFAULT_EMISSION_TYPE,
    FIELD_MASK,
    FUEL_EFFICIENT_FIELD_MASK,
    GoogleRoutesService,
    RouteResult,
)
from ai_daily_report.mileage_service import METERS_PER_MILE, MileageService  # noqa: E402


def make_route(meters, labels, duration="600s", polyline="enc_polyline"):
    route = {"distanceMeters": meters, "duration": duration}
    if polyline is not None:
        route["polyline"] = {"encodedPolyline": polyline}
    if labels is not None:
        route["routeLabels"] = labels
    return route


class FuelEfficientRequestBodyTest(unittest.TestCase):
    """A / B / C：请求体与 Field Mask。"""

    def test_A_fuel_efficient_request_body(self):
        """A. 开启后请求体字段完全符合 Google 省油路线要求。"""
        svc = GoogleRoutesService("key", prefer_fuel_efficient=True)
        body = svc._build_request_body("Origin St", "Dest St")
        self.assertEqual(body["travelMode"], "DRIVE")
        self.assertEqual(body["routingPreference"], "TRAFFIC_AWARE_OPTIMAL")
        self.assertEqual(body["requestedReferenceRoutes"], ["FUEL_EFFICIENT"])
        self.assertEqual(
            body["routeModifiers"]["vehicleInfo"]["emissionType"], "GASOLINE"
        )
        self.assertIs(body["computeAlternativeRoutes"], False)
        self.assertEqual(body["origin"], {"address": "Origin St"})
        self.assertEqual(body["destination"], {"address": "Dest St"})

    def test_B_emission_type_configurable(self):
        """B. emission_type 可配置；默认 GASOLINE；大小写/空白归一化。"""
        default_svc = GoogleRoutesService("key", prefer_fuel_efficient=True)
        self.assertEqual(
            default_svc._build_request_body("a", "b")["routeModifiers"]["vehicleInfo"][
                "emissionType"
            ],
            DEFAULT_EMISSION_TYPE,
        )
        svc = GoogleRoutesService("key", prefer_fuel_efficient=True, emission_type="diesel")
        self.assertEqual(
            svc._build_request_body("a", "b")["routeModifiers"]["vehicleInfo"]["emissionType"],
            "DIESEL",
        )
        # 空白 / None 回退默认，不发出空 emissionType
        blank = GoogleRoutesService("key", prefer_fuel_efficient=True, emission_type="  ")
        self.assertEqual(
            blank._build_request_body("a", "b")["routeModifiers"]["vehicleInfo"]["emissionType"],
            "GASOLINE",
        )

    def test_C_field_mask_contains_route_labels(self):
        """C. 开启时 Field Mask 必须含 routes.routeLabels，否则无法按标签选路。"""
        on = GoogleRoutesService("key", prefer_fuel_efficient=True)
        off = GoogleRoutesService("key", prefer_fuel_efficient=False)
        self.assertIn("routes.routeLabels", on._field_mask())
        self.assertEqual(on._field_mask(), FUEL_EFFICIENT_FIELD_MASK)
        self.assertNotIn("routes.routeLabels", off._field_mask())
        self.assertEqual(off._field_mask(), FIELD_MASK)


class FuelEfficientRouteSelectionTest(unittest.TestCase):
    """D / E / F / G / H：选路语义。"""

    def setUp(self):
        self.svc = GoogleRoutesService("key", prefer_fuel_efficient=True)

    def parse(self, routes):
        return self.svc._parse_response({"routes": routes}, select_fuel_efficient=True)

    def test_D_prefers_fuel_efficient_when_both_present(self):
        """D. 同时有 DEFAULT_ROUTE 和 FUEL_EFFICIENT -> 选省油。"""
        result = self.parse([
            make_route(10000, ["DEFAULT_ROUTE"]),
            make_route(12000, ["FUEL_EFFICIENT"]),
        ])
        self.assertTrue(result.success)
        self.assertEqual(result.route_labels, ["FUEL_EFFICIENT"])
        self.assertTrue(result.is_fuel_efficient)

    def test_E_longer_fuel_efficient_is_still_chosen(self):
        """E. 省油路线更长也必须选它——目标是省油语义，不是最短/最长路线。"""
        result = self.parse([
            make_route(8000, ["DEFAULT_ROUTE"]),          # 最短
            make_route(15000, ["FUEL_EFFICIENT"]),        # 最长，但省油
        ])
        self.assertEqual(result.route_labels, ["FUEL_EFFICIENT"])
        self.assertEqual(result.distance_meters, 15000.0)

    def test_F_fuel_efficient_anywhere_in_list(self):
        """F. 省油路线不在首位也要选中（不能无条件 routes[0]）。"""
        result = self.parse([
            make_route(9000, ["DEFAULT_ROUTE"]),
            make_route(9500, ["DEFAULT_ROUTE", "SHORTER"]),
            make_route(13000, ["FUEL_EFFICIENT"]),
        ])
        self.assertEqual(result.route_labels, ["FUEL_EFFICIENT"])
        self.assertEqual(result.distance_meters, 13000.0)

    def test_G_falls_back_to_default_route(self):
        """G. 没有 FUEL_EFFICIENT -> 回退 DEFAULT_ROUTE（而非 routes[0]）。"""
        result = self.parse([
            make_route(9000, ["SHORTER"]),               # routes[0] 但不是默认路线
            make_route(11000, ["DEFAULT_ROUTE"]),
        ])
        self.assertEqual(result.route_labels, ["DEFAULT_ROUTE"])
        self.assertEqual(result.distance_meters, 11000.0)
        self.assertFalse(result.is_fuel_efficient)

    def test_G2_falls_back_even_when_default_is_first(self):
        """G2. 回退顺序不受位置影响，始终取 DEFAULT_ROUTE。"""
        result = self.parse([
            make_route(11000, ["DEFAULT_ROUTE"]),
            make_route(9000, []),
        ])
        self.assertEqual(result.route_labels, ["DEFAULT_ROUTE"])

    def test_H_no_labels_falls_back_to_first_route(self):
        """H. 完全没有 routeLabels（旧响应/默认模式）-> 兜底 routes[0]。"""
        result = self.parse([make_route(8000, None), make_route(9000, None)])
        self.assertEqual(result.distance_meters, 8000.0)
        self.assertEqual(result.route_labels, [])

    def test_H2_empty_labels_list_falls_back_to_first_route(self):
        """H2. routeLabels 为空数组同样兜底 routes[0]。"""
        result = self.parse([make_route(8000, []), make_route(9000, ["DEFAULT_ROUTE"])])
        # 有 DEFAULT_ROUTE 时仍优先默认路线（比空标签更明确）
        self.assertEqual(result.route_labels, ["DEFAULT_ROUTE"])

    def test_selection_ignores_distance_for_labels(self):
        """选路只看标签，绝不按里程长短挑（反例：最长路线不是省油就不选）。"""
        result = self.parse([
            make_route(9000, ["DEFAULT_ROUTE"]),
            make_route(99999, []),   # 最长但无标签
        ])
        self.assertEqual(result.route_labels, ["DEFAULT_ROUTE"])
        self.assertEqual(result.distance_meters, 9000.0)


class DefaultBehaviourUnchangedTest(unittest.TestCase):
    """I：关闭开关时行为与历史完全一致。"""

    def test_I_request_body_matches_legacy(self):
        """I. 默认请求体与历史实现逐字一致（不能引入 recomputed 差异）。"""
        svc = GoogleRoutesService("key")
        self.assertEqual(
            svc._build_request_body("A", "B"),
            {
                "origin": {"address": "A"},
                "destination": {"address": "B"},
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_UNAWARE",
                "computeAlternativeRoutes": False,
            },
        )

    def test_I_default_ignores_route_labels_and_uses_first(self):
        """I2. 默认模式仍取 routes[0]，即使它不是省油路线。"""
        svc = GoogleRoutesService("key")
        result = svc._parse_response(
            {"routes": [
                make_route(10000, ["DEFAULT_ROUTE"]),
                make_route(12000, ["FUEL_EFFICIENT"]),
            ]},
            select_fuel_efficient=False,
        )
        self.assertEqual(result.distance_meters, 10000.0)
        self.assertFalse(result.is_fuel_efficient)

    def test_I3_no_neutral_fields_added_in_default_mode(self):
        """I3. 默认模式请求体不含省油专用字段。"""
        body = GoogleRoutesService("key")._build_request_body("A", "B")
        self.assertNotIn("requestedReferenceRoutes", body)
        self.assertNotIn("routeModifiers", body)


class MileageConversionTest(unittest.TestCase):
    """J / K：里程换算与往返规则。"""

    @staticmethod
    def _worker(**kw):
        worker = MagicMock()
        for key, value in kw.items():
            setattr(worker, key, value)
        return worker

    def _service_with_result(self, result):
        routes = MagicMock()
        routes.get_driving_route.return_value = result
        return MileageService(routes)

    def test_J_uses_raw_distance_meters_without_uplift(self):
        """J. 直接使用 Google 原始 distanceMeters / 1609.344，无系数放大。"""
        meters = 16093.44  # 恰好 10 英里
        result = RouteResult(
            success=True, distance_meters=meters, duration_seconds=600,
            status="success", route_labels=["FUEL_EFFICIENT"],
        )
        svc = self._service_with_result(result)
        worker = self._worker(
            transportation="self_drive", origin="A", origin_confirmed=True,
            destination="B", route_status="not_calculated", route_distance_meters=None,
            trip_type="one_way", travel_hours_source=None,
        )
        svc.calculate_for_worker(worker)
        expected_one_way = meters / METERS_PER_MILE
        self.assertAlmostEqual(worker.one_way_miles, round(expected_one_way, 2), places=2)
        self.assertEqual(worker.route_distance_meters, meters)  # 原始值原样保存
        self.assertEqual(worker.reported_miles, round(expected_one_way, 2))

    def test_K_round_trip_doubles_fuel_efficient_distance(self):
        """K. 往返仍按 trip_type 规则 ×2，与是否省油无关。"""
        meters = 16093.44
        result = RouteResult(
            success=True, distance_meters=meters, duration_seconds=600,
            status="success", route_labels=["FUEL_EFFICIENT"],
        )
        svc = self._service_with_result(result)
        worker = self._worker(
            transportation="self_drive", origin="A", origin_confirmed=True,
            destination="B", route_status="not_calculated", route_distance_meters=None,
            trip_type="round_trip", travel_hours_source=None,
        )
        svc.calculate_for_worker(worker)
        one_way = meters / METERS_PER_MILE
        self.assertEqual(worker.reported_miles, round(one_way * 2, 2))

    def test_no_coefficient_hack_applied(self):
        """反例：里程不得被人为乘系数——一万米就该是一万米换算结果。"""
        meters = 10000
        result = RouteResult(success=True, distance_meters=meters, status="success")
        svc = self._service_with_result(result)
        worker = self._worker(
            transportation="self_drive", origin="A", origin_confirmed=True,
            destination="B", route_status="not_calculated", route_distance_meters=None,
            trip_type="one_way", travel_hours_source=None,
        )
        svc.calculate_for_worker(worker)
        self.assertAlmostEqual(worker.one_way_miles, round(meters / METERS_PER_MILE, 2), places=2)


class RouteLabelsAuditTest(unittest.TestCase):
    """L：routeLabels 进入审计数据。"""

    def test_L_route_result_exposes_labels(self):
        result = RouteResult(success=True, route_labels=["FUEL_EFFICIENT"])
        self.assertEqual(result.route_labels, ["FUEL_EFFICIENT"])
        self.assertTrue(result.is_fuel_efficient)

    def test_L2_default_result_has_empty_labels(self):
        result = RouteResult(success=True)
        self.assertEqual(result.route_labels, [])
        self.assertFalse(result.is_fuel_efficient)

    def test_L3_worker_saves_route_labels(self):
        """worker 上必须记录被选中路线的标签，便于确认走的是省油路线。"""
        routes = MagicMock()
        routes.get_driving_route.return_value = RouteResult(
            success=True, distance_meters=10000, duration_seconds=600,
            status="success", route_labels=["FUEL_EFFICIENT"],
        )
        worker = MagicMock()
        worker.transportation = "self_drive"
        worker.origin = "A"
        worker.origin_confirmed = True
        worker.destination = "B"
        worker.route_status = "not_calculated"
        worker.route_distance_meters = None
        worker.trip_type = "one_way"
        worker.travel_hours_source = None
        MileageService(routes).calculate_for_worker(worker)
        self.assertEqual(worker.route_labels, ["FUEL_EFFICIENT"])

    def test_L4_failed_route_clears_route_labels(self):
        """失败时不得残留上一次成功的 label（避免审计误判）。"""
        routes = MagicMock()
        routes.get_driving_route.return_value = RouteResult(
            success=False, status="failed", error="boom",
        )
        worker = MagicMock()
        worker.transportation = "self_drive"
        worker.origin = "A"
        worker.origin_confirmed = True
        worker.destination = "B"
        worker.route_status = "not_calculated"
        worker.route_distance_meters = None
        worker.trip_type = "one_way"
        worker.travel_hours_source = None
        worker.route_labels = ["FUEL_EFFICIENT"]
        MileageService(routes).calculate_for_worker(worker)
        self.assertIsNone(worker.route_labels)


class EndToEndRequestTest(unittest.TestCase):
    """M：端到端（mock HTTP），验证实际发出的请求头/体。"""

    def _capture(self, service, payload):
        captured = {}

        class FakeResponse:
            def __init__(self, data):
                self._data = data
            def read(self):
                return self._data
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            # urllib 会把 header 名归一化成 "X-goog-fieldmask"，统一转小写比较，
            # 避免测试绑定在 urllib 的大小写规则上。
            captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse(json.dumps(payload).encode("utf-8"))

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = service.get_driving_route("Origin", "Dest")
        return captured, result

    def test_M_request_headers_and_body_when_fuel_efficient(self):
        """M. 实际请求带 X-Goog-FieldMask=...routeLabels 与省油参数。"""
        svc = GoogleRoutesService("test-key", prefer_fuel_efficient=True)
        captured, result = self._capture(svc, {"routes": [
            make_route(10000, ["DEFAULT_ROUTE"]),
            make_route(12000, ["FUEL_EFFICIENT"]),
        ]})
        self.assertEqual(captured["url"], "https://routes.googleapis.com/directions/v2:computeRoutes")
        self.assertIn("routes.routeLabels", captured["headers"]["x-goog-fieldmask"])
        self.assertEqual(captured["headers"]["x-goog-api-key"], "test-key")
        self.assertEqual(captured["body"]["requestedReferenceRoutes"], ["FUEL_EFFICIENT"])
        self.assertEqual(captured["body"]["routingPreference"], "TRAFFIC_AWARE_OPTIMAL")
        self.assertEqual(
            captured["body"]["routeModifiers"]["vehicleInfo"]["emissionType"], "GASOLINE"
        )
        # 端到端选了省油路线，且距离就是 Google 原始值（未被放大）
        self.assertTrue(result.success)
        self.assertTrue(result.is_fuel_efficient)
        self.assertEqual(result.distance_meters, 12000.0)

    def test_M2_default_mode_field_mask_has_no_labels(self):
        """M2. 默认模式不请求 routeLabels，保持旧 payload。"""
        svc = GoogleRoutesService("test-key")
        captured, _ = self._capture(svc, {"routes": [make_route(5000, None)]})
        self.assertNotIn("routeLabels", captured["headers"]["x-goog-fieldmask"])
        self.assertNotIn("requestedReferenceRoutes", captured["body"])
        self.assertEqual(captured["body"]["routingPreference"], "TRAFFIC_UNAWARE")

    def test_M3_api_key_never_in_body(self):
        """API key 只走 header，绝不进 body / 日志。"""
        svc = GoogleRoutesService("super-secret-key", prefer_fuel_efficient=True)
        captured, _ = self._capture(svc, {"routes": [make_route(1000, ["FUEL_EFFICIENT"])]})
        self.assertNotIn("super-secret-key", json.dumps(captured["body"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class WorkReportEntryPointWiringTest(unittest.TestCase):
    """app.py 两个工作日报入口必须显式开启省油路线，且其他入口保持默认。

    这是「预览里程 == 最终佐证里程」的关键：两处若策略不一致，
    用户会看到预览和佐证对不上。用源码契约钉住，避免回归。
    """

    @classmethod
    def setUpClass(cls):
        cls.source = (REPO_DIR / "app.py").read_text(encoding="utf-8")

    def _function_body(self, name):
        marker = f"def {name}("
        start = self.source.index(marker)
        # 到下一个顶层 def 为止
        rest = self.source[start + len(marker):]
        end = rest.find("\ndef ")
        return rest[: end if end != -1 else len(rest)]

    def test_mileage_evidence_enables_fuel_efficient(self):
        """里程佐证生成入口必须开启 prefer_fuel_efficient。"""
        body = self._function_body("generate_service_report_mileage_evidence")
        self.assertIn("prefer_fuel_efficient=True", body)

    def test_assist_plan_enables_fuel_efficient(self):
        """辅助填写预览入口必须开启 prefer_fuel_efficient（同策略）。"""
        body = self._function_body("service_report_assist_plan")
        self.assertIn("prefer_fuel_efficient=True", body)

    def test_both_entry_points_use_same_strategy(self):
        """两处都用 prefer_fuel_efficient=True，不存在一个开一个不开。"""
        evidence = self._function_body("generate_service_report_mileage_evidence")
        assist = self._function_body("service_report_assist_plan")
        self.assertIn("prefer_fuel_efficient=True", evidence)
        self.assertIn("prefer_fuel_efficient=True", assist)
        self.assertNotIn("prefer_fuel_efficient=False", evidence)
        self.assertNotIn("prefer_fuel_efficient=False", assist)

    def test_global_default_is_not_flipped(self):
        """不得把 GoogleRoutesService 的构造默认值改成省油路线（会误伤出行工具等）。"""
        from ai_daily_report.google_routes import GoogleRoutesService as Svc
        import inspect

        signature = inspect.signature(Svc.__init__)
        self.assertIs(signature.parameters["prefer_fuel_efficient"].default, False)

    def test_only_two_entry_points_enable_fuel_efficient(self):
        """全库只允许工作日报的两个入口开启省油路线，其他调用保持默认。

        这是「不误伤出行工具 / AI 日报草稿」的守卫：一旦有人在别处
        也传 prefer_fuel_efficient=True，数量会变成 3，这里立刻失败。
        """
        count = self.source.count("prefer_fuel_efficient=True")
        self.assertEqual(count, 2, msg="应恰好有两处开启省油路线（里程佐证 + 辅助填写）")

    def test_ai_daily_report_draft_keeps_default(self):
        """AI 日报草稿的里程计算入口不得开启省油路线（保留历史行为）。"""
        # 草稿链路的 GoogleRoutesService 由 _service_report_assist_routes_service
        # 之类的工厂构造；这里直接钉住草稿侧唯一构造点没有传该开关。
        body = self._function_body("_ai_report_draft_routes_service") if (
            "def _ai_report_draft_routes_service(" in self.source
        ) else None
        if body is None:
            self.skipTest("草稿侧无独立工厂函数，已由 test_only_two_entry_points_enable_fuel_efficient 覆盖")
        self.assertNotIn("prefer_fuel_efficient=True", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
