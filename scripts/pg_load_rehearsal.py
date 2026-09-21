"""Bounded real-HTTP load test; disposable acceptance DB and internal host only."""
import concurrent.futures
import http.cookiejar
import json
import os
from pathlib import Path
import statistics
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(seconds=60, clients=4):
    if urllib.parse.urlsplit(os.environ['DATABASE_URL']).path not in (
            '/invoice_acceptance_rehearsal', '/invoice_current_acceptance_rehearsal'):
        raise ValueError('An explicitly allowed disposable acceptance database is required')
    if not (1 <= seconds <= 120 and 1 <= clients <= 6):
        raise ValueError('Load must be bounded to 120 seconds and six clients')
    import app as m
    if not Path(m.DATA_DIR).resolve().is_relative_to('/scratch'):
        raise ValueError('File root must be inside the isolated /scratch directory')
    base = 'http://invoice-pg-browser:8000'
    prefix = 'load-' + uuid.uuid4().hex[:12]
    with m.app.app_context():
        c = m.db()
        from werkzeug.security import generate_password_hash
        email = prefix+'@test.invalid'
        uid = c.execute('insert into users(name,email,password_hash,role,is_active,created_at) values(?,?,?,\'employee\',1,?)',
                        (prefix,email,generate_password_hash(os.environ['REHEARSAL_PASSWORD']),m.now())).lastrowid
        order = c.execute('''insert into service_orders
            (order_number,client_name,site_address,client_order_number,start_date,created_by,created_at)
            values(?,?,?,?,?,?,?)''', (prefix, 'Load fixture', 'Isolated', prefix, '2026-09-01', uid, m.now())).lastrowid
        project = c.execute("select id from projects where name='Accommodation/Lodging' and is_active=1 order by id limit 1").fetchone()[0]
        c.commit()
    barrier = threading.Barrier(clients)
    def worker(index):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def request(path, data=None):
            start = time.monotonic()
            body = urllib.parse.urlencode(data).encode() if data is not None else None
            with opener.open(base+path, body, timeout=30) as response:
                response.read()
                if urllib.parse.urlsplit(response.url).path == '/login':
                    raise RuntimeError('Unexpected login redirect')
                return (time.monotonic()-start)*1000, response.status
        request('/login', {'email':email,'password':os.environ['REHEARSAL_PASSWORD']})
        barrier.wait(timeout=30)
        deadline = time.monotonic()+seconds
        timings=[]; errors=[]; writes=0; iteration=0
        while time.monotonic()<deadline:
            try:
                if iteration % 5 == 0:
                    token=f'{prefix}-{index}-{iteration}'
                    form={'save_token':token,'action':'save','project_id':str(project),'item_amount':'12.34',
                          'item_description':token,'item_line_key':'load-line','expense_date':'2026-09-11',
                          'beneficiary_id':str(uid)}
                    for _ in range(2):
                        ms,status=request(f'/service-orders/{order}/expenses/new',form)
                        timings.append(ms)
                        if status != 200: raise RuntimeError('Unexpected post redirect status '+str(status))
                    writes += 1
                else:
                    path=(f'/service-orders/{order}', '/reports/expenses', '/service-orders')[iteration%3]
                    ms,status=request(path)
                    timings.append(ms)
                    if status != 200: raise RuntimeError('Unexpected read status '+str(status))
            except Exception as error:
                errors.append(type(error).__name__+': '+str(error)[:100])
            iteration += 1
            time.sleep(.2)  # bounded workload; never saturate the production host
        return {'timings':timings,'errors':errors,'writes':writes}
    started=time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as pool:
        results=list(pool.map(worker,range(clients)))
    timings=sorted(ms for r in results for ms in r['timings'])
    expected=sum(r['writes'] for r in results)
    with m.app.app_context():
        actual=m.db().execute('select count(*) from expenses where service_order_id=?',(order,)).fetchone()[0]
        wrong=m.db().execute('select count(*) from expenses where service_order_id=? and amount != 12.34',(order,)).fetchone()[0]
    errors=[e for r in results for e in r['errors']]
    result={'seconds':round(time.monotonic()-started,2),'clients':clients,'http_requests':len(timings),
            'p50_ms':round(statistics.median(timings),1) if timings else None,
            'p95_ms':round(timings[min(len(timings)-1,int(len(timings)*.95))],1) if timings else None,
            'max_ms':round(max(timings),1) if timings else None,'errors':errors,
            'expected_expenses':expected,'actual_expenses':actual,'wrong_amount_rows':wrong}
    print(json.dumps(result))
    if errors or actual != expected or wrong: raise SystemExit(1)


if __name__ == '__main__': run()
