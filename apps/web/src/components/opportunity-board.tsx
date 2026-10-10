"use client";
import {AssetClassBadge} from "./asset-class-badge";

import Link from "next/link";
import {Fragment,useCallback,useEffect,useMemo,useState} from "react";
import {OpportunityDetail} from "./opportunity-detail";
import {api,clock,compact,intervalLabel,pct,venues,sourceAge} from "@/lib/api";
import {priceText,strategyNames,opportunityAdvice} from "@/lib/opportunity-display";
import {appPath} from "@/lib/paths";
import {useRefresh} from "@/lib/use-refresh";

type Level="l1"|"l2"|"l3";
type Target={low:string;high:string;method?:string;timeframe?:string;known_at?:number;remaining_upside_pct?:string|null;basis_price?:string|null;basis_kind?:string}|null;
type Timeframe={timeframe:string;status:string;direction:string;momentum:string;macd_cross_confirmed:boolean;roc_5_pct:string|null;volume_multiple:string|null};
type OiChange={period:string;value_pct:string|null;notional_change_pct?:string|null;price_change_pct:string|null;status?:string|null};
export type Row={valuation_kind?:string;valuation_source?:string;valuation_received_at?:number|null;current_fields_as_of?:number;asset_class?:string;asset_class_label?:string;asset_class_source?:string|null;asset_name?:string|null;valid_until?:number;contract_status?:string;pending_volume_multiple?:string|null;oi_sources?:string[];circulating_cap?:string|null;oi_as_of?:number|null;funding_status?:string;funding_settled_at?:number|null;native_oi?:{instrument_id?:string|null;status?:string;quantity?:string|null;notional_quote?:string|null;quote_asset?:string|null;observed_at?:number|null;fx_status?:string};tick_size?:string|null;pending_state?:string;distance_to_trigger_pct?:string|null;trigger_requirements?:{relative_volume_min?:string;timeframe?:string;close_above?:string};id:string;snapshot_id:string|null;event_id:string;strategy_id:string|null;strategy_version:string|null;asset_id:string;instrument_id:string;symbol:string;venue:string;price:string|null;quote_asset:string;opportunity_level:Level;action_code:string;reason:string;phase_label:string;entry_zone:string[]|null;protection:string|null;protection_reference:{price:string;status:string;method:string}|null;invalidation:{price:string;timeframe:string;condition:string;method:string}|null;box:{upper:string|null;lower:string|null;trigger_price?:string|null};first_seen_at:number;first_seen_price:string;since_first_pct:string|null;as_of:number|null;timeframes:Timeframe[];oi_usd:string|null;oi_notional_quote:string|null;oi_quote_asset:string;oi_cap_ratio:string|null;oi_scope:string;oi_status?:string|null;oi_quantity_status?:string|null;oi_market_count?:number;oi_changes:OiChange[];valuation_status?:string|null;funding_rate:string|null;funding_reference_instrument:string;funding_interval_seconds:number|null;targets:{t1:Target;daily:Target;weekly:Target};preliminary_net_r:string|null;candidate_min_net_r:string;opportunity_eligible:boolean;opportunity_eligibility_reason:string;row_number:number};
type Result={as_of:number;rows:Row[];groups:{id:string;count:number}[];opportunity_counts:Record<Level,number>;scan:{stale?:number;scanned?:number;matched?:number;as_of?:number;last_full_scan_at?:number};projection:{as_of:number|null;coverage?:{unique_assets?:number;eligible_markets?:number;history_ready_markets?:number;history_pending_or_short?:number;source_failures?:string[]}};pagination:{list_snapshot:string;page:number;pages:number;total:number;new_assets_waiting:number}};

const tfLabel:Record<string,string>={"60":"1H","240":"4H","1D":"1D","1W":"1W"};
const directionLabel:Record<string,string>={up:"结构上行",down:"结构下行",sideways:"高低点冲突",mixed:"高低点冲突",broken:"结构失效",unknown:"历史不足"};
const directionTone=(v?:Timeframe)=>v?.status!=="fresh"?"pending":v.direction==="up"?"up":v.direction==="down"||v.direction==="broken"?"down":"turn";
const range=(target:Target,tick?:string|null)=>target?target.low===target.high?priceText(target.low,tick):priceText(target.low,tick)+"–"+priceText(target.high,tick):"未形成";
const change=(row:Row,period:string)=>row.oi_changes.find(item=>item.period===period)?.value_pct;
const changeTone=(value:string|null|undefined)=>value==null?"":Number(value)>0?"positive":Number(value)<0?"negative":"";

function hasContract(row:Row){return row.contract_status==='verified'&&Boolean(row.native_oi?.instrument_id);}

