/** Display only: execution and eligibility continue to use the original decimals. */
export function priceText(value:string|number|null|undefined,tick?:string|null):string {
 if(value==null||!Number.isFinite(Number(value)))return "—";
 const decimal=(input:string)=>{
  const match=/^(\d+)(?:\.(\d*))?$/.exec(input);
  return match?{units:BigInt(match[1]+(match[2]||"")),scale:(match[2]||"").length}:null;
 };
 const amount=decimal(String(value)),step=tick?decimal(tick):null;
 if(amount&&step&&step.units>BigInt(0)){
  const scale=Math.max(amount.scale,step.scale);
  const a=amount.units*BigInt(10)**BigInt(scale-amount.scale);
  const t=step.units*BigInt(10)**BigInt(scale-step.scale);
  const rounded=((a+t/BigInt(2))/t)*t;
  const digits=rounded.toString().padStart(scale+1,"0");
  const whole=scale?digits.slice(0,-scale):digits;
  const fraction=scale?digits.slice(-scale).replace(/0+$/,""):"";
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g,",")+(fraction?"."+fraction:"");
 }
 return new Intl.NumberFormat("en-US",{maximumSignificantDigits:6}).format(Number(value));
}

export function readableReason(value:string){
 return value.replace(/\d+\.\d{8,}/g,number=>priceText(number));
}

export function opportunityAdvice(row:{action_code:string;reason:string;pending_state?:string;pending_volume_multiple?:string|null;tick_size?:string|null;box?:{trigger_price?:string|null};strategy_id?:string|null;trigger_requirements?:{relative_volume_min?:string;timeframe?:string;close_above?:string};timeframes?:{timeframe:string;status:string;direction:string}[]}){
 const tf=(id:string)=>row.timeframes?.find(t=>t.timeframe===id&&t.status==='fresh');
 const daily=tf('1D'),weekly=tf('1W');
 const background=daily?.direction==='up'&&weekly?.direction==='down'?'日线上行、周线仍下行':daily?.direction==='up'&&weekly?.direction==='up'?'日线与周线结构上行':daily?.direction==='down'?'日线仍下行':null;
 if(['paused','ended','expired'].includes(row.action_code))return {action:row.action_code==='paused'?'暂停新增参与':'原计划已结束',summary:readableReason(row.reason),background};
 if(row.action_code==='no_chase')return {action:'等待回到参与区',summary:readableReason(row.reason),background};
 if(row.action_code==='waiting'&&row.trigger_requirements?.close_above){
  const r=row.trigger_requirements;const frame=({'60':'1H','240':'4H'} as Record<string,string>)[r.timeframe||'']||r.timeframe||'';
  return {action:'等待'+(strategyNames[row.strategy_id||'']||'结构确认'),summary:frame+'收盘 > '+priceText(r.close_above,row.tick_size)+'；量比 ≥ '+Number(r.relative_volume_min||1.5).toFixed(2)+'倍'+(row.pending_volume_multiple!=null?'（当前 '+Number(row.pending_volume_multiple).toFixed(2)+'倍）':''),background};
 }
 if(row.action_code==='waiting'&&row.box?.trigger_price){
  const required=row.trigger_requirements?.relative_volume_min;
  const volume=row.pending_volume_multiple;
  const prefix=row.pending_state==='close_above_volume_unconfirmed'?'已越线，量能尚未确认':row.pending_state==='intrabar_above_close_rejected'?'盘中越线，收盘未站稳':'等待箱体突破';
  return {action:prefix,summary:'4H收盘 > '+priceText(row.box.trigger_price,row.tick_size)+(required?'；量比 ≥ '+Number(required).toFixed(2)+'倍':'')+(volume!=null?'（当前 '+Number(volume).toFixed(2)+'倍）':''),background};
 }
 return {action:row.action_code==='trial'?'可小仓试探':row.action_code==='participate'?'可按计划参与':'等待参与条件',summary:readableReason(row.reason),background};
}

export const strategyNames:Record<string,string>={early_breakout:"箱体早期触发",confirmed_breakout:"箱体突破",box_breakout:"箱体突破",trend_restart:"新箱体再突破",retest_entry:"突破后回踩",recent_high:"近期前高突破",ath:"历史新高突破",retest:"突破后回踩",squeeze:"收缩突破",double_bottom:"双底突破",early_strength:"多周期转强",trend_pullback:"趋势回踩",failed_breakdown:"假跌破收回",repeated_test:"多轮试探再突破"};

type ContractFields={contract_status?:string;oi_status?:string|null;oi_usd?:string|null;oi_as_of?:number|null;oi_notional_quote?:string|null;native_oi?:{quantity?:string|null;status?:string;observed_at?:number|null;notional_quote?:string|null;fx_status?:string};valuation_status?:string|null;circulating_cap?:string|null};
export function contractDataMessage(row:ContractFields,now=Date.now()):string {
 if(row.contract_status!=="verified"&&!row.native_oi)return "未关联币安合约";
 if(row.native_oi?.status==="unmapped"||row.contract_status==="unmapped")return "未关联币安合约";
 const at=row.native_oi?.observed_at??row.oi_as_of;
 if(at!=null&&at>now||row.oi_status==="clock_invalid")return "持仓量时间异常";
 if(row.oi_status==="stale"||row.native_oi?.status==="stale"||at!=null&&now-at>600000)return "持仓量更新延迟";
 if(row.oi_status==="notional_unavailable"&&row.native_oi?.quantity!=null)return "OI 数量已返回，金额待同刻标记价格";
 if(!row.oi_usd&&!row.oi_notional_quote&&!row.native_oi?.notional_quote)return "已核验合约，OI 待采集";
 if(row.oi_status==="fx_missing_or_stale"||row.native_oi?.fx_status==="missing_or_stale")return "OI 已返回，USD 换算待更新";
 return "";
}

export function valuationMessage(row:ContractFields):string {
 const labels:Record<string,string>={binance_supply_not_provided:"市值暂无数据",supply_stale:"市值数据已过期",fx_missing_or_stale:"市值换算待更新",price_stale:"价格更新延迟",reference:""};
 return labels[row.valuation_status||""]??(!row.circulating_cap?"市值暂无数据":"");
}

export function observationOiMessage(row:{oi_status?:string|null;oi_change?:number|null;oi_change_status?:string|null}):string {
 if(row.oi_status==="not_applicable")return "现货不适用";
 if(row.oi_change!=null&&row.oi_change_status==="available")return (row.oi_change>0?"+":"")+row.oi_change.toFixed(2)+"%";
 if(row.oi_status==="stale"||row.oi_change_status==="stale")return "持仓量更新延迟";
 if(row.oi_status==="clock_invalid")return "时间异常";
 if(row.oi_status==="fresh")return "15M 历史端点待齐";
 return "等待持仓量";
}
