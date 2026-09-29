"""Read-only market adapters. Catalog identity is never guessed from ticker prefixes."""
from decimal import Decimal as D
from engine.adapters.base import PublicAdapter,Batch,decimal_text as num,now_ms
from engine.pd2.scheduling import rotation

PERIODS=[('60','1h',3600000),('240','4h',14400000),('1D','1d',86400000),('1W','1w',604800000)]

def alpha_quality(token,received_at=None):
    """Official list exposes circulatingSupply separately from totalSupply/FDV."""
    try:
        supply=D(str(token.get('circulatingSupply')));price=D(str(token.get('price')))
        liquidity=D(str(token.get('liquidity')));reported=D(str(token.get('marketCap')))
        cap=supply*price
        if not all(n.is_finite() and n>0 for n in (supply,price,liquidity,reported)):return {}
        if abs(cap/reported-1)>D('.05'):return {'quality_status':'circulating_cap_conflict'}
        return {'circulating_cap_usd':str(cap),'liquidity_usd':str(liquidity),'quality_status':'provider_circulating_supply_crosschecked',
            'cap_method':'Binance Alpha circulatingSupply * price; checked against marketCap','quality_received_at':received_at or now_ms()}
    except (ArithmeticError,ValueError,TypeError):return {'quality_status':'circulating_cap_or_liquidity_missing'}
def contract_asset(chain,address):
    chain=str(chain)
    normalized=chain+':'+(address.lower() if chain in ('1','56','8453') else address)
    from hashlib import sha256
    return normalized if len(normalized)<=100 else 'contract:'+sha256(normalized.encode()).hexdigest()

def kline(batch,iid,res,row,ref,received):
    batch.candles.append(dict(instrument_id=iid,resolution=res,open_time=int(row[0]),close_time=int(row[6])+1,
      is_closed=int(row[6])+1<=received,received_at=received,raw_ref=ref,
      **{k:num(row[i]) for k,i in [('open',1),('high',2),('low',3),('close',4),('base_volume',5),('quote_volume',7)]}))

