from types import SimpleNamespace
from engine.pd2.source_health import supplement_health


def record(status='fresh', observed_at=900, expires_at=1100):
    return SimpleNamespace(status=status, observed_at=observed_at, received_at=950, expires_at=expires_at)


def test_collected_cmc_is_not_permanently_pending():
    result = supplement_health('cmc', [record()], 1000)
    assert result['state'] == 'ok'
    assert result['last_success_at'] == 950


def test_expired_and_unknown_timestamp_are_not_fresh():
    result = supplement_health('bykaranteli', [record(expires_at=999), record('source_time_unknown', None)], 1000)
    assert result['state'] == 'partial'
    assert result['capabilities']['status_counts'] == {'stale': 1, 'source_time_unknown': 1}


def test_future_timestamp_and_denied_capability_remain_gaps():
    result = supplement_health('cmc', [record(observed_at=1001), record('permission_denied')], 1000)
    assert result['state'] == 'error'
    assert result['last_success_at'] is None


def test_empty_source_stays_pending():
    assert supplement_health('cmc', [], 1000)['state'] == 'pending'


def test_public_http_error_keeps_status_without_sensitive_context():
    import httpx
    from engine.adapters.base import public_error
    request = httpx.Request('GET', 'https://example.com/?key=private-test-value')
    response = httpx.Response(451, request=request)
    error = httpx.HTTPStatusError('private-test-value', request=request, response=response)
    assert '451' in public_error(error)
    assert 'private-test-value' not in public_error(error)
