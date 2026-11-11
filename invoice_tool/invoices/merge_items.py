"""合并开票：把多份工单结算的发票行按「发票项目」合并成一行。

每份结算派生出来的行是按项目细分的（MRO Supplies / Fuel / Airfare ...）。多工单合并开票时，
如果直接拼接这些行，同一项目会在发票上重复出现多次；这里按 project_id 归并、金额相加，
保持 Decimal 精确到分，避免 0.01 的浮点漂移。
"""
from decimal import Decimal
from typing import Iterable, List, Dict


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal('0')
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def merge_items_by_project(items: Iterable[Dict]) -> List[Dict]:
    """按 project_id 归并发票行；顺序按项目首次出现的顺序。"""
    grouped: Dict[object, Dict] = {}
    order: List[object] = []
    for item in items:
        project_id = item.get('project_id')
        amount = _to_decimal(item.get('amount'))
        if project_id not in grouped:
            grouped[project_id] = {
                'project_id': project_id,
                'amount': amount,
                'tax_rate': item.get('tax_rate'),
            }
            order.append(project_id)
        else:
            grouped[project_id]['amount'] += amount
    result = []
    for project_id in order:
        row = grouped[project_id]
        result.append({
            'project_id': row['project_id'],
            'amount': float(row['amount'].quantize(Decimal('0.01'))),
            'tax_rate': row['tax_rate'],
        })
    return result
