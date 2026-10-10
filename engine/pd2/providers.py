"""Historical compatibility helpers; external collection has been retired."""
import json,re
from pathlib import Path
from datetime import datetime
from engine.pd2.source_policy import DISABLED,SourcePolicyDisabled,disabled_result
from engine.pd2.binance_facts import valuation_inputs,valuation_for,quote_usd_rate

def timestamp(value):
    if isinstance(value,(int,float)):
        return int(value if value>100000000000 else value*1000)
    if isinstance(value,str):
        try:return int(datetime.fromisoformat(value.replace("Z","+00:00")).timestamp()*1000)
        except ValueError:return None
    return None

def read_key(path):
    if not path:return None
    content=Path(path).read_text(encoding="utf-8-sig").strip()
    try:
        obj=json.loads(content)
        if isinstance(obj,dict):
            for name in ("api_key","apiKey","key","token","bearer"):
                if isinstance(obj.get(name),str):return obj[name].removeprefix("Bearer ").strip()
    except json.JSONDecodeError:pass
    match=re.search(r"(?:Bearer\s+|(?:api[_ -]?key|token|key)\s*[:=：]\s*)[\"']?([A-Za-z0-9_.:-]{20,})",content,re.I)
    if match:return match.group(1)
    candidates=[line.strip() for line in content.splitlines() if re.fullmatch(r"[A-Za-z0-9_.:-]{20,}",line.strip())]
    if len(candidates)==1:return candidates[0]
    if re.fullmatch(r"[A-Za-z0-9_.:-]{20,}",content):return content
    raise ValueError("secret_file_format_unsupported")

class Supplemental:
    """No transport, key access, budget claims or writes for retired providers."""
    async def close(self):
        return None

    async def request(self,provider,capability,subject,path,params=None,ttl=300000,refresh=1800000,priority="normal"):
        if provider not in DISABLED:
            raise SourcePolicyDisabled('retired_supplemental_adapter')
        return disabled_result(provider)

    async def _request(self,*args,**kwargs):
        return await self.request(*args,**kwargs)

    async def cmc(self):
        return disabled_result('cmc')

    async def bykaranteli(self):
        return disabled_result('bykaranteli')

    async def map_rotation(self):
        return disabled_result('bykaranteli')
