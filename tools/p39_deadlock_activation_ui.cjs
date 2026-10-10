const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs');
const out='docs/p39-validation/deadlock-fix-20261008/activation';
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const results=[];
 for(const route of ['observations','signals']){
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[],failed=[],api=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('response',async r=>{if(r.url().includes('anomaly-observations')){try{const j=await r.json();api.push({url:r.url(),status:r.status(),rows:j.rows?.length,counts:j.counts,unique_assets:j.unique_assets,error:j.error});}catch{}}});
  page.on('response',r=>{if(r.url().includes('127.0.0.1')&&r.status()>=400)failed.push({url:r.url(),status:r.status()});});
  await page.goto('http://127.0.0.1:18770/'+route,{waitUntil:'domcontentloaded'});
  if(route==='observations')await page.getByLabel('我的关注',{exact:true}).uncheck();
  await page.locator('body').innerText();
  await page.waitForTimeout(15000);
  const body=await page.locator('body').innerText();
  await page.screenshot({path:out+'/'+route+'.png',fullPage:true});
  results.push({route,title:await page.title(),errors,failed,api,text:body.slice(0,8000)});
  await page.close();
 }
 await browser.close();fs.writeFileSync(out+'/ui-verification.json',JSON.stringify(results,null,2));
 console.log(JSON.stringify(results.map(r=>({...r,text:r.text.slice(0,1400)})),null,2));
})().catch(e=>{console.error(e);process.exit(1)});
