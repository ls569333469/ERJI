// Read-only artifact rendering check, using the existing browser runtime.
const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
 const out='docs/p39-validation/binance-only-implementation',browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1520,height:980}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const response=await page.goto('http://127.0.0.1:18770/previews/P39_Binance_only_acceptance.html',{waitUntil:'domcontentloaded'});
  assert.equal(response.status(),200);
  assert.match(await page.locator('h1').innerText(),/币安单一来源/);
  assert.ok((await page.locator('table').count())>=9);
  assert.equal(await page.locator('main').evaluate(el=>el.scrollWidth>el.clientWidth),false);
  await page.screenshot({path:out+'/acceptance-report-desktop.png'});
  await page.setViewportSize({width:420,height:880});
  assert.equal(await page.locator('main').evaluate(el=>el.scrollWidth>el.clientWidth),false);
  await page.screenshot({path:out+'/acceptance-report-mobile.png'});
  assert.deepEqual(errors,[]);
  await fs.writeFile(out+'/report-render-check.json',JSON.stringify({status:'passed',at:new Date().toISOString(),http_status:200,desktop:true,mobile:true,page_errors:errors},null,2));
  console.log('Report HTTP / desktop / mobile rendering passed');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
