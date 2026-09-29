"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import {createRefreshLoop} from "./refresh-loop";

export function useRefresh<T>(load:(signal:AbortSignal)=>Promise<T>,enabled=true) {
  const [data,setData]=useState<T>();
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  const active=useRef<ReturnType<typeof createRefreshLoop<T>>|null>(null);
  useEffect(()=>{
    if(!enabled)return;
    // A new query owns its own lifecycle; old responses cannot overwrite it.
    setData(undefined);setError("");
    const loop=createRefreshLoop(load,value=>{setData(value);setError("")},e=>setError(e.message),setBusy);
    active.current=loop;void loop.refresh();
    return()=>{active.current=null;loop.dispose();};
  },[load,enabled]);
  const refresh=useCallback((force=false)=>{void active.current?.refresh(force)},[]);
  return {data,error,busy,refresh,setError};
}
