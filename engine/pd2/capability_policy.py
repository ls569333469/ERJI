"""Reference retention is not permission to reuse stale data in a live decision."""
POLICIES = {
    ('cmc','derivatives'):(1800000,3600000),
    ('cmc','liquidations'):(1800000,3600000),
    ('bykaranteli','symbols'):(21600000,21600000),
    ('bykaranteli','coverage'):(21600000,21600000),
    ('bykaranteli','oi_history'):(21600000,21600000),
    ('bykaranteli','pressure'):(600000,1200000),
    ('bykaranteli','oi'):(600000,1200000),
    ('bykaranteli','funding'):(600000,1200000),
    ('bykaranteli','slippage'):(7200000,7200000),
    ('bykaranteli','markets'):(3600000,3600000),
    ('bykaranteli','liquidation_leaderboard'):(3600000,3600000),
    ('bykaranteli','whales'):(1800000,3600000),
    ('bykaranteli','liquidations'):(1800000,3600000),
    ('bykaranteli','liqmap'):(1800000,3600000),
}

def describe(provider,capability,observed_at,now):
    spec=POLICIES.get((provider,capability))
    if not spec:return {}
    refresh,reference_age=spec
    age=now-observed_at if observed_at is not None else None
    return {'usage':'background_reference','action_eligible':False,
        'planned_refresh_ms':refresh,'live_max_age_ms':300000,'reference_max_age_ms':reference_age,
        'source_age_ms':age,'reference_status':'available' if age is not None and 0<=age<=reference_age else
        'source_time_unknown' if age is None else 'clock_invalid' if age<0 else 'stale'}
