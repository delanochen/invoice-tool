# -*- coding: utf-8 -*-
"""工资生成前的里程佐证拦截测试（用假 API，不连数据库）。"""
import unittest
from datetime import date

from employee_finance import _reject_unsubstantiated_mileage


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params
        return FakeResult(self.rows)


class FakeAPI:
    def __init__(self, rows):
        self.db = FakeDB(rows)

    def __getitem__(self, key):
        if key == "db":
            return lambda: self.db
        raise KeyError(key)


def _row(user_id, name, work_date):
    return {
        "worker_id": user_id,
        "worker_name": name,
        "report_id": 100 + user_id,
        "report_date": work_date,
        "work_date": work_date,
    }


class PayrollMileageGuardTest(unittest.TestCase):
    def test_no_rows_passes(self):
        api = FakeAPI([])
        _reject_unsubstantiated_mileage(api, date(2026, 11, 9), date(2026, 11, 22), {7})
        self.assertIn("service_report_mileage_evidence", api.db.sql)

    def test_empty_employee_set_skips_query(self):
        api = FakeAPI([_row(7, "张三", "2026-11-19")])
        _reject_unsubstantiated_mileage(api, date(2026, 11, 9), date(2026, 11, 22), set())
        self.assertEqual(api.db.sql, "", "没有员工时不查库")

    def test_waiver_join_is_used_when_table_available(self):
        api = FakeAPI([])
        _reject_unsubstantiated_mileage(
            api, date(2026, 11, 9), date(2026, 11, 22), {7},
            waivers_available=True,
        )
        self.assertIn("service_report_mileage_zero_waivers", api.db.sql)
        self.assertIn("wz.worker_user_id is null", api.db.sql)

    def test_blocks_with_employee_and_date_list(self):
        api = FakeAPI([
            _row(7, "张三", "2026-11-19"),
            _row(7, "张三", "2026-11-20"),
            _row(8, "李四", "2026-11-21"),
        ])
        with self.assertRaises(ValueError) as ctx:
            _reject_unsubstantiated_mileage(api, date(2026, 11, 9), date(2026, 11, 22), {7, 8})
        message = str(ctx.exception)
        self.assertIn("张三", message)
        self.assertIn("2026-11-19, 2026-11-20", message)
        self.assertIn("李四", message)
        self.assertIn("里程佐证", message)
        self.assertEqual(set(api.db.params[2:]), {7, 8})


if __name__ == "__main__":
    unittest.main()
