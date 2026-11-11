"""合并开票：发票行按发票项目归并，金额精确到分。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ''))

from invoice_tool.invoices.merge_items import merge_items_by_project  # noqa: E402


def test_merges_same_project_across_settlements():
    items = [
        {'project_id': 11, 'amount': 1290.0, 'tax_rate': 0},
        {'project_id': 12, 'amount': 468.26, 'tax_rate': 0},
        {'project_id': 11, 'amount': 804.0, 'tax_rate': 0},
        {'project_id': 13, 'amount': 1484.62, 'tax_rate': 0},
        {'project_id': 12, 'amount': 4860.0, 'tax_rate': 0},
    ]
    merged = merge_items_by_project(items)
    assert [row['project_id'] for row in merged] == [11, 12, 13]
    assert merged[0]['amount'] == 2094.0
    assert merged[1]['amount'] == 5328.26
    assert merged[2]['amount'] == 1484.62


def test_sum_is_exact_to_the_cent():
    items = [
        {'project_id': 1, 'amount': 10.10, 'tax_rate': 0},
        {'project_id': 1, 'amount': 20.20, 'tax_rate': 0},
        {'project_id': 2, 'amount': 0.01, 'tax_rate': 0},
        {'project_id': 2, 'amount': 0.02, 'tax_rate': 0},
    ]
    merged = merge_items_by_project(items)
    cents = sum(int(round(row['amount'] * 100)) for row in merged)
    assert cents == 3033, cents
    assert merged[0]['amount'] == 30.30
    assert merged[1]['amount'] == 0.03


def test_empty_input():
    assert merge_items_by_project([]) == []
