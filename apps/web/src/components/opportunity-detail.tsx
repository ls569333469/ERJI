"use client";

import Link from "next/link";
import {appPath} from "@/lib/paths";
import dynamic from "next/dynamic";
import {useEffect,useMemo,useState,useRef} from "react";
import {api,clock,compact,money,pct,venues,intervalLabel} from "@/lib/api";
import {priceText,readableReason,strategyNames,opportunityAdvice} from "@/lib/opportunity-display";
import type {DecisionSnapshot} from "@/lib/pd2-types";
import type {Row} from "./opportunity-board";
import {CaseEvidence,type CaseMatches} from "./case-evidence";

const Chart=dynamic(()=>import("./chart").then(module=>module.Chart),{ssr:false});
type Tracking={target_first_observed_at?:Record<string,number>;first_invalidation_observed_at?:number;protection_history?:{price:string;observed_at:number}[]};
type Snapshot=DecisionSnapshot&{tracking?:Tracking;phase?:string;strategy_id?:string;strategy_version?:string;case_matches?:CaseMatches};
type HistoryRow={id:string;as_of:number;action:{code:string;label:string};phase?:string;phase_label?:string;price?:string|null;protection?:string|null;tracking:Tracking};
type Event={id:string;time:number;label:string;detail:string};
type OiFacts={native_reference?:Row["native_oi"];daily_oi_path?:{status:string;path:string|null;reason:string};cross_market_oi?:{total_oi_usd?:string|null;oi_cap_ratio?:string|null;scope?:string;status?:string;observed_at?:number;markets?:{venue:string;observed_at:number}[];changes?:{period:string;quantity_change_pct?:string|null;status?:string}[]};funding_reference?:{rate?:string|null;interval_seconds?:number|null;instrument_id?:string;settled_at?:number}};

const targetNames:Record<string,string>={t1:"第一目标",daily:"日线目标",weekly:"周线目标"};
const zone=(values:string[]|null|undefined,tick?:string|null)=>values?.length===2?values.map(value=>priceText(value,tick)).join("–"):"尚未形成";
const target=(value:{low:string;high:string}|null|undefined,tick?:string|null)=>value?value.low===value.high?priceText(value.low,tick):priceText(value.low,tick)+"–"+priceText(value.high,tick):"尚未形成";
const qualification=(value:{method?:string;status?:string}|null|undefined)=>!value?"":value.status==="conditional"||/projection|extension/.test(value.method||"")?"条件测算":"结构参考";

function events(rows:HistoryRow[],firstSeen:number):Event[]{
 const ordered=[...rows].sort((a,b)=>a.as_of-b.as_of);
 const result:Event[]=[{id:"first",time:firstSeen,label:"首次发现",detail:"首次进入本事件的信号追踪"}];
 let previous:HistoryRow|undefined;
 for(const item of ordered){
  if(item.phase!==previous?.phase&&item.phase){result.push({id:item.id+":phase",time:item.as_of,label:item.phase_label||"阶段变化",detail:item.action.label});}
  else if(item.action.code!==previous?.action.code){result.push({id:item.id+":action",time:item.as_of,label:item.action.label,detail:item.price?"快照价格 "+money(item.price):"动作状态变化"});}
  for(const [name,time] of Object.entries(item.tracking?.target_first_observed_at||{})){
   if(time&&!previous?.tracking?.target_first_observed_at?.[name])result.push({id:item.id+":target:"+name,time,label:(targetNames[name]||name)+"首次检测达标",detail:"按当时有效报价检测；不代表成交"});
  }
  const invalidated=item.tracking?.first_invalidation_observed_at;
  if(invalidated&&!previous?.tracking?.first_invalidation_observed_at)result.push({id:item.id+":invalid",time:invalidated,label:"首次检测失效",detail:"原事件停止新增买入"});
  const oldCount=previous?.tracking?.protection_history?.length||0;
  for(const adjustment of (item.tracking?.protection_history||[]).slice(oldCount)){
   if(previous)result.push({id:item.id+":protect:"+adjustment.observed_at,time:adjustment.observed_at,label:"保护调整",detail:"保护价 " + money(adjustment.price)});
  }
  previous=item;
 }
 return result.sort((a,b)=>b.time-a.time).slice(0,20);
}

