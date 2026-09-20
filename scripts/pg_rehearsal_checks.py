"""Business differential checks on isolated data only; never sends email/API calls."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import traceback
import hashlib
from io import BytesIO
from collections import Counter
from datetime import date
from decimal import Decimal

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def normalized(value):
    if isinstance(value,dict): return {str(k):normalized(v) for k,v in sorted(value.items(),key=lambda x:str(x[0]))}
    if isinstance(value,(list,tuple)): return [normalized(v) for v in value]
    if isinstance(value,set): return sorted(normalized(v) for v in value)
    if isinstance(value,(date,)): return value.isoformat()
    if isinstance(value,(float,Decimal)): return round(float(value),8)
    if hasattr(value,'keys'): return normalized(dict(value))
    return value


def run():
    # Import with PG configured: performs schema verification, not SQLite startup migrations.
    import app as m
    from flask import g
    from profitability import build_profit_lines
    m.app.config.update(TESTING=True)
    m.SHARED_PHOTOS_DIR='/scratch/reference-photos'
    pg_url=os.environ['DATABASE_URL']
    assert 'rehearsal' in pg_url
    source='/snapshot/source.sqlite3'
    baseline='/scratch/sqlite-baseline.db'
    shutil.copyfile(source,baseline)
    m.DB_PATH=baseline
    output={}
    for backend in ('sqlite','postgresql'):
        os.environ['DATABASE_URL']=pg_url if backend=='postgresql' else ''
        failures=[]
        data={}
        with m.app.test_request_context('/'):
            g.user=m.db().execute("select * from users where role='admin' order by id limit 1").fetchone()
            user_id=g.user['id']
            def check(key,fn):
                try: data[key]=normalized(fn())
                except Exception as exc:
                    m.db().rollback()
                    failures.append({'check':key,'error':str(exc),'trace':traceback.format_exc()})
            invoices=[r[0] for r in m.db().execute('select id from invoices order by id')]
            orders=[r[0] for r in m.db().execute('select id from service_orders order by id')]
            reimbursements=[r[0] for r in m.db().execute('select id from customer_reimbursements order by id')]
            check('invoice_totals',lambda:{i:m.invoice_totals(i) for i in invoices})
            check('settlement_totals',lambda:{i:m.customer_reimbursement_totals(m.customer_reimbursement_items(i)) for i in reimbursements})
            check('order_mro',lambda:{i:m.approved_mro_supplies_total(i) for i in orders})
            check('order_person_days',lambda:{i:m.customer_reimbursement_person_days(i) for i in orders})
            check('profit_lines',lambda:build_profit_lines(vars(m),'2026-01-01','2026-12-31'))
            from flask import url_for
            attachment_paths=[]
            for table,endpoint in [('invoice_attachments','preview_attachment'),('expense_attachments','preview_expense_attachment'),
                    ('service_report_attachments','preview_report_attachment'),('user_attachments','preview_user_attachment'),
                    ('contract_attachments','preview_contract_attachment'),('company_attachments','preview_company_attachment'),
                    ('customer_reimbursement_attachments','preview_customer_reimbursement_attachment')]:
                attachment_paths += [url_for(endpoint,attachment_id=r[0]) for r in m.db().execute('select id from '+table+' order by id limit 3')]
            attachment_paths += [url_for('field_photo_preview',photo_id=r[0]) for r in m.db().execute('select id from field_photos order by id limit 5')]
            for detail in (False,True):
                check('payroll_'+str(detail),lambda detail=detail:m.payroll_rows_for_range(date(2026,1,1),date(2026,12,31),date(2027,1,15),detail=detail))
        client=m.app.test_client()
        with client.session_transaction() as session: session['user_id']=user_id
        paths=['/','/workspace','/users','/clients','/contracts','/buyers','/projects','/invoices','/expenses',
               '/reports/invoices','/reports/service-orders','/reports/service-reports','/reports/buyers',
               '/reports/customer-reimbursements','/reports/payroll','/reports/payroll-details',
               '/reports/user-certificates','/service-orders','/settings/database','/field/',
               '/api/field/session','/api/field/photos','/field/photos','/field/repairs',
               '/ai-assistant','/ai-daily-report/drafts','/api/ai/daily-report/service-orders','/api/ai/daily-report/staff']
        paths += ['/service-orders/'+str(i) for i in orders]
        paths += ['/invoices/'+str(i) for i in invoices]
        with sqlite3.connect(source) as c:
            paths += ['/expenses/'+str(r[0]) for r in c.execute('select id from expenses')]
        # Add report endpoints discovered from the real route registry.
        paths += [str(r) for r in m.app.url_map.iter_rules() if '<' not in str(r) and 'GET' in r.methods and str(r).startswith('/reports/')]
        statuses={}
        adapter=m.app.url_map.bind('localhost')
        for path in dict.fromkeys(paths):
            try: adapter.match(path,method='GET')
            except Exception: continue
            try:
                response=client.get(path)
                statuses[path]=response.status_code
                if response.status_code>=500: failures.append({'path':path,'status':response.status_code})
            except Exception as exc:
                statuses[path]=500
                failures.append({'path':path,'error':str(exc),'trace':traceback.format_exc()})
        binary={}
        for path in attachment_paths:
            response=client.get(path)
            binary[path]={'status':response.status_code,'sha256':hashlib.sha256(response.data).hexdigest() if response.status_code==200 else None}
            response.close()
        data['attachment_previews']=binary
        from openpyxl import load_workbook
        exports={}
        for i in reimbursements:
            path='/customer-reimbursements/'+str(i)+'/download.xlsx'
            response=client.get(path)
            if response.status_code!=200:
                failures.append({'path':path,'status':response.status_code}); continue
            book=load_workbook(BytesIO(response.data),read_only=True,data_only=True)
            exports[path]=normalized({sheet.title:list(sheet.values) for sheet in book})
            book.close(); response.close()
        data['settlement_excel']=exports
        from pypdf import PdfReader
        response=client.get('/invoices/'+str(invoices[0])+'/export-pdf')
        if response.status_code==200:
            data['invoice_pdf_text']=[page.extract_text() for page in PdfReader(BytesIO(response.data)).pages]
        else: failures.append({'check':'invoice_pdf','status':response.status_code})
        response.close()
        output[backend]={'business':data,'statuses':statuses,'failures':failures}
    diffs=[]
    for key in output['sqlite']['business']:
        if output['sqlite']['business'][key]!=output['postgresql']['business'].get(key): diffs.append(key)
    status_diff={p:[s,output['postgresql']['statuses'].get(p)] for p,s in output['sqlite']['statuses'].items() if s!=output['postgresql']['statuses'].get(p)}
    output['business_differences']=diffs
    output['status_differences']=status_diff
    # Detailed financial results remain in the private rehearsal directory.
    Path('/scratch/differential-results.json').write_text(json.dumps(output,ensure_ascii=True,indent=2))
    print(json.dumps({'business_differences':diffs,'status_differences':status_diff,
        'failures':{k:v['failures'] for k,v in output.items() if k in ('sqlite','postgresql')},
        'paths':len(output['sqlite']['statuses']),
        'status_counts':dict(Counter(output['postgresql']['statuses'].values())),
        'attachment_status_counts':dict(Counter(v['status'] for v in output['postgresql']['business']['attachment_previews'].values())),
        'settlement_excel_exports':len(output['postgresql']['business']['settlement_excel'])},ensure_ascii=True))
    return not diffs and not status_diff and not output['postgresql']['failures']

if __name__=='__main__': raise SystemExit(0 if run() else 1)
