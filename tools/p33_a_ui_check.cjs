const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises');const assert=require('node:assert/strict');
(async()=>{
 const out='docs/p33-validation/a-ui-20261002';await fs.mkdir(out,{recursive:true});
 const b=await chromium.launch({channel:'msedge',headless:true});const p=await b.newPage({viewport:{width:1600,height:1000}});const errors=[];p.on('pageerror',e=>errors.push(e.message));
 const base='http://127.0.0.1:18770';
 await p.route('**/v1/preferences/chart',async r=>r.fulfill({json:r.request().method()==='PUT'?{saved:true}:{payload:{resolution:'240',preset:'technical',layers:{structure:true,targets:true,ema:false,macd:false}}}}));
 await p.goto(base+'/signals');await p.locator('.opportunity-table').first().waitFor();
 const rows=await(await p.request.get(base+'/v1/signals?view=opportunities&page_size=300')).json();
 const width=await p.locator('.opportunity-table-scroll').first().evaluate(el=>({client:el.clientWidth,scroll:el.scrollWidth}));assert(width.scroll<=width.client+1);
 await p.screenshot({path:out+'/signals.png',fullPage:true});
 await p.getByRole('button',{name:'展开',exact:true}).first().click();await p.getByRole('tab',{name:'机会详情',exact:true}).waitFor();
 await p.screenshot({path:out+'/detail.png',fullPage:true});
 await p.getByRole('tab',{name:'策略与案例',exact:true}).click();await p.getByRole('button',{name:'刷新机会'}).click();
 await p.waitForFunction(()=>!document.querySelector('[aria-label="刷新机会"]').disabled);
 assert.equal(await p.getByRole('tab',{name:'策略与案例',exact:true}).getAttribute('aria-selected'),'true');
 await p.getByRole('tab',{name:'K线与指标',exact:true}).click();await p.locator('.chart-root iframe').waitFor({timeout:30000});await p.locator('.chart-loading').waitFor({state:'detached',timeout:30000});
 const frame=await p.locator('.chart-root iframe').elementHandle();await p.getByRole('button',{name:'刷新机会'}).click();await p.waitForFunction(()=>!document.querySelector('[aria-label="刷新机会"]').disabled);
 assert(await frame.evaluate(el=>el.isConnected));
 await p.getByRole('tab',{name:'机会详情',exact:true}).click();await p.getByRole('tab',{name:'K线与指标',exact:true}).click();assert(await frame.evaluate(el=>el.isConnected));
 const candidateChartErrors=await p.locator('.chart-error').allTextContents();await p.screenshot({path:out+'/candidate-chart.png',fullPage:true});
 const history=await(await p.request.get(base+'/v1/history')).json();const selected=history.rows?.find(r=>r.instrument_id&&r.episode_id&&r.asset_id);
 if(selected){await p.goto(base+'/assets/'+encodeURIComponent(selected.asset_id)+'?'+new URLSearchParams({instrument:selected.instrument_id,episode:selected.episode_id,snapshot:selected.id}));await p.locator('.chart-root iframe').waitFor({timeout:30000});await p.locator('.chart-loading').waitFor({state:'detached',timeout:30000});await p.getByText('图层',{exact:true}).click();for(const name of ['EMA20/50','MACD12/26/9','事件斐波参考']){const l=p.getByLabel(name,{exact:true});if(await l.count())await l.check();}await p.screenshot({path:out+'/frozen-chart.png',fullPage:true});}
 const chartErrors=await p.locator('.chart-error').allTextContents();
 const latencies=[];for(let i=0;i<8;i++){const t=Date.now();const r=await p.request.get(base+'/v1/signals?view=opportunities&page_size=300');assert(r.ok());await r.body();latencies.push(Date.now()-t);}
 const report={rows:rows.rows.map(r=>({symbol:r.symbol,level:r.opportunity_level,tick:r.tick_size})),width,errors,candidateChartErrors,chartErrors,detailRetained:true,chartRetained:true,historicalSnapshot:selected?.id,latencies};await fs.writeFile(out+'/browser.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));await b.close();assert.equal(errors.length+candidateChartErrors.length+chartErrors.length,0);
})().catch(e=>{console.error(e);process.exitCode=1});
