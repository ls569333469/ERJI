#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${ERJI_PREFLIGHT_CONFIRMED:?Inspect XBOT, TelegramForwarder, host load, Docker and routes first}"
[ "$ERJI_PREFLIGHT_CONFIRMED" = yes ] || exit 1
: "${ERJI_DB_CPUS:?Set inspected CPU budget}"
: "${ERJI_API_CPUS:?Set inspected CPU budget}"
: "${ERJI_WEB_CPUS:?Set inspected CPU budget}"
: "${ERJI_LIVE_CPUS:?Set inspected CPU budget}"
: "${ERJI_JOBS_CPUS:?Set inspected CPU budget}"
: "${ERJI_STREAMS_CPUS:?Set inspected CPU budget}"
export ERJI_DB_CPUS ERJI_API_CPUS ERJI_WEB_CPUS ERJI_LIVE_CPUS ERJI_JOBS_CPUS ERJI_STREAMS_CPUS
compose() { docker compose -f compose.yml -f infra/compose.limits.yml "$@"; }
cd -- "$(dirname -- "$0")/.."
release=$(pwd -P)
case "$release" in /opt/erji-releases/*) ;; *) echo 'Use an /opt/erji-releases/<release> directory'; exit 1;; esac
[ "$(id -u)" = 0 ] || { echo 'Run as server root'; exit 1; }
for executable in docker python3 curl; do command -v "$executable" >/dev/null || { echo "Missing prerequisite: $executable"; exit 1; }; done
docker compose version
docker info --format '{{.ServerVersion}}'
if [ -n "$(docker ps -aq --filter label=com.docker.compose.project=erji)" ] || [ -e /opt/erji-current ] || [ -e /opt/erji-shared ]; then
 echo 'Existing ERJI deployment found; requires update/backup workflow. Nothing changed.'; exit 1
fi
if docker volume inspect erji_pgdata >/dev/null 2>&1; then
 echo 'Existing ERJI database volume found; refusing first-deploy migration.'; exit 1
fi
python3 - <<'PY'
import socket,shutil,hashlib,json
from pathlib import Path
s=socket.socket();s.bind(('127.0.0.1',18770));s.close()
values={k:int(v.split()[0]) for k,v in (l.split(':',1) for l in Path('/proc/meminfo').read_text().splitlines())}
reserve=max(1024*1024,values['MemTotal']//5)
if values['MemAvailable'] < 3200*1024+reserve:raise SystemExit('Insufficient available RAM for draft 3200 MiB service caps plus host reserve; revise budget before startup')
if shutil.disk_usage('/opt').free < 5*1024**3: raise SystemExit('At least 5 GiB free disk required for build and database')
m=json.loads(Path('deployment-manifest.json').read_text())
for row in m['files']:
 p=Path(row['path'])
 if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:
  raise SystemExit('Release checksum failed: '+row['path'])
print('Release verified:',m['base_commit'],len(m['files']),'files')
PY
for keyfile in CMC.txt bykaranteli.txt; do
 [ -s "/opt/erji-incoming/$keyfile" ] || { echo "Missing separately uploaded credential: $keyfile"; exit 1; }
done
printf 'Host resources before deployment:
'
free -m
df -h /opt
install -d -m 700 /opt/erji-shared
install -d -m 500 -o 10001 -g 10001 /opt/erji-shared/secrets
python3 - <<'PY'
from pathlib import Path
import secrets,os
shared=Path('/opt/erji-shared')
p=shared/'secrets/admin-token.txt'
p.write_text(secrets.token_urlsafe(48));os.chown(p,10001,10001);p.chmod(0o400)
e=shared/'.env'
e.write_text('ERJI_DB_PASSWORD='+secrets.token_urlsafe(48)+'\nERJI_SECRET_DIR=/opt/erji-shared/secrets\nERJI_WEB_ORIGIN=http://127.0.0.1:18770\n')
with e.open('a') as f:
 for key in ('DB','API','WEB','LIVE','JOBS','STREAMS'):
  f.write('ERJI_'+key+'_CPUS='+os.environ['ERJI_'+key+'_CPUS']+'\n')
e.chmod(0o600)
PY
for keyfile in CMC.txt bykaranteli.txt; do
 source="/opt/erji-incoming/$keyfile"
 [ -s "$source" ] || { echo "Missing separately uploaded credential: $keyfile"; exit 1; }
 install -o 10001 -g 10001 -m 400 "$source" "/opt/erji-shared/secrets/$keyfile"
done
ln -s /opt/erji-shared/.env .env
# Builds finish before database migration or worker startup. No XBOT service is touched.
compose config --quiet
compose --parallel 1 build
compose up -d db
# One-off migration must succeed before the API starts.
compose run --rm migrate
# Limit this first deployment to data services; notify is opt-in only.
compose up -d api web worker-live worker-jobs worker-streams
for attempt in $(seq 1 90); do
 if curl -fsS --max-time 5 http://127.0.0.1:18770/erji/v1/health >/dev/null; then break; fi
 sleep 2
done
python3 - <<'PY'
import json,urllib.request,urllib.error,http.cookiejar
from pathlib import Path
origin='http://127.0.0.1:18770'
base=origin+'/erji'
def check_unauth(path):
 try: urllib.request.urlopen(base+path,timeout=10)
 except urllib.error.HTTPError as e:
  assert e.code==401,(path,e.code);return
 raise AssertionError('Unauthenticated endpoint accessible: '+path)
check_unauth('/v1/markets')
check_unauth('/v1/stream')
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
key=Path('/opt/erji-shared/secrets/admin-token.txt').read_text().strip()
req=urllib.request.Request(base+'/v1/auth/login',data=json.dumps({'token':key}).encode(),headers={'Content-Type':'application/json','X-ERJI-Client':'local-ui','Origin':origin})
with opener.open(req,timeout=15) as r: assert json.load(r)['authenticated']
for path in ['/v1/health','/v1/markets','/v1/signals?view=stages','/charting_library/charting_library.standalone.js']:
 with opener.open(base+path,timeout=60) as r:
  assert r.status==200;print('HTTP 200',path)
with opener.open(base+'/v1/stream',timeout=25) as r:
 assert r.status==200 and 'text/event-stream' in r.headers.get('Content-Type','')
 assert r.readline();print('Authenticated SSE received')
PY
# Point at private accepted release; public Nginx acceptance is a separate step.
ln -s "$release" /opt/erji-current
compose ps
printf '
ERJI deployed privately: http://127.0.0.1:18770/erji/
'
printf 'Use SSH tunnel for access. Admin token file: /opt/erji-shared/secrets/admin-token.txt
'
printf 'Nginx /erji/ route and HTTPS origin must be enabled and verified after private acceptance.
'
