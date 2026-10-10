import {appPath} from "@/lib/paths";
import {clock} from "@/lib/api";
export type CaseMatches={status:string;matches:{case_id:string;symbol:string;label:string;trigger_at:number;data_scope:string;similarities:string[];differences:{metric:string;current:string|null;historical:string|null}[];missing:string[]}[]};
export function CaseEvidence({value}:{value?:CaseMatches}){
 return <section><h3>历史案例</h3>{value?.matches?.length?value.matches.map(item=><details key={item.case_id}><summary>{item.symbol} · {item.label} · {clock(item.trigger_at)}</summary><p>{item.similarities.join("；")}</p><dl>{item.differences.map((d,i)=><div key={i}><dt>{d.metric}</dt><dd>当前 {d.current==null?"缺数据":Number(d.current).toFixed(2)} / 案例 {d.historical==null?"缺数据":Number(d.historical).toFixed(2)}</dd></div>)}</dl><p>未能比较：{item.missing.join("；")}</p><p><a href={appPath("/v1/strategy-cases/"+encodeURIComponent(item.case_id))} target="_blank" rel="noreferrer">查看独立案例原始档案</a></p><small>{item.data_scope}。同规则形态参考，不代表上涨概率。</small></details>):<p>暂无可用的同规则、同阶段案例匹配。</p>}</section>
}
