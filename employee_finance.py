"""PostgreSQL-backed employee payments, treasury, advances and assets.

The module deliberately sits after the existing payroll/expense calculations:
those systems remain the source of truth for amounts, while payment orders are
the accounts-payable document and bank transactions are the cash evidence.
Schema changes live in the numbered PostgreSQL migrations (0285 and later).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from flask import Response, abort, flash, g, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import LongTable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, TableStyle

from invoice_tool.payroll.tax import (
    EXPENSE_CLASSIFICATION_CURRENT,
    EXPENSE_CLASSIFICATION_LEGACY_BACKFILL,
)
from invoice_tool.accounting import EventKey, PostingError, PostingService


PAYMENT_STATUS_LABELS = {
    "draft": "草稿", "pending_review": "待审核", "approved": "已批准",
    "pending_payment": "待付款", "paid": "已付款", "reconciled": "已对账",
    "rejected": "已驳回", "cancelled": "已取消", "payment_failed": "付款失败",
}
PAYMENT_TYPE_LABELS = {
    "salary": "工资", "expense": "员工报销",
}
PAYMENT_METHOD_LABELS = {
    "ach": "ACH", "wire": "电汇", "check": "支票", "cash": "现金", "other": "其他",
}
# v0.1.350：合并发放。审核通过后不再一张一张发，而是把同一员工的多张付款单
# 合成一张支票（付款批次 PB-YYMM-NNNN）一次性发放。
PAYMENT_BATCH_STATUS_LABELS = {
    "issued": "已发放", "reconciled": "已对账", "void": "已作废",
}
BATCH_NUMBER_PREFIX = "PB"
# 能进批次的付款单状态：已批准（approved）直接合并发放时，会先补做借款抵扣
# 再置为已付款，省掉「先进入待付款」这一步。
BATCHABLE_PAYMENT_STATUSES = ("approved", "pending_payment")
ASSET_STATUS_LABELS = {
    "available": "可领用", "assigned": "使用中", "damaged": "损坏",
    "lost": "丢失", "retired": "已报废",
}
ASSET_EVENT_LABELS = {
    "created": "建档", "assign": "领用", "return": "归还", "damaged": "损坏",
    "lost": "丢失", "retired": "报废", "updated": "更新",
}


def _money(value, field="金额") -> Decimal:
    try:
        amount = Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field}格式不正确。")
    return amount


def _csv_value(row, *names, default=""):
    """Read a CSV value using either the English template or Chinese headings."""
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return default


def _unmatch_bank_transaction(api, transaction_id, actor_id):
    """Remove a bank association without changing the underlying paid fact.

    Returns the newly-created payment-order event used as the accounting
    business anchor plus the target identity. The caller owns the transaction.
    """
    tx = api["db"]().execute(
        "select * from bank_transactions where id=?", (transaction_id,)
    ).fetchone()
    if not tx:
        raise ValueError("银行流水不存在。")
    payment_id = tx["matched_payment_order_id"]
    batch_id = tx["matched_batch_id"]
    if bool(payment_id) == bool(batch_id):
        raise ValueError("银行流水没有唯一有效的匹配目标。")
    timestamp = api["now"]()
    api["db"]().execute(
        "update bank_transactions set matched_payment_order_id=null,matched_batch_id=null,"
        "matched_by=null,matched_at=null,sync_status='imported' where id=?",
        (transaction_id,),
    )
    anchor_id = None
    if payment_id:
        payment = _payment_order(api, payment_id)
        if payment["status"] != "reconciled":
            raise ValueError("匹配付款单当前不是已对账状态。")
        api["db"]().execute(
            "update employee_payment_orders set status='paid',reconciled_by=null,"
            "reconciled_at=null,updated_at=? where id=?", (timestamp, payment_id),
        )
        cursor = api["db"]().execute(
            "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,"
            "details,created_by,created_at) values(?,'unmatch','reconciled','paid',?,?,?)",
            (payment_id, f"解除银行流水 #{transaction_id} 匹配", actor_id, timestamp),
        )
        anchor_id = cursor.lastrowid
        target_type, target_id, target_number = "payment", payment_id, payment["payment_number"]
    else:
        batch = _load_batch(api, batch_id)
        if batch["status"] != "reconciled":
            raise ValueError("匹配付款批次当前不是已对账状态。")
        orders = _batch_orders(api, batch_id)
        if not orders:
            raise ValueError("付款批次没有成员，不能解除匹配。")
        api["db"]().execute(
            "update employee_payment_batches set status='issued',reconciled_by=null,"
            "reconciled_at=null,updated_at=? where id=?", (timestamp, batch_id),
        )
        for order in orders:
            if order["status"] != "reconciled":
                raise ValueError("付款批次包含非已对账成员，不能解除匹配。")
            api["db"]().execute(
                "update employee_payment_orders set status='paid',reconciled_by=null,"
                "reconciled_at=null,updated_at=? where id=?", (timestamp, order["id"]),
            )
            cursor = api["db"]().execute(
                "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,"
                "details,created_by,created_at) values(?,'unmatch','reconciled','paid',?,?,?)",
                (order["id"], f"批次 {batch['batch_number']} 解除银行流水 #{transaction_id} 匹配",
                 actor_id, timestamp),
            )
            if anchor_id is None:
                anchor_id = cursor.lastrowid
        target_type, target_id, target_number = "batch", batch_id, batch["batch_number"]
    return {
        "transaction": tx,
        "target_type": target_type,
        "target_id": target_id,
        "target_number": target_number,
        "business_anchor_id": anchor_id,
    }


def _record_bank_accounting_event(
    api, posting, *, enabled, tx, target_type, target_id, business_anchor_id,
    event_type, actor_id, reason_code,
):
    """Record a matched/unmatched audit event without touching the ledger."""
    if not enabled:
        return None
    source_type = f"bank_transaction.{target_type}"
    occurrence = api["db"]().execute(
        "select count(*) from posting_events where source_type=? and source_id=? "
        "and related_source_id=? and event_type=?",
        (source_type, tx["id"], target_id, event_type),
    ).fetchone()[0] + 1
    return posting.post_event(
        key=EventKey(source_type, tx["id"], 1, event_type,
                     occurrence_no=occurrence, related_source_id=target_id),
        business_anchor_type="payment_order_event",
        business_anchor_id=business_anchor_id,
        business_date=tx["transaction_date"],
        accounting_date=date.today().isoformat(),
        requires_gl=False,
        source_snapshot={
            "bank_transaction_id": tx["id"],
            "target_type": target_type,
            "target_id": target_id,
            "amount": str(tx["amount"]),
            "currency": tx["currency"],
        },
        actor_id=actor_id,
        idempotency_key=request.headers.get("Idempotency-Key") or None,
        reason_code=reason_code,
    )


def _csv_rows(upload):
    if not upload or not upload.filename:
        raise ValueError("请选择 CSV 文件。")
    text = io.TextIOWrapper(upload.stream, encoding="utf-8-sig", newline="")
    reader = csv.DictReader(text)
    if not reader.fieldnames:
        raise ValueError("CSV 文件缺少表头。")
    rows = list(reader)
    if not rows:
        raise ValueError("CSV 文件没有可导入的数据。")
    return rows


def payment_amounts(gross_amount, advance_offset=0, other_adjustment=0):
    """Return normalized (gross, offset, adjustment, net) with domain checks."""
    gross = _money(gross_amount, "应付金额")
    offset = _money(advance_offset, "借款抵扣")
    adjustment = _money(other_adjustment, "其他调整")
    if gross < 0 or offset < 0:
        raise ValueError("应付金额和借款抵扣不能为负数。")
    net = gross - offset + adjustment
    if net < 0:
        raise ValueError("实付金额不能为负数。")
    return gross, offset, adjustment, net


def payment_transition_target(status, action):
    transitions = {
        ("draft", "submit"): "pending_review",
        ("pending_review", "approve"): "approved",
        ("pending_review", "reject"): "rejected",
        ("approved", "ready"): "pending_payment",
        ("pending_payment", "mark_paid"): "paid",
        ("pending_payment", "fail"): "payment_failed",
        ("payment_failed", "retry"): "pending_payment",
        ("paid", "reconcile"): "reconciled",
    }
    return transitions.get((status, action))


def _require(api, resource, action="view"):
    if not api["has_action_permission"](resource, action):
        abort(403)


def _can_view_all_payments(api):
    return api["normalized_role"]() in {"admin", "manager", "finance"}


def _require_payment_access(api, payment):
    if not _can_view_all_payments(api) and payment["employee_id"] != g.user["id"]:
        abort(403)


# v0.1.343：员工付款单按业务类型分前缀 —— 工资走 SL-，员工报销走 ER-。
# 以前两类共用 EP- 前缀 + 同一个流水池（工资 EP-2609-0001~0007、报销接着 0008
# 往下排），光看单号分不清是工资还是报销；历史迁移还留下过第三种
# EP-MIG-EXP-<id> 格式。现在与全站其它单据（SO/EX/CT/PP）一致：一类一前缀。
PAYMENT_NUMBER_PREFIXES = {
    "salary": "SL",    # 工资
    "expense": "ER",   # 员工报销
}


def _next_number(api, prefix, table, column):
    api["lock_number_allocation"](api["db"]())
    stamp = date.today().strftime("%y%m")
    root = f"{prefix}-{stamp}-"
    # 取同前缀同月份里**流水号最大**的那条，不能 `order by id desc` ——
    # 存量改号后 id 顺序与流水顺序不再一致，按 id 取会让新号与旧号撞车。
    rows = api["db"]().execute(
        f"select {column} from {table} where {column} like ?", (root + "%",)
    ).fetchall()
    sequence = 1
    for row in rows:
        try:
            value = int(str(row[column]).rsplit("-", 1)[1])
        except (ValueError, IndexError):
            continue
        if value >= sequence:
            sequence = value + 1
    return f"{root}{sequence:04d}"


def _payment_order(api, payment_id):
    row = api["db"]().execute(
        """
        select p.*, u.name employee_name, a.account_name, a.bank_name,
               creator.name creator_name, reviewer.name reviewer_name, payer.name payer_name
        from employee_payment_orders p
        join users u on u.id=p.employee_id
        left join bank_accounts a on a.id=p.bank_account_id
        left join users creator on creator.id=p.created_by
        left join users reviewer on reviewer.id=p.reviewed_by
        left join users payer on payer.id=p.paid_by
        where p.id=?
        """, (payment_id,)
    ).fetchone()
    if not row:
        abort(404)
    return row


def _advance_balances(api, employee_id=None):
    clauses, params = ["1=1"], []
    if employee_id:
        clauses.append("a.employee_id=?")
        params.append(employee_id)
    return api["db"]().execute(
        f"""
        select a.*, u.name employee_name, b.account_name,
               coalesce(sum(l.amount),0) applied_amount,
               a.principal_amount-coalesce(sum(l.amount),0) remaining_amount
        from employee_advances a join users u on u.id=a.employee_id
        left join bank_accounts b on b.id=a.bank_account_id
        left join employee_advance_applications l on l.advance_id=a.id
        where {' and '.join(clauses)}
        group by a.id,u.name,b.account_name
        order by a.advance_date desc,a.id desc
        """, params
    ).fetchall()


def employee_finance_summary(api, employee_id):
    """Small, read-only employee profile summary used by the existing user page."""
    pending = api["db"]().execute(
        "select coalesce(sum(net_amount),0) total,count(*) count from employee_payment_orders "
        "where employee_id=? and status in ('pending_review','approved','pending_payment','payment_failed')",
        (employee_id,),
    ).fetchone()
    paid = api["db"]().execute(
        "select coalesce(sum(net_amount),0) total,count(*) count from employee_payment_orders "
        "where employee_id=? and status in ('paid','reconciled')", (employee_id,),
    ).fetchone()
    advances = _advance_balances(api, employee_id)
    assets = api["db"]().execute(
        "select id,asset_number,name,status from assets where current_holder_id=? and status='assigned' order by asset_number",
        (employee_id,),
    ).fetchall()
    return {
        "pending_total": pending["total"], "pending_count": pending["count"],
        "paid_total": paid["total"], "paid_count": paid["count"],
        "advance_balance": sum(Decimal(str(row["remaining_amount"] or 0)) for row in advances),
        "assets": assets,
    }


def _insert_payment(api, *, employee_id, payment_type, gross_amount, source_type,
                    source_id=None, source_number="", source_key="", description="",
                    other_adjustment=Decimal("0"), advance_id=None, advance_offset=Decimal("0")):
    if payment_type not in PAYMENT_TYPE_LABELS:
        raise ValueError("付款单类型只能是工资或员工报销。")
    gross, offset, adjustment, net = payment_amounts(gross_amount, advance_offset, other_adjustment)
    if offset and not advance_id:
        raise ValueError("填写借款抵扣时必须选择员工借款。")
    if advance_id:
        advance = api["db"]().execute(
            "select employee_id from employee_advances where id=?", (advance_id,)
        ).fetchone()
        if not advance or advance["employee_id"] != employee_id:
            raise ValueError("所选借款不属于该员工。")
    if source_key:
        existing = api["db"]().execute(
            "select id from employee_payment_orders where source_key=?", (source_key,)
        ).fetchone()
        if existing:
            return existing["id"], False
    # v0.1.343：按业务类型选前缀（工资 SL-、员工报销 ER-），各自独立流水池。
    number = _next_number(api, PAYMENT_NUMBER_PREFIXES[payment_type],
                          "employee_payment_orders", "payment_number")
    created_at = api["now"]()
    cursor = api["db"]().execute(
        """
        insert into employee_payment_orders
        (payment_number,employee_id,payment_type,status,currency,gross_amount,advance_offset,
         other_adjustment,net_amount,primary_advance_id,description,source_type,source_id,
         source_number,source_key,sync_source,sync_status,created_by,created_at,updated_at)
        values (?,?,?,'draft','USD',?,?,?,?,?,?,?,?,?,?,'manual','local',?,?,?)
        """,
        (number, employee_id, payment_type, gross, offset, adjustment, net, advance_id,
         description, source_type, source_id, source_number, source_key or None,
         g.user["id"], created_at, created_at),
    )
    payment_id = cursor.lastrowid
    api["db"]().execute(
        "insert into payment_order_sources(payment_order_id,source_type,source_id,source_number,amount,created_at) values(?,?,?,?,?,?)",
        (payment_id, source_type, source_id, source_number, gross, created_at),
    )
    api["db"]().execute(
        "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at) values(?,?,'','draft',?,?,?)",
        (payment_id, "created", description, g.user["id"], created_at),
    )
    api["log_action"]("create", "employee_payment", payment_id, number, f"{PAYMENT_TYPE_LABELS[payment_type]} {net}")
    return payment_id, True


def expense_payment_components(api, expense, payment_order_id,
                               classification_mode=EXPENSE_CLASSIFICATION_CURRENT):
    """Phase 3A/3D：按 expense_items 构建 ER 组件快照（构建期硬校验）。

    分类依据全部是结构化字段：item 专属凭证（expense_item_key=line_key）、
    业务用途（见 expense_purpose_ok 的口径选择）、工单关联、金额有效性、
    按 expense_date 查 worker_tax_status_history。
    禁止按项目名称猜税类（is_lodging_project_name 等不参与判定）。

    classification_mode（Phase 3D，必须显式）：
    - "current"：3D 上线后的新数据，业务用途只认 business_purpose；
    - "legacy_backfill"：历史 ER 回填专用兼容口径（business_purpose OR
      description OR reviewed_by），只用于解释已冻结的历史 snapshot。
    """
    from invoice_tool.payroll.tax import build_expense_payment_components
    from invoice_tool.payroll.tax import expense_purpose_ok

    db = api["db"]()
    items = db.execute(
        "select i.id, i.line_key, i.amount, coalesce(p.name, i.project) as project_name"
        " from expense_items i left join projects p on p.id = i.project_id"
        " where i.expense_id = ? order by i.sort_order, i.id",
        (expense["id"],),
    ).fetchall()
    if not items:
        raise ValueError("报销单 %s 没有明细行，无法生成税务组件。" % expense["expense_number"])
    # Phase 3D：口径由调用方显式决定（current=只认 business_purpose；
    # legacy_backfill=历史兼容，含 description/reviewed_by）。
    purpose_ok = expense_purpose_ok(expense, classification_mode)
    rows = []
    for item in items:
        amount = _money(item["amount"])
        receipt_ok = bool(db.execute(
            "select 1 from expense_attachments where expense_id = ? and expense_item_key = ? limit 1",
            (expense["id"], item["line_key"]),
        ).fetchone())
        rows.append({
            "amount": amount,
            "component_name": item["project_name"],
            "service_date": expense["expense_date"],
            "work_order_id": expense["service_order_id"],
            "source_id": item["id"],
            "amount_ok": amount > 0,
            "receipt_ok": receipt_ok,
            "purpose_ok": purpose_ok,
            "work_order_ok": bool(expense["service_order_id"]),
        })
    return build_expense_payment_components(
        rows,
        employee_id=int(expense["employee_id"]),
        gross_amount=expense["amount"],
        payment_order_id=payment_order_id,
        # app.globals() 暴露的是 build_payroll_services 的工厂闭包，先调用取 lookup。
        tax_status_lookup=api["worker_tax_status_lookup"](),
    )


def _write_expense_components(api, payment_id, expense,
                              classification_mode=EXPENSE_CLASSIFICATION_CURRENT):
    """ER 组件快照与三项税务合计与付款单同事务落库（失败整体回滚）。

    幂等：已有生效快照（superseded_at is null）直接跳过，绝不重复插入；
    被重开取代的旧快照只置 superseded_at，原始内容永不 UPDATE/DELETE。
    """
    db = api["db"]()
    live = db.execute(
        "select count(*) from employee_payment_components"
        " where payment_order_id = ? and superseded_at is null",
        (payment_id,),
    ).fetchone()[0]
    if live:
        return
    components, totals = expense_payment_components(
        api, expense, payment_id, classification_mode=classification_mode)
    gross = _money(expense["amount"])
    line_sum = sum((line["amount"] for line in components), Decimal("0"))
    if line_sum != gross or sum(totals.values()) != gross:
        raise ValueError(
            "报销组件合计 %s / 税务合计 %s 与付款单金额 %s 不一致，已中止生成。"
            % (line_sum, sum(totals.values()), gross)
        )
    for line in components:
        db.execute(
            """
            insert into employee_payment_components
            (payment_order_id,employee_id,component_code,component_name,amount,
             quantity,unit,unit_rate,service_date,work_order_id,source_type,source_id,
             daily_report_id,tax_category,tax_status_snapshot,substantiated,review_status,created_at)
            values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (payment_id, line["employee_id"], line["component_code"], line["component_name"],
             line["amount"], line["quantity"], line["unit"], line["unit_rate"],
             line["service_date"], line["work_order_id"], line["source_type"],
             line["source_id"], line["daily_report_id"], line["tax_category"],
             line["tax_status_snapshot"], line["substantiated"], line["review_status"],
             api["now"]()),
        )
    db.execute(
        "update employee_payment_orders set taxable_compensation_total=?,"
        "accountable_reimbursement_total=?,tax_review_required_total=? where id=?",
        (totals["taxable_compensation"], totals["accountable_reimbursement"],
         totals["tax_review_required"], payment_id),
    )