class BinanceCashAdapter(PublicAdapter):
    def __init__(self,alpha=False):
        super().__init__();self.alpha=alpha;self.venue='binance_alpha' if alpha else 'binance_spot'
        self.base='https://www.binance.com/bapi/defi/v1/public/alpha-trade' if alpha else 'https://api.binance.com/api/v3'
        self.catalog=[];self.catalog_at=0;self.tokens={}
    async def data(self,batch,path,params=None):
        data,ref,received=await self.request(batch,'GET',self.base+path,params=params)
        if self.alpha:
            if str(data.get('code'))!='000000':raise ValueError('alpha_business_error')
            data=data['data']
        return data,ref,received
    async def collect(self,config,history=True):
        batch=Batch(self.venue)
        catalog_changed=False
        if not self.catalog or now_ms()-self.catalog_at>1800000:
            data,_,_=await self.data(batch,'/get-exchange-info' if self.alpha else '/exchangeInfo')
            self.catalog=[x for x in data['symbols'] if x.get('status')=='TRADING' and x['quoteAsset'] in ('USDT','USDC') and (self.alpha or x.get('isSpotTradingAllowed',True))]
            if self.alpha:
                tokens,_,_=await self.request(batch,'GET','https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list')
                self.tokens={x['alphaId']:x for x in tokens['data'] if x.get('alphaId') and not x.get('stockState') and not x.get('fullyDelisted') and x.get('contractAddress')}
                self.catalog=[x for x in self.catalog if x['baseAsset'] in self.tokens]
            self.catalog_at=now_ms();catalog_changed=True
        for x in self.catalog:
            token=self.tokens.get(x['baseAsset'],{})
            asset=contract_asset(token['chainId'],token['contractAddress']) if self.alpha else x['baseAsset']
            iid=self.venue+':spot:'+x['symbol']
            batch.instruments.append(dict(id=iid,asset_id=asset,venue=self.venue,native_symbol=x['symbol'],market_type='spot',quote_asset=x['quoteAsset'],multiplier='1',spec={
              'base_asset':x['baseAsset'],'display_symbol':token.get('symbol',x['baseAsset']),
              'tick_size':next((f['tickSize'] for f in x.get('filters',[]) if f.get('filterType')=='PRICE_FILTER'),None),
              'price_precision':x.get('pricePrecision',x.get('quoteAssetPrecision',8)),
              'chain_id':token.get('chainId'),'contract_address':token.get('contractAddress'),'identity_source':'Binance Alpha token list' if self.alpha else 'Binance spot exchangeInfo',
              'pool':'binance','status':'active',**(alpha_quality(token,self.catalog_at) if self.alpha else {})}))
        from engine.pd2.collection_schedule import due
        from engine.pd2.universe_policy import primary_eligible
        from types import SimpleNamespace
        chosen=set(due(self.venue,[x['native_symbol'] for x in batch.instruments if primary_eligible(SimpleNamespace(**x))],kind='price'))
        if not chosen:
            batch.metadata={'idle':not catalog_changed,'catalog_complete':catalog_changed,'catalog_count':len(self.catalog)}
            if not catalog_changed:batch.instruments=[]
            return batch
        if not self.alpha:
            tickers,ref,received=await self.data(batch,'/ticker/24hr',{'type':'MINI'})
            prices={x['symbol']:x for x in tickers}
            for inst in batch.instruments:
                t=prices.get(inst['native_symbol'])
                if t:batch.states.append(dict(instrument_id=inst['id'],price=num(t['lastPrice']),price_kind='last',as_of=int(t['closeTime']),received_at=received,raw_ref=ref,oi_quote_asset=inst['quote_asset'],oi_status='not_applicable',funding_status='not_applicable',funding_kind='settled',volume_24h_quote=num(t['quoteVolume']),deep_history=inst['native_symbol'] in chosen))
        for inst in batch.instruments:
            if inst['native_symbol'] not in chosen:continue
            if self.alpha:
                try:
                    t,ref,received=await self.data(batch,'/ticker',{'symbol':inst['native_symbol']})
                    batch.states.append(dict(instrument_id=inst['id'],price=num(t['lastPrice']),price_kind='last',as_of=int(t['closeTime']),received_at=received,raw_ref=ref,oi_quote_asset=inst['quote_asset'],oi_status='not_applicable',funding_status='not_applicable',funding_kind='settled',volume_24h_quote=num(t['quoteVolume']),deep_history=True))
                except Exception as e:batch.errors.append(inst['native_symbol']+':ticker:'+type(e).__name__)
            if not history:continue
            for res,interval,_ in PERIODS:
                try:
                    rows,ref,received=await self.data(batch,'/klines',{'symbol':inst['native_symbol'],'interval':interval,'limit':300 if history else 2})
                    for row in rows:kline(batch,inst['id'],res,row,ref,received)
                except Exception as e:batch.errors.append(inst['native_symbol']+':'+res+':'+type(e).__name__)
        batch.metadata={'catalog_complete':catalog_changed,'catalog_count':len(self.catalog),'scan_count':len(batch.states),'deep_count':len(chosen),'scope':'all_eligible_rotating','pool':'binance'}
        if not catalog_changed:batch.instruments=[]
        return batch

    async def collect_candles(self, targets):
        batch=Batch(self.venue,candle_only=True)
        for iid,native,res,interval,limit,start in targets:
            try:
                rows,ref,received=await self.data(batch,'/klines',{'symbol':native,'interval':interval,'limit':min(limit,1000),**({'startTime':start} if start is not None else {})})
                for row in rows:kline(batch,iid,res,row,ref,received)
            except Exception as error:batch.errors.append(native+':candles:'+res+':'+type(error).__name__)
        return batch

