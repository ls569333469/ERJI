import asyncio,time
from decimal import Decimal as D
from types import SimpleNamespace as N
import pytest
from sqlalchemy import select
from engine.models import Asset,Instrument,IdentityLink,DataCapability,Observation,UserPreference,Task,RawEvent
from engine.pd2 import source_policy,binance_facts,binance_identity,universe_policy,providers
from engine.p37 import analysis,worker
from engine.p38 import jobs,observation_query,windows
from engine.repository import bootstrap
from services.api import main as api
from test_p25_signals import isolated

def instrument(session,asset='ONLY',venue='binance',**spec):
    if not session.get(Asset,asset):session.add(Asset(id=asset,symbol=asset,known_at=1));session.flush()
    iid=f'{venue}:perp:{asset}USDT'
    session.add(Instrument(id=iid,asset_id=asset,venue=venue,native_symbol=asset+'USDT',quote_asset='USDT',market_type='linear_perpetual',spec={'base_asset':asset,**spec}));session.flush()
    return iid

def observation(session,iid,asset,ident,at,**payload):
    session.add(Observation(id=ident,instrument_id=iid,asset_id=asset,direction='up',first_at=at,visible_at=at,last_at=at,status='observing',version=at,payload={'symbol':asset+'USDT','visible_price':str(at),'reasons':[{'kind':'manual','value':at}],**payload}))

@pytest.mark.parametrize('url,provider',[
 ('https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest','cmc'),
 ('https://api.hyperliquid.xyz/info','hyperliquid'),('https://fstream.asterdex.com','aster'),
 ('https://www.okx.com/api/v5/market/candles','okx'),
 ('https://api.geckoterminal.com/api/v2/networks/bsc/tokens/a/pools','geckoterminal'),
 ('https://bykaranteli.com/liqmap','bykaranteli'),
 ('https://dquery.sintral.io/u-kline/v1/k-line/candles','wallet_rising'),
 ('https://fapi.binance.com.attacker.invalid/fapi/v1/klines','binance'),
 ('https://fapi.binance.com/fapi/v1/klines','geckoterminal')])
def test_network_boundary_before_transport(url,provider):
    from engine.adapters.base import PublicAdapter,Batch
    adapter=object.__new__(PublicAdapter)
    with pytest.raises(source_policy.SourcePolicyDisabled):asyncio.run(adapter.request(Batch(provider),'GET',url))

@pytest.mark.parametrize('url',['https://fapi.binance.com/fapi/v1/assetIndex','https://data-api.binance.vision/api/v3/klines','https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/token-rank','https://data.binance.vision/data/futures/um/daily/klines/a.zip'])
def test_allowed_binance_hosts(url):source_policy.assert_market_request(url,'binance')

@pytest.mark.parametrize('retired_budget',[15000,15001,1000000])
def test_old_saved_config_cannot_enable_foreign_sources(retired_budget):
    from engine.contracts import RuntimeConfig
    cfg=RuntimeConfig.model_validate({'sources':{'cmc':{'enabled':True},'bykaranteli':{'enabled':True,'month_budget':retired_budget}},'execution':{'taker_fee_rates':{'binance':.0005,'aster':.0001}}})
    assert not cfg.sources.cmc.enabled and not cfg.sources.bykaranteli.enabled
    assert cfg.sources.policy=='binance_only' and set(cfg.execution.taker_fee_rates)=={'binance'}
    restored=RuntimeConfig.model_validate(cfg.model_dump())
    assert not restored.sources.bykaranteli.enabled and restored.sources.bykaranteli.month_budget==retired_budget

def test_supplement_both_entrypoints_do_not_read_key_or_claim(monkeypatch):
    monkeypatch.setattr(providers,'read_key',lambda *a:pytest.fail('disabled provider read a key'))
    obj=object.__new__(providers.Supplemental)
    for method in (obj.request,obj._request):
        row=asyncio.run(method('cmc','quotes','BTC','/unused'))
        assert row['status']=='disabled_by_policy' and not row['action_eligible']
        assert row['capabilities']['enabled'] is False

@pytest.mark.parametrize('index',['NaN','Infinity','-1','0','bad'])
def test_invalid_fx_never_falls_back(index):
    at=2000000000000
    records={'binance:fx:USDTUSD':N(status='fresh',observed_at=at,payload={'symbol':'USDTUSD','index':index}),
             'cmc:quotes:USDT':N(status='fresh',observed_at=at,payload={'price':'1'})}
    assert binance_facts.quote_usd_rate('USDT',None,at,records) is None

