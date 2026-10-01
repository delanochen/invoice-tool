"""历史 ER 税务组件回填工具（Phase 3C，一次性运维工具）。

用法（生产容器内）：
    # 只读预演：全程读，不写任何数据
    python /app/scripts/backfill_er_tax_components.py            # 或 --dry-run

    # 正式写入（务必先停部署定时器；逐张提交，可重复运行、可中断续跑）
    python /app/scripts/backfill_er_tax_components.py --commit

行为：
- 目标：payment_type='expense'、source_type='expense'、source_id 仍指向存在的
  expenses 行、且**尚无生效组件快照**的 ER（已有的跳过 → 幂等，可安全重跑）。
- 每张 ER：按 expense_items 1:1 重算（与 3A 新单生成完全相同的
  employee_finance.expense_payment_components，历史兼容口径
  business_purpose OR description OR reviewed_by）→
  sum(组件) == gross_amount 硬校验（Decimal + ROUND_HALF_UP，禁止 round()）→
  写入 employee_payment_components（source_type='historical_expense_recompute'）
  → 更新付款单三项税务合计。
- 校验失败 / 重算异常：跳过该张并计报告，绝不为了凑金额改组件，也绝不改动
  付款单金额、状态、批次关系。
- 排除项：source_id 指向已删除 expense 的 ER（如已 cancelled 的
  ER-2609-0098）永不生成组件、不补伪造 source，仅在报告中列为
  metadata_error / cancelled_source_missing。

已知限制（必须随报告呈现，不得掩盖）：
  本工具重建的是「当前算法下的组件划分」，只能保证总额与 gross 一致，不能
  证明与历史审核当日真正确认的证据状态完全相同——这是历史系统未保存
  component snapshot 的固有限制。
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BACKFILL_SOURCE_TYPE = "historical_expense_recompute"
CENT = Decimal("0.01")


def money(value):
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return dec.quantize(CENT, rounding=ROUND_HALF_UP)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true",
                        help="正式写入；缺省为只读 dry-run")
    args = parser.parse_args()

    import app as A  # noqa: E402  生产容器 /app 内执行
    import employee_finance as EF  # noqa: E402

    api = vars(A)

    with A.app.app_context():
        db = A.db()
        orders = db.execute(
            """
            select p.id, p.payment_number, p.gross_amount, p.status, p.source_id
            from employee_payment_orders p
            where p.payment_type = 'expense'
              and p.source_type = 'expense'
              and p.source_id is not null
              and exists (select 1 from expenses e where e.id = p.source_id)
              and not exists (
                  select 1 from employee_payment_components c
                  where c.payment_order_id = p.id and c.superseded_at is null)
            order by p.id
            """
        ).fetchall()
        excluded = db.execute(
            """
            select p.id, p.payment_number, p.status, p.gross_amount, p.source_id
            from employee_payment_orders p
            where p.payment_type = 'expense'
              and (p.source_type <> 'expense' or p.source_id is null
                   or not exists (select 1 from expenses e where e.id = p.source_id))
            order by p.id
            """
        ).fetchall()
        already = db.execute(
            """
            select count(*) from employee_payment_orders p
            where p.payment_type = 'expense' and exists (
                select 1 from employee_payment_components c
                where c.payment_order_id = p.id and c.superseded_at is null)
            """
        ).fetchone()[0]
        print("candidate ER rows: %d" % len(orders))
        print("skipped_existing (已有生效快照): %d" % already)
        print("excluded metadata_error: %d" % len(excluded))
        for row in excluded:
            print("[EXCLUDED] %s status=%s gross=%s source_id=%s reason=metadata_error/cancelled_source_missing"
                  % (row["payment_number"], row["status"], row["gross_amount"], row["source_id"]))
        stats = {"backfilled": 0, "verified": 0, "mismatch": 0, "error": 0}
        review_amount = Decimal("0")
        missing_status_lines = 0
        for row in orders:
            expense = db.execute(
                "select *,coalesce(beneficiary_id,created_by) employee_id"
                " from expenses where id=?", (row["source_id"],)).fetchone()
            if expense is None:  # 竞态保护：与上一条 exists 之间被删除
                stats["error"] += 1
                print("[SKIP-ERROR] %s: expense 不存在" % row["payment_number"])
                continue
            try:
                components, totals = EF.expense_payment_components(api, expense, row["id"])
            except ValueError as error:
                stats["mismatch"] += 1
                print("[SKIP-MISMATCH] %s: %s" % (row["payment_number"], error))
                continue
            except Exception as error:  # noqa: BLE001 单张失败不能中断整批
                stats["error"] += 1
                print("[SKIP-ERROR] %s: %s: %s"
                      % (row["payment_number"], type(error).__name__, error))
                continue
            gross = money(row["gross_amount"])
            line_sum = sum((money(line["amount"]) for line in components), Decimal("0"))
            totals_sum = sum((money(v) for v in totals.values()), Decimal("0"))
            if line_sum != gross or totals_sum != gross:  # 双闸门：组件与三项合计都要闭合
                stats["mismatch"] += 1
                print("[SKIP-MISMATCH] %s: components=%s totals=%s gross=%s"
                      % (row["payment_number"], line_sum, totals_sum, gross))
                continue
            missing = sum(1 for line in components if line["tax_status_snapshot"] is None)
            missing_status_lines += missing
            review_amount += money(totals["tax_review_required"])
            stats["verified"] += 1
            if not args.commit:
                print("[DRY] %s gross=%s components=%d review_required=%s missing_status=%d"
                      % (row["payment_number"], gross, len(components),
                         money(totals["tax_review_required"]), missing))
                continue
            for line in components:
                db.execute(
                    """
                    insert into employee_payment_components
                    (payment_order_id,employee_id,component_code,component_name,amount,
                     quantity,unit,unit_rate,service_date,work_order_id,source_type,source_id,
                     daily_report_id,tax_category,tax_status_snapshot,substantiated,
                     review_status,created_at)
                    values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (row["id"], line["employee_id"], line["component_code"],
                     line["component_name"], line["amount"], line["quantity"], line["unit"],
                     line["unit_rate"], line["service_date"], line["work_order_id"],
                     BACKFILL_SOURCE_TYPE, line["source_id"], line["daily_report_id"],
                     line["tax_category"], line["tax_status_snapshot"], line["substantiated"],
                     line["review_status"], A.now()),
                )
            db.execute(
                "update employee_payment_orders set taxable_compensation_total=?,"
                "accountable_reimbursement_total=?,tax_review_required_total=?,updated_at=? where id=?",
                (totals["taxable_compensation"], totals["accountable_reimbursement"],
                 totals["tax_review_required"], A.now(), row["id"]),
            )
            db.commit()
            stats["backfilled"] += 1
            print("[OK] %s: %d components, review_required=%s"
                  % (row["payment_number"], len(components),
                     money(totals["tax_review_required"])))
        print("\n=== SUMMARY ===")
        print("mode:", "COMMIT" if args.commit else "DRY-RUN (read only)")
        for key, value in stats.items():
            print("%s: %s" % (key, value))
        print("excluded: %d" % len(excluded))
        print("components with missing tax_status_snapshot:", missing_status_lines)
        print("tax_review_required amount total:", review_amount)
        if not args.commit:
            print("\n已知限制：本工具重算的是「当前算法下的组件划分」，只能保证总额")
            print("与 gross 一致，不能证明与历史审核当日冻结的证据状态完全相同。")


if __name__ == "__main__":
    main()
