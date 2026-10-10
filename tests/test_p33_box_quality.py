from copy import deepcopy
from engine.pd2.box_quality import audit
from engine.strategies.structures import find_box, candidate, FOUR_HOURS as H4
from test_structure_signals import bar


def source():
    highs=[104,106,110,105,104,103];lows=[97,96,95,96,90,95]
    bars=[bar(i,high=highs[i%6],low=lows[i%6]) for i in range(42)]
    return find_box(bars,20),bars


def test_quality_accepts_repeated_touches_with_original_buffer():
    box,bars=source();result=audit(box,bars)
    assert result['status']=='verified' and result['sample_count']==20
    assert result['last_open_time']==bars[-1]['open_time']
    assert candidate('test',bars,[],42*H4)['box']['qualification']['status']=='verified'


def test_old_eighty_percent_rule_cannot_hide_completed_excursions():
    box,bars=source();original=deepcopy(box)
    bars[-7].update(high='140',close='135')
    result=audit(box,bars)
    assert result['status']=='rejected'
    assert result['violations'][0]['kind']=='close_outside'
    assert box==original  # never stretch the frozen boundaries to hide an excursion


def test_wick_beyond_existing_touch_tolerance_is_rejected():
    box,bars=source();bars[-7]['high']='140'
    assert audit(box,bars)['violations'][0]['kind']=='excursion_outside'


def test_missing_candle_or_unclosed_source_cannot_certify_box():
    box,bars=source()
    assert audit(box,bars[:-1])['status']=='unverified'
    bars[-4]['is_closed']=False
    assert audit(box,bars)['status']=='unverified'
