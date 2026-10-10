const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1740,height:1160}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{let library;Object.defineProperty(window,'TradingView',{configurable:true,get:()=>library,set:value=>{
   if(value?.widget){const original=value.widget;value.widget=new Proxy(original,{construct(target,args){const instance=Reflect.construct(target,args);window.__p34StudyChart=instance;return instance;}});}library=value;
  }});});
  await page.route('**/v1/preferences/**',r=>r.fulfill({json:r.request().method()==='PUT'?{saved:true}:{payload:{resolution:'240',preset:'technical',layers:{structure:true,ema:true,macd:true}}}}));
  const base='http://127.0.0.1:18770',report=JSON.parse(await fs.readFile('docs/p34-validation/browser.json','utf8'));
  const iid=report.verified.instrument_id;
  const instruments=(await(await page.request.get(base+'/v1/instruments')).json()).rows;
  const asset=instruments.find(i=>i.id===iid).asset_id;
  await page.goto(base+'/assets/'+encodeURIComponent(asset)+'?instrument='+encodeURIComponent(iid)+'&episode='+report.verified.event_id);
  await page.locator('.chart-root iframe').waitFor();
  await page.locator('.chart-loading').waitFor({state:'detached',timeout:30000});
  await page.waitForFunction(()=>window.__p34StudyChart?.activeChart().getAllStudies().length>=3);
  await page.waitForFunction(()=>window.__p34StudyChart?.activeChart().dataReady());
  await page.waitForTimeout(8000);
  let exported,source,checked=0;
  for(const pass of [0,1]){
  if(pass)await page.waitForTimeout(16000); // exercise the automatic analysis refresh
  exported=await page.evaluate(()=>window.__p34StudyChart.activeChart().exportData({includeTime:true,includeSeries:true}));
  const end=(Math.max(...exported.data.map(r=>r[0]))+14400)*1000;
  source=await(await page.request.get(base+'/v1/analysis/series?instrument='+encodeURIComponent(iid)+'&resolution=240&before='+end+'&limit=1500')).json();
  const lookup=new Map(source.rows.map(r=>[r.time,r]));
  const fields={EMA20:'ema20',EMA50:'ema50',MACD:'macd',Signal:'signal',Histogram:'histogram'};
  for(const [column,definition] of exported.schema.entries()){
   const key=fields[definition.plotTitle];if(!key)continue;
   for(const row of exported.data){
    if(!Number.isFinite(row[column]))continue;
    const expected=lookup.get(row[0]*1000)?.[key];
    assert.ok(expected!=null,JSON.stringify({time:row[0],key,actual:row[column],expected,source_status:source.status}));
    assert.ok(Math.abs(row[column]-Number(expected))<=Math.max(1e-12,Math.abs(Number(expected))*1e-10),JSON.stringify({time:row[0],key,actual:row[column],expected}));checked++;
   }
  }
  }
  assert.ok(checked>500,'verify populated study data, not only presence of a panel');assert.equal(errors.length,0);
  await page.locator('.pd2-chart').screenshot({path:'docs/p34-validation/study-loaded.png'});
  await fs.writeFile('docs/p34-validation/study-export.json',JSON.stringify({as_of:new Date().toISOString(),instrument:iid,errors,refresh_passes:2,checked_values:checked,source_revision:source.series_revision,exported},null,2));
  console.log(JSON.stringify({errors,checked_values:checked,rows:exported.data?.length}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