export function OpportunityBoard({waiting=false}:{waiting?:boolean}){
 const [query,setQuery]=useState(""),[sort,setSort]=useState("first"),[market,setMarket]=useState<"all"|"contract"|"spot">("all");
 const [connected,setConnected]=useState(false);
 const [openLevels,setOpenLevels]=useState({l1:true,l2:true,l3:true});
 const [now,setNow]=useState(0);
 useEffect(()=>{setNow(Date.now());const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer)},[]);
 const sections:{id:Level;title:string;description:string}[]=waiting?[{id:'l2',title:'等待条件的关联计划',description:'保留原参与区、保护、目标和详情；条件未满足时不属于现在可以买。'}]:[{id:'l1',title:'现在可以买',description:'已通过现有规则且仍在有效期内。仓位与分批方式属于每条信号的策略。'},{id:'l2',title:'关联计划 · 等待条件',description:'等待价格、收盘或其他条件，满足买入条件后进入上方列表。'}];
 const load=useCallback(async(signal:AbortSignal)=>{
  const params=new URLSearchParams({view:"opportunities",q:query,sort,page:"1",page_size:"300"});
  const result=await api<Result>('/signals?'+params,{signal,timeoutMs:8000});
  for(let page=2;page<=result.pagination.pages;page++){
   params.set('page',String(page));params.set('list_snapshot',result.pagination.list_snapshot);
   const next=await api<Result>('/signals?'+params,{signal,timeoutMs:8000});
   if(next.pagination.list_snapshot!==result.pagination.list_snapshot)throw new Error('结果列表版本已更新，正在等待重新读取。');
   result.rows.push(...next.rows);
  }
  return result;
 },[query,sort]);
 const {data,error,busy,refresh}=useRefresh(load,true,connected);
 useEffect(()=>{const stream=new EventSource(appPath("/v1/stream"));let timer:ReturnType<typeof setTimeout>|undefined;stream.onopen=()=>setConnected(true);stream.onerror=()=>setConnected(false);const changed=()=>{if(!timer)timer=setTimeout(()=>{timer=undefined;refresh()},5000)};for(const name of ["signal_update","reset"])stream.addEventListener(name,changed);return()=>{stream.close();if(timer)clearTimeout(timer)}},[refresh]);
 const allRows=useMemo(()=>data?.rows.filter(row=>market==="all"||(market==="contract"?hasContract(row):!hasContract(row)))||[],[data,market]);
 const buyRows=useMemo(()=>allRows.filter(row=>['participate','trial'].includes(row.action_code)&&now>0&&now<row.valid_until!),[allRows,now]);
 const waitingRows=useMemo(()=>allRows.filter(row=>row.opportunity_level==='l2'),[allRows]);
 const rows=waiting?waitingRows:buyRows;
 const expired=waiting?0:allRows.filter(row=>['participate','trial'].includes(row.action_code)&&!rows.includes(row)).length;
 const visibleCount=rows.length;
 const dataDelayed=Boolean(data&&(!data.projection.as_of||data.as_of-data.projection.as_of>90000||(data.scan.stale||0)>0));
 const assessed=data?.projection.coverage?.eligible_markets??data?.scan.scanned;
 const groupCounts=useMemo(()=>Object.fromEntries((data?.groups||[]).map(group=>[group.id,group.count])),[data]);
 function resetAndRefresh(){refresh(true)}
 return <main className="opportunity-page">
  <section className="opportunity-heading"><div><h1>{waiting?'关联计划':'买入机会'}</h1><p>{waiting?'等待指定价格、收盘或其他条件，完整计划与原详情保留。':'现在可以买什么，以及每条信号的买入策略、参与区和保护。'}</p></div><div className="opportunity-stats"><div><strong>{data?visibleCount:"—"}</strong><span>{waiting?'等待计划':'现在可以买'}</span></div><div><strong>{assessed??"—"}</strong><span>后台覆盖</span></div></div></section>
  <section className="opportunity-toolbar">
   <div className="opportunity-search"><input aria-label="搜索代币" placeholder="搜索有机会的代币" value={query} onChange={event=>setQuery(event.target.value)}/></div>
   <div className="opportunity-segment" aria-label="市场筛选">{([['all','全部机会'],['contract','有合约'],['spot','现货 / 链上']] as const).map(([id,label])=><button key={id} className={market===id?"active":""} onClick={()=>setMarket(id)}>{label}</button>)}</div>
   <select aria-label="机会排序" value={sort} onChange={event=>setSort(event.target.value)}><option value="first">首次信号时间</option><option value="space">第一目标空间</option><option value="oi">合约持仓金额</option></select>
   <span className="opportunity-live"><i className={connected?"online":""}/>{connected?"实时更新 · 30秒对账":"30秒补偿读取"}</span>
   <button className="opportunity-refresh" disabled={busy} onClick={resetAndRefresh} aria-label="刷新机会" title="刷新机会">↻</button>
  </section>
  {error&&<div className="opportunity-notice error">读取失败：{error}。页面保留上一次有效结果，不会补入演示数据。</div>}
  {!waiting&&expired>0&&<div className="opportunity-notice">{expired} 条买入资格已到期，等待后台复核；不会继续显示为现在可以买。</div>}
  {data?.pagination.pages&&data.pagination.pages>1?<div className="opportunity-notice">已读取本次结果的全部 {data.pagination.total} 条记录，可使用搜索与市场筛选定位。</div>:null}
  {!data&&!error&&<div className="opportunity-empty">正在读取真实机会结果…</div>}
  {data&&sections.map(section=>{
   const sectionRows=section.id==='l2'?waitingRows:buyRows;
   return <details open={openLevels[section.id]} onToggle={event=>{const open=event.currentTarget.open;setOpenLevels(value=>value[section.id]===open?value:{...value,[section.id]:open})}} data-section={section.id==='l2'?'waiting-plans':'buy-now'} className={"opportunity-stage "+section.id} key={section.id}>
    <summary><span>{section.id==='l2'?'等待':'可买'}</span><strong>{section.title}</strong><p>{section.description}</p><b>{sectionRows.length} 个</b><i>{openLevels[section.id]?'收起':'展开'}</i></summary>
  {section.id==='l1'&&sectionRows.length===0&&<div className={"opportunity-empty"+(waitingRows.length?" has-related-plans":"")}><h2>{dataDelayed?"数据仍在更新，暂未得到有效机会":"当前筛选下没有可执行机会"}</h2><p>{dataDelayed?"部分行情或计算结果已过期，当前空列表不能代表市场没有机会。可查看后台运行中的最新采集与评估记录。":"本次有效结果中没有代币通过完整买入规则。"}</p><details className="opportunity-empty-status" open={waitingRows.length===0}><summary>查看筛选状态</summary><div className="opportunity-runtime"><span><b>{data.projection.coverage?.unique_assets??"—"}</b>唯一资产</span><span><b>{assessed??"—"}</b>合格市场</span><span><b>{data.projection.coverage?.history_ready_markets??"—"}</b>基础历史达标</span><span><b>{data.scan.scanned??"—"}</b>上轮有效扫描</span><span><b>{data.scan.matched??"—"}</b>结构命中</span><span><b>{groupCounts.risk??0}</b>风险记录</span></div><Link className="opportunity-monitor-link" href="/operations">查看后台运行与筛选漏斗 →</Link></details></div>}
    {sectionRows.length===0?(section.id==='l2'?<div className="opportunity-stage-empty">当前没有等待条件的关联计划</div>:null):<div className="opportunity-table-scroll"><table className="opportunity-table"><thead><tr><th>代币</th><th>现在怎么做 / 策略</th><th>现价 / 首报</th><th title="展示按市场最小价格单位取整；判断使用原始精度，必须等待收盘及其他条件同时成立">参与区 / 保护 ⓘ</th><th>第一 / 日线 / 周线目标</th><th>1H / 4H / 1D / 1W</th><th>OI金额（USD） / 持仓数量</th><th>市值（USD） / OI占市值比</th><th>资金费率 / 结算周期</th><th>OI持仓变化<br/>1H / 4H / 1D</th><th>OI更新时间</th><th>详情</th></tr></thead><tbody>{sectionRows.map(row=><OpportunityRow key={row.id} row={row}/>)}</tbody></table></div>}
   </details>
  })}
  {data&&<p className="opportunity-footnote">{waiting?`共显示 ${waitingRows.length} 条关联计划`:`现在可以买 ${buyRows.length} 条 · 等待条件 ${waitingRows.length} 条`}。北京时间 · 报价 {clock(data.as_of)}，投影 {clock(data.projection.as_of)}。</p>}
 </main>
}

