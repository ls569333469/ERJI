"""Binance Wallet discovery only. Unsupported candles never use an outside host."""
from decimal import Decimal as D
from engine.adapters.base import PublicAdapter,Batch,now_ms
from engine.adapters.expanded_markets import contract_asset
from engine.pd2.storage import get,put

NETWORKS={'56':'bsc','1':'eth','8453':'base','CT_501':'solana'}
RANK='https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/unified/rank/list/ai'

class WalletAdapter(PublicAdapter):
    venue='wallet_rising'
    async def collect(self,config,history=True):
        batch=Batch(self.venue)
        if not config.sources.wallet_enabled:
            batch.metadata={'disabled':True};return batch
        import asyncio
        for chain in NETWORKS:
            cached=await asyncio.to_thread(get,self.venue,'rising',chain)
            if cached.get('received_at',0)+300000>now_ms():continue
            try:
                data,ref,received=await self.request(batch,'POST',RANK,json={'rankType':10,'tabId':100,'memeTrending':True,'chainId':chain,'period':50,'page':1,'size':50,'sortOrder':'default','marketCapMin':config.sources.min_market_cap_usd})
                if str(data.get('code'))!='000000':raise ValueError('wallet_business_error')
                rows=data['data']['tokens']
                await asyncio.to_thread(put,self.venue,'rising',chain,{'tokens':rows,'limit':50,'possibly_truncated':len(rows)==50,'rank_type':'rising','source':'Binance Wallet','candle_status':'not_provided_by_binance_owned_endpoint'},None,300000,endpoint=RANK)
                for row in rows:
                    if row.get('stockCompanyName') or not row.get('contractAddress'):continue
                    aid=contract_asset(chain,row['contractAddress']);old=await asyncio.to_thread(get,self.venue,'candidate',aid)
                    old=old.get('payload') or {}
                    cap=row.get('marketCap');liq=row.get('liquidity')
                    try:qualified=cap is not None and liq is not None and D(str(cap)).is_finite() and D(str(liq)).is_finite() and D(str(cap))>=config.sources.min_market_cap_usd and D(str(liq))>=100000
                    except (ArithmeticError,ValueError):qualified=False
                    await asyncio.to_thread(put,self.venue,'candidate',aid,{'token':row,'first_seen_at':old.get('first_seen_at',received),'last_ranked_at':received,'qualified_discovery':qualified,'valuation_kind':'binance_reported_not_independently_verified','market_time':'unknown','quote_action_eligible':False,'candle_status':'not_provided_by_binance_owned_endpoint','reason':'币安Wallet发现记录；仅有已核验Alpha/上市市场可继续原生K线分析；不使用外部链上K线'},None,300000,endpoint=RANK)
            except Exception as error:batch.errors.append(chain+':discovery:'+type(error).__name__)
        batch.metadata={'scope':'binance_discovery_only','candle_status':'not_provided_by_binance_owned_endpoint','historical_pool_evidence_retained':True}
        return batch
