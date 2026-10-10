/* Actual built UI, disposable port and in-browser HTTP fixtures. No production API. */
const fs=require('node:fs'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),out=path.join(root,'docs/p39-validation');
const checks=[],requests=[],errors=[];let server,browser,port;
const check=(name,test)=>{assert.ok(test,name);checks.push(name)};
const at=2000000000000;
function row(symbol,action='participate',expires=at+10000){return {
 id:'event-'+symbol,event_id:'event-'+symbol,snapshot_id:'snap-'+symbol,strategy_id:'recent_high',strategy_version:'fixture',
 asset_id:symbol,instrument_id:'binance:perp:'+symbol+'USDT',symbol,venue:'binance',quote_asset:'USDT',action_code:action,
 opportunity_level:action==='waiting'?'l2':action==='trial'?'l3':'l1',price:'0.034862341234',tick_size:'0.00001',
 first_seen_at:at-60000,first_seen_price:'0.04',since_first_pct:'-12.844',as_of:at,reason:'隔离验收数据',phase_label:'结构确认',
 entry_zone:['0.034','0.035'],protection:'0.032',protection_reference:null,invalidation:null,box:{upper:'0.033',lower:'0.030'},
 timeframes:['60','240','1D','1W'].map(timeframe=>({timeframe,status:'fresh',direction:'up',momentum:'up'})),
 targets:{t1:{low:'0.04',high:'0.04',remaining_upside_pct:'15',basis_price:'0.0348'},daily:{low:'0.04',high:'0.04'},weekly:null},
 oi_usd:null,native_oi:{instrument_id:'binance:perp:'+symbol+'USDT',quantity:'100000',notional_quote:'12345',quote_asset:'USDT',observed_at:at},
 oi_notional_quote:'12345',oi_quote_asset:'USDT',oi_cap_ratio:null,oi_scope:'native',oi_status:'fx_missing',valuation_status:'unmapped',
 oi_changes:[{period:'1D',value_pct:'-5',price_change_pct:null}],funding_rate:null,funding_interval_seconds:null,funding_status:'missing',
 funding_reference_instrument:'binance:perp:'+symbol+'USDT',preliminary_net_r:'2',candidate_min_net_r:'1.8',opportunity_eligible:true,
 opportunity_eligibility_reason:'fixture',valid_until:expires,row_number:1
}}
const first=row('BUY'),trial=row('TRIAL','trial',at+3600000),waiting={...row('WAIT','waiting',at+3600000),entry_zone:null,protection:null,protection_reference:{price:'0.032',status:'estimated',method:'fixture'},box:{upper:'0.033',lower:'0.030',trigger_price:'0.035'},targets:{t1:null,daily:null,weekly:null},pending_volume_multiple:null};
const observe=(symbol,pinned=false,spot=false)=>({id:'o-'+symbol,asset_id:symbol,instrument_id:'binance:'+(spot?'spot:':'perp:')+symbol+'USDT',symbol,status:'observing',visible_at:at-60000,last_at:at,version:1,direction:'up',reasons:[{kind:'price',window:5,value:4,direction:'up',source_at:at}],price:'10',price_at:at,tracked_at:at,since_first_pct:2,pinned,changes:{'5':4},rvol:3,oi_change:null,oi_status:spot?'not_applicable':'missing',matrix:{},analysis_status:'queued',task_status:'not_analyzed',related_events:[],timeline:[],plans:[],data_status:'fresh'});
async function run(){
 port=await new Promise(resolve=>{const probe=net.createServer();probe.listen(0,'127.0.0.1',()=>{const n=probe.address().port;probe.close(()=>resolve(n))})});
 const log=fs.openSync(path.join(out,'frontend-layout-server-20261006.log'),'w');
 server=spawn(process.execPath,[path.join(root,'node_modules/next/dist/bin/next'),'start',path.join(root,'apps/web'),'--hostname','127.0.0.1','--port',String(port)],{cwd:root,windowsHide:true,stdio:['ignore',log,log]});fs.closeSync(log);
 const origin='http://127.0.0.1:'+port;
 for(let attempt=0;attempt<60;attempt++){
  if(server.exitCode!==null)throw Error('Test web exited; inspect frontend-layout-server-20261006.log');
  try{if((await fetch(origin+'/signals')).ok)break}catch{}
  if(attempt===59)throw Error('Test web readiness timeout');
  await new Promise(resolve=>setTimeout(resolve,500));
 }
 browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1600,height:1000}});
 page.on('pageerror',e=>errors.push(e.message));
 await page.clock.install({time:new Date(at)});
 await page.addInitScript(()=>{
  class FixtureEvents{
   constructor(){this.listeners={};setTimeout(()=>this.onopen?.({}),10);window.__p39Streams=(window.__p39Streams||[]).concat(this)}
   addEventListener(name,fn){(this.listeners[name]??=[]).push(fn)}
   close(){this.closed=true}
  }
  window.EventSource=FixtureEvents;
 });
 await page.route('**/v1/**',async route=>{
  const url=new URL(route.request().url());requests.push(url.pathname+url.search);
  let body={};
  if(url.pathname==='/v1/signals'){
   const pageNum=Number(url.searchParams.get('page')||1);
   body={as_of:at,rows:pageNum===1?[first,waiting]:[trial],groups:[],opportunity_counts:{l1:1,l2:1,l3:1},scan:{scanned:3,matched:3},projection:{as_of:at,coverage:{eligible_markets:3}},pagination:{list_snapshot:'fixed-fixture',page:pageNum,pages:2,total:3,new_assets_waiting:0}};
  }else if(url.pathname==='/v1/anomaly-observations'){
   const rows=url.searchParams.get('pinned')==='true'?[observe('MANUAL',true)]:[observe('AUTO',false,true),observe('MANUAL',true),...Array.from({length:10},(_,i)=>observe('EXTRA'+i))];
   body={as_of:at,rows,counts:{today:2,focused:1,active:0,delayed:0},unique_assets:rows.length,discovery:{updated_at:at,state:'ok',details:{}},asset_ids:rows.map(r=>r.asset_id),cursor:1,incremental:false,next_offset:null};
  }else if(url.pathname==='/v1/chart/snapshot')body={snapshot:null};
  else if(url.pathname.includes('/snapshots'))body={rows:[],truncated:false};
  else if(url.pathname==='/v1/health')body={status:'ok',workers:[]};
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.goto(origin+'/');await page.getByRole('heading',{name:'买入机会',exact:true}).waitFor();
 await page.locator('.opportunity-coin b').filter({hasText:/^TRIAL$/}).waitFor();
 check('default route is buy opportunities',new URL(page.url()).pathname==='/signals');
 check('one buy table includes both participate and trial',await page.locator('[data-section="buy-now"] .opportunity-table').count()===1 && await page.locator('[data-section="buy-now"] .opportunity-table tbody tr').count()===2);
 check('all pagination uses same list snapshot',requests.some(r=>r.includes('page=2')&&r.includes('list_snapshot=fixed-fixture')));
 check('buy header retains eight columns',await page.locator('[data-section="buy-now"] .opportunity-table thead th').count()===8);
 check('waiting plan shown separately on buy page',await page.locator('[data-section="waiting-plans"] .opportunity-coin b').count()===1);
 check('both sections share one signal load',requests.filter(r=>r.startsWith('/v1/signals?')&&r.includes('page=1&')).length===1);
 const headers=await page.locator('[data-section="buy-now"] .opportunity-table thead th').allTextContents();
 check('targets and four timeframes and derivatives columns retained',headers[4]==='第一 / 日线 / 周线目标'&&headers[5]==='1H / 4H / 1D / 1W'&&headers[6].includes('OI / 市值'));
 const text=await page.locator('[data-section="buy-now"] .opportunity-table').innerText();
 check('tick precision and negative first change',text.includes('0.03486')&&text.includes('-12.84%'));
 check('native OI currency retained without inventing USD',text.includes('USDT')&& !text.includes('$12.3K'));
 check('null funding and unknown interval are not zero',text.includes('费率 未返回')&&!text.includes('/ 0H'));
 check('duplicate and absent targets shown accurately',text.includes('日线同第一目标')&&text.includes('周线 未形成'));
 await page.screenshot({path:path.join(out,'frontend-layout-buy-20261006.png'),fullPage:true});
 await page.locator('.opportunity-table tbody tr').first().getByRole('button',{name:'展开',exact:true}).click();
 await page.waitForTimeout(100);
 check('expanded detail keeps frozen snapshot parameter',requests.some(r=>r.includes('/chart/snapshot?')&&r.includes('snapshot_id=snap-BUY')));
 const before=requests.filter(r=>r.startsWith('/v1/signals?')).length;
 await page.clock.runFor(11000);
 check('one-second clock removes expired buy without refresh',await page.locator('.opportunity-coin b').filter({hasText:/^BUY$/}).count()===0&&await page.locator('.opportunity-coin b').filter({hasText:/^TRIAL$/}).count()===1);
 await page.clock.runFor(22000);await page.waitForTimeout(100);
 check('30-second reconciliation continues with SSE open',requests.filter(r=>r.startsWith('/v1/signals?')).length>before);
 await page.goto(origin+'/radar');await page.locator('.observation-table tbody tr').first().waitFor();
 check('observation list initially shows first ten without losing records',await page.locator('.observation-table tbody tr').count()===10);
 await page.getByRole('button',{name:'展开全部 12 条',exact:true}).click();
 check('all observation rows remain available',await page.locator('.observation-table tbody tr').count()===12);
 await page.clock.runFor(33000);await page.waitForTimeout(100);
 check('refresh retains expanded list',await page.locator('.observation-table tbody tr').count()===12);
 await page.getByRole('button',{name:'收起为前10条',exact:true}).click();
 check('collapse restores first ten rows',await page.locator('.observation-table tbody tr').count()===10);
 await page.getByRole('button',{name:'收起列表',exact:true}).click();
 check('whole table can collapse',await page.locator('.observation-table').isHidden());
 await page.getByRole('button',{name:'展开列表',exact:true}).click();
 check('whole table can reopen',await page.locator('.observation-table').isVisible());
 check('waiting plans moved off observation page',await page.locator('[data-section="waiting-plans"]').count()===0);
 check('manual pin is a filter, not another stage',await page.getByLabel('我的关注').count()===1&&await page.getByRole('button',{name:'添加观察'}).count()===1);
 check('source age and spot OI not applicable', (await page.locator('.observation-table').innerText()).includes('上次更新')&&(await page.locator('.observation-table').innerText()).includes('现货不适用'));
 await page.getByLabel('我的关注').check();await page.waitForTimeout(100);
 await page.locator('.observation-table tbody tr').filter({hasText:'MANUAL'}).waitFor();
 check('personal filter preserves manual row',await page.locator('.observation-table tbody tr').count()===1);
 await page.getByLabel('我的关注').uncheck();await page.waitForTimeout(100);
 await page.screenshot({path:path.join(out,'frontend-layout-observations-20261006.png'),fullPage:true});
 await page.goto(origin+'/signals');await page.locator('[data-section="waiting-plans"] .opportunity-table tbody tr').first().waitFor();
 check('waiting plans keep full original columns',await page.locator('[data-section="waiting-plans"] .opportunity-table thead th').count()===8);
 const waitingText=await page.locator('[data-section="waiting-plans"] .opportunity-table').innerText();
 check('trigger-only, estimated protection, unavailable targets retained',waitingText.includes('4H 收盘高于')&&waitingText.includes('保护参考')&&waitingText.includes('待形成'));
 await page.clock.runFor(3600001);await page.waitForTimeout(100);
 check('zero-buy level remains above waiting plans after expiry',await page.locator('[data-section="buy-now"]').count()===1&&await page.locator('[data-section="buy-now"] .opportunity-table tbody tr').count()===0&&await page.locator('[data-section="waiting-plans"] .opportunity-coin b').count()===1);
 check('two explicit levels visible including zero count',(await page.locator('[data-section="buy-now"]>summary').innerText()).includes('L1')&&(await page.locator('[data-section="buy-now"]>summary').innerText()).includes('0 个')&&(await page.locator('[data-section="waiting-plans"]>summary').innerText()).includes('L2'));
 await page.locator('[data-section="waiting-plans"]>summary').click();
 check('waiting level can collapse',await page.locator('[data-section="waiting-plans"] .opportunity-table').isHidden());
 await page.clock.runFor(33000);await page.waitForTimeout(100);
 check('refresh keeps level collapsed',await page.locator('[data-section="waiting-plans"] .opportunity-table').isHidden());
 await page.locator('[data-section="waiting-plans"]>summary').click();
 check('waiting level reopens with original table',await page.locator('[data-section="waiting-plans"] .opportunity-table').isVisible());
 await page.screenshot({path:path.join(out,'frontend-layout-observations-20261006.png'),fullPage:true});
 await page.locator('.opportunity-nav-more summary').click();
 for(const label of ['信号回顾','市场查询','系统状态','设置与提醒'])check('auxiliary entry '+label,await page.getByRole('link',{name:label,exact:true}).count()===1);
 check('two primary navigation entries',await page.locator('.topbar nav a').count()===2);
 check('no client rendering exception',errors.length===0);
 fs.writeFileSync(path.join(out,'frontend-layout-browser-check-20261006.json'),JSON.stringify({status:'passed',checks,headers,errors,requests,test_port:port,fixture:true,production_api_access:false,detail_fixture_limit:'snapshot null response only verifies original frozen request parameters; no live chart recovery asserted'},null,2));
 console.log(JSON.stringify({passed:checks.length,test_port:port,production_api_access:false}));
}
run().catch(e=>{fs.writeFileSync(path.join(out,'frontend-layout-browser-check-20261006.json'),JSON.stringify({status:'failed',checks,errors,error:e.stack,requests},null,2));console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r))}});
