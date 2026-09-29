from engine.repository import unique_candles


def bar(received=100, closed=True, close='1', instrument='pool:one', resolution='60'):
    return {'instrument_id':instrument,'resolution':resolution,'open_time':0,
            'received_at':received,'is_closed':closed,'close':close}


def test_duplicate_provider_bars_use_latest_without_repeated_conflict_key():
    result=unique_candles([bar(),bar(101,close='2'),bar(99,close='3')])
    assert len(result)==1
    assert result[0]['close']=='2'


def test_closed_bar_is_not_replaced_by_duplicate_partial_bar():
    assert unique_candles([bar(),bar(101,False)])[0]['is_closed']


def test_markets_and_timeframes_are_not_collapsed_together():
    assert len(unique_candles([bar(),bar(instrument='pool:two'),bar(resolution='240')]))==3
