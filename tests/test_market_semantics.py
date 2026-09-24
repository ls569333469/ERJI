from decimal import Decimal as D
from engine.features.oi import quantity_change,nominal_decomposition,funding_window
from engine.features.indicators import closed_indicators
from engine.contracts import CandleDTO,RuntimeConfig
from pydantic import ValidationError
import json
import pytest

def test_price_rally_is_not_quantity_growth():
    growth,status=quantity_change(D("100"),D("100"),{"a"},{"a"})
    qty,value=nominal_decomposition(D("100"),D("100"),D("1"),D("10"))
    assert growth==0 and status=="fresh"
    assert qty==0 and value==900

def test_added_market_does_not_become_inflow():
    assert quantity_change(D("100"),D("200"),{"a"},{"a","b"})==(None,"coverage_changed")

@pytest.mark.parametrize("start,end",[(None,D(10)),(D(0),D(10)),(D(10),None)])
def test_unknown_or_zero_baseline_stays_unknown(start,end):
    assert quantity_change(start,end,{"a"},{"a"})==(None,"insufficient_history")

def test_nominal_decomposition_reconciles_total():
    quantity,value=nominal_decomposition(D(100),D(120),D(2),D(3))
    assert quantity+value==D(120)*D(3)-D(100)*D(2)

def test_predicted_funding_never_enters_settled_cost():
    result=funding_window([{"kind":"settled","time":100,"rate":"-0.001"},{"kind":"predicted","time":101,"rate":"-0.5"},{"kind":"settled","time":50,"rate":"1"}],50,101)
    assert result["count"]==1 and result["short_net_cost"]==D("0.001")

def test_current_candle_does_not_change_closed_indicators():
    bars=[{"open_time":i,"close_time":i+1,"is_closed":True,"high":str(i+11),"low":str(i+9),"close":str(i+10)} for i in range(30)]
    baseline=closed_indicators(bars)
    assert baseline["atr14"] is not None
    assert closed_indicators(bars+[{"open_time":30,"close_time":31,"is_closed":False,"high":"10000","low":"0","close":"9999"}])==baseline

def test_oi_one_day_has_no_duplicate_config_field():
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({"closed_day_oi":True})

def test_settings_cannot_exceed_accepted_small_pool():
    assert RuntimeConfig(symbols=["NEW_NATIVE_ASSET"]).symbols==["NEW_NATIVE_ASSET"]
    with pytest.raises(ValidationError):RuntimeConfig(symbols=[])
    with pytest.raises(ValidationError):RuntimeConfig(collector_interval_seconds=1)

def test_decimal_dto_preserves_small_price():
    item=CandleDTO(instrument_id="x",resolution="240",open_time=1,close_time=2,is_closed=True,open=D("0.0000000123456789"),high=D("0.00000002"),low=D("0.00000001"),close=D("0.000000015"),base_volume=D("100"),quote_volume=None,revision=1,received_at=3)
    value=json.loads(item.model_dump_json())["open"]
    assert isinstance(value,str) and D(value)==D("0.0000000123456789")
