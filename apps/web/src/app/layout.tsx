import type { Metadata } from "next";
import "./globals.css";
import "./radar.css";
import "./opportunity-board.css";
import {TopNavigation} from "@/components/top-navigation";
export const metadata: Metadata = {title:"ERJI · 多源市场雷达",description:"跨市场数据与交易结构研究",icons:{icon:"/icon.svg"}};
export default function Layout({children}:{children:React.ReactNode}) {
  return <html lang="zh-CN"><body><TopNavigation/>{children}<footer>ERJI 0.1 · 本机研究环境 · 无自动交易功能</footer></body></html>
}
