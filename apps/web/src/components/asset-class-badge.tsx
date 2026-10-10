type Identity={asset_class?:string;asset_class_label?:string;asset_class_source?:string|null;asset_name?:string|null};

export function AssetClassBadge({row}:{row:Identity}){
 if(!row.asset_class&&!row.asset_class_label)return null;
 const stock=row.asset_class?.startsWith('equity');
 const known=row.asset_class==='crypto'||stock;
 return <small title={[row.asset_name,row.asset_class_source].filter(Boolean).join(' · ')} style={{display:'inline-block',margin:'4px 0',padding:'1px 6px',borderRadius:4,fontSize:11,fontWeight:500,color:stock?'#92400e':known?'#1e40af':'#475569',background:stock?'#fef3c7':known?'#eff6ff':'#f1f5f9'}}>{row.asset_class_label||'分类待核验'}</small>;
}
