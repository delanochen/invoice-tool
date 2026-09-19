import ast
import json
import sqlite3
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from scripts.repair_sqlite_integrity_20260919 import repair, table_rows
from settlement_review import _migrate_historical_auto_sources


def fixture():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
      create table users(id integer primary key);
      create table invoices(id integer primary key,paid_at text);
      create table invoice_items(id integer primary key,invoice_id integer references invoices(id) on delete cascade,amount real);
      create table audit_logs(id integer primary key,user_id integer references users(id) on delete set null,user_name text,summary text,created_at text);
      create table settings(key text primary key,value text);
      create table countries(code text primary key,created_at text);
      create table field_photos(id integer primary key,source text,watermark_source text,watermark_at text,captured_at text,capture_date text,timezone_name text,relative_path text);
      create table customer_reimbursement_expense_links(id integer primary key,selected_by integer,selected_at text);
      insert into invoices values(1,'2026-07-03');
      insert into invoice_items values(1,1,123.45),(20,3,100);
      insert into audit_logs values(53,5,'Historical user','keep audit text','2026-06-19T00:03:38-05:00');
      insert into settings values('settlement_expense_links_migration_v1','2026-09-19 14:54:26');
      insert into customer_reimbursement_expense_links values(1,null,'2026-09-19 14:54:26');
      insert into field_photos values(1,'camera','system','','2026-09-04T14:19:12.075-05:00','2026-09-04','America/Chicago','unchanged.jpg');
    ''')
    c.execute("insert into countries values('CA','2026-07-03 03:51:30')")
    c.execute('insert into audit_logs values(178,null,?,?,?)',('Admin',"INSERT INTO countries VALUES ('CA', datetime('now'));",'2026-07-02T22:51:30-05:00'))
    c.commit()
    c.execute('pragma foreign_keys=on')
    return c


class IntegrityRepairTest(unittest.TestCase):
    def test_repair_preserves_money_dates_files_and_audit_history(self):
        c = fixture()
        self.addCleanup(c.close)
        c.execute('begin immediate')
        result = repair(c)
        self.assertEqual(result['changed_rows'],5)
        self.assertEqual(c.execute('select amount from invoice_items where id=1').fetchone()[0],123.45)
        self.assertEqual(c.execute('select paid_at from invoices').fetchone()[0],'2026-07-03')
        audit = c.execute('select * from audit_logs where id=53').fetchone()
        self.assertIsNone(audit['user_id'])
        self.assertEqual(audit['user_name'],'Historical user')
        self.assertEqual(audit['summary'],'keep audit text')
        archive = c.execute("select original_json from data_repair_archive where table_name='invoice_items'").fetchone()
        self.assertEqual(json.loads(archive[0])['amount'],100)
        photo = c.execute('select * from field_photos').fetchone()
        self.assertEqual(photo['watermark_at'],photo['captured_at'])
        self.assertEqual(photo['relative_path'],'unchanged.jpg')
        self.assertEqual(photo['capture_date'],'2026-09-04')
        for table,col in [('countries','created_at'),('customer_reimbursement_expense_links','selected_at')]:
            self.assertIsNotNone(datetime.fromisoformat(c.execute(f'select {col} from {table}').fetchone()[0]).tzinfo)
        self.assertEqual(repair(c)['changed_rows'],0)
        self.assertEqual(c.execute('select count(*) from data_repair_archive').fetchone()[0],5)

    def test_unknown_orphan_aborts_before_any_mutation(self):
        c = fixture()
        self.addCleanup(c.close)
        c.execute('pragma foreign_keys=off')
        c.execute('insert into invoice_items values(999,999,77)')
        c.commit()
        before = table_rows(c)
        with self.assertRaisesRegex(ValueError,'Unreviewed'):
            repair(c)
        self.assertEqual(table_rows(c),before)

    def test_unknown_timezone_aborts_without_guessing(self):
        c = fixture()
        self.addCleanup(c.close)
        c.execute("update countries set created_at='2026-07-03 04:51:30'")
        before = table_rows(c)
        with self.assertRaisesRegex(ValueError,'evidence'):
            repair(c)
        self.assertEqual(table_rows(c),before)

    def test_unknown_watermark_aborts_without_guessing(self):
        c = fixture()
        self.addCleanup(c.close)
        c.execute("update field_photos set source='file'")
        before = table_rows(c)
        with self.assertRaisesRegex(ValueError,'watermark'):
            repair(c)
        self.assertEqual(table_rows(c),before)

    def test_application_connections_enforce_foreign_keys(self):
        tree = ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
        func = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='db')
        with tempfile.TemporaryDirectory() as root:
            import os
            scope = {'os':os,'sqlite3':sqlite3,'g':{}}
            class State(dict):
                def __getattr__(self,k): return self[k]
                def __setattr__(self,k,v): self[k]=v
            scope['g']=State()
            for node in ast.walk(func):
                if isinstance(node,ast.Name) and node.id.endswith('_DIR'):
                    scope[node.id]=root
            scope['DB_PATH']=str(Path(root)/'test.db')
            exec(compile(ast.Module(body=[func],type_ignores=[]),'app.py','exec'),scope)
            c=scope['db']()
            try:
                self.assertEqual(c.execute('pragma foreign_keys').fetchone()[0],1)
                c.executescript('create table parent(id integer primary key); create table child(pid integer references parent(id) on delete cascade); insert into parent values(1); insert into child values(1);')
                c.execute('delete from parent where id=1')
                self.assertEqual(c.execute('select count(*) from child').fetchone()[0],0)
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute('insert into child values(999)')
            finally:
                c.close()

    def test_new_historical_selection_migration_writes_aware_utc(self):
        c=sqlite3.connect(':memory:')
        self.addCleanup(c.close)
        c.row_factory=sqlite3.Row
        c.executescript('''create table settings(key text primary key,value text);
        create table customer_reimbursement_items(customer_reimbursement_id integer,auto_expense_sources text);
        create table expense_items(id integer,expense_id integer,line_key text,amount real,project text,sort_order integer);
        create table expenses(id integer,status text);
        create table customer_reimbursement_expense_links(customer_reimbursement_id integer,expense_item_id integer,amount_snapshot real,project_snapshot text,expense_status_snapshot text,selected_by integer,selected_at text);
        insert into expenses values(1,'approved'); insert into expense_items values(2,1,'key',12,'MRO',1);''')
        c.execute('insert into customer_reimbursement_items values(1,?)',(json.dumps({'mro':[{'expense_id':1,'line_key':'key'}]}),))
        _migrate_historical_auto_sources(c)
        for value in [c.execute('select selected_at from customer_reimbursement_expense_links').fetchone()[0],c.execute('select value from settings').fetchone()[0]]:
            self.assertIsNotNone(datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo)
        _migrate_historical_auto_sources(c)
        self.assertEqual(c.execute('select count(*) from customer_reimbursement_expense_links').fetchone()[0],1)


if __name__=='__main__': unittest.main()
