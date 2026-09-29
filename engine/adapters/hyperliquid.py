from engine.adapters.base import PublicAdapter, Batch, decimal_text, now_ms
from decimal import Decimal

class HyperliquidAdapter(PublicAdapter):
    venue="hyperliquid"
    url="https://api.hyperliquid.xyz/info"
    async def collect(self,symbols,history=False):
        batch=Batch(self.venue)
        doc,ref,received=await self.request(batch,"POST",self.url,json={"type":"metaAndAssetCtxs"})
        contexts={meta["name"]:(meta,ctx) for meta,ctx in zip(doc[0]["universe"],doc[1]) if not meta.get("isDelisted")}
        dynamic=not isinstance(symbols,(list,tuple))
        if dynamic:
            from engine.pd2.scheduling import rotation
            selected=set(rotation(self.venue,list(contexts),min(symbols.deep_asset_limit,12)))
        else:selected=set(symbols)
        batch.metadata={"catalog_count":len(contexts),"scanned_count":len(contexts) if dynamic else len(selected),"deep_count":len(selected),"namespace":"default"}
        for symbol in contexts if dynamic else symbols:
            if symbol not in contexts: continue
            spec,ctx=contexts[symbol];iid=f"hyperliquid:perp:{symbol}"
            batch.instruments.append({"id":iid,"asset_id":symbol if symbol in ("BTC","ETH","SOL","HYPE","ASTER","RAVE") else "hyperliquid:"+symbol,"venue":self.venue,"native_symbol":symbol,"market_type":"linear_perpetual","quote_asset":"USDC","multiplier":"1","spec":{"namespace":"default","sz_decimals":spec["szDecimals"],"source":"metaAndAssetCtxs","native_identity":symbol,"cross_source_identity":"verified" if symbol in ("BTC","ETH","SOL","HYPE","ASTER","RAVE") else "unmapped"}})
            quantity=decimal_text(ctx["openInterest"]);price=decimal_text(ctx["markPx"])
            notional=decimal_text(Decimal(quantity)*Decimal(price))
            batch.oi.append({"instrument_id":iid,"event_time":received,"base_quantity":quantity,"notional_quote":notional,"quote_asset":"USDC","mark_price":price,"received_at":received,"raw_ref":ref})
            state={"instrument_id":iid,"price":price,"price_kind":"mark","as_of":received,"received_at":received,"source_time_kind":"received_at","raw_ref":ref,"oi_base":quantity,"oi_notional_quote":notional,"oi_quote_asset":"USDC","oi_as_of":received,"oi_status":"fresh","funding_kind":"settled","funding_status":"missing","volume_24h_quote":ctx.get("dayNtlVlm")}
            if symbol not in selected:
                state.pop("funding_status",None);batch.states.append(state);continue
            try:
                records,fref,_=await self.request(batch,"POST",self.url,json={"type":"fundingHistory","coin":symbol,"startTime":received-6*3600_000})
                records=sorted(records,key=lambda r:int(r["time"]))
                for index,row in enumerate(records):
                    interval=(int(row["time"])-int(records[index-1]["time"]))//1000 if index else None
                    batch.funding.append({"instrument_id":iid,"settled_at":int(row["time"]),"rate":decimal_text(row["fundingRate"]),"interval_seconds":interval,"raw_ref":fref})
                if records:
                    last=batch.funding[-1];state.update(funding_rate=last["rate"],funding_interval_seconds=last["interval_seconds"],funding_settled_at=last["settled_at"],funding_status="fresh")
            except Exception as e: batch.errors.append(symbol+":funding:"+type(e).__name__)
            batch.states.append(state)
            for resolution,interval,duration in [("60","1h",3600_000),("240","4h",14400_000),("1D","1d",86400_000),("1W","1w",604800_000)]:
                try:
                    rows,cref,arrived=await self.request(batch,"POST",self.url,json={"type":"candleSnapshot","req":{"coin":symbol,"interval":interval,"startTime":received-(300 if history else 2)*duration,"endTime":received}})
                    for r in rows:
                        batch.candles.append({"instrument_id":iid,"resolution":resolution,"open_time":int(r["t"]),"close_time":int(r["T"])+1,"is_closed":int(r["T"])+1<=arrived,
                            **{key:decimal_text(r[n]) for key,n in [("open","o"),("high","h"),("low","l"),("close","c"),("base_volume","v")]},
                            "quote_volume":None,"received_at":arrived,"raw_ref":cref})
                except Exception as e: batch.errors.append(symbol+":candles:"+resolution+":"+type(e).__name__)
        return batch

