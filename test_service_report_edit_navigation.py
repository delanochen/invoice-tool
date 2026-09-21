"""工作日报编辑页「上一条 / 下一条」（同工单）导航的回归测试。

与查看页（test_service_report_navigation.py，全局范围）的关键差异：
编辑页导航只在本工单内切换，且外部员工只能在自己创建的日报间导航
（与 edit_service_report 的 created_by 守卫一致，导航不得给出点了就 403 的链接）。

覆盖：
  * 同工单内日期降序（同日按 id desc）的三态：最新一条只有「下一条」、中间双链接、最早一条只有「上一条」；
  * 边界处按钮置灰（disabled）且带提示文案；
  * 链接指向同工单相邻日报的编辑页（/edit）；
  * 跨工单的日报（哪怕全局更新）不得出现在编辑页导航里；
  * 外部员工：他人在同工单创建的日报不出现在导航中，直接访问他人日报编辑页仍为 403。
"""
import re
import unittest

import test_expense_on_behalf as fixture


class ServiceReportEditNavigationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ExpenseOnBehalfTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.module = self.fixture.app
        self.http = self.fixture.http
        self.reports = self._seed_reports()

    def _seed_reports(self):
        """当前工单建 3 条日报（09-03/09-02/09-01）；另建一个其他工单放 09-04 的日报。"""
        with self.module.app.app_context():
            db = self.module.db()
            reports = []
            for day in ("2026-09-03", "2026-09-02", "2026-09-01"):
                report_id = db.execute(
                    """
                    insert into service_reports
                        (service_order_id, report_date, actual_work_date, total_service_hours,
                         travel_hours, service_description, created_by, created_at, updated_at)
                    values (?, ?, ?, 8, 1, 'edit nav fixture', ?, ?, ?)
                    """,
                    (self.fixture.order, day, day, self.fixture.people["Submitter"],
                     self.module.now(), self.module.now()),
                ).lastrowid
                reports.append((report_id, day))
            self.hidden_order = db.execute(
                """
                insert into service_orders
                    (order_number, client_name, site_address, client_order_number, start_date, created_by, created_at)
                values ('SO-EDIT-NAV-HIDDEN', 'Other Site', 'Elsewhere', 'HIDDEN', '2026-09-01', ?, ?)
                """,
                (self.fixture.people["Submitter"], self.module.now()),
            ).lastrowid
            self.hidden_report = db.execute(
                """
                insert into service_reports
                    (service_order_id, report_date, actual_work_date, total_service_hours,
                     travel_hours, service_description, created_by, created_at, updated_at)
                values (?, '2026-09-04', '2026-09-04', 8, 1, 'hidden fixture', ?, ?, ?)
                """,
                (self.hidden_order, self.fixture.people["Submitter"],
                 self.module.now(), self.module.now()),
            ).lastrowid
            db.commit()
            return reports

    def _nav_block(self, html):
        blocks = re.findall(r'<div class="actions">(.*?)</div>', html, re.S)
        block = next((b for b in blocks if "上一条" in b), "")
        self.assertTrue(block, "编辑页工具栏缺少上一条/下一条导航")
        return block

    def _cells(self, report_id):
        """返回（上一条是否为链接, 下一条是否为链接, 导航块文本）。"""
        response = self.http.get(f"/service-reports/{report_id}/edit")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        html = response.get_data(as_text=True)
        block = self._nav_block(html)
        previous_linked = bool(re.search(r"<a[^>]*>← 上一条</a>", block))
        next_linked = bool(re.search(r"<a[^>]*>下一条 →</a>", block))
        self.assertEqual(block.count("← 上一条"), 1)
        self.assertEqual(block.count("下一条 →"), 1)
        return previous_linked, next_linked, block

    def test_newest_of_order_has_only_next(self):
        """工单内最新一条（09-03）：同工单没有更新的日报 → 上一条置灰。

        注意：其他工单 09-04 的日报全局更新，也不得出现在导航里（与查看页全局导航的差异）。
        """
        self.fixture.login("Submitter")
        newest = self.reports[0][0]
        previous_linked, next_linked, block = self._cells(newest)
        self.assertFalse(previous_linked, "同工单最新一条的「上一条」应置灰")
        self.assertTrue(next_linked, "同工单最新一条的「下一条」应可点")
        self.assertIn("已是同工单最新一条日报", block)
        self.assertIn("disabled", block)
        self.assertNotIn(
            f"/service-reports/{self.hidden_report}/edit",
            block,
            "编辑页导航不得指向其他工单的日报",
        )

    def test_middle_report_has_both(self):
        self.fixture.login("Submitter")
        middle = self.reports[1][0]
        previous_linked, next_linked, _ = self._cells(middle)
        self.assertTrue(previous_linked)
        self.assertTrue(next_linked)

    def test_oldest_of_order_has_only_previous(self):
        self.fixture.login("Submitter")
        oldest = self.reports[2][0]
        previous_linked, next_linked, block = self._cells(oldest)
        self.assertTrue(previous_linked, "同工单最早一条的「上一条」应可点")
        self.assertFalse(next_linked, "同工单最早一条的「下一条」应置灰")
        self.assertIn("已是同工单最早一条日报", block)

    def test_navigation_links_point_to_adjacent_edit_urls(self):
        self.fixture.login("Submitter")
        middle = self.reports[1][0]
        _, _, block = self._cells(middle)
        hrefs = re.findall(r'href="([^"]+)"', block)
        self.assertEqual(
            hrefs,
            [
                f"/service-reports/{self.reports[0][0]}/edit",
                f"/service-reports/{self.reports[2][0]}/edit",
            ],
        )

    def test_same_day_reports_order_by_id(self):
        """同日多条日报：上一条/下一条按 id 衔接，不跳过也不重复。"""
        self.fixture.login("Submitter")
        with self.module.app.app_context():
            db = self.module.db()
            extra = db.execute(
                """
                insert into service_reports
                    (service_order_id, report_date, actual_work_date, total_service_hours,
                     travel_hours, service_description, created_by, created_at, updated_at)
                values (?, '2026-09-02', '2026-09-02', 8, 1, 'same day', ?, ?, ?)
                """,
                (self.fixture.order, self.fixture.people["Submitter"],
                 self.module.now(), self.module.now()),
            ).lastrowid
            db.commit()
        _, _, block = self._cells(extra)
        hrefs = re.findall(r'href="([^"]+)"', block)
        self.assertEqual(
            hrefs,
            [
                f"/service-reports/{self.reports[0][0]}/edit",
                f"/service-reports/{self.reports[1][0]}/edit",
            ],
        )

    def test_external_employee_nav_limited_to_own_reports(self):
        """外部员工：同工单内他人创建的日报不得出现在导航里，直接访问仍 403。"""
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "insert into user_service_orders (user_id, service_order_id, assigned_by, assigned_at) "
                "values (?, ?, ?, ?)",
                (
                    self.fixture.people["External"],
                    self.fixture.order,
                    self.fixture.people["Manager"],
                    self.module.now(),
                ),
            )
            own_report = db.execute(
                """
                insert into service_reports
                    (service_order_id, report_date, actual_work_date, total_service_hours,
                     travel_hours, service_description, created_by, created_at, updated_at)
                values (?, '2026-08-20', '2026-08-20', 8, 1, 'external own', ?, ?, ?)
                """,
                (self.fixture.order, self.fixture.people["External"],
                 self.module.now(), self.module.now()),
            ).lastrowid
            db.commit()
        self.fixture.login("External")

        # 他人的日报编辑页：403（路由守卫，导航必须与该边界一致）。
        response = self.http.get(f"/service-reports/{self.reports[1][0]}/edit")
        self.assertEqual(response.status_code, 403)

        # 自己的日报编辑页：可见，但同工单其他三条都是他人创建 → 双向置灰。
        previous_linked, next_linked, block = self._cells(own_report)
        self.assertFalse(previous_linked, "外部员工不应通过「上一条」跳到他人日报")
        self.assertFalse(next_linked, "外部员工不应通过「下一条」跳到他人日报")
        self.assertNotIn(f"/service-reports/{self.reports[0][0]}/edit", block)
        self.assertNotIn(f"/service-reports/{self.reports[1][0]}/edit", block)
        self.assertNotIn(f"/service-reports/{self.reports[2][0]}/edit", block)

    def test_internal_user_can_reach_adjacent_edit_pages(self):
        """导航链接指向的相邻编辑页真实可开（内部账号 200）。"""
        self.fixture.login("Submitter")
        for report_id, _ in self.reports:
            response = self.http.get(f"/service-reports/{report_id}/edit")
            self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
