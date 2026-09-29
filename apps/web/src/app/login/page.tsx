"use client";
import {appPath} from "@/lib/paths";
import {useState} from 'react';
import {api} from '@/lib/api';
export default function Login(){const[token,setToken]=useState('');const[error,setError]=useState('');const[busy,setBusy]=useState(false);
 return <section className="panel" style={{maxWidth:460,margin:'60px auto',padding:32}}><h1>登录 ERJI</h1><p>输入服务器管理员口令。口令只用于建立本次会话。</p><form onSubmit={async e=>{e.preventDefault();setBusy(true);try{await api('/auth/login',{method:'POST',body:JSON.stringify({token})});setToken('');window.location.assign(appPath('/signals'))}catch(e){setError((e as Error).message)}finally{setBusy(false)}}}><label>管理员口令<input type="password" required autoComplete="current-password" value={token} onChange={e=>setToken(e.target.value)}/></label><button className="button" disabled={busy}>{busy?'登录中…':'登录'}</button>{error&&<p role="alert">{error}</p>}</form></section>
}
