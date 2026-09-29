"""Binance Wallet Rising discovery + explicit pool OHLCV. Never an execution quote."""
import asyncio
from decimal import Decimal as D
from sqlalchemy import select
from engine.adapters.base import PublicAdapter,Batch,decimal_text as num,now_ms
from engine.adapters.expanded_markets import contract_asset
from engine.models import DataCapability,Instrument
from engine.db import Session
from engine.pd2.storage import get,put
from engine.pd2.scheduling import rotation
from engine.pd2.analysis import aggregate
NETWORKS={'56':'bsc','1':'eth','8453':'base','CT_501':'solana'}
RANK='https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/unified/rank/list/ai'

class WalletAdapter(PublicAdapter):
    venue='wallet_rising'
    async def collect(self,config,history=True):
        batch=Batch(self.venue)
        if not config.sources.wallet_enabled:
            batch.metadata={'disabled':True};return batch
        for chain in NETWORKS:
            cached=get(self.venue,'rising',chain)
            if cached.get('received_at',0)+600000>now_ms():continue
            try:
                data,ref,received=await self.request(batch,'POST',RANK,json={'rankType':10,'tabId':100,'memeTrending':True,'chainId':chain,'period':50,'page':1,'size':50,'sortOrder':'default','marketCapMin':config.sources.min_market_cap_usd})
                if str(data.get('code'))!='000000':raise ValueError('wallet_business_error')
                rows=data['data']['tokens'];put(self.venue,'rising',chain,{'tokens':rows,'limit':50,'possibly_truncated':len(rows)==50,'rank_type':'rising'},None,600000)
                for row in rows:
                    if row.get('stockCompanyName') or not row.get('contractAddress'):continue
                    aid=contract_asset(chain,row['contractAddress']);old=get(self.venue,'candidate',aid).get('payload') or {}
                    cap=row.get('marketCap');liq=row.get('liquidity')
                    qualified=cap is not None and liq is not None and D(cap)>=config.sources.min_market_cap_usd and D(liq)>=100000
                    put(self.venue,'candidate',aid,{'token':row,'first_seen_at':old.get('first_seen_at',received),'last_ranked_at':received,'qualified_discovery':qualified,'valuation_kind':'provider_reported_not_independently_verified','market_time':'unknown','quote_action_eligible':False},None,600000)
            except Exception as e:batch.errors.append(chain+':discovery:'+type(e).__name__)
        with Session() as s:
            candidates={r.subject:r.payload for r in s.scalars(select(DataCapability).where(DataCapability.provider==self.venue,DataCapability.capability=='candidate'))}
        eligible=[aid for aid,p in candidates.items() if p.get('qualified_discovery')]
        selected=rotation(self.venue,eligible,min(config.deep_asset_limit,8))
        for aid in selected:
            token=candidates[aid]['token'];chain=str(token['chainId']);network=NETWORKS[chain];address=token['contractAddress'];key=network+'_'+(address.lower() if chain!='CT_501' else address)
            try:
                await asyncio.sleep(2.1)
                pools,_,_=await self.request(batch,'GET',f'https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{address}/pools')
                matched=[p for p in pools.get('data',[]) if p.get('relationships',{}).get('base_token',{}).get('data',{}).get('id')==key]
                matched=[p for p in matched if D(p['attributes'].get('reserve_in_usd') or 0)>=100000 and p['attributes'].get('market_cap_usd') is not None and D(p['attributes']['market_cap_usd'])>=config.sources.min_market_cap_usd]
                if not matched:
                    put(self.venue,'pool',aid,{},None,0,status='not_returned',reason='没有同身份、流动性与流通市值合格的基础资产池；FDV不替代市值');continue
                p=max(matched,key=lambda r:D(r['attributes']['reserve_in_usd']));pa=p['attributes'];pool=pa['address'];iid='wallet_rising:pool:'+network+':'+pool
                batch.instruments.append(dict(id=iid,asset_id=aid,venue=self.venue,native_symbol=token['symbol'],market_type='dex_pool',quote_asset='USD',multiplier='1',spec={'display_symbol':token['symbol'],'base_asset':token['symbol'],'chain_id':chain,'contract_address':address,'pool_address':pool,'dex_id':p['relationships']['dex']['data']['id'],'price_precision':min(int(token.get('decimals',8)),12),'volume_unit':'USD','base_volume_available':False,'pool':'meme','execution_status':'quote_and_sell_simulation_unavailable','identity_source':'exact chain and contract; GeckoTerminal base_token'}))
                put(self.venue,'pool',aid,{'pool_id':iid,'reserve_usd':pa['reserve_in_usd'],'market_cap_usd':pa['market_cap_usd'],'scope':'single_pool','source':'GeckoTerminal','symbol':token['symbol']},None,3600000)
                for resolution,frame,n,duration in [('60','hour',1,3600000),('240','hour',4,14400000),('1D','day',1,86400000)]:
                    await asyncio.sleep(2.1)
                    data,ref,received=await self.request(batch,'GET',f'https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool}/ohlcv/{frame}',params={'aggregate':n,'currency':'usd','token':'base','limit':300})
                    bars=[]
                    for r in data['data']['attributes']['ohlcv_list']:
                        bars.append(dict(instrument_id=iid,resolution=resolution,open_time=int(r[0])*1000,close_time=int(r[0])*1000+duration,is_closed=int(r[0])*1000+duration<=received,open=num(r[1]),high=num(r[2]),low=num(r[3]),close=num(r[4]),base_volume=None,quote_volume=num(r[5]),received_at=received,raw_ref=ref))
                    batch.candles.extend(bars)
                    if resolution=='1D':batch.candles.extend(aggregate(bars,'1W',received))
                    if resolution=='60' and bars:
                        latest=max(bars,key=lambda x:x['open_time'])
                        # API returns no trade timestamp; bar close is not asserted to be live last.
                        batch.states.append(dict(instrument_id=iid,price=latest['close'],price_kind='pool_bar_reference',as_of=min(latest['open_time'],received),received_at=received,raw_ref=ref,oi_quote_asset='USD',oi_status='not_applicable',funding_kind='settled',funding_status='not_applicable',volume_24h_quote=pa.get('volume_usd',{}).get('h24'),deep_history=True))
            except Exception as e:batch.errors.append(aid+':pool:'+type(e).__name__)
        batch.metadata={'candidate_count':len(candidates),'quality_eligible':len(eligible),'deep_count':len(selected),'scope':'chain_contract_pool','rising_limit_per_chain':50,'price_scope':'pool_reference_not_executable','supplemental_pool_source':'GeckoTerminal free public API'}
        return batch
