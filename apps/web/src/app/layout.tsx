import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import "./radar.css";
export const metadata: Metadata = {title:"ERJI · 多源市场雷达",description:"跨市场数据与交易结构研究"};
export default function Layout({children}:{children:React.ReactNode}) {
  return <html lang="zh-CN"><body><header className="topbar"><Link href="/signals" className="brand"><span className="brand-icon">E</span>ERJI<span className="brand-sub">多源市场雷达</span></Link><nav><Link href="/signals">信号总览</Link><Link href="/markets">市场数据</Link><Link href="/sources">数据来源</Link><Link href="/settings">参数设置</Link><Link href="/notifications">通知</Link><Link href="/history">历史</Link></nav><span className="mode"><i/>实时结构研究</span></header>{children}<footer>ERJI 0.1 · 本机研究环境 · 无自动交易功能</footer></body></html>
}
