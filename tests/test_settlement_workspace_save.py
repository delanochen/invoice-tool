"""Exercise the real settlement form route with a transactional in-memory fake.

No app.py import, credentials, database driver, or database connection.
"""
import copy
import inspect
import json
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from flask import Flask, abort, flash, g, jsonify, redirect, request, url_for
from werkzeug.datastructures import MultiDict
from invoice_tool.customer_reimbursements.form_routes import register_settlement_form_routes


class Result:
    def __init__(self, row=None): self.row = row
    def fetchone(self): return self.row
    def fetchall(self): return [] if self.row is None else [self.row]


class MemoryTransaction:
    def __init__(self):
        self.records = {i: dict(id=i, service_order_id=4, series_id=1, status='draft',
            is_current=i == 10, sources_frozen=i == 11, lodging_person_nights=1,
            expense_selection_mode='manual_review', reviewed_by=None, invoice_id=None,
            file_name='settlement.pdf', mro_supplies_total=0, rental_fuel_total=0,
            gross_amount=0, discount_amount=0, settlement_basis='contract') for i in (10, 11)}
        self.links = {10: {7: (373.99, '')}, 11: {7: (373.99, '')}}
        self.items = {i: [dict(worker_name='Test', project_date='2026-10-19',
            auto_expense_sources='{}')] for i in self.records}
        self.commit()
    def execute(self, sql, args=()):
        text = ' '.join(sql.split()).lower()
        if text.startswith('select * from customer_reimbursements'):
            return Result(copy.deepcopy(self.records.get(args[0])))
        if text.startswith('delete from customer_reimbursement_expense_links'):
            self.links[args[0]] = {}
        elif text.startswith('insert into customer_reimbursement_expense_links'):
            self.links[args[0]][args[1]] = (args[5] if args[5] is not None else args[2], args[6])
        elif text.startswith('update customer_reimbursements set lodging_person_nights'):
            self.records[args[-1]].update(lodging_person_nights=args[0], expense_selection_mode='manual_review')
        elif text.startswith('update customer_reimbursements set status'):
            self.records[args[-1]]['status'] = args[0]
        elif text.startswith('select') or text.startswith('delete from customer_reimbursement_expense_ignores'):
            return Result()
        else:
            raise AssertionError('Unexpected SQL: '+text)
        return Result()
    def commit(self): self.saved = copy.deepcopy((self.records, self.links, self.items))
    def rollback(self): self.records, self.links, self.items = copy.deepcopy(self.saved)


class WorkspaceSaveTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'isolated-test'
        self.db = MemoryTransaction()
        self.manage = True
        self.order = dict(id=4, start_date='2026-10-19', order_number='TEST')
        self.rows = Mock(return_value=[dict(worker_name='Test', project_date='2026-10-19')])
        self.merge = Mock(side_effect=lambda rows, *args: rows)
        self.gate = Mock(side_effect=lambda r: None if self.db.links[r['id']][7][0] == 7.47 else ('source', 'wrong amount'))
        self.api = {'db': lambda: self.db, 'now': lambda: '2026-10-19',
                    'merge_approved_expenses_into_customer_reimbursement': self.merge}
        self.patches = [
            patch('invoice_tool.customer_reimbursements.workspace_sources.settlement_expense_candidates', side_effect=self.candidates),
            patch('invoice_tool.customer_reimbursements.workspace_sources._contract_lodging_cap', return_value={'rate': 100, 'version_id': 1}),
        ]
        for p in self.patches: p.start(); self.addCleanup(p.stop)
        def render(_template, **ctx):
            r = ctx['reimbursement']; candidates = ctx['workspace']['other_candidates']
            return jsonify(id=r['id'], status=r['status'], sources=candidates)
        def update(i, rows):
            total = sum(a for a, _ in self.db.links[i].values())
            self.db.records[i]['mro_supplies_total'] = total
            return {'total_amount': total}
        def save_items(i, rows): self.db.items[i] = copy.deepcopy(rows)
        values = dict(
            _merge_root_api=lambda: self.api, login_required=lambda f: f,
            can_view_customer_reimbursement=lambda: True,
            can_manage_customer_reimbursement=lambda: self.manage,
            require_service_order=lambda i: self.order,
            require_service_order_start_date=lambda order: None,
            latest_customer_reimbursement=lambda i: copy.deepcopy(self.db.records[10]),
            ensure_customer_reimbursement_pdf_record=lambda r,o:r,
            db=lambda: self.db, request=request, g=g, abort=abort, flash=flash,
            redirect=redirect, url_for=url_for, json=json, now=lambda:'2026-10-19',
            customer_reimbursement_items_from_form=self.rows,
            save_customer_reimbursement_items=save_items,
            update_customer_reimbursement_totals=update,
            customer_reimbursement_gate_error=self.gate,
            customer_reimbursement_items=lambda i: self.db.items[i],
            customer_reimbursement_linked_invoice=lambda r,i: None,
            resolve_contract_rates=lambda *args: ({k:0 for k in ['standard_rate','transport_rate','public_transport_rate','overtime_rate','holiday_rate','mileage_rate']}, []),
            customer_reimbursement_workspace_data=lambda o,i,rows: {'other_candidates': self.candidates(self.api,o,i)},
            customer_reimbursement_totals=lambda *args: {},
            settlement_versions=lambda *args: [],
            render_template=render, money=str,
        )
        params = {}
        for name in inspect.signature(register_settlement_form_routes).parameters:
            if name == 'app': continue
            key = name.removeprefix('_cb_resolve_')
            value = values.get(key, Mock(return_value=None))
            params[name] = lambda v=value: v
        register_settlement_form_routes(self.app, **params)
        @self.app.before_request
        def user(): g.user = {'id':1,'name':'Test'}
        self.client = self.app.test_client()
    def candidates(self, api, order_id, reimbursement_id):
        amount, reason = self.db.links[reimbursement_id].get(7, (373.99, ''))
        return [dict(expense_item_id=7, amount=373.99, settled_amount=amount,
                     adjustment_reason=reason, selectable=True, selected=7 in self.db.links[reimbursement_id],
                     project_name='Other', status='approved', expense_number='EXP-TEST')]
    def payload(self, amount='7.47', reason='Customer share', action='save', version=10):
        return MultiDict(dict(workspace_sources_present='1', expense_item_id='7',
                         settled_amount_7=amount, adjustment_reason_7=reason,
                         lodging_person_nights='1', action=action, version_id=str(version)))
    def post(self, payload): return self.client.post('/service-orders/4/customer-reimbursement', data=payload)
    def test_save_reload_partial_and_original_expense_unchanged(self):
        response = self.post(self.payload())
        self.assertEqual(response.status_code,302)
        reloaded = self.client.get(response.location).json
        self.assertEqual(reloaded['sources'][0]['settled_amount'],7.47)
        self.assertEqual(reloaded['sources'][0]['amount'],373.99)
        self.assertEqual(self.db.records[10]['mro_supplies_total'],7.47)
    def test_zero_is_saved_and_reloaded(self):
        response = self.post(self.payload('0'))
        self.assertEqual(self.client.get(response.location).json['sources'][0]['settled_amount'],0)
    def test_invalid_amount_or_missing_reason_rolls_back_and_retains_input(self):
        for amount,reason in [('7.47',''),('-1','x'),('374','x'),('NaN','x'),('Infinity','x'),('1e100','x'),('','x')]:
            with self.subTest(amount=amount,reason=reason):
                response=self.post(self.payload(amount,reason))
                self.assertEqual(response.status_code,200)
                self.assertEqual(self.db.links[10][7][0],373.99)
                self.assertEqual(response.json['sources'][0]['posted_settled_amount'],amount)
                self.assertEqual(response.json['sources'][0]['adjustment_reason'],reason)
    def test_later_item_validation_failure_rolls_back_links(self):
        self.rows.side_effect=ValueError('Invalid row')
        self.assertEqual(self.post(self.payload()).status_code,200)
        self.assertEqual(self.db.links[10][7][0],373.99)
    def test_submit_checks_newly_saved_amount(self):
        response=self.post(self.payload(action='submit'))
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.db.records[10]['status'],'submitted')
        self.assertEqual(self.db.links[10][7][0],7.47)
    def test_revision_save_does_not_modify_current_version(self):
        response=self.post(self.payload(version=11))
        self.assertEqual(response.status_code,302)
        self.assertIn('version_id=11',response.location)
        self.assertEqual(self.db.links[10][7][0],373.99)
        self.assertEqual(self.client.get(response.location).json['sources'][0]['settled_amount'],7.47)
        self.merge.assert_called_once()
    def test_legacy_request_keeps_links_and_explicit_empty_clears(self):
        self.assertEqual(self.post({'action':'save'}).status_code,302)
        self.assertEqual(self.db.links[10][7][0],373.99)
        p=self.payload();p.pop('expense_item_id')
        self.assertEqual(self.post(p).status_code,302)
        self.assertEqual(self.db.links[10],{})
    def test_readonly_and_permission_denial_keep_data(self):
        self.manage=False
        self.assertEqual(self.post(self.payload()).status_code,403)
        self.manage=True;self.db.records[10]['status']='approved';self.db.commit()
        self.assertEqual(self.post(self.payload()).status_code,200)
        self.assertEqual(self.db.links[10][7][0],373.99)
    def test_full_amount_does_not_require_reason(self):
        self.assertEqual(self.post(self.payload('373.99','')).status_code,302)
        self.assertEqual(self.db.links[10][7][0],373.99)
    def test_foreign_or_unapproved_source_is_rejected(self):
        p=self.payload();p['expense_item_id']='999';p['settled_amount_999']='1'
        self.assertEqual(self.post(p).status_code,200)
        self.assertEqual(self.db.links[10][7][0],373.99)
        with patch('invoice_tool.customer_reimbursements.workspace_sources.settlement_expense_candidates',return_value=[dict(self.candidates(self.api,4,10)[0],selectable=False)]):
            self.assertEqual(self.post(self.payload()).status_code,200)
            self.assertEqual(self.db.links[10][7][0],373.99)


