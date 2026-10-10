// Isolated browser fixtures only. Does not write sample data to application storage.
const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
 const dir='docs/p33-validation/completion-20261002',base='http://127.0.0.1:18770';
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1600,height:1080}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/v1/preferences/**',r=>r.fulfill({json:r.request().method()==='PUT'?{saved:true}:{payload:{resolution:'240',preset:'technical',layers:{structure:true,targets:true,ema:false,macd:false}}}}));
 await page.goto(base+'/operations');await page.locator('summary').filter({hasText:'新策略验证记录'}).click();
 const live=await(await page.request.get(base+'/v1/research/events')).json();
 const report={liveEvents:live.rows.length,liveChart:null,controlled:[],errors};
 if(live.rows.length){
  const select=page.getByLabel('选择研究事件');await select.selectOption(live.rows[0].snapshot_id);
  await page.getByRole('button',{name:'查看该事件K线',exact:true}).click();
  await page.locator('.chart-root iframe').waitFor({timeout:30000});await page.locator('.chart-loading').waitFor({state:'detached',timeout:30000});
  await page.locator('.pd2-chart').scrollIntoViewIfNeeded();await page.locator('.pd2-chart').screenshot({path:dir+'/live-research-chart.png'});
  report.liveChart={event:live.rows[0],errors:await page.locator('.chart-error').allTextContents()};
  assert.equal(report.liveChart.errors.length,0);
 }
 const fixtures=JSON.parse(await fs.readFile(dir+'/chart-fixtures.json','utf8'));let chosen;
 await page.route('**/v1/**',async route=>{
  const u=new URL(route.request().url()),path=u.pathname.replace(/^.*\/v1/,'');let data;
  if(path==='/research/events')data={rows:Object.entries(fixtures).map(([rule,f])=>({snapshot_id:f.snapshot.id,instrument_id:f.snapshot.instrument_id,event_id:f.snapshot.event_id,label:'算法验收样例 · '+rule,as_of:f.snapshot.as_of,status:'triggered'}))};
  else if(path==='/chart/snapshot'){chosen=Object.values(fixtures).find(f=>f.snapshot.id===u.searchParams.get('snapshot_id'))?.snapshot;data={snapshot:chosen};}
  else if(path==='/instruments'&&chosen)data={rows:[{id:chosen.instrument_id,native_symbol:'算法验收样例',venue:'test',spec:{tick_size:'0.001'}}]};
  else if(path==='/analysis'&&chosen)data={instrument_id:chosen.instrument_id,as_of:chosen.as_of,timeframes:chosen.timeframe_assessments,opportunity:chosen.opportunity_plan};
  else if(path==='/derivatives/history'&&chosen)data=chosen.derivative_history_at_snapshot;
  else if(path==='/analysis/series'&&chosen){const tf=u.searchParams.get('resolution'),all=chosen.timeframe_assessments.find(a=>a.timeframe===tf)?.series||[];data={instrument_id:chosen.instrument_id,timeframe:tf,snapshot_id:chosen.id,series_revision:chosen.series_revision,rows:all.filter(b=>b.time<Number(u.searchParams.get('before'))).slice(-Number(u.searchParams.get('limit'))),status:'historical_frozen'};}
  else if(path==='/candles'&&chosen){const tf=u.searchParams.get('resolution'),all=chosen.candles_at_snapshot[tf]||[];data={rows:all.filter(b=>b.open_time<Number(u.searchParams.get('before')||Infinity)).slice(-Number(u.searchParams.get('limit')||300))};}
  if(data!==undefined)return route.fulfill({json:data});return route.fallback();
 });
 await page.reload();await page.locator('summary').filter({hasText:'新策略验证记录'}).click();
 for(const [rule,f] of Object.entries(fixtures)){
  await page.getByLabel('选择研究事件').selectOption(f.snapshot.id);
  await page.getByRole('button',{name:'查看该事件K线',exact:true}).click();
  await page.locator('.chart-root iframe').waitFor({timeout:30000});await page.locator('.chart-loading').waitFor({state:'detached',timeout:30000});
  await page.locator('.pd2-chart').scrollIntoViewIfNeeded();
  await page.locator('.pd2-chart').screenshot({path:dir+'/controlled-'+rule+'.png'});
  const chartErrors=await page.locator('.chart-error').allTextContents();
  report.controlled.push({rule,kind:f.kind,anchors:f.snapshot.drawing_contract.anchors.length,chartErrors});
  assert.equal(chartErrors.length,0,rule+': '+chartErrors.join('; '));
 }
 await fs.writeFile(dir+'/chart-browser.json',JSON.stringify(report,null,2));
 await browser.close();console.log(JSON.stringify(report));assert.equal(errors.length,0);
})().catch(e=>{console.error(e);process.exit(1)});
