"use client";
import {appPath} from "@/lib/paths";
import {useCallback,useEffect,useRef,useState} from "react";
import {api,CandleList,Candle,money,clock,pct} from "@/lib/api";
import {priceText} from "@/lib/opportunity-display";
import {backendStudies} from "@/lib/backend-studies";
import type {Analysis,DecisionSnapshot,DerivativeHistory,StructureEvidence,Level,DrawingContract} from "@/lib/pd2-types";
type Bar={time:number;open:number;high:number;low:number;close:number;volume?:number};
type Info={ticker:string;name:string;description:string;type:string;session:string;timezone:string;exchange:string;minmov:number;pricescale:number;has_intraday:boolean;has_weekly_and_monthly:boolean;supported_resolutions:string[];volume_precision:number;data_status:string};
type Subscription<T>={subscribe:(owner:unknown,callback:(value:T)=>void)=>void;unsubscribeAll:(owner:unknown)=>void};
type DrawingState={sources:Map<string,unknown>|null;groups:Map<string,unknown>;symbol?:string};
type ChartSurface={
 getShapeById:(id:string)=>{setPoints:(p:{time:number;price?:number}[])=>void;setProperties:(p:Record<string,unknown>)=>void};
 setResolution:(resolution:string)=>Promise<boolean>;
 getLineToolsState:()=>DrawingState;applyLineToolsState:(state:DrawingState)=>Promise<void>;
 dataReady:(callback?:()=>void)=>boolean;
 createStudy:(name:string,overlay?:boolean,lock?:boolean,inputs?:Record<string,unknown>,overrides?:Record<string,unknown>)=>Promise<string|null>;
 getAllStudies:()=>{id:string;name:string}[];getStudyById:(id:string)=>{setVisible:(v:boolean)=>void;setInputValues:(v:{id:string;value:number}[])=>void};
 createShape:(point:{time:number;price?:number},options:Record<string,unknown>)=>Promise<string|null>;
 createMultipointShape:(points:{time:number;price:number}[],options:Record<string,unknown>)=>Promise<string|null>;
 removeEntity:(id:string)=>void;setVisibleRange:(range:{from:number;to:number})=>Promise<void>;
 getVisibleRange:()=>{from:number;to:number};onVisibleRangeChanged:()=>Subscription<{from:number;to:number}>;
 crossHairMoved:()=>Subscription<{time:number;price:number}>;
 getPanes:()=>{getHeight:()=>number;getMainSourcePriceScale:()=>{getVisiblePriceRange:()=>{from:number;to:number}|null;setAutoScale:(v:boolean)=>void;setVisiblePriceRange:(r:{from:number;to:number})=>void}|null}[];
 onIntervalChanged:()=>Subscription<string>;resolution:()=>string;
};
type ChartWidget={changeTheme:(theme:string)=>Promise<void>;remove:()=>void;chartReady:()=>Promise<void>;activeChart:()=>ChartSurface};
type ChartConstructor=new(options:Record<string,unknown>)=>ChartWidget;
declare global {interface Window {TradingView?:{widget:ChartConstructor}}}
export type StructureOverlay={candidate_quality?:{status:string;reason:string}|null;drawing_contract?:DrawingContract|null;id:string;phase_label:string;first_seen_at:number;first_seen_price:string;evidence:StructureEvidence};
const toBar=(r:Candle):Bar=>({time:r.open_time,open:Number(r.open),high:Number(r.high),low:Number(r.low),close:Number(r.close),...(r.base_volume==null?{}:{volume:Number(r.base_volume)})});
const labels:Record<string,string>={"60":"1H","120":"2H","240":"4H","4H":"4H","1D":"1D","1W":"1W"};
const direction:Record<string,string>={up:"结构上行",down:"结构下行",mixed:"整理/分歧",broken:"结构已破坏",unknown:"确认点不足"};
const momentum:Record<string,string>={strengthening:"动能增强",weakening:"动能减弱",unknown:"历史不足"};
const resolutions=["5","15","30","60","120","240","480","720","1D","1W"];
const strategyNames:Record<string,string>={early_breakout:"箱体早期触发",confirmed_breakout:"箱体突破",box_breakout:"箱体突破",trend_restart:"新箱体再突破",recent_high:"近期前高突破",ath:"历史新高突破",retest:"突破后回踩",squeeze:"收缩突破",double_bottom:"双底突破",early_strength:"多周期转强",trend_pullback:"趋势回踩",failed_breakdown:"假跌破收回",repeated_test:"多轮试探再突破"};
let libraryPromise:Promise<void>|undefined;
function loadLibrary(){
 if(window.TradingView)return Promise.resolve();
 if(!libraryPromise)libraryPromise=new Promise<void>((resolve,reject)=>{
  const script=document.createElement("script");script.dataset.erjiChart="true";script.src=appPath("/charting_library/charting_library.standalone.js");
  script.onload=()=>resolve();script.onerror=()=>{libraryPromise=undefined;script.remove();reject(new Error("图表资源加载失败"));};document.head.appendChild(script);
 });
 return libraryPromise;
}
export function Chart({instrument,episode,snapshotId,onSnapshot,observation}:{instrument:string;episode?:string;snapshotId?:string;onSnapshot?:(snapshot:DecisionSnapshot|null)=>void;observation?:{at:number;price:string;events?:{at:number;price?:string}[]}}){
 const root=useRef<HTMLDivElement>(null);const widgetRef=useRef<ChartWidget|undefined>(undefined);
 const [priceTick,setPriceTick]=useState<string|null>(null);
 const [candleAsOf,setCandleAsOf]=useState<number|null>(null);
 const [drawingMessage,setDrawingMessage]=useState("");
 const [rangeMessage,setRangeMessage]=useState("");
 const loadedTimes=useRef(new Map<string,Set<number>>());
 const [error,setError]=useState("");const [loading,setLoading]=useState(true);const [analysis,setAnalysis]=useState<Analysis>();
 const [snapshot,setSnapshot]=useState<DecisionSnapshot|null>(null);const [derivatives,setDerivatives]=useState<DerivativeHistory>();
 const [resolution,setResolution]=useState("240");const [preset,setPreset]=useState<"technical"|"derivatives">("technical");
 const resolutionRef=useRef(resolution);resolutionRef.current=resolution;
 const defaultLayers={structure:true,levels:true,plan:true,markers:true,history:false,crossFrame:false,targets:true,allTargets:false,ema:false,macd:false,bb:false,avwap:false,rsi:false,adx:false,fib:false};
 const [layers,setLayers]=useState(defaultLayers);
 const overridesRef=useRef<Partial<typeof defaultLayers>>({});
 const resolutionExplicit=useRef(false);
 const defaultStyle={width:1,transparency:92,detailed:false,resistance:"#bc5264",support:"#168572",structure:"#647c95",theme:"light"};
 const [style,setStyle]=useState(defaultStyle);const styleRef=useRef(style);styleRef.current=style;
 const shapeCache=useRef(new Map<string,{id:string;signature:string;kind:string}>());
 const historyRef=useRef<DrawingContract[]>([]);
 const setLayer=(key:keyof typeof defaultLayers,value:boolean)=>{overridesRef.current={...overridesRef.current,[key]:value};setLayers(v=>({...v,[key]:value}));};
 const applyDefaults=(drawing?:DrawingContract|null)=>{const required=drawing?.required_indicators||[];setLayers(v=>({...v,ema:required.includes('ema'),macd:required.includes('macd'),bb:required.includes('bb'),...overridesRef.current}));};
 const [range,setRange]=useState<{from:number;to:number}>();const [hoverTime,setHoverTime]=useState<number>();
 const overlayRef=useRef<StructureOverlay|null>(null);const analysisRef=useRef<Analysis|undefined>(undefined);
 const snapshotRef=useRef<DecisionSnapshot|null>(null);const shapes=useRef<string[]>([]);const drawSeq=useRef(0);
 const [studyState,setStudyState]=useState<string[]>([]);
 const [seriesCoverage,setSeriesCoverage]=useState<string>("");
 const candleResets=useRef(new Map<string,()=>void>());
 const backendIds=useRef<string[]>([]);const onSnapshotRef=useRef(onSnapshot);onSnapshotRef.current=onSnapshot;
 const auxiliaryIds=useRef<Record<string,string>>({});
 const defaultsEvent=useRef<string|undefined>(undefined);
 const [candidateDrawing,setCandidateDrawing]=useState<DrawingContract|null>(null);
 const [candidateAudit,setCandidateAudit]=useState<StructureOverlay['candidate_quality']>(null);
 const [layerInfo,setLayerInfo]=useState<Level[]>([]);const layersRef=useRef(layers);layersRef.current=layers;
 const observationRef=useRef(observation);observationRef.current=observation;
 const draw=useCallback(async()=>{
  const observation=observationRef.current;
  const widget=widgetRef.current;if(!widget)return;const chart=widget.activeChart();const seq=++drawSeq.current;
  const view=chart.getVisibleRange();if(!chart.dataReady()||!view?.to||!Number.isFinite(view.from))return;
  const opts=styleRef.current,l=layersRef.current,snap=snapshotRef.current,d=snap?.drawing_contract||overlayRef.current?.drawing_contract;
  const frame=chart.resolution(),step=frame==='1W'?604800:frame==='1D'?86400:Number(frame)*60;
  const currentEnd=snapshotId?(snap?.as_of||Date.now())/1000:Math.floor(Date.now()/1000/step)*step+step;
  const desired=new Map<string,{points:{time:number;price?:number}[];options:Record<string,unknown>}>();
  const levels:Level[]=[];const labelsToDraw:{price:number;text:string;color:string;key:string}[]=[];
  const common={lock:true,disableSave:true,disableUndo:true,disableSelection:true};
  const add=(key:string,shape:string,points:{time:number;price?:number}[],props:Record<string,unknown>,text='')=>{
   if(points.every(p=>Number.isFinite(p.time)&&p.time>0&&(p.price===undefined||Number.isFinite(p.price)&&p.price>0)))desired.set(key,{points,options:{...common,shape,text,overrides:props}});
  };
  const segment=(key:string,a:number,b:number,price:number,color:string,dashed=false)=>add(key,'trend_line',[{time:a,price},{time:Math.max(a+step,b),price}],{linecolor:color,linewidth:opts.width,linestyle:dashed?2:0,showPriceRange:false,showDateRange:false,showBarsRange:false});
  const label=(key:string,text:string,price:number,color:string)=>{const near=labelsToDraw.find(p=>Math.abs(p.price-price)<=Math.max(Number(priceTick)||0,price*1e-8));if(near){if(!near.text.includes(text))near.text+=' / '+text;}else labelsToDraw.push({key,text,price,color});};
  if(observation)for(const [i,event] of (observation.events||[]).slice(-20).entries())if(event.price)add("observation-repeat-"+i,"arrow_up",[{time:Math.floor(event.at/1000/step)*step,price:Number(event.price)}],{color:"#8096b7"},"后续异动");
  if(observation)add("observation-first","arrow_up",[{time:Math.floor(observation.at/1000/step)*step,price:Number(observation.price)}],{color:"#245bd6"},"首次异动");
  let crossHidden=false,missingRange=false;
  const render=(drawing:DrawingContract,historical=false)=>{
   if(drawing.market_id!==instrument||drawing.status==='missing_anchors')return;
   if(drawing.timeframe!==frame&&!l.crossFrame){if(!historical)crossHidden=true;return;}
   const prefix=drawing.event_id+(historical?':past':':current'),tf=labels[drawing.timeframe||'']||drawing.timeframe||'',color=historical?'#9aa9b6':opts.structure;
   const valid=!historical&&!['ended','invalidated','expired'].includes(snap?.action.code||'');
   const last=historical?drawing.as_of/1000:currentEnd;
   const horizontal=(key:string,name:string,value:string,from:number,to:number,roleColor:string)=>{
    const price=Number(value),c=historical?color:roleColor;
    if(l.levels){segment(prefix+key+':formed',from/1000,to/1000,price,c);if(valid&&last>to/1000)segment(prefix+key+':extension',to/1000,last,price,c,true);}
    if(!historical){label(prefix+key,tf+' '+name,price,c);levels.push({id:prefix+key,label:name,low:value,high:value,timeframe:drawing.timeframe||frame,method:'recorded_structure',known_at:to});}
   };
   if(l.structure){
    for(const [index,a] of drawing.anchors.entries()){
     const key=prefix+':anchor:'+a.kind+':'+index;
     if(a.kind==='box'){
      const finish=a.last_open_time??a.end_time,slot=(n:number)=>Math.floor((n/1000-(frame==='1W'?345600:0))/step);
      if(slot(a.start_time)!==slot(finish))add(key,'rectangle',[{time:a.start_time/1000,price:Number(a.upper)},{time:finish/1000,price:Number(a.lower)}],{color,backgroundColor:color,transparency:historical?97:opts.transparency,fillBackground:true,linewidth:opts.width,showLabel:false});
      else missingRange=true;
      horizontal(':upper','阻力',a.upper,a.start_time,a.end_time,opts.resistance);horizontal(':lower','支撑',a.lower,a.start_time,a.end_time,opts.support);
     }else if(a.kind==='boundary'){
      horizontal(':boundary:'+index,a.label,a.price,a.start_time,a.known_at,/支撑|回踩/.test(a.label)?opts.support:opts.resistance);
      if(drawing.strategy_id==='squeeze')add(key+':range','trend_line',[{time:a.start_time/1000,price:Number(a.price)},{time:a.known_at/1000,price:Number(a.price)}],{linecolor:color,linewidth:opts.width+1,linestyle:2});
     }else if(a.kind==='trigger_boundary')horizontal(':trigger','触发',a.price,a.known_at,a.known_at,'#bd811b');
     else if(a.kind==='pivot'){
      if(l.markers)add(key,'icon',[{time:a.time/1000,price:Number(a.price)}],{color,size:7},'');
      if(!historical)levels.push({id:key,label:a.label,low:a.price,high:a.price,timeframe:drawing.timeframe||frame,method:'confirmed_pivot',known_at:a.known_at});
      if(opts.detailed&&!historical)label(key,tf+' '+a.label+(drawing.strategy_id==='repeated_test'?' '+index:''),Number(a.price),color);
     }else if(a.kind==='trigger_bar'&&l.markers){
      add(key,'arrow_up',[{time:a.open_time/1000,price:Number(a.price)}],{color:historical?color:opts.support});
      if(!historical)levels.push({id:key,label:'突破确认',low:a.price,high:a.price,timeframe:a.timeframe,method:'closed_confirmation',known_at:a.close_time});
     }
    }
    if(drawing.strategy_id==='double_bottom'){
     const points=drawing.anchors.filter(a=>a.kind==='pivot');const neck=drawing.anchors.find(a=>a.kind==='boundary');
     if(points.length>=2&&neck?.kind==='boundary'){
      const [a,b]=points;add(prefix+':neck-link','trend_line',[{time:a.time/1000,price:Number(a.price)},{time:b.time/1000,price:Number(b.price)}],{linecolor:color,linewidth:opts.width,linestyle:2});
      // Only confirmed lows are joined; never invent a timestamp for the neckline high.
     }
    }
   }
   if(!historical&&l.plan&&valid){
    const zone=drawing.entry_zone;
    if(zone){add(prefix+':entry','rectangle',[{time:zone.valid_from/1000,price:Number(zone.high)},{time:last,price:Number(zone.low)}],{color:opts.support,backgroundColor:opts.support,transparency:opts.transparency,fillBackground:true,linewidth:1});label(prefix+':entry','参与 '+priceText(zone.low)+'–'+priceText(zone.high),Number(zone.high),opts.support);}
    if(drawing.protection){horizontal(':stop','保护',drawing.protection.price,drawing.protection.valid_from,drawing.protection.known_at,opts.resistance);}
    if(opts.detailed){const trail=(drawing.protection_history||[]).slice(-12);for(const [i,p] of trail.entries()){const end=trail[i+1]?.observed_at||drawing.as_of;segment(prefix+':stop-history:'+p.observed_at,p.observed_at/1000,end/1000,Number(p.price),opts.resistance,true);}}
   }
   if(!historical&&l.targets&&valid){
    const targets=[['第一目标',drawing.first_target],...(l.allTargets?[['日线目标',snap?.opportunity_plan?.daily],['周线目标',snap?.opportunity_plan?.weekly]]:[])] as [string,DrawingContract['first_target']|undefined][];
    for(const [name,t] of targets){if(!t)continue;horizontal(':target:'+name,name,t.low,t.known_at||drawing.as_of,t.known_at||drawing.as_of,'#a0792c');}
   }
  };
  try{
   if(l.history)for(const past of historyRef.current.filter(p=>p.event_id!==d?.event_id).slice(0,3))render(past,true);
   if(d&&((snap&&d.snapshot_id===snap.id&&d.event_id===snap.episode_id&&d.series_revision===snap.series_revision)||(!snap&&d.event_id===overlayRef.current?.id)))render(d);
   const fib=snap?.event_overlays?.fibonacci;
   if(l.fib&&fib&&fib.status!=='invalidated'&&(fib.timeframe===frame||l.crossFrame)){
    for(const [i,a] of (fib.anchors||[]).entries())if(a.known_at<=snap!.as_of&&l.markers)add('fib-anchor:'+i,'icon',[{time:a.pivot_time/1000,price:Number(a.price)}],{color:'#8d78ac',size:6});
    for(const [i,a] of (fib.levels||[]).entries())if(a.known_at<=snap!.as_of){segment('fib-level:'+i,a.known_at/1000,currentEnd,Number(a.price),'#8d78ac',true);label('fib-label:'+i,'Fib '+a.ratio,Number(a.price),'#8d78ac');}
   }
   // Labels use the visible price scale. Displacement changes text only; leaders point to true prices.
   const pane=chart.getPanes()[0],priceRange=pane?.getMainSourcePriceScale()?.getVisiblePriceRange();
   const min=priceRange?.from??Math.min(...labelsToDraw.map(v=>v.price)),max=priceRange?.to??Math.max(...labelsToDraw.map(v=>v.price));
   const spacing=(max-min)/Math.max(1,(pane?.getHeight()||500)-30)*22;
   const visible=labelsToDraw.filter(p=>p.price>=min&&p.price<=max).sort((a,b)=>a.price-b.price).slice(0,opts.detailed?14:8);
   let previous=-Infinity;
   // Reserve measured text width inside the plot, including narrow-price labels.
   // Time-based padding alone clips long labels when many candles are visible.
   const measure=document.createElement('canvas').getContext('2d');if(measure)measure.font='11px Arial';
   const longest=Math.max(0,...visible.map(p=>measure?.measureText(p.text+' '+priceText(p.price,priceTick)).width||180));
   const plotWidth=Math.max(200,(root.current?.clientWidth||1000)-135);
   const textSpace=Math.min(.65,Math.max(.18,(longest+30)/plotWidth));
   const labelTime=view.to-(view.to-view.from)*textSpace;
   for(const p of visible){const textPrice=Math.max(p.price,previous+spacing);if(textPrice>max)continue;previous=textPrice;
    add(p.key+':label','text',[{time:labelTime,price:textPrice}],{color:p.color,fontsize:11,bold:false},p.text+' '+priceText(p.price,priceTick));
    add(p.key+':leader','trend_line',[{time:labelTime-step*2,price:p.price},{time:labelTime,price:textPrice}],{linecolor:p.color,linewidth:1,linestyle:2});
   }
   for(const [key,entry] of shapeCache.current)if(!desired.has(key)){try{chart.removeEntity(entry.id)}catch{}shapeCache.current.delete(key);}
   for(const [key,shape] of desired){
    if(seq!==drawSeq.current)return;
    const signature=JSON.stringify(shape),old=shapeCache.current.get(key);if(old?.signature===signature)continue;
    if(old&&old.kind===shape.options.shape){try{const tool=chart.getShapeById(old.id);tool.setPoints(shape.points);tool.setProperties({...shape.options.overrides as Record<string,unknown>,text:shape.options.text});old.signature=signature;continue;}catch{try{chart.removeEntity(old.id)}catch{}shapeCache.current.delete(key);}}
    const id=await (shape.points.length===1?chart.createShape(shape.points[0],shape.options):chart.createMultipointShape(shape.points as {time:number;price:number}[],shape.options));
    if(id){if(seq!==drawSeq.current){try{chart.removeEntity(id)}catch{}return;}shapeCache.current.set(key,{id,signature,kind:String(shape.options.shape)});}
   }
   shapes.current=[...shapeCache.current.values()].map(v=>v.id);
   if(seq===drawSeq.current){setLayerInfo(levels);setRangeMessage(crossHidden?'所选结构来自 '+(labels[d?.timeframe||'']||d?.timeframe)+'，可返回来源周期或开启周期参考':missingRange?'当前周期压缩了结构区间，请查看来源周期':'');}
  }catch(e){if(seq===drawSeq.current)setError('图层更新失败：'+(e as Error).message);}
 },[instrument,snapshotId,priceTick]);
 useEffect(()=>{
  let live=true,pending=false;const ctrl=new AbortController();
  setCandleAsOf(null);setAnalysis(undefined);setCandidateDrawing(null);setCandidateAudit(null);setDrawingMessage('');setRangeMessage('');setSnapshot(null);setDerivatives(undefined);overlayRef.current=null;analysisRef.current=undefined;snapshotRef.current=null;
  const load=async()=>{
   if(pending)return;pending=true;
   try{
    const result=await api<{snapshot:DecisionSnapshot|null}>("/chart/snapshot?instrument="+encodeURIComponent(instrument)+(episode?"&episode="+encodeURIComponent(episode):"")+(snapshotId?"&snapshot_id="+encodeURIComponent(snapshotId):""),{signal:ctrl.signal});
    if(!live)return;const snap=result.snapshot;
    if(snap&&(snap.instrument_id!==instrument||snapshotId&&snap.id!==snapshotId||episode&&snap.episode_id!==episode))throw Error("快照身份不匹配");
    const suffix=snapshotId&&snap?"&snapshot_id="+encodeURIComponent(snap.id):"";
    const frame=resolutionRef.current;
    const out=await Promise.allSettled([
     api<Analysis>("/analysis?instrument="+encodeURIComponent(instrument)+suffix,{signal:ctrl.signal}),
     api<DerivativeHistory>("/derivatives/history?instrument="+encodeURIComponent(instrument)+suffix,{signal:ctrl.signal}),
     !snap&&episode?api<StructureOverlay>("/structure-signals/"+encodeURIComponent(episode),{signal:ctrl.signal}):Promise.resolve(null),
     !snapshotId&&["60","240","1D","1W"].includes(frame)?api<{rows:NonNullable<Analysis['timeframes'][number]['series']>;series_revision:string;status:string}>("/analysis/series?instrument="+encodeURIComponent(instrument)+"&resolution="+frame+"&limit=1500",{signal:ctrl.signal}):Promise.resolve(null)
    ]);
    if(!live)return;
    setError("");
    snapshotRef.current=snap;setSnapshot(snap);onSnapshotRef.current?.(snap);
    if(snap&&defaultsEvent.current!==snap.episode_id){defaultsEvent.current=snap.episode_id;const required=snap.drawing_contract?.required_indicators||[];
     applyDefaults(snap.drawing_contract);}
    if(out[0].status==="fulfilled"){
     // A live technical assessment uses a bounded strategy window. Its EMA
     // seed must never replace the canonical paged chart series on refresh.
     // Frozen snapshots retain precisely their own original indicator inputs.
     const previous=analysisRef.current;const segment=out[3].status==="fulfilled"?out[3].value:null;
     const value=snapshotId?out[0].value:{...out[0].value,timeframes:out[0].value.timeframes.map(a=>{
      const old=previous?.timeframes.find(v=>v.timeframe===a.timeframe) as (typeof a & {indicator_series_revision?:string})|undefined;
      if(a.timeframe===frame){
       const points=new Map((segment&&old?.indicator_series_revision===segment.series_revision?old.series||[]:[]).map(p=>[p.time,p]));
       for(const p of segment?.rows||[])points.set(p.time,p);
       return {...a,indicator_series_revision:segment?.series_revision,series:[...points.values()].sort((x,y)=>x.time-y.time)};
      }
      return {...a,indicator_series_revision:old?.indicator_series_revision,series:old?.indicator_series_revision?old.series:[]};
     })};
     analysisRef.current=value;setAnalysis(value);
    }
    if(out[1].status==="fulfilled")setDerivatives(out[1].value);
    overlayRef.current=snap?{id:snap.episode_id,phase_label:snap.phase_label,first_seen_at:snap.first_seen_at,first_seen_price:snap.first_seen_price,evidence:snap.evidence}:out[2].status==="fulfilled"&&out[2].value?.evidence.instrument_id===instrument?out[2].value:null;
    setCandidateDrawing(!snap?overlayRef.current?.drawing_contract||null:null);
    const activeDrawing=snap?.drawing_contract||overlayRef.current?.drawing_contract;
    if(activeDrawing&&defaultsEvent.current!==activeDrawing.event_id){defaultsEvent.current=activeDrawing.event_id;applyDefaults(activeDrawing);}
    setCandidateAudit(!snap?overlayRef.current?.candidate_quality||null:null);
    if(!snap&&overlayRef.current?.candidate_quality?.status!=='verified')setDrawingMessage(overlayRef.current?.candidate_quality?.reason||'');
    for(const id of backendIds.current){try{widgetRef.current?.activeChart().getStudyById(id).setInputValues([{id:"revision",value:analysisRef.current?.as_of||0}])}catch{}}
    const rejected=out.find(r=>r.status==="rejected");
    if(rejected?.status==="rejected"&&!ctrl.signal.aborted)setError((rejected.reason as Error).message);
   }catch(e){if(live&&!ctrl.signal.aborted)setError((e as Error).message)}finally{pending=false;}
   await draw();
  };
  void load();
  const stream=snapshotId?null:new EventSource(appPath("/v1/stream"));
  let refreshTimer:ReturnType<typeof setTimeout>|undefined;
  const refresh=(event:Event)=>{
   if(event.type!=="reset"){
    let value:{instrument_id?:string;asset_id?:string;episode_id?:string};
    try{value=JSON.parse((event as MessageEvent).data)}catch{return;}
    if(value.instrument_id&&value.instrument_id!==instrument)return;
    if(value.asset_id&&snapshotRef.current?.market.asset_id&&value.asset_id!==snapshotRef.current.market.asset_id)return;
    if(value.episode_id&&episode&&value.episode_id!==episode)return;
    if(event.type==="candle_revision")for(const reset of candleResets.current.values())reset();
   }
   if(!refreshTimer)refreshTimer=setTimeout(()=>{refreshTimer=undefined;void load();},5000);
  };
  for(const name of ["signal_update","candle_revision","reset"])stream?.addEventListener(name,refresh);
  const timer=snapshotId?undefined:setInterval(()=>void load(),60000);
  return()=>{live=false;ctrl.abort();stream?.close();if(timer)clearInterval(timer);if(refreshTimer)clearTimeout(refreshTimer)};
 },[instrument,episode,snapshotId,draw]);
 useEffect(()=>{
  let disposed=false;let widget:ChartWidget|undefined;const timers=new Map<string,ReturnType<typeof setInterval>>();
  loadedTimes.current.clear();
  const resets=candleResets.current;const owner={};
  const feed={
   onReady:(cb:(x:unknown)=>void)=>setTimeout(()=>cb({supported_resolutions:resolutions,supports_time:false}),0),
   searchSymbols:(_q:string,_ex:string,_t:string,cb:(x:unknown[])=>void)=>cb([]),
   resolveSymbol:(_s:string,cb:(x:Info)=>void,fail:(s:string)=>void)=>{
    api<{rows:{id:string;native_symbol:string;venue:string;spec:{price_precision?:number;sz_decimals?:number;tick_size?:string}}[]}>("/instruments").then(data=>{
     const spec=data.rows.find(x=>x.id===instrument);if(!spec)throw Error("市场规格尚未就绪");
     const tick=spec.spec.tick_size;
     const tickDecimals=tick&&/^\d+(?:\.\d+)?$/.test(tick)&&Number(tick)>0?(tick.replace(/0+$/,"").split(".")[1]||"").length:null;
     const decimals=tickDecimals??spec.spec.price_precision??Math.max(0,6-(spec.spec.sz_decimals??2));
     if(!disposed)setPriceTick(tick||null);
     const minmov=tickDecimals!=null&&tickDecimals<=12?Math.max(1,Math.round(Number(tick)*10**tickDecimals)):1;
     setTimeout(()=>{if(!disposed)cb({ticker:instrument,name:spec.native_symbol,description:spec.native_symbol+" · "+spec.venue,type:"crypto",session:"24x7",timezone:"Etc/UTC",exchange:spec.venue,minmov,pricescale:10**Math.min(decimals,12),has_intraday:true,has_weekly_and_monthly:true,supported_resolutions:resolutions,volume_precision:4,data_status:"streaming"})},0);
    }).catch(e=>{if(!disposed)fail(e.message)});
   },
   getBars:(_i:Info,r:string,period:{from:number;to:number;countBack:number},cb:(b:Bar[],meta:{noData:boolean})=>void,fail:(m:string)=>void)=>{
    api<CandleList>("/candles?instrument="+encodeURIComponent(instrument)+"&resolution="+r+"&before="+period.to*1000+"&limit="+Math.min(1500,Math.max(300,period.countBack))+(snapshotId?"&snapshot_id="+encodeURIComponent(snapshotId):"")).then(result=>{
     if(!disposed){
      if(!snapshotId&&result.rows.length)setCandleAsOf(old=>Math.max(old||0,...result.rows.map(v=>v.received_at)));
      const times=loadedTimes.current.get(r)||new Set<number>();for(const bar of result.rows)times.add(bar.open_time);loadedTimes.current.set(r,times);
      setTimeout(()=>{if(disposed)return;cb(result.rows.map(toBar),{noData:result.rows.length===0});
       setTimeout(()=>{const chart=widgetRef.current?.activeChart();if(!disposed&&chart?.dataReady(()=>void draw()))void draw();},0);
      },0);
     }
     if(!disposed&&["60","240","1D","1W"].includes(r)){
      const frozen=snapshotId;
      void api<{rows:NonNullable<Analysis['timeframes'][number]['series']>;instrument_id:string;timeframe:string;snapshot_id?:string;series_revision:string;status:string;reason?:string;coverage_start?:number}>("/analysis/series?instrument="+encodeURIComponent(instrument)+"&resolution="+r+"&before="+period.to*1000+"&limit="+Math.min(1500,Math.max(300,period.countBack))+(frozen?"&snapshot_id="+encodeURIComponent(frozen):"")).then(segment=>{
       if(disposed||segment.instrument_id!==instrument||segment.timeframe!==r||snapshotId!==frozen)return;
       if(frozen&&segment.series_revision!==snapshotRef.current?.series_revision)return;
       // An exhausted older page has no replacement data. Keep the current curve.
       if(!segment.rows.length){
        if(segment.reason)setSeriesCoverage(segment.reason);
        if(['offline_rebuild_required','offline_segment_missing','coverage_limit'].includes(segment.status)&&analysisRef.current){
         const cleared={...analysisRef.current,timeframes:analysisRef.current.timeframes.map(a=>a.timeframe===r?{...a,series:[]}:a)};
         analysisRef.current=cleared;setAnalysis(cleared);
         for(const id of backendIds.current){try{widgetRef.current?.activeChart().getStudyById(id).setInputValues([{id:'revision',value:Date.now()}])}catch{}}
        }
        return;
       }
       const current=analysisRef.current;if(!current)return;
       const next={...current,timeframes:current.timeframes.map(a=>{
        if(a.timeframe!==r)return a;
        const sameRevision=frozen||(a as typeof a & {indicator_series_revision?:string}).indicator_series_revision===segment.series_revision;
        const points=new Map((sameRevision?a.series||[]:[]).map(p=>[p.time,p]));
        for(const point of segment.rows)points.set(point.time,{...points.get(point.time),...point});
        return {...a,indicator_series_revision:segment.series_revision,series:[...points.values()].sort((a,b)=>a.time-b.time)};
       })};
       analysisRef.current=next;setAnalysis(next);setSeriesCoverage(segment.reason|| (segment.coverage_start?"指标覆盖起点 "+clock(segment.coverage_start):"暂无指标历史"));
       for(const id of backendIds.current){try{widgetRef.current?.activeChart().getStudyById(id).setInputValues([{id:"revision",value:Date.now()}])}catch{}}
      }).catch(e=>{if(!disposed)setSeriesCoverage("指标历史读取失败："+e.message)});
     }
    }).catch(e=>{if(!disposed){setError(e.message);fail(e.message)}});
   },
   subscribeBars:(_i:Info,r:string,cb:(b:Bar)=>void,id:string,reset:()=>void)=>{
    if(snapshotId)return;
    let pending=false,last=0;resets.set(id,reset);
    timers.set(id,setInterval(async()=>{if(pending||disposed)return;pending=true;try{
     const result=await api<CandleList>("/candles?instrument="+encodeURIComponent(instrument)+"&resolution="+r+"&limit=2");
     const b=result.rows.at(-1);if(b&&!disposed&&b.open_time>=last){last=b.open_time;setCandleAsOf(b.received_at);cb(toBar(b));}
    }catch{/* visible diagnostics are supplied by the API panel */}finally{pending=false}},30000));
   },
   unsubscribeBars:(id:string)=>{const t=timers.get(id);if(t)clearInterval(t);timers.delete(id);resets.delete(id);}
  };
  async function start(){
   try{
    await loadLibrary();if(disposed||!root.current||!window.TradingView)return;
    const saved=await api<{payload:{resolution?:string;resolutionExplicit?:boolean;preset?:"technical"|"derivatives";layers?:Partial<typeof layers>;explicit?:Partial<typeof layers>;style?:Partial<typeof style>}}>("/preferences/chart").catch(()=>{setDrawingMessage("图表设置读取失败，已使用默认视图。");return {payload:{} as {resolution?:string;resolutionExplicit?:boolean;preset?:"technical"|"derivatives";layers?:Partial<typeof layers>;explicit?:Partial<typeof layers>;style?:Partial<typeof style>}}});
    if(disposed)return;
    overridesRef.current=saved.payload.explicit||{};resolutionExplicit.current=Boolean(saved.payload.resolutionExplicit);
    if(saved.payload.preset)setPreset(saved.payload.preset);
    if(saved.payload.layers)setLayers(v=>({...v,...saved.payload.layers,...overridesRef.current}));
    if(saved.payload.style){styleRef.current={...defaultStyle,...saved.payload.style};setStyle(styleRef.current);}
    let initialSnapshot=snapshotRef.current;
    if(snapshotId&&!initialSnapshot){const frozen=await api<{snapshot:DecisionSnapshot|null}>("/chart/snapshot?instrument="+encodeURIComponent(instrument)+"&snapshot_id="+encodeURIComponent(snapshotId));if(disposed)return;initialSnapshot=frozen.snapshot;}
    let initialCandidate:StructureOverlay|null=null;
    if(episode&&!initialSnapshot){initialCandidate=await api<StructureOverlay>("/structure-signals/"+encodeURIComponent(episode)).catch(()=>null);if(disposed)return;}
    const initial=resolutionExplicit.current&&saved.payload.resolution?saved.payload.resolution:episode?(initialSnapshot?.drawing_contract?.timeframe||initialCandidate?.drawing_contract?.timeframe||initialSnapshot?.evidence.trigger?.resolution||"240"):saved.payload.resolution||"240";setResolution(initial);
    applyDefaults(initialSnapshot?.drawing_contract||initialCandidate?.drawing_contract);
    widget=new window.TradingView.widget({container:root.current,library_path:appPath("/charting_library/"),datafeed:feed,symbol:instrument,interval:initial,
      custom_indicators_getter:(PineJS:Parameters<typeof backendStudies>[0])=>backendStudies(PineJS,()=>analysisRef.current),
      locale:"zh",timezone:"Asia/Shanghai",theme:styleRef.current.theme,autosize:true,enabled_features:["iframe_loading_same_origin","saveload_separate_drawings_storage"],
      disabled_features:["create_volume_indicator_by_default","header_symbol_search","symbol_search_hot_key","header_compare","use_localstorage_for_settings"],
      overrides:{"mainSeriesProperties.candleStyle.upColor":"#15967b","mainSeriesProperties.candleStyle.downColor":"#df5b67","mainSeriesProperties.candleStyle.wickUpColor":"#15967b","mainSeriesProperties.candleStyle.wickDownColor":"#df5b67","paneProperties.backgroundType":"solid","paneProperties.background":styleRef.current.theme==="dark"?"#131d29":"#ffffff","scalesProperties.textColor":styleRef.current.theme==="dark"?"#c4d2df":"#536879","paneProperties.vertGridProperties.color":styleRef.current.theme==="dark"?"#24303d":"#edf1f4","paneProperties.horzGridProperties.color":styleRef.current.theme==="dark"?"#24303d":"#edf1f4"}});
    await widget.chartReady();if(disposed)return;widgetRef.current=widget;const chart=widget.activeChart();
    chart.onIntervalChanged().subscribe(owner,(r)=>{++drawSeq.current;for(const id of shapes.current){try{chart.removeEntity(id)}catch{}}shapes.current=[];shapeCache.current.clear();setResolution(r);resolutionRef.current=r;resolutionExplicit.current=true;void api("/preferences/chart",{method:"PUT",body:JSON.stringify({payload:{resolution:r,resolutionExplicit:resolutionExplicit.current,preset,layers:layersRef.current,explicit:overridesRef.current,style:styleRef.current}})}).catch(()=>setDrawingMessage("图表设置保存失败，K线仍可查看，请稍后重试。"));if(chart.dataReady(()=>void draw()))void draw();});
    chart.onVisibleRangeChanged().subscribe(owner,(r)=>{setRange(r);void draw();});
    chart.crossHairMoved().subscribe(owner,({time})=>{if(Number.isFinite(time))setHoverTime(time*1000)});
    const studies:string[]=[];
    if(!chart.getAllStudies().some(x=>/volume|成交量/i.test(x.name)))await chart.createStudy("Volume",false,false);
    studies.push("成交量");
    setStudyState(studies);
    await new Promise<void>(resolve=>{if(chart.dataReady(resolve))resolve()});
    if(!disposed&&episode){
     const frame=chart.resolution(),step=frame==="1W"?604800:frame==="1D"?86400:Number(frame)*60;
     const until=(initialSnapshot?.as_of||Date.now())/1000;
     await chart.setVisibleRange({from:Math.max(until-step*500,Math.min(until-step*100,(initialSnapshot?.drawing_contract?.coverage_start||overlayRef.current?.drawing_contract?.coverage_start||until*1000)/1000-step*5)),to:until+step*15});
    }
    if(!disposed){setRange(chart.getVisibleRange());await draw();setLoading(false);
     const savedDrawings=await api<{payload:{sources?:[string,unknown][];groups?:[string,unknown][];symbol?:string}}>("/preferences/"+await drawingKey(instrument)).catch(()=>{setDrawingMessage("保存的绘图暂时无法读取，K线仍可查看。");return {payload:{} as {sources?:[string,unknown][];groups?:[string,unknown][];symbol?:string}}});
     if(!disposed&&savedDrawings.payload.sources)await chart.applyLineToolsState({sources:new Map(savedDrawings.payload.sources),groups:new Map(savedDrawings.payload.groups||[]),symbol:savedDrawings.payload.symbol});
    }
   }catch(e){if(!disposed){setError((e as Error).message);setLoading(false);}}
  }
  setError("");setLoading(true);setStudyState([]);void start();
  return()=>{disposed=true;drawSeq.current++;widgetRef.current=undefined;shapes.current=[];shapeCache.current.clear();historyRef.current=[];backendIds.current=[];auxiliaryIds.current={};resets.clear();for(const timer of timers.values())clearInterval(timer);if(widget){try{widget.activeChart().onIntervalChanged().unsubscribeAll(owner);widget.activeChart().onVisibleRangeChanged().unsubscribeAll(owner);widget.activeChart().crossHairMoved().unsubscribeAll(owner)}catch{}widget.remove();}};
 },[instrument,episode,snapshotId,draw]);
 useEffect(()=>{void draw();const chart=widgetRef.current?.activeChart();if(!chart)return;
  if(studyState.length)void api("/preferences/chart",{method:"PUT",body:JSON.stringify({payload:{resolution,resolutionExplicit:resolutionExplicit.current,preset,layers,explicit:overridesRef.current,style}})}).catch(()=>setDrawingMessage("图表设置保存失败，K线仍可查看，请稍后重试。"));
 },[layers,preset,style,studyState,resolution,draw]);
 useEffect(()=>{
  const chart=widgetRef.current?.activeChart();if(!chart)return;
  let cancelled=false;
  const definitions=[["ema","EMA 20 / 50 · 闭合K线",true],["macd","MACD 12 / 26 / 9 · 闭合K线",false],["bb","ERJI 布林带",true],["avwap","ERJI 事件锚定均价",true],["rsi","ERJI RSI 14",false],["adx","ERJI ADX 14",false]] as const;
  void (async()=>{for(const [key,name,overlay] of definitions){
   let id=auxiliaryIds.current[key];
   const eventFrame=snapshotRef.current?.drawing_contract?.timeframe||overlayRef.current?.drawing_contract?.timeframe;
   const enabled=preset==="technical"&&layers[key]&&(overridesRef.current[key]===true||!eventFrame||eventFrame===resolution||layers.crossFrame);
   if(id&&!enabled){chart.removeEntity(id);delete auxiliaryIds.current[key];backendIds.current=backendIds.current.filter(value=>value!==id);continue;}
   if(!id&&enabled){const created=await chart.createStudy(name,overlay,true);if(created){if(cancelled){chart.removeEntity(created);return;}id=created;auxiliaryIds.current[key]=id;backendIds.current.push(id);}}
  }})().catch(e=>{if(!cancelled)setError((e as Error).message)});
  return()=>{cancelled=true;};
 },[layers,preset,studyState,resolution]);
 useEffect(()=>{if(!studyState.length)return;const timer=setInterval(()=>void draw(),3000);return()=>clearInterval(timer);},[studyState,draw]);
 useEffect(()=>{if(studyState.length)void widgetRef.current?.changeTheme(style.theme).then(()=>draw());},[style.theme,studyState,draw]);
 useEffect(()=>{if(!layers.history){historyRef.current=[];void draw();return;}const controller=new AbortController();void api<{rows:DrawingContract[]}>("/chart/structures?instrument="+encodeURIComponent(instrument)+(snapshotId&&snapshot?.as_of?"&as_of="+snapshot.as_of:""),{signal:controller.signal}).then(r=>{historyRef.current=r.rows;void draw();}).catch(()=>{});return()=>controller.abort();},[layers.history,instrument,snapshotId,draw]);
 async function locate(mode:"first"|"current"|"targets"|"structure"){
  const chart=widgetRef.current?.activeChart();if(!chart)return;const source=overlayRef.current;const duration=resolution==="1W"?604800:resolution==="1D"?86400:Number(resolution)*60;
  const end=mode==="first"&&source?source.first_seen_at/1000:(snapshotRef.current?.as_of||Date.now())/1000;
  if(mode==="targets"){
   setLayers(value=>({...value,allTargets:true}));
   const plan=snapshotRef.current?.opportunity_plan;
   const values=[...layerInfo.flatMap(l=>[Number(l.low),Number(l.high)]),...[plan?.t1,plan?.daily,plan?.weekly].flatMap(level=>level?[Number(level.low),Number(level.high)]:[])].filter(n=>Number.isFinite(n)&&n>0);
   if(values.length){const scale=chart.getPanes()[0]?.getMainSourcePriceScale();scale?.setAutoScale(false);scale?.setVisiblePriceRange({from:Math.min(...values)*.95,to:Math.max(...values)*1.05});}
  }else{setLayers(value=>({...value,allTargets:false}));chart.getPanes()[0]?.getMainSourcePriceScale()?.setAutoScale(true);}
  await chart.setVisibleRange({from:(mode==="first"||mode==="structure")&&source?Math.max(end-duration*500,(snapshotRef.current?.drawing_contract?.coverage_start||source.drawing_contract?.coverage_start||source.first_seen_at)/1000-duration*5):end-duration*100,to:end+duration*15});
 }
 async function saveDrawings(){
  try{const chart=widgetRef.current?.activeChart();if(!chart)return;const state=chart.getLineToolsState();
   const sources=[...(state.sources||new Map()).entries()].filter(([id])=>!shapes.current.includes(id));
   await api("/preferences/"+await drawingKey(instrument),{method:"PUT",body:JSON.stringify({payload:{sources,groups:[...state.groups.entries()],symbol:state.symbol,instrument_id:instrument}})});setDrawingMessage("手工绘图已按当前市场保存");
  }catch{setDrawingMessage("绘图保存失败，请稍后重试。当前图表仍可查看。")}
 }
 const assessments=snapshotId?snapshot?.timeframe_assessments:analysis?.timeframes;
 return <section className="pd2-chart">
  <div className="chart-decision"><b>{snapshot?.action.label||(candidateAudit?.status==='rejected'?'该候选已撤销':candidateDrawing?"等待"+(strategyNames[candidateDrawing.strategy_id||""]||"结构确认"):"当前技术结构")}</b><span>{snapshot?.action.reason||(candidateAudit?.status==='rejected'?'原始记录已保留，等待新的有效结构':candidateDrawing?"当前显示候选结构，尚未形成有效买入计划":"选择事件查看结构与参与计划")}</span>{snapshot&&<small>{snapshotId?"历史冻结快照":"当前快照"} · {clock(snapshot.as_of)}</small>}</div>
  <div className="timeframe-matrix">{assessments?.map(a=><div key={a.timeframe}><b>{labels[a.timeframe]||a.timeframe}</b><span>{direction[a.direction]}</span><span>{momentum[a.momentum]}{a.macd_cross_confirmed?" · 新金叉已确认":""}</span><small>{a.status==="fresh"?"收盘 "+clock(a.closed_at):a.reason}</small></div>)}</div>
  <div className="chart-toolbar"><div role="group" aria-label="图表视图"><button className={preset==="technical"?"active":""} onClick={()=>setPreset("technical")}>技术分析</button><button className={preset==="derivatives"?"active":""} onClick={()=>setPreset("derivatives")}>合约分析</button></div>
   <details className="chart-layer-menu"><summary>图表设置</summary><div><b>显示内容</b>{([["structure","当前策略结构"],["levels","关键价位"],["plan","参与与保护"],["markers","事件标记"],["history","历史结构（最多3个）"],["targets","第一目标"],["ema","EMA20/50"],["macd","MACD12/26/9"],["bb","布林带20/2"],["avwap","事件AVWAP"],["rsi","RSI14"],["adx","ADX14"],["fib","事件斐波参考"]] as const).filter(([key])=>{
    const fields:Record<string,string>={ema:"ema20",macd:"macd",bb:"bb_middle",avwap:"event_avwap",rsi:"rsi14",adx:"adx14"};
    if(key==="fib")return Boolean(snapshot?.event_overlays?.fibonacci?.levels.length);
    return !fields[key]||Boolean(analysis?.timeframes.find(a=>a.timeframe===resolution)?.series?.some(point=>point[fields[key]]!=null));
   }).map(([key,label])=><label key={key}><input type="checkbox" checked={layers[key]} onChange={e=>setLayer(key,e.target.checked)}/>{label}</label>)}<hr/><b>样式</b><label>线宽<select value={style.width} onChange={e=>setStyle({...style,width:Number(e.target.value)})}>{[1,2,3].map(v=><option key={v}>{v}</option>)}</select></label><label>区域透明度<input type="range" min="80" max="98" value={style.transparency} onChange={e=>setStyle({...style,transparency:Number(e.target.value)})}/></label><label><input type="checkbox" checked={style.detailed} onChange={e=>setStyle({...style,detailed:e.target.checked})}/>详细标签</label>{([['resistance','阻力 / 保护'],['support','支撑 / 参与'],['structure','结构']] as const).map(([key,name])=><label key={key}>{name}<input type="color" value={style[key]} onChange={e=>setStyle({...style,[key]:e.target.value})}/></label>)}<label>主题<select value={style.theme} onChange={e=>setStyle({...style,theme:e.target.value})}><option value="light">浅色</option><option value="dark">深色</option></select></label><hr/><b>周期参考</b><label><input type="checkbox" checked={layers.crossFrame} onChange={e=>setLayer('crossFrame',e.target.checked)}/>叠加所选事件来源周期</label><button onClick={()=>{overridesRef.current={};resolutionExplicit.current=false;setLayers(defaultLayers);setStyle(defaultStyle);applyDefaults(snapshotRef.current?.drawing_contract||overlayRef.current?.drawing_contract);}}>恢复默认</button></div></details>
   {episode&&<button onClick={()=>void locate("first")}>首次信号</button>}<button onClick={()=>void locate("structure")}>当前结构</button><button onClick={()=>void locate("current")}>最新行情</button>{(snapshot?.drawing_contract||candidateDrawing)?.timeframe!==resolution&&(snapshot?.drawing_contract||candidateDrawing)?.timeframe&&<button onClick={()=>void widgetRef.current?.activeChart().setResolution((snapshot?.drawing_contract||candidateDrawing)!.timeframe!)}>返回来源周期</button>}<button onClick={()=>void locate("targets")}>查看全部目标</button><button onClick={()=>void saveDrawings()}>保存我的绘图</button><small>{rangeMessage}</small><small role="status">{drawingMessage}</small>
  </div>
  <div className="chart-container">{loading&&!error&&<div className="chart-loading">正在加载K线及默认指标…</div>}{error&&<div className="chart-error">{error}</div>}<div ref={root} className="chart-root"/></div>
   <div className="chart-caption">{snapshotId&&snapshot?.action.reason?.includes("发布门槛")?"旧版冻结记录（保留原始说明） · ":""}{snapshot?.drawing_contract?.status==="confirmed"?"当前主策略："+(snapshot.strategy_label||strategyNames[snapshot.drawing_contract.strategy_id||""]||"结构待核验")+" · 来源"+(labels[snapshot.drawing_contract.timeframe||""]||"未知")+" · 确认于 "+clock(snapshot.drawing_contract.closed_at):candidateDrawing?(strategyNames[candidateDrawing.strategy_id||""]||"候选结构")+" · 来源 "+(labels[candidateDrawing.timeframe||""]||candidateDrawing.timeframe)+" · 尚未触发":"当前无已确认策略结构"} · {snapshotId?"历史快照":"实时追踪"} · 北京时间</div>
   {!snapshotId&&<small className="chart-caption">最近行情观测：{clock(candleAsOf)}{candleAsOf&&Date.now()-candleAsOf>180000?" · 已延迟":""}</small>}
   {seriesCoverage&&<small className="chart-caption">{seriesCoverage}</small>}
   <small className="chart-caption">指标覆盖：{clock(analysis?.timeframes.find(a=>a.timeframe===resolution)?.series?.[0]?.time)} — {clock(analysis?.timeframes.find(a=>a.timeframe===resolution)?.closed_at)}</small>
  {preset==="derivatives"&&<div className="derivative-panels">
   {derivatives?.instrument_id&&<small className="chart-caption">合约来源 · {derivatives.instrument_id}</small>}
   <HistoryPanel title={"数量 OI · "+(derivatives?.quantity_unit||"")} rows={(derivatives?.oi||[]).map(x=>({time:x.time,value:x.quantity}))} range={range} hoverTime={hoverTime} onHover={setHoverTime} status={derivatives?.oi_status}/>
   <HistoryPanel title={"OI 名义金额 · "+(derivatives?.quote_asset||"")} rows={(derivatives?.oi||[]).map(x=>({time:x.time,value:x.notional_quote}))} range={range} hoverTime={hoverTime} onHover={setHoverTime} status={derivatives?.oi_status}/>
   <HistoryPanel title="实际结算资金费率 · %" rows={(derivatives?.funding||[]).map(x=>({time:x.time,value:String(Number(x.rate)*100)}))} range={range} hoverTime={hoverTime} onHover={setHoverTime} status={derivatives?.funding_status}/>
   <p className="chart-caption">三个面板使用K线当前可见时间范围，同一光标时间对照。OI缺口不补零；结算费率不代表预测费率。鼠标在主K线上移动会同步三个副图的时间。</p>
  </div>}
  {preset==="derivatives"&&snapshot?.derivatives&&<section className="panel" style={{padding:16}}><b>清算证据 · ByKaranteli / 原生 / CMC</b><p>跨平台模型只在下表作参考，不能套成当前交易所的精确爆仓价。</p><div className="table-scroll"><table><thead><tr><th>证据类型</th><th>方向</th><th>价格 / 金额</th><th>有效性</th></tr></thead><tbody>{snapshot.derivatives.liquidation_model?.levels?.slice(0,30).map(l=><tr key={l.id}><td>供应商模型区</td><td>{l.liquidated_side==="short"?"潜在空头风险":"潜在多头风险"}</td><td>{money(l.price)}</td><td>{l.status==="fresh"?"当前参考（模型）":"已过期或身份不匹配，退出当前判断"}</td></tr>)}{snapshot.derivatives.liquidation_summary?.windows?.map(w=><tr key={w.period}><td>CMC已发生 · {w.period}</td><td>空头 / 多头</td><td>{money(w.short_usd)} / {money(w.long_usd)} USD</td><td>{w.status==="fresh"?"有效汇总，覆盖范围内":"过期或未返回"}</td></tr>)}</tbody></table></div><small>模型：{snapshot.derivatives.liquidation_model?.status}；HL样本：{snapshot.derivatives.visible_positions?.status}。缺失不填零，价格触及不等于实际清算。</small></section>}
  <details className="chart-level-details"><summary>图上点位及计算依据 · {layerInfo.length}</summary><div className="table-scroll"><table><thead><tr><th>含义</th><th>价格区</th><th>周期 / 方法</th><th>确认时间</th><th>计算依据</th></tr></thead><tbody>{layerInfo.map((l,i)=><tr key={l.id+":"+i}><td>{l.label}</td><td title={l.low+"–"+l.high}>{priceText(l.low,priceTick)}{l.low!==l.high?"–"+priceText(l.high,priceTick):""}</td><td>{labels[l.timeframe]} · {l.method}</td><td>{clock(l.known_at)}</td><td>{l.formula||(candidateDrawing?"已记录的候选结构点位":"同一决策快照的确认点位")}{l.price_multiple?" · 价格"+Number(l.price_multiple).toFixed(2)+"倍":""}</td></tr>)}</tbody></table></div>{!layerInfo.length&&<p>当前没有已确认的策略绘图锚点。</p>}</details>
 </section>
}
function HistoryPanel({title,rows,range,hoverTime,onHover,status}:{title:string;rows:{time:number;value:string|null}[];range?:{from:number;to:number};hoverTime?:number;onHover:(n:number)=>void;status?:string}){
 const points=rows.filter(x=>x.value!=null&&(!range||x.time>=range.from*1000&&x.time<=range.to*1000));
 const from=range?range.from*1000:points[0]?.time||0,to=range?range.to*1000:points.at(-1)?.time||1;
 const values=points.map(x=>Number(x.value));const lo=values.length?Math.min(...values):0,hi=values.length?Math.max(...values):1;
 const x=(t:number)=>((t-from)/Math.max(1,to-from))*1000,y=(v:number)=>85-(v-lo)/Math.max(1e-12,hi-lo)*65;
 const path=points.map((p,i)=>(i&&p.time-points[i-1].time<900000?"L":"M")+x(p.time).toFixed(2)+","+y(Number(p.value)).toFixed(2)).join(" ");
 const selected=hoverTime?points.filter(point=>point.time<=hoverTime).at(-1):points.at(-1);
 return <section className="history-panel"><header><b>{title}</b><span>{selected?money(selected.value)+" · "+clock(selected.time):"此可见范围暂无数据"} {status==="stale"?"（已过期）":""}</span></header>
  {points.length?<svg role="img" aria-label={title+"历史曲线"} viewBox="0 0 1000 105" preserveAspectRatio="none" onPointerMove={event=>{const r=event.currentTarget.getBoundingClientRect();onHover(from+(event.clientX-r.left)/r.width*(to-from));}}><path d={path} fill="none" stroke="#188b80" strokeWidth="2" vectorEffect="non-scaling-stroke"/>{points.map((p,i)=><circle key={i} cx={x(p.time)} cy={y(Number(p.value))} r="1.5" fill="#188b80"/>)}{hoverTime&&<line x1={x(hoverTime)} x2={x(hoverTime)} y1="0" y2="105" stroke="#97a9b8" strokeDasharray="4 4"/>}</svg>:<div className="empty compact">历史不足、未覆盖或当前范围无样本；不会填零。</div>}
 </section>
}

async function drawingKey(instrument:string){const bytes=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(instrument));return "chart-drawings:"+Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,"0")).join("");}
