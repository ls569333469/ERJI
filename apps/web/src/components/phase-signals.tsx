"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import Link from "next/link";
import {useRefresh} from "@/lib/use-refresh";
import {appPath} from "@/lib/paths";
import {api,money,compact,pct,clock,venues} from "@/lib/api";

type View="buy"|"waiting"|"risk"|"history";
type Target={low:string;high:string;method?:string;timeframe?:string;remaining_upside_pct?:string|null}|null;
type Row={id:string;asset_id:string;instrument_id:string;snapshot_id?:string;symbol:string;venue:string;price:string|null;quote_asset:string;category:View;action_label:string;action_code:string;entry_type:string|null;reason:string;phase_label:string;entry_zone:string[]|null;protection:string|null;box:{upper:string|null;lower:string|null};first_seen_at:number;first_seen_price:string;since_first_pct:string|null;as_of:number|null;oi_usd:string|null;oi_notional_quote:string|null;oi_quote_asset:string;oi_cap_ratio:string|null;oi_scope:string;oi_as_of:number|null;oi_changes:{period:string;display_label?:string;value_pct:string|null;price_change_pct:string|null}[];funding_rate:string|null;funding_reference_instrument:string;funding_settled_at:number|null;funding_interval_seconds:number|null;valid_until:number;targets:{t1:Target;daily:Target;weekly:Target};row_number:number};
type Result={as_of:number;rows:Row[];groups:{id:View;label:string;count:number}[];scan:{scanned?:number;matched?:number;stale?:number;as_of?:number};projection:{as_of:number|null;status:string;coverage?:{unique_assets:number;eligible_markets:number;history_ready_markets:number;history_pending_or_short:number;source_failures:string[]}};pagination:{list_snapshot:string;page:number;pages:number;total:number;new_assets_waiting:number}};
const labels={buy:"建议买入",waiting:"等待买入",risk:"风险提醒",history:"历史信号"};
const range=(v:Target)=>v?money(v.low)+"–"+money(v.high):"尚无可靠目标";
export function PhaseSignals({view}:{view:View}){
 const [query,setQuery]=useState(""),[sort,setSort]=useState("first"),[page,setPage]=useState(1);
 const [connected,setConnected]=useState(false),[now,setNow]=useState(Date.now());
 const snapshot=useRef<string|undefined>(undefined);
 const load=useCallback(async(signal:AbortSignal)=>{const result=await api<Result>("/signals?"+new URLSearchParams({view,q:query,sort,page:String(page),page_size:"25",...(snapshot.current?{list_snapshot:snapshot.current}:{})}),{signal});if(!signal.aborted)snapshot.current=result.pagination.list_snapshot;return result},[view,query,sort,page]);
 const {data,error,busy,refresh}=useRefresh(load,true,connected);
 useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer)},[]);
 useEffect(()=>{
  const stream=new EventSource(appPath("/v1/stream"));let timer:ReturnType<typeof setTimeout>|undefined;
  stream.onopen=()=>{setConnected(true);refresh()};stream.onerror=()=>setConnected(false);
  const changed=()=>{if(timer===undefined)timer=setTimeout(()=>{timer=undefined;refresh()},1000)};
  for(const name of ["signal_update","reset"])stream.addEventListener(name,changed);
  return()=>{stream.close();if(timer)clearTimeout(timer)};
 },[refresh]);
 // Use server clock plus elapsed browser time, rather than trusting the workstation timezone/clock.
 const received=useRef({at:Date.now(),server:0});
 useEffect(()=>{if(data)received.current={at:Date.now(),server:data.as_of}},[data]);
 const serverNow=received.current.server+(now-received.current.at);
 const rows=(data?.rows||[]).filter(r=>view!=="buy"||r.valid_until>serverNow);
 const expired=(data?.rows.length||0)-rows.length;
 return <section className="phase-signals">
  <div className="signal-status"><div><i className={"mini-dot "+(connected?"green":"amber")}/><b>{labels[view]}</b><span>后台已评估 {data?.scan.scanned??"—"} 个市场 · 当前分类 {data?.pagination.total??"—"} 条</span></div><small>结果更新 {clock(data?.projection.as_of)} · {connected?"变更推送":"15秒补偿读取"}</small></div>
  <div className="radar-toolbar"><div className="search"><input aria-label="搜索信号" placeholder="搜索有信号的代币" value={query} onChange={e=>{snapshot.current=undefined;setPage(1);setQuery(e.target.value)}}/></div><select aria-label="信号排序" value={sort} onChange={e=>{snapshot.current=undefined;setPage(1);setSort(e.target.value)}}><option value="first">首次信号时间</option><option value="oi">合约持仓金额</option><option value="space">第一目标空间</option></select><span className="toolbar-end">同币一个当前入口 · 首报长期保留</span><button className="button" disabled={busy} onClick={()=>{snapshot.current=undefined;refresh(true)}}>{busy?"读取中…":"刷新结果"}</button></div>
  {data?.projection.coverage?.unique_assets!=null&&<div className="window-help">已准入 {data.projection.coverage.unique_assets} 个唯一资产，关联 {data.projection.coverage.eligible_markets} 个市场；四周期基础历史就绪 {data.projection.coverage.history_ready_markets} 个，待初始化或历史不足 {data.projection.coverage.history_pending_or_short} 个。{(data.projection.coverage.source_failures?.length||0)>0&&<span>来源缺口：{data.projection.coverage.source_failures.join(" / ")}，详见数据来源页。</span>}</div>}
  {error&&<div className="notice error">读取失败：{error}。已过期的买入建议自动隐藏。</div>}
  {expired>0&&<div className="notice">{expired} 条买入建议已超过有效期，暂停展示，等待后台核对报价与盘口。</div>}
  {data?.pagination.new_assets_waiting? <div className="notice">有 {data.pagination.new_assets_waiting} 条新信号，点击“刷新结果”加入列表；当前排序保持稳定。</div>:null}
  {!data&&!error&&<div className="panel empty">读取后台信号结果…</div>}
  {data&&!rows.length&&<div className="panel empty"><h3>当前没有{labels[view]}信号</h3><p>已评估 {data.scan.scanned??0} 个市场；最近扫描 {clock(data.scan.as_of)}，数据不足或过期 {data.scan.stale??0} 个。</p><p>{data.projection.status==="initializing"?"信号结果正在初始化。":view==="buy"?"目前没有同时通过结构触发、买入区、报价、盘口和成本校验的计划，可查看等待买入与风险提醒中的具体原因。":"普通波动、方向未明及无触发的代币保留后台扫描，不填充信号列表。"}</p></div>}
  {rows.length>0&&<div className="radar-table-wrap"><table className="radar-table signal-table"><thead><tr><th># / 代币</th><th>现在怎么做</th><th>现价 / 首报</th><th>盘面结构</th><th>持仓 OI / 市值</th><th>数量 OI · 4H / 1D</th><th>买入区 / 保护</th><th>第一 / 日线 / 周线目标</th><th>K线</th></tr></thead><tbody>{rows.map(r=><tr key={r.id}>
   <td><small>{r.row_number}</small> <b>{r.symbol}</b><div><small>{venues[r.venue]||r.venue} · {r.quote_asset}</small></div></td>
   <td><b className={"action-label "+(view==="buy"?"green":view==="risk"?"amber":"blue")}>{r.entry_type||r.action_label}</b><p style={{maxWidth:290,whiteSpace:"normal",lineHeight:1.5,margin:"8px 0"}}>{r.reason}</p></td>
   <td className="numeric"><b>{money(r.price)}</b><div><small>首报 {money(r.first_seen_price)} · {pct(r.since_first_pct)}</small></div><small>{clock(r.first_seen_at)}</small></td>
   <td><b>{r.phase_label}</b><div><small>{r.box?.upper?"箱体 "+money(r.box.lower)+"–"+money(r.box.upper):"结构见图表"}</small></div></td>
   <td className="numeric"><b>{r.oi_usd?"$"+compact(r.oi_usd):r.oi_notional_quote?compact(r.oi_notional_quote)+" "+r.oi_quote_asset:"无可用合约持仓"}</b><div><small>占流通市值 {r.oi_cap_ratio!=null?(Number(r.oi_cap_ratio)*100).toFixed(1)+"%":"数据不足"}</small></div><small>{r.oi_scope}</small><div><small>已结算费率 {r.funding_rate!=null?(Number(r.funding_rate)*100).toFixed(4)+"%":"缺数据"}{r.funding_interval_seconds?" / "+r.funding_interval_seconds/3600+"H":""} · {venues[r.funding_reference_instrument?.split(":")[0]]||r.venue}</small></div></td>
   <td>{["4H","24H"].map(w=>{const c=r.oi_changes.find(v=>v.period===w);return <div key={w}><small>{w==="24H"?"1D":"4H"} OI {c?.value_pct!=null?pct(c.value_pct):"待积累"} · 价格 {pct(c?.price_change_pct)}</small></div>})}<small>1D = 滚动24小时</small></td>
   <td className="numeric"><b>{r.entry_zone?.map(money).join("–")||"等待触发"}</b><div>保护 {money(r.protection)}</div><small>触及保护停止旧计划</small></td>
   <td className="numeric"><div>第一 {range(r.targets.t1)}</div><div><small>日线 {range(r.targets.daily)}</small></div><div><small>周线 {range(r.targets.weekly)}</small></div></td>
   <td><Link className="chart-link" href={"/assets/"+encodeURIComponent(r.asset_id)+"?instrument="+encodeURIComponent(r.instrument_id)+"&episode="+encodeURIComponent(r.id)+(view==="history"&&r.snapshot_id?"&snapshot="+encodeURIComponent(r.snapshot_id):"")}>看结构 ↗</Link></td>
  </tr>)}</tbody></table></div>}
  <div className="radar-toolbar"><span>每页25条 · 第 {data?.pagination.page||page} / {data?.pagination.pages||1} 页</span><button disabled={page<=1} onClick={()=>setPage(page-1)}>上一页</button><button disabled={page>=(data?.pagination.pages||1)} onClick={()=>setPage(page+1)}>下一页</button></div>
  <div className="table-note">技术阶段描述结构；OI描述未平仓数量，不代表净资金流入或多头单独增仓。买入行动需同时通过执行校验。日线与周线目标是有条件的结构目标，不是保证涨幅。报价 {clock(data?.as_of)}。</div>
 </section>
}
