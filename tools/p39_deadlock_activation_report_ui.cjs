const {chromium}=require('C:/Users/ls569/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs');
(async()=>{
 const b=await chromium.launch({channel:'msedge',headless:true});const results=[];
 for(const [name,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
  const p=await b.newPage({viewport:{width,height}}),errors=[];
  p.on('pageerror',e=>errors.push(e.message));
  const r=await p.goto('http://127.0.0.1:18770/previews/P39_deadlock_fix_activation.html',{waitUntil:'load'});
  await p.screenshot({path:'docs/p39-validation/deadlock-fix-20261008/activation/report-'+name+'.png',fullPage:true});
  const metrics=await p.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,text:document.body.innerText.slice(0,450)}));
  results.push({name,status:r.status(),errors,...metrics});await p.close();
 }
 await b.close();fs.writeFileSync('docs/p39-validation/deadlock-fix-20261008/activation/report-ui-verification.json',JSON.stringify(results,null,2));console.log(JSON.stringify(results,null,2));
})().catch(e=>{console.error(e);process.exit(1)});
