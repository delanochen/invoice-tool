"""工单结算修订版本（v0.1.398，迁移 0313）。

「一份结算、多个修订版本」：
  * 工单保留一个结算入口，默认展示当前有效版本（is_current）；
  * 创建修订版 = 从原版本复制完整快照（结算明细 / 费率 / 报销关联 / 原报销金额 /
    客户结算金额 / 不计入确认 / 附件），不自动重读日报与报销来源；
  * 新版本仍是草稿或审核期间，原版本继续有效；
  * 新版本审核通过并确认替代后，原版本标记 superseded 且只读，原始 PDF /
    附件 / 审核 / 发送记录全部保留；
  * 结算统计、开票、利润表只读当前有效版本，各版本金额不得累加。

本模块不 import app（依赖全部通过 api dict 注入），与 settlement_review 同构。
"""

from __future__ import annotations

import os
import secrets
import shutil

from flask import flash, g, redirect, url_for

SUPERSEDED_STATUS = "superseded"


def settlement_versions(api, order_id):
    """同一工单结算系列下的全部版本，按版本号升序（V1 → 最新）。"""
    return api["db"]().execute(
        """
        select customer_reimbursements.*, users.name as creator_name
        from customer_reimbursement_series
        join customer_reimbursements
          on customer_reimbursements.series_id = customer_reimbursement_series.id
        left join users on users.id = customer_reimbursements.created_by
        where customer_reimbursement_series.service_order_id = ?
        order by customer_reimbursements.version_no, customer_reimbursements.id
        """,
        (order_id,),
    ).fetchall()


def _clone_rows(api, table, id_column, source_id, target_id, overrides=None, skip=()):
    """按行复制整表数据（自动跟随列增减），返回复制的行数。"""
    rows = api["db"]().execute(
        f"select * from {table} where {id_column} = ?", (source_id,)
    ).fetchall()
    copied = 0
    for row in rows:
        data = dict(row)
        data.pop("id", None)
        for key in skip:
            data.pop(key, None)
        data[id_column] = target_id
        if overrides:
            data.update(overrides)
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        api["db"]().execute(
            f"insert into {table} ({columns}) values ({placeholders})",
            tuple(data.values()),
        )
        copied += 1
    return copied


def _copy_attachments(api, source_id, target_id):
    """附件必须物理复制到新版本的目录，不能共享文件路径。"""
    attachments = api["db"]().execute(
        "select * from customer_reimbursement_attachments where customer_reimbursement_id = ?",
        (source_id,),
    ).fetchall()
    source_dir = os.path.join(api["CUSTOMER_REIMBURSEMENT_DIR"], str(source_id), "attachments")
    target_dir = api["customer_reimbursement_attachment_dir"](target_id)
    copied = 0
    for attachment in attachments:
        data = dict(attachment)
        data.pop("id", None)
        data["customer_reimbursement_id"] = target_id
        # 修订版附件是“原结算附件”的独立快照，不是把同一员工报销附件再次传递到结算。
        # source_expense_attachment_id 在全表上有唯一约束，只能属于第一次传递形成的附件记录；
        # 修订版继续保留文件本身、文件名、上传人与时间，但必须清空这个来源占位，
        # 否则复制 V1 -> V2 会因唯一键冲突而导致创建修订版 500/540。
        if "source_expense_attachment_id" in data:
            data["source_expense_attachment_id"] = None
        data["stored_filename"] = f"{secrets.token_hex(12)}-{attachment['original_filename']}"
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        api["db"]().execute(
            f"insert into customer_reimbursement_attachments ({columns}) values ({placeholders})",
            tuple(data.values()),
        )
        source_path = os.path.join(source_dir, attachment["stored_filename"])
        if os.path.isfile(source_path):
            shutil.copy2(source_path, os.path.join(target_dir, data["stored_filename"]))
        copied += 1
    return copied


