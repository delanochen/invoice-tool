"""工单间数据转移：把一个工单的日报 / 报销（含附件、现场照片、AI 日报留痕）搬到另一个工单。

为什么需要这个工具：日报编辑页（`edit_service_report`）的 UPDATE 里没有 `service_order_id`，
报销编辑页同理 —— 以前只能手工改库再手工搬文件，而**日报附件的相对路径里嵌了工单号**，
只改库不改磁盘就会让附件 404（见技能文档 invoice-tool-order-data-transfer）。

设计原则（与人工流程一致）：
  - **只搬用户勾选的那几条**，不顺手搬同类其它数据；
  - 日报附件按当前工单号 + 日报日期 + 分类重算相对路径，DB 与磁盘一起改；
  - 现场照片按日报日期**整批**搬（`pictures` + `thumbnails` 两份，缩略图路径由
    `relative_path` 推导，只搬一份会让缩略图 404）；
  - AI 日报草稿/附件清单跟着日报走，否则「某工单的 AI 草稿列表」会和该工单日报数量对不上；
  - 报销是**整单**转移：附件目录按 `expense_id` 组织，不嵌工单号，所以磁盘零搬运；
  - 已被工单结算单引用的日报/报销**拒绝转移**（转移会让结算单对不上账）；
  - **不改动 `updated_at`**：这不是内容编辑，改了会影响「最近修改」排序/复核时点判断；
  - 全程单事务：先改库 → 再复制文件 → commit → 清理旧文件。中途任何失败都回滚数据库
    并删掉已复制的文件，保证「要么全成，要么原样」。
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from flask import flash, redirect, render_template, request, url_for


ORDER_NUMBER_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


# ────────────────────────────── 候选清单 ──────────────────────────────

def _report_candidates(api, order_id):
    rows = api["db"]().execute(
        """
        select r.id, r.report_date, r.actual_work_date, r.total_service_hours,
               r.service_description,
               (select count(*) from service_report_attachments a
                 where a.report_id = r.id) as attachment_count,
               (select count(*) from service_report_workers w
                 where w.report_id = r.id) as worker_count,
               (select count(*) from field_photos fp
                 where fp.order_id = r.service_order_id and fp.capture_date = r.report_date) as photo_count,
               (select count(*) from customer_reimbursement_items ci
                 where ci.source_report_id = r.id) as settlement_refs,
               (select count(*) from service_report_mileage_evidence me
                 where me.report_id = r.id) as mileage_count
        from service_reports r
        where r.service_order_id = ?
        order by r.report_date, r.id
        """,
        (order_id,),
    ).fetchall()
    candidates = []
    for row in rows:
        blocked = None
        if row["settlement_refs"]:
            blocked = "已被工单结算单引用（人工/工时行），转移后结算单会对不上账"
        candidates.append(
            {
                "id": row["id"],
                "report_date": row["report_date"],
                "actual_work_date": row["actual_work_date"],
                "total_service_hours": row["total_service_hours"],
                "service_description": row["service_description"] or "",
                "attachment_count": row["attachment_count"],
                "worker_count": row["worker_count"],
                "photo_count": row["photo_count"],
                "mileage_count": row["mileage_count"],
                "blocked": blocked,
            }
        )
    return candidates


def _expense_candidates(api, order_id):
    rows = api["db"]().execute(
        """
        select e.id, e.expense_number, e.expense_date, e.amount, e.status, e.project,
               (select count(*) from expense_items i where i.expense_id = e.id) as item_count,
               (select count(*) from expense_attachments a where a.expense_id = e.id) as attachment_count,
               (select count(*) from customer_reimbursement_expense_links l
                  join expense_items i on i.id = l.expense_item_id
                where i.expense_id = e.id) as settlement_refs,
               (select count(*) from customer_reimbursement_attachments ca
                  join expense_attachments a on a.id = ca.source_expense_attachment_id
                where a.expense_id = e.id) as settlement_attachment_refs
        from expenses e
        where e.service_order_id = ?
        order by e.expense_date, e.id
        """,
        (order_id,),
    ).fetchall()
    candidates = []
    for row in rows:
        blocked = None
        if row["settlement_refs"]:
            blocked = "已勾选进工单结算单，转移后结算单金额会对不上"
        elif row["settlement_attachment_refs"]:
            blocked = "附件已被工单结算单引用"
        candidates.append(
            {
                "id": row["id"],
                "expense_number": row["expense_number"],
                "expense_date": row["expense_date"],
                "amount": row["amount"],
                "status": row["status"],
                "project": row["project"] or "",
                "item_count": row["item_count"],
                "attachment_count": row["attachment_count"],
                "blocked": blocked,
            }
        )
    return candidates


def downstream_counts(api, order_id):
    """工单下游关联（只作提示，不拦）：结算单 / 发票 / 利润台账 / AI 草稿 / 人员指派。"""
    db = api["db"]()
    counts = {
        "customer_reimbursements": db.execute(
            "select count(*) as count from customer_reimbursements where service_order_id = ?",
            (order_id,),
        ).fetchone()["count"],
        "invoices": db.execute(
            "select count(*) as count from invoices where service_order_id = ? and status != 'void'",
            (order_id,),
        ).fetchone()["count"],
        "profit_ledger": db.execute(
            "select count(*) as count from profit_ledger where service_order_id = ?",
            (order_id,),
        ).fetchone()["count"],
    }
    labels = {
        "customer_reimbursements": "工单结算",
        "invoices": "发票",
        "profit_ledger": "利润台账",
    }
    return [f"{labels[key]} {counts[key]}" for key, count in counts.items() if count]


# ────────────────────────────── 转移执行 ──────────────────────────────

def _replace_order_prefix(relative_path, source_number, target_number):
    """把 `SO源/...` 换成 `SO目标/...`（路径首段一定是工单号，锚定前缀最稳）。"""
    parts = str(relative_path or "").split("/")
    if not parts or parts[0] != source_number:
        return None
    parts[0] = target_number
    return "/".join(parts)


def _photo_dirs(api, order_number, report_date):
    root = Path(api["SHARED_PHOTOS_DIR"])
    return (
        root / order_number / "pictures" / str(report_date),
        root / order_number / "thumbnails" / str(report_date),
    )


def _cleanup_copied(copied_files, copied_dirs):
    """回滚：删掉已经复制到新位置的文件/目录（旧位置还没动过，天然完好）。"""
    for path in reversed(copied_files):
        try:
            os.remove(path)
        except OSError:
            pass
    for path in reversed(copied_dirs):
        shutil.rmtree(path, ignore_errors=True)


def transfer_order_data(api, source_order, target_order, report_ids, expense_ids):
    """把勾选的日报/报销从源工单转移到目标工单。

    返回摘要 dict。任何一步失败都抛 ValueError（**且已回滚**），调用方只需提示用户。
    注意：本函数不 commit —— 提交由调用方决定（路由里成功后 commit）。
    """
    db = api["db"]()
    source_id = source_order["id"]
    target_id = target_order["id"]
    if source_id == target_id:
        raise ValueError("源工单和目标工单是同一个。")

    # ── 1) 校验 + 生成计划（只读）
    reports = []
    for report_id in report_ids:
        row = db.execute(
            "select * from service_reports where id = ? and service_order_id = ?",
            (report_id, source_id),
        ).fetchone()
        if not row:
            raise ValueError(f"日报 #{report_id} 不在源工单 {source_order['order_number']} 里。")
        refs = db.execute(
            "select count(*) as count from customer_reimbursement_items where source_report_id = ?",
            (report_id,),
        ).fetchone()["count"]
        if refs:
            raise ValueError(
                f"{row['report_date']} 的日报已被工单结算单引用，请先从结算单里去掉再转移。"
            )
        reports.append(row)

    expenses = []
    for expense_id in expense_ids:
        row = db.execute(
            "select * from expenses where id = ? and service_order_id = ?",
            (expense_id, source_id),
        ).fetchone()
        if not row:
            raise ValueError(f"报销 #{expense_id} 不在源工单 {source_order['order_number']} 里。")
        refs = db.execute(
            """
            select count(*) as count from customer_reimbursement_expense_links l
            join expense_items i on i.id = l.expense_item_id
            where i.expense_id = ?
            """,
            (expense_id,),
        ).fetchone()["count"]
        attachment_refs = db.execute(
            """
            select count(*) as count from customer_reimbursement_attachments ca
            join expense_attachments a on a.id = ca.source_expense_attachment_id
            where a.expense_id = ?
            """,
            (expense_id,),
        ).fetchone()["count"]
        if refs or attachment_refs:
            raise ValueError(f"报销 {row['expense_number']} 已被工单结算单引用，不能转移。")
        expenses.append(row)

    # ── 2) 改库（此时还没碰磁盘，失败可无损回滚）
    attachment_copies = []  # (旧绝对路径, 新绝对路径)
    photo_plan = {}  # (源目录, 目标目录) —— 同一天多个日报只搬一次
    moved_attachments = 0
    moved_photos = 0
    mileage_reports = 0

    for report in reports:
        # 日期列是 text，但保险起见统一成 'YYYY-MM-DD' 字符串再用于比较/拼目录名。
        report_date = str(report["report_date"] or "")[:10]
        db.execute(
            "update service_reports set service_order_id = ? where id = ?",
            (target_id, report["id"]),
        )

        attachments = db.execute(
            "select * from service_report_attachments where report_id = ?",
            (report["id"],),
        ).fetchall()
        for attachment in attachments:
            old_abs = api["report_attachment_path"](attachment)
            new_relative = api["report_attachment_relative_path"](
                report["id"], attachment["category"], os.path.basename(attachment["stored_filename"])
            )
            new_abs = os.path.join(api["REPORT_ATTACHMENTS_DIR"], *Path(new_relative).parts)
            if os.path.normcase(os.path.abspath(old_abs)) == os.path.normcase(os.path.abspath(new_abs)):
                continue
            db.execute(
                "update service_report_attachments set stored_filename = ? where id = ?",
                (new_relative, attachment["id"]),
            )
            attachment_copies.append((old_abs, new_abs))
            moved_attachments += 1

        photos = db.execute(
            "select id, relative_path from field_photos where order_id = ? and capture_date = ?",
            (source_id, report_date),
        ).fetchall()
        if photos:
            for photo in photos:
                new_relative = _replace_order_prefix(
                    photo["relative_path"], source_order["order_number"], target_order["order_number"]
                )
                if new_relative is None:
                    raise ValueError(f"现场照片 #{photo['id']} 的路径不以源工单号开头，已中止。")
                conflict = db.execute(
                    "select count(*) as count from field_photos where relative_path = ? and id != ?",
                    (new_relative, photo["id"]),
                ).fetchone()["count"]
                if conflict:
                    raise ValueError(
                        f"目标工单 {target_order['order_number']} 下已有同名现场照片（{new_relative}），已中止。"
                    )
                db.execute(
                    "update field_photos set order_id = ?, relative_path = ? where id = ?",
                    (target_id, new_relative, photo["id"]),
                )
                moved_photos += 1
            source_pictures, source_thumbnails = _photo_dirs(
                api, source_order["order_number"], report_date
            )
            target_pictures, target_thumbnails = _photo_dirs(
                api, target_order["order_number"], report_date
            )
            photo_plan[(str(source_pictures), str(target_pictures))] = True
            photo_plan[(str(source_thumbnails), str(target_thumbnails))] = True

        # 里程佐证挂在 report_id 上、终点是**源**工单的 site_address —— 换工单后
        # 终点就不对了，只能提示人工重算（与「改起点需手动重算」的既有口径一致）。
        mileage = db.execute(
            "select count(*) as count from service_report_mileage_evidence where report_id = ?",
            (report["id"],),
        ).fetchone()["count"]
        if mileage:
            mileage_reports += 1

        # AI 日报留痕：草稿按 saved_report_id 或 同工单同日期 对应；清单按 draft_id 跟随。
        db.execute(
            """
            update ai_daily_report_drafts set service_order_id = ?
             where saved_report_id = ? or (service_order_id = ? and report_date = ?)
            """,
            (target_id, report["id"], source_id, report_date),
        )
        db.execute(
            """
            update ai_daily_report_attachment_manifests set service_order_id = ?
             where service_order_id = ?
               and draft_id in (select id from ai_daily_report_drafts where service_order_id = ?)
            """,
            (target_id, source_id, target_id),
        )

    for expense in expenses:
        db.execute(
            "update expenses set service_order_id = ? where id = ?",
            (target_id, expense["id"]),
        )

    # ── 3) 复制文件（先复制，commit 成功后才删旧的）
    copied_files = []
    copied_dirs = []
    try:
        for old_abs, new_abs in attachment_copies:
            if not os.path.isfile(old_abs):
                continue  # 磁盘上本来就缺文件：DB 已指向新路径，不阻塞转移
            os.makedirs(os.path.dirname(new_abs), exist_ok=True)
            shutil.copy2(old_abs, new_abs)
            copied_files.append(new_abs)
        for source_dir, target_dir in photo_plan:
            if not os.path.isdir(source_dir):
                continue
            os.makedirs(os.path.dirname(target_dir), exist_ok=True)
            shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
            copied_dirs.append(target_dir)
    except Exception:
        _cleanup_copied(copied_files, copied_dirs)
        db.rollback()
        raise

    summary = {
        "reports": [
            {"id": report["id"], "report_date": report["report_date"]} for report in reports
        ],
        "expenses": [
            {"id": expense["id"], "expense_number": expense["expense_number"]} for expense in expenses
        ],
        "moved_attachments": moved_attachments,
        "moved_photos": moved_photos,
        "mileage_reports": mileage_reports,
        "copied_files": copied_files,
        "copied_dirs": copied_dirs,
        # commit 后要删掉的旧文件/目录（此时新位置已生效）
        "stale_files": [old_abs for old_abs, _new_abs in attachment_copies],
        "stale_dirs": [source_dir for source_dir, _target_dir in photo_plan],
    }
    return summary


def finalize_transfer(api, summary):
    """commit 之后调用：删掉旧位置的文件/目录，并写操作日志。"""
    for path in summary["stale_files"]:
        try:
            os.remove(path)
        except OSError:
            continue
        api["prune_empty_report_folders"](path)
    for path in summary["stale_dirs"]:
        shutil.rmtree(path, ignore_errors=True)


def _resolve_order(api, order_number, field_label):
    """按工单号取工单；走 require_service_order 以保证越权访问照样 403/404。"""
    number = (order_number or "").strip()
    if not number:
        return None, f"请填写{field_label}工单号。"
    if not ORDER_NUMBER_PATTERN.match(number):
        return None, f"{field_label}工单号格式不正确。"
    row = api["db"]().execute(
        "select id from service_orders where order_number = ?", (number,)
    ).fetchone()
    if not row:
        return None, f"找不到{field_label}工单 {number}。"
    return api["require_service_order"](row["id"]), None


def _selected_ids(field_name):
    values = []
    for value in request.form.getlist(field_name):
        if str(value).isdigit():
            values.append(int(value))
    return values


def register_order_data_transfer_routes(app, api):
    @app.route("/tools/order-data-transfer", methods=["GET", "POST"])
    @api["login_required"]
    def order_data_transfer():
        source_number = (request.values.get("source") or "").strip()
        target_number = (request.values.get("target") or "").strip()
        source_order = target_order = None
        lookup_error = None
        reports = []
        expenses = []
        source_downstream = []
        target_downstream = []

        if source_number:
            source_order, lookup_error = _resolve_order(api, source_number, "源")
        if not lookup_error and target_number:
            target_order, lookup_error = _resolve_order(api, target_number, "目标")

        if source_order and target_order and source_order["id"] == target_order["id"]:
            target_order = None
            lookup_error = "源工单和目标工单不能是同一个。"

        if source_order:
            reports = _report_candidates(api, source_order["id"])
            expenses = _expense_candidates(api, source_order["id"])
            source_downstream = downstream_counts(api, source_order["id"])
        if target_order:
            target_downstream = downstream_counts(api, target_order["id"])

        if request.method == "POST":
            if lookup_error:
                flash(lookup_error, "error")
            elif not source_order or not target_order:
                flash("请先填写源工单号和目标工单号。", "error")
            else:
                report_ids = _selected_ids("report_ids")
                expense_ids = _selected_ids("expense_ids")
                blocked = {row["id"] for row in reports if row["blocked"]}
                blocked_expenses = {row["id"] for row in expenses if row["blocked"]}
                if set(report_ids) & blocked or set(expense_ids) & blocked_expenses:
                    flash("勾选了不可转移的数据（已被工单结算单引用）。", "error")
                elif not report_ids and not expense_ids:
                    flash("请至少勾选一条日报或报销。", "error")
                elif request.form.get("confirm") != "1":
                    flash("请先勾选「我确认转移」再执行。", "error")
                else:
                    try:
                        summary = transfer_order_data(
                            api, source_order, target_order, report_ids, expense_ids
                        )
                        api["db"]().commit()
                    except ValueError as exc:
                        api["db"]().rollback()
                        flash(f"转移失败：{exc}", "error")
                    except Exception as exc:  # pragma: no cover - 兜底，避免半途状态
                        api["db"]().rollback()
                        flash(f"转移失败，已回滚：{exc}", "error")
                    else:
                        finalize_transfer(api, summary)
                        for report in summary["reports"]:
                            api["log_action"](
                                "update",
                                "service_report",
                                report["id"],
                                f"{source_order['order_number']} → {target_order['order_number']}",
                                f"转移日报 {report['report_date']} 到工单 {target_order['order_number']}",
                            )
                        for expense in summary["expenses"]:
                            api["log_action"](
                                "update",
                                "expense",
                                expense["id"],
                                expense["expense_number"],
                                f"转移报销到工单 {target_order['order_number']}",
                            )
                        # 操作日志也是一次写入：请求结束只 close 不 commit，必须显式提交。
                        api["db"]().commit()
                        parts = []
                        if summary["reports"]:
                            parts.append(f"{len(summary['reports'])} 条日报")
                        if summary["expenses"]:
                            parts.append(f"{len(summary['expenses'])} 张报销")
                        detail = f"；搬运附件 {summary['moved_attachments']} 个、现场照片 {summary['moved_photos']} 张"
                        if summary["mileage_reports"]:
                            detail += (
                                f"；{summary['mileage_reports']} 条日报带里程佐证（终点是源工单地址），"
                                "请在新工单下重算里程"
                            )
                        flash(
                            f"已转移 {'、'.join(parts)} 到 {target_order['order_number']}{detail}。",
                            "success",
                        )
                        return redirect(
                            url_for(
                                "order_data_transfer",
                                source=source_order["order_number"],
                                target=target_order["order_number"],
                            )
                        )

        return render_template(
            "order_data_transfer.html",
            source=source_number,
            target=target_number,
            source_order=source_order,
            target_order=target_order,
            lookup_error=lookup_error,
            reports=reports,
            expenses=expenses,
            source_downstream=source_downstream,
            target_downstream=target_downstream,
        )
