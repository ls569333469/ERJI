from decimal import Decimal as D
import pytest
from fastapi.testclient import TestClient
from services.api.main import app

@pytest.fixture
def client():
    with TestClient(app) as c:yield c

def test_live_db_health_and_strategy_boundary(client):
    data=client.get("/v1/health").json()
    assert data["database"]=="postgresql"
    assert data["strategy_status"]=="pd2_decisions"
    assert data["execution_status"]=="native_book_cost_gated"
    data=client.get("/v1/signals?view=recommended").json()
    assert len(data["groups"])==4
    assert all(r["action_code"] in ("participate","trial") for r in data["rows"])

def test_market_oi_day_label_reuses_24h(client):
    rows=client.get("/v1/markets").json()["rows"]
    assert rows, "Run one real collector cycle before integration checks"
    for row in rows:
        windows=row["oi_changes"]
        assert [(x["period"],x["display_label"]) for x in windows]==[("1H","1H"),("4H","4H"),("24H","1D")]
        if row["oi_cap_ratio"] is not None:
            assert row["valuation_status"]=="fresh" and D(row["circulating_cap"])>0
        assert isinstance(row["price"],str)

def test_candle_pagination_has_no_overlap(client):
    instrument="binance:perp:BTCUSDT"
    first=client.get("/v1/candles",params={"instrument":instrument,"resolution":"240","limit":5}).json()
    second=client.get("/v1/candles",params={"instrument":instrument,"resolution":"240","limit":5,"before":first["next_before"]}).json()
    assert len(first["rows"])==5
    assert max(r["open_time"] for r in second["rows"])<min(r["open_time"] for r in first["rows"])

def test_cross_origin_settings_write_denied(client):
    r=client.post("/v1/settings/drafts",json={"config":{}},headers={"origin":"https://external.example","x-erji-client":"local-ui"})
    assert r.status_code==403

def test_unknown_market_and_bad_resolution_are_not_fabricated(client):
    assert client.get("/v1/candles?instrument=imaginary").status_code==404
    assert client.get("/v1/candles?instrument=imaginary&resolution=42").status_code==422

def test_settings_publish_version_check(client):
    headers={"x-erji-client":"local-ui"}
    active=client.get("/v1/settings/active").json()
    draft=client.post("/v1/settings/drafts",headers=headers,json={"config":active["config"]}).json()
    conflict=client.post(f"/v1/settings/drafts/{draft['draft_id']}/publish",headers=headers,json={"expected_version":-1})
    assert conflict.status_code==409
    result=client.post(f"/v1/settings/drafts/{draft['draft_id']}/publish",headers=headers,json={"expected_version":active["version"]})
    assert result.status_code==200
    assert client.get("/v1/settings/active").json()["config"]==active["config"]