def ensure_expense_payment_order(api, expense_id):
    """Idempotently bridge an already-approved expense into accounts payable."""
    expense = api["db"]().execute(
        "select *,coalesce(beneficiary_id,created_by) employee_id from expenses where id=?", (expense_id,)
    ).fetchone()
    if not expense or expense["status"] != "approved":
        return None
    existing = api["db"]().execute(
        "select id,status,payment_number from employee_payment_orders where source_key=?",
        (f"expense:{expense_id}",),
    ).fetchone()
    if existing:
        if existing["status"] in {"cancelled", "rejected"}:
            reopened_at = api["now"]()
            api["db"]().execute(
                """update employee_payment_orders
                   set employee_id=?,status='draft',gross_amount=?,advance_offset=0,
                       other_adjustment=0,net_amount=?,primary_advance_id=null,
                       bank_account_id=null,payment_method=null,external_transaction_id=null,
                       reviewed_by=null,reviewed_at=null,paid_by=null,paid_at=null,
                       description=?,source_number=?,updated_at=?
                   where id=?""",
                (expense["employee_id"], expense["amount"], expense["amount"],
                 f"报销单 {expense['expense_number']}", expense["expense_number"],
                 reopened_at, existing["id"]),
            )
            api["db"]().execute(
                "update payment_order_sources set source_number=?,amount=? where payment_order_id=? and source_type='expense' and source_id=?",
                (expense["expense_number"], expense["amount"], existing["id"], expense_id),
            )
            api["db"]().execute(
                "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at) values(?,'reopen',?,'draft','报销重新审核通过',?,?)",
                (existing["id"], existing["status"], g.user["id"], reopened_at),
            )
            api["log_action"](
                "reopen", "employee_payment", existing["id"], existing["payment_number"],
                "报销重新审核通过，付款单恢复为草稿",
            )
            # Phase 3A：重开复用同一 ER。旧组件快照是不可变历史——只置
            # superseded_at 退役（原始内容永不改写），再按当前 expense_items
            # 生成新的生效快照；若金额/明细未变，新快照内容与旧快照一致。
            api["db"]().execute(
                "update employee_payment_components set superseded_at = ?"
                " where payment_order_id = ? and superseded_at is null",
                (reopened_at, existing["id"]),
            )
            _write_expense_components(api, existing["id"], expense)
        return existing["id"]
    payment_id, _ = _insert_payment(
        api, employee_id=expense["employee_id"], payment_type="expense",
        gross_amount=expense["amount"], source_type="expense", source_id=expense_id,
        source_number=expense["expense_number"], source_key=f"expense:{expense_id}",
        description=f"报销单 {expense['expense_number']}",
    )
    _write_expense_components(api, payment_id, expense)
    return payment_id


def cancel_expense_payment_order(api, expense_id, reason="报销审核流程已重置"):
    """Cancel an unpaid expense payment and reverse any posted advance offset."""
    payment = api["db"]().execute(
        "select * from employee_payment_orders where source_key=?",
        (f"expense:{expense_id}",),
    ).fetchone()
    if not payment:
        return None
    if payment["status"] in {"paid", "reconciled"}:
        raise ValueError("该报销付款单已经发放，不能重置审核流程。")
    if payment["status"] == "cancelled":
        return payment["id"]
    _reverse_advance_application(api, payment)
    changed_at = api["now"]()
    api["db"]().execute(
        "update employee_payment_orders set status='cancelled',updated_at=? where id=?",
        (changed_at, payment["id"]),
    )
    api["db"]().execute(
        "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at) values(?,'cancel',?,'cancelled',?,?,?)",
        (payment["id"], payment["status"], reason, g.user["id"], changed_at),
    )
    api["log_action"](
        "cancel", "employee_payment", payment["id"], payment["payment_number"], reason,
    )
    return payment["id"]


def _create_advance_application(api, payment):
    if not payment["primary_advance_id"] or not Decimal(str(payment["advance_offset"] or 0)):
        return
    api["db"]().execute("begin immediate")
    exists = api["db"]().execute(
        "select id from employee_advance_applications where payment_order_id=? and entry_type='application'",
        (payment["id"],),
    ).fetchone()
    if exists:
        return
    balances = {row["id"]: Decimal(str(row["remaining_amount"])) for row in _advance_balances(api, payment["employee_id"])}
    amount = Decimal(str(payment["advance_offset"]))
    if balances.get(payment["primary_advance_id"], Decimal("0")) < amount:
        raise ValueError("借款未结余额不足以完成本次抵扣。")
    api["db"]().execute(
        "insert into employee_advance_applications(advance_id,payment_order_id,entry_type,amount,notes,created_by,created_at) values(?,?,'application',?,?,?,?)",
        (payment["primary_advance_id"], payment["id"], amount, f"付款单 {payment['payment_number']} 抵扣", g.user["id"], api["now"]()),
    )


def _reverse_advance_application(api, payment):
    applied = api["db"]().execute(
        "select coalesce(sum(amount),0) amount from employee_advance_applications where payment_order_id=?",
        (payment["id"],),
    ).fetchone()["amount"]
    applied = Decimal(str(applied or 0))
    if applied:
        api["db"]().execute(
            "insert into employee_advance_applications(advance_id,payment_order_id,entry_type,amount,notes,created_by,created_at) values(?,?,'reversal',?,?,?,?)",
            (payment["primary_advance_id"], payment["id"], -applied, f"付款单 {payment['payment_number']} 取消冲销", g.user["id"], api["now"]()),
        )


def _settle_expense_payout(api, payment, extra=""):
    """付款单发放后回写来源报销单（单张发放与批次发放共用同一条回写口径）。

    extra 会追加到通知正文末尾（批次发放时用来说明是哪张支票），不传则与
    原来的单张发放文案完全一致。
    """
    if payment["source_type"] != "expense" or not payment["source_id"]:
        return None
    expense = api["db"]().execute("select * from expenses where id=?", (payment["source_id"],)).fetchone()
    api["db"]().execute(
        "update expenses set payout_status='paid',reimbursed_by=?,reimbursed_at=?,updated_at=? where id=?",
        (g.user["id"], api["now"](), api["now"](), payment["source_id"]),
    )
    if expense and "notify_expense_participants" in api:
        note = f"报销 {expense['expense_number']} 已通过付款单 {payment['payment_number']} 发放。"
        api["notify_expense_participants"](
            expense, "报销付款已发放", f"{note}{extra}",
            url_for("employee_payment_detail", payment_id=payment["id"]),
        )
    if expense:
        api["log_action"]("pay", "expense", expense["id"], expense["expense_number"],
                          f"通过付款单 {payment['payment_number']} 发放")
    return expense


def _release_expense_payout(api, payment):
    """批次作废：来源报销单退回未付款（与报销审核重置同一口径）。"""
    if payment["source_type"] != "expense" or not payment["source_id"]:
        return
    api["db"]().execute(
        "update expenses set payout_status='pending',reimbursed_by=null,reimbursed_at=null,updated_at=? where id=?",
        (api["now"](), payment["source_id"]),
    )


def _load_batch(api, batch_id):
    """读取批次并按员工校验可见性（员工本人只能看自己的批次）。"""
    batch = api["db"]().execute(
        """select b.*, u.name employee_name, a.account_name
           from employee_payment_batches b
           join users u on u.id=b.employee_id
           left join bank_accounts a on a.id=b.bank_account_id
           where b.id=?""", (batch_id,)
    ).fetchone()
    if not batch:
        abort(404)
    _require_payment_access(api, batch)
    return batch


def _batch_orders(api, batch_id):
    return api["db"]().execute(
        """select p.*,u.name employee_name from employee_payment_orders p
           join users u on u.id=p.employee_id where p.batch_id=? order by p.id""", (batch_id,)
    ).fetchall()


# ---------------------------------------------------------------------------
# Phase 5A：Payment Statement（read model / presentation layer）。
#
# Statement 对应一次实际员工付款（一个 payment batch），金额全部来自现有冻结
# 数据（批次 / 付款单 / 组件快照 / 复核链 / 报销明细），绝不重新计算工资或
# 报销，也绝不写库。HTML / 以后的 PDF / Email 必须复用这一份 payload，
# 不允许各写一套业务查询。
# ---------------------------------------------------------------------------

STATEMENT_TAX_CATEGORIES = ("taxable_compensation", "accountable_reimbursement", "tax_review_required")


