// Isolated browser verification. Does not alter the user's browser or application data.
const {chromium}=require(process.env.ERJI_PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const output=path.resolve('docs/p33-validation');await fs.mkdir(output,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 const page=await browser.newPage({viewport:{width:1600,height:1000}});
 await page.route('**/v1/preferences/chart',async route=>{
  // Test only display preferences in this browser; do not overwrite user settings.
  if(route.request().method()==='PUT')return route.fulfill({json:{saved:true}});
  return route.fulfill({json:{payload:{resolution:'240',preset:'technical',layers:{structure:true,targets:true,ema:false,macd:false}}}});
 });
 const errors=[];page.on('pageerror',e=>errors.push(e.stack||e.message));
 const base='http://127.0.0.1:18770';
 await page.goto(base+'/signals',{waitUntil:'domcontentloaded'});
 await page.getByRole('heading',{name:'买入机会',exact:true}).waitFor();
 await page.screenshot({path:path.join(output,'signals.png'),fullPage:true});
 const history=await (await page.request.get(base+'/v1/history')).json();
 const selected=history.rows?.find(row=>row.instrument_id&&row.episode_id&&row.asset_id);
 if(!selected)throw Error('No genuine stored snapshot available for chart verification');
 const url=base+'/assets/'+encodeURIComponent(selected.asset_id)+'?'+new URLSearchParams({instrument:selected.instrument_id,episode:selected.episode_id,snapshot:selected.id});
 await page.goto(url,{waitUntil:'domcontentloaded'});
 await page.locator('.chart-root iframe').waitFor({timeout:30000});
 await page.locator('.chart-loading').waitFor({state:'detached',timeout:30000});
 await page.waitForFunction(()=>Array.from(document.querySelectorAll('.chart-level-details summary')).some(el=>/· [1-9]/.test(el.textContent||'')),null,{timeout:15000});
 await page.screenshot({path:path.join(output,'frozen-chart.png'),fullPage:true});
 await page.getByText('图层',{exact:true}).click();
 const available=await page.locator('.chart-layer-menu label').allTextContents();
 const ema=page.getByLabel('EMA20/50',{exact:true});
 if(await ema.count())await ema.check();
 const macd=page.getByLabel('MACD12/26/9',{exact:true});
 if(await macd.count())await macd.check();
 await page.screenshot({path:path.join(output,'indicator-layers.png'),fullPage:true});
 const chartErrors=await page.locator('.chart-error').allTextContents();
 const report={url,snapshot_id:selected.id,errors,chartErrors,availableLayers:available};
 await fs.writeFile(path.join(output,'browser-check.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify(report));await browser.close();
 if(errors.length||chartErrors.length)process.exitCode=1;
})().catch(error=>{console.error(error.message);process.exitCode=1});
