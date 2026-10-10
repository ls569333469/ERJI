"use client";
import {useEffect,useState} from 'react';
import {OperationsDashboard} from './operations-dashboard';
import {Sources,SettingsPage,EmptySystem} from './system-pages';
import {AnomalySettings} from './anomaly-settings';

export function SystemStatusPage(){
 const [sources,setSources]=useState(false);
 useEffect(()=>{if(location.hash==='#sources')setSources(true)},[]);
 return <><OperationsDashboard/><details id="sources" className="p39-auxiliary" open={sources} onToggle={e=>setSources(e.currentTarget.open)}><summary>来源覆盖、预算与数据明细</summary>{sources&&<Sources/>}</details></>;
}
export function SettingsAndAlertsPage(){
 const [alerts,setAlerts]=useState(false);
 useEffect(()=>{if(location.hash==='#notifications')setAlerts(true)},[]);
 return <><AnomalySettings/><SettingsPage/><details id="notifications" className="p39-auxiliary" open={alerts} onToggle={e=>setAlerts(e.currentTarget.open)}><summary>Telegram 提醒 · 授权目标与投递记录</summary>{alerts&&<EmptySystem kind="notifications"/>}</details></>;
}
