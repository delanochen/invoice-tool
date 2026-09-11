import sqlite3
import unittest
from test_expense_on_behalf import ExpenseOnBehalfTest


class MroMergeTest(unittest.TestCase):
    def test_alias_merge_keeps_target_and_amounts_and_is_repeatable(self):
        fixture = ExpenseOnBehalfTest(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row; self.addCleanup(c.close)
        c.executescript('''
          create table projects(id integer primary key,name text,name_key text,project_type text);
          create table invoice_items(id integer primary key,project_id integer,description text,amount real);
          create table expenses(id integer primary key,project_id integer,project text,amount real);
          create table expense_items(id integer primary key,project_id integer,project text,amount real);
          insert into projects values(21,'MRO Supplies配件及耗材费','mro supplies配件及耗材费','invoice');
          insert into projects values(33,'MRO Supplies','mro supplies','invoice');
          insert into projects values(34,'MroSupplies','mrosupplies','expense');
          insert into invoice_items values(1,33,'MRO Supplies',125);
          insert into expense_items values(1,34,'MroSupplies',45);
          insert into expenses values(1,34,'MroSupplies',45);
        ''')
        for _ in range(2): fixture.app.merge_mro_project_aliases(c)
        self.assertIsNone(c.execute('select * from projects where id=33').fetchone())
        self.assertEqual(tuple(c.execute('select project_id,description,amount from invoice_items').fetchone()), (21,'MRO Supplies配件及耗材费',125))
        self.assertEqual(tuple(c.execute('select project_id,project,amount from expense_items').fetchone()), (34,'MRO Supplies配件及耗材费',45))
        self.assertEqual(c.execute('select count(*) from projects').fetchone()[0],2)
        with fixture.app.app.app_context():
            fixture.app.seed_customer_reimbursement_projects(fixture.app.db())
            self.assertFalse(fixture.app.db().execute("select 1 from projects where name='MRO Supplies'").fetchone())

if __name__ == '__main__': unittest.main()
