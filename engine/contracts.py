from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from engine.pd2.config import AnalysisConfig, ExecutionConfig, SourcesConfig, NotificationConfig, ValidationConfig

SCHEMA_VERSION = "0.2.0"
PHASES = {
    "anomaly_watch": "异常关注", "structure_pending": "结构待突破",
    "early_trigger": "早期上行触发", "structure_confirmed": "结构确认",
    "trend_continuing": "趋势持续", "weakening": "结构转弱",
    "invalidated": "已失效", "ended": "已结束",
}

class CandleDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    instrument_id: str
    resolution: str
    open_time: int
    close_time: int
    is_closed: bool
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    base_volume: Decimal | None
    quote_volume: Decimal | None
    revision: int
    received_at: int

class Quality(BaseModel):
    status: str
    reason: str | None = None

class OiChange(BaseModel):
    period: str
    display_label: str
    value_pct: Decimal | None = None
    quality: Quality
    start_sample_time: int | None = None
    end_sample_time: int | None = None
    price_change_pct: Decimal | None = None
    notional_change_pct: Decimal | None = None
    quantity_contribution_quote: Decimal | None = None
    price_contribution_quote: Decimal | None = None
    quote_asset: str | None = None

class MarketDTO(BaseModel):
    asset_class:str='unknown'
    asset_class_label:str='分类待核验'
    asset_class_source:str|None=None
    asset_class_as_of:int|None=None
    asset_name:str|None=None
    underlying_symbol:str|None=None
    instrument_id: str
    asset_id: str
    symbol: str
    venue: str
    native_symbol: str
    quote_asset: str
    price_kind: str
    price: Decimal | None = None
    as_of: int
    quality: Quality
    oi_base: Decimal | None = None
    oi_notional_quote: Decimal | None = None
    oi_quote_asset: str
    oi_changes: list[OiChange] = Field(default_factory=list)
    funding_rate: Decimal | None = None
    funding_kind: str
    funding_interval_seconds: int | None = None
    funding_settled_at: int | None = None
    oi_quality: Quality
    funding_quality: Quality
    oi_as_of: int | None = None
    circulating_cap: Decimal | None = None
    oi_cap_ratio: Decimal | None = None
    valuation_status: str = "unmapped"
    oi_notional_usd: Decimal | None = None
    coverage: dict = Field(default_factory=dict)
    candles_count: int = 0
    volume_24h_quote: Decimal | None = None
    universe_rank: int | None = None
    in_universe: bool = True
    deep_history: bool = False

class MarketList(BaseModel):
    schema_version: str = SCHEMA_VERSION
    as_of: int
    mode: str = "research_live"
    rows: list[MarketDTO]
    strategy_status: str = "not_enabled"
    universe: dict = Field(default_factory=dict)

class CandleList(BaseModel):
    schema_version: str = SCHEMA_VERSION
    as_of: int
    rows: list[CandleDTO]
    has_more: bool
    next_before: int | None
    quality: Quality

class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disabled_strategies: list[Literal["recent_high","ath","squeeze","double_bottom","early_strength","trend_pullback","failed_breakdown","retest","repeated_test"]] = Field(default_factory=list)
    collector_enabled: bool = True
    collector_interval_seconds: int = Field(default=60, ge=60, le=3600)
    symbols: list[str] = Field(default_factory=lambda: ["BTC","ETH","SOL","HYPE"], min_length=1, max_length=5000)
    universe_mode: Literal["binance_volume"] = "binance_volume"
    asset_limit: int = Field(default=200, ge=10, le=1000)
    deep_asset_limit: int = Field(default=80, ge=10, le=500)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    default_view: Literal["stages","recommended","trial","all"] = "stages"

class DraftRequest(BaseModel):
    config: RuntimeConfig

class PublishRequest(BaseModel):
    expected_version: int
