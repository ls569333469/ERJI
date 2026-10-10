"""Public spot endpoint retains the conservative Binance request-weight budget."""
import pytest
from engine.pd2.request_budget import budget_key, budget_spec

@pytest.mark.parametrize('path,params,weight',[
    ('exchangeInfo',{},20),('ticker/24hr',{'type':'MINI'},80),
    ('depth',{'limit':100},5),('klines',{'limit':1000},2)])
def test_public_domain_uses_binance_weight_budget(path,params,weight):
    host,cost,cap,gap=budget_spec('https://data-api.binance.vision/api/v3/'+path,{'params':params})
    assert host=='data-api.binance.vision'
    assert (cost,cap,gap)==(weight,5400,0)


@pytest.mark.parametrize('url,expected',[
    ('https://fapi.binance.com/fapi/v1/openInterest',(1,2160,0)),
    ('https://www.asterdex.com/api/v1/ticker',(1,240,500)),
    ('https://api.hyperliquid.xyz/info',(20,300,1000)),
    ('https://www.okx.com/api/v5/public/open-interest',(1,120,500)),
])
def test_source_budgets_are_conservative(url,expected):
    _,cost,cap,gap=budget_spec(url,{})
    assert (cost,cap,gap)==expected


@pytest.mark.parametrize('host,family',[
    ('data-api.binance.vision','binance_family'),
    ('fapi.binance.com','binance_family'),
    ('www.binance.com','binance_family'),
    ('fapi.asterdex.com','aster_family'),
    ('api.hyperliquid.xyz','hyperliquid_family'),
    ('www.okx.com','okx_family'),
])
def test_related_hosts_share_one_source_budget(host,family):
    assert budget_key(host)==family
