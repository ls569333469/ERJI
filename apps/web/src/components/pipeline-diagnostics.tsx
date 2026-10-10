"use client";
import {useCallback} from "react";
import {api,clock} from "@/lib/api";
import {useRefresh} from "@/lib/use-refresh";
type Pipeline={as_of:number;transport:{failing?:boolean;last_error?:string|null;last_success_at?:number|null;recovered_at?:number|null};discovery:{state:string;updated_at?:number;details?:Record<string,unknown>};observations:Record<string,number>;analysis:Record<string,number>;jobs:Record<string,number>;plans:number;projected:number;stage_clocks:Record<string,number|null>};
const labels:Record<string,string>={quote_source_at:"行情来源时间",closed_candle_received_at:"收盘K线接收时间",observation_analyzed_at:"观察分析时间",projection_at:"机会结果时间",observing:"观察中",focused:"重点观察",suppressed:"随大盘波动，已降噪",cooling:"降频观察",archived:"已归档",queued:"等待执行",leased:"执行中",failed:"等待重试",complete:"已完成",ready:"分析已更新",filling:"分析数据待补",delayed:"分析待更新"};
export function PipelineDiagnostics(){
 const load=useCallback((signal:AbortSignal)=>api<Pipeline>("/diagnostics/pipeline",{signal,timeoutMs:5000}),[]);
 const {data,error}=useRefresh(load);
 if(error)return <p role="status">暂时无法读取各阶段进度，后台可能尚未加载本次更新。</p>;
 if(!data)return <p>正在读取各阶段进度…</p>;
 return <div style={{padding:16}}><p>通知读取 {data.transport.failing?"暂时失败":"正常"} · 最近成功 {clock(data.transport.last_success_at)}。通知连接正常仍需分别检查行情、分析和结果。</p>{data.transport.failing&&<p>当前错误：{data.transport.last_error}</p>}<p>发现服务更新：{clock(data.discovery.updated_at)} · 当前计划 {data.plans} · 已生成机会结果 {data.projected}</p><table><thead><tr><th>阶段</th><th>最近数据时间</th></tr></thead><tbody>{Object.entries(data.stage_clocks).map(([k,v])=><tr key={k}><td>{labels[k]||k}</td><td>{clock(v)}</td></tr>)}</tbody></table>{[["观察档案",data.observations],["分析结果",data.analysis],["执行队列",data.jobs]].map(([title,counts])=><p key={title as string}><b>{title as string}</b> · {Object.entries(counts as Record<string,number>).map(([k,v])=>`${labels[k]||k||'待分析'} ${v}`).join(" · ")}</p>)}</div>;
}
