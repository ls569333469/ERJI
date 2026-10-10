/* Synthetic product-review fixtures. No live provider requests or trade execution. */
const seedEvent=event,episodeCache=new Map();
function quoteRatio(series,i){const prev=series.slice(Math.max(0,i-20),i).map(b=>b.volume*b.close).sort((x,y)=>x-y);return prev.length===20?series[i].volume*series[i].close/((prev[9]+prev[10])/2):null;}
function episode(a){
 if(episodeCache.has(a.id))return episodeCache.get(a.id);
 const e=seedEvent(a),d=bars(a),idx=d.findIndex(b=>b.time/1000===e.time);let atr=0;
 for(let i=1;i<idx;i++){const b=d[i],prev=d[i-1].close,tr=Math.max(b.high-b.low,Math.abs(b.high-prev),Math.abs(b.low-prev));if(i<=14)atr+=tr/14;else atr=(atr*13+tr)/14;}
 const buffer=atr*.25;let trigger=null,revoke=null,broken=null;
 for(let i=idx;i<d.length;i++){const b=d[i];if(b.close<e.low){broken=b;break;}if(trigger&&b.close<e.high-buffer){revoke=b;break;}if(!trigger&&b.close>e.high+buffer&&quoteRatio(d,i)>=1.5)trigger=b;}
 const last=d.at(-1),invalid=!!(revoke||broken),stage=broken?'箱体下沿失守':revoke?'突破后跌回箱体':trigger?'箱体突破 · 跟踪':'箱体整理 · 待突破';
 const p={...e,atr,buffer,trigger,revoke,broken,invalid,stage,price:last.close,zoneLow:e.high-buffer,zoneHigh:e.high+buffer,time:trigger?trigger.time/1000:e.time,kind:invalid?'invalid':trigger?'tracking':'watch'};
 episodeCache.set(a.id,p);return p;
}
event=function(a){const p=episode(a);return{time:p.time,high:p.high,low:p.low,ratio:p.trigger?quoteRatio(bars(a),bars(a).indexOf(p.trigger)):seedEvent(a).ratio};};
const venueFixtures={
 SOL:[['Binance',.68,8.6,-.008,8,-.06],['Hyperliquid',.22,11.2,-.002,1,-.04],['Aster',.10,5.1,-.009,4,-.07]],
 WIF:[['Binance',.48,-5.3,-.034,4,-.65],['Aster',.39,-9.2,-.08,1,-1.2],['Hyperliquid',.13,-3.2,-.02,1,-.4]],
 PEPE:[['Binance',.75,2.1,.012,8,.04],['Aster',.25,4.2,.008,4,.03]],
 HYPE:[['Hyperliquid',.8,11.2,-.005,1,-.04],['Binance',.2,5.6,-.008,8,-.05]],
 ASTER:[['Aster',.7,6.4,-.048,1,-.6],['Binance',.3,2.7,-.012,4,-.1]]
};
function fixtures(a){
 const d=bars(a),price=d.at(-1).close,previous=d.at(-2).close;
 return (venueFixtures[a.id]||[]).map(([name,weight,qChange,rate,cycle,basis])=>{
  const mark=price*(1+basis/100),q=a.oi*weight/mark;
  return {name,key:name+':demo:'+a.id,usd:q*mark,q,mark,index:price,basis,rate,cycle,qChange,qPrevious:q/(1+qChange/100),usdPrevious:q/(1+qChange/100)*previous*(1+basis/100),h1:qChange*.28,h24:qChange*1.65,
   funding:Array.from({length:24/cycle},(_,i)=>({time:anchor-i*cycle*3600,rate})),age:20+name.length*3,status:'fresh'};
 });
}
const venueCache=new Map(assets.map(a=>[a.id,fixtures(a)]));
function aggregateOI(rows){
 const seen=new Set(),valid=rows.filter(r=>{if(r.status!=='fresh'||seen.has(r.key))return false;seen.add(r.key);return true;});
 if(!valid.length)return null;
 const sum=k=>valid.reduce((s,r)=>s+r[k],0),usd=sum('usd'),q=sum('q'),prevQ=sum('qPrevious'),prevUSD=sum('usdPrevious');
 return {rows:valid,usd,q,q4:(q/prevQ-1)*100,usd4:(usd/prevUSD-1)*100,h1:(q/valid.reduce((s,r)=>s+r.q/(1+r.h1/100),0)-1)*100,h24:(q/valid.reduce((s,r)=>s+r.q/(1+r.h24/100),0)-1)*100,top:[...valid].sort((x,y)=>y.usd-x.usd)[0]};
}
function fundingCost(row,hours){const records=row.funding.filter(f=>f.time>anchor-hours*3600&&f.time<=anchor);return {count:records.length,net:records.reduce((s,f)=>s-f.rate,0),paid:records.reduce((s,f)=>s+Math.max(-f.rate,0),0)};}
function oiEvidence(a){
 const agg=aggregateOI(venueCache.get(a.id)),coverageChanged=a.id==='HYPE';
 if(!agg)return {agg:null,summary:a.id==='ALPHA-X'?'合约映射待核':'已查目录无匹配',quality:a.id==='ALPHA-X'?'身份待核':'范围内无匹配',detail:a.id==='ALPHA-X'?'合约身份尚未匹配；不能认定没有永续市场。':'预览模拟已查 Binance / Aster / Hyperliquid 无匹配；不能代表全网没有合约。',pressure:null,coverageChanged:false};
 const pressure=[...agg.rows].sort((x,y)=>fundingCost(y,24).paid-fundingCost(x,24).paid)[0];
 const summary=coverageChanged?'覆盖变化 · 暂缓判读':a.id==='WIF'?'减仓上涨 · 清算已观测':a.id==='ASTER'?'空头付费 · 待突破':a.id==='PEPE'?'持仓扩张 · 方向待核':'持仓扩张 · 继续跟踪';
 return {agg,pressure,summary,coverageChanged,quality:coverageChanged?'2 所可用 · 1 所过期':a.id==='ASTER'?'2 所可用 · 1 所缺失':a.id==='PEPE'?'2 所可用 · 部分覆盖':'3 所可用 · 部分覆盖',detail:coverageChanged?'Aster 样本过期，已从金额排除；与 4H 前覆盖不同，窗口变化暂停显示。':a.id==='ASTER'?'Hyperliquid 为已映射但缺失的预设样例；未计入总量。真实挂牌情况尚未接 API 核验。':'仅汇总本页合成且时点合格的市场，不代表真实平台挂牌或全网覆盖。'};
}
function watchStar(a){return `<button class="star-btn ${saved.has(a.id)?'saved':''}" data-watch="${a.id}" aria-label="${saved.has(a.id)?'取消观察':'观察'} ${a.id}" aria-pressed="${saved.has(a.id)}">${saved.has(a.id)?'★':'☆'}</button>`;}
function planText(a){const p=episode(a);return{zone:p.invalid?'旧计划停用':p.trigger?fmt(p.zoneLow)+'–'+fmt(p.zoneHigh):'站上 '+fmt(p.zoneHigh),condition:p.invalid?'等待新的箱体':a.pool==='chain'?'先核实池深度与可卖出性':p.trigger?'回踩后 1H 收回并放量':'4H 收盘突破并放量',full:p.invalid?'本次事件已经结束。后来价格反弹不恢复旧事件，需要新的整理结构与新 episode。':p.trigger?`先回踩 ${fmt(p.zoneLow)}–${fmt(p.zoneHigh)}；其后完整 1H 收盘高于 ${fmt(p.zoneHigh)}，报价成交额达到此前20根中位数1.5倍。`:`完整 4H 收盘高于 ${fmt(p.zoneHigh)}，报价成交额达到此前20根中位数1.5倍。`};}
let overviewMode='decision';
const cell=(value,tip='',cls='')=>`<td class="${cls}"${tip?` title="${tip}"`:''}>${value}</td>`;
const pct=v=>v==null?'—':signed(v,1);
tableRow=function(a){const p=episode(a),ev=oiEvidence(a),o=ev.agg,t=planText(a),fund=ev.pressure;
 let s=`<tr data-asset="${a.id}"><td class="pin-asset"><div class="asset-inline">${watchStar(a)}<span class="token-dot" style="background:${a.color}"></span><b>${a.id}</b></div></td>`;
 if(overviewMode==='decision'){
 s+=cell(fmt(p.price),a.market+' · 合成报价','number')+cell(`<span class="stage-pill ${p.kind}">4H ${p.stage}</span>`,`冻结箱体 ${fmt(p.low)}–${fmt(p.high)}，非实时规则结果`)+cell(o?money(o.usd):'—',ev.quality+'。'+ev.detail,'number')+cell(ev.coverageChanged?'暂停比较':pct(o?.q4),'同资产、同市场集合的数量变化，排除币价估值影响','number')+cell(fund?`${fund.name} ${signed(fund.rate,3)}/${fund.cycle}H`:'—','代表平台最新已结算单次费率；负数为空头支付，非预测费率','number')+cell(`<span class="evidence-chip ${ev.coverageChanged?'muted':''}">${ev.summary}</span>`,ev.detail)+cell(t.zone,'观察价不等于到价买入','number')+cell(t.condition,t.full)+cell(p.invalid?'已撤销':p.trigger?`4H < ${fmt(p.zoneLow)}`:'突破后启用',`完整4H收盘低于 ${fmt(p.zoneLow)} 撤销突破；箱体下沿失守另看 ${fmt(p.low)}`,'number');
 }else{
 s+=cell(o?money(o.usd):'—',ev.detail,'number')+cell(ev.coverageChanged?'暂停':pct(o?.h1),'持仓数量1H','number')+cell(ev.coverageChanged?'暂停':pct(o?.q4),'持仓数量4H','number')+cell(ev.coverageChanged?'暂停':pct(o?.h24),'持仓数量24H','number')+cell(ev.coverageChanged?'暂停':pct(o?.usd4),'数量变化和币价重估共同影响美元金额','number')+cell(o?(o.usd/a.cap*100).toFixed(2)+'%':'—','合成同期流通市值 '+money(a.cap)+'；不是空头占比或保证金','number')+cell(o?`${o.top.name} ${(o.top.usd/o.usd*100).toFixed(0)}%`:'—','占已覆盖OI合计，不是全网份额')+cell(fund?`${fund.name} ${fundingCost(fund,4).count?fundingCost(fund,4).paid.toFixed(3)+'%':'未结算'}`:'—','4H窗口内已结算负费率支付项之和；固定美元名义本金参考，非账户实际亏损','number')+cell(a.id==='WIF'?'空头 $1.24M / 4H':'缺少完整证据','Binance 合成清算快照示例；存在采样缺口，不能等同全量清算')+cell(`<span class="quality-text">${ev.quality}</span>`,ev.detail);
 }
 return s+`<td class="open-cell"><button class="open-oi" data-open="${a.id}" aria-label="查看 ${a.id} 图表与证据">图表 ↗</button></td></tr>`;
};
const rawRender=render;
render=function(){rawRender();const t=document.querySelector('#results table');if(t){t.className='oi-table';const labels=overviewMode==='decision'?['代币','当前价','结构与阶段','持仓 OI · USD','数量 Δ · 4H','费率 · 平台/周期','合约判断','关注价 / 区间','等待什么确认','突破撤销条件','']:['代币','持仓 OI · USD','数量 · 1H','数量 · 4H','数量 · 24H','美元 · 4H','OI / 流通市值','主要持仓平台','空头付费 · 4H','已观测清算','数据覆盖',''];t.querySelector('thead').innerHTML='<tr>'+labels.map((x,i)=>`<th ${i===0?'class="pin-asset"':''}>${x}</th>`).join('')+'</tr>';if(state.sort==='oi')t.querySelector('tbody').append(...[...t.querySelectorAll('tbody tr')].sort((x,y)=>(oiEvidence(assets.find(a=>a.id===y.dataset.asset)).agg?.usd??-Infinity)-(oiEvidence(assets.find(a=>a.id===x.dataset.asset)).agg?.usd??-Infinity)));}
 $('viewMode').textContent=overviewMode==='decision'?'价格触发 + 合约证据':'同一资产 · 跨平台持仓';$('filterDescription').textContent=state.pool==='all'?'全部市场':state.pool==='chain'?'链上 Meme / FOMO':'币安生态';document.querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('active',b.dataset.view===overviewMode);b.setAttribute('aria-pressed',b.dataset.view===overviewMode)});lucide.createIcons();};
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{overviewMode=b.dataset.view;render()});
function venueTable(a,ev){if(!ev.agg)return `<p class="missing-note">${ev.detail} OI、费率和清算显示缺失；技术结构仍可独立观察。</p>`;return `<div class="venue-scroll"><table class="venue-table"><thead><tr><th>平台（演示）</th><th>OI / 占比</th><th>数量4H</th><th>已结算费率</th><th>空头支付4H / 24H</th><th>合约/index</th><th>数据年龄</th></tr></thead><tbody>${ev.agg.rows.map(r=>`<tr><td>${r.name}</td><td>${money(r.usd)} / ${(r.usd/ev.agg.usd*100).toFixed(1)}%</td><td>${signed(r.qChange)}</td><td>${signed(r.rate,3)} / ${r.cycle}H</td><td>${fundingCost(r,4).count?fundingCost(r,4).paid.toFixed(3)+'%':'未结算'} / ${fundingCost(r,24).paid.toFixed(3)}%</td><td>${signed(r.basis,2)}</td><td>${r.age}s</td></tr>`).join('')}</tbody></table></div><p class="evidence-note">窗口截止：2026-09-22 16:00 UTC。所有平台、挂牌、分布与费率均为合成场景。${ev.detail} 数量为已标准化的线性合约 base 数量；展示持仓不双算多空。未合并 CMC 与原生重复数据。</p>`;}
const chartOpenBase=openDetail;
openDetail=function(id){
 chartOpenBase(id);const a=assets.find(a=>a.id===id),p=episode(a),ev=oiEvidence(a),o=ev.agg,t=planText(a);
 $('detailSubtitle').textContent=`${a.market} · 4H 箱体 ${fmt(p.low)}–${fmt(p.high)} · ${p.stage} · 合成演示`;
 $('detailEvidence').innerHTML=`<div class="detail-headline ${p.kind}"><span>当前判断</span><h3>${p.stage}</h3><p>${ev.summary}</p></div><section><h3>位置与确认</h3><dl class="plan-grid"><dt>当前价</dt><dd>${fmt(p.price)}</dd><dt>关注区</dt><dd>${t.zone}</dd><dt>突破撤销</dt><dd>4H 收盘 &lt; ${fmt(p.zoneLow)}</dd><dt>箱体下沿失守</dt><dd>4H 收盘 &lt; ${fmt(p.low)}</dd></dl><p>${t.full}</p><p>${a.pool==='chain'?'链上池深度、供应与可卖出性待核，暂不生成参与候选。':'确认条件满足后仍须核对深度、滑点及数据时效。'}</p></section><section><h3>合约读数</h3><dl class="plan-grid"><dt>已覆盖 OI</dt><dd>${o?money(o.usd):'—'}</dd><dt>数量变化 / 4H</dt><dd>${ev.coverageChanged?'覆盖变化，暂停比较':pct(o?.q4)}</dd><dt>美元变化 / 4H</dt><dd>${ev.coverageChanged?'覆盖变化，暂停比较':pct(o?.usd4)}</dd><dt>OI / 流通市值</dt><dd>${o?(o.usd/a.cap*100).toFixed(2)+'%':'—'}</dd></dl><p>分母为合成同期流通市值 ${money(a.cap)}，不是 FDV。美元 OI 随价格重估；数量 OI 变化不说明主动多空方向。</p></section><section><h3>支持、反向与缺口</h3><p>${a.id==='WIF'?'演示已观测 Binance 空头清算 $1.24M / 4H；快照存在采样缺口，不能解释全部减仓。':ev.pressure&&ev.pressure.rate<0?'演示负费率提供空头费用压力线索，可能包含套保仓位；并未确认主动平空或强平。':'当前没有足够清算证据；不能根据 OI 推算爆空金额。'}</p><p>现货主动买卖差额、历史分位与地址集中度：本页未提供可核验证据。${p.invalid?'价格结构已失效，负费率不能恢复旧计划。':'需继续核实现货接力，不能把合约活跃等同真实现货需求。'}</p></section><section><h3>后续跟踪，不设涨幅上限</h3><p>1H 看回踩确认，4H 看已确认结构，1D 独立跟踪趋势。到达前高后继续观察突破与买盘，不把第一阻力当终点。日线不能用于挽救已失效的 4H 事件。</p><p><b>上方清算集中区：暂无可验证数据。</b>总 OI 无法推算唯一痛点；地址清算价与模型估算需单独标来源、范围和时间。</p></section>`;
 $('venueEvidence').innerHTML=venueTable(a,ev)+(o?`<details class="cost-help"><summary>数量、价格与持续付费的原始依据</summary><div class="venue-scroll"><table class="venue-table"><thead><tr><th>平台</th><th>数量 OI / 币</th><th>mark / index</th><th>1H / 4H / 24H 数量变化</th><th>负费率连续结算 / 覆盖</th><th>24H 空头净费用指数</th></tr></thead><tbody>${o.rows.map(r=>`<tr><td>${r.name}</td><td>${r.q.toLocaleString('en-US',{maximumFractionDigits:2})}</td><td>${fmt(r.mark)} / ${fmt(r.index)}</td><td>${signed(r.h1)} / ${signed(r.qChange)} / ${signed(r.h24)}</td><td>${r.rate<0?r.funding.length+' 次 / 24H窗口':'无连续负费率'}</td><td>${signed(fundingCost(r,24).net,3)}</td></tr>`).join('')}</tbody></table></div><p>正净费用表示参考空头支付，负值表示参考空头净收入。演示合约成交价与mark设为相同，真实接口须分别保存。历史分位与地址集中度尚未提供，不能从这些数值推算操盘概率。</p></details>`:'')+`<details class="cost-help"><summary>费用、价差与清算怎么读</summary><p>空头支付项逐次累计 max(−已结算费率, 0)，窗口为 (截止−4H, 截止]；不同结算周期不直接横比。正费率可抵扣净费用，固定币数实际费用还受每次标记价与仓位影响。这里是固定美元名义本金参考；无结算记录显示“未结算”，不是零费用。</p><p>合约/index = (合约成交价÷指数价−1)。标记价、指数价和现货是不同价格，强平按平台规则的 mark 触发。快照清算只说明已观测部分；上涨减仓也可能主动退出，OI净增也可能伴随清算。</p></details>`;
 $('eventTime').textContent=`事件周期固定 4H · ${p.trigger?'首次价格确认':'结构参考'} ${date(p.time)} UTC · 快照 09-22 16:00 UTC`;
 lucide.createIcons();
};
locate=async function(){if(!ready)return;try{const p=episode(activeAsset);await widget.activeChart().setVisibleRange({from:seedEvent(activeAsset).time-20*4*3600,to:anchor})}catch{notify('视窗定位未完成，请手动拖动图表')}};
$('locate').onclick=locate;
const chartNativeBase=nativeChart;
nativeChart=async function(version){await chartNativeBase(version);if(version!==dialogVersion||!ready||!widget)return;const a=activeAsset,p=episode(a),ch=widget.activeChart();try{
 await ch.createMultipointShape([{time:seedEvent(a).time-20*4*3600,price:p.high},{time:seedEvent(a).time,price:p.low}],{shape:'rectangle',lock:true,disableSelection:true,disableSave:true,overrides:{color:'#6e8dc1',backgroundColor:'#e6edfa',transparency:82,fillBackground:true}});
 for(const [price,text,color] of [[p.zoneHigh,'确认门槛 / 回踩带上沿','#6587bc'],[p.zoneLow,'4H 收盘突破撤销线','#cb956b']]){if(version!==dialogVersion||!widget)return;await ch.createShape({time:p.time,price},{shape:'horizontal_line',text,lock:true,disableSelection:true,disableSave:true,overrides:{linecolor:color,linestyle:2,linewidth:1,showLabel:false,showPrice:true}});}
 if(p.revoke||p.broken){const b=p.revoke||p.broken;await ch.createShape({time:b.time/1000,price:b.close},{shape:'vertical_line',text:p.broken?'箱体下沿失守':'突破撤销',lock:true,disableSave:true,overrides:{linecolor:'#bd6470',linewidth:1,showLabel:true}});}
 }catch{notify('部分图层未绘制，请核对右侧冻结点位')}};
