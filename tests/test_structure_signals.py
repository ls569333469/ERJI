from decimal import Decimal as D
from types import SimpleNamespace as NS
from engine.strategies.structures import closed, pivots, find_box, evaluate, volume_multiple, target, FOUR_HOURS as H4
from engine.features.observations import paired_changes, describe_market


def bar(i, close=100, volume=100, step=H4, high=None, low=None):
    return dict(open_time=i*step, close_time=(i+1)*step-1, is_closed=True,
                open="100", close=str(close), high=str(high or max(102,close+1)),
                low=str(low or min(98,close-1)), quote_volume=str(volume))


def frozen_box():
    return dict(upper="110", lower="90", atr="8", trigger_price="112",
                start_time=0, end_time=30*H4-1, lookback=20)


def test_unclosed_and_future_candles_cannot_confirm():
    history=[bar(i) for i in range(30)]
    trigger=bar(30,120,200)
    assert evaluate(frozen_box(),history+[trigger],[],30*H4)["phase"]=="structure_pending"
    trigger["is_closed"]=False
    assert evaluate(frozen_box(),history+[trigger],[],31*H4)["phase"]=="structure_pending"
    assert len(closed(history+[trigger],31*H4))==30


def test_pivot_requires_two_completed_right_bars():
    bars=[bar(i,high=v) for i,v in enumerate([101,102,110,102,101])]
    assert pivots(bars[:-1],"high")==[]
    assert pivots(bars,"high")[0]["known_at"]==bars[4]["close_time"]


def test_box_uses_repeated_swings_and_rejects_drift_and_gaps():
    highs=[104,106,110,105,104,103];lows=[97,96,95,96,90,95]
    bars=[bar(i,high=highs[i%6],low=lows[i%6]) for i in range(42)]
    b=find_box(bars,20)
    assert b and D(b["upper"])==110 and D(b["lower"])==90
    assert b["upper_touches"]>=2 and b["lower_touches"]>=2
    assert find_box([bar(i,100+i) for i in range(42)],20) is None
    assert find_box(bars[:-5]+bars[-4:],20) is None


def test_breakout_needs_volume_and_close_above_buffer():
    history=[bar(i) for i in range(30)]
    assert volume_multiple(bar(30,120,200),history)==2
    assert evaluate(frozen_box(),history+[bar(30,120,149)],[],31*H4)["phase"]=="structure_pending"
    assert evaluate(frozen_box(),history+[bar(30,112,200)],[],31*H4)["phase"]=="structure_pending"
    confirmed=evaluate(frozen_box(),history+[bar(30,120,200)],[],31*H4)
    assert confirmed["phase"]=="structure_confirmed" and confirmed["volume_multiple"]=="2"
    assert confirmed["protection"]=="108.00"


def test_pending_structure_exposes_rule_based_invalidation_and_protection_reference():
    history=[bar(i) for i in range(30)]
    pending=evaluate(frozen_box(),history,[],30*H4)
    assert pending["invalidation"]=={"price":"90","timeframe":"240","condition":"close_below","method":"frozen_box_lower"}
    assert pending["protection_reference"]["price"]=="108.00"
    assert pending["protection_reference"]["status"]=="estimated"
    assert pending["trigger_requirements"]["relative_volume_min"]=="1.5"


def test_close_above_without_required_volume_is_not_called_confirmed():
    history=[bar(i) for i in range(30)]
    pending=evaluate(frozen_box(),history+[bar(30,120,120)],[],31*H4)
    assert pending["phase"]=="structure_pending"
    assert pending["pending_state"]=="close_above_volume_unconfirmed"
    assert pending["unconfirmed_breakouts"][0]["volume_multiple"]=="1.2"


def test_target_preserves_method_timeframe_and_confirmation_time():
    bars=[bar(i,high=v) for i,v in enumerate([100,101,110,101,100]+[100]*20)]
    result=target(bars,D("101"),"4H")
    assert result and result["method"]=="confirmed_pivot_atr_zone"
    assert result["timeframe"]=="4H" and result["known_at"]==bars[4]["close_time"]


def test_early_revoke_does_not_resurrect_at_later_four_hour_close():
    history=[bar(i) for i in range(30)]
    one=[bar(i,step=3600000) for i in range(120)]
    one += [bar(120,114,200,3600000),bar(121,109,100,3600000),bar(122,115,100,3600000),bar(123,120,200,3600000)]
    state=evaluate(frozen_box(),history+[bar(30,120,200)],one,31*H4)
    assert state["phase"]=="invalidated" and "1H" in state["reason"]


def test_confirmed_revocation_and_frozen_plan_survive_rolling_history():
    bars=[bar(i) for i in range(30)]+[bar(30,120,200)]
    original=evaluate(frozen_box(),bars,[],31*H4)
    original["entry_zone"]=["112","115.75"]
    rolled=[bar(i,121,150) for i in range(31,80)]
    later=evaluate(frozen_box(),rolled,[],80*H4,original)
    for key in ("trigger","protection","entry_zone","expires_at","volume_multiple"):
        assert later[key]==original[key]
    assert later["phase"]=="structure_confirmed"
    revoked=evaluate(frozen_box(),rolled+[bar(80,109)],[],81*H4,original)
    assert revoked["phase"]=="invalidated"