def payment_statement_payload(api, batch_id):
    """构建一份 Payment Statement payload（只读，不改任何业务表）。

    闭合规则（不静默补差，交由页面显式报 data integrity error）：
    - 批次闭合：sum(批次内付款单 net_amount) == batch.total_amount，
      且付款单张数 == batch.payment_count；
    - 组件闭合：有快照的付款单 component sum == order.gross_amount；
      历史无快照的特殊单据不伪造组件，标 component detail unavailable。
    """
    batch = _load_batch(api, batch_id)
    orders = _batch_orders(api, batch_id)
    employee = api["db"]().execute(
        "select email from users where id=?", (batch["employee_id"],)
    ).fetchone()

    def dec(value):
        return Decimal(str(value))

    salary_orders, expense_orders = [], []
    original_totals = {key: Decimal("0") for key in STATEMENT_TAX_CATEGORIES}
    effective_totals = {key: Decimal("0") for key in STATEMENT_TAX_CATEGORIES}
    review_pending = {"count": 0, "amount": Decimal("0")}

    for order in orders:
        tax = api["payment_tax_components"](order["id"])
        rows = tax["rows"]
        has_components = bool(rows)
        component_sum = sum((dec(row["amount"]) for row in rows), Decimal("0"))
        gross = dec(order["gross_amount"])
        # 组件闭合：有快照必须逐分相等；无快照不伪造，只显示付款单金额。
        component_closure_ok = (not has_components) or component_sum == gross

        # 税务分类汇总按「有效分类」聚合（原始列保留审计意义）。
        for row in rows:
            amount = dec(row["amount"])
            original = row["tax_category"]
            effective = row["effective_tax_category"]
            if original in original_totals:
                original_totals[original] += amount
            if effective in effective_totals:
                effective_totals[effective] += amount
            if effective == "tax_review_required":
                review_pending["count"] += 1
                review_pending["amount"] += amount

        entry = {
            "id": order["id"],
            "payment_number": order["payment_number"],
            "payment_type": order["payment_type"],
            "status": order["status"],
            "description": order["description"],
            "gross_amount": gross,
            "advance_offset": dec(order["advance_offset"]),
            "net_amount": dec(order["net_amount"]),
            "paid_at": order["paid_at"],
            "source_type": order["source_type"],
            "source_id": order["source_id"],
            # SL：source_number = 「period_start~period_end」；不可靠时留空。
            "pay_period": order["source_number"] if (
                order["payment_type"] == "salary" and order["source_number"]
                and "~" in order["source_number"]) else "",
            "has_components": has_components,
            "components": rows,
            "component_sum": component_sum,
            "component_closure_ok": component_closure_ok,
            "original_totals": tax["original_totals"],
            "effective_totals": tax["effective_totals"],
            "expense": None,
            "expense_item_rows": [],
        }

        # SL：按 component_code 汇总小计（绝不用 component_name 猜归类）。
        if order["payment_type"] == "salary":
            aggregated = {}
            for row in rows:
                key = row["component_code"]
                bucket = aggregated.setdefault(key, {
                    "component_code": key,
                    "component_name": row["component_name"] or key,
                    "amount": Decimal("0"),
                    "quantity": Decimal("0"),
                    "has_quantity": False,
                    "unit": row["unit"] or "",
                })
                bucket["amount"] += dec(row["amount"])
                if row["quantity"] is not None:
                    bucket["quantity"] += dec(row["quantity"])
                    bucket["has_quantity"] = True
            entry["component_subtotals"] = [
                aggregated[key] for key in sorted(aggregated, key=lambda k: (-aggregated[k]["amount"], k))
            ]
            salary_orders.append(entry)

        # ER：来源报销单 + 逐 item（缺来源/缺 item 不伪造，留空展示）。
        if order["payment_type"] == "expense":
            if order["source_id"]:
                expense = api["db"]().execute(
                    """select e.expense_number, e.expense_date, e.project,
                              so.order_number
                       from expenses e
                       left join service_orders so on so.id=e.service_order_id
                       where e.id=?""", (order["source_id"],)
                ).fetchone()
                if expense:
                    entry["expense"] = {
                        "id": order["source_id"],
                        "expense_number": expense["expense_number"],
                        "expense_date": expense["expense_date"],
                        "project": expense["project"],
                        "order_number": expense["order_number"],
                    }
                    entry["expense_item_rows"] = [
                        dict(item) for item in api["db"]().execute(
                            """select project, description, amount from expense_items
                               where expense_id=? order by sort_order, id""",
                            (order["source_id"],),
                        ).fetchall()
                    ]
            expense_orders.append(entry)

    orders_net = sum((dec(order["net_amount"]) for order in orders), Decimal("0"))
    batch_total = dec(batch["total_amount"])
    # 批次闭合：金额与张数都对得上才算闭合，差一分也必须显式报错。
    batch_closure_ok = (
        orders_net == batch_total and len(orders) == int(batch["payment_count"])
    )

    return {
        "statement_number": batch["batch_number"],  # 第一版直接用 batch_number，不建新序列
        "batch": {
            "id": batch["id"],
            "batch_number": batch["batch_number"],
            "employee_id": batch["employee_id"],
            "employee_name": batch["employee_name"],
            "employee_email": (employee["email"] or "").strip() if employee else "",
            "payment_method": batch["payment_method"],
            "check_number": batch["check_number"],
            "status": batch["status"],
            "currency": batch["currency"],
            "account_name": batch["account_name"],
            "issued_at": batch["issued_at"],
            "reconciled_at": batch["reconciled_at"],
            "voided_at": batch["voided_at"],
            "notes": batch["notes"],
            "total_amount": batch_total,
            "payment_count": int(batch["payment_count"]),
        },
        "salary_orders": salary_orders,
        "expense_orders": expense_orders,
        "salary_total": sum((entry["net_amount"] for entry in salary_orders), Decimal("0")),
        "expense_total": sum((entry["net_amount"] for entry in expense_orders), Decimal("0")),
        "orders_net": orders_net,
        "original_totals": original_totals,
        "effective_totals": effective_totals,
        "review_pending": review_pending,
        "batch_closure_ok": batch_closure_ok,
    }


# ---------------------------------------------------------------------------
# Phase 5B：Payment Statement PDF（presentation layer）。
#
# 分层铁律：Business = payment_statement_payload()，Presentation =
# render_payment_statement_pdf(payload)。PDF 绝不再查库、绝不重算任何金额，
# HTML / PDF / 以后的 Email 附件共享同一份 payload。下载路由零写入。
# ---------------------------------------------------------------------------

STATEMENT_PDF_TAX_LABELS = {
    "taxable_compensation": "Compensation (taxable)",
    "accountable_reimbursement": "Reimbursement (accountable)",
    "tax_review_required": "Tax review required",
}

_STATEMENT_FILENAME_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def payment_statement_pdf_filename(batch_number, employee_name=""):
    """Payment_Statement_{batch}_{employee}.pdf；非法字符换 '-'，Unicode 保留。

    secure_filename 会把中文整个剥掉，所以这里用与工单结算导出相同的白名单外
    字符替换策略；员工名清洗后为空时回退 batch 号，保证文件名稳定可下载。
    """
    parts = ["Payment_Statement"]
    for value in (batch_number, employee_name):
        cleaned = _STATEMENT_FILENAME_UNSAFE.sub("-", str(value or "")).strip(" .")
        if cleaned:
            parts.append(cleaned)
    return "_".join(parts) + ".pdf"


def _pdf_money(value, currency="USD"):
    symbols = {"USD": "$", "CNY": "¥", "EUR": "€", "GBP": "£", "JPY": "¥"}
    amount = Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    symbol = symbols.get(currency, f"{currency} ")
    return f"{symbol}{amount:,.2f}"


def _pdf_datetime(value):
    text = "" if value is None else str(value)
    if not text:
        return "—"
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text


def render_payment_statement_pdf(payload, *, company_name="", generated_at=""):
    """由 payload 渲染 Payment Statement PDF（Letter、黑白友好、BytesIO）。

    只做 payload → presentation 转换：
    - 金额/分类/闭合结论一律取 payload，本函数不查库、不算账；
    - 批次不闭合仍允许生成，但顶部 DATA INTEGRITY WARNING 显式列出差额；
    - review_pending > 0 显示 Review Pending 横幅，但不让生成失败；
    - 组件/报销明细缺失与 HTML 同口径容错，不伪造；
    - 不出现 Taxes / Deductions / Net Pay 等传统 paystub 栏位。
    """
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    batch = payload["batch"]
    currency = batch.get("currency") or "USD"

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Payment Statement {payload['statement_number']}",
        author=company_name or "Prasinos Power",
    )

    title_style = ParagraphStyle(
        "StatementPdfTitle", fontName="STSong-Light", fontSize=16, leading=20,
        alignment=TA_CENTER, textColor=colors.HexColor("#10233F"),
    )
    section_style = ParagraphStyle(
        "StatementPdfSection", fontName="STSong-Light", fontSize=11, leading=14,
        alignment=TA_LEFT, textColor=colors.HexColor("#10233F"), spaceBefore=6,
    )
    order_style = ParagraphStyle(
        "StatementPdfOrder", fontName="STSong-Light", fontSize=9, leading=12,
        alignment=TA_LEFT, textColor=colors.black,
    )
    meta_style = ParagraphStyle(
        "StatementPdfMeta", fontName="STSong-Light", fontSize=9, leading=12,
        alignment=TA_LEFT, textColor=colors.black,
    )
    note_style = ParagraphStyle(
        "StatementPdfNote", fontName="STSong-Light", fontSize=7.5, leading=10,
        alignment=TA_LEFT, textColor=colors.HexColor("#475467"),
    )
    warn_style = ParagraphStyle(
        "StatementPdfWarn", fontName="STSong-Light", fontSize=8.5, leading=11,
        alignment=TA_LEFT, textColor=colors.HexColor("#B42318"),
    )
    head_style = ParagraphStyle(
        "StatementPdfHead", fontName="STSong-Light", fontSize=7.5, leading=9,
        alignment=TA_LEFT, textColor=colors.black,
    )
    cell_style = ParagraphStyle(
        "StatementPdfCell", fontName="STSong-Light", fontSize=7.5, leading=9,
        alignment=TA_LEFT, textColor=colors.black,
    )
    money_style = ParagraphStyle(
        "StatementPdfMoney", parent=cell_style, alignment=TA_RIGHT,
    )

    def para(text, style):
        return Paragraph("" if text is None else str(text), style)

    def banner(text, warn=False):
        style = warn_style if warn else note_style
        table = LongTable(
            [[para(text, style)]],
            colWidths=[document.width],
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1),
             colors.HexColor("#FEF3F2") if warn else colors.HexColor("#FFFAEB")),
            ("BOX", (0, 0), (-1, -1), 0.6,
             colors.HexColor("#B42318") if warn else colors.HexColor("#DC6803")),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return table

    def grid_table(header, rows, col_widths, *, repeat_header=True):
        data = [[para(text, head_style) for text in header]]
        data.extend(rows)
        table = LongTable(
            data, colWidths=col_widths, repeatRows=1 if repeat_header else 0,
            hAlign="LEFT",
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E7EBF0")),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#98A2B3")),
            ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#475467")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        return table

    # ---- 抬头：标题 + 元数据 ------------------------------------------------
    story = [
        para(company_name or "Payment Statement", ParagraphStyle(
            "StatementPdfCompany", parent=title_style, fontSize=11, leading=14)),
        para("Payment Statement", title_style),
        Spacer(1, 4 * mm),
    ]
    order_count = len(payload["salary_orders"]) + len(payload["expense_orders"])
    meta_rows = [
        ("Statement No.", payload["statement_number"]),
        ("Batch", batch["batch_number"]),
        ("Employee", batch["employee_name"]),
        ("Payment Status", PAYMENT_BATCH_STATUS_LABELS.get(batch["status"], batch["status"])),
        ("Issued Date", _pdf_datetime(batch["issued_at"])),
        ("Payment Method", PAYMENT_METHOD_LABELS.get(batch["payment_method"], batch["payment_method"])),
        ("Check Number", batch["check_number"] or "—"),
    ]
    if batch["reconciled_at"]:
        meta_rows.append(("Reconciled Date", _pdf_datetime(batch["reconciled_at"])))
    if batch["voided_at"]:
        meta_rows.append(("Voided Date", _pdf_datetime(batch["voided_at"])))
    meta_table = LongTable(
        [[para(label, meta_style), para(value, meta_style)] for label, value in meta_rows],
        colWidths=[document.width * 0.3, document.width * 0.7], hAlign="LEFT",
    )
    meta_table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 3 * mm))

    # ---- 顶部横幅：闭合失败 / review pending（都不阻止生成）-----------------
    if not payload["batch_closure_ok"]:
        story.append(banner(
            "DATA INTEGRITY WARNING — Payments in batch total "
            f"{_pdf_money(payload['orders_net'], currency)} across {order_count} payment order(s) "
            f"does not match the batch recorded total {_pdf_money(batch['total_amount'], currency)} "
            f"({batch['payment_count']} expected). This statement is for reference only; "
            "amounts have NOT been adjusted.", warn=True))
        story.append(Spacer(1, 2 * mm))
    if payload["review_pending"]["amount"] > 0:
        story.append(banner(
            "Tax Classification Review Pending — "
            f"{payload['review_pending']['count']} component(s) totaling "
            f"{_pdf_money(payload['review_pending']['amount'], currency)} in this batch are "
            "awaiting tax classification review. Classification is subject to the final review result."))
        story.append(Spacer(1, 2 * mm))

    total_table = LongTable(
        [[para("Total Payment Amount", ParagraphStyle(
            "StatementPdfTotalLabel", parent=meta_style, fontSize=10)),
          para(_pdf_money(batch["total_amount"], currency), ParagraphStyle(
              "StatementPdfTotalValue", parent=money_style, fontSize=10))]],
        colWidths=[document.width * 0.7, document.width * 0.3], hAlign="LEFT",
    )
    total_table.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.8, colors.HexColor("#475467")),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(total_table)
    story.append(Spacer(1, 4 * mm))

    # ---- 工资 / 报酬 --------------------------------------------------------
    if payload["salary_orders"]:
        story.append(para("Earnings / Compensation", section_style))
        story.append(Spacer(1, 1.5 * mm))
        component_widths = [
            document.width * w for w in
            (0.13, 0.13, 0.30, 0.09, 0.08, 0.12, 0.15)
        ]
        for order in payload["salary_orders"]:
            head_parts = [f"<b>{order['payment_number']}</b>"]
            if order["pay_period"]:
                head_parts.append(f"Pay Period: {order['pay_period']}")
            head_parts.append(
                "Status: " + PAYMENT_STATUS_LABELS.get(order["status"], order["status"]))
            head_parts.append(f"Order Amount: {_pdf_money(order['net_amount'], currency)}")
            heading = para("　·　".join(head_parts), order_style)
            if not order["has_components"]:
                block = [
                    heading,
                    para("Component detail unavailable — this payment order has no component "
                         "snapshot; only the payment order amount is shown.", note_style),
                ]
            elif not order["component_closure_ok"]:
                block = [
                    heading,
                    banner(
                        f"Data integrity error — {order['payment_number']} component sum "
                        f"{_pdf_money(order['component_sum'], currency)} does not equal the "
                        f"order amount {_pdf_money(order['gross_amount'], currency)}.", warn=True),
                ]
            else:
                rows = []
                for row in order["components"]:
                    component_cell = [
                        para(row["component_name"] or row["component_code"], cell_style),
                        para(row["component_code"], ParagraphStyle(
                            "StatementPdfCode", parent=note_style, fontSize=6.5, leading=8)),
                    ]
                    effective = row["effective_tax_category"]
                    original = row["tax_category"]
                    if effective != original:
                        component_cell.append(para(
                            f"{STATEMENT_PDF_TAX_LABELS.get(original, original)} → "
                            f"{STATEMENT_PDF_TAX_LABELS.get(effective, effective)}",
                            ParagraphStyle(
                                "StatementPdfReclass", parent=note_style, fontSize=6.5, leading=8)))
                    quantity = (
                        f"{row['quantity']:g}" if row["quantity"] is not None else "—")
                    rate = (
                        _pdf_money(row["unit_rate"], currency)
                        if row["unit_rate"] is not None else "—")
                    rows.append([
                        para(row["service_date"] or "—", cell_style),
                        para(row["order_number"] or "—", cell_style),
                        component_cell,
                        para(quantity, cell_style),
                        para(row["unit"] or "", cell_style),
                        para(rate, money_style),
                        para(_pdf_money(row["amount"], currency), money_style),
                    ])
                rows.append([
                    para(f"<b>{order['payment_number']} Subtotal</b>", cell_style),
                    para("", cell_style), para("", cell_style), para("", cell_style),
                    para("", cell_style), para("", cell_style),
                    para(f"<b>{_pdf_money(order['net_amount'], currency)}</b>", money_style),
                ])
                block = [heading, Spacer(1, 1 * mm),
                         grid_table(
                             ["Service Date", "Work Order", "Component", "Qty", "Unit",
                              "Rate", "Amount"],
                             rows, component_widths)]
            story.append(KeepTogether(block))
            story.append(Spacer(1, 2.5 * mm))

    # ---- 报销 ---------------------------------------------------------------
    if payload["expense_orders"]:
        story.append(para("Expense Reimbursements", section_style))
        story.append(Spacer(1, 1.5 * mm))
        item_widths = [
            document.width * w for w in (0.28, 0.47, 0.25)
        ]
        for order in payload["expense_orders"]:
            expense = order["expense"]
            head_parts = [f"<b>{order['payment_number']}</b>"]
            if expense:
                head_parts.append(f"Expense: {expense['expense_number']}")
                if expense["expense_date"]:
                    head_parts.append(f"Date: {expense['expense_date']}")
                if expense["order_number"]:
                    head_parts.append(f"Work Order: {expense['order_number']}")
            else:
                head_parts.append("Source expense unavailable")
            head_parts.append(
                "Status: " + PAYMENT_STATUS_LABELS.get(order["status"], order["status"]))
            head_parts.append(f"Order Amount: {_pdf_money(order['net_amount'], currency)}")
            heading = para("　·　".join(head_parts), order_style)
            if expense and order["expense_item_rows"]:
                rows = [
                    [
                        para(item["project"] or "—", cell_style),
                        para(item["description"] or "—", cell_style),
                        para(_pdf_money(item["amount"], currency), money_style),
                    ]
                    for item in order["expense_item_rows"]
                ]
                rows.append([
                    para(f"<b>{order['payment_number']} Subtotal</b>", cell_style),
                    para("", cell_style),
                    para(f"<b>{_pdf_money(order['net_amount'], currency)}</b>", money_style),
                ])
                block = [heading, Spacer(1, 1 * mm),
                         grid_table(["Project", "Description", "Amount"], rows, item_widths)]
            elif expense:
                block = [
                    heading,
                    para("Expense item detail unavailable — only the payment order amount "
                         "is shown.", note_style),
                ]
            else:
                block = [
                    heading,
                    para("Source expense unavailable — only the payment order amount is "
                         "shown.", note_style),
                ]
            story.append(KeepTogether(block))
            story.append(Spacer(1, 2.5 * mm))

    # ---- 税务分类汇总 -------------------------------------------------------
    story.append(para("Tax Classification Summary", section_style))
    story.append(Spacer(1, 1.5 * mm))
    summary_rows = [
        ("Compensation", "taxable_compensation"),
        ("Reimbursements", "accountable_reimbursement"),
        ("Tax Review Required", "tax_review_required"),
    ]
    rows = [
        [
            para(label, cell_style),
            para(_pdf_money(payload["original_totals"][key], currency), money_style),
            para(_pdf_money(payload["effective_totals"][key], currency), money_style),
        ]
        for label, key in summary_rows
    ]
    story.append(grid_table(
        ["Classification", "Original (snapshot)", "Effective (after review)"],
        rows, [document.width * 0.4, document.width * 0.3, document.width * 0.3]))
    story.append(Spacer(1, 1.5 * mm))
    story.append(para(
        "Tax classifications reflect the current effective classification and may include "
        "later tax review decisions.", note_style))
    story.append(Spacer(1, 4 * mm))

    # ---- 付款汇总 -----------------------------------------------------------
    story.append(para("Payment Summary", section_style))
    story.append(Spacer(1, 1.5 * mm))
    summary_payment_rows = [
        [para("Salary / Compensation subtotal", cell_style),
         para(_pdf_money(payload["salary_total"], currency), money_style)],
        [para("Expense Reimbursement subtotal", cell_style),
         para(_pdf_money(payload["expense_total"], currency), money_style)],
        [para("<b>Total Payment Amount</b>", cell_style),
         para(f"<b>{_pdf_money(payload['orders_net'], currency)}</b>", money_style)],
    ]
    if not payload["batch_closure_ok"]:
        summary_payment_rows.append(
            [para("Batch recorded total", cell_style),
             para(_pdf_money(batch["total_amount"], currency), money_style)])
    summary_table = LongTable(
        summary_payment_rows,
        colWidths=[document.width * 0.7, document.width * 0.3], hAlign="LEFT",
    )
    summary_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#98A2B3")),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#475467")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E7EBF0")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(summary_table)

    def draw_footer(page_canvas, doc):
        page_canvas.saveState()
        page_canvas.setFont("STSong-Light", 7)
        page_canvas.setFillColor(colors.HexColor("#667085"))
        footer_parts = [f"Statement {payload['statement_number']}", f"Page {doc.page}"]
        if generated_at:
            footer_parts.append(f"Generated: {generated_at}")
        page_canvas.drawRightString(
            letter[0] - 16 * mm, 9 * mm, "　·　".join(footer_parts))
        page_canvas.restoreState()

    document.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    buffer.seek(0)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Phase 5C：Payment Statement Email + Delivery Log。
