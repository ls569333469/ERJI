"use client";
import Link from "next/link";
import {Chart} from "./chart";
import {AssetClassBadge} from "./asset-class-badge";
import {Fragment,useCallback,useEffect,useState,useRef,useMemo} from "react";
import {api,clock,pct,sourceAge} from "@/lib/api";
import {observationOiMessage} from "@/lib/opportunity-display";
import {appPath} from "@/lib/paths";
import {useRefresh} from "@/lib/use-refresh";
import type {components} from "@/lib/api.generated";
type Reason=components['schemas']['AnomalyReason'];
type Row=components['schemas']['ObservationRow'];
type Result=components['schemas']['ObservationList'] & {scope?:string};
const labels:Record<string,string>={observing:"观察中",focused:"重点跟踪",cooling:"降频观察",archived:"已归档",queued:"等待分析",filling:"补充分析数据",ready:"分析已更新",delayed:"行情更新延迟"};
const tfs=["5","15","30","60","240","1D"];
const tfNames=["5M","15M","30M","1H","4H","1D"];
const reason=(r:Reason)=>r.kind==="manual"?"手动关注":r.kind==="volume"?`5分钟${r.value>=5?"强放量":"放量"} ${r.value.toFixed(1)}倍${r.intrabar?"（盘中）":""}`:r.kind==="oi"?`15分钟持仓 ${pct(r.value)}`:`${r.window}分钟 ${pct(r.value)}`;
function structureStatus(row:Row,now:number){
 const previous=row.structure_checked_at?' · 上次检查 '+sourceAge(row.structure_checked_at,now):'';
 const reasons:Record<string,string>={quote_stale:'等待新鲜报价',candles_stale:'等待最新收盘K线',candle_gaps:'等待补齐K线缺口',market_outside_scope:'市场不在分析范围',market_not_eligible:'结构市场资格待核实'};
 const reason=reasons[row.structure_check_status||''];
 const status=row.structure_task_status;
 const outcomes:Record<string,string>={no_match:'已分析，暂无匹配策略',waiting_trigger:'结构候选，等待触发',plan_created:'已有买入计划'};
 const label=status==='inactive'?'降频监测'
  :status==='completed'?(outcomes[row.strategy_result]||'结构已检查')
  :status==='waiting_inputs'?(reason||'等待分析数据')
  :status==='queued_for_admission'?'等待结构分析名额'
  :status==='failed'?'结构分析执行失败，等待重试'
  :status==='leased'?'结构复核中'
  :status==='queued'?'结构复核排队中'
  :reason|| (row.structure_checked_at?'结构已检查':'等待首次结构分析');
 return label+previous;
}
export function ObservationBoard({watch=false}:{watch?:boolean}){
 const [archived,setArchived]=useState(false),[pinned,setPinned]=useState(watch),[q,setQ]=useState(""),[direction,setDirection]=useState('all'),[expanded,setExpanded]=useState<string|null>(null),[connected,setConnected]=useState(false),[actionError,setActionError]=useState("");
 const [listOpen,setListOpen]=useState(true),[showAll,setShowAll]=useState(false);
 const [sort,setSort]=useState("latest"),[manual,setManual]=useState("");
 const cache=useRef<{key:string;value:Result}|null>(null);
 const load=useCallback(async(signal:AbortSignal)=>{
   const key=`${archived}:${pinned}`,prior=cache.current?.key===key?cache.current.value:null;
   const result=await api<Result>(`/anomaly-observations?archived=${archived}&pinned=${pinned}${prior?'&since='+prior.cursor:''}`,{signal,timeoutMs:8000});
   if(result.next_offset!=null){
     const loaded=new Set(result.rows.map(r=>r.asset_id));const pending=result.asset_ids.filter(id=>!loaded.has(id));
     for(let start=0;start<pending.length;start+=100){
       const assets=pending.slice(start,start+100).map(id=>'assets='+encodeURIComponent(id)).join('&');
       const page=await api<Result>(`/anomaly-observations?archived=${archived}&pinned=${pinned}&${assets}`,{signal,timeoutMs:8000});
       result.rows.push(...page.rows);
     }
     result.next_offset=null;
   }
   const map=new Map((result.incremental&&prior?prior.rows:[]).map(r=>[r.asset_id,r]));
   for(const row of result.rows)map.set(row.asset_id,row);
   const value={...result,scope:key,rows:[...map.values()].filter(r=>result.asset_ids.includes(r.asset_id))};
   if(!signal.aborted)cache.current={key,value};
   return value;
 },[archived,pinned]);
 const {data,error,refresh}=useRefresh(load,true,connected);
 useEffect(()=>{const stream=new EventSource(appPath('/v1/stream'));let timer:ReturnType<typeof setTimeout>|undefined;stream.onopen=()=>{setConnected(true);refresh()};stream.onerror=()=>setConnected(false);const update=()=>{if(!timer)timer=setTimeout(()=>{timer=undefined;refresh()},5000)};const reset=()=>{cache.current=null;update()};stream.addEventListener('observation_update',update);stream.addEventListener('market_update',update);stream.addEventListener('reset',reset);return()=>{stream.close();if(timer)clearTimeout(timer)}},[refresh]);
 const [cohort,setCohort]=useState<string[]|null>(null);const scope=useRef("");
 useEffect(()=>{const key=`${archived}:${pinned}`;if(scope.current!==key){scope.current=key;setCohort(null)}},[archived,pinned]);
 const rebuild=useRef(false);
 useEffect(()=>{if(data&&data.scope===`${archived}:${pinned}`&&(cohort===null||cohort.length===0&&data.rows.length>0||rebuild.current)){setCohort(data.rows.map(r=>r.asset_id));rebuild.current=false}},[data,cohort,archived,pinned]);
 const added=data?.rows.filter(r=>cohort&&!cohort.includes(r.asset_id)).length||0;
 const matches=(r:Row)=>direction==='all'||r.direction===direction||direction==='up'&&r.reasons?.some(x=>x.kind==='volume');
 const rows=(data?.rows||[]).map(r=>{const candidates=[r,...(r.related_events||[])];const chosen=candidates.find(matches);return chosen?{...chosen,related_events:candidates.filter(x=>x.id!==chosen.id)}:r}).filter(r=>!cohort||cohort.includes(r.asset_id)).sort((a,b)=>(cohort?.indexOf(a.asset_id)??0)-(cohort?.indexOf(b.asset_id)??0)).filter(r=>matches(r)&&r.symbol.toLowerCase().includes(q.toLowerCase()));
 const sorted=[...rows].sort((a,b)=>sort==='first'?b.visible_at-a.visible_at:sort==='volume'?(b.rvol??-Infinity)-(a.rvol??-Infinity):sort==='change'?(b.since_first_pct??-Infinity)-(a.since_first_pct??-Infinity):0);
 const visibleRows=showAll?sorted:sorted.slice(0,10);
 const addWatch=async()=>{try{await api(`/anomaly-observations/watch/${encodeURIComponent(manual.trim().toUpperCase())}`,{method:'POST'});setManual('');setActionError('');refresh()}catch(e){setActionError(String(e))}};
 const toggle=async(r:Row)=>{try{await api(`/anomaly-observations/${r.id}/pin`,{method:'PUT',body:JSON.stringify({pinned:!r.pinned})});setActionError('');refresh()}catch(e){setActionError(String(e))}};
 return <><main className="opportunity-page observation-workspace"><h1>市场观察</h1><p>自动发现异动并持续跟踪；“我的关注”只筛选你手动关注的资产。</p>
 <p>今日新增 {data?.counts.today??"—"} · 重点观察 {data?.counts.focused??"—"} · 满足买入条件 {data?.counts.active??"—"} · 更新延迟 {data?.counts.delayed??"—"}</p><p>当前筛选 {data?sorted.length:'—'} / {data?.unique_assets??'—'} 个资产 · {connected?'通知连接正常':'轮询更新'} · 结果时间 {clock(data?.as_of)}</p>
 <div className="opportunity-controls"><input aria-label="搜索币种" placeholder="搜索币种" value={q} onChange={e=>setQ(e.target.value)}/><select aria-label="异动方向" value={direction} onChange={e=>setDirection(e.target.value)}><option value="up">上涨 / 放量</option><option value="down">下跌风险</option><option value="all">全部方向</option></select><label><input type="checkbox" checked={pinned} onChange={e=>setPinned(e.target.checked)}/>我的关注</label><label><input type="checkbox" checked={archived} onChange={e=>setArchived(e.target.checked)}/>归档记录</label><select aria-label="排序" value={sort} onChange={e=>{setSort(e.target.value);setCohort(data?.rows.map(r=>r.asset_id)||[])}}><option value="latest">最新触发（固定行序）</option><option value="first">首次发现</option><option value="volume">量能</option><option value="change">首次后涨跌</option></select><button onClick={()=>{rebuild.current=true;refresh()}}>刷新</button></div>
 {<div className="opportunity-controls"><input aria-label="手动关注币种" placeholder="例如 BTCUSDT" value={manual} onChange={e=>setManual(e.target.value)}/><button disabled={!manual.trim()} onClick={()=>addWatch()}>添加观察</button><small>手动关注不会冒充异动触发</small></div>}
 {added>0&&<button onClick={()=>setCohort(data?.rows.map(r=>r.asset_id)||[])}>新增 {added} 条，点击加载</button>}
 {(error||actionError)&&<p role="alert">读取或操作失败，保留最近结果：{error||actionError}</p>}
 {!data&&!error&&<p>正在读取观察记录…</p>}
 {data&&!rows.length&&<p>{q||pinned||!!data.rows.length?'当前筛选没有记录':(!data.discovery.updated_at||data.as_of-data.discovery.updated_at>180000||data.discovery.state==='delayed'?'发现服务未就绪或更新延迟，请查看系统状态。':data.discovery.state==='warming'?'行情窗口预热中，已成立的异动仍会显示。':'当前没有满足规则的可见异动。')} <Link href="/operations">系统状态</Link></p>}
 {Array.isArray(data?.discovery.details?.pending_pool)&&data.discovery.details.pending_pool.length>0&&<details><summary>新币 / 流动性待核实（不计为标准异动）</summary>{(data.discovery.details.pending_pool as {symbol:string;instrument_id:string;reason:string}[]).map(x=><p key={x.instrument_id}>{x.symbol} · {x.reason}</p>)}</details>}
 <section className="observation-list"><header className="observation-list-heading"><strong>观察列表 · {sorted.length} 个资产</strong><span>{listOpen?`显示 ${visibleRows.length} / ${sorted.length} 条`:'列表已收起，行情继续更新'}</span><button type="button" aria-expanded={listOpen} aria-controls="observation-list-content" onClick={()=>setListOpen(value=>!value)}>{listOpen?'收起列表':'展开列表'}</button></header><div id="observation-list-content" hidden={!listOpen}>
 <div style={{overflowX:'auto'}}><table className="observation-table" style={{width:'100%',borderCollapse:'collapse'}}><thead><tr>{['币种 / 市场','首次异动 / 原因','当前价 / 首次后','5M / RVOL10 / OI15M','六周期趋势','观察 / 分析','操作'].map(h=><th className={h.includes('六周期')||h.includes('RVOL10')?'observation-wide':''} key={h} style={{textAlign:'left',padding:12}}>{h}</th>)}</tr></thead><tbody>{visibleRows.map(r=><Fragment key={r.asset_id}><tr style={{borderTop:'1px solid #dde4ed'}}><td style={{padding:12}}><b>{r.symbol}</b><br/><AssetClassBadge row={r}/><small style={{display:'block'}}>{r.instrument_id.includes(':spot:')?'现货':'合约'}</small></td><td>{clock(r.visible_at)}{r.direction==='manual'&&<small>手动关注</small>}{r.reasons?.map((x,i)=><small style={{display:'block'}} key={i}>{reason(x)}</small>)}</td><td>{r.price??'等待行情'}<br/>{r.since_first_pct==null?'—':pct(r.since_first_pct)}<small style={{display:'block'}}>{sourceAge(r.price_at,data?.as_of??Date.now())}{r.data_status==='delayed'?' · 行情延迟':r.snapshot_status==='waiting'?' · 观察样本待更新':''}</small></td><td className="observation-wide">{pct(r.changes?.["5"])}<br/>RVOL10 {r.rvol?.toFixed(1)??"—"}倍<br/>OI15M {observationOiMessage(r)}{r.oi_status!=="not_applicable"&&<small style={{display:"block"}} title={r.oi_change_start_at?"比较起点 "+clock(r.oi_change_start_at):undefined}>{r.oi_as_of?"OI "+sourceAge(r.oi_as_of,data?.as_of??Date.now()):"OI 尚无采样时间"}</small>}</td><td className="observation-wide">{tfs.map((tf,i)=>{const t=r.matrix?.[tf];return <span key={tf} style={{display:'inline-block',padding:6}} title={`${t?.status??'待分析'} · ${t?.bars??0}根 · ${clock(t?.as_of)}`}>{tfNames[i]}<br/>{!t||t.direction==='unknown'?'○':t.direction==='up'?'↑':t.direction==='down'?'↓':'—'}{t?.status==='provisional'?'*':''}</span>})}<small style={{display:'block'}}>* 预热未完成；○ 查看分析状态</small></td><td>{labels[r.status]??r.status}<br/>{r.analysis_status==='delayed'?'分析待更新':labels[r.analysis_status||'queued']??'等待分析'}<br/><small>趋势 {clock(r.analyzed_at)}</small><small style={{display:'block'}}>{structureStatus(r,data?.as_of??Date.now())}</small></td><td><button onClick={()=>toggle(r)}>{r.pinned?'取消重点':'重点关注'}</button><button onClick={()=>setExpanded(expanded===r.id?null:r.id)}>详情</button></td></tr>{expanded===r.id&&<tr><td colSpan={7}><ObservationDetail row={r}/></td></tr>}</Fragment>)}</tbody></table></div>{sorted.length>10&&<div className="observation-list-actions"><button type="button" aria-expanded={showAll} onClick={()=>setShowAll(value=>!value)}>{showAll?'收起为前10条':`展开全部 ${sorted.length} 条`}</button><small>全部记录保留，可按币种搜索。</small></div>}</div></section></main></>
}

