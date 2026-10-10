from sqlalchemy import select
from engine.models import UserPreference
from engine.pd2 import request_budget as budget
from test_p25_signals import isolated


def test_waiting_ticket_heartbeat_survives_database_reload(isolated, monkeypatch):
    clock = [2000000000000]
    monkeypatch.setattr(budget, 'now_ms', lambda: clock[0])
    monkeypatch.setattr(budget, 'budget_spec', lambda *a: ('p34-heartbeat.test', 1, 1, 500))
    url = 'https://p34-heartbeat.test/api'
    assert budget.reserve(url, {}, 'first') == 0
    assert budget.reserve(url, {}, 'waiting') > 0
    clock[0] += 30000
    assert budget.reserve(url, {}, 'waiting') > 0
    # A second session must see the heartbeat, not only the original ORM object.
    with isolated() as s:
        row = s.get(UserPreference, 'http_budget:p34-heartbeat.test')
        assert row.payload['pending'][0]['seen_at'] == clock[0]
    clock[0] += 30000
    assert budget.reserve(url, {}, 'newcomer') > 0
    assert budget.reserve(url, {}, 'waiting') == 0


def test_repair_gets_capacity_without_starving_ordinary_catalog():
    from engine.pd2.collection_schedule import choose_with_repair
    now=2000000000000
    names=['candidate','old_a','old_b','old_c']
    visited={'candidate':now-400000,**{n:now-86400000 for n in names[1:]}}
    result=choose_with_repair(names,visited,{},now,2,{}, {'candidate'})
    assert result==['candidate','old_a']
    # Recently repaired candidates cannot bypass their deadlines on every call.
    visited['candidate']=now
    assert choose_with_repair(names,visited,{},now,2,{}, {'candidate'})==['old_a','old_b']


def test_waiting_ticket_remains_alive_past_original_lease(isolated, monkeypatch):
    clock=[2000000000000]
    monkeypatch.setattr(budget,'now_ms',lambda:clock[0])
    monkeypatch.setattr(budget,'budget_spec',lambda *a:('p34-longwait.test',1,1,500))
    key='http_budget:p34-longwait.test';url='https://p34-longwait.test/api'
    with isolated.begin() as s:
        s.add(UserPreference(key=key,updated_at=clock[0],payload={'blocked_until':clock[0]+150000}))
    for _ in range(5):
        assert budget.reserve(url,{},'old-waiter')>0
        clock[0]+=30000
    assert budget.reserve(url,{},'new-waiter')>0
    with isolated() as s:
        assert [r['id'] for r in s.get(UserPreference,key).payload['pending']]==['old-waiter','new-waiter']
    assert budget.reserve(url,{},'old-waiter')==0


def test_config_reuses_callers_transaction(isolated,monkeypatch):
    from engine import repository
    with isolated() as s:
        expected=repository.runtime_config(s)
        def forbidden():raise AssertionError('nested database checkout')
        monkeypatch.setattr(repository,'Session',forbidden)
        assert repository.runtime_config(s)==expected


def test_expired_candidate_does_not_override_current_tier_or_fair_age(isolated,monkeypatch):
    from engine.pd2 import collection_schedule as schedule
    from engine.models import Asset,Instrument,CurrentSignal
    now=2000000000000
    monkeypatch.setattr(schedule,'now_ms',lambda:now)
    names=['P34CANDIDATEUSDT','P34ORDINARYUSDT']
    with isolated.begin() as s:
        for native in names:
            s.add(Asset(id=native,symbol=native,known_at=now));s.flush()
            s.add(Instrument(id='p34:'+native,asset_id=native,venue='p34-repair',native_symbol=native,
                market_type='linear_perpetual',quote_asset='USDT',spec={}));s.flush()
        s.add(CurrentSignal(id='p34-repair-candidate',asset_id=names[0],instrument_id='p34:'+names[0],
            action='waiting',first_seen_at=now-86400000,computed_at=now,valid_until=now-3600000,
            payload={'opportunity_eligible':True,'structure_valid_until':now-3600000}))
        s.add(UserPreference(key='due:p34-repair:candles',updated_at=now,payload={
            'visited':{names[0]:now-400000,names[1]:now-86400000}}))
    assert schedule.due('p34-repair',names,'candles',1)==[names[1]]
    with isolated() as s:
        candidate=s.get(CurrentSignal,'p34-repair-candidate')
        assert candidate.action=='waiting' and candidate.valid_until<now
    # Other fields still use ordinary deadlines; no forced promotion to a fast
    # live quote/OI queue and no extension of the stale structure's lifetime.
    assert schedule.due('p34-repair',names,'oi',1)==[names[0]]  # both initially unseen


def test_projection_uses_one_quote_snapshot_during_concurrent_ws_updates(isolated,monkeypatch):
    from types import SimpleNamespace as N
    from test_p25_signals import seed,NOW
    from engine.models import Campaign,Episode,MarketState
    from engine.pd2 import service,signal_projection,derivatives
    seed(isolated,action='waiting');iid='binance:perp:P25TEST0USDT';clock=[NOW]
    monkeypatch.setattr(service,'now_ms',lambda:clock[0])
    market=N(instrument_id=iid,asset_id='P25TEST0',venue='binance',as_of=NOW+500,
        model_dump=lambda **kw:{'price':'1','as_of':NOW+500})
    def load(ids):
        clock[0]=NOW+1000
        return N(rows=[market])
    monkeypatch.setattr(service,'list_markets',load)
    monkeypatch.setattr(derivatives,'shared_inputs',lambda now:{})
    monkeypatch.setattr(derivatives,'context_for',lambda *args:{})
    published=[];monkeypatch.setattr(signal_projection,'publish',lambda rows:published.extend(rows))
    with isolated.begin() as s:
        # A stream writes after the quote snapshot was read. It must not make
        # that valid snapshot look like a future timestamp / stale market.
        s.get(MarketState,iid).as_of=NOW+2000
        s.add(Campaign(id='p34-clock-campaign',asset_id='P25TEST0',first_seen_at=NOW-10000,
            first_seen_price=1,reference_instrument_id=iid,state='active'));s.flush()
        s.add(Episode(id='p34-clock-episode',campaign_id='p34-clock-campaign',strategy_id='box',
            phase='structure_pending',rule_version='test',evidence={'reason':'pending'}))
    service.decision_list()
    row=next(r for r in published if r['id']=='p34-clock-episode')
    assert row['fresh'] is True and row['market']['as_of']==NOW+500

