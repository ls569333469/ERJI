"""Read-only actual-data evidence. No new source requests or strategy releases."""
from tools.p34_local import prepare
prepare()
import json,time
from pathlib import Path
from collections import Counter
from sqlalchemy import select,func,event
from engine.db import Session,engine
from engine.models import DataCapability,RawEvent,UserPreference,WorkerHeartbeat
from engine.adapters.base import now_ms


def run():
    now=now_ms();result={'as_of':now,'source_requests':0}
    with Session() as s:
        books=list(s.scalars(select(DataCapability).where(DataCapability.provider=='binance',DataCapability.capability=='execution_book')))
        result['books']={'stored':len(books),'fresh_10s':sum(r.status=='fresh' and r.observed_at is not None and 0<=now-r.observed_at<=10000 for r in books)}
        result['workers']=[{'role':r.role,'state':r.state,'age_seconds':round((now-r.updated_at)/1000,2),'details':r.details}
            for r in s.scalars(select(WorkerHeartbeat)) if now-r.updated_at<600000]
        firsts=list(s.scalars(select(RawEvent).where(RawEvent.source=='erji',RawEvent.endpoint=='p33_first')))
        originals={r.payload['event']['initial']['event_id']:r for r in firsts if r.payload.get('event',{}).get('initial')}
        matches=[];mismatches=[]
        for record in s.scalars(select(UserPreference).where(UserPreference.key.startswith('p33_shadow:',autoescape=True))):
            for key,value in record.payload.get('active_events',{}).items():
                original=originals.get(key)
                if not original:continue
                before=original.payload['event']
                item={'event_id':key,'first_raw_ref':original.id,'current_phase':value.get('status'),
                      'original_initial_preserved':before.get('initial')==value.get('initial'),
                      'original_plan_preserved':before.get('plan')==value.get('plan')}
                (matches if item['original_initial_preserved'] and item['original_plan_preserved'] else mismatches).append(item)
        result['frozen_event_audit']={'first_events':len(originals),'retained_active_events':len(matches),
            'mismatches':mismatches,'examples':matches[:20],'limitation':'current retained states; not proof every natural lifecycle finished'}
        result['history']=[{'key':r.key,**r.payload} for r in s.scalars(select(UserPreference).where(UserPreference.key.startswith('p34_history:',autoescape=True)))]
    from tools.p33_forward_outcomes import run as outcomes
    result['forward_outcomes']=outcomes()
    # Instrument the same list route inside an isolated diagnostic process. The
    # separately retained browser timings include real HTTP and the Next proxy.
    from fastapi.testclient import TestClient
    from services.api.main import app
    durations=[];sql=[];started={}
    def before(conn,cursor,statement,parameters,context,many):started[id(context)]=time.perf_counter()
    def after(conn,cursor,statement,parameters,context,many):
        sql.append((time.perf_counter()-started.pop(id(context)))*1000)
    event.listen(engine,'before_cursor_execute',before);event.listen(engine,'after_cursor_execute',after)
    try:
        client=TestClient(app) # no lifespan startup, bootstrap, or event pump
        for _ in range(10):
            sql.clear();at=time.perf_counter();response=client.get('/v1/signals?view=opportunities&page_size=300')
            response.raise_for_status()
            durations.append({'total_ms':round((time.perf_counter()-at)*1000,2),'sql_ms':round(sum(sql),2),
                'sql_count':len(sql),'rows':len(response.json()['rows'])})
        client.close()
    finally:
        event.remove(engine,'before_cursor_execute',before);event.remove(engine,'after_cursor_execute',after)
    result['sql_route_profile']={'kind':'same_API_in_process_real_current_rows','samples':durations,'includes_network_proxy':False}
    return result


if __name__=='__main__':
    result=run()
    Path('docs/p34-validation/closeout-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf8')
    print(json.dumps({'books':result['books'],'frozen_events':{k:v for k,v in result['frozen_event_audit'].items() if k!='examples'},
        'outcomes':result['forward_outcomes']['scenario_counts'],'sql':result['sql_route_profile']['samples'][:2]},ensure_ascii=False))
