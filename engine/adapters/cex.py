from decimal import Decimal
from engine.adapters.base import PublicAdapter, Batch, decimal_text, now_ms

class LinearPerpAdapter(PublicAdapter):
    def __init__(self,venue):
        super().__init__();self.venue=venue
        self.base="https://fapi.binance.com" if venue=="binance" else "https://fapi.asterdex.com"
        self.prefix="/fapi/v1" if venue=="binance" else "/fapi/v3"
        self.catalog=None;self.catalog_at=0;self.funding_cache={}
    async def collect(self,symbols,history=False):
        batch=Batch(self.venue)
        catalog_changed=False
        if self.catalog is None or now_ms()-self.catalog_at>1800000:
            doc,_,_=await self.request(batch,"GET",self.base+self.prefix+"/exchangeInfo")
            self.catalog_at=now_ms()
            catalog_changed=True
            self.catalog={x["symbol"]:x for x in doc["symbols"] if x.get("status")=="TRADING" and x.get("contractType")=="PERPETUAL" and x.get("symbolType",0)==0 and not ({"STOCK","COMMODITY","FOREX","INDEX"}&set(x.get("underlyingSubType",[])))}
        dynamic=not isinstance(symbols,(list,tuple))
        if dynamic:
            from engine.pd2.scheduling import rotation
            cfg=symbols
            natives=[k for k,v in self.catalog.items() if v.get("quoteAsset")=="USDT"]
            from engine.pd2.universe_policy import auxiliary_symbols
            from engine.pd2.collection_schedule import due
            allowed=auxiliary_symbols(self.venue)
            selected=due(self.venue,[n for n in natives if n in allowed])
            symbols=[self.catalog[k]["baseAsset"] for k in selected]
            for k in natives if catalog_changed else []:
                v=self.catalog[k];asset=v["baseAsset"]
                batch.instruments.append({"id":f"{self.venue}:perp:{k}","asset_id":asset if asset in ("BTC","ETH","SOL","HYPE","ASTER","RAVE") else self.venue+":"+asset,"venue":self.venue,"native_symbol":k,"market_type":"linear_perpetual","quote_asset":"USDT","multiplier":"1","spec":{"base_asset":asset,"price_precision":v.get("pricePrecision"),"source":"exchangeInfo","status":v.get("status")}})
            batch.metadata={"catalog_complete":catalog_changed,"catalog_count":len(natives),"scanned_count":len(selected),"selected_ids":[x["id"] for x in batch.instruments]}
        for symbol in symbols:
            native=symbol+"USDT";spec=self.catalog.get(native)
            if not spec or spec.get("baseAsset")!=symbol: continue
            iid=f"{self.venue}:perp:{native}"
            batch.instruments=[v for v in batch.instruments if v["id"]!=iid]
            batch.instruments.append({"id":iid,"asset_id":symbol if symbol in ("BTC","ETH","SOL","HYPE","ASTER","RAVE") else self.venue+":"+symbol,"venue":self.venue,"native_symbol":native,"market_type":"linear_perpetual","quote_asset":"USDT","multiplier":"1","spec":{"base_asset":symbol,"price_precision":spec.get("pricePrecision"),"tick_size":next((f["tickSize"] for f in spec.get("filters",[]) if f.get("filterType")=="PRICE_FILTER"),"0.00000001"),"source":"exchangeInfo"}})
            state={"instrument_id":iid,"price_kind":"mark","oi_quote_asset":"USDT","funding_kind":"settled","oi_status":"missing","funding_status":"missing"}
            try:
                mark,raw_ref,received=await self.request(batch,"GET",self.base+self.prefix+"/premiumIndex",params={"symbol":native})
                state.update(price=decimal_text(mark["markPrice"]),as_of=int(mark.get("time") or received),received_at=received,raw_ref=raw_ref)
            except Exception as e:
                batch.errors.append(native+":price:"+type(e).__name__);continue
            try:
                # Aster public OI retains the v1 path; no authenticated endpoint is used.
                oi,ref,received=await self.request(batch,"GET",self.base+"/fapi/v1/openInterest",params={"symbol":native})
                quantity=decimal_text(oi["openInterest"]);event_time=int(oi.get("time") or received)
                item={"instrument_id":iid,"event_time":event_time,"base_quantity":quantity,"notional_quote":decimal_text(Decimal(quantity)*Decimal(state["price"])),"quote_asset":"USDT","mark_price":state["price"],"received_at":received,"raw_ref":ref}
                batch.oi.append(item);state.update(oi_base=quantity,oi_notional_quote=item["notional_quote"],oi_as_of=event_time,oi_status="fresh")
            except Exception as e: batch.errors.append(native+":oi:"+type(e).__name__)
            try:
                cached=self.funding_cache.get(native)
                if cached and now_ms()<cached[0]:
                    state.update(cached[1]);batch.states.append(state);continue
                records,ref,_=await self.request(batch,"GET",self.base+self.prefix+"/fundingRate",params={"symbol":native,"limit":4})
                records=sorted(records,key=lambda r:int(r["fundingTime"]))
                for index,row in enumerate(records):
                    interval=(int(row["fundingTime"])-int(records[index-1]["fundingTime"]))//1000 if index else None
                    batch.funding.append({"instrument_id":iid,"settled_at":int(row["fundingTime"]),"rate":decimal_text(row["fundingRate"]),"interval_seconds":interval,"raw_ref":ref})
                if records:
                    last=batch.funding[-1];state.update(funding_rate=last["rate"],funding_interval_seconds=last["interval_seconds"],funding_settled_at=last["settled_at"],funding_status="fresh")
            except Exception as e: batch.errors.append(native+":funding:"+type(e).__name__)
            if state.get("funding_status")=="fresh":
                self.funding_cache[native]=(state['funding_settled_at']+(state.get('funding_interval_seconds') or 3600)*1000+60000,{k:v for k,v in state.items() if k.startswith("funding_")})
            batch.states.append(state)
            if dynamic: continue  # auxiliary evidence only; no duplicate chart history
            for resolution,interval in [("60","1h"),("240","4h"),("1D","1d"),("1W","1w")]:
                try:
                    rows,ref,received=await self.request(batch,"GET",self.base+self.prefix+"/klines",params={"symbol":native,"interval":interval,"limit":300 if history else 2})
                    for r in rows:
                        batch.candles.append({"instrument_id":iid,"resolution":resolution,"open_time":int(r[0]),"close_time":int(r[6])+1,"is_closed":int(r[6])+1<=received,
                            **{key:decimal_text(r[n]) for key,n in [("open",1),("high",2),("low",3),("close",4),("base_volume",5),("quote_volume",7)]},
                            "received_at":received,"raw_ref":ref})
                except Exception as e: batch.errors.append(native+":candles:"+resolution+":"+type(e).__name__)
        return batch

