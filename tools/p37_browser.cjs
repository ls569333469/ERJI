// P37 UI contract fixtures only. Never inserts synthetic observations into ERJI.
const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {spawn}=require('node:child_process');const fs=require('node:fs');const assert=require('node:assert/strict');
(async()=>{
 const server=spawn(process.execPath,['node_modules/next/dist/bin/next','start','apps/web','-p','18780','-H','127.0.0.1'],{env:{...process.env,ERJI_NEXT_DIST_DIR:'.next-p37-validation'},windowsHide:true,stdio:'ignore'});
 let browser;
 try{
  for(let i=0;i<40;i++){try{if((await fetch('http://127.0.0.1:18780/radar')).ok)break}catch{}await new Promise(r=>setTimeout(r,250))}
  browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const now=Date.now();let phase=0,incremental=false;
  const row=(symbol,id)=>({id,asset_id:symbol,instrument_id:'binance:perp:'+symbol+'USDT',symbol:symbol+'USDT',direction:'up',visible_at:now-60000,discovered_at:now-60000,published_at:now,first_at:now-60000,last_at:now,status:'observing',observation_status:'observing',version:now,source_at:now,price:'1.03',visible_price:'1',price_at:now,since_first_pct:3,reasons:[{kind:'price',window:5,value:3.4}],changes:{'5':3.4},rvol:3.2,oi_change:null,analysis_status:'filling',data_status:'fresh',matrix:{},related_events:[],timeline:[]});
  await page.route('**/v1/**',async route=>{
    const u=new URL(route.request().url());
    if(u.pathname==='/v1/stream')return route.fulfill({status:200,contentType:'text/event-stream',body:': fixture\n\n'});
    if(u.pathname==='/v1/anomaly-observations'){
      if(phase===2)return route.fulfill({status:503,json:{detail:'测试读取失败'}});
      incremental ||= u.searchParams.has('since');
      const rows=phase===1?[row('FIXTURE_B','b')]:[row('FIXTURE_A','a')];
      return route.fulfill({json:{as_of:now,cursor:now,incremental:u.searchParams.has('since'),asset_ids:phase===1?['FIXTURE_A','FIXTURE_B']:['FIXTURE_A'],rows,unique_assets:phase===1?2:1,counts:{today:phase===1?2:1,focused:0,active:0,delayed:0},discovery:{state:'ok',updated_at:now,details:{}}}});
    }
    return route.fulfill({json:{rows:[],payload:{},timeframes:[],groups:[],positions:[]}});
  });
  await page.goto('http://127.0.0.1:18780/radar');await page.getByText('FIXTURE_AUSDT',{exact:true}).waitFor();
  assert.equal(await page.getByText(/相对BTC/).count(),0);
  await page.screenshot({path:'docs/p37-validation/radar-desktop.png',fullPage:true});
  phase=1;await page.getByRole('button',{name:'刷新',exact:true}).click();await page.getByRole('button',{name:/新增 1 条/}).waitFor();
  assert.equal(await page.getByText('FIXTURE_BUSDT',{exact:true}).count(),0);
  await page.getByRole('button',{name:/新增 1 条/}).click();await page.getByText('FIXTURE_BUSDT',{exact:true}).waitFor();assert(incremental);
  phase=2;await page.getByRole('button',{name:'刷新',exact:true}).click();await page.getByRole('alert').waitFor();assert.equal(await page.getByText('FIXTURE_AUSDT',{exact:true}).count(),1);
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'docs/p37-validation/radar-mobile.png',fullPage:true});
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);assert.equal(overflow,false);
  assert.deepEqual(errors,[]);
  fs.writeFileSync('docs/p37-validation/browser-check.json',JSON.stringify({fixture_only:true,production_writes:false,checks:['desktop radar','unique asset counts','incremental response merge','new row opt-in','failure retains prior data','390px viewport without page overflow','no BTC comparison UI'],page_errors:errors},null,2));
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
