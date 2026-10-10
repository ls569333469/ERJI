"""Bounded, database-free discovery. Native market units, event timestamps."""
from collections import deque
from decimal import Decimal as D
from threading import RLock
from bisect import bisect_left,bisect_right
from time import perf_counter,time

DEFAULTS = dict(price_5m=3, price_15m=5, volume_multiple=3, volume_min=100000,
                liquidity_min=1000000, oi_change=5, capacity=50, merge_ms=1800000,
                cool_ms=7200000, archive_ms=604800000, btc_5m=1, btc_15m=2,
                breadth_min=30, breadth_coverage=.7, breadth_ratio=.6, excess=2)

from engine.features.numeric import change,relative_volume

class Detector:
    def __init__(self):
        self.lock=RLock();self.prices={};self.price_kinds={};self.oi={};self.bars={};self.catalog={};self.config=dict(DEFAULTS)
        self.bar_versions={};self.contexts=deque(maxlen=40);self.oi_known={};self.price_known={}
        self.price_receipts={};self.oi_receipts={};self.oi_evaluations={}
    def reject_price(self,iid,at,received,kind,connection_id,reason,source_kind=None):
        with self.lock:
            if iid not in self.catalog:return
            row=self.price_receipts.setdefault(iid,{'accepted':0,'rejections':{}})
            row.update(last_attempt_source_at=at,last_attempt_receive_at=received,
                last_attempt_kind=kind,last_attempt_connection_id=connection_id,last_attempt_reason=reason,last_attempt_source_kind=source_kind)
            row['rejections'][reason]=row['rejections'].get(reason,0)+1
    def price(self,iid,at,value,kind=None,received=None,connection_id=None,source_kind=None):
        if D(str(value))<=0:return
        lock_started=perf_counter()
        with self.lock:
            lock_wait=(perf_counter()-lock_started)*1000
            if iid not in self.catalog:return
            venue=self.catalog[iid].get('venue')
            if venue=='binance' and kind=='last' or venue=='binance_spot' and kind=='mark':
                self.reject_price(iid,at,received,kind,connection_id,'wrong_price_kind',source_kind);return
            q=self.prices.setdefault(iid,deque(maxlen=2100))
            if q and at<=q[-1][0]:
                self.reject_price(iid,at,received,kind,connection_id,'non_increasing_source_time',source_kind);return
            previous=self.price_kinds.get(iid)
            if kind=='last' and previous=='mark' and q and at-q[-1][0]<=15000:return
            # Mark and trade prices have different bases. A source switch must
            # rebuild continuity, never create a synthetic price jump.
            if kind and previous and kind!=previous:
                q.clear();self.price_known.pop(iid,None)
            if kind:self.price_kinds[iid]=kind
            if q and at-q[-1][0]<1000:
                self.reject_price(iid,at,received,kind,connection_id,'sampling_interval',source_kind);return
            receipt=received if received is not None else at
            row=self.price_receipts.setdefault(iid,{'accepted':0,'rejections':{}})
            source_gap=at-row.get('last_accepted_source_at',at)
            receive_gap=receipt-row.get('last_accepted_receive_at',receipt)
            row.update(last_accepted_source_at=at,last_accepted_receive_at=receipt,
                source_gap_ms=source_gap,receive_gap_ms=receive_gap,
                max_source_gap_ms=max(source_gap,row.get('max_source_gap_ms',0)),
                max_receive_gap_ms=max(receive_gap,row.get('max_receive_gap_ms',0)),
                lock_wait_ms=round(lock_wait,3),max_lock_wait_ms=round(max(lock_wait,row.get('max_lock_wait_ms',0)),3),
                accepted_kind=kind,source_kind=source_kind,connection_id=connection_id,accepted=row['accepted']+1)
            if source_gap>15000 or receive_gap>15000:
                row['recent_gaps']=[*row.get('recent_gaps',[]),{'source_at':at,'received_at':receipt,
                    'source_gap_ms':source_gap,'receive_gap_ms':receive_gap,'connection_id':connection_id,'source_kind':source_kind}][-8:]
            q.append((at,value))
            self.price_known.setdefault(iid,deque(maxlen=2100)).append((at,received if received is not None else at))
    def oi_point(self,iid,at,value,received=None):
        if not at or value is None:return
        with self.lock:
            q=self.oi.setdefault(iid,deque(maxlen=160))
            accepted=not q or at>q[-1][0]
            known=received if received is not None else at
            # Record actual known time separately from ingestion wall clock.
            if accepted:
                self.oi_receipts.setdefault(iid,deque(maxlen=8)).append({'source_at':at,'known_at':known,
                    'ingested_at':int(time()*1000),'previous_source_at':q[-1][0] if q else None,
                    'origin':'market_state_latest','accepted':True})
                q.append((at,value));self.oi_known.setdefault(iid,deque(maxlen=160)).append((at,known))
    def bar(self,iid,row):
        with self.lock:
            if iid not in self.catalog:return
            b=self.bars.setdefault(iid,{})
            old=b.get(row['open_time'])
            if old and (old['closed'] and not row['closed'] or old.get('at',0)>row.get('at',0)):return
            b[row['open_time']]=row
            versions=self.bar_versions.setdefault(iid,deque(maxlen=2400))
            versions.append(dict(row,known_at=row.get('known_at',row.get('at',0))))
            for key in sorted(b)[:-12]:del b[key]
    @staticmethod
    def delta(q,now,window,tolerance=5000,max_age=15000):
        if isinstance(q,PriceSlice):return q.delta(now,window,tolerance,max_age)
        if not q or not 0<=now-q[-1][0]<=max_age:return None
        end=q[-1][0];target=end-window
        candidates=[r for r in q if abs(r[0]-target)<=tolerance]
        if not candidates:return None
        first=min(candidates,key=lambda r:abs(r[0]-target))
        # A window with missing price intervals cannot claim continuous observation.
        if tolerance==5000:
            segment=[r for r in q if first[0]<=r[0]<=end]
            if any(b[0]-a[0]>15000 for a,b in zip(segment,segment[1:])):return None
        return change(q[-1][1],first[1]),first[0],end
    def facts(self,iid,now):
        c=self.config;q=self.prices.get(iid,[]);signals=[];windows={}
        for minutes,threshold in [(5,c['price_5m']),(15,c['price_15m'])]:
            d=self.delta(q,now,minutes*60000)
            windows[str(minutes)]=d[0] if d else None
            if d and abs(d[0])>=threshold:signals.append(dict(kind='price',window=minutes,value=d[0],direction='up' if d[0]>0 else 'down',source_at=d[2],source_from=d[1],span_ms=d[2]-d[1]))
        b=self.bars.get(iid,{});boundary=now//300000*300000
        current=b.get(boundary) or b.get(boundary-300000)
        vol=None
        if current and 0<=now-current.get('at',0)<=90000:
            prev=[b.get(current['open_time']-n*300000) for n in range(1,11)]
            if current.get('volume') is not None and all(r and r['closed'] and r.get('volume') is not None for r in prev):
                multiple=relative_volume(current['volume'],[r['volume'] for r in prev],10)
                if multiple is not None:vol=float(multiple)
                if vol is not None and vol>=c['volume_multiple'] and D(str(current['volume']))>=c['volume_min']:
                    signals.append(dict(kind='volume',window=5,value=vol,direction='up' if D(str(current['close']))>=D(str(current['open'])) else 'down',source_at=current['at'],intrabar=not current['closed']))
        oi=self.delta(self.oi.get(iid,[]),now,900000,60000,90000)
        if oi and abs(oi[0])>=c['oi_change']:signals.append(dict(kind='oi',window=15,value=oi[0],direction='up' if oi[0]>0 else 'down',source_at=oi[2],source_from=oi[1],span_ms=oi[2]-oi[1]))
        # Freeze the price inside the same evaluation lock as the trigger facts.
        # OI is independent: absence of a fresh price is not permission to invent it.
        fresh=bool(q) and 0<=now-q[-1][0]<=15000
        meta=self.catalog.get(iid,{})
        for reason in signals:
            reason['metric_direction']=reason['direction']
            reason['price_direction']=reason['direction'] if reason['kind'] in ('price','volume') else None
            reason['window_kind']='bar' if reason['kind']=='volume' else 'rolling'
            reason['metric_unit']='multiple' if reason['kind']=='volume' else 'percent'
            reason['price_kind']=self.price_kinds.get(iid) if reason['kind']=='price' else None
            reason['field_status']='available'
            if reason['kind']=='volume':
                reason.update(bar_closed=bool(current['closed']),source_from=current['open_time'],
                    span_ms=300000,baseline_method='prior_closed_bar_mean',baseline_count=10,
                    volume_unit=meta.get('quote_asset','USDT'),bar_open_time=current['open_time'])
            if reason['kind']=='oi':reason['quantity_unit']=meta.get('oi_unit')
        return dict(reasons=signals,changes=windows,rvol=vol,oi_change=oi[0] if oi else None,
                    basis_price=str(q[-1][1]) if fresh else None,
                    basis_at=q[-1][0] if fresh else None,basis_source='discovery_stream' if fresh else None,
                    basis_price_kind=self.price_kinds.get(iid) if fresh else None)
    def backgrounds(self,all_facts):
        peers={};contracts={}
        for k,m in self.catalog.items():
            venue=m.get('venue')
            if (m.get('liquidity') or 0)>=self.config['liquidity_min']:
                peers.setdefault(venue,{})[m['asset_id']]=all_facts.get(k,{})
            if venue=='binance':contracts.setdefault(m['asset_id'],[]).append(all_facts.get(k,{}))
        contexts={}
        for venue,group in peers.items():
            btc=venue+(':spot:' if venue=='binance_spot' else ':perp:')+'BTCUSDT'
            contexts[venue]={'version':'p39-background-1','btc_windows':all_facts.get(btc,{}).get('changes',{}),'coverage':{w:{'available':sum(p.get('changes',{}).get(w) is not None for p in group.values()),'eligible':len(group)} for w in ('5','15')},'continuity_check':'delta_requires_no_price_interval_over_15s'}
        return peers,contracts,contexts
    def classify(self,iid,facts,all_facts,background=None):
        if not facts['reasons']:return 'none'
        if any(r['kind']!='price' or r['direction']=='down' for r in facts['reasons']):return 'visible'
        meta=self.catalog[iid];venue=meta['venue'];btc=venue+(':spot:' if venue=='binance_spot' else ':perp:')+'BTCUSDT'
        if facts['rvol'] is None or (venue=='binance' and facts['oi_change'] is None):return 'unknown'
        if venue=='binance_spot':
            contracts=background[1].get(meta['asset_id'],[]) if background else [all_facts.get(k,{}) for k,m in self.catalog.items() if m['venue']=='binance' and m['asset_id']==meta['asset_id']]
            if contracts and any(f.get('oi_change') is None for f in contracts):return 'unknown'
            if any(abs(f['oi_change'])>=self.config['oi_change'] for f in contracts):return 'visible'
        peers=background[0].get(venue,{}) if background else {m['asset_id']:all_facts.get(k,{}) for k,m in self.catalog.items() if m['venue']==venue and (m.get('liquidity') or 0)>=self.config['liquidity_min']}
        for reason in facts['reasons']:
            w=str(reason['window']);btc_change=all_facts.get(btc,{}).get('changes',{}).get(w)
            valid=[p.get('changes',{}).get(w) for p in peers.values() if p.get('changes',{}).get(w) is not None]
            if btc_change is None or len(valid)<self.config['breadth_min'] or len(valid)/max(1,len(peers))<self.config['breadth_coverage']:return 'unknown'
            floor=.5 if w=='5' else 1
            broad=sum(v>=floor for v in valid)/len(valid)>=self.config['breadth_ratio']
            if not broad or btc_change<self.config['btc_'+w+'m'] or reason['value']-btc_change>=self.config['excess']:return 'visible'
        return 'market_comovement'
    def evaluate(self,now):
        with self.lock:
            facts={iid:self.facts(iid,now) for iid in self.catalog}
            background=self.backgrounds(facts)
            for iid,f in facts.items():
                venue=self.catalog[iid].get('venue');btc=venue+(':spot:' if venue=='binance_spot' else ':perp:')+'BTCUSDT' if venue else ''
                f['classification_context']=background[2].get(venue,{'version':'p39-background-1','coverage':{},'btc_windows':{}})
            return [(iid,f,self.classify(iid,f,facts,background)) for iid,f in facts.items() if f['reasons'] and (self.catalog[iid].get('liquidity') or 0)>=self.config['liquidity_min']]

    def remember_context(self,now):
        with self.lock:
            self.contexts.append((now,{iid:dict(m) for iid,m in self.catalog.items()},dict(self.config)))

    def evaluate_since(self,since,now):
        """Replay recorded endpoints, with contemporaneous peer prices and metadata.

        Select the first crossing per metric/window/direction and the current end.
        No endpoint is created across an offline or receive-time gap.
        """
        with self.lock:
            replay=Detector();replay.catalog={k:dict(v) for k,v in self.catalog.items()};replay.config=dict(self.config)
            replay.price_kinds=dict(self.price_kinds)
            prices={k:list(q) for k,q in self.prices.items()}
            known={k:dict(q) for k,q in self.price_known.items()}
            oi={k:list(q) for k,q in self.oi.items()};oi_known={k:dict(q) for k,q in self.oi_known.items()}
            bars={k:list(q) for k,q in self.bar_versions.items()}
            current_bars={k:dict(v) for k,v in self.bars.items()}
            contexts=list(self.contexts)
        # Indexed views avoid scanning thirty minutes of ticks for each endpoint.
        views={k:PriceView(q,known.get(k,{})) for k,q in prices.items()}
        candidates={};events=[]
        def config_at(at):
            context=next((c for c in reversed(contexts) if c[0]<=at),None)
            return context[2] if context else replay.config
        for iid,start in since.items():
            if iid not in replay.catalog:continue
            if not start:
                candidates.setdefault(now,set()).add(iid);continue
            found=set();view=views.get(iid)
            if view:
                for at in view.times:
                    at=view.known.get(at,at)
                    if not start<at<=now:continue
                    for window in (5,15):
                        d=view.delta(at,window*60000)
                        if d and abs(d[0])>=config_at(at)['price_'+str(window)+'m']:
                            key=('price',window,'up' if d[0]>0 else 'down')
                            if key not in found:found.add(key);candidates.setdefault(at,set()).add(iid)
            # Bar/OI revisions carry their own known time, not the batch start.
            bar_map={key:row for key,row in current_bars.get(iid,{}).items() if row.get('known_at',row.get('at',0))<=start}
            for row in bars.get(iid,()):
                stamp=row.get('known_at',row.get('at',0));bar_map[row['open_time']]=row
                if not start<stamp<=now:continue
                prev=[bar_map.get(row['open_time']-n*300000) for n in range(1,11)]
                if row.get('volume') is None or not all(r and r['closed'] and r.get('volume') is not None for r in prev):continue
                ratio=relative_volume(row['volume'],[r['volume'] for r in prev],10)
                direction='up' if D(str(row['close']))>=D(str(row['open'])) else 'down'
                key=('volume',5,direction)
                c=config_at(stamp)
                if ratio is not None and ratio>=c['volume_multiple'] and D(str(row['volume']))>=c['volume_min'] and key not in found:
                    found.add(key);candidates.setdefault(stamp,set()).add(iid)
            for at,_ in oi.get(iid,()):
                received=oi_known.get(iid,{}).get(at,at)
                if start<received<=now:candidates.setdefault(received,set()).add(iid)
            candidates.setdefault(now,set()).add(iid)
        emitted=set()
        for at,ids in sorted(candidates.items()):
            context=next((c for c in reversed(contexts) if c[0]<=at),None)
            if context:
                replay.catalog=context[1];replay.config=context[2]
            elif at!=now:continue
            replay.prices={k:v.at(at) for k,v in views.items()}
            replay.oi={k:[r for r in q if r[0]<=at and oi_known.get(k,{}).get(r[0],r[0])<=at] for k,q in oi.items()}
            replay.bars={}
            for k in ids:
                versions=bars.get(k,())
                b={}
                for row in versions:
                    if row.get('known_at',row.get('at',0))<=at:b[row['open_time']]=row
                # Restart seeds are eligible only after they were actually received.
                for key,row in current_bars.get(k,{}).items():
                    if key not in b and row.get('known_at',row.get('at',0))<=at:b[key]=row
                replay.bars[k]=b
            facts={k:{'changes':{str(w):(d[0] if (d:=views[k].delta(at,w*60000)) else None) for w in (5,15)}} if k in views else {} for k in replay.catalog}
            for k,m in replay.catalog.items():
                if m.get('venue')=='binance':
                    d=self.delta(replay.oi.get(k),at,900000,60000,90000)
                    facts[k]['oi_change']=d[0] if d else None
            background=replay.backgrounds(facts)
            for iid in ids:
                if iid not in replay.catalog or (replay.catalog[iid].get('liquidity') or 0)<replay.config['liquidity_min']:continue
                f=replay.facts(iid,at)
                reasons=[]
                for reason in f['reasons']:
                    key=(iid,reason['kind'],reason['window'],reason['direction'])
                    if key not in emitted or at==now:
                        reasons.append(reason);emitted.add(key)
                if not reasons:continue
                f['reasons']=reasons;f['classification_context']=background[2].get(replay.catalog[iid]['venue'])
                f['evaluated_at']=at;f['replayed']=at<now
                events.append((iid,f,replay.classify(iid,f,facts,background)))
        with self.lock:
            for iid,start in since.items():
                endpoints=[{'source_at':source,'known_at':oi_known.get(iid,{}).get(source,source)}
                    for source,_ in oi.get(iid,()) if start<oi_known.get(iid,{}).get(source,source)<=now]
                self.oi_evaluations.setdefault(iid,deque(maxlen=8)).append({'since':start,'evaluated_until':now,
                    'endpoints_in_memory':len(endpoints),'endpoints':endpoints[-8:],
                    'endpoints_truncated':len(endpoints)>8,'initial_scan':not bool(start),
                    'oi_reasons_emitted':sum(r['kind']=='oi' for key,f,_ in events if key==iid for r in f['reasons'])})
        return events