def create_customer_reimbursement_revision(api, reimbursement_id, reason):
    """从指定版本创建下一版草稿。

    快照语义：新版本不再自动合并日报 / 报销来源（sources_frozen），
    明细、费率、报销关联、客户结算金额、不计入确认、附件全部照抄原版本。
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("创建修订版必须填写修订原因。")
    reimbursement = api["db"]().execute(
        "select * from customer_reimbursements where id = ?", (reimbursement_id,)
    ).fetchone()
    if not reimbursement:
        raise ValueError("工单结算不存在。")
    if not reimbursement["is_current"]:
        raise ValueError("只能从当前有效版本创建修订版。")
    if reimbursement["status"] != "approved":
        raise ValueError("只有审核通过的工单结算可以创建修订版；草稿请直接编辑。")
    # 已开票的版本必须先处理发票与应收差额，否则替代后结算 / 发票 / 利润表三方对不上。
    active_invoice = api["customer_reimbursement_linked_invoice"](reimbursement, reimbursement["service_order_id"])
    if active_invoice:
        raise ValueError(
            "该版本已生成发票，请先作废 / 冲销对应发票并处理应收差额，再创建修订版。"
        )

    version_row = api["db"]().execute(
        "select coalesce(max(version_no), 0) + 1 as next_version "
        "from customer_reimbursements where series_id = ?",
        (reimbursement["series_id"],),
    ).fetchone()
    next_version = int(version_row["next_version"])

    cursor = api["db"]().execute(
        """
        insert into customer_reimbursements (
            series_id, service_order_id, file_name, stored_filename,
            labor_total, lodging_total, travel_total, mileage_total, total_amount,
            mro_supplies_total, rental_fuel_total, other_total, gross_amount,
            discount_amount, expense_selection_mode, settlement_basis,
            quotation_revision_id, lodging_person_nights, lodging_cap_rate_snapshot,
            contract_rate_version_id, expense_transfer_cutoff_at,
            version_no, supersedes_id, is_current, revision_reason, sources_frozen,
            status, created_by, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            reimbursement["series_id"], reimbursement["service_order_id"],
            reimbursement["file_name"], f"{secrets.token_hex(12)}.pdf",
            reimbursement["labor_total"], reimbursement["lodging_total"],
            reimbursement["travel_total"], reimbursement["mileage_total"],
            reimbursement["total_amount"], reimbursement["mro_supplies_total"],
            reimbursement["rental_fuel_total"], reimbursement["other_total"],
            reimbursement["gross_amount"], reimbursement["discount_amount"],
            reimbursement["expense_selection_mode"], reimbursement["settlement_basis"],
            reimbursement["quotation_revision_id"], reimbursement["lodging_person_nights"],
            reimbursement["lodging_cap_rate_snapshot"], reimbursement["contract_rate_version_id"],
            reimbursement["expense_transfer_cutoff_at"],
            next_version, reimbursement["id"], False, reason, True,
            "draft", g.user["id"], api["now"](),
        ),
    )
    revision_id = cursor.lastrowid

    items = _clone_rows(
        api, "customer_reimbursement_items", "customer_reimbursement_id",
        reimbursement_id, revision_id,
    )
    links = _clone_rows(
        api, "customer_reimbursement_expense_links", "customer_reimbursement_id",
        reimbursement_id, revision_id,
        # selected_by / selected_at 保留原勾选记录，不覆盖成修订人：审计要能看出
        # 「谁勾的、谁改的金额」是两件事。
    )
    ignores = _clone_rows(
        api, "customer_reimbursement_expense_ignores", "customer_reimbursement_id",
        reimbursement_id, revision_id,
    )
    attachments = _copy_attachments(api, reimbursement_id, revision_id)

    api["log_action"](
        "create",
        "customer_reimbursement",
        revision_id,
        reimbursement["file_name"],
        f"创建修订版 V{next_version}，来源版本 id={reimbursement_id}；"
        f"原因：{reason}；复制明细 {items} 行、报销关联 {links} 条、"
        f"不计入确认 {ignores} 条、附件 {attachments} 个",
    )
    return api["db"]().execute(
        "select * from customer_reimbursements where id = ?", (revision_id,)
    ).fetchone()


