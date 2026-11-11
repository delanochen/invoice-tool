"""Backfill invoice_orders from invoices.service_order_id — idempotent, test-only.

Phase 1 的兼容层脚本，做三件事：
  1) 体检：统计历史关联（全部 / 有效）、历史重复有效发票、尚未回填的旧发票；
  2) 回填：把 invoices.service_order_id 写进 invoice_orders，作废发票也写但标记 released_at；
  3) 复核：回填后再次统计，核对"历史关联总数"与"有效关联数"，以及每条关联的整数分金额。

为什么可以反复执行：回填是 insert ... where not exists，迁移窗口内旧代码继续写单工单发票，
下一次运行会把这些新增的发票补进来。所以冲突检查也要能在同一个窗口里反复跑。
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

APPLY_SQL = """
BEGIN;
SET LOCAL lock_timeout = '5s';
WITH candidates AS (
    SELECT i.id AS invoice_id,
           i.service_order_id AS service_order_id,
           i.status AS status,
           COALESCE((
               SELECT sum(ii.amount) FROM public.invoice_items ii WHERE ii.invoice_id = i.id
           ), 0)::numeric AS item_total
      FROM public.invoices i
     WHERE i.service_order_id IS NOT NULL
)
INSERT INTO public.invoice_orders (invoice_id, service_order_id, amount_cents, released_at, created_at)
SELECT c.invoice_id,
       c.service_order_id,
       round(c.item_total * 100)::bigint,
       CASE WHEN c.status = 'void' THEN now() ELSE NULL END,
       now()
  FROM candidates c
 WHERE NOT EXISTS (
       SELECT 1 FROM public.invoice_orders io
        WHERE io.invoice_id = c.invoice_id AND io.service_order_id = c.service_order_id
 );
COMMIT;
"""

# 状态对齐：迁移窗口内旧代码仍会把发票标成 void，回填必须同时释放这些链接，
# 否则工单的开票资格会被一张已作废的发票继续占着。
RECONCILE_SQL = """
BEGIN;
SET LOCAL lock_timeout = '5s';
UPDATE public.invoice_orders io
   SET released_at = now()
  FROM public.invoices i
 WHERE i.id = io.invoice_id
   AND i.status = 'void'
   AND io.released_at IS NULL;
COMMIT;
"""

CHECKS = {
    'legacy_total': "select count(*) from public.invoices where service_order_id is not null",
    'legacy_void': "select count(*) from public.invoices where service_order_id is not null and status = 'void'",
    'links_total': "select count(*) from public.invoice_orders",
    'links_active': "select count(*) from public.invoice_orders where released_at is null",
    'links_released': "select count(*) from public.invoice_orders where released_at is not null",
    'pending_legacy': """
        select count(*) from public.invoices i
         where i.service_order_id is not null
           and not exists (select 1 from public.invoice_orders io
                            where io.invoice_id = i.id and io.service_order_id = i.service_order_id)
    """,
    'conflict_orders': """
        select count(*) from (
            select service_order_id from public.invoices
             where service_order_id is not null and status != 'void'
             group by service_order_id having count(*) > 1
        ) t
    """,
    'amount_mismatch': """
        select count(*) from public.invoice_orders io
         where io.amount_cents <> round((
             select coalesce(sum(ii.amount), 0) from public.invoice_items ii where ii.invoice_id = io.invoice_id
         )::numeric * 100)::bigint
    """,
    # 已作废但仍占着工单的链接；回填的 --apply 会把它们释放。
    'void_unreleased': """
        select count(*) from public.invoice_orders io
          join public.invoices i on i.id = io.invoice_id
         where i.status = 'void' and io.released_at is null
    """,
}

CONFLICT_DETAIL = """
select i.service_order_id, i.id, i.invoice_number, i.status, i.issue_date
  from public.invoices i
 where i.service_order_id is not null and i.status != 'void'
   and i.service_order_id in (
       select service_order_id from public.invoices
        where service_order_id is not null and status != 'void'
        group by service_order_id having count(*) > 1
   )
 order by i.service_order_id, i.issue_date, i.id
"""


ALLOWED_DATABASES = ('invoice_test', 'invoice_production')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, choices=list(ALLOWED_DATABASES))
    parser.add_argument('--container', default='invoice-tool-postgres')
    parser.add_argument('--apply', action='store_true', help='实际写入；默认只做体检')
    parser.add_argument('--force', action='store_true', help='即使存在历史重复有效发票也继续回填')
    parser.add_argument('--allow-production', action='store_true', help='生产库必须显式带上这个开关')
    args = parser.parse_args()

    if args.database == 'invoice_production' and not args.allow_production:
        raise SystemExit('Refusing production database without --allow-production')

    command = ['docker', 'exec', '-i', args.container, 'psql', '-U', 'invoice_owner', '-d', args.database, '-At']

    identity = subprocess.run(command + ['-c', 'select current_database()'], text=True, capture_output=True, check=True)
    if identity.stdout.strip() != args.database:
        raise SystemExit('Refusing unexpected database: ' + identity.stdout.strip())

    def scalar(sql):
        result = subprocess.run(command + ['-c', sql], text=True, capture_output=True, check=True)
        return int(result.stdout.strip() or 0)

    before = {name: scalar(sql) for name, sql in CHECKS.items()}

    detail = subprocess.run(command + ['-F', '|', '-c', CONFLICT_DETAIL], text=True, capture_output=True, check=True)
    conflicts = [line for line in detail.stdout.splitlines() if line.strip()]
    if conflicts:
        print(json.dumps({'conflicts': conflicts}, ensure_ascii=False))

    report = {'mode': 'apply' if args.apply else 'check', 'before': before, 'conflict_rows': len(conflicts)}

    if args.apply:
        if conflicts and not args.force:
            report['result'] = 'aborted: historical duplicate active invoices (rerun with --force after review)'
            print(json.dumps(report, ensure_ascii=False))
            sys.exit(2)
        applied = subprocess.run(
            command + ['-v', 'ON_ERROR_STOP=1', '-f', '-'],
            input=APPLY_SQL,
            text=True,
            capture_output=True,
        )
        if applied.returncode != 0:
            report['result'] = 'failed: ' + (applied.stderr or '').strip()[:400]
            print(json.dumps(report, ensure_ascii=False))
            sys.exit(1)
        reconciled = subprocess.run(
            command + ['-v', 'ON_ERROR_STOP=1', '-f', '-'],
            input=RECONCILE_SQL,
            text=True,
            capture_output=True,
        )
        if reconciled.returncode != 0:
            report['result'] = 'failed while reconciling void invoices: ' + (reconciled.stderr or '').strip()[:400]
            print(json.dumps(report, ensure_ascii=False))
            sys.exit(1)
        report['after'] = {name: scalar(sql) for name, sql in CHECKS.items()}
        report['result'] = 'applied'

    print(json.dumps(report, ensure_ascii=False))
    if before['conflict_orders'] and not args.apply:
        sys.exit(2)


if __name__ == '__main__':
    main()