class PriceView:
    def __init__(self,rows,known):
        self.rows=rows;self.times=[r[0] for r in rows];self.known=known;self.gaps=[0]
        for a,b in zip(rows,rows[1:]):self.gaps.append(self.gaps[-1]+(b[0]-a[0]>15000))
    def at(self,now):
        end=bisect_right(self.times,now)-1
        while end>=0 and self.known.get(self.times[end],self.times[end])>now:end-=1
        return PriceSlice(self,end,now)
    def delta(self,now,window,tolerance=5000,max_age=15000):
        return self.at(now).delta(now,window,tolerance,max_age)


class PriceSlice:
    def __init__(self,view,end,now):self.view=view;self.end=end;self.now=now
    def __bool__(self):return self.end>=0
    def __getitem__(self,index):
        if index==-1:return self.view.rows[self.end]
        raise IndexError(index)
    def delta(self,now,window,tolerance=5000,max_age=15000):
        v=self.view;end=self.end
        if end<0 or not 0<=now-v.times[end]<=max_age:return None
        target=v.times[end]-window;start=bisect_left(v.times,target-tolerance)
        stop=bisect_right(v.times,target+tolerance,hi=end+1)
        choices=[i for i in range(start,stop) if v.known.get(v.times[i],v.times[i])<=self.now]
        if not choices:return None
        first=min(choices,key=lambda i:abs(v.times[i]-target))
        if tolerance==5000 and v.gaps[end]!=v.gaps[first]:return None
        return change(v.rows[end][1],v.rows[first][1]),v.times[first],v.times[end]