def promote_customer_reimbursement_revision(api, reimbursement_id):
    """修订版审核通过后确认替代：原版本只读留档，新版本成为当前有效版本。"""
    revision = api["db"]().execute(
        "select * from customer_reimbursements where id = ?", (reimbursement_id,)
    ).fetchone()
    if not revision:
        raise ValueError("工单结算不存在。")
    if revision["is_current"]:
        raise ValueError("该版本已经是当前有效版本。")
    if revision["status"] != "approved":
        raise ValueError("只有审核通过的修订版可以替代原版本。")
    if not revision["supersedes_id"]:
        raise ValueError("该版本没有来源版本，无法替代。")

    current = api["db"]().execute(
        "select * from customer_reimbursements where series_id = ? and is_current",
        (revision["series_id"],),
    ).fetchone()
    if not current:
        raise ValueError("找不到该系列的当前有效版本。")
    active_invoice = api["customer_reimbursement_linked_invoice"](current, current["service_order_id"])
    if active_invoice:
        raise ValueError(
            "当前有效版本已生成发票，请先作废 / 冲销并处理应收差额，再确认替代。"
        )

    api["db"]().execute(
        "update customer_reimbursements set is_current = false, superseded_at = ?, "
        "status = ? where id = ?",
        (api["now"](), SUPERSEDED_STATUS, current["id"]),
    )
    api["db"]().execute(
        "update customer_reimbursements set is_current = true, superseded_at = null "
        "where id = ?",
        (reimbursement_id,),
    )
    api["log_action"](
        "update",
        "customer_reimbursement",
        reimbursement_id,
        revision["file_name"],
        f"修订版 V{revision['version_no']} 替代原版本 id={current['id']}"
        f"（V{current['version_no']} 已标记 {SUPERSEDED_STATUS}，只读留档）",
    )
    return api["db"]().execute(
        "select * from customer_reimbursements where id = ?", (reimbursement_id,)
    ).fetchone()


def register_settlement_revision_routes(app, api):
    @app.post("/customer-reimbursements/<int:reimbursement_id>/revision")
    @api["login_required"]
    def create_customer_reimbursement_revision_route(reimbursement_id):
        if not api["can_manage_customer_reimbursement"]():
            app.abort(403)
        reimbursement, order = api["require_customer_reimbursement"](reimbursement_id)
        reason = api["request"].form.get("revision_reason", "").strip()
        try:
            revision = create_customer_reimbursement_revision(
                api, reimbursement["id"], reason
            )
            api["db"]().commit()
        except ValueError as error:
            api["db"]().rollback()
            api["flash"](str(error), "error")
            return api["redirect"](
                api["url_for"]("customer_reimbursement_form", order_id=order["id"])
            )
        api["flash"](
            f"已创建修订版 V{revision['version_no']} 草稿，原版本仍然有效。", "success"
        )
        return api["redirect"](
            api["url_for"]("customer_reimbursement_form", order_id=order["id"])
        )

    @app.post("/customer-reimbursements/<int:reimbursement_id>/promote")
    @api["login_required"]
    def promote_customer_reimbursement_revision_route(reimbursement_id):
        if not api["can_manage_customer_reimbursement"]():
            app.abort(403)
        reimbursement, order = api["require_customer_reimbursement"](reimbursement_id)
        try:
            promote_customer_reimbursement_revision(api, reimbursement["id"])
            api["db"]().commit()
        except ValueError as error:
            api["db"]().rollback()
            api["flash"](str(error), "error")
            return api["redirect"](
                api["url_for"]("customer_reimbursement_form", order_id=order["id"])
            )
        api["flash"]("修订版已替代原版本，成为当前有效结算。", "success")
        return api["redirect"](
            api["url_for"]("customer_reimbursement_form", order_id=order["id"])
        )
