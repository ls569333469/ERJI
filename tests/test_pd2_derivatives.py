from decimal import Decimal as D
from engine.pd2.derivatives import cmc_markets,model_map,whale_positions,settled_costs,oi_daily_baseline

def market(oi=100,at=1700000000000):
 return {"market_id":1,"category":"perpetual","exchange":{"exchange_id":270,"exchange_slug":"binance"},"market_pair_base":{"crypto_id":1},"quotes":[{"convert_symbol":"USD","open_interest":oi,"last_updated":at}]}
def test_cmc_duplicate_not_sum_and_conflict_isolated():
 r=market();d={"data":{"market_pairs":[r,r],"num_market_pairs":1}}
 assert cmc_markets(d,1,1700000000000)["total_oi_usd"]=="100"
 d["data"]["market_pairs"].append(market(200))
 out=cmc_markets(d,1,1700000000000)
 assert out["total_oi_usd"] is None and out["excluded"][0]["reason"]=="duplicate_conflict"
def test_zero_oi_valid_but_stale_missing_and_identity_not_zero():
 assert cmc_markets({"data":{"market_pairs":[market(0)]}},1,1700000000000)["total_oi_usd"]=="0"
 for identity,at in [(2,1700000000000),(1,1700000400000)]:
  assert cmc_markets({"data":{"market_pairs":[market()]}},identity,at)["total_oi_usd"] is None

def test_models_never_become_reported_positions_or_predicted_upside():
 doc={"symbol":"BTC","exchange_symbol":"BTCUSDT","as_of":1700000000000,"meta":{"max_stale_age_ms":600000},"real_levels":{"reference_price":100,"levels":[{"price":110,"side":"short","notional_usd":100}]},"real_liquidations":{"points":[]}}
 out=model_map(doc,"BTC","BTCUSDT",100,1700000000000)
 assert out["status"]=="stale" and out["levels"][0]["kind"]=="modeled"
 assert out["levels"][0]["distance_pct"]=="10.0" and out["extra_upside_forecast"] is None
 assert not out["levels"][0]["action_eligible"]
def test_whale_null_and_namespace_do_not_create_exact_market_risk():
 doc={"positions":[{"coin":"ASTER","address":"0xabc","size":-2,"liq_px":None},{"coin":"xyz:ASTER","address":"0xdef","size":2,"liq_px":3}],"accounts_scanned":300}
 p=whale_positions(doc,"ASTER",1700000000000)["positions"]
 assert len(p)==1 and p[0]["kind"]=="unavailable" and p[0]["liquidation_price"] is None
 assert p[0]["status"]=="source_time_unknown"
def test_funding_uses_real_cycles_and_gaps():
 h=3600000;rows=[{"time":i*h,"rate":"-.001","interval_seconds":3600} for i in range(1,25)]
 assert settled_costs(rows,24*h)[2]["settled_rate_sum"]=="-0.024"
 assert settled_costs(rows[:10]+rows[11:],24*h)[2]["status"]=="gap"
 assert settled_costs([],24*h)[2]["settled_rate_sum"] is None

def test_overlapping_minutes_do_not_fake_daily_sample_count():
 points=[{"time":1700000000000+i*60000,"quantity":"100"} for i in range(500)]
 out=oi_daily_baseline(points,100)
 assert out["independent_days"]<=2 and out["percentile"] is None


def test_funding_tail_duplicate_and_zero_require_observed_coverage():
    h=3600000
    rows=[{'time':i*8*h,'rate':'.001','interval_seconds':28800} for i in range(1,5)]
    assert settled_costs(rows,33*h)[2]['status']=='tail_unverified'
    full=settled_costs(rows,33*h,33*h)
    assert full[0]['settled_rate_sum']=='0' and full[0]['settlement_count']==0
    assert full[2]['settled_rate_sum']=='0.003'
    assert settled_costs(rows+[rows[-1]],33*h,33*h)[2]['settled_rate_sum']=='0.003'
    bad=rows+[{**rows[-1],'rate':'.002'}]
    assert settled_costs(bad,33*h,33*h)[2]['status']=='conflicting_settlement'