def test_fx_symbol_time_and_reference_only_error_retention():
    at=2000000000000;row=N(status='source_error',observed_at=at,payload={'symbol':'USDTUSD','index':'.9997'})
    records={'binance:fx:USDTUSD':row}
    assert binance_facts.quote_usd_rate('USDT',None,at+1000,records)==D('.9997')
    assert binance_facts.quote_usd_rate('USDT',None,at+3600001,records) is None
    assert binance_facts.quote_usd_rate('USDT',None,at-1,records) is None
    row.payload['symbol']='USDCUSD'
    assert binance_facts.quote_usd_rate('USDT',None,at,records) is None

def test_oi_usd_independent_of_missing_supply_and_alpha_valuation():
    at=2000000000000;m=N(instrument_id='a',quote_asset='USDT',oi_notional_quote=D(100),price=D(2),quality=N(status='fresh'),oi_quality=N(status='fresh'))
    records={'binance:fx:USDTUSD':N(status='fresh',observed_at=at,payload={'symbol':'USDTUSD','index':'.9997'})}
    r=binance_facts.valuation_for(m,None,at,({'a':{}},records))
    assert r['usd']==D('99.97') and r['cap'] is None and r['status']=='binance_supply_not_provided'
    r=binance_facts.valuation_for(m,None,at,({'a':{'circulating_supply':'1000','quality_received_at':at}},records))
    assert r['cap']==D('1999.4') and r['ratio']==D('.05')

def test_native_identity_keeps_vendor_alias_and_unit_assets_separate(isolated):
    with isolated.begin() as s:
        a=instrument(s,'PEPE');b=instrument(s,'1000PEPE');x=instrument(s,'aster:PEPE','aster')
        for asset in ('PEPE','1000PEPE','aster:PEPE'):s.add(IdentityLink(asset_id=asset,provider='cmc',external_id='oldshared',status='verified',evidence={'old':True},known_at=1))
        binance_identity.seed(s,[a,b]);s.flush()
        aliases,admitted=universe_policy.membership(s)
        assert aliases['1000PEPE']=='1000PEPE' and aliases['aster:PEPE']=='aster:PEPE'
        assert {'PEPE','1000PEPE'}<=admitted
        assert len(list(s.scalars(select(IdentityLink).where(IdentityLink.provider=='cmc'))))==3
        links=list(s.scalars(select(IdentityLink).where(IdentityLink.provider=='binance',IdentityLink.asset_id.in_(['PEPE','1000PEPE']))))
        assert len(links)==2 and len({r.external_id for r in links})==2

def test_reference_never_falls_back_to_auxiliary_contract():
    from engine.pd2.derivatives import choose_reference
    foreign={'venue':'hyperliquid','instrument_id':'hl:a','quote_asset':'USDC'}
    assert choose_reference([foreign]) is None
    b={'venue':'binance','instrument_id':'b:a','quote_asset':'USDT'}
    assert choose_reference([foreign,b],'hl:a')==b

def test_macd_decimal_equivalence():
    values=[D(100)+D(i%29)/D(17)+D(i)/D(101) for i in range(300)]
    expected=[analysis.ema(values[:n],12)-analysis.ema(values[:n],26) for n in range(26,len(values)+1)]
    assert analysis.macd_differences(values)==expected

@pytest.mark.parametrize('count',[1,5,20])
def test_one_market_compute_preserves_events_and_new_event_uses_cache(isolated,monkeypatch,count):
    at=int(time.time()*1000);source={'version':1};calls=[]
    with isolated.begin() as s:
        iid=instrument(s,'CACHE')
        for n in range(count):observation(s,iid,'CACHE','cache'+str(n),at-n)
    monkeypatch.setattr(windows,'token',lambda *a,**k:(source['version'],))
    monkeypatch.setattr(windows,'read',lambda *a,**k:[])
    monkeypatch.setattr(analysis,'trend',lambda rows,step,now:(calls.append(step) or {'status':'ready','direction':'up','bars':300}))
    assert analysis.analyze_market(iid,at) and len(calls)==6
    with isolated() as s:
        for n in range(count):
            row=s.get(Observation,'cache'+str(n));assert row.payload['visible_price']==str(at-n) and row.payload['reasons'][0]['value']==at-n
    with isolated.begin() as s:observation(s,iid,'CACHE','cache-new',at+1)
    analysis.analyze_market(iid,at+1);assert len(calls)==6
    source['version']=2;analysis.analyze_market(iid,at+2);assert len(calls)==12
    # P39 recomputes on changed candle input, not solely on wall-clock expiry.
    analysis.analyze_market(iid,at+300001);assert len(calls)==12
    # Earlier callers can neither replace a newer cached result nor event facts.
    assert analysis.analyze_market(iid,at)=={'superseded':1}
    with isolated() as s:assert s.get(Observation,'cache-new').payload['analysis_checked_at']==at+300001

