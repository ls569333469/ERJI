"""Read-only stored-data readiness audit; never claims profitability from code tests."""
from pathlib import Path
import sys,json,time
from collections import Counter
from sqlalchemy import select,func
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[4]/'.env')
load_dotenv(Path(__file__).resolve().parents[1]/'.env',override=True)
from engine.db import Session,engine
from engine.models import Candle,RawEvent,UserPreference,IdentityLink,Instrument
from engine.repository import runtime_config
from engine.strategies.p33 import NAMES
from engine.strategies.p33_release import gate


def run():
    if engine.url.host not in ('127.0.0.1','localhost'):raise RuntimeError('local_database_required')
    now=int(time.time()*1000);config=runtime_config()
    with Session() as session:
        history=list(session.execute(select(Candle.resolution,func.count(),func.count(func.distinct(Candle.instrument_id)),
            func.min(Candle.open_time),func.max(Candle.close_time)).where(Candle.is_closed.is_(True)).group_by(Candle.resolution)))
        coverage=[dict(timeframe=r[0],bars=r[1],markets=r[2],earliest=r[3],latest=r[4]) for r in history]
        records=list(session.execute(select(RawEvent.endpoint,func.count(),func.min(RawEvent.received_at),func.max(RawEvent.received_at)).where(
            RawEvent.source=='erji',RawEvent.endpoint.in_(['p33_first','p33_observation','p33_snapshot','p33_shadow_error'])).group_by(RawEvent.endpoint)))
        releases={rule:gate(session,rule,config.validation,now) for rule in NAMES}
        identities=dict(session.execute(select(IdentityLink.status,func.count()).group_by(IdentityLink.status)).all())
    return {'as_of':now,'network_requests':0,'database_read_only':True,'history':coverage,
            'forward_records':[dict(kind=r[0],count=r[1],first_at=r[2],last_at=r[3]) for r in records],
            'identity_statuses':identities,'release_gates':releases,
            'required_sample':config.validation.model_dump(),
            'not_proven':['数据范围不等于连续有效样本','失败/下架、样本外和基线对照尚未通过审核',
                          '前瞻样本必须自然积累，不能以回放替代','全部策略真实成功/失败/等待/缺锚点场景仍需真实事件覆盖']}


if __name__=='__main__':
    dest=Path('docs/p33-validation/completion-20261002');dest.mkdir(parents=True,exist_ok=True)
    result=run();(dest/'readiness.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
