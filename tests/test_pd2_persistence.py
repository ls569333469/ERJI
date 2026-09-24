"""PostgreSQL persistence regressions. All test writes roll back together."""
import copy,sys
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from engine.db import engine
from engine.models import *
from engine.pd2 import snapshots,tasks

@pytest.fixture
def isolated(monkeypatch):
    connection=engine.connect();transaction=connection.begin()
    factory=sessionmaker(bind=connection,expire_on_commit=False,join_transaction_mode='create_savepoint')
    for name,module in list(sys.modules.items()):
        if name.startswith('engine.') and hasattr(module,'Session'):monkeypatch.setattr(module,'Session',factory)
    try:yield factory
    finally:transaction.rollback();connection.close()

def test_dataset_persists_same_values_after_new_data_and_deduplicates(isolated):
    data={'timeframe_assessments':[{'timeframe':'60','series':[{'time':1,'ema20':'1.123456'}]}],
          'candles_at_snapshot':{'60':[{'open_time':1,'close':'1.20'}]},'derivative_history_at_snapshot':{'oi':[{'time':1,'quantity':'20'}]}}
    with isolated.begin() as s:
        first=snapshots.freeze_datasets(s,data);s.flush();second=snapshots.freeze_datasets(s,data);s.flush()
        assert first['datasets']==second['datasets']
    data['candles_at_snapshot']['60'][0]['close']='999';data['timeframe_assessments'][0]['series'][0]['ema20']='888'
    with isolated() as s:
        restored=snapshots.hydrate(s,first)
        assert restored['candles_at_snapshot']['60'][0]['close']=='1.20'
        assert restored['timeframe_assessments'][0]['series'][0]['ema20']=='1.123456'
        assert restored['derivative_history_at_snapshot']['oi'][0]['quantity']=='20'

def test_expired_lease_reclaimed_and_old_owner_cannot_complete(isolated,monkeypatch):
    clock=[10000];monkeypatch.setattr(tasks,'now_ms',lambda:clock[0])
    first=tasks.claim('test','isolated_pd2',1000)
    assert tasks.claim('test','isolated_pd2',1000) is None
    clock[0]+=1001;second=tasks.claim('test','isolated_pd2',1000)
    assert second['owner']!=first['owner'];tasks.finish(first)
    with isolated() as s:assert s.get(Task,second['id']).state=='leased'
    tasks.finish(second)
    with isolated() as s:assert s.get(Task,second['id']).state=='complete'
