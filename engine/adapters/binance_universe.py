import asyncio
from decimal import Decimal
from engine.adapters.base import PublicAdapter, Batch, decimal_text, now_ms
from engine.universe import select_binance_universe, select_deep_ids


class BinanceUniverseAdapter(PublicAdapter):
    """Bounded REST scan: batch marks, individual quantity OI, cached settled funding."""
    venue = "binance"
    base = "https://fapi.binance.com/fapi/v1"

    def __init__(self):
        super().__init__()
        self.selection = []
        self.volumes = {}
        self.catalog_count = 0
        self.selected_at = 0
        self.selection_limit = 0
        self.funding_cache = {}
        self.cursor = 0

    async def collect(self, config):
        batch = Batch(self.venue)
        if not self.selection or now_ms()-self.selected_at >= 3600_000 or self.selection_limit != config.asset_limit:
            catalog, _, _ = await self.request(batch, "GET", self.base+"/exchangeInfo")
            tickers, _, received = await self.request(batch, "GET", self.base+"/ticker/24hr")
            selected, count, volumes = select_binance_universe(catalog["symbols"], tickers, 100000)
            if not selected:
                raise ValueError("empty_active_catalog")
            self.selection, self.catalog_count, self.volumes = selected, count, volumes
            self.selected_at, self.selection_limit = received, config.asset_limit
        marks, mark_ref, mark_received = await self.request(batch, "GET", self.base+"/premiumIndex")
        marks = {m["symbol"]:m for m in marks}
        deep_ids = set(select_deep_ids(self.selection, config.deep_asset_limit, config.symbols))
        batch.metadata = {"mode":"binance_volume", "requested_assets":config.asset_limit,
                          "eligible_assets":self.catalog_count, "selected_assets":len(self.selection),
                          "selection_as_of":self.selected_at, "selection_interval_seconds":3600,
                          "deep_asset_limit":config.deep_asset_limit, "deep_ids":sorted(deep_ids),
                          "selected_ids":["binance:perp:"+s["symbol"] for s in self.selection],
                          "scope":"Binance active USDT crypto perpetuals by 24h quote volume"}

        async def market(spec, rank):
            native, symbol = spec["symbol"], spec["baseAsset"]
            iid = "binance:perp:"+native
            mark = marks.get(native)
            if not mark:
                batch.errors.append(native+":price:missing")
                return
            # 1000PEPE etc retain the exchange's base unit and identity. No heuristic stripping.
            batch.instruments.append({"id":iid,"asset_id":symbol,"venue":"binance","native_symbol":native,
                "market_type":"linear_perpetual","quote_asset":"USDT","multiplier":"1",
                "spec":{"base_asset":symbol,"price_precision":spec.get("pricePrecision"),
                        "tick_size":next((f["tickSize"] for f in spec.get("filters",[]) if f["filterType"]=="PRICE_FILTER"),None),
                        "source":"exchangeInfo","unit_policy":"native_exchange_unit"}})
            state = {"instrument_id":iid,"price":decimal_text(mark["markPrice"]),"price_kind":"mark",
                     "as_of":int(mark.get("time") or mark_received),"received_at":mark_received,"raw_ref":mark_ref,
                     "oi_quote_asset":"USDT","oi_status":"missing","funding_kind":"settled","funding_status":"missing",
                     "volume_24h_quote":decimal_text(self.volumes[native]),"universe_rank":rank,
                     "deep_history":iid in deep_ids}
            try:
                oi, ref, received = await self.request(batch,"GET",self.base+"/openInterest",params={"symbol":native})
                quantity = decimal_text(oi["openInterest"])
                timestamp = int(oi.get("time") or received)
                notional = decimal_text(Decimal(quantity)*Decimal(state["price"]))
                batch.oi.append({"instrument_id":iid,"event_time":timestamp,"base_quantity":quantity,
                    "notional_quote":notional,"quote_asset":"USDT","mark_price":state["price"],
                    "received_at":received,"raw_ref":ref})
                state.update(oi_base=quantity,oi_notional_quote=notional,oi_as_of=timestamp,oi_status="fresh")
            except Exception as error:
                batch.errors.append(native+":oi:"+type(error).__name__)
            cached = self.funding_cache.get(native)
            if not cached or now_ms()-cached[0]>=900_000:
                try:
                    records, ref, received = await self.request(batch,"GET",self.base+"/fundingRate",params={"symbol":native,"limit":4})
                    records.sort(key=lambda r:int(r["fundingTime"]))
                    for index, row in enumerate(records):
                        interval = (int(row["fundingTime"])-int(records[index-1]["fundingTime"]))//1000 if index else None
                        point = {"instrument_id":iid,"settled_at":int(row["fundingTime"]),"rate":decimal_text(row["fundingRate"]),"interval_seconds":interval,"raw_ref":ref}
                        batch.funding.append(point)
                    if records:
                        latest = records[-1]
                        cached = (received,{"funding_rate":decimal_text(latest["fundingRate"]),"funding_interval_seconds":interval,
                            "funding_settled_at":int(latest["fundingTime"]),"funding_status":"fresh"})
                        self.funding_cache[native] = cached
                except Exception as error:
                    batch.errors.append(native+":funding:"+type(error).__name__)
            if cached:
                state.update(cached[1])
            batch.states.append(state)

        # The base adapter bounds parallel HTTP requests to four and paces starts to four/s.
        full=self.selection
        current=[(full[i%len(full)],i%len(full)+1) for i in range(self.cursor,self.cursor+min(config.asset_limit,len(full)))]
        for spec in full:
            batch.instruments.append({"id":"binance:perp:"+spec["symbol"],"asset_id":spec["baseAsset"],"venue":"binance","native_symbol":spec["symbol"],"market_type":"linear_perpetual","quote_asset":"USDT","multiplier":"1","spec":{"base_asset":spec["baseAsset"],"price_precision":spec.get("pricePrecision"),"tick_size":next((f["tickSize"] for f in spec.get("filters",[]) if f["filterType"]=="PRICE_FILTER"),None),"source":"exchangeInfo","unit_policy":"native_exchange_unit"}})
        await asyncio.gather(*(market(spec,rank) for spec,rank in current))
        self.cursor=(self.cursor+len(current))%len(full)
        batch.instruments=list({v["id"]:v for v in batch.instruments}.values())
        batch.metadata.update(batch_size=len(current),next_cursor=self.cursor,scope="all_eligible_rotating",queued_assets=len(full)-len(current))
        return batch

    async def collect_candles(self, targets):
        batch = Batch(self.venue, candle_only=True)
        async def collect(target):
            iid, native, resolution, interval, limit = target
            try:
                rows, ref, received = await self.request(batch,"GET",self.base+"/klines",params={"symbol":native,"interval":interval,"limit":limit})
                for row in rows:
                    batch.candles.append({"instrument_id":iid,"resolution":resolution,"open_time":int(row[0]),
                        "close_time":int(row[6])+1,"is_closed":int(row[6])+1<=received,
                        **{key:decimal_text(row[n]) for key,n in [("open",1),("high",2),("low",3),("close",4),("base_volume",5),("quote_volume",7)]},
                        "received_at":received,"raw_ref":ref})
            except Exception as error:
                batch.errors.append(native+":candles:"+resolution+":"+type(error).__name__)
        await asyncio.gather(*(collect(t) for t in targets))
        return batch