def test_changed_source_before_commit_rejects_result(isolated,monkeypatch):
    at=int(time.time()*1000);version={'v':1}
    with isolated.begin() as s:
        iid=instrument(s,'CHANGED');observation(s,iid,'CHANGED','changed',at)
    monkeypatch.setattr(windows,'token',lambda *a,**k:(version['v'],))
    monkeypatch.setattr(windows,'read',lambda *a,**k:[])
    def calculate(*args):version['v']=2;return {'status':'ready','direction':'up'}
    monkeypatch.setattr(analysis,'trend',calculate)
    assert analysis.analyze_market(iid,at)=={'superseded':1}
    with isolated() as s:assert 'matrix' not in s.get(Observation,'changed').payload

def test_layered_admissions_union_and_fair_rotation(isolated):
    at=int(time.time()*1000)
    with isolated.begin() as s:
        for n in range(3):
            asset='FAIR'+str(n);iid=instrument(s,asset);observation(s,iid,asset,'fair'+str(n),at-n,analysis_checked_at=at if n==0 else 0)
    trend,_=worker.select_work(at,1,'trend');structure,_=worker.select_work(at,1,'structure')
    # Independent lanes may analyze the same native market; neither consumes
    # the sibling lane's capacity. The union represents native markets.
    with isolated() as s:
        assert s.get(UserPreference,'p39_trend_active').payload['instrument_ids']==[trend[0][1]]
        assert s.get(UserPreference,'p39_structure_active').payload['instrument_ids']==[structure[0][1]]
    with isolated() as s:
        union=s.get(UserPreference,'p37_active').payload['instrument_ids'];assert set(union)=={trend[0][1],structure[0][1]}
    with isolated.begin() as s:
        from engine.models import MarketAnalysisState
        s.add(MarketAnalysisState(instrument_id=structure[0][1],work_kind='structure',analysis_status='completed',strategy_result='no_match',last_attempt_at=at,last_success_at=at,next_check_at=at+300000,payload={}))
    rotated,_=worker.select_work(at+1,1,'structure');assert rotated[0][1]!=structure[0][1]

def test_same_input_rechecks_after_retry_and_lanes_do_not_steal(isolated):
    at=2000000040000;iid='binance:perp:RETRYUSDT'
    jobs.submit([iid],at=at);lease=jobs.take(at,work_kinds=('structure',));assert lease
    assert jobs.take(at,work_kinds=('trend',)) is None
    jobs.finish(lease,at=at+1);jobs.submit([iid],at=at+2)
    assert jobs.take(at+2,work_kinds=('structure',)) is None
    jobs.submit([iid],at=at+300001)
    assert jobs.take(at+300001,work_kinds=('structure',))
    assert jobs.LANES['trend']==('trend',) and jobs.LANES['structure']==('structure',)

def test_task_clock_consistent_when_analysis_ages():
    row=dict(visible_at=1,version=1,status='observing',analyzed_at=1,analysis_status='ready')
    result=observation_query.clock_fields(row,None,500000)
    assert result['task_status']==result['analysis_status']=='delayed'

def test_policy_retirement_keeps_raw_cache_and_closes_old_ws_at_proven_time(isolated):
    at=2000000000000
    with isolated.begin() as s:
        s.add(UserPreference(key='ws_coverage:aster:market:1',updated_at=at-5000,payload={'connected':True,'segments':[{'start':at-10000,'end':None,'instruments':['old']}]}))
        s.add(Task(id='old-provider',dedupe_key='old-provider',kind='supplement',state='failed',available_at=at,attempts=1,payload={'subject':'cmc:quotes:BTC','error':'HistoricalError'}))
        s.add(RawEvent(id='old-raw',source='cmc',endpoint='old',received_at=at,payload={'original':True}))
        source_policy.retire_runtime(s,at)
    with isolated() as s:
        row=s.get(UserPreference,'ws_coverage:aster:market:1');assert not row.payload['connected'] and row.payload['segments'][0]['end']==at-5000
        task=s.get(Task,'old-provider');assert task.state=='complete' and task.payload['error']=='HistoricalError'
        assert s.get(RawEvent,'old-raw').payload['original']

