const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
 const dir='docs/p34-validation',base='http://127.0.0.1:18770';
 const audit=JSON.parse(await fs.readFile('docs/p33-validation/box-integrity-20261002/audit.json','utf8'));
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1740,height:1160}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{
  let library;
  Object.defineProperty(window,'TradingView',{configurable:true,get:()=>library,set:value=>{
   if(value?.widget){const original=value.widget;value.widget=new Proxy(original,{construct(target,args){
    const instance=Reflect.construct(target,args);window.__p33Chart=instance;return instance;
   }});}
   library=value;
  }});
 });
 // Only isolate browser preferences; all market data comes from the real API.
 await page.route('**/v1/preferences/**',r=>r.fulfill({json:r.request().method()==='PUT'?{saved:true}:{payload:{resolution:'240',preset:'technical',layers:{structure:true,targets:true,ema:true,macd:true}}}}));
 const report={errors,rejected:null,verified:null};
 const instruments=(await(await page.request.get(base+'/v1/instruments')).json()).rows;
 const rejected=audit.rows.find(r=>r.instrument_id==='binance_spot:spot:TUTUSDT'&&r.quality.status==='rejected')||audit.rows.find(r=>r.instrument_id.includes('TUT')&&r.quality.status==='rejected');
 assert.ok(rejected);
 async function open(row){
  const market=instruments.find(i=>i.id===row.instrument_id);assert.ok(market);
  await page.goto(base+'/assets/'+encodeURIComponent(market.asset_id)+'?instrument='+encodeURIComponent(row.instrument_id)+'&episode='+row.event_id);
  await page.locator('.chart-root iframe').waitFor({timeout:30000});
  await page.locator('.chart-loading').waitFor({state:'detached',timeout:30000});
  await page.waitForFunction(()=>window.__p33Chart?.activeChart().dataReady());
 }
 await open(rejected);
 await page.getByText('整理区间含明显越界行情，不能作为完整待突破箱体',{exact:true}).waitFor({timeout:30000});
 const rejectedApi=await(await page.request.get(base+'/v1/structure-signals/'+rejected.event_id)).json();
 assert.equal(rejectedApi.drawing_contract,null);
 await page.locator('.pd2-chart').screenshot({path:dir+'/TUT-rejected.png'});
 report.rejected={event_id:rejected.event_id,phase:rejectedApi.phase,quality:rejectedApi.candidate_quality,chart_errors:await page.locator('.chart-error').allTextContents()};
 let valid,contract;
 for(const row of audit.rows.filter(r=>r.quality.status==='verified')){
  const data=await(await page.request.get(base+'/v1/structure-signals/'+row.event_id)).json();
  if(data.drawing_contract?.status==='candidate'){valid=row;contract=data.drawing_contract;break;}
 }
 assert.ok(valid,'at least one real verified candidate');
 await open(valid);
 await page.locator('.chart-caption').filter({hasText:'候选箱体'}).waitFor({timeout:30000});
 await page.locator('.chart-level-details').evaluate(e=>e.open=true);
 await page.waitForFunction(()=>window.__p33Chart.activeChart().getAllShapes().some(s=>s.name==='rectangle'));
 const box=contract.anchors.find(a=>a.kind==='box');
 const pointChecks=[];
 for(const resolution of ['240','60','1D','1W']){
  await page.evaluate(r=>new Promise(resolve=>window.__p33Chart.activeChart().setResolution(r,resolve)),resolution);
  await page.waitForFunction(()=>window.__p33Chart.activeChart().dataReady());
  await page.waitForFunction(()=>window.__p33Chart.activeChart().getAllShapes().some(s=>s.name==='rectangle')||document.body.textContent.includes('请切换来源4H查看完整结构'));
  const shapes=await page.evaluate(()=>{
   const c=window.__p33Chart.activeChart();return c.getAllShapes().filter(s=>s.name==='rectangle').map(s=>({name:s.name,points:c.getShapeById(s.id).getPoints()}));
  });
  const rectangle=shapes.find(s=>Math.abs(s.points[0].price-Number(box.upper))<1e-9&&Math.abs(s.points[1].price-Number(box.lower))<1e-9);
  if(!rectangle){
   assert.notEqual(resolution,'240','source timeframe must have exact boundaries');
   assert.ok((await page.locator('.pd2-chart').innerText()).includes('请切换来源4H查看完整结构'));
   pointChecks.push({resolution,status:'range_unrepresentable_no_fake_rectangle'});
   await page.locator('.pd2-chart').screenshot({path:dir+'/verified-'+resolution+'.png'});continue;
  }
  if(resolution==='240'||resolution==='60'){
   assert.equal(rectangle.points[0].time,box.start_time/1000);
   assert.equal(rectangle.points[1].time,box.last_open_time/1000);
  }
  pointChecks.push({resolution,points:rectangle.points});
  await page.locator('.pd2-chart').screenshot({path:dir+'/verified-'+resolution+'.png'});
 }
 report.verified={event_id:valid.event_id,instrument_id:valid.instrument_id,box,pointChecks,chart_errors:await page.locator('.chart-error').allTextContents()};
 await fs.writeFile(dir+'/browser.json',JSON.stringify(report,null,2));
 await browser.close();console.log(JSON.stringify(report));assert.equal(errors.length,0);
})().catch(e=>{console.error(e);process.exit(1)});
