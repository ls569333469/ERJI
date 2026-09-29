import asyncio
import pytest
from pydantic import ValidationError
from engine.contracts import RuntimeConfig
from engine.universe import select_binance_universe
from engine.adapters.binance_universe import BinanceUniverseAdapter

def spec(base, **changes):
    return {"symbol":base+"USDT","baseAsset":base,"quoteAsset":"USDT","status":"TRADING",
            "contractType":"PERPETUAL","underlyingType":"COIN","pricePrecision":6,**changes}

def test_universe_includes_alts_by_volume_excludes_ineligible_markets():
    catalog=[spec("BTC"),spec("DOGE"),spec("1000PEPE"),spec("USDC"),spec("DELIST",status="SETTLING"),spec("STOCK",underlyingType="INDEX")]
    volumes=[{"symbol":s["symbol"],"quoteVolume":str(i*100+1)} for i,s in enumerate(catalog)]
    selected,count,_=select_binance_universe(catalog,volumes,150)
    assert [s["baseAsset"] for s in selected]==["1000PEPE","DOGE","BTC"]
    assert count==3

def test_budget_deduplicates_native_assets_and_breaks_ties_stably():
    catalog=[spec("B"),spec("A"),spec("A",symbol="ALT_A_USDT")]
    selected,count,_=select_binance_universe(catalog,[{"symbol":s["symbol"],"quoteVolume":"100"} for s in catalog],1)
    assert count==2 and len(selected)==2 and selected[0]["baseAsset"]=="A"

def test_missing_activity_does_not_remove_eligible_catalog_assets():
    selected,_,_=select_binance_universe([spec("A"),spec("B")],[{"symbol":"AUSDT","quoteVolume":"NaN"},{"symbol":"BUSDT","quoteVolume":"-5"}],150)
    assert {s["baseAsset"] for s in selected}=={"A","B"}

@pytest.mark.parametrize("values",[{"asset_limit":1001},{"asset_limit":9},{"deep_asset_limit":501}])
def test_scan_budgets_are_enforced(values):
    with pytest.raises(ValidationError): RuntimeConfig(**values)

def test_native_units_and_failure_isolation(monkeypatch):
    monkeypatch.setattr("engine.pd2.collection_schedule.due",lambda venue,natives,**kw:natives)
    monkeypatch.setattr("engine.pd2.collection_schedule.history_seeded",lambda venue:{"1000PEPEUSDT","DOGEUSDT"})
    async def scenario():
        adapter=BinanceUniverseAdapter();calls=[]
        async def request(batch,method,url,**kwargs):
            endpoint=url.rsplit("/",1)[-1];calls.append(endpoint)
            symbol=kwargs.get("params",{}).get("symbol")
            if endpoint=="exchangeInfo":data={"symbols":[spec("1000PEPE"),spec("DOGE")]}
            elif endpoint=="24hr":data=[{"symbol":"1000PEPEUSDT","quoteVolume":"1000"},{"symbol":"DOGEUSDT","quoteVolume":"900"}]
            elif endpoint=="premiumIndex":data=[{"symbol":"1000PEPEUSDT","markPrice":"0.01","time":100000000},{"symbol":"DOGEUSDT","markPrice":"0.2","time":100000000}]
            elif endpoint=="openInterest":
                if symbol=="DOGEUSDT":raise TimeoutError()
                data={"openInterest":"200","time":100000000}
            elif endpoint=="fundingRate":data=[{"fundingTime":10000000,"fundingRate":"-0.0001"},{"fundingTime":38800000,"fundingRate":"-0.0002"}]
            else:raise AssertionError(endpoint)
            return data,"raw",100000000
        adapter.request=request
        try:
            batch=await adapter.collect(RuntimeConfig())
            assert len(batch.states)==2 and len(batch.errors)==1
            row=next(r for r in batch.states if "1000PEPE" in r["instrument_id"])
            assert row["oi_base"]=="200" and row["oi_notional_quote"]=="2.00"
            assert next(r for r in batch.states if "DOGE" in r["instrument_id"])["oi_status"]=="missing"
            assert batch.metadata["selected_assets"]==2 and "klines" not in calls
        finally:await adapter.close()
    asyncio.run(scenario())

def test_deep_history_includes_dex_assets_inside_shared_budget():
    from engine.universe import select_deep_ids
    specs=[spec("ALT"+str(i)) for i in range(100)]+[spec("BTC")]
    ids=select_deep_ids(specs,80,["BTC","ETH","SOL","HYPE"])
    assets={iid.split(":")[-1][:-4] for iid in ids}|{"BTC","ETH","SOL","HYPE"}
    assert len(assets)==80 and "binance:perp:BTCUSDT" in ids
