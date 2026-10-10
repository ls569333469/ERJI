// Local static artifact verification; no business API or exchange requests.
const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
 const dir='docs/p39-validation/layered-analysis-implementation-20261007';
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const report={pages:[],errors:[],business_api_requests:0};
 try{
  const page=await browser.newPage({viewport:{width:1520,height:980}});
  page.on('pageerror',e=>report.errors.push(e.message));
  await page.route('**/v1/**',route=>{report.business_api_requests++;return route.abort()});
  for(const [file,selector,sections] of [['P39_T1_T2_T3_acceptance.html','main',8],['P39_T1_T2_T3_execution_plan.html','article',14]]){
   const response=await page.goto('http://127.0.0.1:18770/previews/'+file,{waitUntil:'domcontentloaded',timeout:15000});
   assert.equal(response.status(),200);assert.equal(await page.locator('h2').count(),sections);
   assert.match(await page.locator('body').innerText(),/199项|199通过/);
   for(const width of [1520,420]){
    await page.setViewportSize({width,height:980});
    assert.equal(await page.locator(selector).evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
    await page.screenshot({path:dir+'/'+file.replace('.html','')+'-'+width+'.png'});
   }
   report.pages.push({file,http_status:200,desktop:true,mobile:true,sections});
  }
  assert.deepEqual(report.errors,[]);assert.equal(report.business_api_requests,0);report.status='passed';
 }catch(e){report.status='failed';report.failure=String(e);throw e}
 finally{await fs.writeFile(dir+'/report-render-check.json',JSON.stringify(report,null,2));await browser.close()}
 console.log(JSON.stringify(report));
})().catch(e=>{console.error(e);process.exitCode=1});
