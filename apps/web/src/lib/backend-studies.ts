import type {Analysis} from './pd2-types';
type Context=unknown;
type Pine={Std:{time:(c:Context)=>number;period:(c:Context)=>string}};
type Study={main?:(context:Context)=>number[]};
/** Draw the exact stored closed-bar series. No second EMA/MACD implementation. */
export function backendStudies(PineJS:Pine,current:()=>Analysis|undefined){
 let indexed:Analysis|undefined;
 let rows=new Map<string,Map<number,NonNullable<Analysis['timeframes'][number]['series']>[number]>>();
 const lookup=(time:number,tf:string)=>{
  const analysis=current();
  if(analysis!==indexed){
   indexed=analysis;
   rows=new Map(analysis?.timeframes.map(a=>[a.timeframe,new Map(a.series?.map(r=>[r.time,r]))])||[]);
  }
  return rows.get(tf)?.get(time);
 };
 const definitions=[
  {name:'EMA 20 / 50 · 闭合K线',overlay:true,plots:[['ema20','EMA20','#3089ab'],['ema50','EMA50','#c58e3f']]},
  {name:'MACD 12 / 26 / 9 · 闭合K线',overlay:false,plots:[['macd','MACD','#408bc1'],['signal','Signal','#d69741'],['histogram','Histogram','#189b83']]},
  {name:'ERJI 布林带',overlay:true,plots:[['bb_upper','上轨','#9287bb'],['bb_middle','中轨','#ababbd'],['bb_lower','下轨','#9287bb']]},
  {name:'ERJI 事件锚定均价',overlay:true,plots:[['event_avwap','事件AVWAP','#2b9993']]},
  {name:'ERJI RSI 14',overlay:false,plots:[['rsi14','RSI14','#9287bb']]},
  {name:'ERJI ADX 14',overlay:false,plots:[['adx14','ADX14','#c58e3f']]}
 ];
 return Promise.resolve(definitions.map(d=>({name:d.name,metainfo:{
  _metainfoVersion:53,id:d.name+'@tv-basicstudies-1',description:d.name,shortDescription:d.name,
  is_price_study:d.overlay,is_hidden_study:false,isCustomIndicator:true,format:d.overlay?{type:'inherit'}:{type:'price',precision:d.name.startsWith('MACD')?6:2},
  plots:[...d.plots.map((_,i)=>({id:'plot_'+i,type:'line'})),...(d.plots.some(([key])=>key==='histogram')?[{id:'histogram_color',type:'colorer',target:'plot_2',palette:'hist_colors'}]:[])],
  ...(d.name==='ERJI 布林带'?{filledAreas:[{id:'bb_fill',objAId:'plot_0',objBId:'plot_2',type:'plot_plot',title:'布林带范围',fillgaps:false}]}:{}),
  palettes:{hist_colors:{colors:{0:{name:'正值'},1:{name:'负值'}},valToIndex:{0:0,1:1}}},
  defaults:{...(d.name==='ERJI 布林带'?{filledAreasStyle:{bb_fill:{color:'#9287bb',transparency:92,visible:true}}}:{}),palettes:{hist_colors:{colors:{0:{color:'#189b83',width:1,style:0},1:{color:'#df5b67',width:1,style:0}}}},styles:Object.fromEntries(d.plots.map(([key,,color],i)=>['plot_'+i,{linestyle:0,linewidth:key==='histogram'?1:2,plottype:key==='histogram'?5:0,trackPrice:false,color,visible:true}])),inputs:{revision:0}},
  styles:Object.fromEntries(d.plots.map(([,title],i)=>['plot_'+i,{title,histogramBase:0}])),
  inputs:[{id:'revision',name:'数据快照版本',type:'integer',defval:0,min:0,max:9007199254740991,isHidden:true}]
 },constructor:function(this:Study){this.main=function(context){
  const time=PineJS.Std.time(context);const tf=PineJS.Std.period(context);
  const row=lookup(time,tf);
  const values=d.plots.map(([key])=>{const value=row?.[key];return value==null?NaN:Number(value)});
  if(d.plots.some(([key])=>key==='histogram'))values.push(row?.histogram==null?NaN:Number(row.histogram)>=0?0:1);
  return values;
 };}})));
}
