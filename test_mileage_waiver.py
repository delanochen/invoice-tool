# -*- coding: utf-8 -*-
"""人工确认 0 英里豁免的纯单元测试（假 DB，不连数据库）。"""
import unittest

from invoice_tool.service_reports import mileage_waivers as waivers


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeDB:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return FakeResult(self.rows)


class MileageWaiverTest(unittest.TestCase):
    def test_normalize_confirmation(self):
        for value in ("1", "true", "YES", "on"):
            self.assertTrue(waivers.normalize_confirmation(value))
        for value in ("", None, 0, "0", "false", "no"):
            self.assertFalse(waivers.normalize_confirmation(value))

    def test_applicable_only_for_driving_zero_miles(self):
        base = {"worker_travel_mode": "self_drive", "worker_zero_mileage_confirmed": "1"}
        self.assertTrue(waivers.is_applicable(base, 0))
        self.assertTrue(waivers.is_applicable(base, "0.0"))
        self.assertFalse(waivers.is_applicable(base, 12.5))
        self.assertFalse(waivers.is_applicable({**base, "worker_travel_mode": "following"}, 0))
        self.assertFalse(waivers.is_applicable({**base, "worker_zero_mileage_confirmed": "0"}, 0))

    def test_replace_for_report_only_writes_applicable_rows(self):
        db = FakeDB()
        rows = [
            {"worker_user_id": "7", "worker_travel_mode": "self_drive",
             "worker_driving_miles": 0, "worker_zero_mileage_confirmed": "1"},
            {"worker_user_id": "8", "worker_travel_mode": "self_drive",
             "worker_driving_miles": 0, "worker_zero_mileage_confirmed": "0"},
            {"worker_user_id": "9", "worker_travel_mode": "rental_drive",
             "worker_driving_miles": "35.4", "worker_zero_mileage_confirmed": "1"},
        ]
        written = waivers.replace_for_report(
            lambda: db, 42, rows, user_id=3, now_text="2026-11-19 12:00:00"
        )
        self.assertEqual(written, 1)
        self.assertEqual(len(db.calls), 2, "一条删除 + 一条符合条件的插入")
        self.assertIn("delete from", db.calls[0][0])
        self.assertEqual(db.calls[1][1], (42, 7, 3, "2026-11-19 12:00:00"))

    def test_user_ids_for_report(self):
        db = FakeDB([{"worker_user_id": 7}, {"worker_user_id": 8}])
        self.assertEqual(waivers.user_ids_for_report(lambda: db, 42), {7, 8})

    def test_clauses_disabled_when_table_missing(self):
        self.assertEqual(waivers.join_clause(False), "")
        self.assertEqual(waivers.not_waived_clause(False), "")
        self.assertIn("service_report_mileage_zero_waivers", waivers.join_clause(True))


if __name__ == "__main__":
    unittest.main()
