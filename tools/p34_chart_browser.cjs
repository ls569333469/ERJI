// Actual frozen research snapshots only; no generated market/strategy values.
const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
 const dir='docs/p34-validation',base='http://127.0.0.1:18770';
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1740,height:1160}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('console',m=>{if(m.type()==='error')console.log(m.text());});
 await page.addInitScript(()=>{let library;Object.defineProperty(window,'TradingView',{configurable:true,get:()=>library,set:value=>{
  if(value?.widget){const original=value.widget;value.widget=new Proxy(original,{construct(target,args){const instance=Reflect.construct(target,args);window.__p33Chart=instance;return instance;}});}library=value;
 }});});
 const events=await(await page.request.get(base+'/v1/research/events?limit=100')).json();
 const prior=JSON.parse(await fs.readFile('docs/p33-validation/remaining-20261002/real-chart-matrix.json','utf8'));
 // A bounded newest-events endpoint can age out a rare strategy. Reuse its
 // retained immutable ID; each selected snapshot is still fetched and verified.
 const selected=[...new Map([...prior.rows,...events.rows].map(e=>[e.label,e])).values()];
 // Freeze only the selection menu to these exact API records, while the worker keeps collecting.
 await page.route('**/v1/research/events',route=>route.fulfill({json:{...events,rows:selected}}));
 await page.route('**/v1/preferences/**',r=>r.fulfill({json:r.request().method()==='PUT'?{saved:true}:{payload:{preset:'technical',layers:{structure:true,targets:true}}}}));
 await page.goto(base+'/operations');await page.locator('summary').filter({hasText:'新策略验证记录'}).click();
 const report={as_of:new Date().toISOString(),source:'local API immutable real research snapshots',rows:[],errors};
 for(const row of selected){
  const {snapshot:snap}=await(await page.request.get(base+'/v1/chart/snapshot?instrument='+encodeURIComponent(row.instrument_id)+'&snapshot_id='+row.snapshot_id)).json();
  assert.equal(snap.id,row.snapshot_id);
  await page.getByLabel('选择研究事件').selectOption(row.snapshot_id);await page.getByRole('button',{name:'查看该事件K线',exact:true}).click();
  await page.locator('.chart-root iframe').waitFor({timeout:30000});await page.locator('.chart-loading').waitFor({state:'detached',timeout:30000});
  await page.waitForFunction(()=>window.__p33Chart?.activeChart().dataReady());
  await page.waitForFunction(()=>document.querySelector('.chart-level-details tbody')?.children.length>0).catch(async e=>{
   await page.screenshot({path:dir+'/chart-failure.png',fullPage:true});
   console.log((await page.locator('.pd2-chart').innerText()).slice(0,2200));throw e;
  });
  const actual=await page.evaluate(()=>{const c=window.__p33Chart.activeChart();return {resolution:c.resolution(),shapes:c.getAllShapes().map(s=>({name:s.name,points:c.getShapeById(s.id).getPoints()}))};});
  const checked=[];
  for(const a of snap.drawing_contract.anchors){
   if(a.kind!=='boundary'&&a.kind!=='pivot')continue;
   const name=a.kind==='boundary'?'horizontal_ray':'icon',at=a.kind==='boundary'?a.known_at:a.time;
   // TradingView places known-at timestamps inside their source candle slot.
   const step=actual.resolution==='1D'?86400000:actual.resolution==='1W'?604800000:Number(actual.resolution)*60000;
   const offset=actual.resolution==='1W'?345600000:0,slot=Math.floor((at-offset)/step)*step+offset;
   const found=actual.shapes.find(s=>s.name===name&&s.points.some(p=>Math.abs(p.price-Number(a.price))<Math.max(1e-10,Math.abs(Number(a.price))*1e-10)&&Math.abs(p.time-slot/1000)<.001));
   assert.ok(found,row.label+': '+a.kind+' missing/misaligned');checked.push({anchor:a,actual:found});
  }
  const chartErrors=await page.locator('.chart-error').allTextContents();assert.equal(chartErrors.length,0);
  await page.locator('.pd2-chart').screenshot({path:dir+'/real-'+snap.strategy_id+'.png'});
  report.rows.push({...row,strategy_id:snap.strategy_id,resolution:actual.resolution,checked,chartErrors});
 }
 await fs.writeFile(dir+'/real-chart-matrix.json',JSON.stringify(report,null,2));await browser.close();
 console.log(JSON.stringify({real_checked:report.rows.map(r=>r.strategy_id),errors}));assert.equal(errors.length,0);
})().catch(e=>{console.error(e);process.exit(1)});
