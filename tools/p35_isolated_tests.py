"""Run regression in a disposable database, never against live collection locks."""
from tools.p34_local import prepare
prepare()
import os, time, json
from pathlib import Path
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from engine.config import settings

live_url=make_url(settings().database_url)
name='erji_p35_check_'+str(int(time.time()))
admin=create_engine(live_url,isolation_level='AUTOCOMMIT')
with admin.connect() as c:c.exec_driver_sql('CREATE DATABASE "'+name+'"')
try:
    os.environ['ERJI_DATABASE_URL']=live_url.set(database=name).render_as_string(hide_password=False)
    settings.cache_clear()
    from engine.db import engine,Base
    from engine import models
    Base.metadata.create_all(engine)
    # The existing API integration tests require real BTC market/candle records.
    # Copy bounded public observations; all writes and fixture locks stay in the test database.
    source=create_engine(live_url)
    iid='binance:perp:BTCUSDT'
    with source.connect() as s,engine.begin() as d:
        inst=dict(s.execute(select(models.Instrument.__table__).where(models.Instrument.id==iid)).mappings().one())
        asset=dict(s.execute(select(models.Asset.__table__).where(models.Asset.id==inst['asset_id'])).mappings().one())
        d.execute(models.Asset.__table__.insert(),asset);d.execute(models.Instrument.__table__.insert(),inst)
        state=dict(s.execute(select(models.MarketState.__table__).where(models.MarketState.instrument_id==iid)).mappings().one())
        d.execute(models.MarketState.__table__.insert(),state)
        candles=[]
        for tf in ('60','240','1D','1W'):
            candles.extend(dict(r) for r in s.execute(select(models.Candle.__table__).where(models.Candle.instrument_id==iid,models.Candle.resolution==tf).order_by(models.Candle.open_time.desc()).limit(300)).mappings())
        refs={r['raw_ref'] for r in candles}
        raw=[dict(r) for r in s.execute(select(models.RawEvent.__table__).where(models.RawEvent.id.in_(refs))).mappings()]
        if raw:d.execute(models.RawEvent.__table__.insert(),raw)
        if candles:d.execute(models.Candle.__table__.insert(),candles)
    source.dispose()
    import pytest
    result=pytest.main(['tests','-q','--basetemp=./.test-tmp/p35-isolated','--junitxml=docs/p35-validation/tests.xml'])
    Path('docs/p35-validation/test-isolation.json').write_text(json.dumps({'database':name,'source':'bounded read-only copy of BTC public market and candles','candles_copied':len(candles),'exit_code':int(result),'production_writes':False,'database_disposed_after_run':True},indent=2),encoding='utf8')
finally:
    if 'engine' in globals():engine.dispose()
    with admin.connect() as c:c.exec_driver_sql('DROP DATABASE "'+name+'" WITH (FORCE)')
    admin.dispose()
raise SystemExit(result)
