// Browser-only checks. ATH is explicitly a drawing fixture, never a stored signal.
const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
 const dir='docs/p35-validation',base='http://127.0.0.1:18770',browser=await chromium.launch({channel:'msedge',headless:true});
 const report={as_of:new Date().toISOString(),checks:[],errors:[]};
 try{
  const page=await browser.newPage({viewport:{width:1740,height:1160}});
  page.on('pageerror',e=>report.errors.push(e.message));
  await page.addInitScript(()=>{let lib;Object.defineProperty(window,'TradingView',{configurable:true,get:()=>lib,set:value=>{if(value?.widget){const ctor=value.widget;value.widget=new Proxy(ctor,{construct(t,a){const i=Reflect.construct(t,a);window.__chart=i;return i;}});}lib=value;}});});
  let prefs={};await page.route('**/v1/preferences/**',async r=>{if(r.request().url().endsWith('/chart')&&r.request().method()==='PUT')prefs=r.request().postDataJSON().payload;await r.fulfill({json:r.request().method()==='PUT'?{saved:true}:{payload:r.request().url().endsWith('/chart')?prefs:{}}});});
  const payload=await(await page.request.get(base+'/v1/signals?view=opportunities&page_size=300')).json();
  const row=payload.rows.find(r=>r.id.startsWith('box-'));assert.ok(row);
  await page.goto(base+'/assets/'+encodeURIComponent(row.asset_id)+'?instrument='+encodeURIComponent(row.instrument_id)+'&episode='+row.id);
  const ready=async()=>{await page.locator('.chart-root iframe').waitFor({timeout:30000});await page.locator('.chart-loading').waitFor({state:'detached',timeout:30000});await page.waitForFunction(()=>window.__chart?.activeChart().dataReady());};
  await ready();await page.waitForFunction(()=>window.__chart.activeChart().getAllShapes().length>0);
  await page.evaluate(()=>window.__chart.activeChart().setResolution('1D'));
  await page.waitForFunction(()=>window.__chart.activeChart().resolution()==='1D'&&document.body.textContent.includes('所选结构来自'));
  await page.reload();await ready();assert.equal(await page.evaluate(()=>window.__chart.activeChart().resolution()),'1D');
  report.checks.push({name:'explicit_daily_resolution_survives_reload',passed:true});
  await page.getByRole('button',{name:'返回来源周期'}).click();await page.waitForFunction(()=>window.__chart.activeChart().getAllShapes().length>0&&window.__chart.activeChart().dataReady()&&!document.querySelector('.chart-loading'));
  await page.waitForTimeout(1500);
  const counts=[];
  for(let n=0;n<3;n++){
   await page.evaluate(factor=>{const c=window.__chart.activeChart(),r=c.getVisibleRange();return c.setVisibleRange({from:r.to-(r.to-r.from)*factor,to:r.to});},n===1?2:.75);
   await page.waitForTimeout(3200);
   counts.push(await page.evaluate(()=>window.__chart.activeChart().getAllShapes().length));
  }
  await page.locator('.pd2-chart').screenshot({path:dir+'/zoomed-live.png'});
  assert.ok(Math.max(...counts)-Math.min(...counts)<=10,'no accumulating duplicated layers');
  await page.locator('summary').filter({hasText:'图表设置'}).click();await page.getByLabel('历史结构（最多3个）',{exact:true}).check();await page.locator('summary').filter({hasText:'图表设置'}).click();
  await page.waitForTimeout(3500);const historyCount=await page.evaluate(()=>window.__chart.activeChart().getAllShapes().length);
  await page.locator('summary').filter({hasText:'图表设置'}).click();await page.getByLabel('历史结构（最多3个）',{exact:true}).uncheck();await page.locator('summary').filter({hasText:'图表设置'}).click();
  await page.context().setOffline(true);await page.waitForTimeout(6000);await page.context().setOffline(false);await page.waitForTimeout(6500);
  assert.equal((await page.request.get(base+'/v1/health')).status(),200);assert.equal(await page.locator('.chart-error').count(),0);
  report.checks.push({name:'zoom_history_toggle_short_disconnect_recovery',passed:true,counts,historyCount,scope:'short reconnect and several redraws; not a multi-day soak'});
  // Compare exactly the same frozen event that was captured for P34.
  const prior=JSON.parse(await fs.readFile('docs/p34-validation/real-chart-matrix.json','utf8'));
  const original=prior.rows.find(r=>r.strategy_id==='trend_pullback');assert.ok(original);
  await page.route('**/v1/research/events',r=>r.fulfill({json:{rows:[original]}}));prefs={};
  await page.goto(base+'/operations');await page.locator('summary').filter({hasText:'新策略验证记录'}).click();await page.getByLabel('选择研究事件').selectOption(original.snapshot_id);await page.getByRole('button',{name:'查看该事件K线',exact:true}).click();await ready();await page.waitForFunction(()=>window.__chart.activeChart().getAllShapes().length>0);
  await page.locator('.pd2-chart').screenshot({path:dir+'/same-event-after.png'});
  report.same_event={...original,before:'../p34-validation/real-trend_pullback.png',after:'same-event-after.png'};
  // Reuse real candles/anchor coordinates; only the browser drawing contract is synthetic.
  const high=prior.rows.find(r=>r.strategy_id==='recent_high');assert.ok(high);
  const response=await(await page.request.get(base+'/v1/chart/snapshot?instrument='+encodeURIComponent(high.instrument_id)+'&snapshot_id='+high.snapshot_id)).json();
  const fixture=structuredClone(response);fixture.snapshot.strategy_id='ath';fixture.snapshot.strategy_label='ATH 构造绘图检查';fixture.snapshot.drawing_contract.strategy_id='ath';fixture.snapshot.action={...fixture.snapshot.action,label:'构造样例',reason:'仅检查 ATH 图层；复用历史前高真实K线与价位，不代表真实ATH识别或当前信号。'};
  for(const a of fixture.snapshot.drawing_contract.anchors)if(a.kind==='boundary')a.label='构造ATH参考位';
  await page.route('**/v1/chart/snapshot?*',async r=>{if(new URL(r.request().url()).searchParams.get('snapshot_id')===high.snapshot_id)await r.fulfill({json:fixture});else await r.continue();});
  await page.unroute('**/v1/research/events');await page.route('**/v1/research/events',r=>r.fulfill({json:{rows:[high]}}));
  prefs={};await page.goto(base+'/operations');await page.locator('summary').filter({hasText:'新策略验证记录'}).click();await page.getByLabel('选择研究事件').selectOption(high.snapshot_id);await page.getByRole('button',{name:'查看该事件K线',exact:true}).click();await ready();await page.waitForFunction(()=>window.__chart.activeChart().getAllShapes().length>0);
  assert.ok((await page.locator('.chart-decision').innerText()).includes('构造样例'));
  const anchor=fixture.snapshot.drawing_contract.anchors.find(a=>a.kind==='boundary');
  const prices=await page.evaluate(()=>{const c=window.__chart.activeChart();return c.getAllShapes().flatMap(s=>c.getShapeById(s.id).getPoints().map(p=>p.price));});
  assert.ok(prices.some(p=>Math.abs(p-Number(anchor.price))<Math.max(1e-10,Number(anchor.price)*1e-10)));
  await page.locator('.pd2-chart').screenshot({path:dir+'/ath-drawing-fixture.png'});
  report.ath_fixture={kind:'browser-only constructed drawing contract; real historical candles',source_snapshot:high.snapshot_id,price:anchor.price,no_database_write:true,passed:true};
  assert.deepEqual(report.errors,[]);
 }finally{await fs.writeFile(dir+'/chart-final.json',JSON.stringify(report,null,2));await browser.close();}
 console.log(JSON.stringify(report));
})().catch(e=>{console.error(e);process.exit(1)});
