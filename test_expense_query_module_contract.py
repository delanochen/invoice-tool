"""Expense list totals, filters and participant visibility in invoice_test."""
import unittest
from flask import template_rendered
import app as module
import test_expense_on_behalf as fixtures


class ExpenseQueryContractTest(unittest.TestCase):
    def fixture(self):
        fixture=fixtures.ExpenseOnBehalfTest()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        first=fixture.create()
        second=fixture.create(item_amount='90')
        app=fixture.app
        with app.app.app_context():
            app.db().execute('insert into expense_items (expense_id,line_key,project_id,project,amount,description,sort_order) values (?,?,?,?,?,?,?)',(first['id'],'extra-line',fixture.project,'Accommodation/Lodging',20,'Synthetic extra',1))
            app.db().execute("update expenses set amount=70,status='approved',payout_status='pending' where id=?",(first['id'],))
            app.db().execute("update expenses set status='approved',payout_status='paid',expense_date='2026-09-02' where id=?",(second['id'],))
            app.db().commit()
        return fixture,first['id'],second['id']

    def page(self,fixture,url):
        contexts=[]
        def record(sender,template,context,**extra):
            contexts.append(context)
        with template_rendered.connected_to(record,fixture.app.app):
            response=fixture.http.get(url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(contexts),1)
        return contexts[0]

    def test_original_get_endpoints(self):
        expected={'expense_query':'/reports/expenses','expense_processing':'/expense-processing'}
        for name,path in expected.items():
            rules=[r for r in module.app.url_map.iter_rules() if r.endpoint==name]
            self.assertEqual(len(rules),1)
            self.assertEqual(rules[0].rule,path)
            self.assertEqual(rules[0].methods,{'GET','HEAD','OPTIONS'})
            self.assertIs(module.app.view_functions[name],getattr(module,name))

    def test_line_totals_and_query_filters_do_not_double_count(self):
        fixture,first,second=self.fixture()
        fixture.login('Manager')
        context=self.page(fixture,'/reports/expenses')
        self.assertEqual(len(context['rows']),3)
        self.assertEqual(float(context['total']),70)
        checks=[('payout_status=pending',2,70),('payout_status=paid',1,0),('date_from=2026-09-02',1,0),('date_to=2026-09-01',2,70),('project_name=missing',0,0),('order_number=SO-DELEGATE',3,70),('order_number=missing',0,0),('q=Synthetic+extra',1,20)]
        for query,count,total in checks:
            with self.subTest(query=query):
                context=self.page(fixture,'/reports/expenses?'+query)
                self.assertEqual(len(context['rows']),count)
                self.assertEqual(float(context['total']),total)
        context=self.page(fixture,f'/reports/expenses?person_id={fixture.people["Beneficiary"]}')
        self.assertEqual(len(context['rows']),3)
        context=self.page(fixture,f'/reports/expenses?person_id={fixture.people["Submitter"]}')
        self.assertEqual(len(context['rows']),0)

    def test_queue_totals_keep_accessible_scope_and_priority(self):
        fixture,first,second=self.fixture()
        fixture.login('Manager')
        context=self.page(fixture,'/expense-processing')
        self.assertEqual(context['rows'][0]['id'],first)
        self.assertEqual(float(context['pending_total']),70)
        self.assertEqual(float(context['paid_total']),90)
        context=self.page(fixture,'/expense-processing?q=missing')
        self.assertEqual(len(context['rows']),0)
        self.assertEqual(float(context['pending_total']),70)
        self.assertEqual(float(context['paid_total']),90)

    def test_visibility_applies_to_rows_totals_and_filter_options(self):
        fixture,first,second=self.fixture()
        for name,visible in (('Submitter',True),('Beneficiary',True),('Unrelated',False)):
            fixture.login(name)
            context=self.page(fixture,'/reports/expenses')
            self.assertEqual(len(context['rows']),3 if visible else 0)
            self.assertEqual(float(context['total']),70 if visible else 0)
            self.assertEqual({r['id'] for r in context['people']},{fixture.people['Beneficiary']} if visible else set())
            self.assertEqual(len(context['project_options']),1 if visible else 0)
            queue=self.page(fixture,'/expense-processing')
            self.assertEqual(len(queue['rows']),2 if visible else 0)
            self.assertEqual(float(queue['pending_total']),70 if visible else 0)
            self.assertEqual(float(queue['paid_total']),90 if visible else 0)
