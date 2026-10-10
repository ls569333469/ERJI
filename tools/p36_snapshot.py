"""Bounded read-only before/after evidence; no provider traffic."""
from tools.p34_local import prepare
prepare()
import json,sys,time
from pathlib import Path
from sqlalchemy import text
from engine.db import Session

now=int(sys.argv[2]) if len(sys.argv)>2 else int(time.time()*1000)
out={'at':now}
queries={
 'oi':"SELECT count(*) total,count(*) FILTER(WHERE (SELECT max(event_time) FROM oi_points o WHERE o.instrument_id=m.instrument_id AND o.event_time<=:now) > :now-360000) fresh FROM market_states m JOIN instruments i ON i.id=m.instrument_id WHERE i.venue='binance'",
 'continuity':"WITH r AS (SELECT c.instrument_id,c.open_time,c.close_time,lag(c.close_time) OVER(PARTITION BY c.instrument_id ORDER BY c.open_time) prev FROM candles c JOIN instruments i ON i.id=c.instrument_id WHERE i.venue IN ('binance','binance_spot') AND c.resolution='240' AND c.is_closed AND c.open_time>:now-4320000000) SELECT count(*) FILTER(WHERE prev<open_time) gaps,count(DISTINCT instrument_id) markets FROM r",
 'traffic':"SELECT source,endpoint,count(*) n FROM raw_events WHERE received_at>:now-300000 AND received_at<=:now AND endpoint LIKE 'https://%' GROUP BY source,endpoint",
 'db_wait':"SELECT wait_event_type,wait_event,count(*) n FROM pg_stat_activity WHERE datname=current_database() GROUP BY wait_event_type,wait_event",
 'health':"SELECT role,updated_at,state,details FROM worker_heartbeats",
 'oi_current':"SELECT count(*) total,count(*) FILTER(WHERE at>:now-360000) fresh FROM instruments i LEFT JOIN (SELECT instrument_id,max(event_time) at FROM oi_points WHERE event_time<=:now GROUP BY instrument_id) o ON o.instrument_id=i.id WHERE i.venue='binance'",
 'history':"SELECT key,payload FROM user_preferences WHERE key LIKE 'p34_history:%'",
 'budget':"SELECT key,payload FROM user_preferences WHERE key LIKE 'http_budget:%' OR key='p36_http_ledger'",
 'candles':"SELECT i.venue,c.resolution,count(DISTINCT c.instrument_id) fresh FROM candles c JOIN instruments i ON i.id=c.instrument_id WHERE c.is_closed AND c.resolution IN ('240','1D') AND c.close_time>= (:now / CASE c.resolution WHEN '240' THEN 14400000 ELSE 86400000 END)*CASE c.resolution WHEN '240' THEN 14400000 ELSE 86400000 END GROUP BY i.venue,c.resolution"
}
with Session() as s:
 s.execute(text('SET TRANSACTION READ ONLY'))
 s.execute(text("SET LOCAL statement_timeout='20s'"))
 for key,q in queries.items():
  try:
   with s.begin_nested():out[key]=[dict(r) for r in s.execute(text(q),{'now':now}).mappings()]
  except Exception as e:out[key]={'error':str(e).split('\n')[0]}
p=Path('docs/p36-validation');p.mkdir(exist_ok=True,parents=True)
(p/(sys.argv[1]+'.json')).write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str),encoding='utf8')
print(json.dumps({k:v for k,v in out.items() if k not in ('budget','history','health')},ensure_ascii=False,default=str))
