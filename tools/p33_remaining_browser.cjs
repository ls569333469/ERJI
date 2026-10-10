// Controlled fixtures are intercepted only in this browser, never stored by the app.
const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
 const dir='docs/p33-validation/remaining-20261002',base='http://127.0.0.1:18770';
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1740,height:1160}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{
  let library;
  Object.defineProperty(window,'TradingView',{configurable:true,get:()=>library,set:value=>{
   if(value?.widget){const original=value.widget;value.widget=new Proxy(original,{construct(target,args){
    const instance=Reflect.construct(target,args);window.__p33Chart=instance;return instance;
   }});}library=value;
  }});
 });
 await page.goto(base+'/signals');
 await page.locator('h1').waitFor();
 const actual=await(await page.request.get(base+'/v1/signals?view=opportunities')).json();
 await page.screenshot({path:dir+'/live-signals.png',fullPage:true});
 const report={live:{rows:actual.rows.length,total:actual.pagination.total,assets:actual.rows.map(r=>r.asset_id)},controlled:[],errors};
 const fixtures=JSON.parse(await fs.readFile(dir+'/chart-fixtures.json','utf8'));let chosen;
 await page.route('**/v1/**',async route=>{
  const u=new URL(route.request().url()),path=u.pathname.replace(/^.*\/v1/,'');let data;
  if(path.startsWith('/preferences/'))data=route.request().method()==='PUT'?{saved:true}:{payload:{resolution:'240',preset:'technical',layers:{structure:true,targets:true,ema:false,macd:false,fib:true}}};
  else if(path==='/research/events')data={rows:Object.entries(fixtures).map(([rule,f])=>({snapshot_id:f.snapshot.id,instrument_id:f.snapshot.instrument_id,event_id:f.snapshot.event_id,label:'算法验收样例 · '+rule,as_of:f.snapshot.as_of,status:'triggered'}))};
  else if(path==='/chart/snapshot'){chosen=Object.values(fixtures).find(f=>f.snapshot.id===u.searchParams.get('snapshot_id'))?.snapshot;data={snapshot:chosen};}
  else if(path==='/instruments'&&chosen)data={rows:[{id:chosen.instrument_id,native_symbol:'算法验收样例',venue:'test',spec:{tick_size:'0.001'}}]};
  else if(path==='/analysis'&&chosen)data={instrument_id:chosen.instrument_id,as_of:chosen.as_of,timeframes:chosen.timeframe_assessments,opportunity:chosen.opportunity_plan};
  else if(path==='/derivatives/history'&&chosen)data=chosen.derivative_history_at_snapshot;
  else if(path==='/analysis/series'&&chosen){const tf=u.searchParams.get('resolution'),all=chosen.timeframe_assessments.find(a=>a.timeframe===tf)?.series||[];data={instrument_id:chosen.instrument_id,timeframe:tf,snapshot_id:chosen.id,series_revision:chosen.series_revision,rows:all.filter(b=>b.time<Number(u.searchParams.get('before'))).slice(-Number(u.searchParams.get('limit'))),status:'historical_frozen'};}
  else if(path==='/candles'&&chosen){const tf=u.searchParams.get('resolution'),all=chosen.candles_at_snapshot[tf]||[];data={rows:all.filter(b=>b.open_time<Number(u.searchParams.get('before')||Infinity)).slice(-Number(u.searchParams.get('limit')||300))};}
  if(data!==undefined)return route.fulfill({json:data});return route.fallback();
 });
 await page.goto(base+'/operations');await page.locator('summary').filter({hasText:'新策略验证记录'}).click();
 for(const [rule,f] of Object.entries(fixtures)){
  await page.getByLabel('选择研究事件').selectOption(f.snapshot.id);
  await page.getByRole('button',{name:'查看该事件K线',exact:true}).click();
  await page.locator('.chart-root iframe').waitFor({timeout:30000});
  await page.locator('.chart-loading').waitFor({state:'detached',timeout:30000});
  await page.waitForFunction(()=>window.__p33Chart?.activeChart().dataReady());
  // Wait for the asynchronous drawing pass, not merely the iframe.
  await page.waitForFunction(()=>window.__p33Chart.activeChart().getAllShapes().length>0);
  await page.waitForFunction(()=>document.body.textContent.includes('确认K线'));
  if(rule==='fib_c_and_protection_steps'){
   await page.locator('.chart-layer-menu').evaluate(e=>e.open=true);
   await page.getByLabel('事件斐波参考',{exact:true}).check();
   await page.waitForFunction(()=>document.body.textContent.includes('Fib C'));
  }
  const shapes=await page.evaluate(()=>{const c=window.__p33Chart.activeChart();return c.getAllShapes().map(s=>({name:s.name,points:c.getShapeById(s.id).getPoints()}));});
  const tolerance=1e-8;
  function point(price,time,name){return shapes.some(s=>(!name||s.name===name)&&s.points.some(p=>Math.abs(p.price-Number(price))<tolerance&&p.time===time/1000));}
  const checked=[];
  for(const a of f.snapshot.drawing_contract.anchors){
   if(a.kind==='boundary'){assert.ok(point(a.price,a.known_at,'horizontal_ray'),rule+' boundary');checked.push(a);}
   if(a.kind==='pivot'){assert.ok(point(a.price,a.time,'icon'),rule+' pivot');checked.push(a);}
   if(a.kind==='trigger_bar'){
    const frame=f.snapshot.drawing_contract.timeframe,step=frame==='1D'?86400000:frame==='1W'?604800000:Number(frame)*60000;
    if(step<=a.close_time-a.open_time)assert.ok(point(a.price,a.open_time,'arrow_up'),rule+' trigger');
    else assert.equal(shapes.some(s=>s.name==='arrow_up'),false,'higher timeframe must not invent an intrabar trigger');
    checked.push(a);
   }
  }
  if(rule==='fib_c_and_protection_steps'){
   const fib=f.snapshot.event_overlays.fibonacci;
   for(const a of fib.anchors)assert.ok(point(a.price,a.pivot_time,'icon'),'Fib anchor '+a.price);
   for(const p of fib.levels)assert.ok(point(p.price,p.known_at,'horizontal_ray'),'Fib level '+p.price);
   const trail=f.snapshot.drawing_contract.protection_history;
   for(let i=0;i<trail.length;i++){
    const a=trail[i],until=trail[i+1]?.observed_at||f.snapshot.as_of;
    assert.ok(shapes.some(s=>s.name==='trend_line'&&s.points.length===2&&s.points[0].time===a.observed_at/1000&&s.points[1].time===until/1000&&s.points.every(p=>Math.abs(p.price-Number(a.price))<tolerance)),'protection segment '+i);
   }
   checked.push({fib,trail});
   await page.locator('.chart-level-details').evaluate(e=>e.open=true);
   await page.locator('.pd2-chart').screenshot({path:dir+'/controlled-fib-protection.png'});
  }
  const chartErrors=await page.locator('.chart-error').allTextContents();assert.equal(chartErrors.length,0,rule);
  report.controlled.push({rule,kind:f.kind,checked,shapes,chartErrors});
 }
 await fs.writeFile(dir+'/browser.json',JSON.stringify(report,null,2));
 await browser.close();console.log(JSON.stringify({live:report.live,controlled:report.controlled.length,errors}));assert.equal(errors.length,0);
})().catch(e=>{console.error(e);process.exit(1)});
