import {appPath} from "./paths";
import type {components} from "./api.generated";
export type Market = components["schemas"]["MarketDTO"];
export type MarketList = components["schemas"]["MarketList"];
export type Candle = components["schemas"]["CandleDTO"];
export type CandleList = components["schemas"]["CandleList"];
export async function api<T>(path:string, options?:RequestInit):Promise<T> {
  const timeout=AbortSignal.timeout(45000);
  const signal=options?.signal?AbortSignal.any([options.signal,timeout]):timeout;
  try {
  const response=await fetch(appPath("/v1"+path),{...options,signal,headers:{"Content-Type":"application/json","X-ERJI-Client":"local-ui","X-ERJI-CSRF":typeof document!=="undefined"?decodeURIComponent(document.cookie.split("; ").find(x=>x.startsWith("erji_csrf="))?.split("=")[1]||""):"",...options?.headers},cache:"no-store"});
  if(response.status===401&&typeof window!=="undefined"&&!window.location.pathname.startsWith(appPath("/login"))){window.location.assign(appPath("/login"));}
  if(!response.ok) {
    const body=await response.json().catch(()=>({}));
    throw new Error(typeof body.detail==="string"?body.detail:body.message||"服务暂不可用（"+response.status+"）");
  }
  return await response.json();
  } catch(error) {
    if(timeout.aborted&&!options?.signal?.aborted)throw new Error("读取数据超时，已保留上次结果；稍后自动重试，也可手动刷新。");
    throw error;
  }
}
export function money(value:string|number|null|undefined) {
  if(value==null)return "—";
  const n=Number(value);
  if(!Number.isFinite(n))return "—";
  if(n!==0&&Math.abs(n)<1e-7)return n.toPrecision(4);
  return new Intl.NumberFormat("en-US",{maximumFractionDigits:n>=100?2:n>=1?4:10}).format(n);
}
export function compact(value:string|number|null|undefined) {
  if(value==null)return "—";const n=Number(value);
  return new Intl.NumberFormat("en-US",{notation:"compact",maximumFractionDigits:2}).format(n);
}
export function pct(value:string|number|null|undefined) {
  if(value==null)return "积累中";
  const n=Number(value);return (n>0?"+":"")+n.toFixed(2)+"%";
}
export function clock(value:number|null|undefined) {
  return value?new Date(value).toLocaleString("zh-CN",{hour12:false}):"尚无记录";
}
export function intervalLabel(seconds:number|null|undefined) {
  if(!seconds)return "周期未知";
  const hours=seconds/3600;const nearest=Math.round(hours);
  if(nearest>0&&Math.abs(seconds-nearest*3600)<=2)return (seconds===nearest*3600?"":"约")+nearest+"H";
  return Number(hours.toFixed(3))+"H";
}
export const venues:Record<string,string>={binance:"Binance",hyperliquid:"Hyperliquid",aster:"Aster",binance_spot:"Binance 现货",binance_alpha:"Binance Alpha",okx:"OKX",cmc:"CoinMarketCap",wallet_rising:"币安钱包 · 飙升"};

export function qualityLabel(status:string|undefined) {
  const labels:Record<string,string>={fresh:"有效",available:"有历史样本",stale:"已过期",insufficient_history:"历史不足",not_applicable:"不适用（无合约）",missing:"未返回",not_returned:"未返回",unsupported:"来源不支持",rate_limited:"请求限流",error:"采集异常"};
  return status?(labels[status]||"状态待核对") : "状态未知";
}
export function oiChangeLabel(change:NonNullable<Market["oi_changes"]>[number]) {
  return change.value_pct!=null?pct(change.value_pct):qualityLabel(change.quality.status);
}
export function oiRatioLabel(market:Market) {
  if(market.oi_cap_ratio!=null)return (Number(market.oi_cap_ratio)*100).toFixed(2)+"%";
  if(market.oi_quality.status==="not_applicable")return "不适用（无合约）";
  const labels:Record<string,string>={unmapped:"身份待映射",supply_missing_or_stale:"流通供应缺失或过期",fx_missing_or_stale:"USD换算缺失或过期",price_stale:"报价已过期",invalid_supply:"流通供应无效"};
  return labels[market.valuation_status]||("OI "+qualityLabel(market.oi_quality.status));
}
