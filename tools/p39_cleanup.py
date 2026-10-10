"""Explicit, backed-up removal of retired external-source implementation.

Only --apply mutates listed source files. Historical database evidence is untouched.
"""
import ast,json,hashlib,shutil,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/p39-validation/cleanup-review-20261007'
ARCHIVE=[
 'engine/adapters/cex.py','engine/adapters/hyperliquid.py',
 'tools/p25_analysis_probe.py','tools/p25_source_probe.py','tools/p25_stream_probe.py',
 'tools/p25_supplement_probe.py','tools/pd2_livecheck.py','tools/pd2_probe.py','tools/pd2_cmc_refresh.py',
]
EDIT=['engine/pd2/providers.py','engine/pd2/native_streams.py','engine/pd2/execution.py','engine/pd2/derivatives.py','engine/pd2/identity.py','tests/test_binance_only.py']
def read(p):return (ROOT/p).read_text('utf8')
def drop_functions(code,names):
    tree=ast.parse(code);lines=code.splitlines(keepends=True)
    nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in nodes}==set(names)
    for node in sorted(nodes,key=lambda n:n.lineno,reverse=True):del lines[node.lineno-1:node.end_lineno]
    return ''.join(lines)
def replace(code,old,new):
    assert old in code,old[:100]
    return code.replace(old,new,1)
def plan():
    changes={}
    p='engine/pd2/providers.py';code=read(p);tree=ast.parse(code)
    helper_nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('timestamp','read_key')]
    assert len(helper_nodes)==2
    helpers='\n\n'.join(ast.get_source_segment(code,n) for n in helper_nodes)
    changes[p]='''"""Historical compatibility helpers; external collection has been retired."""
import json,re
from pathlib import Path
from datetime import datetime
from engine.pd2.source_policy import DISABLED,SourcePolicyDisabled,disabled_result
from engine.pd2.binance_facts import valuation_inputs,valuation_for,quote_usd_rate

'''+helpers+'''

class Supplemental:
    """No transport, key access, budget claims or writes for retired providers."""
    async def close(self):
        return None

    async def request(self,provider,capability,subject,path,params=None,ttl=300000,refresh=1800000,priority="normal"):
        if provider not in DISABLED:
            raise SourcePolicyDisabled('retired_supplemental_adapter')
        return disabled_result(provider)

    async def _request(self,*args,**kwargs):
        return await self.request(*args,**kwargs)

    async def cmc(self):
        return disabled_result('cmc')

    async def bykaranteli(self):
        return disabled_result('bykaranteli')

    async def map_rotation(self):
        return disabled_result('bykaranteli')
'''
    p='engine/pd2/native_streams.py';code=read(p)
    code=drop_functions(code,('save_hl_context','stream_hyperliquid'))
    start=code.index("        if venue in ('aster','hyperliquid'):")
    end=code.index('        return list(',start)
    code=code[:start]+code[end:]
    changes[p]=code
    p='engine/pd2/execution.py';code=read(p)
    code=replace(code,'            multiplier=inst.multiplier\n','')
    code=replace(code,'            if venue in ("binance","aster"):\n                base="https://fapi.binance.com/fapi/v1" if venue=="binance" else "https://fapi.asterdex.com/fapi/v3"','            if venue=="binance":\n                base="https://fapi.binance.com/fapi/v1"')
    start=code.index('            elif venue=="okx":');end=code.index('            else:',start)
    changes[p]=code[:start]+code[end:]
    p='engine/pd2/identity.py';code=read(p)
    # Exact-contract parsers remain for historical proof. No new CMC DB writes.
    code=code[:code.index('REVIEWED={')].replace('from sqlalchemy import select\n','').replace('from engine.db import Session\n','').replace('from engine.models import IdentityLink,Instrument\n','').replace('from engine.adapters.base import now_ms\n','')
    changes[p]=code
    p='engine/pd2/derivatives.py';code=read(p)
    code=replace(code,'    from engine.pd2.storage import get\n','')
    code=replace(code,'        "cmc_liquidations": {"status":"disabled_by_policy"},\n        "bykaranteli_whales": {"status":"disabled_by_policy"},\n        "liquidation_summaries": {},\n','')
    code=replace(code,'    from engine.models import IdentityLink,FundingPoint,OiPoint,Instrument,MarketState,UserPreference,RawEvent','    from engine.models import FundingPoint,OiPoint,Instrument,MarketState,RawEvent')
    code=replace(code,'    from engine.pd2.storage import get\n','')
    start=code.index('    link=None\n');end=code.index('    cross=',start)
    code=code[:start]+code[end:]
    code=replace(code,'        selection=s.get(UserPreference,"main_market:"+market.asset_id)\n','')
    code=replace(code,"        reference_selection='verified_binance_catalog' if chosen and chosen['venue']=='binance' else 'verified_auxiliary_catalog'","        reference_selection='verified_binance_catalog' if chosen else 'no_verified_contract'")
    start=code.index('    # Keep slow reference evidence');end=code.index('    with Session() as s:\n        checkpoint',start)
    code=code[:start]+'''    # Retired fields remain in the contract for frozen historical consumers.
    references={}
    model={"status":"disabled_by_policy","levels":[],"recorded_events":[]}
    positions={"status":"out_of_sample","positions":[]}
    liquidation_summary={"status":"disabled_by_policy","windows":[]}
    from engine.pd2.native_streams import tape_context
    tape=tape_context(market.instrument_id,now)
'''+code[end:]
    start=code.index('    for window in liquidation_summary.get(');end=code.index('    reference_quantity=',start)
    code=code[:start]+code[end:]
    changes[p]=code
    p='tests/test_binance_only.py';code=read(p)
    code=replace(code,"    monkeypatch.setattr(providers,'runtime_config',lambda:pytest.fail('disabled provider read runtime config'))","    monkeypatch.setattr(providers,'read_key',lambda *a:pytest.fail('disabled provider read a key'))")
    code=replace(code,'    from engine.pd2.native_streams import subscriptions,stream_hyperliquid,stream_venue','    from engine.pd2.native_streams import subscriptions,stream_venue')
    code=replace(code,"    asyncio.run(stream_venue('aster',stop_after=.1));asyncio.run(stream_hyperliquid(stop_after=.1))","    asyncio.run(stream_venue('aster',stop_after=.1));asyncio.run(stream_venue('hyperliquid',stop_after=.1))")
    code+='''

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
'''
    changes[p]=code
    for name,code in changes.items():ast.parse(code,filename=name)
    return changes
