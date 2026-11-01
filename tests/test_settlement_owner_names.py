import json
import unittest
from unittest.mock import Mock
from invoice_tool.customer_reimbursements.item_queries import resolve_expense_owner_names


class OwnerNamesTest(unittest.TestCase):
    def row(self, **changes):
        row = dict(worker_name='栾婷', total=481.69, auto_lodging=481.69,
                   standard_hours=0, auto_expense_sources=json.dumps({'lodging':[
                       dict(expense_id=150, worker_name='栾婷', amount=481.69)]}))
        row.update(changes)
        return row

    def connection(self, owners=None):
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = owners if owners is not None else [
            dict(expense_id=150, employee_id=41, name='Assistant')]
        return conn

    def test_transferred_expense_updates_name_and_source_without_changing_money_or_snapshot(self):
        row = self.row()
        result = resolve_expense_owner_names([row], self.connection())[0]
        self.assertEqual(result['worker_name'], 'Assistant')
        self.assertEqual(json.loads(result['auto_expense_sources'])['lodging'][0]['worker_name'], 'Assistant')
        self.assertEqual(result['total'], row['total'])
        self.assertEqual(result['auto_lodging'], row['auto_lodging'])
        self.assertEqual(row['worker_name'], '栾婷')

    def test_labor_manual_and_malformed_rows_are_preserved(self):
        for changes in [dict(standard_hours=1), dict(source_worker_user_id=15),
                        dict(source_report_id=1), dict(miles=2),
                        dict(auto_expense_sources='broken'), dict(auto_expense_sources='[]'),
                        dict(auto_expense_sources='{}')]:
            with self.subTest(changes=changes):
                conn = self.connection()
                row = self.row(**changes)
                self.assertEqual(resolve_expense_owner_names([row], conn), [row])
                conn.execute.assert_not_called()

    def test_missing_or_mixed_owners_keep_snapshot(self):
        row = self.row(auto_expense_sources={'lodging':[
            dict(expense_id=150), dict(expense_id=151)]})
        for owners in [[], [dict(expense_id=150, employee_id=41, name='Assistant')],
                       [dict(expense_id=150, employee_id=41, name='Assistant'),
                        dict(expense_id=151, employee_id=42, name='Other')]]:
            self.assertEqual(resolve_expense_owner_names([row], self.connection(owners))[0]['worker_name'], '栾婷')


if __name__ == '__main__':
    unittest.main()
