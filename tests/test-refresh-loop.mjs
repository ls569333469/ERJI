import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source=await fs.readFile(new URL('../apps/web/src/lib/refresh-loop.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {createRefreshLoop}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));
const flush=async()=>{await Promise.resolve();await Promise.resolve();};

test('20-second response survives timer and SSE bursts; polling waits for completion',async t=>{
  t.mock.timers.enable({apis:['setTimeout','Date'],now:100000});
  let calls=0,finish;const accepted=[],errors=[];
  const loop=createRefreshLoop(()=>{calls++;return new Promise(resolve=>{finish=resolve})},v=>accepted.push(v),e=>errors.push(e),()=>{});
  const pending=loop.refresh();
  for(let n=0;n<20;n++){t.mock.timers.tick(1000);void loop.refresh();}
  assert.equal(calls,1);finish({rows:[{symbol:'BTC'}]});await pending;
  assert.equal(accepted[0].rows.length,1);assert.deepEqual(errors,[]);
  t.mock.timers.tick(14999);assert.equal(calls,1);
  t.mock.timers.tick(1);assert.equal(calls,2);
  loop.dispose();finish({rows:[]});await flush();assert.equal(accepted.length,1);
});

test('old period and unmounted requests are aborted and never replace the new result',async()=>{
  let finishOld,oldSignal;const accepted=[];
  const old=createRefreshLoop(signal=>{oldSignal=signal;return new Promise(r=>finishOld=r)},v=>accepted.push(v),()=>{},()=>{});
  const pending=old.refresh();old.dispose();assert.equal(oldSignal.aborted,true);
  const next=createRefreshLoop(async()=>({period:'4H'}),v=>accepted.push(v),()=>{},()=>{});
  await next.refresh();finishOld({period:'1H'});await pending;next.dispose();
  assert.deepEqual(accepted,[{period:'4H'}]);
});

test('failures are visible, retain the accepted data, then recover on next poll',async t=>{
  t.mock.timers.enable({apis:['setTimeout','Date'],now:100000});
  let calls=0;const accepted=[],errors=[];
  const loop=createRefreshLoop(async()=>{if(++calls===2)throw new Error('timeout');return calls},v=>accepted.push(v),e=>errors.push(e.message),()=>{});
  await loop.refresh();await loop.refresh(true);
  assert.deepEqual(accepted,[1]);assert.deepEqual(errors,['timeout']);
  t.mock.timers.tick(15000);await flush();assert.deepEqual(accepted,[1,3]);loop.dispose();
});

test('SSE burst after completion is coalesced into one refresh with a minimum gap',async t=>{
  t.mock.timers.enable({apis:['setTimeout','Date'],now:100000});
  let calls=0;const loop=createRefreshLoop(async()=>++calls,()=>{},()=>{},()=>{});
  await loop.refresh();
  for(let n=0;n<30;n++)void loop.refresh();
  assert.equal(calls,1);t.mock.timers.tick(3000);await flush();assert.equal(calls,2);loop.dispose();
});
