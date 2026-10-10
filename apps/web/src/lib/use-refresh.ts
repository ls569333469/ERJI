"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import {createRefreshLoop} from "./refresh-loop";

export function useRefresh<T>(load:(signal:AbortSignal)=>Promise<T>,enabled=true,streamConnected=false) {
  const [data,setData]=useState<T>();
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  const active=useRef<ReturnType<typeof createRefreshLoop<T>>|null>(null);
  useEffect(()=>{
    if(!enabled)return;
    // A new query owns its own lifecycle; old responses cannot overwrite it.
    setData(undefined);setError("");
    const loop=createRefreshLoop(load,value=>{setData(value);setError("")},e=>setError(e.message),setBusy,60000,5000);
    active.current=loop;
    const visibility=()=>loop.setVisible(document.visibilityState!=="hidden");
    document.addEventListener("visibilitychange",visibility);visibility();
    return()=>{document.removeEventListener("visibilitychange",visibility);active.current=null;loop.dispose();};
  },[load,enabled]);
  // An open SSE socket does not prove events still advance. Always reconcile
  // at 60 seconds; events continue to request coalesced earlier refreshes.
  const refresh=useCallback((force=false)=>{void active.current?.refresh(force)},[]);
  return {data,error,busy,refresh,setError};
}