class WorkspaceTemplateTest(unittest.TestCase):
    def test_rendered_source_controls_belong_to_toolbar_save_form_and_are_editable(self):
        from pathlib import Path
        from html.parser import HTMLParser
        from jinja2 import Environment
        template = (Path(__file__).resolve().parents[1] / "templates/customer_reimbursement_form.html").read_text(encoding="utf-8")
        start = template.index('  <div id="settlementSourceSelectionForm">')
        end = template.index('  <section class="panel settlement-tab-panel" data-settlement-tab-panel="mileage"', start)
        env = Environment(autoescape=True)
        env.filters['money'] = lambda value: f"{value:.2f}"
        row = dict(expense_item_id=7, selected=False, selectable=True, worker_name="Test", expense_id=1,
                   expense_number="EXP", expense_date="2026-10-19", description="Test", project_name="Parking",
                   amount=373.99, status="approved", settled_amount=373.99, adjustment_reason="", settlement_shortfall=0)
        rendered = env.from_string(template[start:end]).render(
            workspace=dict(lodging_rows=[], lodging_candidates=[], other_candidates=[row]),
            reimbursement=dict(lodging_person_nights=1), lodging_range=dict(minimum=1),
            can_edit=True, is_external_user=lambda:False, url_for=lambda *args, **kwargs:'/expense/1')
        class Controls(HTMLParser):
            def __init__(self): super().__init__(); self.inputs=[]
            def handle_starttag(self, tag, attrs):
                if tag == 'input': self.inputs.append(dict(attrs))
        parser = Controls(); parser.feed(rendered)
        controls = {item['name']:item for item in parser.inputs}
        self.assertEqual(controls['workspace_sources_present']['value'],'1')
        for name in ['expense_item_id','settled_amount_7','adjustment_reason_7']:
            self.assertEqual(controls[name]['form'],'customerReimbursementForm')
            self.assertNotIn('readonly',controls[name])
            self.assertNotIn('disabled',controls[name])
        self.assertEqual(controls['settled_amount_7']['value'],'373.99')
        row['posted_settled_amount']='7.47'
        row['adjustment_reason']='Customer "share" <test>'
        rendered = env.from_string(template[start:end]).render(
            workspace=dict(lodging_rows=[], lodging_candidates=[], other_candidates=[row]),
            reimbursement=dict(lodging_person_nights=1), lodging_range=dict(minimum=1),
            can_edit=True, is_external_user=lambda:False, url_for=lambda *args, **kwargs:'/expense/1')
        self.assertIn('value="7.47"',rendered)
        self.assertNotIn('<test>',rendered)


if __name__ == '__main__': unittest.main()
