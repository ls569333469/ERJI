"""Local stored-candle audit. Apply only withdraws disproved pending boxes."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select,text
from engine.db import Session
from engine.models import Episode,Campaign,Candle
from engine.adapters.base import now_ms
from engine.pd2.service import candle_dict
from engine.pd2.box_quality import audit
from engine.repository import emit

def run(apply=False):
    now=now_ms();results=[]
    with Session.begin() as s:
        if apply:s.execute(text('SELECT pg_advisory_xact_lock(1877103)'))
        for row,campaign in s.execute(select(Episode,Campaign).join(Campaign).where(Episode.phase=='structure_pending')):
            if row.evidence.get('trigger') or not row.evidence.get('box'):continue
            box=row.evidence['box']
            candles=list(s.scalars(select(Candle).where(Candle.instrument_id==campaign.reference_instrument_id,
                Candle.resolution=='240',Candle.open_time>=box['start_time'],Candle.close_time<=box['end_time'],
                Candle.is_closed.is_(True)).order_by(Candle.open_time).limit(61)))
            quality=audit(box,[candle_dict(c) for c in candles])
            results.append({'event_id':row.id,'instrument_id':campaign.reference_instrument_id,'box':box,'quality':quality,
                            'source_candles':[candle_dict(c) for c in candles]})
            if apply and quality['status']=='rejected':
                row.phase='ended'
                row.evidence={**row.evidence,'candidate_quality':quality,'phase':'ended','reason':quality['reason'],
                    'evaluated_at':now,'phase_changed_at':now,'transitions':[*row.evidence.get('transitions',[]),
                     {'from':'structure_pending','to':'ended','at':now,'reason':quality['reason'],'audit_version':quality['version']}]}
                emit(s,'signal_update',{'asset_id':campaign.asset_id,'episode_id':row.id,'phase':'ended'})
    return {'as_of':now,'applied':apply,'external_requests':0,'rows':results}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--out',required=True);args=p.parse_args()
    result=run(args.apply);out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'rows':len(result['rows']),'rejected':sum(r['quality']['status']=='rejected' for r in result['rows']),
        'verified':sum(r['quality']['status']=='verified' for r in result['rows']),'out':str(out)},ensure_ascii=False))
