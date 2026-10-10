"""Single-admin sessions for server deployment, with Origin and session-bound CSRF."""
import base64,hmac,hashlib,json,secrets,time
from pathlib import Path
from engine.config import settings

def secret():
    path=settings().admin_token_file
    if not path:raise ValueError("admin_token_file_required")
    value=Path(path).read_text(encoding="utf-8-sig").strip()
    if len(value)<32:raise ValueError("admin_token_too_short")
    return value

def create_session(key,now=None):
    now=int(time.time()) if now is None else now
    data={"expires":now+settings().session_seconds,"csrf":secrets.token_urlsafe(24)}
    body=base64.urlsafe_b64encode(json.dumps(data,separators=(",",":")).encode()).decode().rstrip("=")
    signature=hmac.new(key.encode(),body.encode(),hashlib.sha256).hexdigest()
    return body+"."+signature,data

def verify_session(cookie,key,now=None):
    try:
        body,signature=cookie.split(".")
        expected=hmac.new(key.encode(),body.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):return None
        data=json.loads(base64.urlsafe_b64decode(body+"="*(-len(body)%4)))
        if data["expires"]<=(int(time.time()) if now is None else now):return None
        return data
    except (ValueError,KeyError,TypeError):return None


def login_allowed(client,now=None):
    from sqlalchemy import text
    from engine.db import Session
    from engine.models import UserPreference
    now=int(time.time()) if now is None else now
    key='login_rate:'+hashlib.sha256(client.encode()).hexdigest()[:32]
    with Session.begin() as s:
        if s.bind.dialect.name=='postgresql':s.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':key})
        row=s.get(UserPreference,key)
        times=[t for t in (row.payload.get('times',[]) if row else []) if now-300<t<=now]
        if len(times)>=8:return False
        s.merge(UserPreference(key=key,payload={'times':times+[now]},updated_at=now*1000))
    return True
