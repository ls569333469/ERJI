import asyncio
from fastapi import APIRouter,Request,HTTPException,Query
from engine.db import Session
from engine.models import UserPreference
from engine.adapters.base import now_ms
router=APIRouter()
from engine.p37.contracts import ObservationList

@router.get('/v1/anomaly-observations',response_model=ObservationList)
def anomaly_observations(archived:bool=False,pinned:bool=False,since:int|None=None,offset:int=Query(0,ge=0),limit:int=Query(200,ge=1,le=500),assets:list[str]|None=Query(None,max_length=100)):
    from engine.p37.store import read_observations
    return read_observations(archived,pinned,since,offset,limit,assets)

@router.put('/v1/anomaly-observations/{ident}/pin')
async def observation_pin(ident:str,request:Request):
    from engine.p37.store import pin
    payload=await request.json()
    if not isinstance(payload.get('pinned'),bool):raise HTTPException(422,'pinned must be boolean')
    if not await asyncio.to_thread(pin,ident,payload['pinned']):raise HTTPException(404,'观察记录不存在')
    return {'ok':True}

@router.get('/v1/anomaly-observations/config')
def observation_config():
    from engine.p37.detector import DEFAULTS
    with Session() as s:
        row=s.get(UserPreference,'p37_config')
        return {**DEFAULTS,**(row.payload if row else {})}

@router.put('/v1/anomaly-observations/config')
async def observation_config_update(request:Request):
    from engine.p37.detector import DEFAULTS
    import math
    payload=await request.json()
    if not isinstance(payload,dict) or set(payload)-set(DEFAULTS):raise HTTPException(422,'未知异动设置')
    for k,v in payload.items():
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0:raise HTTPException(422,'参数必须为正数')
        if k in ('capacity','breadth_min') and (not isinstance(v,int) or v>500):raise HTTPException(422,'容量必须为1至500整数')
        if k in ('breadth_coverage','breadth_ratio') and v>1:raise HTTPException(422,'覆盖比例不得超过1')
    def save():
        with Session.begin() as s:
            old=s.get(UserPreference,'p37_config');config={**DEFAULTS,**(old.payload if old else {}),**payload}
            if not config['merge_ms']<=config['cool_ms']<=config['archive_ms']:raise HTTPException(422,'合并时间不得晚于降频时间，降频时间不得晚于归档时间')
            s.merge(UserPreference(key='p37_config',updated_at=now_ms(),payload=config))
        return config
    return await asyncio.to_thread(save)


@router.post('/v1/anomaly-observations/watch/{symbol}')
def observation_manual_watch(symbol:str):
    from engine.p37.store import manual_watch
    ident=manual_watch(symbol)
    if ident is None:raise HTTPException(404,'没有找到对应币安USDT市场')
    return {'id':ident}

@router.get('/v1/anomaly-observation-history/{asset}')
def observation_history(asset:str,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100)):
    from engine.p38.observation_query import history
    return history(asset,offset,limit)

@router.get('/v1/analysis-pipeline/{instrument:path}')
def analysis_pipeline(instrument:str,limit:int=Query(20,ge=1,le=50)):
    from engine.p39.diagnostics import trace
    return trace(instrument,limit)
