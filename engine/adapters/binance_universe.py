import asyncio
from uuid import uuid4
from engine.p39.timing import cycle,ORDINARY_MS,HEARTBEAT_MS,COLLECTION_LEASE_MS,COLLECTION_RENEW_SECONDS
from decimal import Decimal
from engine.adapters.base import PublicAdapter, Batch, decimal_text, now_ms
from engine.universe import select_binance_universe, select_deep_ids

def stored_marks(natives,now):
    from sqlalchemy import select
    from engine.db import Session
    from engine.models import MarketState,RawEvent
    with Session() as session:
        ids=['binance:perp:'+n for n in natives]
        rows=list(session.scalars(select(MarketState).where(MarketState.instrument_id.in_(ids))))
        valid={r.instrument_id:r.payload for r in rows if r.payload.get('price_kind')=='mark' and 0<=now-(r.payload.get('as_of') or 0)<=10000}
        refs={p.get('raw_ref') for p in valid.values() if p.get('raw_ref')}
        raw={r.id:r for r in session.scalars(select(RawEvent).where(RawEvent.id.in_(refs),RawEvent.endpoint=='ws:quote_batch'))} if refs else {}
        return {r.instrument_id.rsplit(':',1)[-1]:({'symbol':r.instrument_id.rsplit(':',1)[-1], 'markPrice':r.payload['price'],'time':r.payload['as_of']},r.payload['raw_ref'],r.payload['received_at'])
                for r in rows if r.instrument_id in valid and r.payload.get('raw_ref') in raw}


def stored_mark(native,now):
    return stored_marks([native],now).get(native)


