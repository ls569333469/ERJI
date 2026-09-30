import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from engine.adapters.base import Batch
from engine.pd2 import collection_schedule
from engine.models import WorkerHeartbeat
from services.worker import main


@pytest.fixture
def bridge(monkeypatch):
    market = Batch('binance', states=[{'instrument_id': 'binance:perp:BTCUSDT'}])
    candles = Batch('binance', candle_only=True, candles=[
        {'instrument_id': 'binance:perp:BTCUSDT', 'resolution': '60'},
    ])
    adapter = SimpleNamespace(venue='binance', collect=AsyncMock(return_value=market),
                              collect_candles=AsyncMock(return_value=candles), close=AsyncMock())
    monkeypatch.setattr(main, 'BinanceUniverseAdapter', lambda: adapter)
    monkeypatch.setattr(main, 'runtime_config', lambda: SimpleNamespace(collector_enabled=True))
    monkeypatch.setattr(main, 'candle_targets', lambda venue: [
        ('binance:perp:BTCUSDT', 'BTCUSDT', '60', '1h', 10),
        ('binance:perp:BTCUSDT', 'BTCUSDT', '240', '4h', 10),
    ])
    saved, health, acknowledged = Mock(), Mock(), Mock()
    monkeypatch.setattr(main, 'save_batch', saved)
    monkeypatch.setattr(main, 'heartbeat', health)
    monkeypatch.setattr(main, 'save_failure', Mock())
    monkeypatch.setattr(collection_schedule, 'acknowledge_batch', Mock())
    monkeypatch.setattr(collection_schedule, 'acknowledge', acknowledged)
    return adapter, saved, health, acknowledged


def test_incomplete_timeframes_are_not_acknowledged(bridge):
    adapter, saved, health, acknowledged = bridge
    asyncio.run(main.run_binance_bridge(once=True))
    assert saved.call_count == 2
    acknowledged.assert_not_called()
    adapter.close.assert_awaited_once()


def test_complete_candles_acknowledged_after_save(bridge):
    adapter, saved, health, acknowledged = bridge
    adapter.collect_candles.return_value.candles.append(
        {'instrument_id': 'binance:perp:BTCUSDT', 'resolution': '240'})
    asyncio.run(main.run_binance_bridge(once=True))
    acknowledged.assert_called_once_with('binance', {'BTCUSDT'}, 'candles')
    assert health.call_args.args[2]['candles'] == 2


def test_failed_collection_fails_once_and_closes_adapter(bridge):
    adapter, _, _, _ = bridge
    adapter.collect.side_effect = ConnectionError('test')
    with pytest.raises(ConnectionError):
        asyncio.run(main.run_binance_bridge(once=True))
    adapter.close.assert_awaited_once()


def test_candle_errors_report_partial_and_fail_once(bridge):
    adapter, _, health, _ = bridge
    adapter.collect_candles.return_value.errors.append('BTCUSDT:candles:240:error')
    with pytest.raises(RuntimeError, match='incomplete_collection'):
        asyncio.run(main.run_binance_bridge(once=True))
    assert health.call_args.args[1] == 'partial'


def test_server_defers_only_to_recent_bridge_heartbeat(monkeypatch):
    from engine.adapters import base
    from engine import db

    class SessionStub:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, model, role):
            assert model is WorkerHeartbeat and role == 'bridge_binance'
            return WorkerHeartbeat(role=role, state='ok', updated_at=950, details={})

    monkeypatch.setattr(db, 'Session', SessionStub)
    monkeypatch.setattr(base, 'now_ms', lambda: 1000)
    assert main.binance_bridge_active()
    monkeypatch.setattr(base, 'now_ms', lambda: 121001)
    assert not main.binance_bridge_active()
