"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import {api,CandleList,Candle,money,clock,pct} from "@/lib/api";
import {backendStudies} from "@/lib/backend-studies";
import type {Analysis,DecisionSnapshot,DerivativeHistory,StructureEvidence,Level} from "@/lib/pd2-types";
type Bar={time:number;open:number;high:number;low:number;close:number;volume:number};
type Info={ticker:string;name:string;description:string;type:string;session:string;timezone:string;exchange:string;minmov:number;pricescale:number;has_intraday:boolean;has_weekly_and_monthly:boolean;supported_resolutions:string[];volume_precision:number;data_status:string};
type Subscription<T>={subscribe:(owner:unknown,callback:(value:T)=>void)=>void;unsubscribeAll:(owner:unknown)=>void};
type DrawingState={sources:Map<string,unknown>|null;groups:Map<string,unknown>;symbol?:string};
type ChartSurface={
 getLineToolsState:()=>DrawingState;applyLineToolsState:(state:DrawingState)=>Promise<void>;
 dataReady:(callback:()=>void)=>boolean;
 createStudy:(name:string,overlay?:boolean,lock?:boolean,inputs?:Record<string,unknown>,overrides?:Record<string,unknown>)=>Promise<string|null>;
 getAllStudies:()=>{id:string;name:string}[];getStudyById:(id:string)=>{setVisible:(v:boolean)=>void;setInputValues:(v:{id:string;value:number}[])=>void};
 createShape:(point:{time:number;price?:number},options:Record<string,unknown>)=>Promise<string|null>;
 createMultipointShape:(points:{time:number;price:number}[],options:Record<string,unknown>)=>Promise<string|null>;
 removeEntity:(id:string)=>void;setVisibleRange:(range:{from:number;to:number})=>Promise<void>;
 getVisibleRange:()=>{from:number;to:number};onVisibleRangeChanged:()=>Subscription<{from:number;to:number}>;
 crossHairMoved:()=>Subscription<{time:number;price:number}>;
 getPanes:()=>{getMainSourcePriceScale:()=>{setAutoScale:(v:boolean)=>void;setVisiblePriceRange:(r:{from:number;to:number})=>void}|null}[];
 onIntervalChanged:()=>Subscription<string>;resolution:()=>string;
};
type ChartWidget={remove:()=>void;chartReady:()=>Promise<void>;activeChart:()=>ChartSurface};
type ChartConstructor=new(options:Record<string,unknown>)=>ChartWidget;
declare global {interface Window {TradingView?:{widget:ChartConstructor}}}
export type StructureOverlay={id:string;phase_label:string;first_seen_at:number;first_seen_price:string;evidence:StructureEvidence};
const toBar=(r:Candle):Bar=>({time:r.open_time,open:Number(r.open),high:Number(r.high),low:Number(r.low),close:Number(r.close),volume:r.base_volume==null&&r.quote_volume==null?NaN:Number(r.base_volume??r.quote_volume)});
const labels:Record<string,string>={"60":"1H","120":"2H","240":"4H","1D":"1D","1W":"1W"};
const direction:Record<string,string>={up:"结构上行",down:"结构下行",mixed:"整理/分歧",broken:"结构已破坏",unknown:"确认点不足"};
const momentum:Record<string,string>={strengthening:"动能增强",weakening:"动能减弱",unknown:"历史不足"};
const resolutions=["15","30","60","120","240","480","720","1D","1W"];
let libraryPromise:Promise<void>|undefined;
function loadLibrary(){
 if(window.TradingView)return Promise.resolve();
 if(!libraryPromise)libraryPromise=new Promise<void>((resolve,reject)=>{
  const script=document.createElement("script");script.dataset.erjiChart="true";script.src="/charting_library/charting_library.standalone.js";
  script.onload=()=>resolve();script.onerror=()=>{libraryPromise=undefined;script.remove();reject(new Error("图表资源加载失败"));};document.head.appendChild(script);
 });
 return libraryPromise;
}
export function Chart({instrument,episode,snapshotId,onSnapshot}:{instrument:string;episode?:string;snapshotId?:string;onSnapshot?:(snapshot:DecisionSnapshot|null)=>void}){
 const root=useRef<HTMLDivElement>(null);const widgetRef=useRef<ChartWidget|undefined>(undefined);
 const [drawingMessage,setDrawingMessage]=useState("");
 const [error,setError]=useState("");const [loading,setLoading]=useState(true);const [analysis,setAnalysis]=useState<Analysis>();
 const [snapshot,setSnapshot]=useState<DecisionSnapshot|null>(null);const [derivatives,setDerivatives]=useState<DerivativeHistory>();
 const [resolution,setResolution]=useState("240");const [preset,setPreset]=useState<"technical"|"derivatives">("technical");
 const [layers,setLayers]=useState({structure:true,targets:true,bollinger:false,fibonacci:false,liquidations:false,positions:false});
 const [range,setRange]=useState<{from:number;to:number}>();const [hoverTime,setHoverTime]=useState<number>();
 const overlayRef=useRef<StructureOverlay|null>(null);const analysisRef=useRef<Analysis|undefined>(undefined);
 const snapshotRef=useRef<DecisionSnapshot|null>(null);const shapes=useRef<string[]>([]);const drawSeq=useRef(0);
 const [studyState,setStudyState]=useState<string[]>([]);const macdId=useRef<string|null>(null);const bbId=useRef<string|null>(null);
 const backendIds=useRef<string[]>([]);const onSnapshotRef=useRef(onSnapshot);onSnapshotRef.current=onSnapshot;
 const [layerInfo,setLayerInfo]=useState<Level[]>([]);const layersRef=useRef(layers);layersRef.current=layers;
 const draw=useCallback(async()=>{
  const widget=widgetRef.current;if(!widget)return;const chart=widget.activeChart();const seq=++drawSeq.current;
  const currentLayers=layersRef.current;const signal=overlayRef.current;const snap=snapshotRef.current;
  const a=analysisRef.current;const tf=chart.resolution();const end=Math.floor((snap?.as_of||a?.as_of||Date.now())/1000);
  for(const id of shapes.current){try{chart.removeEntity(id)}catch{/* library may already have removed a layer */}}shapes.current=[];
  const options={lock:true,disableSave:true,disableUndo:true};
  const add=async(p:Promise<string|null>)=>{const id=await p;if(id){if(seq!==drawSeq.current){try{chart.removeEntity(id)}catch{}return;}shapes.current.push(id);}};
  const horizontal=async(label:string,price:string,color:string,dashed=false)=>{
   await add(chart.createShape({time:end,price:Number(price)},{...options,shape:"horizontal_line",text:label,overrides:{linecolor:color,linewidth:1,linestyle:dashed?2:0,showPrice:true,showLabel:true,textcolor:color}}));
  };
  const levels:Level[]=[];
  try{
   if(currentLayers.structure){
    const own=a?.timeframes.find(x=>x.timeframe===tf);
    const close=Number(a?.price||0);
    const nearest=[...(own?.levels||[])].sort((x,y)=>Math.abs(Number(x.low)-close)-Math.abs(Number(y.low)-close)).slice(0,2);
    for(const level of nearest){
     levels.push(level);await horizontal(level.label+" "+money(level.low),level.low,level.kind==="support"?"#298b7a":"#c28b35");
    }
    if(signal){
     const b=signal.evidence.box,e=signal.evidence;
     await add(chart.createMultipointShape([{time:b.start_time/1000,price:Number(b.upper)},{time:b.end_time/1000,price:Number(b.lower)}],
       {...options,shape:"rectangle",text:"4H 已确认箱体",overrides:{color:"#547fc2",backgroundColor:"#dbe8ff",fillBackground:true,transparency:85,linewidth:2}}));
     await horizontal("4H箱体上沿 / 收盘撤销",b.upper,"#547fc2");
     await horizontal("4H箱体下沿 / 结构失守",b.lower,"#7b8aa5");
     await horizontal("收盘突破门槛",b.trigger_price,"#15957b");
     if(e.entry_zone)await add(chart.createMultipointShape([{time:(e.trigger?.close_time||b.end_time)/1000,price:Number(e.entry_zone[1])},{time:end,price:Number(e.entry_zone[0])}],
       {...options,shape:"rectangle",text:"原计划参与区",overrides:{color:"#15957b",backgroundColor:"#c7eee2",fillBackground:true,transparency:80}}));
     if(e.protection)await horizontal("原始结构失效价",snap?.original_protection||e.protection,"#c45b70",true);
     if(snap?.current_protection)await horizontal("当前保护价",snap.current_protection,"#d14960");
     if(e.trigger)await add(chart.createShape({time:e.trigger.open_time/1000,price:Number(e.trigger.close)},{...options,shape:"arrow_up",text:labels[e.trigger.resolution]+"触发 · "+clock(e.trigger.close_time),overrides:{color:"#15957b"}}));
     await add(chart.createShape({time:signal.first_seen_at/1000},{...options,shape:"vertical_line",text:"首次信号 "+clock(signal.first_seen_at)+" / "+money(signal.first_seen_price),overrides:{linecolor:"#7688a5",linestyle:2,showLabel:true}}));
    }
   }
   if(currentLayers.targets){
    const op=snap?.opportunity_plan||a?.opportunity;
    const entries:[string,Level|null|undefined][]=[["第一目标",op?.t1],["日线主攻",op?.daily],["周线条件目标",op?.weekly]];
    const used=new Set<string>();
    for(const [title,level] of entries){
     if(!level||used.has(level.id))continue;used.add(level.id);levels.push(level);
     await horizontal(title+" "+money(level.low)+"–"+money(level.high),level.low,"#ad843e",level.method!=="confirmed_pivot"||title.includes("条件"));
    }
   }
   if(currentLayers.fibonacci){
    for(const f of a?.timeframes.find(x=>x.timeframe===tf)?.fibonacci||[])await horizontal("回撤辅助 "+f.ratio,f.price,"#9273b5",true);
   }
   if(currentLayers.liquidations){
    for(const event of snap?.derivatives?.native_tape?.liquidation_events||[]){if(Number(event.price)>0)await add(chart.createShape({time:event.event_time/1000,price:Number(event.price)},{...options,shape:"circle",text:(event.liquidated_side==="short"?"已观察空头清算":"已观察多头清算")+" · 原生采样",overrides:{color:"#ba69ad"}}));}
   }
   if(currentLayers.positions&&instrument.startsWith("hyperliquid:")){
    for(const position of snap?.derivatives?.visible_positions?.positions||[]){if(position.status==="fresh"&&position.liquidation_price)await horizontal("HL可见样本风险价 · "+position.side,position.liquidation_price,"#ae6ab3",true);}
   }
   if(seq===drawSeq.current)setLayerInfo(levels);
  }catch(e){if(seq===drawSeq.current)setError("图层更新失败："+(e as Error).message);}
 },[instrument]);
 useEffect(()=>{
  let live=true,pending=false;const ctrl=new AbortController();
  setAnalysis(undefined);setSnapshot(null);setDerivatives(undefined);overlayRef.current=null;analysisRef.current=undefined;snapshotRef.current=null;
  const load=async()=>{
   if(pending)return;pending=true;
   try{
    const result=await api<{snapshot:DecisionSnapshot|null}>("/chart/snapshot?instrument="+encodeURIComponent(instrument)+(episode?"&episode="+encodeURIComponent(episode):"")+(snapshotId?"&snapshot_id="+encodeURIComponent(snapshotId):""),{signal:ctrl.signal});
    if(!live)return;const snap=result.snapshot;
    const suffix=snap?"&snapshot_id="+encodeURIComponent(snap.id):"";
    const out=await Promise.allSettled([
     api<Analysis>("/analysis?instrument="+encodeURIComponent(instrument)+suffix,{signal:ctrl.signal}),
     api<DerivativeHistory>("/derivatives/history?instrument="+encodeURIComponent(instrument)+suffix,{signal:ctrl.signal}),
     !snap&&episode?api<StructureOverlay>("/structure-signals/"+encodeURIComponent(episode),{signal:ctrl.signal}):Promise.resolve(null)
    ]);
    if(!live)return;
    snapshotRef.current=snap;setSnapshot(snap);onSnapshotRef.current?.(snap);
    if(out[0].status==="fulfilled"){analysisRef.current=out[0].value;setAnalysis(out[0].value);}
    if(out[1].status==="fulfilled")setDerivatives(out[1].value);
    overlayRef.current=snap?{id:snap.episode_id,phase_label:snap.phase_label,first_seen_at:snap.first_seen_at,first_seen_price:snap.first_seen_price,evidence:snap.evidence}:out[2].status==="fulfilled"&&out[2].value?.evidence.instrument_id===instrument?out[2].value:null;
    for(const id of backendIds.current){try{widgetRef.current?.activeChart().getStudyById(id).setInputValues([{id:"revision",value:analysisRef.current?.as_of||0}])}catch{}}
    const rejected=out.find(r=>r.status==="rejected");
    if(rejected?.status==="rejected"&&!ctrl.signal.aborted)setError((rejected.reason as Error).message);
   }catch(e){if(live&&!ctrl.signal.aborted)setError((e as Error).message)}finally{pending=false;}
   await draw();
  };
  void load();
  const stream=new EventSource("/v1/stream");
  const refresh=()=>{if(!snapshotId)void load();};
  for(const name of ["signal_update","candle_revision","reset"])stream.addEventListener(name,refresh);
  const timer=snapshotId?undefined:setInterval(()=>void load(),15000);
  return()=>{live=false;ctrl.abort();stream.close();if(timer)clearInterval(timer)};
 },[instrument,episode,snapshotId,draw]);
 useEffect(()=>{
  let disposed=false;let widget:ChartWidget|undefined;const timers=new Map<string,ReturnType<typeof setInterval>>();
  const resets=new Map<string,()=>void>();const owner={};
  const stream=new EventSource("/v1/stream");
  stream.addEventListener("candle_revision",(event)=>{if(snapshotId)return;const value=JSON.parse((event as MessageEvent).data);if(value.instrument_id===instrument)for(const reset of resets.values())reset();});
  const feed={
   onReady:(cb:(x:unknown)=>void)=>setTimeout(()=>cb({supported_resolutions:resolutions,supports_time:false}),0),
   searchSymbols:(_q:string,_ex:string,_t:string,cb:(x:unknown[])=>void)=>cb([]),
   resolveSymbol:(_s:string,cb:(x:Info)=>void,fail:(s:string)=>void)=>{
    api<{rows:{id:string;native_symbol:string;venue:string;spec:{price_precision?:number;sz_decimals?:number;tick_size?:string}}[]}>("/instruments").then(data=>{
     const spec=data.rows.find(x=>x.id===instrument);if(!spec)throw Error("市场规格尚未就绪");
     const decimals=spec.spec.price_precision??Math.max(0,6-(spec.spec.sz_decimals??2));
     setTimeout(()=>{if(!disposed)cb({ticker:instrument,name:spec.native_symbol,description:spec.native_symbol+" · "+spec.venue,type:"crypto",session:"24x7",timezone:"Etc/UTC",exchange:spec.venue,minmov:1,pricescale:10**Math.min(decimals,12),has_intraday:true,has_weekly_and_monthly:true,supported_resolutions:resolutions,volume_precision:4,data_status:"streaming"})},0);
    }).catch(e=>{if(!disposed)fail(e.message)});
   },
   getBars:(_i:Info,r:string,period:{from:number;to:number;countBack:number},cb:(b:Bar[],meta:{noData:boolean})=>void,fail:(m:string)=>void)=>{
    api<CandleList>("/candles?instrument="+encodeURIComponent(instrument)+"&resolution="+r+"&before="+period.to*1000+"&limit="+Math.min(1500,Math.max(300,period.countBack))+(snapshotId?"&snapshot_id="+encodeURIComponent(snapshotId):"")).then(result=>{
     if(!disposed)setTimeout(()=>cb(result.rows.map(toBar),{noData:result.rows.length===0}),0);
    }).catch(e=>{if(!disposed){setError(e.message);fail(e.message)}});
   },
   subscribeBars:(_i:Info,r:string,cb:(b:Bar)=>void,id:string,reset:()=>void)=>{
    if(snapshotId)return;
    let pending=false,last=0;resets.set(id,reset);
    timers.set(id,setInterval(async()=>{if(pending||disposed)return;pending=true;try{
     const result=await api<CandleList>("/candles?instrument="+encodeURIComponent(instrument)+"&resolution="+r+"&limit=2");
     const b=result.rows.at(-1);if(b&&!disposed&&b.open_time>=last){last=b.open_time;cb(toBar(b));}
    }catch{/* visible diagnostics are supplied by the API panel */}finally{pending=false}},5000));
   },
   unsubscribeBars:(id:string)=>{const t=timers.get(id);if(t)clearInterval(t);timers.delete(id);resets.delete(id);}
  };
  async function start(){
   try{
    await loadLibrary();if(disposed||!root.current||!window.TradingView)return;
    if(!analysisRef.current)analysisRef.current=await api<Analysis>("/analysis?instrument="+encodeURIComponent(instrument)+(snapshotId?"&snapshot_id="+encodeURIComponent(snapshotId):""));
    if(disposed)return;setAnalysis(analysisRef.current);
    const saved=await api<{payload:{resolution?:string;preset?:"technical"|"derivatives";layers?:Partial<typeof layers>}}>("/preferences/chart").catch(()=>({payload:{resolution:undefined,preset:undefined,layers:undefined}}));
    if(disposed)return;
    if(saved.payload.preset)setPreset(saved.payload.preset);if("layers" in saved.payload&&saved.payload.layers)setLayers(v=>({...v,...saved.payload.layers}));
    const initial=episode?"240":saved.payload.resolution||"240";setResolution(initial);
    widget=new window.TradingView.widget({container:root.current,library_path:"/charting_library/",datafeed:feed,symbol:instrument,interval:initial,
      custom_indicators_getter:(PineJS:Parameters<typeof backendStudies>[0])=>backendStudies(PineJS,()=>analysisRef.current),
      locale:"zh",timezone:"Etc/UTC",theme:"light",autosize:true,enabled_features:["iframe_loading_same_origin","saveload_separate_drawings_storage"],
      disabled_features:["header_symbol_search","symbol_search_hot_key","header_compare","use_localstorage_for_settings"],
      overrides:{"mainSeriesProperties.candleStyle.upColor":"#15967b","mainSeriesProperties.candleStyle.downColor":"#df5b67","mainSeriesProperties.candleStyle.wickUpColor":"#15967b","mainSeriesProperties.candleStyle.wickDownColor":"#df5b67","paneProperties.backgroundType":"solid","paneProperties.background":"#ffffff"}});
    await widget.chartReady();if(disposed)return;widgetRef.current=widget;const chart=widget.activeChart();
    chart.onIntervalChanged().subscribe(owner,(r)=>{setResolution(r);void api("/preferences/chart",{method:"PUT",body:JSON.stringify({payload:{resolution:r,preset,layers:layersRef.current}})});void draw();});
    chart.onVisibleRangeChanged().subscribe(owner,setRange);
    chart.crossHairMoved().subscribe(owner,({time})=>{if(Number.isFinite(time))setHoverTime(time*1000)});
    const studies:string[]=[];
    const ema=await chart.createStudy("ERJI 趋势 EMA",true,true);if(ema)backendIds.current.push(ema);studies.push("EMA20/50（后台同源）");
    if(!chart.getAllStudies().some(x=>/volume|成交量/i.test(x.name)))await chart.createStudy("Volume",false,false);
    studies.push("成交量");
    macdId.current=await chart.createStudy("ERJI MACD",false,true);if(macdId.current)backendIds.current.push(macdId.current);studies.push("MACD 12/26/9（后台同源）");
    setStudyState(studies);setLoading(false);
    await new Promise<void>(resolve=>{if(chart.dataReady(resolve))resolve()});
    if(!disposed){setRange(chart.getVisibleRange());await draw();
     const savedDrawings=await api<{payload:{sources?:[string,unknown][];groups?:[string,unknown][];symbol?:string}}>("/preferences/"+await drawingKey(instrument));
     if(!disposed&&savedDrawings.payload.sources)await chart.applyLineToolsState({sources:new Map(savedDrawings.payload.sources),groups:new Map(savedDrawings.payload.groups||[]),symbol:savedDrawings.payload.symbol});
    }
   }catch(e){if(!disposed){setError((e as Error).message);setLoading(false);}}
  }
  setError("");setLoading(true);setStudyState([]);void start();
  return()=>{disposed=true;drawSeq.current++;widgetRef.current=undefined;shapes.current=[];backendIds.current=[];macdId.current=null;bbId.current=null;stream.close();for(const timer of timers.values())clearInterval(timer);if(widget){try{widget.activeChart().onIntervalChanged().unsubscribeAll(owner);widget.activeChart().onVisibleRangeChanged().unsubscribeAll(owner);widget.activeChart().crossHairMoved().unsubscribeAll(owner)}catch{}widget.remove();}};
 },[instrument,episode,snapshotId,draw]);
 useEffect(()=>{void draw();const chart=widgetRef.current?.activeChart();if(!chart)return;
  if(macdId.current)chart.getStudyById(macdId.current).setVisible(preset==="technical");
  void (async()=>{try{if(layers.bollinger&&!bbId.current){bbId.current=await chart.createStudy("ERJI 布林带",true,true);if(bbId.current)backendIds.current.push(bbId.current);}if(bbId.current)chart.getStudyById(bbId.current).setVisible(layers.bollinger)}catch(e){setError((e as Error).message)}})();
  if(studyState.length)void api("/preferences/chart",{method:"PUT",body:JSON.stringify({payload:{resolution,preset,layers}})});
 },[layers,preset,studyState,resolution,draw]);
 async function locate(mode:"first"|"current"|"targets"){
  const chart=widgetRef.current?.activeChart();if(!chart)return;const source=overlayRef.current;const duration=resolution==="1W"?604800:resolution==="1D"?86400:Number(resolution)*60;
  const end=mode==="first"&&source?source.first_seen_at/1000:(snapshotRef.current?.as_of||Date.now())/1000;
  if(mode==="targets"){const values=layerInfo.flatMap(l=>[Number(l.low),Number(l.high)]).filter(n=>n>0);if(values.length){const scale=chart.getPanes()[0]?.getMainSourcePriceScale();scale?.setAutoScale(false);scale?.setVisiblePriceRange({from:Math.min(...values)*.95,to:Math.max(...values)*1.05});}}
  await chart.setVisibleRange({from:mode==="first"&&source?source.evidence.box.start_time/1000:end-duration*100,to:end+duration*5});
 }
 async function saveDrawings(){
  try{const chart=widgetRef.current?.activeChart();if(!chart)return;const state=chart.getLineToolsState();
   const sources=[...(state.sources||new Map()).entries()].filter(([id])=>!shapes.current.includes(id));
   await api("/preferences/"+await drawingKey(instrument),{method:"PUT",body:JSON.stringify({payload:{sources,groups:[...state.groups.entries()],symbol:state.symbol,instrument_id:instrument}})});setDrawingMessage("手工绘图已按当前市场保存");
  }catch(e){setDrawingMessage((e as Error).message)}
 }
 const assessments=snapshotId?snapshot?.timeframe_assessments:analysis?.timeframes;
 return <section className="pd2-chart">
  <div className="chart-decision"><b>{snapshot?.action.label||"当前技术结构"}</b><span>{snapshot?.trade_thesis.headline||"根据已闭合K线计算；没有符合模板的计划时不生成买入建议。"}</span>{snapshot&&<small>{snapshotId?"历史冻结快照":"当前快照"} · {clock(snapshot.as_of)}</small>}</div>
  <div className="timeframe-matrix">{assessments?.map(a=><div key={a.timeframe}><b>{labels[a.timeframe]||a.timeframe}</b><span>{direction[a.direction]}</span><span>{momentum[a.momentum]}{a.macd_cross_confirmed?" · 新金叉已确认":""}</span><small>{a.status==="fresh"?"收盘 "+clock(a.closed_at):a.reason}</small></div>)}</div>
  <div className="chart-toolbar"><div role="group" aria-label="图表视图"><button className={preset==="technical"?"active":""} onClick={()=>setPreset("technical")}>技术分析</button><button className={preset==="derivatives"?"active":""} onClick={()=>setPreset("derivatives")}>合约分析</button></div>
   {([["structure","结构 / 参与 / 保护"],["targets","日周目标"],["bollinger","布林带"],["fibonacci","回撤辅助"],["liquidations","已发生清算"],["positions","HL可见仓位风险价"]] as const).map(([key,label])=><label key={key}><input type="checkbox" checked={layers[key]} onChange={e=>setLayers({...layers,[key]:e.target.checked})}/>{label}</label>)}
   {episode&&<button onClick={()=>void locate("first")}>首次信号</button>}<button onClick={()=>void locate("current")}>当前趋势</button><button onClick={()=>void locate("targets")}>查看全部目标</button><button onClick={()=>void saveDrawings()}>保存我的绘图</button><small>{drawingMessage}</small>
  </div>
  <div className="chart-container">{loading&&!error&&<div className="chart-loading">正在加载K线及默认指标…</div>}{error&&<div className="chart-error">{error}</div>}<div ref={root} className="chart-root"/></div>
  <div className="chart-caption">自动显示：{studyState.join(" · ")||"加载中"}。EMA/MACD/布林带绘制后台已闭合序列，覆盖当前分析窗口；预热或窗口外留空。成交量按本市场数量单位，链上池为USD成交额。{snapshotId?"指标、信号依据和K线均冻结在该快照时刻。":"盘中K线变化不等于已收盘信号。"}</div>
  {preset==="derivatives"&&<div className="derivative-panels">
   <HistoryPanel title={"数量 OI · "+(derivatives?.quantity_unit||"")} rows={(derivatives?.oi||[]).map(x=>({time:x.time,value:x.quantity}))} range={range} hoverTime={hoverTime} onHover={setHoverTime} status={derivatives?.oi_status}/>
   <HistoryPanel title={"OI 名义金额 · "+(derivatives?.quote_asset||"")} rows={(derivatives?.oi||[]).map(x=>({time:x.time,value:x.notional_quote}))} range={range} hoverTime={hoverTime} onHover={setHoverTime} status={derivatives?.oi_status}/>
   <HistoryPanel title="实际结算资金费率 · %" rows={(derivatives?.funding||[]).map(x=>({time:x.time,value:String(Number(x.rate)*100)}))} range={range} hoverTime={hoverTime} onHover={setHoverTime} status={derivatives?.funding_status}/>
   <p className="chart-caption">三个面板使用K线当前可见时间范围，同一光标时间对照。OI缺口不补零；结算费率不代表预测费率。鼠标在主K线上移动会同步三个副图的时间。</p>
  </div>}
  {preset==="derivatives"&&snapshot?.derivatives&&<section className="panel" style={{padding:16}}><b>清算证据 · ByKaranteli / 原生 / CMC</b><p>跨平台模型只在下表作参考，不能套成当前交易所的精确爆仓价。</p><div className="table-scroll"><table><thead><tr><th>证据类型</th><th>方向</th><th>价格 / 金额</th><th>有效性</th></tr></thead><tbody>{snapshot.derivatives.liquidation_model?.levels?.slice(0,30).map(l=><tr key={l.id}><td>供应商模型区</td><td>{l.liquidated_side==="short"?"潜在空头风险":"潜在多头风险"}</td><td>{money(l.price)}</td><td>{l.status==="fresh"?"当前参考（模型）":"已过期或身份不匹配，退出当前判断"}</td></tr>)}{snapshot.derivatives.liquidation_summary?.windows?.map(w=><tr key={w.period}><td>CMC已发生 · {w.period}</td><td>空头 / 多头</td><td>{money(w.short_usd)} / {money(w.long_usd)} USD</td><td>{w.status==="fresh"?"有效汇总，覆盖范围内":"过期或未返回"}</td></tr>)}</tbody></table></div><small>模型：{snapshot.derivatives.liquidation_model?.status}；HL样本：{snapshot.derivatives.visible_positions?.status}。缺失不填零，价格触及不等于实际清算。</small></section>}
  <details className="chart-level-details"><summary>图上点位及计算依据 · {layerInfo.length}</summary><div className="table-scroll"><table><thead><tr><th>含义</th><th>价格区</th><th>周期 / 方法</th><th>确认时间</th><th>计算依据</th></tr></thead><tbody>{layerInfo.map((l,i)=><tr key={l.id+":"+i}><td>{l.label}</td><td>{money(l.low)}–{money(l.high)}</td><td>{labels[l.timeframe]} · {l.method}</td><td>{clock(l.known_at)}</td><td>{l.formula||"对应已确认结构"}{l.price_multiple?" · 价格"+Number(l.price_multiple).toFixed(2)+"倍":""}</td></tr>)}</tbody></table></div>{!layerInfo.length&&<p>当前没有可用结构点位，基础趋势指标仍正常显示。</p>}</details>
 </section>
}
function HistoryPanel({title,rows,range,hoverTime,onHover,status}:{title:string;rows:{time:number;value:string|null}[];range?:{from:number;to:number};hoverTime?:number;onHover:(n:number)=>void;status?:string}){
 const points=rows.filter(x=>x.value!=null&&(!range||x.time>=range.from*1000&&x.time<=range.to*1000));
 const from=range?range.from*1000:points[0]?.time||0,to=range?range.to*1000:points.at(-1)?.time||1;
 const values=points.map(x=>Number(x.value));const lo=values.length?Math.min(...values):0,hi=values.length?Math.max(...values):1;
 const x=(t:number)=>((t-from)/Math.max(1,to-from))*1000,y=(v:number)=>85-(v-lo)/Math.max(1e-12,hi-lo)*65;
 const path=points.map((p,i)=>(i&&p.time-points[i-1].time<900000?"L":"M")+x(p.time).toFixed(2)+","+y(Number(p.value)).toFixed(2)).join(" ");
 const selected=hoverTime?points.reduce<{time:number;value:string|null}|undefined>((a,b)=>!a||Math.abs(b.time-hoverTime)<Math.abs(a.time-hoverTime)?b:a,undefined):points.at(-1);
 return <section className="history-panel"><header><b>{title}</b><span>{selected?money(selected.value)+" · "+clock(selected.time):"此可见范围暂无数据"} {status==="stale"?"（已过期）":""}</span></header>
  {points.length?<svg role="img" aria-label={title+"历史曲线"} viewBox="0 0 1000 105" preserveAspectRatio="none" onPointerMove={event=>{const r=event.currentTarget.getBoundingClientRect();onHover(from+(event.clientX-r.left)/r.width*(to-from));}}><path d={path} fill="none" stroke="#188b80" strokeWidth="2" vectorEffect="non-scaling-stroke"/>{points.map((p,i)=><circle key={i} cx={x(p.time)} cy={y(Number(p.value))} r="1.5" fill="#188b80"/>)}{hoverTime&&<line x1={x(hoverTime)} x2={x(hoverTime)} y1="0" y2="105" stroke="#97a9b8" strokeDasharray="4 4"/>}</svg>:<div className="empty compact">历史不足、未覆盖或当前范围无样本；不会填零。</div>}
 </section>
}

async function drawingKey(instrument:string){const bytes=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(instrument));return "chart-drawings:"+Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,"0")).join("");}