def test_target_requires_own_timeframe_history():
    assert target([bar(i) for i in range(10)],D(100)) is None
    assert target([bar(i,100+i) for i in range(40)],D(100)) is None


def test_quantity_decline_can_coexist_with_notional_growth():
    result=paired_changes(D(100),D(10),D(80),D(1600),"USDT")
    assert result["quantity_contribution_quote"]==-200
    assert result["price_contribution_quote"]==800
    assert result["notional_change_pct"]==60
    assert result["price_change_pct"]==100
    assert paired_changes(None,D(10),D(80),D(1600),"USDT")=={}


def test_observation_does_not_fallback_or_infer_short_liquidation():
    quality=NS(status="fresh")
    change=NS(period="4H",quality=quality,value_pct=D(-20),price_change_pct=D(100))
    m=NS(quality=quality,oi_quality=quality,oi_changes=[change])
    assert describe_market(m,"4H")["group"]=="up_unwind"
    assert describe_market(m,"1H")["group"]=="insufficient"
    m.oi_quality=NS(status="stale")
    assert describe_market(m,"4H")["group"]=="stale"


def test_live_scan_preserves_first_report_and_returns_unique_assets():
    from engine.signal_service import scan_signals, signal_list
    before={r["id"]:(r["first_seen_at"],r["first_seen_price"]) for r in signal_list()["rows"]}
    scan_signals()
    after=signal_list()
    assert len({r["asset_id"] for r in after["rows"]})==len(after["rows"])
    for row in after["rows"]:
        if row["id"] in before:
            assert (row["first_seen_at"],row["first_seen_price"])==before[row["id"]]
        assert row["action"]["code"] not in ("participate","trial")
    assert sum(g["asset_count"] for g in after["groups"])==len(after["rows"])


def test_participation_cannot_be_issued_from_structure_alone(monkeypatch):
    from engine.signal_service import action_for
    monkeypatch.setattr("engine.signal_service.now_ms",lambda:1000)
    evidence={"box":{"lower":"90"},"reason":"confirmed","entry_zone":["112","116"],"protection":"108","expires_at":2000}
    for phase in ("early_trigger","structure_confirmed","trend_continuing"):
        assert action_for(evidence,phase,"114",True)["code"]=="execution_pending"
    assert action_for(evidence,"structure_confirmed","117",True)["code"]=="no_chase"
    assert action_for(evidence,"structure_confirmed","111",True)["code"]=="waiting"
    assert action_for(evidence,"structure_confirmed","114",False)["code"]=="paused"
    assert action_for({**evidence,"expires_at":999},"structure_confirmed","114",True)["code"]=="expired"
    assert action_for({**evidence,"entry_zone":None},"structure_confirmed","114",True)["code"]=="paused"


def diagnostic_row(asset,phase,code,price="114",expires=2000):
    return {"asset_id":asset,"phase":phase,"action":{"code":code},"fresh":True,"first_seen_at":500,
            "market":{"price":price},"evidence":{"entry_zone":["112","116"],"protection":"108","expires_at":expires}}


def test_suggested_list_explains_structure_rejections_without_promoting_them():
    from engine.signal_service import signal_payload
    rows=[diagnostic_row("IN_ZONE","structure_confirmed","execution_pending"),
          diagnostic_row("LATE","structure_confirmed","no_chase","120",999),
          diagnostic_row("EARLY","early_trigger","execution_pending"),
          diagnostic_row("WAIT","structure_pending","waiting")]
    payload=signal_payload(rows,"recommended",1000,{})
    assert payload["rows"]==[] and payload["action_counts"]=={"participate":0,"trial":0}
    assert payload["eligibility"]["status"]=="not_enabled"
    assert payload["eligibility"]["total_events"]==4
    candidates=payload["eligibility"]["candidates"]
    assert [r["asset_id"] for r in candidates]==["IN_ZONE","LATE"]
    assert candidates[0]["blockers"]==[]
    assert {b["code"] for b in candidates[1]["blockers"]}=={"above_zone","expired"}
    assert [r["asset_id"] for r in signal_payload(rows,"trial",1000,{})["eligibility"]["candidates"]]==["EARLY"]
    assert signal_payload(rows,"stages",1000,{})["rows"]==rows


def test_recommendation_diagnostics_preserve_unavailable_plan_reasons():
    from engine.signal_service import signal_payload
    row=diagnostic_row("MISSING","structure_confirmed","paused")
    row["fresh"]=False
    row["evidence"]["protection"]=None
    reasons=signal_payload([row],"recommended",1000,{})["eligibility"]["candidates"][0]["blockers"]
    assert {r["code"] for r in reasons}=={"stale","incomplete"}
