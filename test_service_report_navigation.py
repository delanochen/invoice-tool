"""工作日报查看页「上一个 / 下一个」导航的回归测试。

覆盖：
  * 列表顺序为「日期 desc, id desc」时，首/中/尾三态的按钮启用情况；
  * 边界处按钮置灰（禁用）且不渲染链接；
  * 导航链接确实指向相邻日报的查看页；
  * 外部账号不会通过导航跳到无权查看的日报（沿用 service_order_access_filters）。
"""
import re
import unittest

import test_expense_on_behalf as fixture


class ServiceReportNavigationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ExpenseOnBehalfTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.module = self.fixture.app
        self.http = self.fixture.http
        # 查看日报需要 service_reports.view 权限（外部账号尤其如此）。
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "update role_action_permissions set is_enabled = 1 "
                "where resource_key = 'service_reports' and action_key = 'view'"
            )
            db.commit()
        self.reports = self._seed_reports()

    def _seed_reports(self):
        """创建 3 个日报：日期 2026-09-03 / 09-02 / 09-01（列表顺序即创建逆序）。"""
        with self.module.app.app_context():
            db = self.module.db()
            reports = []
            for day in ("2026-09-03", "2026-09-02", "2026-09-01"):
                report_id = db.execute(
                    """
                    insert into service_reports
                        (service_order_id, report_date, actual_work_date, total_service_hours,
                         travel_hours, service_description, created_by, created_at, updated_at)
                    values (?, ?, ?, 8, 1, 'nav fixture', ?, ?, ?)
                    """,
                    (self.fixture.order, day, day, self.fixture.people["Submitter"],
                     self.module.now(), self.module.now()),
                ).lastrowid
                reports.append((report_id, day))
            db.commit()
            return reports

    def _cells(self, report_id):
        """返回（上一个是否为链接, 下一个是否为链接）以及页面文本。"""
        response = self.http.get(f"/service-reports/{report_id}/view")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        html = response.get_data(as_text=True)
        nav = re.search(r'<div class="service-report-nav">(.*?)</div>', html, re.S)
        self.assertIsNotNone(nav, "工具栏缺少 service-report-nav 容器")
        block = nav.group(1)
        previous_linked = bool(re.search(r"<a[^>]*>← 上一个</a>", block))
        next_linked = bool(re.search(r"<a[^>]*>下一个 →</a>", block))
        self.assertEqual(html.count("← 上一个"), 1)
        self.assertEqual(html.count("下一个 →"), 1)
        return previous_linked, next_linked, block

    def test_first_report_has_only_next(self):
        newest = self.reports[0][0]  # 2026-09-03，列表第一条
        previous_linked, next_linked, block = self._cells(newest)
        self.assertFalse(previous_linked, "第一条日报的「上一个」应置灰")
        self.assertTrue(next_linked, "第一条日报的「下一个」应可点")
        self.assertIn("已经是第一个日报", block)
        self.assertIn('disabled', block)

    def test_middle_report_has_both(self):
        middle = self.reports[1][0]  # 2026-09-02
        previous_linked, next_linked, _ = self._cells(middle)
        self.assertTrue(previous_linked)
        self.assertTrue(next_linked)

    def test_last_report_has_only_previous(self):
        oldest = self.reports[2][0]  # 2026-09-01，列表最后一条
        previous_linked, next_linked, block = self._cells(oldest)
        self.assertTrue(previous_linked, "最后一条日报的「上一个」应可点")
        self.assertFalse(next_linked, "最后一条日报的「下一个」应置灰")
        self.assertIn("已经是最后一个日报", block)

    def test_navigation_links_point_to_adjacent_reports(self):
        middle = self.reports[1][0]
        _, _, block = self._cells(middle)
        hrefs = re.findall(r'href="([^"]+)"', block)
        self.assertEqual(
            hrefs,
            [f"/service-reports/{self.reports[0][0]}/view", f"/service-reports/{self.reports[2][0]}/view"],
        )

    def test_same_day_reports_order_by_id(self):
        """同日多条日报：上一个/下一个按 id 衔接，不跳过也不重复。"""
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
            [f"/service-reports/{self.reports[0][0]}/view", f"/service-reports/{self.reports[1][0]}/view"],
        )

    def test_external_user_navigation_skips_invisible_reports(self):
        """外部账号只能看到被授权工单的日报，导航不得跳到无权查看的日报。"""
        with self.module.app.app_context():
            db = self.module.db()
            hidden_order = db.execute(
                """
                insert into service_orders
                    (order_number, client_name, site_address, client_order_number, start_date, created_by, created_at)
                values ('SO-HIDDEN', 'Other Site', 'Elsewhere', 'HIDDEN', '2026-09-01', ?, ?)
                """,
                (self.fixture.people["Submitter"], self.module.now()),
            ).lastrowid
            hidden_report = db.execute(
                """
                insert into service_reports
                    (service_order_id, report_date, actual_work_date, total_service_hours,
                     travel_hours, service_description, created_by, created_at, updated_at)
                values (?, '2026-09-04', '2026-09-04', 8, 1, 'hidden fixture', ?, ?, ?)
                """,
                (hidden_order, self.fixture.people["Submitter"],
                 self.module.now(), self.module.now()),
            ).lastrowid
            db.commit()
        # 内部账号看得到这条 09-04 的日报：它应是 09-03 的「上一个」。
        # 注意：adjacent_service_report_ids 内部走 service_order_access_filters →
        # is_external_manager()，依赖请求上下文里的 g.user，不能在裸 app_context 里直调，
        # 因此统一通过 HTTP 请求断言。
        self.fixture.login("Submitter")
        previous_linked, next_linked, _ = self._cells(self.reports[0][0])
        self.assertTrue(previous_linked, "内部账号应能通过「上一个」看到 09-04 的日报")
        self.assertTrue(next_linked)

        # 外部账号：授权工单里没有这条，导航应停在 09-03（上一个置灰）。
        # 外部员工可见范围 = user_service_orders 授权表，先把它授权的工单打上。
        self.fixture.login("External")
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
            db.commit()
        response = self.http.get(f"/service-reports/{self.reports[0][0]}/view")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        html = response.get_data(as_text=True)
        self.assertNotIn(f"/service-reports/{hidden_report}/view", html, "外部账号不应看到无权日报的导航链接")
        previous_linked, next_linked, _ = self._cells(self.reports[0][0])
        self.assertFalse(previous_linked, "外部账号不应通过「上一个」跳到无权查看的日报")
        self.assertTrue(next_linked)


if __name__ == "__main__":
    unittest.main()
