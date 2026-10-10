const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
 const dir='docs/p35-validation',base='http://127.0.0.1:18770';const b=await chromium.launch({channel:'msedge',headless:true});
 try{const page=await b.newPage({viewport:{width:1740,height:1160}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{let library;Object.defineProperty(window,'TradingView',{configurable:true,get:()=>library,set:value=>{if(value?.widget){const ctor=value.widget;value.widget=new Proxy(ctor,{construct(target,args){const instance=Reflect.construct(target,args);window.__chart=instance;return instance;}});}library=value;}});});
 let prefs={};await page.route('**/v1/preferences/**',async route=>{if(route.request().url().endsWith('/chart')&&route.request().method()==='PUT')prefs=route.request().postDataJSON().payload;await route.fulfill({json:route.request().method()==='PUT'?{saved:true}:{payload:route.request().url().endsWith('/chart')?prefs:{}}});});
 await page.goto(base+'/signals');await page.locator('.opportunity-table').waitFor({timeout:30000});await page.screenshot({path:dir+'/signals-live.png',fullPage:true});
 const latencies=[];let payload;for(let n=0;n<4;n++){const at=Date.now();payload=await(await page.request.get(base+'/v1/signals?view=opportunities&page_size=300')).json();latencies.push(Date.now()-at);}
 const records=[];
 for(const row of [payload.rows.find(r=>r.id.startsWith('box-')),payload.rows.find(r=>r.id.startsWith('p35-candidate-'))].filter(Boolean)){
  await page.goto(base+'/assets/'+encodeURIComponent(row.asset_id)+'?instrument='+encodeURIComponent(row.instrument_id)+'&episode='+row.id);
  await page.locator('.chart-root iframe').waitFor({timeout:30000});await page.locator('.chart-loading').waitFor({state:'detached',timeout:30000});
  await page.waitForFunction(()=>window.__chart?.activeChart().getAllShapes().length>0);
  const current=await page.evaluate(()=>{const c=window.__chart.activeChart();return {resolution:c.resolution(),ids:c.getAllShapes().map(s=>s.id),shapes:c.getAllShapes().map(s=>({name:s.name,points:c.getShapeById(s.id).getPoints()})),range:c.getVisibleRange()}});
  await page.locator('.pd2-chart').screenshot({path:dir+'/live-'+(row.strategy_id||'box')+'.png'});
  await page.locator('summary').filter({hasText:'图表设置'}).click();await page.getByLabel('EMA20/50',{exact:true}).check();await page.getByLabel('详细标签',{exact:true}).check();
  await page.locator('summary').filter({hasText:'图表设置'}).click();
  await page.evaluate(()=>window.__chart.activeChart().setResolution('1D'));await page.waitForFunction(()=>document.body.textContent.includes('所选结构来自'));
  const day=await page.evaluate(()=>window.__chart.activeChart().getAllShapes().length);assert.equal(day,0,'default cross-timeframe shape must be hidden');
  await page.getByRole('button',{name:'返回来源周期'}).click();await page.waitForFunction(()=>window.__chart.activeChart().getAllShapes().length>0);
  await page.locator('summary').filter({hasText:'图表设置'}).click();await page.locator('label').filter({hasText:'主题'}).locator('select').selectOption('dark');await page.locator('summary').filter({hasText:'图表设置'}).click();
  await page.waitForFunction(()=>!document.querySelector('.chart-loading'));await page.locator('.pd2-chart').screenshot({path:dir+'/dark-'+(row.strategy_id||'box')+'.png'});
  await page.reload();await page.locator('.chart-loading').waitFor({state:'detached',timeout:30000});await page.locator('summary').filter({hasText:'图表设置'}).click();assert.equal(await page.getByLabel('EMA20/50',{exact:true}).isChecked(),true);assert.equal(await page.getByLabel('详细标签',{exact:true}).isChecked(),true);
  await page.locator('summary').filter({hasText:'图表设置'}).click();
  records.push({id:row.id,instrument_id:row.instrument_id,strategy:row.strategy_id,current,hidden_on_daily:day,persistence:true,chart_errors:await page.locator('.chart-error').allTextContents()});
  prefs={};
 }
 await page.goto(base+'/operations');await page.getByText('采集容量、排队与降载',{exact:true}).click();await page.screenshot({path:dir+'/operations-live.png',fullPage:true});
 await page.goto(base+'/settings');await page.getByText('启用价格形态策略',{exact:true}).waitFor();assert.equal(await page.getByText('最低成熟样本',{exact:true}).count(),0);await page.screenshot({path:dir+'/settings-live.png',fullPage:true});
 const templates=await(await page.request.get(base+'/v1/strategy-templates')).json();assert.equal(templates.rows.length,10);for(const t of templates.rows)assert.equal((await page.request.get(base+'/v1/strategy-templates/'+t.id+'/document')).status(),200);
 const report={as_of:new Date().toISOString(),errors,latencies,counts:payload.opportunity_counts,records,templates:templates.rows.length};await fs.writeFile(dir+'/live-browser.json',JSON.stringify(report,null,2));console.log(JSON.stringify({errors,latencies,records:records.length,templates:templates.rows.length}));assert.deepEqual(errors,[]);
 }finally{await b.close();}
})().catch(e=>{console.error(e);process.exit(1)});
