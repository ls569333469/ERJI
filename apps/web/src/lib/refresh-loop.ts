/** One request at a time. Events never invalidate a response already in flight. */
export function createRefreshLoop<T>(
  load:(signal:AbortSignal)=>Promise<T>,
  accept:(value:T)=>void,
  reject:(error:Error)=>void,
  busy:(value:boolean)=>void,
  interval=15000,
  minGap=1000,
) {
  let disposed=false, running=false, lastFinished=0;
  let visible=true, polling=true;
  let timer:ReturnType<typeof setTimeout>|undefined;
  let controller:AbortController|undefined;
  const schedule=(delay:number)=>{
    if(timer!==undefined)clearTimeout(timer);
    timer=setTimeout(()=>void refresh(),delay);
  };
  async function refresh(force=false) {
    if(disposed||running||!visible)return;
    if(!force&&lastFinished&&Date.now()-lastFinished<minGap){
      schedule(minGap-(Date.now()-lastFinished));return;
    }
    if(timer!==undefined)clearTimeout(timer);
    running=true;controller=new AbortController();busy(true);
    try {
      const value=await load(controller.signal);
      if(!disposed)accept(value);
    } catch(error) {
      if(!disposed)reject(error instanceof Error?error:new Error(String(error)));
    } finally {
      running=false;lastFinished=Date.now();
      if(!disposed){busy(false);if(polling&&visible)schedule(interval);}
    }
  }
  return {
    refresh,
    setPolling(value:boolean){polling=value;if(!polling&&timer!==undefined)clearTimeout(timer);else if(polling&&visible&&!running)schedule(interval);},
    setVisible(value:boolean){visible=value;if(!visible&&timer!==undefined)clearTimeout(timer);else if(visible)void refresh(true);},
    dispose(){disposed=true;if(timer!==undefined)clearTimeout(timer);controller?.abort();},
  };
}