#
# 唯一发送链路（Email 层绝不自己查付款明细 / 重算 Statement / 走 HTTP）：
#   batch_id → payment_statement_payload() → render_payment_statement_pdf()
#   → payment_statement_pdf_filename() → users.email → send_email()
#   → record_email_delivery()
# 发送与付款事务完全解耦：SMTP 失败记 failed delivery 后原样抛出，
# 绝不影响批次 / 付款单 / 组件 / 往来账 / 对账。
# ---------------------------------------------------------------------------

STATEMENT_EMAIL_ENTITY_TYPE = "payment_statement"
# 与客户报销邮件同一收件人校验口径（app.py deliver_customer_reimbursement_email）。
STATEMENT_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def statement_email_can_send(api):
    """Email Statement 发送权限由权限管理配置（employee_payments.email_statement）。"""
    return api["has_action_permission"]("employee_payments", "email_statement")


def send_payment_statement_email(api, batch_id):
    """发送 Payment Statement 邮件，返回 recipient。

    业务阻断（void / 不闭合 / 缺有效 email）抛 ValueError：不调用 SMTP、
    不记 delivery。SMTP 失败：捕获后记 failed delivery（独立 commit）再抛出。
    """
    batch = _load_batch(api, batch_id)
    if batch["status"] == "void":
        raise ValueError("This payment batch is void and cannot be emailed.")
    payload = payment_statement_payload(api, batch_id)
    if not payload["batch_closure_ok"]:
        raise ValueError(
            "Data integrity error: batch does not close; the statement cannot be emailed.")
    recipient = (payload["batch"]["employee_email"] or "").strip()
    if not STATEMENT_EMAIL_RE.fullmatch(recipient):
        raise ValueError("Employee email not available.")

    subject = f"Payment Statement - {payload['statement_number']}"
    company_profile = api.get("get_company_profile")
    company_name = company_profile()["name"] if company_profile else ""
    currency = payload["batch"].get("currency") or "USD"
    pdf_bytes = render_payment_statement_pdf(
        payload, company_name=company_name, generated_at=api["now"]())
    attachments = [{
        "filename": payment_statement_pdf_filename(
            payload["statement_number"], payload["batch"]["employee_name"]),
        "content": pdf_bytes,
        "maintype": "application",
        "subtype": "pdf",
    }]
    html = api["render_template"](
        "email_payment_statement.html",
        employee_name=payload["batch"]["employee_name"],
        batch_number=payload["statement_number"],
        issued_at=payload["batch"]["issued_at"],
        total_amount=_pdf_money(payload["batch"]["total_amount"], currency),
        payment_method=PAYMENT_METHOD_LABELS.get(
            payload["batch"]["payment_method"], payload["batch"]["payment_method"]),
        check_number=payload["batch"]["check_number"] or "",
        review_pending=payload["review_pending"]["amount"] > 0,
        company_name=company_name,
    )
    try:
        api["send_email"](to=recipient, subject=subject, html=html, attachments=attachments)
    except Exception as error:
        api["record_email_delivery"](
            STATEMENT_EMAIL_ENTITY_TYPE, batch_id, recipient, subject,
            status="failed", error_message=str(error), employee_id=batch["employee_id"])
        api["db"]().commit()
        raise
    api["record_email_delivery"](
        STATEMENT_EMAIL_ENTITY_TYPE, batch_id, recipient, subject,
        status="sent", employee_id=batch["employee_id"])
    api["db"]().commit()
    api["log_action"](
        "email", STATEMENT_EMAIL_ENTITY_TYPE, batch_id, payload["statement_number"],
        f"发送至：{recipient}；附件：{len(attachments)} 个")
    return recipient


def statement_email_history(api, batch_id, limit=10):
    """Email History：最近 N 条，新到旧。0294 未生效时页面必须照常可用。"""
    try:
        return api["db"]().execute(
            f"""select id, recipient, subject, status, error_message, sent_by_name, sent_at
                from email_delivery_logs
                where entity_type=? and entity_id=?
                order by id desc limit {int(limit)}""",
            (STATEMENT_EMAIL_ENTITY_TYPE, batch_id),
        ).fetchall()
    except Exception:
        return []


def statement_email_last_sent(api, batch_id):
    """最近一次成功投递（Send Again 的 Last sent 提示）。0294 未生效时为 None。"""
    try:
        return api["db"]().execute(
            """select recipient, sent_by_name, sent_at
               from email_delivery_logs
               where entity_type=? and entity_id=? and status='sent'
               order by id desc limit 1""",
            (STATEMENT_EMAIL_ENTITY_TYPE, batch_id),
        ).fetchone()
    except Exception:
        return None


def _is_day(value):
    """YYYY-MM-DD 校验（v0.1.360 付款单日期筛选）：非法输入直接忽略，不报错。"""
    try:
        date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return False
    return True


