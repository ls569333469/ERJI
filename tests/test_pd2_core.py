from decimal import Decimal as D
import numpy as np
import talib
from engine.pd2.analysis import technical,pivots,aggregate,choose_targets,trailing_protection
from engine.pd2.decision import decide,execution_cost
from engine.pd2.outcomes import evaluate_path

def bars(n=100,res=3600000):
    return [{"open_time":i*res,"close_time":(i+1)*res,"received_at":(i+1)*res,"is_closed":True,
       "open":str(10+i*.02),"high":str(10.4+i*.02),"low":str(9.6+i*.02),"close":str(10+i*.02),
       "base_volume":"100","quote_volume":"1000","revision":1} for i in range(n)]

def test_indicator_series_matches_independent_talib_and_ignores_open_bar():
    data=bars();data[-1]["is_closed"]=False;now=100*3600000
    actual=technical(data,"60",now)
    values=np.array([float(b["close"]) for b in data[:-1]])
    assert D(actual["latest"]["ema20"])==D(str(talib.EMA(values,20)[-1]))
    assert len(actual["series"])==99
    modified=[dict(b) for b in data];modified[-1]["close"]="999999"
    assert technical(modified,"60",now)["latest"]==actual["latest"]

def test_pivots_confirm_after_right_bars_and_ties_choose_last():
    data=bars(7)
    for b,p in zip(data,[1,2,5,5,2,1,1]):b["high"]=str(p)
    assert pivots(data[:5],"high")==[]
    points=pivots(data,"high")
    assert len(points)==1 and points[0]["pivot_time"]==data[3]["open_time"]
    assert points[0]["known_at"]==data[5]["close_time"]

def test_missing_interval_does_not_bridge_ema_history():
    data=bars(100);data.pop(70)
    a=technical(data,"60",100*3600000)
    assert a["bar_count"]==29 and a["latest"]["ema50"] is None

def test_two_hour_requires_complete_pairs_and_week_is_monday():
    data=bars(4)
    result=aggregate(data,"120",4*3600000)
    assert len(result)==2 and all(r["is_closed"] for r in result)
    assert aggregate([data[0],data[2]],"120",4*3600000)==[]
    daily=bars(7,86400000)
    for b in daily:b["open_time"]+=345600000;b["close_time"]+=345600000
    weekly=aggregate(daily,"1W",daily[-1]["close_time"])
    assert len(weekly)==1 and weekly[0]["open_time"]==345600000

def test_nearest_resistance_cannot_be_skipped_and_no_fixed_multiplier():
    levels=[{"id":"near","kind":"resistance","low":"1.1","high":"1.2","timeframe":"240"},
      {"id":"far","kind":"resistance","low":"3","high":"3.1","timeframe":"1W"}]
    result=choose_targets([{"levels":levels}],"1",{"upper":"1","lower":".8","end_time":0})
    assert result["t1"]["id"]=="near"
    assert choose_targets([],"1")["highest_conditional_target"] is None
    assert choose_targets([{"levels":levels}],"1.15")["entry_obstructions"][0]["id"]=="near"


def test_target_space_uses_explicit_plan_basis_instead_of_live_price():
    levels=[{"id":"near","kind":"resistance","low":"1.2","high":"1.22","timeframe":"240"}]
    result=choose_targets([{"levels":levels}],"1.05",reference_price="1.01")
    assert D(result["t1"]["remaining_upside_pct"])==(D("1.2")/D("1.01")-1)*100

def test_execution_uses_actual_depth_and_nonzero_costs():
    book={"received_at":1000,"observed_at":1000,"scope":"execution_market","asks":[["1","2000"]],"bids":[[".999","2000"]]}
    result=execution_cost("1",".95","1.2",book,1000,fee_rate=".0005",funding_cost_rate=".001")
    assert result["status"]=="eligible"
    assert D(result["net_r"])<D("4")
    assert execution_cost("1",".95","1.2",book,91001,fee_rate=".0005",funding_cost_rate=".001")["status"]=="stale"
    assert execution_cost("1",".95","1.2",book,1000,notional="5000",fee_rate=".0005",funding_cost_rate=".001")["status"]=="insufficient_depth"
    assert execution_cost("1",".95","1.2",book,1000,fee_rate=None,funding_cost_rate=".001")["net_r"] is None

def plan():
    return {"entry_zone":[".98","1.02"],"protection":".9","expires_at":10000,"trigger":{"resolution":"60"},"reason":"test"}
def quote(price="1"):
    return {"price":price,"price_kind":"ask","as_of":1000}

def test_actions_protection_expiry_and_no_oi_dependency():
    e=plan();cost={"status":"eligible","net_r":"2"}
    assert decide(e,"early_trigger",quote(),1000,cost)["code"]=="trial"
    assert decide(e,"structure_confirmed",quote(),1000,cost)["code"]=="participate"
    assert decide(e,"early_trigger",quote(),1000,cost,style="confirmed")["code"]=="waiting"
    assert decide(e,"structure_confirmed",quote("1.5"),10001,cost)["code"]=="expired"
    assert decide(e,"structure_confirmed",{**quote(".89"),"as_of":20000},20000,cost)["code"]=="ended"
    assert decide(e,"structure_confirmed",quote(".89"),20000,cost)["code"]=="ended"
    assert decide(e,"structure_confirmed",{**quote(".89"),"as_of":20000-90001},20000,cost)["code"]=="expired"
    assert decide(e,"structure_confirmed",{"price":"1","price_kind":"mark","as_of":1000},1000,cost)["code"]=="paused"

def test_trailing_cannot_loosen_original_or_previous():
    assert D(trailing_protection(bars(), "20","9",100*3600000))>=D("20")

def test_outcomes_do_not_count_prefill_high_as_win():
    data=[{"open_time":10,"close_time":20,"open":"1","high":"1.5","low":".99"}]
    assert evaluate_path(data,"1","1.2",".9",0,30)["state"]=="entry_bar_order_unknown"