if (OUT/'cleanup-manifest.json').exists():
    print(json.dumps({'status':'already_applied','manifest':str(OUT/'cleanup-manifest.json')},ensure_ascii=False))
    raise SystemExit(0)
changes=plan()
if '--apply' not in sys.argv:
    print(json.dumps({'edit':list(changes),'archive':ARCHIVE},ensure_ascii=False));raise SystemExit(0)
OUT.mkdir(exist_ok=True);before=OUT/'before';archived=OUT/'retired-code'
assert not (OUT/'cleanup-manifest.json').exists(),'Cleanup already applied; use its manifest, do not rerun.'
records=[]
for name in EDIT+ARCHIVE:
    path=ROOT/name;target=before/name;target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(path,target)
    records.append({'path':name,'before_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'before_lines':len(path.read_text('utf8').splitlines()),'backup':str(target)})
for name,code in changes.items():(ROOT/name).write_text(code,encoding='utf8')
for name in ARCHIVE:
    path=ROOT/name;target=archived/(name+'.txt');target.parent.mkdir(parents=True,exist_ok=True)
    # Recoverable plain-text archive is outside the executable module/script tree.
    shutil.copy2(path,target);path.unlink()
for record in records:
    path=ROOT/record['path'];record['action']='edited' if path.exists() else 'archived_from_executable_tree'
    record['after_sha256']=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    record['after_lines']=len(path.read_text('utf8').splitlines()) if path.exists() else 0
manifest={'applied_at_utc':datetime.now(timezone.utc).isoformat(),'files':records,'removed_executable_lines_net':sum(r['before_lines']-r['after_lines'] for r in records),'preserved':'Frozen history, original documents/evidence, active box/P33 strategy, costs as reference, details/Kline layout','services_restarted':False,'database_writes':False}
(OUT/'cleanup-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'edited':len(EDIT),'archived':len(ARCHIVE),'removed_lines_net':manifest['removed_executable_lines_net'],'manifest':str(OUT/'cleanup-manifest.json')},ensure_ascii=False))
