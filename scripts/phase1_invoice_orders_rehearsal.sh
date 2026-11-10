#!/usr/bin/env bash
# Phase 1 演练：在 invoice_phase1_rehearsal（生产库的一致性副本）上验证 invoice_orders 迁移与回填。
#
# 用法（在测试机 root@192.168.7.202 上执行）：
#   bash scripts/phase1_invoice_orders_rehearsal.sh [rehearsal_db_name]
#
# 覆盖：副本生成 → 0319 迁移 → 历史回填 → 迁移窗口内旧代码继续写入 → 冲突拦截
#       → 作废释放 → 重新开票。全部写操作只落在 rehearsal 库，不碰 invoice_production。
set -eu

DB=${1:-invoice_phase1_rehearsal}
SRC_DB=${SRC_DB:-invoice_production}
PG="docker exec -i invoice-tool-postgres"
PSQL="$PG psql -U invoice_owner -d $DB -At -v ON_ERROR_STOP=1"
HERE=$(cd "$(dirname "$0")/.." && pwd)

echo "=== 0) rebuild the rehearsal copy from $SRC_DB"
$PG psql -U invoice_owner -d postgres -At -c "select 1 from pg_database where datname='$DB'" > /tmp/rehearsal_exists.txt
if [ -s /tmp/rehearsal_exists.txt ]; then
    $PG psql -U invoice_owner -d postgres -At -c "drop database $DB with (force)"
fi
$PG psql -U invoice_owner -d postgres -At -c "create database $DB"
docker exec invoice-tool-postgres bash -c "pg_dump -U invoice_owner -Fc -d $SRC_DB | pg_restore -U invoice_owner -d $DB --no-owner --no-privileges"

echo "=== 1) apply 0319 and pre-check"
python3 "$HERE/scripts/upgrade_postgresql_0319.py" --database "$DB"
python3 "$HERE/scripts/backfill_invoice_orders.py" --database "$DB"

echo "=== 2) backfill historical links"
python3 "$HERE/scripts/backfill_invoice_orders.py" --database "$DB" --apply
$PSQL -c "select 'missing_after_backfill=' || count(*) from invoices i where i.service_order_id is not null and not exists (select 1 from invoice_orders io where io.invoice_id=i.id and io.service_order_id=i.service_order_id)"

echo "=== 3) migration window: legacy code writes a new single-order invoice"
$PSQL -c "insert into invoices (invoice_number, client_id, issue_date, due_date, currency, status, created_by, created_at, service_order_id) select 'REHEARSALW-' || so.id, i.client_id, '2026-11-10', '2026-12-10', 'USD', 'submitted', i.created_by, '2026-11-10T00:00:00Z', so.id from service_orders so, (select client_id, created_by from invoices order by id limit 1) i where not exists (select 1 from invoices x where x.service_order_id = so.id) order by so.id limit 1"
python3 "$HERE/scripts/backfill_invoice_orders.py" --database "$DB" --apply

echo "=== 4) conflict guard: second ACTIVE invoice on the same order must abort apply (exit 2)"
$PSQL -c "insert into invoices (invoice_number, client_id, issue_date, due_date, currency, status, created_by, created_at, service_order_id) select 'REHEARSALC', client_id, '2026-11-10','2026-12-10','USD','submitted', created_by,'2026-11-10T00:00:00Z', (select service_order_id from invoices where invoice_number like 'REHEARSALW-%' limit 1) from invoices order by id limit 1"
set +e
python3 "$HERE/scripts/backfill_invoice_orders.py" --database "$DB" --apply
CONFLICT_EXIT=$?
set -e
echo "conflict_exit=$CONFLICT_EXIT (2 = refused as designed)"

echo "=== 5) void the duplicate, backfill releases it, the order becomes reusable"
$PSQL -c "update invoices set status='void' where invoice_number='REHEARSALC'"
python3 "$HERE/scripts/backfill_invoice_orders.py" --database "$DB" --apply
$PSQL -c "select 'total=' || count(*) || ' active=' || count(*) filter (where released_at is null) || ' released=' || count(*) filter (where released_at is not null) from invoice_orders"

echo "=== 6) cleanup the synthetic rows (the rehearsal database keeps the real history)"
$PSQL -c "delete from invoice_orders where invoice_id in (select id from invoices where invoice_number like 'REHEARSAL%')"
$PSQL -c "delete from invoices where invoice_number like 'REHEARSAL%'"
python3 "$HERE/scripts/backfill_invoice_orders.py" --database "$DB" --apply
echo "done: rehearsal database $DB left in place for the next Phase 1 step"
