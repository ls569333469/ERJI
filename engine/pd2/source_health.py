"""Supplemental source summaries use collected field coverage, not seed rows."""
from collections import Counter


def supplement_health(provider, records, now):
    counts = Counter()
    successful = []
    for row in records:
        status = row.status
        if row.observed_at is not None and row.observed_at > now:
            status = 'clock_invalid'
        elif status == 'fresh' and row.expires_at < now:
            status = 'stale'
        counts[status] += 1
        if status in ('fresh', 'stale', 'source_time_unknown'):
            successful.append(getattr(row,'last_success_at',None) or row.received_at)
    fresh = counts['fresh']
    unknown = counts['source_time_unknown']
    stale = counts['stale']
    gaps = len(records) - fresh - unknown - stale
    state = ('pending' if not records else 'disabled' if counts['disabled'] == len(records)
             else 'ok' if fresh == len(records) else 'partial' if fresh or unknown or stale else 'error')
    return {
        'source': provider, 'state': state,
        'checked_at': max(((getattr(r,'last_attempt_at',None) or r.received_at) for r in records), default=0),
        'last_success_at': max(successful, default=None),
        'message': (f'已采集 {len(records)} 项：有效 {fresh}；来源时间未确认 {unknown}；过期 {stale}；其他缺口 {gaps}。逐项覆盖见下方记录。'
                    if records else '等待首次采集'),
        'capabilities': {'kind': 'supplement', 'scope': 'collected_capabilities', 'status_counts': dict(counts)},
    }
