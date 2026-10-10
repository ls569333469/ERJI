"""Versioned case evidence. A match is not a forecast or a trading permission."""
from functools import lru_cache
from pathlib import Path
from hashlib import sha256
import json
from engine.strategies.oi_paths import compare_case

CATALOG = Path(__file__).with_name('p33_case_catalog.json')


@lru_cache(maxsize=4)
def _load(raw):
    data = json.loads(raw)
    return data, sha256(raw).hexdigest()


def match_cases(current, as_of, path=CATALOG):
    result = {'status': 'no_match', 'matches': [], 'similarity_pct': None,
              'rise_probability': None, 'action_impact': 'explanation_only'}
    if not path.exists():
        return {**result, 'status': 'catalog_unavailable', 'reason': '案例档案尚未导入'}
    catalog, revision = _load(path.read_bytes())
    result['catalog_revision'] = revision
    for case in catalog.get('cases', []):
        hit = case['pattern']
        # A case imported later must never be silently attached to an old snapshot.
        if case['available_at'] > as_of or hit.get('event_id') == current.get('event_id'):
            continue
        if hit.get('market_id') == current.get('market_id'):
            continue
        comparison = compare_case(current, hit)
        if comparison:
            result['matches'].append({**comparison, 'symbol': case['symbol'],
                'label': hit['label'], 'trigger_at': hit['trigger']['close_time'],
                'source_sha256': case['source_sha256'], 'data_scope': case['data_scope'],
                'available_at': case['available_at'], 'case_id': case['id']})
    result['matches'] = sorted(result['matches'], key=lambda c: (c['trigger_at'], c['case_id']), reverse=True)[:3]
    if result['matches']:
        result['status'] = 'same_rule_evidence'
    return result
