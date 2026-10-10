from copy import deepcopy
from decimal import Decimal as D
from engine.pd2.event_overlays import event_avwap, event_fibonacci, pivots, attach_event_overlays
from engine.pd2.tracking import advance


def bars(values):
    return [{"open_time":(i+1)*3600000,"close_time":(i+2)*3600000,
             "received_at":(i+2)*3600000,"is_closed":True,
             "open":str(p),"high":str(p+1),"low":str(p-1),"close":str(p),
             "base_volume":"10","quote_volume":"100"} for i,p in enumerate(values)]


def test_event_vwap_exact_formula_and_future_bar_is_invisible():
    rows=bars([10,20,30,40]);now=rows[2]["close_time"]
    rows[1]["base_volume"]="30"
    output=event_avwap(rows,rows[0]["open_time"],"60",now)
    assert [D(p["event_avwap"]) for p in output["series"]]==[D(10),D("17.5"),D(20)]
    assert not output["series"][0]["support_eligible"]
    rows[3]["close"]="9999"
    assert event_avwap(rows,rows[0]["open_time"],"60",now)==output


def test_missing_base_volume_or_gap_never_reanchors_or_uses_quote_volume():
    rows=bars([10,20,30,40]);now=rows[-1]["close_time"]
    rows[1]["base_volume"]=None
    result=event_avwap(rows,rows[0]["open_time"],"60",now)
    assert result["status"]=="missing_volume_or_interval"
    assert [p["event_avwap"] for p in result["series"]]==['10',None,None,None]
    result=event_avwap([rows[0],rows[2]],rows[0]["open_time"],"60",now)
    assert result["series"][-1]["event_avwap"] is None
    assert event_avwap(rows[1:],rows[0]["open_time"],"60",now)["status"]=="missing_anchor"


def test_earliest_equal_pivot_only_after_right_confirmation():
    rows=bars([2,3,8,8,3,2,3])
    assert pivots(rows[:4],"high")==[]
    found=pivots(rows,"high")
    assert len(found)==1 and found[0]["pivot_time"]==rows[2]["open_time"]
    assert found[0]["known_at"]==rows[4]["close_time"]


def test_fibonacci_uses_pretrigger_wave_and_does_not_replace_missing_frozen_wave():
    rows=bars([8,7,4,7,8,12,9,8,9,10,9,8,9,11,13])
    trigger=rows[8]["close_time"]
    first=event_fibonacci(rows[:9],trigger,trigger)
    assert [p["price"] for p in first["anchors"]]==['3','13']
    assert [p["price"] for p in first["levels"]]==['9.180','8.0','6.820']
    later=event_fibonacci(rows,trigger,rows[-1]["close_time"],first)
    assert later["anchors"][:2]==first["anchors"]
    assert event_fibonacci(rows,trigger,rows[-1]["close_time"],{"anchors":[]})["status"]=="missing_anchors"


def test_overlay_attaches_only_to_actual_trigger_timeframe():
    rows=bars([10,20,30]);assessments=[{"timeframe":tf,"series":[{"time":r["open_time"]} for r in rows]} for tf in ('60','240')]
    original=deepcopy(rows)
    attach_event_overlays(assessments,{'60':rows},{'trigger':{**rows[0],'resolution':'60'}},rows[-1]['close_time'])
    assert assessments[0]['series'][-1]['event_avwap']=='20'
    assert 'event_avwap' not in assessments[1]['series'][-1]
    assert rows==original


def test_tracking_rejects_nonfinite_prices_and_separates_expiry():
    for value in ('NaN','Infinity','-1','0'):
        assert advance({}, {}, 'ended', value, 100, False)['protection_history']==[]
    result=advance({}, {}, 'ended', '1', 100, False)
    assert result['first_ended_observed_at']==100
    assert 'first_invalidation_observed_at' not in result
