"""Deploy one verified staging commit to the existing test service, without migrations."""
import argparse,pathlib,subprocess,sys,textwrap
p=argparse.ArgumentParser();p.add_argument('action',choices=['inspect','hold','resume','deploy']);p.add_argument('--commit');p.add_argument('--resume-timer',action='store_true');a=p.parse_args()
ssh=['ssh','-i',str(pathlib.Path.home()/'.ssh/invoice_test_agent'),'-o','BatchMode=yes','root@192.168.7.202','sh','-s']
identity='''
import os,psycopg
u=os.environ['DATABASE_URL']
assert u.rstrip('/').split('/')[-1].split('?',1)[0]=='invoice_test'
with psycopg.connect(u,connect_timeout=10) as c:
 identity=c.execute('select current_database(),current_user').fetchone()
 assert identity==('invoice_test','invoice_test'),identity
 print('Verified test database and role: invoice_test')
'''
script="set -eu\nAPP_DIR=/opt/invoice-tool-test\ncd \"$APP_DIR\"\n"+"docker exec -i invoice-tool-test python - <<'PYIDENTITY'\n"+identity+"\nPYIDENTITY\n"
if a.action=='hold':
 script+='! systemctl is-active --quiet invoice-tool-test-deploy.service\nsystemctl stop invoice-tool-test-deploy.timer\n! systemctl is-active --quiet invoice-tool-test-deploy.service\necho Test deployment timer temporarily held\n'
elif a.action=='resume':
 script+='systemctl start invoice-tool-test-deploy.timer\nsystemctl is-active invoice-tool-test-deploy.timer\n'
elif a.action=='inspect':
 script+='git rev-parse HEAD\nsystemctl is-active invoice-tool-test-deploy.service || true\ndocker inspect invoice-tool-test --format "{{.State.Status}}"\n'
else:
 if not a.commit or len(a.commit)!=40 or any(c not in '0123456789abcdef' for c in a.commit):p.error('Exact 40-character commit required')
 script+='''
[ -z "$(git status --porcelain --untracked-files=no)" ]
! systemctl is-active --quiet invoice-tool-test-deploy.service
timer_was_active=0
if systemctl is-active --quiet invoice-tool-test-deploy.timer; then timer_was_active=1; fi
systemctl stop invoice-tool-test-deploy.timer
mkdir /run/invoice-tool-test-deploy.lock
cleanup() {
 rmdir /run/invoice-tool-test-deploy.lock 2>/dev/null || true
 if [ "$timer_was_active" = 1 ]; then systemctl start invoice-tool-test-deploy.timer; fi
}
trap cleanup EXIT INT TERM
OLD_COMMIT=$(git rev-parse HEAD)
git fetch --quiet origin staging
'''+f'[ "$(git rev-parse origin/staging)" = "{a.commit}" ]\n'+'''
# Structural release: do not run schema upgrades or change Compose configuration.
git diff --exit-code "$OLD_COMMIT" origin/staging -- deploy/docker-compose.test.yml scripts/upgrade_postgresql.py 'scripts/upgrade_postgresql_*.py' migrations
python3 scripts/backup_postgresql.py --container invoice-tool-postgres --database invoice_test --output "/srv/invoice-tool-test/backups/invoice-test-pre-split-$(date +%Y%m%d-%H%M%S).dump"
git merge --ff-only origin/staging
APP_VERSION="$(tr -d '\\r\\n' < VERSION)-test"
export APP_VERSION
docker compose -p invoice-tool-test -f deploy/docker-compose.test.yml up -d --build --no-deps invoice-tool-test
healthy=0
i=0
while [ "$i" -lt 30 ]; do
 if curl -fsS --max-time 5 http://127.0.0.1:8089/ >/dev/null; then healthy=1; break; fi
 i=$((i+1)); sleep 2
done
[ "$healthy" = 1 ]
'''+"docker exec -i invoice-tool-test python - <<'PYIDENTITY'\n"+identity+"\nPYIDENTITY\n"+'''
docker exec -i invoice-tool-test python - <<'PYSMOKE'
import ast,pathlib,app
source=pathlib.Path('/app/app.py').read_text()
names=[n.name for n in ast.parse(source).body if isinstance(n,ast.FunctionDef)]
assert len(names)==len(set(names))
assert app.CUSTOMER_REIMBURSEMENT_STATUS_LABELS['superseded']=='已被替代'
assert 'customer_reimbursement_form' in app.app.view_functions
assert any('revision' in rule.endpoint for rule in app.app.url_map.iter_rules())
with app.app.test_client() as client:
 response=client.get('/login')
 assert response.status_code==200,response.status_code
print('Smoke passed: login, settlement routes, revision routes, status catalog; app.py lines:',len(source.splitlines()))
PYSMOKE
git rev-parse HEAD
docker inspect invoice-tool-test --format '{{.State.Status}} {{.Config.Image}}'
'''
if a.resume_timer:
 script=script.replace('timer_was_active=0','timer_was_active=1')
result=subprocess.run(ssh,input=script.replace('\r\n','\n').encode('utf-8'))
sys.exit(result.returncode)
