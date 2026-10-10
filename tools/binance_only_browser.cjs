// Real local UI acceptance; no exchange requests, no production writes or mocked market facts.
const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
 const dir='docs/p39-validation/binance-only-implementation',base='http://127.0.0.1:18770';
 const report={started_at:new Date().toISOString(),production_writes:false,mocked_market_data:false,errors:[],pages:[],blocked_writes:[],charts:[]};
 const browser=await chromium.launch({channel:'msedge',headless:true});let page;
 try{
  const context=await browser.newContext({viewport:{width:1600,height:1050}});
  await context.route('**/v1/**',route=>{
   if(!['GET','HEAD','OPTIONS'].includes(route.request().method())){report.blocked_writes.push({method:route.request().method(),url:route.request().url()});return route.abort('blockedbyclient')}
   return route.continue();
  });
  page=await context.newPage();page.on('pageerror',e=>report.errors.push(e.message));
  report.network_failures=[];page.on('requestfailed',r=>{if(r.failure()?.errorText!=='net::ERR_ABORTED')report.network_failures.push({url:r.url(),error:r.failure()?.errorText})});
  for(const path of ['/signals','/radar','/sources','/settings','/operations']){
   const response=await page.goto(base+path,{waitUntil:'domcontentloaded',timeout:45000});
   await page.locator('h1').first().waitFor({timeout:30000});
   if(path==='/signals'){
    await page.getByText('现在可以买',{exact:true}).waitFor({timeout:30000});
    await page.getByText('关联计划 · 等待条件',{exact:true}).waitFor({timeout:30000});
    await page.locator('.opportunity-table tbody tr').first().waitFor({timeout:30000});
    report.buy_levels=await page.locator('.opportunity-stage').count();
    await page.screenshot({path:dir+'/signals-live.png',fullPage:true});
    const waiting=page.locator('[data-section="waiting-plans"]');
    const stable=waiting.locator('tr').filter({has:page.locator('.opportunity-coin b',{hasText:/^GTC$/})});
    const expand=(await stable.count()?stable:waiting).getByRole('button',{name:'展开',exact:true}).last();
    if(await expand.count()){
     await expand.click();await page.locator('.opportunity-detail').waitFor({timeout:30000});
     const tabs=await page.getByRole('tab').allTextContents();report.detail_tabs=tabs;
     const chart=page.getByRole('tab',{name:'K线与指标',exact:true});
     if(await chart.count()){
      await chart.click();await page.locator('.chart-root iframe').first().waitFor({timeout:45000});
      await page.locator('.chart-loading').first().waitFor({state:'detached',timeout:45000});
      report.charts.push({scope:'current waiting plan existing detail',errors:await page.locator('.chart-error').allTextContents(),caption:await page.locator('.chart-caption').allTextContents()});
      await page.locator('.opportunity-detail').screenshot({path:dir+'/detail-live.png'});
     }
    }
   }
   if(path==='/radar'){
    await page.locator('.observation-table tbody tr').first().waitFor({timeout:45000});
    const collapse=page.getByRole('button',{name:'收起列表',exact:true});await collapse.click();
    assert.equal(await page.locator('#observation-list-content').isVisible(),false);
    await page.getByRole('button',{name:'展开列表',exact:true}).click();assert(await page.locator('#observation-list-content').isVisible());
    report.observation_collapse=true;
    const detail=page.getByRole('button',{name:'详情',exact:true}).first();await detail.click();
    await page.locator('.observation-detail').waitFor();report.observation_detail=await page.locator('.observation-detail h2').innerText();
    await page.locator('.chart-loading').first().waitFor({state:'detached',timeout:45000});
    report.charts.push({scope:'observation existing detail',errors:await page.locator('.chart-error').allTextContents()});
    await page.screenshot({path:dir+'/observations-live.png',fullPage:true});
   }
   if(path==='/sources'){
    await page.locator('.source-card').first().waitFor({timeout:30000});
    const titles=await page.locator('.source-card h2').allTextContents();report.source_titles=titles;
    assert(!titles.some(x=>/CMC|CoinMarketCap|ByKaranteli|Aster|Hyperliquid|OKX|GeckoTerminal/i.test(x)));
   }
   if(path==='/settings'){
    await page.locator('.settings-form fieldset').first().waitFor({timeout:30000});
    const fields=await page.locator('.settings-form').innerText();assert(!/CoinMarketCap|ByKaranteli|自然月免费额度/.test(fields));
    report.settings_external_fields_absent=true;
   }
   if(path==='/operations')await page.locator('.operations-sources').waitFor({timeout:30000});
   report.pages.push({path,status:response.status(),heading:await page.locator('h1').first().innerText(),alerts:await page.getByRole('alert').allTextContents()});
   await page.screenshot({path:dir+'/'+path.slice(1)+'-page.png',fullPage:true});
  }
  // Frozen historical chart remains readable without recomputing its decision.
  const histories=await(await page.request.get(base+'/v1/signals?view=all&page_size=20')).json();
  const row=histories.rows?.find(r=>r.snapshot_id&&r.event_id);
  if(row){
   const path='/assets/'+encodeURIComponent(row.asset_id)+'?instrument='+encodeURIComponent(row.instrument_id)+'&episode='+encodeURIComponent(row.event_id)+'&snapshot='+encodeURIComponent(row.snapshot_id);
   await page.goto(base+path);await page.locator('.chart-root iframe').first().waitFor({timeout:45000});
   await page.locator('.chart-loading').first().waitFor({state:'detached',timeout:45000});
   report.charts.push({scope:'historical snapshot',snapshot_id:row.snapshot_id,errors:await page.locator('.chart-error').allTextContents()});
  }
  assert.deepEqual(report.errors,[]);assert(report.charts.every(x=>!x.errors.length));
  report.status='passed';
 }catch(error){report.status='failed';report.failure=String(error);if(page){report.visible_text=(await page.locator('body').innerText()).slice(-18000);report.chart_errors=await page.locator('.chart-error').allTextContents();await page.screenshot({path:dir+'/browser-failure.png',fullPage:true})}throw error}
 finally{report.completed_at=new Date().toISOString();await fs.writeFile(dir+'/browser-acceptance.json',JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify(report))}
})().catch(error=>{console.error(error);process.exitCode=1});
