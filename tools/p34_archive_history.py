"""Official checksum-verified BTC 1H archives; isolated real-history acceptance.

Only the revision/recovery stage injects a fault, explicitly labelled below.
No archived candle is published as a current signal or written to the live schema.
"""
from tools.p34_local import prepare
prepare()
import asyncio,csv,io,json,ssl,uuid,zipfile
from pathlib import Path
from hashlib import sha256
from decimal import Decimal as D
from copy import deepcopy
from urllib.request import getproxies
import httpx
from sqlalchemy import create_engine,text,insert
from sqlalchemy.orm import sessionmaker
from engine.db import engine,Base
from engine.models import Asset,Instrument,RawEvent,Candle,SnapshotDataset
from engine.adapters.base import now_ms
from engine.pd2.request_budget import request_slot,feedback
from engine.pd2.persistent_avwap import update,read_series
from engine.pd2.offline_indicators import build,read,source_state

DEST=Path('docs/p34-validation/official-long-history');IID='binance:perp:BTCUSDT';STEP=3600000


async def download():
    DEST.mkdir(parents=True,exist_ok=True);rows=[];sources=[]
    async with httpx.AsyncClient(timeout=25,verify=ssl.create_default_context(),proxy=getproxies().get('https')) as client:
        for n in range(28):
            year,month=2023+n//12,n%12+1;name=f'BTCUSDT-1h-{year}-{month:02}.zip'
            url='https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1h/'+name
            path=DEST/name;check=DEST/(name+'.CHECKSUM');meta=DEST/(name+'.receipt.json')
            if not (path.exists() and check.exists() and meta.exists()):
                for address,destination in [(url+'.CHECKSUM',check),(url,path)]:
                    async with request_slot(address,{}):
                        response=await client.get(address)
                        await asyncio.to_thread(feedback,address,response.status_code,response.headers)
                        response.raise_for_status();destination.write_bytes(response.content)
                    await asyncio.sleep(.5)
                meta.write_text(json.dumps({'received_at':now_ms(),'url':url}),encoding='utf8')
            receipt=json.loads(meta.read_text());digest=sha256(path.read_bytes()).hexdigest()
            assert check.read_text().split()[0]==digest,'archive checksum mismatch'
            with zipfile.ZipFile(path) as archive:
                assert len(archive.namelist())==1
                content=archive.read(archive.namelist()[0]);count=0
                for row in csv.reader(io.StringIO(content.decode('utf8'))):
                    if not row[0].isdigit():continue
                    start,end=int(row[0]),int(row[6])+1
                    assert end-start==STEP and end<=receipt['received_at']
                    rows.append(dict(instrument_id=IID,resolution='60',open_time=start,close_time=end,is_closed=True,
                        open=row[1],high=row[2],low=row[3],close=row[4],base_volume=row[5],quote_volume=row[7],
                        received_at=receipt['received_at'],raw_ref=digest,revision=1));count+=1
            sources.append({**receipt,'zip_sha256':digest,'csv_sha256':sha256(content).hexdigest(),'bars':count,'path':str(path)})
            print(name,count,flush=True)
    rows.sort(key=lambda r:r['open_time'])
    assert len(rows)>20000 and all(a['close_time']==b['open_time'] for a,b in zip(rows,rows[1:]))
    return rows,sources


def verify(rows,sources):
    assert engine.url.host in ('localhost','127.0.0.1')
    schema='p34_archive_'+uuid.uuid4().hex;db=None;now=now_ms();anchor=rows[0]['open_time'];end=rows[-1]['close_time']
    result={'kind':'official_native_archive_real_OHLCV','sources':sources,'bars':len(rows),'timeframe':'60',
        'instrument_id':IID,'start':anchor,'end':end,'as_of':now,'continuous':True,'live_schema_written':False,
        'source_known_at':'actual_archive_download_time_not_historical_bar_close'}
    try:
        with engine.begin() as c:c.execute(text('CREATE SCHEMA '+schema))
        db=create_engine(engine.url,connect_args={'options':'-csearch_path='+schema});Base.metadata.create_all(db)
        factory=sessionmaker(bind=db,expire_on_commit=False)
        with factory.begin() as s:
            s.add(Asset(id='BTC',symbol='BTC',known_at=now));s.flush()
            s.add(Instrument(id=IID,asset_id='BTC',venue='binance',native_symbol='BTCUSDT',market_type='linear_perpetual',quote_asset='USDT',spec={}));s.flush()
            s.execute(insert(RawEvent),[{'id':r['zip_sha256'],'source':'binance','endpoint':r['url'],'received_at':r['received_at'],'payload':r} for r in sources])
            for pos in range(0,len(rows),500):s.execute(insert(Candle),rows[pos:pos+500])
            first=update(s,'official-history',IID,'60',anchor,now)
            result['first_checkpoint']=first['next_open']
        # A new session resumes the committed cursor, rather than in-memory state.
        with factory.begin() as s:
            state=first
            for _ in range(60):
                state=update(s,'official-history',IID,'60',anchor,now);s.flush()
                if state['next_open']>=end:break
            points=read_series(s,state);frozen=deepcopy(state)
            expected=sum(((D(r['high'])+D(r['low'])+D(r['close']))/3*D(r['base_volume']) for r in rows),D(0))/sum((D(r['base_volume']) for r in rows),D(0))
            actual=D(points[-1]['event_avwap'])
            assert len(points)==len(rows) and state['next_open']==end
            assert abs(actual-expected)<D('1e-18')
            result['avwap']={'points':len(points),'status':state['status'],'expected':str(expected),'actual':str(actual),
                'session_resume_passed':True,'pages':len(state['segments'])}
            built=build(s,IID,'60',now);s.flush();meta=source_state(s,IID,'60',now)
            last=read(s,IID,'60',None,1500,now,meta);older=read(s,IID,'60',last['rows'][0]['time'],1500,now,meta)
            assert last['status']=='available' and older['rows'][-1]['close_time']==last['rows'][0]['time']
            result['indicator_pages']={'build':built,'latest_rows':len(last['rows']),'previous_rows':len(older['rows']),'boundary_contiguous':True}
            # Controlled fault on real data; never claimed as a provider revision.
            changed=s.get(Candle,(IID,'60',rows[10]['open_time']));original_high=changed.high
            changed.high+=1;changed.revision+=1;s.flush();revised=None
            for _ in range(100):
                state=update(s,'official-history',IID,'60',anchor,now);s.flush()
                if state.get('generation',1)>1 and state['next_open']==end:revised=state;break
            assert revised and D(read_series(s,frozen)[-1]['event_avwap'])==actual
            changed.high=original_high;changed.revision+=1;s.flush();restored=None
            for _ in range(100):
                state=update(s,'official-history',IID,'60',anchor,now);s.flush()
                if state.get('generation',1)>revised['generation'] and state['next_open']==end:restored=state;break
            assert restored and D(read_series(s,restored)[-1]['event_avwap'])==actual
            result['controlled_recovery']={'injected_fault':'early high + 1, then exact restoration; isolated only',
                'revision_detected':True,'old_snapshot_unchanged':True,'restored_equal':True,'generations':[frozen['generation'],revised['generation'],restored['generation']]}
    finally:
        if db:db.dispose()
        with engine.begin() as c:c.execute(text('DROP SCHEMA IF EXISTS '+schema+' CASCADE'))
    result['schema_removed']=True
    return result


if __name__=='__main__':
    rows,sources=asyncio.run(download());result=verify(rows,sources)
    (DEST/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='sources'},ensure_ascii=False))