function OpportunityRow({row}:{row:Row}){
 const [expanded,setExpanded]=useState(false);
 const [detailSnapshot,setDetailSnapshot]=useState<string|null>(row.snapshot_id);
 const oiAmount=row.oi_usd!=null?'$'+compact(row.oi_usd):'—';
 const oiQuantity=row.native_oi?.quantity!=null?compact(row.native_oi.quantity):'—';
 const oiRatio=row.oi_cap_ratio!=null?(Number(row.oi_cap_ratio)*100).toFixed(1)+"%":null;
 const timeframes=new Map(row.timeframes?.map(item=>[item.timeframe,item])||[]);
 const t1=row.targets.t1,daily=row.targets.daily,weekly=row.targets.weekly;
 const dailyDuplicatesT1=Boolean(t1&&daily&&t1.low===daily.low&&t1.high===daily.high);
 const price=(value:string|null|undefined)=>priceText(value,row.tick_size);
 const protectionText=row.protection?`保护 ${price(row.protection)}`:row.protection_reference?.price?`保护参考 ${price(row.protection_reference.price)}（估算）`:"保护待形成";
 const strategy=row.strategy_id?strategyNames[row.strategy_id]||"策略待核验":row.box?.upper?"箱体突破":"结构待确认";
 const advice=opportunityAdvice(row);
 return <Fragment><tr>
  <td><div className="opportunity-coin"><span>{row.symbol.slice(0,4)}</span><div><b>{row.symbol}</b><br/><AssetClassBadge row={row}/><small>{venues[row.venue]||row.venue} · {row.quote_asset}</small></div></div></td>
  <td><span className="opportunity-action">{advice.action}</span><div className="opportunity-reason">{advice.summary}</div><small>{strategy} · {row.phase_label}</small>{advice.background&&<small>{advice.background}</small>}</td>
  <td className="numeric"><b title={row.price||undefined}>{price(row.price)}</b><small>首报 {price(row.first_seen_price)} · {pct(row.since_first_pct)}</small><small title="北京时间">{clock(row.first_seen_at)}</small></td>
  <td className="numeric"><b title={row.box?.trigger_price?"原始触发阈值 "+row.box.trigger_price:undefined}>{row.entry_zone?.map(value=>price(value)).join("–")||(row.box?.trigger_price?"4H 收盘高于 "+price(row.box.trigger_price):"等待结构价")}</b><small className="negative">{protectionText}</small>{row.distance_to_trigger_pct!=null&&<small>{Number(row.distance_to_trigger_pct)>0?"距触发线 "+pct(row.distance_to_trigger_pct):"已越过触发线，待收盘确认"}</small>}</td>
  <td className="numeric"><div className="opportunity-targets"><span>第一 {range(t1,row.tick_size)}</span><span>{dailyDuplicatesT1?"日线同第一目标":"日线 "+range(daily,row.tick_size)}</span><span>周线 {range(weekly,row.tick_size)}</span></div><small title={t1?.basis_price?"从计划参考价 "+price(t1.basis_price)+" 计算":"目标形成后计算"}>计划空间 {t1?.remaining_upside_pct!=null?pct(t1.remaining_upside_pct):"待形成"}{row.preliminary_net_r!=null?" · 盈亏比 "+Number(row.preliminary_net_r).toFixed(2):""}</small></td>
  <td><div className="opportunity-timeframes">{["60","240","1D","1W"].map(id=>{const value=timeframes.get(id);return <span key={id} className={directionTone(value)} title={value?`${directionLabel[value.direction]||value.direction} · ${value.momentum||"动能待确认"}`:"该周期尚无投影"}>{tfLabel[id]}<small>{value?.status==="fresh"?(directionLabel[value.direction]||"待确认"):"待数据"}</small></span>})}</div></td>
  <td className="numeric opportunity-market-data"><b>{oiAmount}</b><small>{oiQuantity}</small></td>
  <td className="numeric opportunity-market-data"><b>{row.circulating_cap!=null?'$'+compact(row.circulating_cap):'—'}</b><small>{oiRatio||'—'}</small></td>
  <td className="numeric opportunity-market-data"><b>{row.funding_rate!=null?(Number(row.funding_rate)*100).toFixed(4)+'%':'—'}</b><small>{row.funding_interval_seconds?intervalLabel(row.funding_interval_seconds):'—'}</small></td>
  <td className="numeric opportunity-market-data">{["1H","4H","1D"].map(period=><div key={period} className={changeTone(change(row,period))}>{change(row,period)!=null?pct(change(row,period)):'—'}</div>)}</td>
  <td className="numeric opportunity-market-data"><span title={clock(row.native_oi?.observed_at??row.oi_as_of)}>{row.native_oi?.observed_at||row.oi_as_of?sourceAge(row.native_oi?.observed_at??row.oi_as_of,Date.now()).replace(/^上次更新\s*/,""):'—'}</span></td>
  <td><button type="button" className="opportunity-expand" aria-expanded={expanded} onClick={()=>{if(!expanded)setDetailSnapshot(row.snapshot_id);setExpanded(value=>!value)}}>{expanded?"收起":"展开"}</button>{expanded&&row.snapshot_id!==detailSnapshot&&<button type="button" className="opportunity-expand" onClick={()=>setDetailSnapshot(row.snapshot_id)}>更新详情</button>}</td>
 </tr>{expanded&&<tr className="opportunity-detail-row"><td colSpan={12}><OpportunityDetail key={row.event_id+":"+(detailSnapshot||"candidate")} row={{...row,snapshot_id:detailSnapshot}}/></td></tr>}</Fragment>
}
