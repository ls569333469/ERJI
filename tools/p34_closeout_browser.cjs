const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1740,height:1160}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const base='http://127.0.0.1:18770',url=base+'/v1/signals?view=opportunities&page_size=300';
  await page.goto(base+'/signals');await page.locator('tbody tr').first().waitFor({timeout:30000});
  const samples=[];
  for(let i=0;i<5;i++)await Promise.all(Array.from({length:4},async()=>{
   const start=performance.now(),response=await page.request.get(url);assert.equal(response.status(),200);
   const body=await response.json();samples.push({ms:performance.now()-start,rows:body.rows.length,bytes:JSON.stringify(body).length});
  }));
  const data=await(await page.request.get(url)).json();
  const times=samples.map(x=>x.ms).sort((a,b)=>a-b);
  const result={as_of:new Date().toISOString(),concurrency:4,samples,p95_ms:times[18],nonempty:samples.every(x=>x.rows>0),
   visible_rows:await page.locator('tbody tr').count(),errors,
   fields:data.rows.map(r=>({symbol:r.symbol,action:r.action_code,oi_status:r.oi_status,native_oi_status:r.native_oi?.status,
    oi_cap_ratio:r.oi_cap_ratio,funding_rate:r.funding_rate,oi_changes:r.oi_changes}))};
  await page.screenshot({path:'docs/p34-validation/closeout-signals.png',fullPage:true});
  await fs.writeFile('docs/p34-validation/closeout-browser.json',JSON.stringify(result,null,2));
  assert.equal(errors.length,0);assert.ok(result.nonempty);assert.ok(result.p95_ms<2000);
  console.log(JSON.stringify({rows:result.visible_rows,p95_ms:result.p95_ms,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