export function OpportunityDetail({row}:{row:Row}){
 const [snapshot,setSnapshot]=useState<Snapshot|null>(null);
 const [history,setHistory]=useState<HistoryRow[]>([]);
 const [historyMessage,setHistoryMessage]=useState("");
 const [error,setError]=useState("");
 const [busy,setBusy]=useState(true);
 const [tab,setTab]=useState<"detail"|"chart"|"strategy">("detail");
 const [chartMounted,setChartMounted]=useState(false);
 // Quote updates must not reset the selected frozen decision or the user's chart.
 const cutoff=useRef(row.as_of||0);
 const price=(value:string|number|null|undefined)=>priceText(value,row.tick_size);
 useEffect(()=>{
  const controller=new AbortController();
  setSnapshot(null);setHistory([]);setHistoryMessage("");setBusy(true);setError("");
  const requests:Promise<unknown>[]=[api<{rows:HistoryRow[];truncated:boolean}>("/signals/"+encodeURIComponent(row.event_id)+"/snapshots?limit=500"+(row.snapshot_id?"&snapshot_id="+encodeURIComponent(row.snapshot_id):""),{signal:controller.signal,timeoutMs:5000})];
  if(row.snapshot_id)requests.push(api<{snapshot:Snapshot|null}>("/chart/snapshot?instrument="+encodeURIComponent(row.instrument_id)+"&snapshot_id="+encodeURIComponent(row.snapshot_id),{signal:controller.signal,timeoutMs:5000}));
  Promise.allSettled(requests).then(results=>{
   if(controller.signal.aborted)return;
   let actual:Snapshot|null=null;
   if(row.snapshot_id){
    const value=results[1];
    if(value?.status==="fulfilled"){
     actual=(value.value as {snapshot:Snapshot|null}).snapshot;
     if(actual?.id===row.snapshot_id&&actual.instrument_id===row.instrument_id&&actual.episode_id===row.event_id)setSnapshot(actual);
     else setError("快照身份不匹配，详情已停止显示");
    }else setError("冻结快照读取失败，请稍后重试");
   }
   if(results[0].status==="fulfilled"){
    const result=results[0].value as {rows:HistoryRow[];truncated:boolean};
    setHistory(result.rows.filter(item=>actual?item.as_of<=actual.as_of:!row.snapshot_id&&item.as_of<=cutoff.current));
    if(result.truncated)setHistoryMessage("仅展示该计划最近500份记录中的变化");
   }else setHistoryMessage("追踪记录读取失败，请收起后重试");
   setBusy(false);
  });
  return()=>controller.abort();
 },[row.event_id,row.instrument_id,row.snapshot_id]);
 const timeline=useMemo(()=>events(history,row.first_seen_at),[history,row.first_seen_at]);
 if(busy)return <div className="opportunity-detail-loading">正在读取同一事件的冻结快照与追踪记录…</div>;
 if(error)return <div className="opportunity-detail-loading error">{error}</div>;
 const evidence=snapshot?.evidence;
 const oiPath=(snapshot?.derivatives as unknown as OiFacts|undefined)?.daily_oi_path;
 const oi=(snapshot?.derivatives as unknown as OiFacts|undefined)?.cross_market_oi;
 const native=snapshot?(snapshot.derivatives as unknown as OiFacts|undefined)?.native_reference:row.native_oi;
 const funding=(snapshot?.derivatives as unknown as OiFacts|undefined)?.funding_reference;
 const plan=snapshot?.opportunity_plan;
 const market=snapshot?.market;
 const entry=snapshot?evidence?.entry_zone:row.entry_zone;
 const protection=snapshot?snapshot.current_protection:row.protection;
 const t1=snapshot?plan?.t1:row.targets.t1,daily=snapshot?plan?.daily:row.targets.daily,weekly=snapshot?plan?.weekly:row.targets.weekly;
 const oiValue=snapshot?oi?.total_oi_usd:row.oi_usd,oiRatio=snapshot?oi?.oi_cap_ratio:row.oi_cap_ratio;
 const fundingValue=snapshot?funding?.rate:row.funding_rate;
 const fundingInterval=snapshot?funding?.interval_seconds:row.funding_interval_seconds;
 const fundingInstrument=snapshot?funding?.instrument_id:row.funding_reference_instrument;
 const changes=snapshot?oi?.changes:row.oi_changes.map(item=>({...item,quantity_change_pct:item.value_pct}));
 const tfNames:Record<string,string>={"60":"1H","240":"4H","1D":"1D","1W":"1W"};
 const directions:Record<string,string>={up:"结构上行",down:"结构下行",mixed:"结构分歧",broken:"结构失效",unknown:"历史不足"};
 const strategyId=snapshot?.strategy_id||row.strategy_id;
 const chartUrl="/assets/"+encodeURIComponent(row.asset_id)+"?instrument="+encodeURIComponent(row.instrument_id)+"&episode="+encodeURIComponent(row.event_id)+(row.snapshot_id?"&snapshot="+encodeURIComponent(row.snapshot_id):"");
 return <div className="opportunity-detail">
  <header className="opportunity-detail-head"><div><b>{row.symbol} · {snapshot?.phase_label||row.phase_label}</b><span>{snapshot?"冻结决策 "+clock(snapshot.as_of):"当前无冻结决策快照"}</span></div><Link href={chartUrl}>打开完整K线与点位 →</Link></header>
  <div className="opportunity-detail-tabs" role="tablist" aria-label="机会详情视图">{([["detail","机会详情"],["chart","K线与指标"],["strategy","策略与案例"]] as const).map(([id,label])=><button key={id} role="tab" aria-selected={tab===id} onClick={()=>{setTab(id);if(id==="chart")setChartMounted(true)}}>{label}</button>)}</div>
  <div className="opportunity-detail-columns" hidden={tab!=="detail"}>
   <section className="opportunity-detail-plan"><h3>参与计划与合约</h3><dl>
    <div><dt>现在怎么做</dt><dd>{snapshot?snapshot.action.label:opportunityAdvice(row).action}</dd></div>
    <div><dt>{snapshot?"快照现价":"列表价格"}</dt><dd>{price(snapshot?market?.price:row.price)} {row.quote_asset}</dd></div>
    <div><dt>参与区</dt><dd>{zone(entry,row.tick_size)}</dd></div>
    <div><dt>有效保护</dt><dd>{protection?price(protection):"尚未形成"}{snapshot?.original_protection&&snapshot.original_protection!==protection?" · 原保护 "+price(snapshot.original_protection):""}</dd></div>
    {!snapshot&&row.protection_reference?.price&&!protection&&<div><dt title="触发前估算，不代表已生效保护">保护参考</dt><dd>{price(row.protection_reference.price)}（估算）</dd></div>}
    {!snapshot&&row.box?.trigger_price&&<div><dt>等待触发</dt><dd>4H 收盘高于 {price(row.box.trigger_price)}{row.trigger_requirements?.relative_volume_min?"，量能至少 "+Number(row.trigger_requirements.relative_volume_min).toFixed(2)+" 倍":"，并满足量能确认"}</dd></div>}
    {!snapshot&&row.invalidation?.price&&<div><dt>触发前失效</dt><dd>{tfNames[row.invalidation.timeframe]||row.invalidation.timeframe} 收盘跌破 {price(row.invalidation.price)}</dd></div>}
    <div><dt>第一目标</dt><dd>{target(t1,row.tick_size)}<small>{qualification(t1)}</small></dd></div>
    <div><dt>日线 / 周线</dt><dd>{target(daily,row.tick_size)} / {target(weekly,row.tick_size)}<small>{daily||weekly?"沿途阻力逐级确认后观察":"条件未形成"}</small></dd></div>
    <div><dt title="已核验市场的OI名义金额 / 同时刻流通市值，覆盖范围见来源">OI / 流通市值</dt><dd>{oiValue!=null?"$"+compact(oiValue):native?.notional_quote!=null?compact(native.notional_quote)+" "+native.quote_asset:!snapshot&&row.oi_notional_quote?compact(row.oi_notional_quote)+" "+row.oi_quote_asset:"OI 缺数据"} · {oiRatio!=null?(Number(oiRatio)*100).toFixed(1)+"%":"市值比例缺数据"}<small>{oi?.markets?.length?Array.from(new Set(oi.markets.map(item=>venues[item.venue]||item.venue))).join(" + "):native?.instrument_id?(venues[native.instrument_id.split(":")[0]]||native.instrument_id)+" · "+clock(native.observed_at)+(native.status==="stale"?"（已过期）":""):snapshot?"来源范围未记录":row.oi_scope==="binance_reference_contract"?"币安主参考合约":row.oi_scope==="verified_covered_markets_not_global"?"已核验市场合计":row.oi_scope}</small></dd></div>
    <div><dt title="数量变化与名义金额变化分开；缺端点不填零">数量 OI</dt><dd>{["1H","4H","1D"].map(period=><small key={period}>{period} {pct(changes?.find(item=>item.period===period)?.quantity_change_pct)}</small>)}</dd></div>
    <div><dt>资金费率</dt><dd>{fundingValue!=null?(Number(fundingValue)*100).toFixed(4)+"%":"缺数据"}{fundingInterval?" / "+intervalLabel(fundingInterval):""}<small>{fundingInstrument?venues[fundingInstrument.split(":")[0]]||fundingInstrument:""} {funding?.settled_at?clock(funding.settled_at):""}</small></dd></div>
    <div><dt>多周期状态</dt><dd>{(snapshot?snapshot.timeframe_assessments:row.timeframes).filter(item=>tfNames[item.timeframe]).map(item=><small key={item.timeframe}>{tfNames[item.timeframe]} {item.status==="fresh"?directions[item.direction]||"待确认":"数据不足或过期"}</small>)}</dd></div>
   </dl></section>
   <section className="opportunity-detail-evidence"><h3>判断依据与持续追踪</h3>
    {oiPath?.path&&<p title="按完整自然日价格与数量OI同窗判断，不代表净资金或确认轧空">{oiPath.path}</p>}
    <p>{readableReason(snapshot?.trade_thesis.headline||row.reason)}</p>
    {snapshot?.trade_thesis.support?.length?<div><b>已满足</b><ul>{snapshot.trade_thesis.support.map((item,index)=><li key={index}>{readableReason(item)}</li>)}</ul></div>:null}
    {snapshot?.trade_thesis.opposition?.length?<div><b>反证</b><ul>{snapshot.trade_thesis.opposition.map((item,index)=><li key={index}>{readableReason(item)}</li>)}</ul></div>:null}
    {snapshot?.trade_thesis.missing?.length?<div><b>缺数据</b><ul>{snapshot.trade_thesis.missing.map((item,index)=><li key={index}>{readableReason(item)}</li>)}</ul></div>:null}
    <div className="opportunity-timeline"><b>事件追踪</b><small>{historyMessage||(snapshot?"截至本条快照 · 北京时间":"候选观察 · 北京时间")}</small>{timeline.length?timeline.map(event=><div key={event.id}><time>{clock(event.time)}</time><strong>{event.label}</strong><span>{event.detail}</span></div>):<p>尚无追踪快照。</p>}</div>
   </section>
  </div>
  <div className="opportunity-detail-chart" hidden={tab!=="chart"}>{chartMounted&&<Chart instrument={row.instrument_id} episode={row.event_id} snapshotId={row.snapshot_id||undefined}/>}</div>
  <section className="opportunity-strategy-panel" hidden={tab!=="strategy"}><h3>当前策略</h3><dl><dt>识别形态</dt><dd>{strategyId?strategyNames[strategyId]||"策略待核验":row.box?.upper?"箱体突破候选":"尚未确认"}</dd><dt>当前阶段</dt><dd>{snapshot?.phase_label||row.phase_label}</dd><dt>规则版本</dt><dd>{snapshot?.strategy_version||row.strategy_version||"未记录"}</dd></dl>{(strategyId||row.box?.upper)&&<p><a href={appPath("/v1/strategy-templates/"+(strategyId||"box_breakout")+"/document")} target="_blank" rel="noreferrer">查看完整独立模板：识别条件、参与、保护与画线</a></p>}<CaseEvidence value={snapshot?.case_matches}/></section>
 </div>;
}
