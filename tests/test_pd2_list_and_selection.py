from types import SimpleNamespace
from decimal import Decimal as D
from engine.pd2 import pagination,market_selection
from engine.contracts import RuntimeConfig
from test_pd2_persistence import isolated

def result(rows):
    return {'rows':rows,'groups':[{'phase_id':'early_trigger','label':'early'},{'phase_id':'structure_confirmed','label':'confirmed'}]}

def row(asset,oi,action='participate'):
    return {'id':asset,'asset_id':asset,'market':{'symbol':asset,'oi_notional_usd':oi},'first_seen_at':1000,'phase':'structure_confirmed','action':{'code':action}}

def test_oi_sort_without_target_and_live_safety_on_frozen_pages(isolated,monkeypatch):
    monkeypatch.setattr(pagination,'now_ms',lambda:1000)
    first=pagination.paginate(result([row('A','2'),row('B','100'),row('C',None)]),'stages','','oi',1,2)
    assert [x['asset_id'] for x in first['rows']]==['B','A']
    sid=first['pagination']['list_snapshot']
    changed=pagination.paginate(result([row('A','200'),row('B','1','paused'),row('C',None),row('D','999')]),'stages','','oi',1,2,sid)
    assert [x['asset_id'] for x in changed['rows']]==['B','A']
    assert changed['rows'][0]['action']['code']=='paused'
    assert changed['pagination']['new_assets_waiting']==1
    second=pagination.paginate(result([row('A','200'),row('B','1'),row('C',None)]),'stages','','oi',2,2,sid)
    assert second['rows'][0]['row_number']==3 and second['pagination']['total']==3
    assert second['groups'][1]['asset_count']==3

def test_main_selection_runs_through_database_even_without_candidates(isolated):
    selected=market_selection.select_main('PD2_TEST_NO_MARKET',RuntimeConfig(),1000)
    assert selected['selected'] is None and selected['ranked']==[]