def test_realtime_coverage_excludes_vendor_cache_but_history_readable(isolated,monkeypatch):
    monkeypatch.setattr(api,'Session',isolated)
    at=int(time.time()*1000)
    with isolated.begin() as s:
        s.add(DataCapability(key='cmc:quotes:test',provider='cmc',capability='quotes',subject='test',status='fresh',observed_at=at,received_at=at,expires_at=at+10000,payload={'unchanged':True}))
    assert all(r['provider'] not in source_policy.DISABLED for r in api.coverage()['rows'])
    assert any(r['provider']=='cmc' for r in api.coverage(historical=True)['rows'])
    assert api.coverage()['budgets']==[]

def test_disabled_ws_entrypoints_never_connect():
    from engine.pd2.native_streams import subscriptions,stream_venue
    assert subscriptions('aster')==[]
    asyncio.run(stream_venue('aster',stop_after=.1));asyncio.run(stream_venue('hyperliquid',stop_after=.1))

def test_legacy_request_slot_blocked_before_reservation(monkeypatch):
    from engine.pd2 import request_budget
    monkeypatch.setattr(request_budget,'reserve',lambda *a,**k:pytest.fail('external reservation attempted'))
    async def attempt():
        async with request_budget.request_slot('https://bykaranteli.com/liqmap',{}):pytest.fail('external request admitted')
    with pytest.raises(source_policy.SourcePolicyDisabled):asyncio.run(attempt())

def test_saved_external_buy_never_enters_realtime_list_but_history_remains(isolated,monkeypatch):
    from engine.models import CurrentSignal,MarketState
    from engine.pd2 import signal_projection
    at=int(time.time()*1000)
    with isolated.begin() as s:
        iid=instrument(s,'old:ext','hyperliquid')
        s.add(MarketState(instrument_id=iid,as_of=at,payload={'price':'10','price_kind':'last'}))
        s.add(CurrentSignal(id='foreign-plan',asset_id='old:ext',instrument_id=iid,action='participate',first_seen_at=at,computed_at=at,valid_until=at+10000,payload={'id':'foreign-plan','instrument_id':iid,'asset_id':'old:ext','symbol':'OLD','action_code':'participate','entry_zone':['9','11'],'protection':'8','first_seen_price':'10'}))
    monkeypatch.setattr(signal_projection,'now_ms',lambda:at)
    assert not any(r['instrument_id']==iid for r in signal_projection.read('opportunities')['rows'])
    with isolated.begin() as s:s.get(CurrentSignal,'foreign-plan').action='ended'
    assert any(r['instrument_id']==iid for r in signal_projection.read('history')['rows'])

def test_asset_index_uses_shared_official_budget():
    from engine.pd2.p36_budget import specification
    assert specification('https://fapi.binance.com/fapi/v1/assetIndex',{'params':{'symbol':'USDTUSD'}})==('um',{'um':1},1)


def test_retired_supplement_never_constructs_http_transport(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx,'AsyncClient',lambda *a,**k:pytest.fail('retired transport constructed'))
    obj=providers.Supplemental()
    for provider in source_policy.DISABLED:
        assert asyncio.run(obj.request(provider,'old','x','/old'))['status']=='disabled_by_policy'
    with pytest.raises(source_policy.SourcePolicyDisabled):
        asyncio.run(obj.request('binance','old','x','/old'))
    asyncio.run(obj.close())

def test_external_shared_cache_cannot_reenter_binance_context(isolated):
    from engine.pd2.derivatives import context_for
    at=int(time.time()*1000)
    with isolated.begin() as s:iid=instrument(s,'CLEAN')
    market=N(asset_id='CLEAN',instrument_id=iid,native_symbol='CLEANUSDT',quote_asset='USDT',venue='binance',circulating_cap=None,oi_changes=[])
    # Even explicitly supplied stale vendor reference caches cannot be consumed.
    shared={'aliases':{'CLEAN':'CLEAN'},'members':{'CLEAN':['CLEAN']},'fx_rates':{},
            'cmc_liquidations':{'payload':{'old':True}},'bykaranteli_whales':{'payload':{'old':True}},'liquidation_summaries':{None:{'status':'fresh','windows':[{'period':'1H','short_usd':'999'}]}}}
    result=context_for(market,at,shared)
    assert result['liquidation_summary']['status']=='disabled_by_policy'
    assert result['supplemental_references']=={} and result['facts']==[]
    assert result['native_reference']['instrument_id']==iid