portableChart=function(){const a=activeAsset,p=episode(a),all=bars(a,resolution),start=seedEvent(a).time-12*4*3600,d=all.filter(b=>b.time/1000>=start),w=880,h=390,left=20,right=116,top=30,bottom=50,lo=Math.min(p.low,...d.map(b=>b.low))*.995,hi=Math.max(p.high,...d.map(b=>b.high))*1.005,x=i=>left+i*(w-left-right)/d.length,y=v=>top+(hi-v)/(hi-lo)*(h-top-bottom);let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${a.id} 合成 K线与冻结箱体"><rect width="100%" height="100%" fill="white"/>`;for(const b of d){const i=d.indexOf(b),c=b.close>=b.open?'#218c77':'#c46776';svg+=`<path d="M${x(i)} ${y(b.high)}V${y(b.low)}" stroke="${c}"/><rect x="${x(i)-2}" y="${y(Math.max(b.open,b.close))}" width="4" height="${Math.max(1,Math.abs(y(b.open)-y(b.close)))}" fill="${c}"/>`;}
 for(const [v,label,c] of [[p.high,'箱体上沿','#6587bc'],[p.low,'箱体下沿','#c46776'],[p.zoneLow,'突破撤销','#cb956b']])svg+=`<path d="M${left} ${y(v)}H${w-right}" stroke="${c}" stroke-dasharray="5 4"/><text x="${w-right+6}" y="${y(v)-4}" fill="${c}" font-size="10">${label} ${fmt(v)}</text>`;
 svg+=`<text x="20" y="375" fill="#7c879a" font-size="12">${date(d[0].time/1000)} → ${date(anchor)} UTC · 原事件4H · ${resolution==='1D'?'1D':Number(resolution)/60+'H'}查看</text></svg>`;$('portableChart').innerHTML=svg;};
$('helpRules').onclick=()=>$('rulesDialog').showModal();$('closeRules').onclick=()=>$('rulesDialog').close();
render();
