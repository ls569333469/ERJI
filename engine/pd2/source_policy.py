"""One policy for new market facts; immutable historical evidence remains readable."""
from urllib.parse import urlsplit

VERSION = 'p39-binance-only-v1'
REALTIME = frozenset(('binance', 'binance_spot', 'binance_alpha', 'wallet_rising'))
DISABLED = frozenset(('aster', 'hyperliquid', 'cmc', 'bykaranteli', 'okx', 'geckoterminal'))
REASON = '已按币安单一数据源方案停用；历史证据保留'

class SourcePolicyDisabled(ValueError):
    pass

def assert_market_request(url, provider=None):
    host = (urlsplit(url).hostname or '').lower()
    if provider in DISABLED or not (host == 'binance.com' or host.endswith('.binance.com')
            or host in ('data-api.binance.vision', 'data.binance.vision')):
        raise SourcePolicyDisabled('disabled_by_policy')

def disabled_result(provider):
    from engine.adapters.base import now_ms
    return {'provider':provider,'source':provider,'status':'disabled_by_policy','state':'disabled',
            'checked_at':now_ms(),'last_success_at':None,'message':REASON,
            'capabilities':{'enabled':False,'features':[],'latest_gaps':[]},
            'payload':None,'reason':REASON,'source_policy':VERSION,'action_eligible':False}

def retire_runtime(session,now):
    """Close transport coverage at the last proven heartbeat, retain its history."""
    from sqlalchemy import select
    from engine.models import UserPreference,Task
    for row in session.scalars(select(UserPreference).where(UserPreference.key.like('ws_coverage:%')).with_for_update()):
        source=row.key.split(':')[1]
        if source not in DISABLED:continue
        segments=[dict(x) for x in row.payload.get('segments',[])]
        for segment in segments:
            if segment.get('end') is None:segment['end']=max(segment['start'],min(now,row.updated_at))
        row.payload={**row.payload,'connected':False,'segments':segments,'status':'disabled_by_policy','source_policy':VERSION}
    for row in session.scalars(select(Task).where(Task.state.in_(['queued','failed','leased'])).with_for_update(skip_locked=True)):
        subject=row.payload.get('subject','');source=subject.split(':')[0]
        if source not in DISABLED:continue
        if row.state=='leased' and (row.lease_until or 0)>now:continue
        row.state='complete';row.lease_owner=None;row.lease_until=None
        row.payload={**row.payload,'retired_reason':'disabled_by_policy','finished_at':now,'source_policy':VERSION}