function ObservationDetail({row:r}:{row:Row}){
 const marker=useMemo(()=>r.visible_price?{at:r.visible_at,price:r.visible_price,events:r.timeline?.filter(t=>t.price&&t.at>=r.visible_at).map(t=>({at:t.at,price:t.price!}))}:undefined,[r.visible_at,r.visible_price,r.timeline]);
 return <section className="observation-detail"><h2>{r.symbol} · {labels[r.status]??r.status}</h2><p>首次可见 {clock(r.visible_at)} · 最近触发 {clock(r.last_at)} · {r.trend_label??'趋势正在分析'}</p><p>累计触发 {r.trigger_count??0} 次 · {r.tracking_gap?'观测存在缺口，极值只代表已有样本':'已记录观测样本'}</p><p>观测最高变化 {pct(r.max_up_pct)} / 最低变化 {pct(r.max_down_pct)}</p><p>5分钟 {pct(r.changes?.['5'])} · RVOL10 {r.rvol?.toFixed(1)??'—'}倍 · OI15分钟 {pct(r.oi_change)}</p><Chart instrument={r.instrument_id} observation={marker}/><Link href={`/assets/${encodeURIComponent(r.asset_id)}`}>打开市场详情</Link> · <Link href="/signals">查看买入计划</Link><p>{r.plans?.length?`关联计划 ${r.plans.map(p=>p.id).join('、')}`:'暂无入场计划，继续观察'}</p><details><summary>其他市场与历史事件（{r.related_events?.length??0}）</summary>{r.related_events?.map(x=><p key={x.id}>{x.instrument_id} · {clock(x.visible_at)} · {x.reasons?.map(reason).join('；')}</p>)}</details><details><summary>事件时间线</summary>{r.timeline?.map((t,i)=><p key={i}>{clock(t.at)} {t.reasons.map(reason).join('；')}</p>)}</details><details><summary>六周期趋势依据</summary>{tfs.map((tf,i)=><p key={tf}>{tfNames[i]} · {r.matrix?.[tf]?.direction??'等待数据'} · RSI14 {r.matrix?.[tf]?.rsi14?.toFixed(1)??'—'} · RVOL20 {r.matrix?.[tf]?.rvol20?.toFixed(2)??'—'} · MACD {r.matrix?.[tf]?.macd??'—'} · BOLL {r.matrix?.[tf]?.boll_low??'—'}～{r.matrix?.[tf]?.boll_high??'—'}</p>)}</details></section>
}