detector=Detector()

def ingest(venue,packet,received,connection_id=None):
    if venue not in ('binance','binance_spot'):return
    rows=packet.get('data',packet);rows=rows if isinstance(rows,list) else [rows]
    for row in rows:
        if not isinstance(row,dict):continue
        iid=venue+(':spot:' if venue=='binance_spot' else ':perp:')+str(row.get('s',''))
        kind=row.get('e');at=int(row.get('E') or row.get('T') or received)
        if not 0<=received-at<=15000:
            if kind in ('24hrMiniTicker','markPriceUpdate','kline'):
                detector.reject_price(iid,at,received,'mark' if kind=='markPriceUpdate' else 'last',connection_id,'source_age_invalid',kind)
            continue
        if kind in ('24hrMiniTicker','markPriceUpdate'):
            value=row.get('p') if kind=='markPriceUpdate' else row.get('c')
            if value:detector.price(iid,at,value,'mark' if kind=='markPriceUpdate' else 'last',received,connection_id,kind)
        if kind=='kline' and row.get('k',{}).get('i')=='5m':
            k=row['k'];detector.price(iid,at,k['c'],'last',received,connection_id,'kline_5m');detector.bar(iid,dict(open_time=int(k['t']),open=k['o'],close=k['c'],volume=k['q'],closed=bool(k['x']),at=at,known_at=received))
