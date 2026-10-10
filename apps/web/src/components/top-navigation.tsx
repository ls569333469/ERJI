"use client";
import Link from "next/link";
import {usePathname} from "next/navigation";

const items=[["/signals","买入机会"],["/radar","市场观察"]] as const;
const secondary=[["/history","信号回顾"],["/markets","市场查询"],["/operations","系统状态"],["/settings","设置与提醒"]] as const;

export function TopNavigation(){
 const path=usePathname();
 return <header className="topbar"><Link href="/signals" className="brand"><span className="brand-icon">E</span>ERJI<span className="brand-sub">市场机会</span></Link><nav>{items.map(([href,label])=><Link key={href} href={href} className={path===href||path.startsWith(href+"/")?"active":""}>{label}</Link>)}</nav><details className="opportunity-nav-more" key={path}><summary>{secondary.find(([href])=>path===href)?.[1]||"更多"}</summary><div>{secondary.map(([href,label])=><Link key={href} href={href}>{label}</Link>)}</div></details></header>
}
