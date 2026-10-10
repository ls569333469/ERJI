import {ObservationBoard} from "@/components/observation-board";
export default async function Page({searchParams}:{searchParams:Promise<{personal?:string}>}){const query=await searchParams;return <ObservationBoard watch={query.personal==='1'}/>}
