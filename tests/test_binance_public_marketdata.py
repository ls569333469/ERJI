"""Public spot endpoint retains the conservative Binance request-weight budget."""
import pytest
from engine.pd2.request_budget import budget_spec

@pytest.mark.parametrize('path,params,weight',[
    ('exchangeInfo',{},20),('ticker/24hr',{'type':'MINI'},80),
    ('depth',{'limit':100},5),('klines',{'limit':1000},5)])
def test_public_domain_uses_binance_weight_budget(path,params,weight):
    host,cost,cap,gap=budget_spec('https://data-api.binance.vision/api/v3/'+path,{'params':params})
    assert host=='data-api.binance.vision'
    assert (cost,cap,gap)==(weight,600,250)