def register_employee_finance_routes(app, api):
    app.jinja_env.globals["employee_finance_summary"] = lambda employee_id: employee_finance_summary(api, employee_id)

    @app.get("/employee-payments")
    @api["login_required"]
    def employee_payments():
        _require(api, "employee_payments")
        status = request.args.get("status", "")
        employee_id = request.args.get("employee_id", "")
        # v0.1.360：付款单此前只有状态 / 员工两个筛选，页脚「应付合计 / 实付合计」
        # 因此是**全库累计**，拿它跟任何期间报表相减必然对不上。这里补上创建日期
        # 区间（与列表「创建时间」列同口径）。注意付款单创建日期 ≠ 工资的
        # service_date / 报销的 expense_date —— 想跟利润表对齐要看后者。
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        if date_from and not _is_day(date_from):
            date_from = ""
        if date_to and not _is_day(date_to):
            date_to = ""
        if date_from and date_to and date_to < date_from:
            date_from, date_to = date_to, date_from
        clauses, params = ["1=1"], []
        can_view_all = _can_view_all_payments(api)
        if not can_view_all:
            clauses.append("p.employee_id=?")
            params.append(g.user["id"])
        if status in PAYMENT_STATUS_LABELS:
            clauses.append("p.status=?"); params.append(status)
        if date_from:
            clauses.append("date(p.created_at)>=?"); params.append(date_from)
        if date_to:
            clauses.append("date(p.created_at)<=?"); params.append(date_to)
        if can_view_all and employee_id.isdigit():
            clauses.append("p.employee_id=?"); params.append(int(employee_id))
        elif not can_view_all:
            employee_id = str(g.user["id"])
        rows = api["db"]().execute(
            f"""select p.*,u.name employee_name,b.account_name from employee_payment_orders p
            join users u on u.id=p.employee_id left join bank_accounts b on b.id=p.bank_account_id
            where {' and '.join(clauses)} order by p.created_at desc,p.id desc""", params
        ).fetchall()
        employees = (
            api["db"]().execute("select id,name from users where role in ('employee','manager','finance','admin') order by name").fetchall()
            if can_view_all else api["db"]().execute("select id,name from users where id=?", (g.user["id"],)).fetchall()
        )
        accounts = api["db"]().execute("select * from bank_accounts where is_active=1 order by account_name").fetchall() if can_view_all else []
        advances = _advance_balances(api, int(employee_id) if employee_id.isdigit() else None) if can_view_all else []
        agreements = api["db"]().execute(
            "select s.*,u.name employee_name from employee_salary_agreements s join users u on u.id=s.employee_id order by s.is_active desc,s.effective_from desc"
        ).fetchall() if can_view_all else []
        return render_template("employee_payments.html", rows=rows, employees=employees, accounts=accounts,
                               advances=advances, agreements=agreements, status=status, employee_id=employee_id,
                               date_from=date_from, date_to=date_to,
                               status_labels=PAYMENT_STATUS_LABELS, type_labels=PAYMENT_TYPE_LABELS,
                               method_labels=PAYMENT_METHOD_LABELS, can_view_all=can_view_all)

    @app.post("/employee-payments/create")
    @api["login_required"]
    def create_employee_payment():
        _require(api, "employee_payments", "create")
        try:
            employee_id = int(request.form.get("employee_id", ""))
            payment_type = request.form.get("payment_type", "salary")
            if payment_type != "salary":
                raise ValueError("员工报销付款单只能由报销审核通过后自动生成。")
            payment_id, _ = _insert_payment(
                api, employee_id=employee_id, payment_type=payment_type,
                gross_amount=request.form.get("gross_amount"), source_type="manual",
                description=request.form.get("description", "").strip(),
                other_adjustment=request.form.get("other_adjustment", "0"),
                advance_id=int(request.form["advance_id"]) if request.form.get("advance_id", "").isdigit() else None,
                advance_offset=request.form.get("advance_offset", "0"),
            )
            api["db"]().commit()
            flash("员工付款单已创建。", "success")
            return redirect(url_for("employee_payment_detail", payment_id=payment_id))
        except (ValueError, TypeError) as error:
            api["db"]().rollback(); flash(str(error), "error")
            return redirect(url_for("employee_payments"))

    @app.post("/employee-payments/salary-agreements")
    @api["login_required"]
    def create_salary_agreement():
        _require(api, "employee_payments", "create")
        try:
            employee_id = int(request.form.get("employee_id", ""))
            amount = _money(request.form.get("amount"), "约定工资")
            effective_from = date.fromisoformat(request.form.get("effective_from", "")).isoformat()
            effective_to = request.form.get("effective_to", "").strip() or None
            if amount < 0: raise ValueError("约定工资不能为负数。")
            if effective_to and effective_to < effective_from: raise ValueError("结束日期不能早于生效日期。")
            overlap = api["db"]().execute(
                "select id from employee_salary_agreements where employee_id=? and is_active=1 and effective_from<=? and (effective_to is null or effective_to>=?) limit 1",
                (employee_id, effective_to or "9999-12-31", effective_from),
            ).fetchone()
            if overlap: raise ValueError("该员工已有生效期重叠的线下工资约定。")
            api["db"]().execute(
                "insert into employee_salary_agreements(employee_id,amount,currency,effective_from,effective_to,replaces_system_payroll,notes,is_active,created_by,created_at,updated_at) values(?,?,'USD',?,?,?, ?,1,?,?,?)",
                (employee_id, amount, effective_from, effective_to,
                 1 if request.form.get("replaces_system_payroll", "1") == "1" else 0,
                 request.form.get("notes", "").strip(), g.user["id"], api["now"](), api["now"]()),
            )
            api["log_action"]("create", "employee_salary_agreement", employee_id, str(employee_id), f"约定工资 {amount}")
            api["db"]().commit(); flash("线下约定工资已保存。", "success")
        except (ValueError, TypeError) as error:
            api["db"]().rollback(); flash(str(error), "error")
        return redirect(url_for("employee_payments"))

    @app.post("/employee-payments/generate-payroll")
    @api["login_required"]
    def generate_payroll_payments():
        _require(api, "employee_payments", "create")
        try:
            period_start = date.fromisoformat(request.form.get("period_start", ""))
            batch = api["payroll_rows_for_period"](period_start)
            agreements = api["db"]().execute(
                "select * from employee_salary_agreements where is_active=1 and effective_from<=? and (effective_to is null or effective_to>=?)",
                (batch["period_end"].isoformat(), period_start.isoformat()),
            ).fetchall()
            replacements = {row["employee_id"]: row for row in agreements if row["replaces_system_payroll"]}
            created = 0

            def _write_components(payment_id, employee_id, components, totals, gross_amount):
                """组件快照与三项税务合计与付款单同事务落库（失败整体回滚）。

                写入前做最后一道 sum(components) == gross 硬校验（ROUND_HALF_UP），
                任何不一致都拒绝写快照并回滚整个生成操作。

                Decision 8：带 `correction_pre_existing` 标记的行代表
                carry-forward 的冻结组件（例如 09-29 已经存在于旧 SL）。它们继续
                参与上面的 sum == gross 闭合校验，但**不重复 INSERT**，而是通过
                allocation 行把已有 frozenc 组件纳进这张 canonical SL 的 payable。
                """
                gross = _money(gross_amount)
                line_sum = sum((line["amount"] for line in components), Decimal("0"))
                if line_sum != gross or sum(totals.values()) != gross:
                    raise ValueError(
                        "工资组件合计 %s / 税务合计 %s 与付款单金额 %s 不一致，已中止生成。"
                        % (line_sum, sum(totals.values()), gross)
                    )
                db = api["db"]()
                for line in components:
                    if line.get("correction_pre_existing"):
                        continue
                    db.execute(
                        """
                        insert into employee_payment_components
                        (payment_order_id,employee_id,component_code,component_name,amount,
                         quantity,unit,unit_rate,service_date,work_order_id,source_type,source_id,
                         daily_report_id,tax_category,tax_status_snapshot,substantiated,review_status,created_at)
                        values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (payment_id, employee_id, line["component_code"], line["component_name"],
                         line["amount"], line["quantity"], line["unit"], line["unit_rate"],
                         line["service_date"], line["work_order_id"], line["source_type"],
                         line["source_id"], line["daily_report_id"], line["tax_category"],
                         line["tax_status_snapshot"], line["substantiated"], line["review_status"],
                         api["now"]()),
                    )
                db.execute(
                    "update employee_payment_orders set taxable_compensation_total=?,"
                    "accountable_reimbursement_total=?,tax_review_required_total=? where id=?",
                    (totals["taxable_compensation"], totals["accountable_reimbursement"],
                     totals["tax_review_required"], payment_id),
                )

            for row in batch["rows"]:
                agreement = replacements.get(row["worker_id"])
                amount = agreement["amount"] if agreement else row["total_pay"]
                source_kind = "offline_salary" if agreement else "system_payroll"
                # Phase 2A：生成前先算组件并做 sum==gross 硬校验（失败抛 ValueError
                # → 下方 except 回滚整个生成操作），绝不写不完整快照、不静默补差。
                if source_kind == "system_payroll":
                    components, totals = api["payroll_payment_components"](
                        period_start, row["worker_id"], amount)
                else:
                    components, totals = api["offline_salary_payment_components"](
                        row["worker_id"], amount, batch["period_end"])
                # Decision 8：这一段里已经存在 carry-forward 冻结组件的（例如未闭合
                # P7 的 09-29），必须当作 already existing —— 不重造第二份 component，
                # 生成成功后再把它们 allocate 到这张新 SL。判定在统一读层里，这里不重复实现。
                # （生产由 app.py _install_payroll_correction_services 恒安装；api.get 容忍
                #   最小 stub 的单元测试按旧语义跑。调用时解析，不在 register 时缓存。）
                flag_carry_forward = api.get("flag_carry_forward_components")
                carry_forward = (
                    flag_carry_forward(
                        row["worker_id"], components,
                        period_start.isoformat(), batch["period_end"].isoformat())
                    if flag_carry_forward else []
                )
                payment_id, inserted = _insert_payment(
                    api, employee_id=row["worker_id"], payment_type="salary", gross_amount=amount,
                    source_type=source_kind, source_id=agreement["id"] if agreement else None,
                    source_number=f"{period_start.isoformat()}~{batch['period_end'].isoformat()}",
                    source_key=f"payroll:{source_kind}:{row['worker_id']}:{period_start.isoformat()}",
                    description=f"{period_start.isoformat()} 至 {batch['period_end'].isoformat()} {'线下约定工资' if agreement else '系统工资'}",
                )
                if inserted:
                    _write_components(payment_id, row["worker_id"], components, totals, amount)
                    if carry_forward:
                        api["attach_carry_forward"](
                            payment_id, [item["component_id"] for item in carry_forward],
                            reason=f"carry forward into canonical period {period_start.isoformat()}")
                created += int(inserted)
            # Offline-only employees may have no service-report row in this period.
            worker_ids = {row["worker_id"] for row in batch["rows"]}
            for agreement in agreements:
                if agreement["employee_id"] in worker_ids and agreement["replaces_system_payroll"]:
                    continue
                payment_id, inserted = _insert_payment(
                    api, employee_id=agreement["employee_id"], payment_type="salary",
                    gross_amount=agreement["amount"], source_type="offline_salary", source_id=agreement["id"],
                    source_number=f"{period_start.isoformat()}~{batch['period_end'].isoformat()}",
                    source_key=f"payroll:offline_salary:{agreement['employee_id']}:{period_start.isoformat()}",
                    description=f"{period_start.isoformat()} 至 {batch['period_end'].isoformat()} 线下约定工资",
                )
                if inserted:
                    components, totals = api["offline_salary_payment_components"](
                        agreement["employee_id"], agreement["amount"], batch["period_end"])
                    _write_components(payment_id, agreement["employee_id"], components, totals, agreement["amount"])
                created += int(inserted)
            api["db"]().commit(); flash(f"已生成 {created} 张付款单；重复来源已自动跳过。", "success")
        except (ValueError, TypeError) as error:
            api["db"]().rollback(); flash(str(error), "error")
        return redirect(url_for("employee_payments"))

    @app.get("/employee-payments/<int:payment_id>")
    @api["login_required"]
    def employee_payment_detail(payment_id):
        _require(api, "employee_payments")
        payment = _payment_order(api, payment_id)
        _require_payment_access(api, payment)
        sources = api["db"]().execute("select * from payment_order_sources where payment_order_id=? order by id", (payment_id,)).fetchall()
        events = api["db"]().execute(
            "select e.*,u.name creator_name from payment_order_events e left join users u on u.id=e.created_by where e.payment_order_id=? order by e.id",
            (payment_id,),
        ).fetchall()
        applications = api["db"]().execute(
            "select l.*,a.advance_number from employee_advance_applications l join employee_advances a on a.id=l.advance_id where l.payment_order_id=? order by l.id",
            (payment_id,),
        ).fetchall()
        accounts = api["db"]().execute("select * from bank_accounts where is_active=1 order by account_name").fetchall()
        # 合并发放出来的付款单要能一眼看到是哪张支票（批次）。
        batch = (
            api["db"]().execute("select * from employee_payment_batches where id=?", (payment["batch_id"],)).fetchone()
            if payment["batch_id"] else None
        )
        # Phase 4A：Effective Tax Summary。只读 —— 组件快照永远是生成当时的冻结值，
        # 页面上的「有效分类」按最新复核动态算出，不写回任何表。
        from invoice_tool.payroll.routes import TAX_CATEGORY_LABELS
        tax_components = (
            api["payment_tax_components"](payment_id)
            if api["has_action_permission"]("tax_review", "view") else None
        )
        return render_template("employee_payment_detail.html", payment=payment, sources=sources, events=events,
                               applications=applications, accounts=accounts, batch=batch,
                               tax_components=tax_components,
                               tax_category_labels=TAX_CATEGORY_LABELS,
                               status_labels=PAYMENT_STATUS_LABELS,
                               type_labels=PAYMENT_TYPE_LABELS, method_labels=PAYMENT_METHOD_LABELS,
                               correction=(
                                   api["correction_banner"](payment)
                                   if api.get("correction_banner") else None),
                               correction_status_labels=api.get("correction_status_labels", {}))

    @app.post("/employee-payments/<int:payment_id>/transition")
    @api["login_required"]
    def transition_employee_payment(payment_id):
        payment = _payment_order(api, payment_id)
        _require_payment_access(api, payment)
        action = request.form.get("action", "")
        transitions = {
            "submit": ("draft", "pending_review", "edit"),
            "approve": ("pending_review", "approved", "approve"),
            "ready": ("approved", "pending_payment", "pay"),
            "retry": ("payment_failed", "pending_payment", "pay"),
            "mark_paid": ("pending_payment", "paid", "pay"),
            "fail": ("pending_payment", "payment_failed", "pay"),
            "reject": ("pending_review", "rejected", "approve"),
        }
        try:
            if action == "cancel":
                if payment["status"] in {"paid", "reconciled", "cancelled"}:
                    raise ValueError("已付款、已对账或已取消的付款单不能取消。")
                _require(api, "employee_payments", "edit")
                target = "cancelled"
                _reverse_advance_application(api, payment)
            else:
                source, target, permission = transitions.get(action, (None, None, None))
                if not source or payment["status"] != source:
                    raise ValueError("付款单当前状态不允许执行该操作。")
                _require(api, "employee_payments", permission)
                if target == "pending_payment":
                    _create_advance_application(api, payment)
                if target == "paid":
                    account_id = request.form.get("bank_account_id", "")
                    method = request.form.get("payment_method", "")
                    if not account_id.isdigit() or method not in PAYMENT_METHOD_LABELS:
                        raise ValueError("付款时必须选择付款账户和付款方式。")
                    api["db"]().execute(
                        "update employee_payment_orders set bank_account_id=?,payment_method=?,external_transaction_id=?,paid_by=?,paid_at=? where id=?",
                        (int(account_id), method, request.form.get("external_transaction_id", "").strip() or None,
                         g.user["id"], api["now"](), payment_id),
                    )
                    _settle_expense_payout(api, payment)
                if target in {"approved", "rejected"}:
                    api["db"]().execute("update employee_payment_orders set reviewed_by=?,reviewed_at=? where id=?", (g.user["id"], api["now"](), payment_id))
            reason = request.form.get("reason", "").strip()
            api["db"]().execute("update employee_payment_orders set status=?,updated_at=? where id=?", (target, api["now"](), payment_id))
            api["db"]().execute(
                "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at) values(?,?,?,?,?,?,?)",
                (payment_id, action, payment["status"], target, reason, g.user["id"], api["now"]()),
            )
            api["log_action"](action, "employee_payment", payment_id, payment["payment_number"], f"{payment['status']} → {target} {reason}")
            api["db"]().commit(); flash("付款单状态已更新。", "success")
        except ValueError as error:
            api["db"]().rollback(); flash(str(error), "error")
        return redirect(url_for("employee_payment_detail", payment_id=payment_id))

    @app.get("/finance/payment-batches")
    @api["login_required"]
    def payment_batches():
        _require(api, "employee_payments")
        employee_id = request.args.get("employee_id", "")
        status = request.args.get("status", "")
        can_view_all = _can_view_all_payments(api)
        if not can_view_all:
            # 与付款单同一口径：没有「查看全部」权限时只给本人，改 URL 也不放行。
            employee_id = str(g.user["id"])
        employees = (
            api["db"]().execute("select id,name from users where role in ('employee','manager','finance','admin') order by name").fetchall()
            if can_view_all else api["db"]().execute("select id,name from users where id=?", (g.user["id"],)).fetchall()
        )
        clauses, params = ["1=1"], []
        if employee_id.isdigit():
            clauses.append("b.employee_id=?"); params.append(int(employee_id))
        if status in PAYMENT_BATCH_STATUS_LABELS:
            clauses.append("b.status=?"); params.append(status)
        else:
            status = ""
        rows = api["db"]().execute(
            f"""select b.*,u.name employee_name,a.account_name from employee_payment_batches b
            join users u on u.id=b.employee_id left join bank_accounts a on a.id=b.bank_account_id
            where {' and '.join(clauses)} order by b.issued_at desc,b.id desc""", params
        ).fetchall()
        return render_template("payment_batches.html", rows=rows, employees=employees, employee_id=employee_id,
                               status=status, status_labels=PAYMENT_BATCH_STATUS_LABELS,
                               method_labels=PAYMENT_METHOD_LABELS, can_view_all=can_view_all)

    @app.get("/finance/payment-batches/<int:batch_id>")
    @api["login_required"]
    def payment_batch_detail(batch_id):
        _require(api, "employee_payments")
        batch = _load_batch(api, batch_id)
        orders = _batch_orders(api, batch_id)
        return render_template("payment_batch_detail.html", batch=batch, orders=orders,
                               status_labels=PAYMENT_STATUS_LABELS, type_labels=PAYMENT_TYPE_LABELS,
                               method_labels=PAYMENT_METHOD_LABELS,
                               batch_status_labels=PAYMENT_BATCH_STATUS_LABELS)

    @app.get("/finance/payment-batches/<int:batch_id>/statement")
    @api["login_required"]
    def payment_statement(batch_id):
        """Payment Statement：一次实际员工付款的完整付款说明（read model）。

        权限与批次详情同口径：admin/manager/finance 全部可见，员工 self-only
        （_load_batch 内部走 _require_payment_access，改 URL 看别人的直接 403）。
        纯展示：不写批次 / 付款单 / 往来账 / 对账任何一张表。
        """
        _require(api, "employee_payments")
        data = payment_statement_payload(api, batch_id)
        return render_template("payment_statement.html", s=data,
                               status_labels=PAYMENT_STATUS_LABELS,
                               type_labels=PAYMENT_TYPE_LABELS,
                               method_labels=PAYMENT_METHOD_LABELS,
                               batch_status_labels=PAYMENT_BATCH_STATUS_LABELS,
                               can_email=statement_email_can_send(api),
                               email_history=statement_email_history(api, batch_id),
                               last_sent=statement_email_last_sent(api, batch_id))

    @app.get("/finance/payment-batches/<int:batch_id>/statement.pdf")
    @api["login_required"]
    def payment_statement_pdf(batch_id):
        """Payment Statement PDF 下载（Phase 5B）。

        权限与 HTML Statement 完全同口径（_require + payload 内
        _require_payment_access，员工改 URL 看别人的直接 403）。请求时即时生成、
        BytesIO 返回，零写入：不碰批次 / 付款单 / 组件 / 往来账 / 对账，也不记
        下载事件（Delivery Log 属 Phase 5C）。业务数据只来自
        payment_statement_payload()，PDF 不查库、不重算。
        """
        _require(api, "employee_payments")
        data = payment_statement_payload(api, batch_id)
        company_profile = api.get("get_company_profile")
        company_name = company_profile()["name"] if company_profile else ""
        pdf_bytes = render_payment_statement_pdf(
            data, company_name=company_name, generated_at=api["now"]())
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=payment_statement_pdf_filename(
                data["batch"]["batch_number"], data["batch"]["employee_name"]),
        )

    @app.post("/finance/payment-batches/<int:batch_id>/statement/email")
    @api["login_required"]
    def email_payment_statement(batch_id):
        """发送 Payment Statement 邮件（Phase 5C，仅 admin / finance）。

        POST-only（不能用 GET 发送）。发送链路唯一：payload → PDF →
        send_email → record_email_delivery；业务阻断 flash 提示，SMTP 失败
        记 failed delivery 后提示。绝不写批次 / 付款单 / 往来账 / 对账。
        """
        _require(api, "employee_payments")
        if not statement_email_can_send(api):
            abort(403)
        try:
            recipient = send_payment_statement_email(api, batch_id)
        except ValueError as error:
            api["db"]().rollback()
            flash(str(error), "error")
        except Exception as error:
            api["db"]().rollback()  # failed delivery 已在服务层单独 commit
            flash(f"邮件发送失败：{error}", "error")
        else:
            flash(f"Payment Statement 已发送至 {recipient}。", "success")
        return redirect(url_for("payment_statement", batch_id=batch_id))

    @app.post("/finance/payment-batches/create")
    @api["login_required"]
    def create_payment_batch():
        """合并发放：勾选同一员工的多张付款单，合成一张支票。"""
        _require(api, "employee_payments", "pay")
        back = request.referrer or url_for("employee_ledger")
        try:
            raw_ids = [value for value in request.form.getlist("payment_id") if str(value).strip().isdigit()]
            ids = sorted({int(value) for value in raw_ids})
            if not ids:
                raise ValueError("请先勾选要合并发放的付款单。")
            placeholders = ",".join(["?"] * len(ids))
            orders = api["db"]().execute(
                f"""select p.*,u.name employee_name from employee_payment_orders p
                join users u on u.id=p.employee_id where p.id in ({placeholders}) order by p.id""", ids
            ).fetchall()
            if len(orders) != len(ids):
                raise ValueError("部分付款单不存在，请刷新后重试。")
            for order in orders:
                _require_payment_access(api, order)
            blocked = [order["payment_number"] for order in orders
                       if order["status"] not in BATCHABLE_PAYMENT_STATUSES]
            if blocked:
                raise ValueError("只有「已批准」或「待付款」的付款单可以合并发放：" + "、".join(blocked))
            # Decision 8 / 2：已被工资周期纠偏作废（或只做标签纠偏的已付款单）
            # 一律不许再合并发放 —— 这些单里含确认过的重复工资，发第二次就是重复付款。
            # 判定走统一读层，不在页面里重写一遍 correction_status 语义。
            # （生产恒安装；api.get 容忍最小 stub。调用时解析。）
            block_reason_fn = api.get("payment_batch_block_reason")
            correction_blocked = (
                [reason for reason in (block_reason_fn(order) for order in orders) if reason]
                if block_reason_fn else []
            )
            if correction_blocked:
                raise ValueError(correction_blocked[0])
            # 一张支票只有一名收款人 —— 跨员工必须先按人分批，否则支票抬头没法写。
            if len({order["employee_id"] for order in orders}) > 1:
                raise ValueError("一张支票只能开给一名员工，请不要跨员工勾选。")
            if len({order["currency"] for order in orders}) > 1:
                raise ValueError("不同币种的付款单不能合并到同一张支票。")
            # 只拦「还在生效」的批次：支票作废后付款单退回待付款，必须能重新合并发放，
            # 否则那几张单就被作废批次永久占住了。
            live_batches = {row["id"] for row in api["db"]().execute(
                "select id from employee_payment_batches where status<>'void'").fetchall()}
            taken = [order["payment_number"] for order in orders
                     if order["batch_id"] and order["batch_id"] in live_batches]
            if taken:
                raise ValueError("这些付款单已属于其它发放批次：" + "、".join(taken))
            account_id = request.form.get("bank_account_id", "")
            method = request.form.get("payment_method", "")
            if not account_id.isdigit() or method not in PAYMENT_METHOD_LABELS:
                raise ValueError("请选择付款账户和付款方式。")
            account = api["db"]().execute(
                "select * from bank_accounts where id=? and is_active=1", (int(account_id),)
            ).fetchone()
            if not account:
                raise ValueError("请选择有效的付款账户。")
            currency = orders[0]["currency"]
            if account["currency"] != currency:
                raise ValueError(f"付款账户币种（{account['currency']}）与付款单币种（{currency}）不一致。")
            check_number = request.form.get("check_number", "").strip()
            if not check_number:
                raise ValueError("请填写支票号。")
            if api["db"]().execute(
                "select id from employee_payment_batches where bank_account_id=? and check_number=? and status<>'void'",
                (int(account_id), check_number),
            ).fetchone():
                raise ValueError("该账户下的支票号已经用过了。")
            total = sum((Decimal(str(order["net_amount"])) for order in orders), Decimal("0"))
            batch_number = _next_number(api, BATCH_NUMBER_PREFIX, "employee_payment_batches", "batch_number")
            now = api["now"]()
            api["db"]().execute(
                "insert into employee_payment_batches(batch_number,employee_id,currency,bank_account_id,payment_method,"
                " check_number,total_amount,payment_count,status,notes,issued_by,issued_at,created_at,updated_at)"
                " values(?,?,?,?,?,?,?,?,'issued',?,?,?,?,?)",
                (batch_number, orders[0]["employee_id"], currency, int(account_id), method, check_number,
                 total, len(orders), request.form.get("notes", "").strip(), g.user["id"], now, now, now),
            )
            batch_id = api["db"]().execute(
                "select id from employee_payment_batches where batch_number=?", (batch_number,)
            ).fetchone()["id"]
            detail = f"批次 {batch_number} · 支票 {check_number}"
            for order in orders:
                # 从「已批准」直接合并发放时，借款抵扣还没落账（单张流程是点
                # 「进入待付款」时落的），这里补上，否则借款余额不会减少。
                if order["status"] == "approved":
                    _create_advance_application(api, order)
                api["db"]().execute(
                    "update employee_payment_orders set status='paid',batch_id=?,bank_account_id=?,payment_method=?,"
                    "external_transaction_id=?,paid_by=?,paid_at=?,updated_at=? where id=?",
                    (batch_id, int(account_id), method, check_number, g.user["id"], now, now, order["id"]),
                )
                api["db"]().execute(
                    "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at)"
                    " values(?,?,?,?,?,?,?)",
                    (order["id"], "batch_pay", order["status"], "paid", detail, g.user["id"], now),
                )
                _settle_expense_payout(api, order, extra=f"（{detail}）")
                api["log_action"]("batch_pay", "employee_payment", order["id"], order["payment_number"],
                                  f"{order['status']} → paid {detail}")
            api["db"]().commit()
            flash(f"已合并发放 {len(orders)} 张付款单：{batch_number}（支票 {check_number}，合计 {total}）。", "success")
            return redirect(url_for("payment_batch_detail", batch_id=batch_id))
        except (ValueError, TypeError) as error:
            api["db"]().rollback(); flash(str(error), "error")
            return redirect(back)

    @app.post("/finance/payment-batches/<int:batch_id>/void")
    @api["login_required"]
    def void_payment_batch(batch_id):
        """支票作废：批次内付款单退回「待付款」，可重新合并发放。"""
        _require(api, "employee_payments", "pay")
        try:
            batch = _load_batch(api, batch_id)
            if batch["status"] != "issued":
                raise ValueError("只有「已发放」的批次可以作废。")
            orders = _batch_orders(api, batch_id)
            paid_members = [order["payment_number"] for order in orders
                            if order["status"] in {"paid", "reconciled"} or order["paid_at"]]
            if paid_members:
                raise ValueError("批次包含已实付付款单，不能作废；资金回流请走退款：" + "、".join(paid_members))
            reason = request.form.get("reason", "").strip()
            if not reason:
                raise ValueError("请填写作废原因。")
            now = api["now"]()
            for order in orders:
                # 借款抵扣要冲销（发放时落过一笔 application），否则借款余额虚低。
                _reverse_advance_application(api, order)
                _release_expense_payout(api, order)
                api["db"]().execute(
                    "update employee_payment_orders set status='pending_payment',bank_account_id=null,payment_method=null,"
                    "external_transaction_id=null,paid_by=null,paid_at=null,updated_at=? where id=?",
                    (now, order["id"]),
                )
                api["db"]().execute(
                    "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at)"
                    " values(?,?,?,?,?,?,?)",
                    (order["id"], "void", order["status"], "pending_payment",
                     f"批次 {batch['batch_number']} 作废：{reason}", g.user["id"], now),
                )
                api["log_action"]("void", "employee_payment", order["id"], order["payment_number"],
                                  f"批次 {batch['batch_number']} 作废：{reason}")
            api["db"]().execute(
                "update employee_payment_batches set status='void',voided_by=?,voided_at=?,updated_at=? where id=?",
                (g.user["id"], now, now, batch_id),
            )
            api["db"]().commit()
            flash(f"批次 {batch['batch_number']} 已作废，{len(orders)} 张付款单退回待付款。", "success")
        except (ValueError, TypeError) as error:
            api["db"]().rollback(); flash(str(error), "error")
        return redirect(url_for("payment_batch_detail", batch_id=batch_id))

    @app.route("/finance/advances", methods=["GET", "POST"])
    @api["login_required"]
    def employee_advances():
        _require(api, "employee_advances")
        if request.method == "POST":
            _require(api, "employee_advances", "create")
            try:
                employee_id = int(request.form.get("employee_id", ""))
                employee = api["db"]().execute(
                    "select id,name from users where id=? and is_active=1", (employee_id,)
                ).fetchone()
                if not employee: raise ValueError("请选择有效员工。")
                amount = _money(request.form.get("principal_amount"), "借款金额")
                if amount <= 0: raise ValueError("借款金额必须大于零。")
                purpose = request.form.get("purpose", "").strip()
                if not purpose: raise ValueError("借款用途不能为空。")
                account_id = int(request.form["bank_account_id"]) if request.form.get("bank_account_id", "").isdigit() else None
                if account_id and not api["db"]().execute(
                    "select id from bank_accounts where id=? and is_active=1", (account_id,)
                ).fetchone():
                    raise ValueError("请选择有效付款账户。")
                number = _next_number(api, "EA", "employee_advances", "advance_number")
                upload = request.files.get("attachment")
                original, stored = "", ""
                if upload and upload.filename:
                    original = secure_filename(upload.filename)
                    stored = os.urandom(16).hex() + Path(original).suffix.lower()
                    folder = Path(api["DATA_DIR"]) / "employee-advance-attachments"; folder.mkdir(parents=True, exist_ok=True)
                    upload.save(folder / stored)
                cursor = api["db"]().execute(
                    "insert into employee_advances(advance_number,employee_id,principal_amount,currency,advance_date,bank_account_id,purpose,attachment_name,attachment_stored_filename,status,external_transaction_id,sync_source,sync_status,created_by,created_at,updated_at) values(?,?,?,'USD',?,?,?,?,?,'open',?,'manual','local',?,?,?)",
                    (number, employee_id, amount, request.form.get("advance_date") or date.today().isoformat(),
                     account_id, purpose, original, stored,
                     request.form.get("external_transaction_id", "").strip() or None,
                     g.user["id"], api["now"](), api["now"]()),
                )
                advance_id = api["db"]().execute("select id from employee_advances where advance_number=?", (number,)).fetchone()["id"]
                api["log_action"]("create", "employee_advance", advance_id, number, f"借款 {amount}")
                api["db"]().commit(); flash("员工借款已登记。", "success")
            except (ValueError, TypeError) as error:
                api["db"]().rollback(); flash(str(error), "error")
            return redirect(url_for("employee_advances"))
        rows = _advance_balances(api)
        employees = api["db"]().execute("select id,name from users where is_active=1 order by name").fetchall()
        accounts = api["db"]().execute("select * from bank_accounts where is_active=1 order by account_name").fetchall()
        return render_template("employee_advances.html", rows=rows, employees=employees, accounts=accounts)

    @app.get("/finance/advances/<int:advance_id>/attachment")
    @api["login_required"]
    def employee_advance_attachment(advance_id):
        _require(api, "employee_advances")
        advance = api["db"]().execute("select attachment_name,attachment_stored_filename from employee_advances where id=?", (advance_id,)).fetchone()
        if not advance or not advance["attachment_stored_filename"]: abort(404)
        return send_file(Path(api["DATA_DIR"]) / "employee-advance-attachments" / advance["attachment_stored_filename"], download_name=advance["attachment_name"])

    @app.get("/finance")
    @api["login_required"]
    def finance_overview():
        _require(api, "finance_overview")
        payment_summary = api["db"]().execute(
            "select status,count(*) count,coalesce(sum(net_amount),0) total from employee_payment_orders group by status"
        ).fetchall()
        account_summary = api["db"]().execute(
            """select a.*,coalesce(sum(case when t.direction='credit' then t.amount else -t.amount end),0) movement
            from bank_accounts a left join bank_transactions t on t.bank_account_id=a.id group by a.id order by a.account_name"""
        ).fetchall()
        unreconciled = api["db"]().execute("select count(*) count,coalesce(sum(amount),0) total from bank_transactions where matched_payment_order_id is null").fetchone()
        advances = _advance_balances(api)
        return render_template("finance_overview.html", payment_summary=payment_summary, account_summary=account_summary,
                               unreconciled=unreconciled, advance_total=sum(Decimal(str(r["remaining_amount"])) for r in advances),
                               status_labels=PAYMENT_STATUS_LABELS)

    @app.route("/finance/bank-accounts", methods=["GET", "POST"])
    @api["login_required"]
    def bank_accounts():
        _require(api, "bank_accounts")
        if request.method == "POST":
            _require(api, "bank_accounts", "create")
            try:
                action = request.form.get("action", "create")
                if action == "import":
                    created = skipped = 0
                    for row in _csv_rows(request.files.get("csv_file")):
                        name = _csv_value(row, "account_name", "账户名称")
                        if not name: raise ValueError("账户 CSV 中存在空的账户名称。")
                        if api["db"]().execute("select id from bank_accounts where account_name=?", (name,)).fetchone():
                            skipped += 1; continue
                        opening = _money(_csv_value(row, "opening_balance", "期初余额", default="0"), "期初余额")
                        opening_date = _csv_value(row, "opening_balance_date", "期初日期") or None
                        cursor = api["db"]().execute(
                            "insert into bank_accounts(account_name,bank_name,account_type,currency,last_four,opening_balance,opening_balance_date,initialized_at,initialized_by,is_active,external_account_id,sync_source,sync_status,created_by,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,1,?,'csv','imported',?,?,?)",
                            (name, _csv_value(row, "bank_name", "银行"), _csv_value(row, "account_type", "账户类型", default="checking"),
                             _csv_value(row, "currency", "币种", default="USD"), _csv_value(row, "last_four", "尾号"), opening,
                             opening_date, api["now"]() if opening_date else None, g.user["id"] if opening_date else None,
                             _csv_value(row, "external_account_id", "外部账户ID") or None,
                             g.user["id"], api["now"](), api["now"]()),
                        )
                        api["log_action"]("import", "bank_account", cursor.lastrowid, name, "CSV 初始化银行账户")
                        created += 1
                    message = f"银行账户 CSV 导入完成：新增 {created} 个，跳过重复 {skipped} 个。"
                else:
                    name = request.form.get("account_name", "").strip()
                    if not name: raise ValueError("账户名称不能为空。")
                    opening = _money(request.form.get("opening_balance"), "期初余额")
                    opening_date = request.form.get("opening_balance_date", "").strip() or None
                    cursor = api["db"]().execute(
                        "insert into bank_accounts(account_name,bank_name,account_type,currency,last_four,opening_balance,opening_balance_date,initialized_at,initialized_by,is_active,external_account_id,sync_source,sync_status,created_by,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,1,?,'manual','local',?,?,?)",
                        (name, request.form.get("bank_name", "").strip(), request.form.get("account_type", "checking"),
                         request.form.get("currency", "USD"), request.form.get("last_four", "").strip(), opening,
                         opening_date, api["now"]() if opening_date else None, g.user["id"] if opening_date else None,
                         request.form.get("external_account_id", "").strip() or None, g.user["id"], api["now"](), api["now"]()),
                    )
                    api["log_action"]("create", "bank_account", cursor.lastrowid, name, request.form.get("bank_name", "").strip())
                    message = "银行账户已创建。"
                api["db"]().commit(); flash(message, "success")
            except (ValueError, TypeError) as error:
                api["db"]().rollback(); flash(str(error), "error")
            return redirect(url_for("bank_accounts"))
        rows = api["db"]().execute("select * from bank_accounts order by is_active desc,account_name").fetchall()
        return render_template("bank_accounts.html", rows=rows)

    @app.post("/finance/bank-accounts/<int:account_id>/initialize")
    @api["login_required"]
    def initialize_bank_account(account_id):
        _require(api, "bank_accounts", "create")
        try:
            account = api["db"]().execute("select * from bank_accounts where id=?", (account_id,)).fetchone()
            if not account: abort(404)
            count = api["db"]().execute("select count(*) count from bank_transactions where bank_account_id=?", (account_id,)).fetchone()["count"]
            if count: raise ValueError("该账户已有银行流水，不能重设期初余额。")
            opening = _money(request.form.get("opening_balance"), "期初余额")
            opening_date = request.form.get("opening_balance_date", "").strip()
            if not opening_date: raise ValueError("请选择期初日期。")
            api["db"]().execute(
                "update bank_accounts set opening_balance=?,opening_balance_date=?,initialized_at=?,initialized_by=?,updated_at=? where id=?",
                (opening, opening_date, api["now"](), g.user["id"], api["now"](), account_id),
            )
            api["log_action"]("initialize", "bank_account", account_id, account["account_name"], f"期初日期 {opening_date}，期初余额 {opening}")
            api["db"]().commit(); flash("银行账户期初数据已初始化。", "success")
        except (ValueError, TypeError) as error:
            api["db"]().rollback(); flash(str(error), "error")
        return redirect(url_for("bank_accounts"))

    @app.get("/finance/bank-accounts/import-template")
    @api["login_required"]
    def bank_account_import_template():
        _require(api, "bank_accounts", "create")
        content = "account_name,bank_name,account_type,currency,last_four,opening_balance,opening_balance_date,external_account_id\r\n"
        return Response("\ufeff" + content, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=bank-account-import-template.csv"})

    def insert_bank_transaction(account_id, values, source="manual"):
        amount = _money(values.get("amount"), "流水金额")
        if amount <= 0: raise ValueError("流水金额必须大于零。")
        direction = values.get("direction", "debit").strip().lower()
        if direction not in {"debit", "credit"}: raise ValueError("收支方向必须为 debit 或 credit。")
        external_id = (values.get("external_transaction_id") or "").strip() or None
        fingerprint = hashlib.sha256("|".join([
            str(account_id), values.get("transaction_date") or "", direction, str(amount),
            external_id or "", values.get("description") or ""
        ]).encode("utf-8")).hexdigest()
        api["db"]().execute(
            "insert into bank_transactions(bank_account_id,transaction_date,posted_date,direction,amount,currency,description,reference_number,external_transaction_id,sync_source,sync_status,import_fingerprint,created_by,created_at) values(?,?,?,?,?,'USD',?,?,?,?,'imported',?,?,?) on conflict(import_fingerprint) do nothing",
            (account_id, values.get("transaction_date") or date.today().isoformat(), values.get("posted_date") or None,
             direction, amount, values.get("description", "").strip(), values.get("reference_number", "").strip(),
             external_id, source, fingerprint, g.user["id"], api["now"]()),
        )

    @app.route("/finance/bank-transactions", methods=["GET", "POST"])
    @api["login_required"]
    def bank_transactions():
        _require(api, "bank_transactions")
        if request.method == "POST":
            _require(api, "bank_transactions", "create")
            try:
                account_id = int(request.form.get("bank_account_id", ""))
                upload = request.files.get("csv_file")
                if upload and upload.filename:
                    count = 0
                    for row in _csv_rows(upload):
                        insert_bank_transaction(account_id, row, "csv"); count += 1
                    message = f"CSV 已处理 {count} 行；重复流水按指纹跳过。"
                else:
                    insert_bank_transaction(account_id, request.form, "manual"); message = "银行流水已登记。"
                api["log_action"]("import", "bank_transaction", 0, f"账户 {account_id}", message)
                api["db"]().commit(); flash(message, "success")
            except (ValueError, TypeError) as error:
                api["db"]().rollback(); flash(str(error), "error")
            return redirect(url_for("bank_transactions"))
        rows = api["db"]().execute(
            "select t.*,a.account_name,p.payment_number from bank_transactions t join bank_accounts a on a.id=t.bank_account_id left join employee_payment_orders p on p.id=t.matched_payment_order_id order by t.transaction_date desc,t.id desc"
        ).fetchall()
        accounts = api["db"]().execute("select * from bank_accounts where is_active=1 order by account_name").fetchall()
        return render_template("bank_transactions.html", rows=rows, accounts=accounts)

    @app.get("/finance/bank-transactions/import-template")
    @api["login_required"]
    def bank_transaction_import_template():
        _require(api, "bank_transactions", "create")
        content = "transaction_date,posted_date,direction,amount,description,reference_number,external_transaction_id\r\n"
        return Response("\ufeff" + content, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=bank-transaction-import-template.csv"})

    @app.route("/finance/reconciliation", methods=["GET", "POST"])
    @api["login_required"]
    def bank_reconciliation():
        _require(api, "bank_reconciliation")
        if request.method == "POST":
            _require(api, "bank_reconciliation", "reconcile")
            _require(api, "employee_payments", "reconcile")
            posting = PostingService(api["db"]())
            try:
                accounting_enabled = posting.require_posting_ready()
                transaction_id = int(request.form.get("transaction_id", ""))
                tx = api["db"]().execute("select * from bank_transactions where id=?", (transaction_id,)).fetchone()
                if not tx or tx["matched_payment_order_id"] or tx["matched_batch_id"]:
                    raise ValueError("银行流水不存在或已匹配。")
                raw_batch = request.form.get("batch_id", "")
                # 批次级对账：一张支票在流水里是一笔支出，整批核销，
                # 不再要求「一笔流水 = 一张付款单」。
                if str(raw_batch).strip().isdigit():
                    batch = _load_batch(api, int(raw_batch))
                    if batch["status"] != "issued": raise ValueError("只有已发放的付款批次可以对账。")
                    if tx["direction"] != "debit" or tx["bank_account_id"] != batch["bank_account_id"]:
                        raise ValueError("流水必须是付款账户下的支出记录。")
                    if tx["currency"] != batch["currency"]:
                        raise ValueError("流水币种与付款批次不一致。")
                    if Decimal(str(tx["amount"])) != Decimal(str(batch["total_amount"])):
                        raise ValueError("流水金额必须与付款批次合计金额一致。")
                    orders = _batch_orders(api, batch["id"])
                    api["db"]().execute(
                        "update bank_transactions set matched_batch_id=?,matched_by=?,matched_at=?,sync_status='matched' where id=?",
                        (batch["id"], g.user["id"], api["now"](), transaction_id),
                    )
                    business_anchor_id = None
                    for order in orders:
                        api["db"]().execute(
                            "update employee_payment_orders set status='reconciled',reconciled_by=?,reconciled_at=?,updated_at=? where id=?",
                            (g.user["id"], api["now"](), api["now"](), order["id"]),
                        )
                        cursor = api["db"]().execute(
                            "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at) values(?,?,?,?,?,?,?)",
                            (order["id"], "reconcile", order["status"], "reconciled",
                             f"批次 {batch['batch_number']} 匹配银行流水 #{transaction_id}", g.user["id"], api["now"]()),
                        )
                        if business_anchor_id is None:
                            business_anchor_id = cursor.lastrowid
                    api["db"]().execute(
                        "update employee_payment_batches set status='reconciled',reconciled_by=?,reconciled_at=?,updated_at=? where id=?",
                        (g.user["id"], api["now"](), api["now"](), batch["id"]),
                    )
                    _record_bank_accounting_event(
                        api, posting, enabled=accounting_enabled, tx=tx,
                        target_type="batch", target_id=batch["id"],
                        business_anchor_id=business_anchor_id,
                        event_type="bank.matched", actor_id=g.user["id"],
                        reason_code="bank_matched",
                    )
                    api["log_action"]("reconcile", "payment_batch", batch["id"], batch["batch_number"], f"银行流水 #{transaction_id}")
                    api["db"]().commit()
                    flash(f"付款批次与银行流水已完成对账，{len(orders)} 张付款单转为已对账。", "success")
                    return redirect(url_for("bank_reconciliation"))
                payment_id = int(request.form.get("payment_id", ""))
                payment = _payment_order(api, payment_id)
                if payment["status"] != "paid": raise ValueError("只有已付款的付款单可以对账。")
                if tx["direction"] != "debit" or tx["bank_account_id"] != payment["bank_account_id"]:
                    raise ValueError("流水必须是付款账户下的支出记录。")
                if tx["currency"] != payment["currency"]:
                    raise ValueError("流水币种与付款单不一致。")
                if Decimal(str(tx["amount"])) != Decimal(str(payment["net_amount"])):
                    raise ValueError("流水金额必须与付款单实付金额一致。")
                api["db"]().execute("update bank_transactions set matched_payment_order_id=?,matched_by=?,matched_at=?,sync_status='matched' where id=?", (payment_id,g.user["id"],api["now"](),transaction_id))
                api["db"]().execute("update employee_payment_orders set status='reconciled',reconciled_by=?,reconciled_at=?,updated_at=? where id=?", (g.user["id"],api["now"](),api["now"](),payment_id))
                anchor = api["db"]().execute("insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at) values(?,'reconcile','paid','reconciled',?,?,?)", (payment_id,f"匹配银行流水 #{transaction_id}",g.user["id"],api["now"]()))
                _record_bank_accounting_event(
                    api, posting, enabled=accounting_enabled, tx=tx,
                    target_type="payment", target_id=payment_id,
                    business_anchor_id=anchor.lastrowid,
                    event_type="bank.matched", actor_id=g.user["id"],
                    reason_code="bank_matched",
                )
                api["log_action"]("reconcile", "employee_payment", payment_id, payment["payment_number"], f"银行流水 #{transaction_id}")
                api["db"]().commit(); flash("付款单与银行流水已完成对账。", "success")
            except (ValueError, TypeError, PostingError) as error:
                api["db"]().rollback(); flash(str(error), "error")
            return redirect(url_for("bank_reconciliation"))
        transactions = api["db"]().execute("select t.*,a.account_name from bank_transactions t join bank_accounts a on a.id=t.bank_account_id where t.matched_payment_order_id is null and t.matched_batch_id is null and t.direction='debit' order by t.transaction_date desc").fetchall()
        matched_transactions = api["db"]().execute(
            """select t.*,a.account_name,p.payment_number,b.batch_number
               from bank_transactions t join bank_accounts a on a.id=t.bank_account_id
               left join employee_payment_orders p on p.id=t.matched_payment_order_id
               left join employee_payment_batches b on b.id=t.matched_batch_id
               where t.matched_payment_order_id is not null or t.matched_batch_id is not null
               order by t.matched_at desc,t.id desc"""
        ).fetchall()
        payments = api["db"]().execute("select p.*,u.name employee_name from employee_payment_orders p join users u on u.id=p.employee_id where p.status='paid' order by p.paid_at desc").fetchall()
        batches = api["db"]().execute(
            """select b.*,u.name employee_name,a.account_name from employee_payment_batches b
            join users u on u.id=b.employee_id left join bank_accounts a on a.id=b.bank_account_id
            where b.status='issued' order by b.issued_at desc,b.id desc"""
        ).fetchall()
        return render_template("bank_reconciliation.html", transactions=transactions, payments=payments,
                               matched_transactions=matched_transactions, batches=batches,
                               method_labels=PAYMENT_METHOD_LABELS)

    @app.post("/finance/reconciliation/<int:transaction_id>/unmatch")
    @api["login_required"]
    def bank_reconciliation_unmatch(transaction_id):
        _require(api, "bank_reconciliation", "reconcile")
        _require(api, "employee_payments", "reconcile")
        posting = PostingService(api["db"]())
        try:
            accounting_enabled = posting.require_posting_ready()
            result = _unmatch_bank_transaction(api, transaction_id, g.user["id"])
            if accounting_enabled:
                tx = result["transaction"]
                _record_bank_accounting_event(
                    api, posting, enabled=True, tx=tx,
                    target_type=result["target_type"], target_id=result["target_id"],
                    business_anchor_id=result["business_anchor_id"],
                    event_type="bank.unmatched", actor_id=g.user["id"],
                    reason_code="bank_unmatched",
                )
            api["log_action"](
                "unmatch", f"{result['target_type']}_bank_reconciliation",
                result["target_id"], result["target_number"],
                f"解除银行流水 #{transaction_id} 匹配",
            )
            api["db"]().commit()
            flash("银行匹配已解除；付款事实与付款凭证保持不变。", "success")
        except (ValueError, TypeError, PostingError) as error:
            api["db"]().rollback()
            flash(str(error), "error")
        return redirect(url_for("bank_reconciliation"))

    @app.get("/finance/employee-ledger")
    @api["login_required"]
    def employee_ledger():
        _require(api, "employee_ledger")
        # v0.1.344：从「先选员工才出数据」改成 ERP 明细账范式 ——
        # 默认列出全部员工（受 can_view_all_payments 约束），员工 / 类型 / 状态三个筛选
        # 可任意组合；只有员工筛选必须能单独生效，所以员工筛选走服务端（精确匹配），
        # 类型与状态交给表格自带筛选（前端，含排序+列设置）以减少一次整页往返。
        # 员工下拉必须保留「全部」选项：旧版 select 带 required，不给「全部」就没法退回去。
        employee_id = request.args.get("employee_id", "")
        payment_type = request.args.get("payment_type", "")
        status = request.args.get("status", "")
        can_view_all = _can_view_all_payments(api)
        if not can_view_all:
            # 无「查看全部」权限时强制锁定为本人，忽略 URL 里的 employee_id，
            # 否则改一下查询串就能读到别人的付款记录。
            employee_id = str(g.user["id"])
        employees = (
            api["db"]().execute("select id,name from users where role in ('employee','manager','finance','admin') order by name").fetchall()
            if can_view_all else api["db"]().execute("select id,name from users where id=?", (g.user["id"],)).fetchall()
        )
        clauses, params = ["1=1"], []
        if employee_id.isdigit():
            clauses.append("p.employee_id=?"); params.append(int(employee_id))
        # 状态筛选：白名单判定，非法值当作「全部」。
        if status in PAYMENT_STATUS_LABELS:
            clauses.append("p.status=?"); params.append(status)
        else:
            status = ""
        # 汇总栏「工资 / 员工报销」的计数必须是「当前员工+状态」口径，不套类型筛选 ——
        # 否则选中「工资」后报销数会变成 0，看着像数据没了（v0.1.345）。
        # 所以计数排在类型条件加入之前。
        type_counts = {key: 0 for key in PAYMENT_TYPE_LABELS}
        total_count = 0
        for row in api["db"]().execute(
            f"""select p.payment_type, count(*) total from employee_payment_orders p
            join users u on u.id=p.employee_id where {' and '.join(clauses)} group by p.payment_type""", params
        ).fetchall():
            count = int(row["total"])
            total_count += count
            if row["payment_type"] in type_counts:
                type_counts[row["payment_type"]] = count
        # 类型筛选：白名单判定，只接受 PAYMENT_TYPE_LABELS 里的键，
        # 非法值当作「全部」而不是拼进 SQL（值虽已参数化，但未知类型只会筛出空表，徒增困惑）。
        if payment_type in PAYMENT_TYPE_LABELS:
            clauses.append("p.payment_type=?"); params.append(payment_type)
        else:
            payment_type = ""
        payments = api["db"]().execute(
            f"""select p.*,u.name employee_name from employee_payment_orders p
            join users u on u.id=p.employee_id
            where {' and '.join(clauses)} order by p.created_at desc,p.id desc""", params
        ).fetchall()
        # 借款按员工聚合展示（原表格只有单号/本金/余额，看不出是谁的，选「全部」后完全没法读）
        advances = (
            _advance_balances(api, int(employee_id) if employee_id.isdigit() else None)
            if can_view_all else []
        )
        # 合并发放需要付款账户下拉与「pay」权限；没有权限就不画按钮也不查账户。
        can_pay = can_view_all and api["has_action_permission"]("employee_payments", "pay")
        accounts = (
            api["db"]().execute("select * from bank_accounts where is_active=1 order by account_name").fetchall()
            if can_pay else []
        )
        # Decision 12：纠偏后绝不允许「旧应付 + replacement 应付」双份存在。
        # 往来账这里给出的是 effective payable —— 已作废 / 待纠偏的单据不计入，
        # 页面可以拿它对账 canonical payable。
        # （生产恒安装；api.get 容忍最小 stub。调用时解析。）
        payable_totals_fn = api.get("payable_totals")
        payable = (
            payable_totals_fn({"employee_id": employee_id} if employee_id.isdigit() else None)
            if payable_totals_fn else None
        )
        return render_template("employee_ledger.html", employees=employees, employee_id=employee_id,
                               payments=payments, advances=advances, status_labels=PAYMENT_STATUS_LABELS,
                               type_labels=PAYMENT_TYPE_LABELS, payment_type=payment_type, status=status,
                               total_count=total_count, salary_count=type_counts["salary"],
                               expense_count=type_counts["expense"], can_view_all=can_view_all,
                               can_pay=can_pay, accounts=accounts, method_labels=PAYMENT_METHOD_LABELS,
                               payable=payable,
                               correction_status_labels=api.get("correction_status_labels", {}))

    @app.route("/assets", methods=["GET", "POST"])
    @api["login_required"]
    def assets():
        _require(api, "assets")
        if request.method == "POST":
            _require(api, "assets", "create")
            name = request.form.get("name", "").strip()
            if not name:
                flash("资产名称不能为空。", "error"); return redirect(url_for("assets"))
            number = _next_number(api, "AS", "assets", "asset_number")
            cursor = api["db"]().execute(
                "insert into assets(asset_number,stable_id,name,category,acquisition_value,currency,serial_number,status,current_holder_id,service_order_id,notes,created_by,created_at,updated_at) values(?,?,?, ?,?,'USD',?,'available',null,?,?,?, ?,?)",
                (number, os.urandom(16).hex(), name, request.form.get("category", "").strip(),
                 _money(request.form.get("acquisition_value"), "资产价值"), request.form.get("serial_number", "").strip(),
                 int(request.form["service_order_id"]) if request.form.get("service_order_id", "").isdigit() else None,
                 request.form.get("notes", "").strip(), g.user["id"], api["now"](), api["now"]()),
            )
            asset_id = cursor.lastrowid
            api["db"]().execute("insert into asset_events(asset_id,event_type,to_status,notes,created_by,created_at) values(?,'created','available',?,?,?)", (asset_id,"资产建档",g.user["id"],api["now"]()))
            api["log_action"]("create", "asset", asset_id, number, name)
            api["db"]().commit(); flash("资产已建档。", "success")
            return redirect(url_for("asset_detail", asset_id=asset_id))
        rows = api["db"]().execute("select a.*,u.name holder_name,s.order_number from assets a left join users u on u.id=a.current_holder_id left join service_orders s on s.id=a.service_order_id order by a.asset_number desc").fetchall()
        orders = api["db"]().execute("select id,order_number from service_orders order by id desc limit 200").fetchall()
        return render_template("assets.html", rows=rows, orders=orders, status_labels=ASSET_STATUS_LABELS)

    @app.get("/assets/<int:asset_id>")
    @api["login_required"]
    def asset_detail(asset_id):
        _require(api, "assets")
        asset = api["db"]().execute("select a.*,u.name holder_name,s.order_number from assets a left join users u on u.id=a.current_holder_id left join service_orders s on s.id=a.service_order_id where a.id=?", (asset_id,)).fetchone()
        if not asset: abort(404)
        events = api["db"]().execute("select e.*,u.name creator_name,h.name holder_name from asset_events e left join users u on u.id=e.created_by left join users h on h.id=e.holder_id where e.asset_id=? order by e.id desc", (asset_id,)).fetchall()
        photos = api["db"]().execute("select * from asset_photos where asset_id=? order by id", (asset_id,)).fetchall()
        employees = api["db"]().execute("select id,name from users where is_active=1 order by name").fetchall()
        return render_template("asset_detail.html", asset=asset, events=events, photos=photos, employees=employees,
                               status_labels=ASSET_STATUS_LABELS, event_labels=ASSET_EVENT_LABELS)

    @app.post("/assets/<int:asset_id>/event")
    @api["login_required"]
    def asset_event(asset_id):
        _require(api, "assets", "edit")
        asset = api["db"]().execute("select * from assets where id=?", (asset_id,)).fetchone()
        if not asset: abort(404)
        event = request.form.get("event_type", "")
        mapping = {"assign":"assigned","return":"available","damaged":"damaged","lost":"lost","retire":"retired"}
        if event not in mapping: abort(400)
        target = mapping[event]
        holder_id = int(request.form["holder_id"]) if event == "assign" and request.form.get("holder_id", "").isdigit() else None
        if event == "assign" and not holder_id:
            flash("领用时必须选择员工。", "error"); return redirect(url_for("asset_detail", asset_id=asset_id))
        api["db"]().execute("update assets set status=?,current_holder_id=?,updated_at=? where id=?", (target,holder_id,api["now"](),asset_id))
        api["db"]().execute("insert into asset_events(asset_id,event_type,from_status,to_status,holder_id,notes,created_by,created_at) values(?,?,?,?,?,?,?,?)", (asset_id,event,asset["status"],target,holder_id,request.form.get("notes", "").strip(),g.user["id"],api["now"]()))
        api["log_action"](event, "asset", asset_id, asset["asset_number"], f"{asset['status']} → {target}")
        api["db"]().commit(); flash("资产状态已更新。", "success")
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.get("/assets/<int:asset_id>/qr")
    @api["login_required"]
    def asset_qr(asset_id):
        _require(api, "assets")
        asset = api["db"]().execute("select stable_id from assets where id=?", (asset_id,)).fetchone()
        if not asset: abort(404)
        import qrcode
        target = request.url_root.rstrip("/") + url_for("asset_by_stable_id", stable_id=asset["stable_id"])
        output = io.BytesIO(); qrcode.make(target).save(output, format="PNG"); output.seek(0)
        return send_file(output, mimetype="image/png", download_name=f"asset-{asset_id}-qr.png")

    @app.get("/a/<stable_id>")
    @api["login_required"]
    def asset_by_stable_id(stable_id):
        asset = api["db"]().execute("select id from assets where stable_id=?", (stable_id,)).fetchone()
        if not asset: abort(404)
        return redirect(url_for("asset_detail", asset_id=asset["id"]))

    @app.post("/assets/<int:asset_id>/photos")
    @api["login_required"]
    def upload_asset_photo(asset_id):
        _require(api, "assets", "edit")
        upload = request.files.get("photo")
        if not upload or not upload.filename:
            flash("请选择照片。", "error"); return redirect(url_for("asset_detail", asset_id=asset_id))
        original = secure_filename(upload.filename)
        suffix = Path(original).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif"}:
            flash("不支持的图片格式。", "error"); return redirect(url_for("asset_detail", asset_id=asset_id))
        folder = Path(api["DATA_DIR"]) / "asset-photos" / str(asset_id); folder.mkdir(parents=True, exist_ok=True)
        stored = os.urandom(16).hex() + suffix; upload.save(folder / stored)
        api["db"]().execute("insert into asset_photos(asset_id,stored_filename,original_filename,created_by,created_at) values(?,?,?,?,?)", (asset_id,stored,original,g.user["id"],api["now"]()))
        api["log_action"]("upload", "asset", asset_id, str(asset_id), f"上传照片 {original}")
        api["db"]().commit(); flash("资产照片已上传。", "success")
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.get("/asset-photos/<int:photo_id>")
    @api["login_required"]
    def asset_photo(photo_id):
        _require(api, "assets")
        photo = api["db"]().execute("select * from asset_photos where id=?", (photo_id,)).fetchone()
        if not photo: abort(404)
        return send_file(Path(api["DATA_DIR"]) / "asset-photos" / str(photo["asset_id"]) / photo["stored_filename"], download_name=photo["original_filename"])