class OkxAdapter(PublicAdapter):
    venue='okx'
    base='https://www.okx.com/api/v5'
    async def data(self,batch,path,params):
        data,ref,received=await self.request(batch,'GET',self.base+path,params=params)
        if data.get('code')!='0':raise ValueError('okx_business_error')
        return data['data'],ref,received
    async def collect(self,config,history=True):
        batch=Batch(self.venue)
        catalog,_,_=await self.data(batch,'/public/instruments',{'instType':'SWAP'})
        # PD2 P0 contract pilot; catalog retained for expansion without a strategy fork.
        native={x['instId']:x for x in catalog if x.get('state')=='live' and x.get('ctType')=='linear' and x.get('settleCcy')=='USDT'}
        selected=[x for x in ('BTC-USDT-SWAP','ETH-USDT-SWAP','SOL-USDT-SWAP') if x in native]
        for symbol in selected:
            x=native[symbol];base=x['ctValCcy'];iid='okx:perp:'+symbol
            inst=dict(id=iid,asset_id=base,venue='okx',native_symbol=symbol,market_type='linear_perpetual',quote_asset='USDT',multiplier=num(D(x['ctVal'])*D(x['ctMult'])),spec={'base_asset':base,'tick_size':x['tickSz'],'contract_value':x['ctVal'],'contract_multiplier':x['ctMult'],'quantity_unit':'base_coin','pilot':True})
            batch.instruments.append(inst)
            try:
                prices,ref,received=await self.data(batch,'/public/mark-price',{'instType':'SWAP','instId':symbol});p=prices[0]
                state=dict(instrument_id=iid,price=num(p['markPx']),price_kind='mark',as_of=int(p['ts']),received_at=received,raw_ref=ref,oi_quote_asset='USDT',oi_status='missing',funding_status='missing',funding_kind='settled')
                oi,ref,received=await self.data(batch,'/public/open-interest',{'instType':'SWAP','instId':symbol});q=oi[0]
                qty=num(q['oiCcy']);notional=num(D(qty)*D(state['price']))
                batch.oi.append(dict(instrument_id=iid,event_time=int(q['ts']),base_quantity=qty,notional_quote=notional,quote_asset='USDT',mark_price=state['price'],received_at=received,raw_ref=ref))
                state.update(oi_base=qty,oi_notional_quote=notional,oi_as_of=int(q['ts']),oi_status='fresh')
                fund,ref,_=await self.data(batch,'/public/funding-rate-history',{'instId':symbol,'limit':12})
                fund=sorted((r for r in fund if r.get('realizedRate') not in ('',None)),key=lambda r:int(r['fundingTime']))
                for i,r in enumerate(fund):
                    cycle=(int(r['fundingTime'])-int(fund[i-1]['fundingTime']))//1000 if i else None
                    batch.funding.append(dict(instrument_id=iid,settled_at=int(r['fundingTime']),rate=num(r['realizedRate']),interval_seconds=cycle,raw_ref=ref))
                if fund:state.update(funding_rate=num(fund[-1]['realizedRate']),funding_settled_at=int(fund[-1]['fundingTime']),funding_interval_seconds=cycle,funding_status='fresh')
                batch.states.append(state)
                if not history:continue
                for res,bar,dur in [('60','1H',3600000),('240','4H',14400000),('1D','1Dutc',86400000),('1W','1Wutc',604800000)]:
                    rows,ref,received=await self.data(batch,'/market/candles',{'instId':symbol,'bar':bar,'limit':300})
                    for r in rows:
                        batch.candles.append(dict(instrument_id=iid,resolution=res,open_time=int(r[0]),close_time=int(r[0])+dur,is_closed=str(r[8])=='1',open=num(r[1]),high=num(r[2]),low=num(r[3]),close=num(r[4]),base_volume=num(r[6]),quote_volume=num(r[7]),received_at=received,raw_ref=ref))
            except Exception as e:batch.errors.append(symbol+':'+type(e).__name__)
        batch.metadata={'catalog_count':len(native),'scan_count':len(batch.states),'scope':'documented_three_asset_pilot'}
        return batch
