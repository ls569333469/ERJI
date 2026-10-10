from engine.strategies.oi_paths import classify, compare_case


def test_aligned_daily_quantity_and_transition_are_not_notional_changes():
    days=[{'open_time':0,'close_time':86400000,'is_closed':True,'open':'1','close':'1.5'},
          {'open_time':86400000,'close_time':172800000,'is_closed':True,'open':'1.5','close':'2'}]
    oi=[{'time':0,'quantity':'100'},{'time':86400000,'quantity':'120'},{'time':172800000,'quantity':'110'}]
    result=classify(days,oi,172800000)
    assert result['path']=='增仓转减仓的上涨延续'
    assert result['windows'][0]['quantity_change_pct']=='20.0'
    assert classify(days,oi[:-1],172800000)['status']=='missing_data'
    oi[-1]['time']+=1
    assert classify(days,oi,172800001)['status']=='missing_data'


def test_ratio_alone_or_missing_latest_sample_never_matches():
    assert classify([],[],100)['path'] is None
    day={'open_time':0,'close_time':100,'is_closed':True,'open':'1','close':'2'}
    result=classify([day],[{'time':0,'quantity':'100'},{'time':99,'quantity':'200'},{'time':100,'quantity':None}],100)
    assert result['status']=='missing_data'


def test_case_match_requires_same_version_and_phase_without_probability():
    current={'strategy_id':'recent_high','config_hash':'v1','timeframe':'240','status':'triggered','label':'前高突破','volume_multiple':'1.8'}
    historical={**current,'event_id':'past','volume_multiple':'3'}
    match=compare_case(current,historical)
    assert match['similarity_pct'] is None and match['rise_probability'] is None
    assert match['differences'][0]['current']=='1.8'
    assert compare_case(current,{**historical,'config_hash':'v2'}) is None
    assert compare_case(current,{**historical,'status':'waiting'}) is None
