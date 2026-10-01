"""历史 SL 税务组件回填工具（Phase 2C，一次性运维工具）。

用法（生产容器内）：
    # 只读预演：全程只读会话，不写任何数据
    python /app/scripts/backfill_sl_tax_components.py            # 或 --dry-run

    # 正式写入（务必先停部署定时器；逐张提交，可重复运行、可中断续跑）
    python /app/scripts/backfill_sl_tax_components.py --commit

行为：
- 目标：source_type='system_payroll' / 'offline_salary' 且**尚无**组件快照的
  工资付款单（已存在的跳过 → 幂等，可安全重跑）。
- 每张 SL：解析工资周期 → 用与生成 SL 完全相同的组件服务重算
  （payroll_payment_components / offline_salary_payment_components）→
  sum(组件) == gross_amount 硬校验（Decimal + ROUND_HALF_UP，禁止 round()）→
  写入 employee_payment_components（source_type='historical_recompute'）
  → 更新付款单三项税务合计。
- 校验失败 / 重算异常：跳过该张并计入报告，绝不为了凑金额修改组件，
  也绝不改动付款单金额、状态、批次关系。

已知限制（必须随报告呈现，不得掩盖）：
  55/55 只能证明「当前算法能把历史 gross 精确重算到分」，不能证明
  「今天重算出的组件比例就是历史生成当日真正冻结的组件比例」——
  这是历史系统未保存 component snapshot 的固有限制。
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PERIOD_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def parse_period(source_number, source_key):
    """从 source_number 'YYYY-MM-DD~YYYY-MM-DD' 或 source_key 尾部解析周期起始。"""
    if source_number:
        hit = PERIOD_RE.search(str(source_number))
        if hit:
            return date.fromisoformat(hit.group(1))
    if source_key:
        hit = PERIOD_RE.search(str(source_key))
        if hit:
            return date.fromisoformat(hit.group(1))
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true",
                        help="正式写入；缺省为只读 dry-run")
    args = parser.parse_args()

    import app as A  # noqa: E402  生产容器 /app 内执行

    with A.app.app_context():
        db = A.db()
        orders = db.execute(
            """
            select p.id, p.payment_number, p.employee_id, p.gross_amount, p.status,
                   p.source_type, p.source_number, p.source_key
            from employee_payment_orders p
            where p.payment_type = 'salary'
              and p.source_type in ('system_payroll', 'offline_salary')
              and not exists (
                  select 1 from employee_payment_components c
                  where c.payment_order_id = p.id)
            order by p.id
            """
        ).fetchall()
        print("candidate SL rows: %d" % len(orders))
        stats = {"backfilled": 0, "skipped_existing": 0, "verified": 0,
                 "mismatch": 0, "error": 0}
        review_amount = Decimal("0")
        missing_status_lines = 0
        for row in orders:
            period_start = parse_period(row["source_number"], row["source_key"])
            if period_start is None:
                stats["mismatch"] += 1
                print("[SKIP-METADATA] %s: 无法解析工资周期" % row["payment_number"])
                continue
            try:
                if row["source_type"] == "offline_salary":
                    period_end = period_start + timedelta(days=13)
                    components, totals = A.offline_salary_payment_components(
                        row["employee_id"], row["gross_amount"], period_end)
                else:
                    components, totals = A.payroll_payment_components(
                        period_start, row["employee_id"], row["gross_amount"])
            except ValueError as error:
                stats["mismatch"] += 1
                print("[SKIP-MISMATCH] %s: %s" % (row["payment_number"], error))
                continue
            except Exception as error:  # noqa: BLE001 单张失败不能中断整批
                stats["error"] += 1
                print("[SKIP-ERROR] %s: %s: %s" % (row["payment_number"], type(error).__name__, error))
                continue
            missing = sum(1 for line in components if line["tax_status_snapshot"] is None)
            missing_status_lines += missing
            review_amount += totals["tax_review_required"]
            stats["verified"] += 1
            if not args.commit:
                print("[DRY] %s gross=%s components=%d review_required=%s missing_status=%d"
                      % (row["payment_number"], row["gross_amount"], len(components),
                         totals["tax_review_required"], missing))
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
                    (row["id"], row["employee_id"], line["component_code"],
                     line["component_name"], line["amount"], line["quantity"], line["unit"],
                     line["unit_rate"], line["service_date"], line["work_order_id"],
                     "historical_recompute", None, line["daily_report_id"],
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
                  % (row["payment_number"], len(components), totals["tax_review_required"]))
        print("\n=== SUMMARY ===")
        print("mode:", "COMMIT" if args.commit else "DRY-RUN (read only)")
        for key, value in stats.items():
            print("%s: %s" % (key, value))
        print("components with missing tax_status_snapshot:", missing_status_lines)
        print("tax_review_required amount total:", review_amount)
        if not args.commit:
            print("\n已知限制：本工具重算的是「当前算法下的组件比例」，只能保证总额")
            print("与 gross 一致，不能证明与历史生成当日冻结的比例完全相同。")


if __name__ == "__main__":
    main()