class BinanceUniverseAdapter(PublicAdapter):
    """Bounded REST scan: batch marks, individual quantity OI, cached settled funding."""
    venue = "binance"
    base = "https://fapi.binance.com/fapi/v1"

    def __init__(self):
        super().__init__()
        self.selection = []
        self.volumes = {}
        self.volume_clocks = {}
        self.catalog_count = 0
        self.selected_at = 0
        self.selection_limit = 0
        self.funding_cache = {}
        self.cursor = 0
        self._marks_at = 0
        self._marks = {}
        self._marks_lock = asyncio.Lock()
        self.volume_cycle = None

    async def refresh_volumes(self,on_batch=None):
        current=cycle(now_ms())
        if self.volume_cycle==current:return None
        part=Batch(self.venue)
        rows,ref,received=await self.request(part,'GET',self.base+'/ticker/24hr')
        rows={r['symbol']:r for r in rows}
        for spec in self.selection:
            native=spec['symbol'];row=rows.get(native)
            if not row:continue
            self.volumes[native]=Decimal(row['quoteVolume'])
            clocks={'volume_24h_source_at':int(row.get('closeTime') or 0) or None,
                'volume_24h_received_at':received,'volume_24h_raw_ref':ref}
            self.volume_clocks[native]=clocks
            part.states.append({'instrument_id':'binance:perp:'+native,'as_of':0,'price_kind':'mark',
                'volume_24h_quote':decimal_text(self.volumes[native]),**clocks})
        if on_batch:await on_batch(part)
        self.volume_cycle=current
        return part

    async def cached_mark(self,native,at):
        async with self._marks_lock:
            if at-self._marks_at>=1000:
                self._marks=await asyncio.to_thread(stored_marks,[x['symbol'] for x in self.selection],at)
                self._marks_at=at
            value=self._marks.get(native)
            return value if value and 0<=at-value[0]['time']<=10000 else None

    async def collect(self, config, on_batch=None, continuous=False, write_metrics=None):
        batch = Batch(self.venue)
        catalog_changed=False
        if not self.selection or now_ms()-self.selected_at >= 1800_000 or self.selection_limit != config.asset_limit:
            catalog, _, _ = await self.request(batch, "GET", self.base+"/exchangeInfo")
            tickers, volume_ref, received = await self.request(batch, "GET", self.base+"/ticker/24hr")
            selected, count, volumes = select_binance_universe(catalog["symbols"], tickers, 100000)
            if not selected:
                raise ValueError("empty_active_catalog")
            self.selection, self.catalog_count, self.volumes = selected, count, volumes
            self.volume_clocks={r['symbol']:{'volume_24h_source_at':int(r.get('closeTime') or 0) or None,
                'volume_24h_received_at':received,'volume_24h_raw_ref':volume_ref} for r in tickers}
            self.selected_at, self.selection_limit = received, config.asset_limit
            self.volume_cycle=None
            catalog_changed=True
        from engine.pd2.collection_schedule import due
        owner=uuid4().hex
        due_symbols=set(await asyncio.to_thread(due,self.venue,[x['symbol'] for x in self.selection],kind='oi',limit=8 if continuous else 20,lease_ms=COLLECTION_LEASE_MS,owner=owner))
        claims={n:owner for n in due_symbols}
        if not due_symbols and not catalog_changed:
            batch.metadata={'idle':True};return batch
        cached={}
        for native in due_symbols:
            value=await self.cached_mark(native,now_ms())
            if value:cached[native]=value
        marks={};mark_ref=None;mark_received=now_ms()
        if set(cached)!=due_symbols:
            raw_marks,mark_ref,mark_received=await self.request(batch,"GET",self.base+'/premiumIndex')
            marks={m['symbol']:m for m in raw_marks}
        marks.update({n:v[0] for n,v in cached.items()})
        deep_ids = set(select_deep_ids(self.selection, config.deep_asset_limit, config.symbols))
        batch.metadata = {"mode":"binance_full_catalog", "catalog_complete":catalog_changed, "requested_assets":config.asset_limit,
                          "eligible_assets":self.catalog_count, "selected_assets":len(self.selection),
                          "selection_as_of":self.selected_at, "selection_interval_seconds":1800,
                          "deep_asset_limit":config.deep_asset_limit, "deep_ids":sorted(deep_ids),
                          "selected_ids":["binance:perp:"+s["symbol"] for s in self.selection],
                          "scope":"Binance eligible USDT/USDC crypto perpetuals; all assets"}

        async def market(spec, rank):
            native, symbol = spec["symbol"], spec["baseAsset"]
            iid = "binance:perp:"+native
            part=Batch(self.venue)
            part.metadata['collection_claims']={native:claims[native]}
            fresh=await self.cached_mark(native,now_ms())
            if fresh:cached[native]=fresh;marks[native]=fresh[0]
            mark = marks.get(native)
            if not mark:
                try:
                    mark,ref,received=await self.request(part,'GET',self.base+'/premiumIndex',params={'symbol':native})
                    cached[native]=(mark,ref,received)
                except Exception as error:
                    batch.errors.append(native+':price:'+type(error).__name__)
                    from engine.pd2.collection_schedule import update_claims
                    await asyncio.to_thread(update_claims,self.venue,[native],claims[native],True)
                    return
            market_mark_ref=cached[native][1] if native in cached else mark_ref
            market_mark_received=cached[native][2] if native in cached else mark_received
            # 1000PEPE etc retain the exchange's base unit and identity. No heuristic stripping.
            part.instruments.append({"id":iid,"asset_id":symbol,"venue":"binance","native_symbol":native,
                "market_type":"linear_perpetual","quote_asset":spec["quoteAsset"],"multiplier":"1",
                "spec":{"base_asset":symbol,"price_precision":spec.get("pricePrecision"),
                        "tick_size":next((f["tickSize"] for f in spec.get("filters",[]) if f["filterType"]=="PRICE_FILTER"),None),
                        "source":"exchangeInfo","onboard_time":spec.get("onboardDate"),"unit_policy":"native_exchange_unit"}})
            part.metadata['collection_symbols']={iid:native}
            # The catalog is enqueued before these facts in the same FIFO
            # writer. Per-point OI must not repeat asset/identity/catalog writes.
            if continuous and on_batch:part.instruments.clear()
            state = {"instrument_id":iid,"price":decimal_text(mark["markPrice"]),"price_kind":"mark",
                     "as_of":int(mark.get("time") or 0),"received_at":market_mark_received,"raw_ref":market_mark_ref,
                     "oi_quote_asset":spec["quoteAsset"],"oi_status":"missing","funding_kind":"settled","funding_status":"missing",
                     "volume_24h_quote":decimal_text(self.volumes[native]),**self.volume_clocks.get(native,{'volume_24h_source_at':None}),"universe_rank":rank,
                     "deep_history":iid in deep_ids}
            try:
                oi, ref, received = await self.request(part,"GET",self.base+"/openInterest",params={"symbol":native})
                quantity = decimal_text(oi["openInterest"])
                timestamp = int(oi.get("time") or 0)
                # Refresh the cached WS mark after a budget wait, without REST.
                latest=await self.cached_mark(native,received)
                if latest:
                    mark,market_mark_ref,market_mark_received=latest
                    state.update(price=decimal_text(mark['markPrice']),as_of=mark['time'],received_at=market_mark_received,raw_ref=market_mark_ref)
                from engine.pd2.derivative_collection import aligned_notional,notional_evidence
                notional = aligned_notional(quantity,state['price'],timestamp,int(mark.get('time') or 0))
                item={"instrument_id":iid,"event_time":timestamp,"base_quantity":quantity,
                    "notional_quote":notional,"quote_asset":spec["quoteAsset"],"mark_price":state['price'] if notional is not None else None,
                    "received_at":received,"raw_ref":ref}
                if notional is not None:notional_evidence(part,item,market_mark_ref,mark.get('time'))
                if timestamp:part.oi.append(item)
                state.update(oi_base=quantity,oi_notional_quote=notional,oi_as_of=timestamp,oi_received_at=received,oi_status="fresh" if timestamp else 'source_time_unknown',
                    oi_notional_status='derived_aligned' if notional is not None else 'price_time_mismatch',
                    oi_mark_as_of=state['as_of'],oi_mark_raw_ref=market_mark_ref,oi_raw_ref=ref)
            except Exception as error:
                part.errors.append(native+":oi:"+type(error).__name__)
            # Funding and historical OI have their own durable queues. They must
            # never delay publication of this market's current quantity OI.
            state.pop('funding_status',None)
            part.states.append(state)
            part.raw.extend(r for r in batch.raw if r['id']==mark_ref)
            if on_batch:
                await on_batch(part)
                counter='queued_states' if continuous else 'persisted_states'
                batch.metadata[counter]=batch.metadata.get(counter,0)+1
            else:
                batch.oi.extend(part.oi);batch.states.extend(part.states);batch.raw.extend(part.raw)
                batch.metadata.setdefault('collection_claims',{}).update(part.metadata['collection_claims'])
            batch.instruments.extend(part.instruments)
            batch.errors.extend(part.errors)


        # Actual HTTP slots and costs are shared across every producer.
        full=self.selection
        current=[(spec,index+1) for index,spec in enumerate(full) if spec["symbol"] in due_symbols]
        for spec in (full if catalog_changed else []):
            batch.instruments.append({"id":"binance:perp:"+spec["symbol"],"asset_id":spec["baseAsset"],"venue":"binance","native_symbol":spec["symbol"],"market_type":"linear_perpetual","quote_asset":spec["quoteAsset"],"multiplier":"1","spec":{"base_asset":spec["baseAsset"],"price_precision":spec.get("pricePrecision"),"tick_size":next((f["tickSize"] for f in spec.get("filters",[]) if f["filterType"]=="PRICE_FILTER"),None),"source":"exchangeInfo","onboard_time":spec.get("onboardDate"),"unit_policy":"native_exchange_unit"}})
        if continuous and on_batch:
            # Persist catalog before individual facts. Completion of one market
            # releases a slot immediately; a slow peer cannot hold up new work.
            if catalog_changed:
                await on_batch(batch)
                batch.metadata['catalog_complete']=False
            await self.refresh_volumes(on_batch)
            from engine.pd2.continuous_collection import dispatch
            lookup={spec['symbol']:(spec,rank) for rank,spec in enumerate(full,1)}
            first=list(due_symbols)
            running=set(first)
            async def choose(capacity):
                nonlocal first
                if first:
                    chosen=first[:capacity];first=first[capacity:];return chosen
                request_owner=uuid4().hex
                chosen=await asyncio.to_thread(due,self.venue,list(lookup),'oi',capacity,COLLECTION_LEASE_MS,request_owner)
                claims.update({n:request_owner for n in chosen})
                running.update(chosen)
                return chosen
            async def execute(native):
                try:await market(*lookup[native])
                finally:running.discard(native)
                # The real data is already committed by on_batch, keep summaries bounded.
                batch.instruments.clear();batch.errors[:]=batch.errors[-50:]
            from engine.repository import runtime_config
            last_health=0
            last_enabled=0;cached_enabled=True
            async def enabled():
                nonlocal last_health,last_enabled,cached_enabled
                from engine.repository import heartbeat
                if now_ms()-last_health>=HEARTBEAT_MS:
                    await asyncio.to_thread(heartbeat,'live_binance','partial' if batch.errors else 'ok',{'enqueued':batch.metadata.get('queued_states',0),'gaps_since_heartbeat':len(batch.errors),'schedule':'ordinary_300_hot_60','volume_cycle':self.volume_cycle,'fact_write_queue':write_metrics() if write_metrics else None})
                    last_health=now_ms();batch.errors.clear()
                if now_ms()-last_enabled>=30000:
                    cached_enabled=(await asyncio.to_thread(runtime_config)).collector_enabled;last_enabled=now_ms()
                return cached_enabled and now_ms()-self.selected_at<1800000
            async def maintenance():
                from engine.pd2.collection_schedule import update_claims
                while True:
                    await self.refresh_volumes(on_batch)
                    for token in {claims[n] for n in running}:
                        await asyncio.to_thread(update_claims,self.venue,[n for n in running if claims[n]==token],token)
                    await asyncio.sleep(COLLECTION_RENEW_SECONDS)
            maintainer=asyncio.create_task(maintenance())
            try:
                await dispatch(choose,execute,enabled,8,maintenance=maintainer,idle_seconds=15)
            finally:
                maintainer.cancel();await asyncio.gather(maintainer,return_exceptions=True)
        else:
            volumes=await self.refresh_volumes(on_batch)
            if volumes and not on_batch:batch.states.extend(volumes.states);batch.raw.extend(volumes.raw)
            await asyncio.gather(*(market(spec,rank) for spec,rank in current))
        self.cursor=(self.cursor+len(current))%len(full)
        batch.instruments=list({v["id"]:v for v in batch.instruments}.values())
        batch.metadata.update(batch_size=len(current),next_cursor=self.cursor,scope="all_eligible_rotating",queued_assets=len(full)-len(current))
        return batch

    async def collect_candles(self, targets):
        batch = Batch(self.venue, candle_only=True)
        async def collect(target):
            iid, native, resolution, interval, limit = target[:5]
            start = target[5] if len(target)>5 else None
            try:
                rows, ref, received = await self.request(batch,"GET",self.base+"/klines",params={"symbol":native,"interval":interval,"limit":limit,**({"startTime":start} if start is not None else {})})
                for row in rows:
                    batch.candles.append({"instrument_id":iid,"resolution":resolution,"open_time":int(row[0]),
                        "close_time":int(row[6])+1,"is_closed":int(row[6])+1<=received,
                        **{key:decimal_text(row[n]) for key,n in [("open",1),("high",2),("low",3),("close",4),("base_volume",5),("quote_volume",7)]},
                        "received_at":received,"raw_ref":ref})
            except Exception as error:
                batch.errors.append(native+":candles:"+resolution+":"+type(error).__name__)
        await asyncio.gather(*(collect(t) for t in targets))
        return batch
