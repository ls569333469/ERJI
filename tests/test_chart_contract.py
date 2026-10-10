from engine.pd2.chart_contract import drawing_contract
from engine.strategies.structures import VERSION


def snapshot(strategy="confirmed_breakout", action="trial"):
    return {
        "id": "snap-1", "instrument_id": "binance:perp:TESTUSDT", "episode_id": "box-1",
        "strategy_id": strategy, "rule_version": VERSION, "as_of": 5_000_000,
        "valid_until": 5_010_000, "action": {"code": action},
        "market": {"quote_asset": "USDT"}, "current_protection": "1.80",
        "opportunity_plan": {"t1": {"low": "2.40", "high": "2.50", "known_at": 4_000_000,
                                    "timeframe": "240", "method": "confirmed_pivot"}},
        "evidence": {
            "box": {"upper": "2.00", "lower": "1.70", "trigger_price": "2.05",
                    "start_time": 1_000_000, "end_time": 3_000_000, "rule_version": VERSION},
            "trigger": {"open_time": 3_000_000, "close_time": 4_000_000,
                        "close": "2.10", "resolution": "240"},
            "entry_zone": ["2.05", "2.20"],
        },
    }


def test_box_anchors_and_plan_share_snapshot_identity():
    result = drawing_contract(snapshot())
    assert result["snapshot_id"] == "snap-1"
    assert result["event_id"] == "box-1"
    assert result["strategy_version"] == VERSION
    assert result["status"] == "confirmed"
    assert [a["kind"] for a in result["anchors"]] == ["box", "trigger_boundary", "trigger_bar"]
    assert result["entry_zone"]["low"] == "2.05"
    assert result["protection"]["price"] == "1.80"
    assert result["first_target"]["high"] == "2.50"


def test_retest_does_not_draw_parent_box():
    result = drawing_contract(snapshot(strategy="retest_entry"))
    assert result["status"] == "missing_anchors"
    assert result["anchors"] == []
    assert result["entry_zone"] is None


def test_protection_steps_start_when_observed_not_at_original_trigger():
    value = snapshot()
    value['tracking'] = {'protection_history': [
        {'price': '1.75', 'observed_at': 4_100_000},
        {'price': '1.80', 'observed_at': 4_900_000},
    ]}
    result = drawing_contract(value)
    assert result['protection_history'] == value['tracking']['protection_history']
    assert result['protection']['known_at'] == 4_900_000
    assert result['protection']['valid_from'] == 4_900_000
    value['action']['code'] = 'ended'
    result = drawing_contract(value)
    assert len(result['protection_history']) == 2
    assert result['protection'] is None


def test_invalid_protection_history_cannot_draw_a_continuous_path():
    for point in ({'price':'NaN','observed_at':4_500_000},
                  {'price':'1.8','observed_at':5_000_001},
                  {'price':'1.8','observed_at':3_900_000}):
        value = snapshot()
        value['tracking'] = {'protection_history': [point]}
        result = drawing_contract(value)
        assert result['protection_history'] == []
        assert result['protection']['known_at'] == value['as_of']


def test_ended_or_expired_plan_has_no_active_entry_or_target():
    ended = drawing_contract(snapshot(action="ended"))
    expired_snapshot = snapshot()
    expired_snapshot["valid_until"] = 4_999_999
    expired = drawing_contract(expired_snapshot)
    assert ended["status"] == expired["status"] == "confirmed"
    for result in (ended, expired):
        assert result["entry_zone"] is None
        assert result["protection"] is None
        assert result["first_target"] is None


def test_missing_or_future_trigger_does_not_draw_confirmed_structure():
    missing = snapshot()
    missing["evidence"]["box"].pop("upper")
    future = snapshot()
    future["evidence"]["trigger"]["close_time"] = 5_000_001
    assert drawing_contract(missing)["status"] == "missing_anchors"
    assert drawing_contract(future)["status"] == "missing_anchors"


def test_early_breakout_keeps_its_real_one_hour_trigger():
    early = snapshot(strategy="early_breakout")
    early["evidence"]["trigger"]["resolution"] = "60"
    result = drawing_contract(early)
    assert result["status"] == "confirmed"
    assert result["anchors"][-1]["timeframe"] == "60"
    early["evidence"]["trigger"]["resolution"] = "240"
    assert drawing_contract(early)["status"] == "missing_anchors"


def test_target_needs_known_time_and_valid_price_range():
    value = snapshot()
    value["opportunity_plan"]["t1"].pop("known_at")
    assert drawing_contract(value)["first_target"] is None
    value["opportunity_plan"]["t1"].update(known_at=4_000_000, high=None)
    assert drawing_contract(value)["first_target"]["high"] == "2.40"
    value["opportunity_plan"]["t1"]["low"] = "1.90"
    assert drawing_contract(value)["first_target"] is None


def test_invalid_structure_and_entry_prices_do_not_generate_lines():
    bad = snapshot()
    bad["evidence"]["box"]["upper"] = "NaN"
    assert drawing_contract(bad)["status"] == "missing_anchors"
    bad = snapshot()
    bad["evidence"]["entry_zone"] = ["1.75", "2.20"]
    assert drawing_contract(bad)["entry_zone"] is None
    assert drawing_contract(bad)["first_target"] is None


def test_nonbox_contract_uses_only_its_own_typed_anchors():
    from engine.strategies.p33 import VERSION as P33_VERSION
    value=snapshot(strategy="recent_high")
    value['price_pattern']={'strategy_id':'recent_high','strategy_version':P33_VERSION,
        'market_id':value['instrument_id'],'event_id':value['episode_id'],'status':'triggered',
        'timeframe':'240','trigger':{'close_time':4_000_000},'required_indicators':[],
        'anchors':[{'kind':'boundary','price':'2','known_at':3_000_000,'start_time':1_000_000,'label':'30日高点'},
                   {'kind':'trigger_bar','price':'2.1','open_time':3_000_000,'close_time':4_000_000,'known_at':4_000_000,'timeframe':'240'}]}
    actual=drawing_contract(value)
    assert actual['status']=='confirmed'
    assert all(a['kind']!='box' for a in actual['anchors'])
    assert actual['entry_zone']  # P35: actual execution eligibility authorizes plan layers, not report approval.
    value['action']['code']='paused'
    assert drawing_contract(value)['entry_zone'] is None
    value['price_pattern']['anchors'][0]['known_at']=6_000_000
    assert drawing_contract(value)['status']=='missing_anchors'
