"""Explicit local offline build; never fetches exchange data or runs in GET."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select,func
from engine.db import Session
from engine.models import Candle
from engine.adapters.base import now_ms
from engine.pd2.offline_indicators import build

def main():
    p=argparse.ArgumentParser();p.add_argument('--instrument');p.add_argument('--resolution',default='240',choices=['60','240','1D','1W'])
    p.add_argument('--discover',action='store_true');p.add_argument('--out',required=True);args=p.parse_args()
    if not args.discover and not args.instrument:p.error('Provide --instrument or --discover')
    now=now_ms()
    with Session() as s:
        targets=list(s.execute(select(Candle.instrument_id,Candle.resolution).where(Candle.is_closed.is_(True),
            Candle.close_time<=now,Candle.received_at<=now,Candle.resolution.in_(['60','240','1D','1W']))
            .group_by(Candle.instrument_id,Candle.resolution).having(func.count()>20000))) if args.discover else [(args.instrument,args.resolution)]
    results=[]
    for instrument,resolution in targets:
        with Session.begin() as s:results.append({'instrument_id':instrument,'resolution':resolution,**build(s,instrument,resolution,now)})
    result={'as_of':now,'external_requests':0,'rows':results};out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
