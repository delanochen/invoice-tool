import unittest
from unittest.mock import patch

from test_expense_on_behalf import ExpenseOnBehalfTest


class AuditLogReportTest(unittest.TestCase):
    """操作日志：去掉 1000 条上限后的分页（200/页）与四维筛选。"""

    def setUp(self):
        self.f = ExpenseOnBehalfTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.m = self.f.app
        self.f.login('Manager')
        with self.m.app.app_context():
            db = self.m.db()
            now = self.m.now()
            # 205 行：Alice 100（invoice/INV-1/alpha）、Bob 100（expense/EXP-9/beta）、Carol 5（invoice/SO-X/gamma）
            rows = []
            for i in range(100):
                rows.append((self.f.people['Manager'], 'Alice', 'update', 'invoice', f'INV-{i}', 'INV-1', 'alpha', now))
            for i in range(100):
                rows.append((self.f.people['Manager'], 'Bob', 'create', 'expense', f'EXP-{i}', 'EXP-9', 'beta', now))
            for i in range(5):
                rows.append((self.f.people['Manager'], 'Carol', 'delete', 'invoice', f'SO-{i}', 'SO-X', 'gamma', now))
            db.executemany(
                """insert into audit_logs
                   (user_id, user_name, action, entity_type, entity_id, entity_label, summary, created_at)
                   values (?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            db.commit()

    def get(self, **args):
        with patch.object(self.m, 'render_template', return_value='ok') as render:
            resp = self.f.http.get('/reports/audit-logs', query_string=args)
        self.assertEqual(resp.status_code, 200)
        return render.call_args.kwargs

    def test_default_page_is_200_with_full_total(self):
        ctx = self.get()
        self.assertEqual(ctx['total'], 205)
        self.assertEqual(ctx['page'], 1)
        self.assertEqual(ctx['total_pages'], 2)
        self.assertEqual(len(ctx['rows']), 200)
        self.assertEqual(ctx['per_page'], 200)

    def test_second_page_returns_remainder(self):
        # 同一 created_at 时按 id desc：页1=最新200条，页2=最早5条（Alice 的 id 1-5）
        first = self.get()
        ctx = self.get(page=2)
        self.assertEqual(len(ctx['rows']), 5)
        self.assertEqual({r['user_name'] for r in ctx['rows']}, {'Alice'})
        first_ids = {r['id'] for r in first['rows']}
        second_ids = {r['id'] for r in ctx['rows']}
        self.assertFalse(first_ids & second_ids)

    def test_oversized_page_clamps_to_last(self):
        ctx = self.get(page=99)
        self.assertEqual(ctx['page'], 2)
        self.assertEqual(len(ctx['rows']), 5)

    def test_operator_filter(self):
        ctx = self.get(operator='Bob')
        self.assertEqual(ctx['total'], 100)
        self.assertTrue(all(r['user_name'] == 'Bob' for r in ctx['rows']))

    def test_entity_type_filter(self):
        ctx = self.get(entity_type='invoice')
        self.assertEqual(ctx['total'], 105)

    def test_object_filter_matches_label(self):
        ctx = self.get(object='INV-1')
        self.assertEqual(ctx['total'], 100)
        self.assertTrue(all(r['entity_label'] == 'INV-1' for r in ctx['rows']))

    def test_content_filter_matches_summary(self):
        ctx = self.get(content='gamma')
        self.assertEqual(ctx['total'], 5)
        self.assertTrue(all(r['summary'] == 'gamma' for r in ctx['rows']))

    def test_combined_filters(self):
        ctx = self.get(entity_type='invoice', content='gamma')
        self.assertEqual(ctx['total'], 5)

    def test_dropdown_options_come_from_data(self):
        ctx = self.get()
        self.assertEqual(set(ctx['operator_options']), {'Alice', 'Bob', 'Carol'})
        self.assertEqual(set(ctx['entity_type_options']), {'invoice', 'expense'})


if __name__ == '__main__':
    unittest.main()
