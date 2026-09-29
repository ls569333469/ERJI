import type {Analysis} from './pd2-types';
type Context=unknown;
type Pine={Std:{time:(c:Context)=>number;period:(c:Context)=>string}};
type Study={main?:(context:Context)=>number[]};
/** Draw the exact stored closed-bar series. No second EMA/MACD implementation. */
export function backendStudies(PineJS:Pine,current:()=>Analysis|undefined){
 const definitions=[
  {name:'ERJI 趋势 EMA',overlay:true,plots:[['ema20','EMA20','#3089ab'],['ema50','EMA50','#c58e3f']]},
  {name:'ERJI MACD',overlay:false,plots:[['macd','MACD','#408bc1'],['signal','Signal','#d69741'],['histogram','Histogram','#189b83']]},
  {name:'ERJI 布林带',overlay:true,plots:[['bb_upper','上轨','#9287bb'],['bb_middle','中轨','#ababbd'],['bb_lower','下轨','#9287bb']]}
 ];
 return Promise.resolve(definitions.map(d=>({name:d.name,metainfo:{
  _metainfoVersion:53,id:d.name+'@tv-basicstudies-1',description:d.name,shortDescription:d.name,
  is_price_study:d.overlay,is_hidden_study:false,isCustomIndicator:true,format:{type:'price',precision:8},
  plots:d.plots.map((_,i)=>({id:'plot_'+i,type:'line'})),
  defaults:{styles:Object.fromEntries(d.plots.map(([key,,color],i)=>['plot_'+i,{linestyle:0,linewidth:key==='histogram'?1:2,plottype:key==='histogram'?5:0,trackPrice:false,color,visible:true}])),inputs:{revision:0}},
  styles:Object.fromEntries(d.plots.map(([,title],i)=>['plot_'+i,{title,histogramBase:0}])),
  inputs:[{id:'revision',name:'数据快照版本',type:'integer',defval:0,min:0,max:9007199254740991}]
 },constructor:function(this:Study){this.main=function(context){
  const time=PineJS.Std.time(context);const tf=PineJS.Std.period(context);
  const row=current()?.timeframes.find(a=>a.timeframe===tf)?.series?.find(r=>r.time===time);
  return d.plots.map(([key])=>{const value=row?.[key];return value==null?NaN:Number(value)});
 };}})));
}
