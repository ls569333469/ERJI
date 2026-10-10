"""Verified Binance monthly archives for cold history; gaps remain explicit."""
import asyncio,csv,io,zipfile,hashlib
from datetime import datetime,timezone
from decimal import Decimal
from engine.adapters.base import Batch,now_ms,decimal_text
from engine.pd2.request_budget import request_slot,feedback

def parse(blob,checksum,iid,resolution,start,end,received):
    digest=hashlib.sha256(blob).hexdigest()
    if checksum.split()[0].lower()!=digest:raise ValueError('archive_checksum_mismatch')
    step={'60':3600000,'240':14400000,'1D':86400000}[resolution]
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        files=[v for v in archive.infolist() if v.filename.endswith('.csv')]
        if len(files)!=1 or files[0].file_size>20000000:raise ValueError('archive_content_invalid')
        content=archive.read(files[0]).decode('utf-8-sig')
    rows=[]
    for r in csv.reader(io.StringIO(content)):
        if not r or r[0] in ('open_time','open_timestamp'):continue
        at=int(r[0]);closed=int(r[6])
        # Spot archives since 2025 use microseconds; normalize only timestamps.
        micros=at>=10**14
        if micros and closed<10**14 or not micros and closed>=10**14:raise ValueError('archive_timestamp_unit_mixed')
        if micros and (at%1000 or (closed+1)%1000):raise ValueError('archive_timestamp_unaligned')
        at=at//1000 if micros else at;close=(closed+1)//1000 if micros else closed+1
        if not start<=at<end:continue
        if at%step or close!=at+step or close>received:raise ValueError('archive_interval_invalid')
        values={key:decimal_text(r[n]) for key,n in [('open',1),('high',2),('low',3),('close',4),('base_volume',5),('quote_volume',7)]}
        if Decimal(values['low'])<=0 or not Decimal(values['low'])<=min(Decimal(values['open']),Decimal(values['close']))<=max(Decimal(values['open']),Decimal(values['close']))<=Decimal(values['high']):raise ValueError('archive_ohlc_invalid')
        rows.append({'instrument_id':iid,'resolution':resolution,'open_time':at,'close_time':close,'is_closed':True,**values,'received_at':received,'raw_ref':digest})
    if len({r['open_time'] for r in rows})!=len(rows):raise ValueError('archive_duplicate')
    return rows,digest

async def monthly(adapter,inst,resolution,start,end):
    dt=datetime.fromtimestamp(start/1000,timezone.utc)
    month=datetime(dt.year+(dt.month==12),dt.month%12+1,1,tzinfo=timezone.utc)
    finish=int(month.timestamp()*1000)
    if finish>now_ms():return None
    interval={'60':'1h','240':'4h','1D':'1d'}[resolution]
    kind='futures/um' if inst.venue=='binance' else 'spot'
    url=f'https://data.binance.vision/data/{kind}/monthly/klines/{inst.native_symbol}/{interval}/{inst.native_symbol}-{interval}-{dt:%Y-%m}.zip'
    async def read(target):
        async with request_slot(target,{}) as ticket:
            from engine.pd2.p36_budget import sent,durable_call
            await durable_call(sent,ticket)
            async with asyncio.timeout(45):
                response=await adapter.client.get(target)
            await asyncio.to_thread(feedback,target,response.status_code,response.headers,{},ticket)
            if response.status_code==404:return None
            response.raise_for_status()
            if len(response.content)>10000000:raise ValueError('archive_too_large')
            return response.content
    checksum=await read(url+'.CHECKSUM')
    if checksum is None:return None
    blob=await read(url)
    if blob is None:return None
    received=now_ms();rows,digest=parse(blob,checksum.decode(),inst.id,resolution,start,min(end,finish),received)
    raw_id=hashlib.sha256((url+str(received)+str(start)+str(end)).encode()).hexdigest()
    for row in rows:row['raw_ref']=raw_id
    batch=Batch(adapter.venue,candle_only=True)
    batch.candles=rows
    batch.raw=[{'id':raw_id,'source':adapter.venue,'endpoint':url,'received_at':received,'payload':{'sha256':digest,'checksum_verified':True,'row_count':len(rows),'range_start':start,'range_end':min(end,finish)}}]
    return batch
