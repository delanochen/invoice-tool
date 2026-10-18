"""Literal business catalogs; fresh mutable values for each app load."""

def make_status_labels():
    return {'draft': '保存未提交', 'submitted': '待经理审核', 'returned': '已退回', 'completed': '已完成', 'void': '作废'}

def make_expense_status_labels():
    return {'draft': '保存未提交', 'submitted': '待经理审核', 'returned': '已退回', 'approved': '已通过', 'cancelled': '已作废'}

def make_customer_reimbursement_status_labels():
    return {'draft': '保存未提交', 'submitted': '待经理审核', 'returned': '已退回', 'approved': '已通过', 'superseded': '已被替代'}

def make_contract_status_labels():
    return {'draft': '草稿', 'review': '审核中', 'signed': '已签署', 'active': '执行中', 'completed': '已完成', 'terminated': '已终止'}

def make_service_order_static_map_status_colors():
    return {'overdue': 'red', 'warning': 'orange', 'fresh': 'green', 'none': 'gray'}
