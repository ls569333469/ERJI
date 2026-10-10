"""Binance reference facts; no vendor fallback and no synthetic USD peg."""
import asyncio
from decimal import Decimal as D, InvalidOperation
from sqlalchemy import select
from engine.adapters.base import PublicAdapter, Batch, now_ms
from engine.models import DataCapability, Instrument

FX_MAX_AGE = 3600000
SUPPLY_MAX_AGE = 86400000
WEB_PRODUCTS='https://www.binance.com/bapi/asset/v2/public/asset-service/product/get-products'


def product_supplies(data):
    """Website-reported cs metadata, advisory; never its ticker/FDV as live fact.

    No exchange source time is returned. Conflicting native pair records are
    excluded instead of choosing the largest supply. No renamed/unit aliases.
    """
    if str(data.get('code'))!='000000' or not isinstance(data.get('data'),list):raise ValueError('invalid_web_products')
    assets={};conflicts=set()
    for row in data['data']:
        asset=row.get('b');supply=positive(row.get('cs'))
        if not asset or supply is None or row.get('st')!='TRADING':continue
        if asset in assets and D(assets[asset]['supply'])!=supply:conflicts.add(asset)
        assets[asset]={'supply':str(supply),'field':'cs','native_asset':asset,'name':row.get('an'),
            'kind':'binance_web_reported_supply_reference','source_time_kind':'unknown','source_market':row.get('s')}
    for asset in conflicts:assets.pop(asset,None)
    if not assets:raise ValueError('no_valid_web_supply')
    return {'assets':assets,'conflicting_assets':sorted(conflicts),'source_time_kind':'unknown','action_eligible':False,
        'method':'Binance website cs reference; multiply current native price separately; no ticker timestamp assumption',
        'source':'Binance web product metadata'}

def positive(value):
    try:
        value=D(str(value))
        return value if value.is_finite() and value>0 else None
    except (InvalidOperation,ValueError,TypeError):
        return None

def quote_usd_rate(quote,session,now,records=None):
    if quote=='USD':return D(1)
    if quote not in ('USDT','USDC'):return None
    key=f'binance:fx:{quote}USD'
    row=records.get(key) if records is not None else session.get(DataCapability,key)
    if not row or row.observed_at is None or not 0<=now-row.observed_at<=FX_MAX_AGE:return None
    if row.status not in ('fresh','source_error','rate_limited'):return None
    if row.payload.get('symbol')!=quote+'USD':return None
    return positive(row.payload.get('index'))

def valuation_inputs(markets,session):
    specs={i.id:i.spec for i in session.scalars(select(Instrument).where(Instrument.id.in_([m.instrument_id for m in markets])))}
    records={r.key:r for r in session.scalars(select(DataCapability).where(DataCapability.key.in_(['binance:fx:USDTUSD','binance:fx:USDCUSD'])))}
    return specs,records

def valuation_for(market,session,now,inputs=None):
    instrument=None if inputs else session.get(Instrument,market.instrument_id)
    spec=inputs[0].get(market.instrument_id,{}) if inputs else (instrument.spec if instrument else {})
    fx=quote_usd_rate(market.quote_asset,session,now,inputs[1] if inputs else None)
    oi=getattr(market,'oi_notional_quote',None)
    usd=oi*fx if oi is not None and fx is not None else None
    result={'cap':None,'ratio':None,'usd':usd,'source':'Binance','coverage':'this_instrument'}
    supply=positive(spec.get('circulating_supply'))
    at=spec.get('quality_received_at')
    if not supply or not isinstance(at,int) or not 0<=now-at<=SUPPLY_MAX_AGE:
        return {**result,'status':'binance_supply_not_provided' if not supply else 'supply_stale'}
    if fx is None:return {**result,'status':'fx_missing_or_stale'}
    if market.price is None or market.quality.status!='fresh':return {**result,'status':'price_stale'}
    cap=supply*market.price*fx
    return {**result,'status':'fresh','cap':cap,'ratio':usd/cap if usd is not None and market.oi_quality.status=='fresh' else None,
            'cap_method':'Binance Alpha circulatingSupply * native price * Binance asset index',
            'supply_time':at}

class BinanceReferenceAdapter(PublicAdapter):
    venue='binance'
    async def collect_supply(self):
        from engine.pd2.storage import put
        from engine.db import Session
        def due():
            with Session() as s:
                row=s.get(DataCapability,'binance:supply:web_products')
                return not row or not row.next_refresh_at or now_ms()>=row.next_refresh_at
        if not await asyncio.to_thread(due):return 'cached_reference'
        batch=Batch('binance')
        try:
            data,ref,received=await self.request(batch,'GET',WEB_PRODUCTS)
            payload=product_supplies(data);payload['source_raw_ref']=ref
            from engine.repository import save_batch
            await asyncio.to_thread(save_batch,batch)
            await asyncio.to_thread(put,'binance','supply','web_products',payload,None,SUPPLY_MAX_AGE,
                endpoint=WEB_PRODUCTS,next_ms=received+21600000)
            return 'source_time_unknown_reference'
        except Exception as error:
            await asyncio.to_thread(put,'binance','supply','web_products',{},None,0,status='source_error',
                reason=type(error).__name__,endpoint=WEB_PRODUCTS,next_ms=now_ms()+3600000)
            return type(error).__name__

    async def collect_fx(self):
        from engine.pd2.storage import put
        results={}
        for quote in ('USDT','USDC'):
            batch=Batch('binance');symbol=quote+'USD'
            endpoint='https://fapi.binance.com/fapi/v1/assetIndex'
            try:
                data,ref,received=await self.request(batch,'GET',endpoint,params={'symbol':symbol})
                at=data.get('time')
                if data.get('symbol')!=symbol or not positive(data.get('index')) or not isinstance(at,int) or not 0<=received-at<=60000:
                    raise ValueError('invalid_asset_index')
                payload={**data,'valuation_kind':'multi_assets_mode_reference_index','source':'Binance assetIndex','unit':'USD'}
                await asyncio.to_thread(put,'binance','fx',symbol,payload,at,FX_MAX_AGE,endpoint=endpoint,next_ms=received+300000)
                results[symbol]='fresh'
            except Exception as error:
                await asyncio.to_thread(put,'binance','fx',symbol,{},None,0,status='source_error',reason=type(error).__name__,endpoint=endpoint,next_ms=now_ms()+300000)
                results[symbol]=type(error).__name__
        return results
