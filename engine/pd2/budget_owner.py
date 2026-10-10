"""Loopback-only, authenticated quota owner. No provider traffic in this server.

Every producer uses the same permits; no network wait rewrites the DB ledger.
The listener plus a held PG owner lock prevents split ownership. On recovery we
recover a timestamped checkpoint with bounded reservations. Unknown checkpoints
wait the longest window; real bans are always retained.
"""
import json
import os
import secrets
import threading
import time
import urllib.request
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from sqlalchemy import text
from engine.db import Session, engine
from engine.models import UserPreference
from engine.pd2 import p36_budget as budget

PORT = int(os.environ.get('ERJI_BUDGET_PORT','18772'))
HOST=os.environ.get('ERJI_BUDGET_HOST','127.0.0.1')
BIND=os.environ.get('ERJI_BUDGET_BIND','127.0.0.1')
TOKEN = Path(os.environ.get('ERJI_BUDGET_TOKEN_FILE',str(Path(__file__).resolve().parents[2] / '.runtime' / 'budget-owner.token')))


def call(name, args):
    token = TOKEN.read_text(encoding='ascii').strip()
    body = json.dumps({'op': name, 'args': args}).encode()
    request = urllib.request.Request(f'http://{HOST}:{PORT}/quota', data=body,
        headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
    # Explicitly bypass environment proxies for this local control endpoint.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10) as response:
        result = json.load(response)
    if result.get('error'):
        raise budget.QueueDeadlineExceeded(result['error'])
    return result.get('result')


class Store:
    def __init__(self, payload):
        self.lock = threading.RLock()
        self.payload = payload
        self.healthy_until = time.monotonic()+15


    def flush(self):
        captured=time.monotonic();snapshot_at=budget.now_ms()
        with self.lock:
            payload = json.loads(json.dumps(self.payload))
            payload['checkpoint_at']=snapshot_at
        with Session.begin() as session:
            budget.transaction(session)
            session.merge(UserPreference(key=budget.KEY, updated_at=snapshot_at, payload=payload))
        self.healthy_until = captured+15


def recover(payload,checkpoint_at,now):
    """Reserve bounded unknown sends since a fresh checkpoint; never erase bans.

    The owner stops admitting after 15 seconds without a checkpoint. At most
    that interval plus already admitted slots can be absent from the ledger.
    Old/corrupt/unversioned checkpoints cannot prove this and wait a full window.
    """
    trusted=isinstance(payload,dict) and payload.get('version')==budget.VERSION and payload.get('checkpoint_at')==checkpoint_at and isinstance(checkpoint_at,int) and 0<=now-checkpoint_at<=15000
    if trusted:
        try:
            p=budget.clean(payload,now)
            age=now-checkpoint_at
            maxima={'um':40,'spot':250,'spot_raw':1,'statistics':1,'funding':1,'archive':1}
            family={'spot_raw':'spot',**{k:k for k in maxima if k!='spot_raw'}}
            cost={k:math.ceil(age*cap/window)+budget.SLOTS[family[k]]*maxima[k] for k,(cap,window) in budget.LIMITS.items()}
            seq=p.get('seq',0)+1;p['seq']=seq
            p['entries'].append({'id':'owner-recovery:'+str(now),'at':now,'seq':seq,'cost':cost,'lane':'repair','sent':True})
            # An unsent persisted reservation may have been sent before the crash.
            for entry in p['entries']:entry['sent']=True
            p['pending']=[]
            p['recovery']={'reason':'fresh_checkpoint_with_bounded_unknown_exposure','checkpoint_at':checkpoint_at,'reserved':cost,'at':now}
            return p
        except (KeyError,TypeError,ValueError):trusted=False
    p=payload if isinstance(payload,dict) else {}
    # Retain known cooldowns even if other bookkeeping is corrupt.
    recovered={'version':budget.VERSION,'entries':[],'leases':{},'pending':[],'buckets':{},'served':{},'events':[],
        'migration_until':max(now+300000,p.get('migration_until',0)),
        'legacy_blocked_until':p.get('legacy_blocked_until',0),'binance_ban_until':p.get('binance_ban_until',0),
        'recovery':{'reason':'unknown_or_stale_checkpoint','checkpoint_at':checkpoint_at,'at':now}}
    for k,b in p.get('buckets',{}).items():
        if isinstance(b,dict):recovered['buckets'][k]={'blocked_until':b.get('blocked_until',0)}
    return recovered



def serve():
    # A database identity lock supplements the OS listener. Do not retain a
    # transaction: one dedicated connection fences the owner's entire lifetime.
    connection = engine.connect().execution_options(isolation_level='AUTOCOMMIT')
    acquired = connection.execute(text("SELECT pg_try_advisory_lock(hashtext(current_schema() || 'p36-memory-owner'))")).scalar()
    if not acquired:
        connection.close()
        raise RuntimeError('budget_owner_already_running')
    token = secrets.token_hex(32)
    pid=connection.execute(text('SELECT pg_backend_pid()')).scalar()
    if BIND!='127.0.0.1':
        # Compose mounts a shared private token. Only local startup generates it.
        token=TOKEN.read_text(encoding='ascii').strip()
        if len(token)<32:raise RuntimeError('budget_shared_token_required')
    with Session() as session:
        row = session.get(UserPreference, budget.KEY)
        payload = recover(row.payload if row else budget.initial(session,budget.now_ms()),row.updated_at if row else None,budget.now_ms())
    store = Store(payload)
    budget._STORE = store
    flush_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            if self.path!='/health' or time.monotonic()>store.healthy_until:self.send_error(503);return
            self.send_response(200);self.end_headers();self.wfile.write(b'{"status":"ok"}')
        def do_POST(self):
            if self.path != '/quota' or not secrets.compare_digest(self.headers.get('Authorization',''), 'Bearer '+token):
                self.send_error(403); return
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 65536:
                self.send_error(413); return
            try:
                data = json.loads(self.rfile.read(size))
                op, args = data['op'], data['args']
                if op not in ('admit','sent','feedback','release'):
                    raise ValueError('unsupported_operation')
                if op in ('admit','sent') and time.monotonic() > store.healthy_until:
                    raise RuntimeError('owner_checkpoint_unavailable')
                value = getattr(budget, op)(*args)
                # Persist bans before acknowledging them. All other checkpoints
                # are bounded to once per five seconds, not once per waiter.
                if op == 'feedback' and args[1] in (418,429):
                    with flush_lock:store.flush()
                result = {'result': value}
            except Exception as error:
                result = {'error': type(error).__name__+':'+str(error)}
            body = json.dumps(result).encode()
            self.send_response(200); self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

    server = ThreadingHTTPServer((BIND, PORT), Handler)
    server.daemon_threads = True
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    if BIND=='127.0.0.1':
        TOKEN.write_text(token, encoding='ascii');os.chmod(TOKEN, 0o600)
    stop = threading.Event()
    def checkpoint():
        while not stop.wait(5):
            try:
                if connection.execute(text('SELECT pg_backend_pid()')).scalar()!=pid:raise RuntimeError('budget_owner_lock_lost')
                with flush_lock:store.flush()
            except Exception:
                # Fail closed; no reconnection can silently lose the owner lock.
                store.healthy_until = 0
                server.shutdown()
                return
    store.flush()
    thread = threading.Thread(target=checkpoint, daemon=True)
    thread.start()
    try:server.serve_forever(poll_interval=.5)
    finally:
        stop.set();thread.join(timeout=10);server.server_close()
        connection.close();budget._STORE=None


if __name__ == '__main__':
    serve()
