# -*- coding: utf-8 -*-
"""保存日报时里程 / 交通时长自动回填的口径测试。

- 只填空值，人工填的数（含显式 0）不覆盖；
- 自驾 / 租车走与辅助填写同一套 plan_worker 结果；
- 随行不请求路线，留空按 0；
- 路线失败按 0 落库并列出失败员工，供工资生成前拦截。
"""
import unittest

from invoice_tool.service_reports.mileage_autofill import auto_fill_missing_worker_travel


class FakeAssist:
    def __init__(self, plans=None):
        self.plans = plans or {}
        self.calls = []

    def plan_worker(self, worker, destination, fallback_origin):
        self.calls.append((worker, destination, fallback_origin))
        return self.plans.get(str(worker.get("user_id")), {
            "route_status": "failed", "reason": "route_failed",
        })


def _row(user_id, mode="self_drive", miles="", hours="", origin="", trip=""):
    return {
        "worker_user_id": user_id,
        "worker_travel_mode": mode,
        "worker_driving_miles": miles,
        "worker_travel_hours": hours,
        "worker_origin": origin,
        "worker_trip_type": trip,
    }


class MileageAutoFillTest(unittest.TestCase):
    def test_fills_blank_driving_values_from_route_plan(self):
        rows = [_row("7", origin="1 Main St", trip="round_trip")]
        assist = FakeAssist({"7": {
            "route_status": "success", "reported_miles": 24.5, "travel_hours": 1.75,
        }})
        result = auto_fill_missing_worker_travel(
            rows, destination="Site A", fallback_origin="", assist=assist,
        )
        self.assertEqual(rows[0]["worker_driving_miles"], "24.5")
        self.assertEqual(rows[0]["worker_travel_hours"], "1.75")
        self.assertEqual(len(result["filled"]), 1)
        worker, destination, _ = assist.calls[0]
        self.assertEqual(destination, "Site A")
        self.assertEqual(worker["current_origin"], "1 Main St")

    def test_never_overwrites_manually_entered_values_including_zero(self):
        rows = [_row("7", miles="0", hours="0")]
        assist = FakeAssist({"7": {
            "route_status": "success", "reported_miles": 99, "travel_hours": 8,
        }})
        auto_fill_missing_worker_travel(rows, destination="Site A", fallback_origin="", assist=assist)
        self.assertEqual(rows[0]["worker_driving_miles"], "0")
        self.assertEqual(rows[0]["worker_travel_hours"], "0")
        self.assertEqual(assist.calls, [], "已有值（含显式 0）不应请求路线")

    def test_only_fills_the_blank_half(self):
        rows = [_row("7", miles="12", hours="")]
        assist = FakeAssist({"7": {
            "route_status": "success", "reported_miles": 99, "travel_hours": 2.5,
        }})
        auto_fill_missing_worker_travel(rows, destination="Site A", fallback_origin="", assist=assist)
        self.assertEqual(rows[0]["worker_driving_miles"], "12")
        self.assertEqual(rows[0]["worker_travel_hours"], "2.5")

    def test_following_uses_zero_without_route_call(self):
        rows = [_row("8", mode="following")]
        assist = FakeAssist()
        result = auto_fill_missing_worker_travel(rows, destination="Site A", fallback_origin="", assist=assist)
        self.assertEqual(rows[0]["worker_driving_miles"], "0")
        self.assertEqual(rows[0]["worker_travel_hours"], "0")
        self.assertEqual(assist.calls, [])
        self.assertEqual(result["filled"], [])

    def test_failed_route_falls_back_to_zero_and_reports_worker(self):
        rows = [_row("9", mode="rental_drive")]
        assist = FakeAssist({"9": {"route_status": "blocked", "reason": "missing_origin"}})
        result = auto_fill_missing_worker_travel(rows, destination="Site A", fallback_origin="", assist=assist)
        self.assertEqual(rows[0]["worker_driving_miles"], "0")
        self.assertEqual(rows[0]["worker_travel_hours"], "0")
        self.assertEqual(result["failed"][0]["reason"], "missing_origin")

    def test_flight_is_left_untouched(self):
        rows = [_row("10", mode="flight", miles="", hours="")]
        assist = FakeAssist()
        result = auto_fill_missing_worker_travel(rows, destination="Site A", fallback_origin="", assist=assist)
        self.assertEqual(rows[0]["worker_driving_miles"], "")
        self.assertEqual(result["filled"], [])
        self.assertEqual(assist.calls, [])


if __name__ == "__main__":
    unittest.main()
