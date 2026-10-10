"""P37 public contract. Exported to OpenAPI and generated TypeScript."""
from typing import Literal,Any
from pydantic import BaseModel,Field

class AnomalyReason(BaseModel):
    kind:Literal['price','volume','oi','manual']
    window:int
    value:float
    direction:str='up'
    source_at:int=0
    intrabar:bool=False
    source_from:int|None=None
    span_ms:int|None=None
    window_kind:str|None=None
    metric_unit:str|None=None
    metric_direction:str|None=None
    price_direction:str|None=None
    price_kind:str|None=None
    quantity_unit:str|None=None
    volume_unit:str|None=None
    bar_closed:bool|None=None
    bar_open_time:int|None=None
    baseline_method:str|None=None
    baseline_count:int|None=None
    field_status:str|None=None

class ObservationTrend(BaseModel):
    direction:str
    status:str
    bars:int
    as_of:int|None=None
    rsi14:float|None=None
    rvol20:float|None=None
    macd:str|None=None
    macd_signal:str|None=None
    boll_low:str|None=None
    boll_high:str|None=None

class ObservationEvent(BaseModel):
    at:int
    price:str|None=None
    reasons:list[AnomalyReason]

class ObservationPlan(BaseModel):
    id:str
    action:str
    valid_until:int

class ObservationRow(BaseModel):
    asset_class:str='unknown'
    asset_class_label:str='分类待核验'
    asset_class_source:str|None=None
    asset_class_as_of:int|None=None
    asset_name:str|None=None
    underlying_symbol:str|None=None
    id:str
    asset_id:str
    instrument_id:str
    symbol:str
    direction:str
    first_at:int
    visible_at:int
    last_at:int
    version:int
    discovered_at:int
    published_at:int
    source_at:int|None=None
    status:str
    observation_status:str
    analysis_status:str='queued'
    data_status:str='delayed'
    trade_status:str='no_entry'
    rule_version:str='p37-v1'
    classification:str|None=None
    classification_status:str='unconfirmed'
    classification_version:str|None=None
    classification_context:dict[str,Any]|None=None
    task_status:str='not_analyzed'
    pipeline_tiers:list[str]=Field(default_factory=lambda:['T1'])
    analysis_execution_status:str='not_analyzed'
    strategy_result:str='not_analyzed'
    analysis_result_id:str|None=None
    analysis_input_version:str|None=None
    analysis_computed_at:int|None=None
    analysis_checked_at:int|None=None
    demand_ids:list[str]=Field(default_factory=list)
    demand_origins:list[str]=Field(default_factory=list)
    has_analysis_demand:bool=False
    oi_status:str|None=None
    oi_as_of:int|None=None
    oi_change_status:str|None=None
    oi_change_start_at:int|None=None
    oi_change_end_at:int|None=None
    first_rule_config:dict[str,float]=Field(default_factory=dict)
    next_task:str='持续观察'
    first_price:str|None=None
    visible_price:str|None=None
    price:str|None=None
    price_at:int|None=None
    price_source:str|None=None
    source_age_ms:int|None=None
    snapshot_at:int|None=None
    snapshot_age_ms:int|None=None
    tracking_interval_ms:int|None=None
    tracking_tier:str|None=None
    snapshot_status:str='waiting'
    first_basis:dict[str,Any]=Field(default_factory=dict)
    visible_basis:dict[str,Any]=Field(default_factory=dict)
    since_first_pct:float|None=None
    max_up_pct:float|None=None
    max_down_pct:float|None=None
    rvol:float|None=None
    oi_change:float|None=None
    changes:dict[str,float|None]=Field(default_factory=dict)
    reasons:list[AnomalyReason]=Field(default_factory=list)
    first_reasons:list[AnomalyReason]=Field(default_factory=list)
    matrix:dict[str,ObservationTrend]=Field(default_factory=dict)
    analyzed_at:int|None=None
    analysis_instrument_id:str|None=None
    structure_checked_at:int|None=None
    structure_check_status:str|None=None
    structure_last_attempt_at:int|None=None
    structure_next_retry_at:int|None=None
    structure_task_status:str='not_submitted'
    structure_error:str|None=None
    pinned:bool=False
    tracking_gap:bool=False
    trigger_count:int=0
    trend_label:str|None=None
    timeline:list[ObservationEvent]=Field(default_factory=list)
    related_events:list['ObservationRow']=Field(default_factory=list)
    plans:list[ObservationPlan]=Field(default_factory=list)
    missing_timeframes:list[str]=Field(default_factory=list)

class ObservationCounts(BaseModel):
    today:int
    focused:int
    delayed:int
    active:int

class ObservationDiscovery(BaseModel):
    state:str
    updated_at:int|None=None
    details:dict[str,Any]=Field(default_factory=dict)

class ObservationList(BaseModel):
    as_of:int
    cursor:int
    incremental:bool
    next_offset:int|None=None
    asset_ids:list[str]
    rows:list[ObservationRow]
    unique_assets:int
    counts:ObservationCounts
    discovery:ObservationDiscovery
