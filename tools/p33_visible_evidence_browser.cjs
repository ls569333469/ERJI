const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
 const dir='docs/p33-validation/visible-evidence-20261002',base='http://127.0.0.1:18770';
 await fs.mkdir(dir,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1800,height:1150}}),errors=[],report={charts:[],errors};
 page.on('pageerror',e=>errors.push(e.message));
 // No application data mocked; prevent browser verification from changing user's preferences.
 await page.route('**/v1/preferences/**',r=>r.request().method()==='PUT'?r.fulfill({json:{saved:true}}):r.continue());
 await page.goto(base+'/signals');await page.locator('.opportunity-footnote').waitFor({timeout:30000});
 report.list=await page.locator('.opportunity-page').innerText();
 assert.ok(!report.list.includes('不要追价，设置提醒'));
 if(await page.locator('.opportunity-table tbody tr').count()){
  assert.match(report.list,/费率/);
  assert.match(report.list,/4H收盘|可按计划参与|可小仓试探|暂停新增参与|等待回到参与区/);
 }
 await page.screenshot({path:dir+'/signals.png',fullPage:true});
 const tut=page.locator('.opportunity-table tbody tr').filter({has:page.locator('.opportunity-coin b',{hasText:/^TUT$/})});
 if(await tut.count()){
  await tut.getByRole('button',{name:'展开',exact:true}).click();
  await page.getByRole('tab',{name:'K线与指标',exact:true}).click();
  await page.locator('.chart-caption').filter({hasText:'候选箱体'}).waitFor({timeout:30000});
  await page.locator('.chart-loading').waitFor({state:'detached',timeout:30000});
  assert.equal(await page.locator('.chart-error').count(),0);
  await page.locator('.opportunity-detail').screenshot({path:dir+'/inline-chart.png'});
  report.inline_chart='TUT real candidate chart loaded without error';
 }
 for(const symbol of ['TUT','PROVE']){
  const data=await(await page.request.get(base+'/v1/signals?view=all&q='+symbol+'&page_size=20')).json();
  const row=data.rows.find(r=>r.phase==='structure_pending');assert.ok(row,symbol+' pending event required');
  const event=await(await page.request.get(base+'/v1/structure-signals/'+row.event_id)).json();
  assert.equal(event.drawing_contract?.status,'candidate');
  await page.goto(base+'/assets/'+symbol+'?instrument='+encodeURIComponent(row.instrument_id)+'&episode='+encodeURIComponent(row.event_id));
  await page.locator('.chart-root iframe').waitFor({timeout:30000});
  await page.locator('.chart-loading').waitFor({state:'detached',timeout:30000});
  await page.locator('.chart-caption').filter({hasText:'候选箱体'}).waitFor({timeout:15000});
  const chartErrors=await page.locator('.chart-error').allTextContents();assert.equal(chartErrors.length,0);
  await page.locator('.chart-level-details').evaluate(el=>el.open=true);
  const levels=await page.locator('.chart-level-details').innerText();assert.match(levels,/4H候选箱体/);assert.match(levels,/触发线/);
  await page.locator('.pd2-chart').screenshot({path:dir+'/'+symbol+'-candidate.png'});
  report.charts.push({symbol,event_id:row.event_id,instrument_id:row.instrument_id,contract_status:row.contract_status,native_oi:row.native_oi,funding:row.funding_rate,levels,chartErrors});
 }
 await fs.writeFile(dir+'/browser.json',JSON.stringify(report,null,2));await browser.close();
 console.log(JSON.stringify(report));assert.equal(errors.length,0);
})().catch(e=>{console.error(e);process.exit(1)});
