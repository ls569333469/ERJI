"""Persistent models. Numeric market values never use binary floats at rest."""
from decimal import Decimal
from typing import Any
from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from engine.db import Base

class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40))
    identity_status: Mapped[str] = mapped_column(String(40), default="native_catalog")
    known_at: Mapped[int] = mapped_column(BigInteger)

class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    venue: Mapped[str] = mapped_column(String(40))
    native_symbol: Mapped[str] = mapped_column(String(80))
    market_type: Mapped[str] = mapped_column(String(30))
    quote_asset: Mapped[str] = mapped_column(String(20))
    multiplier: Mapped[Decimal] = mapped_column(Numeric(38,18), default=1)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

class RawEvent(Base):
    __tablename__ = "raw_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    endpoint: Mapped[str] = mapped_column(String(150))
    received_at: Mapped[int] = mapped_column(BigInteger, index=True)
    payload: Mapped[Any] = mapped_column(JSON)

class MarketState(Base):
    __tablename__ = "market_states"
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    as_of: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSON)

class Candle(Base):
    __tablename__ = "candles"
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    resolution: Mapped[str] = mapped_column(String(10), primary_key=True)
    open_time: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    close_time: Mapped[int] = mapped_column(BigInteger)
    is_closed: Mapped[bool] = mapped_column(Boolean)
    open: Mapped[Decimal] = mapped_column(Numeric(38,18))
    high: Mapped[Decimal] = mapped_column(Numeric(38,18))
    low: Mapped[Decimal] = mapped_column(Numeric(38,18))
    close: Mapped[Decimal] = mapped_column(Numeric(38,18))
    base_volume: Mapped[Decimal | None] = mapped_column(Numeric(38,18),nullable=True)
    quote_volume: Mapped[Decimal | None] = mapped_column(Numeric(38,18), nullable=True)
    received_at: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    raw_ref: Mapped[str] = mapped_column(ForeignKey("raw_events.id"))

class OiPoint(Base):
    __tablename__ = "oi_points"
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    event_time: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    base_quantity: Mapped[Decimal | None] = mapped_column(Numeric(38,18), nullable=True)
    notional_quote: Mapped[Decimal | None] = mapped_column(Numeric(38,18), nullable=True)
    quote_asset: Mapped[str] = mapped_column(String(20))
    mark_price: Mapped[Decimal | None] = mapped_column(Numeric(38,18), nullable=True)
    received_at: Mapped[int] = mapped_column(BigInteger)
    raw_ref: Mapped[str] = mapped_column(ForeignKey("raw_events.id"))

class FundingPoint(Base):
    __tablename__ = "funding_points"
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    settled_at: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(38,18))
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_ref: Mapped[str] = mapped_column(ForeignKey("raw_events.id"))

class SourceHealth(Base):
    __tablename__ = "source_health"
    source: Mapped[str] = mapped_column(String(40), primary_key=True)
    state: Mapped[str] = mapped_column(String(40))
    checked_at: Mapped[int] = mapped_column(BigInteger)
    last_success_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message: Mapped[str] = mapped_column(String(400))
    capabilities: Mapped[dict] = mapped_column(JSON)

class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    role: Mapped[str] = mapped_column(String(40), primary_key=True)
    updated_at: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSON)

class SettingsVersion(Base):
    __tablename__ = "settings_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(20))
    config: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[int] = mapped_column(BigInteger)
    published_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

class Watchlist(Base):
    __tablename__ = "watchlists"
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    created_at: Mapped[int] = mapped_column(BigInteger)

class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    first_seen_at: Mapped[int] = mapped_column(BigInteger)
    first_seen_price: Mapped[Decimal] = mapped_column(Numeric(38,18))
    reference_instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    state: Mapped[str] = mapped_column(String(30))

class Episode(Base):
    __tablename__ = "episodes"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"))
    strategy_id: Mapped[str] = mapped_column(String(80))
    phase: Mapped[str] = mapped_column(String(40))
    rule_version: Mapped[str] = mapped_column(String(40))
    evidence: Mapped[dict] = mapped_column(JSON)

class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id"))
    execution_instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    revision: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSON)

class SignalSnapshot(Base):
    __tablename__ = "signal_snapshots"
    __table_args__ = (Index('ix_signal_snapshots_plan_latest','plan_id', 'as_of'),)
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    as_of: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSON)

class StreamEvent(Base):
    __tablename__ = "stream_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSON)

class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("stream_events.id"))
    dedupe_key: Mapped[str] = mapped_column(String(240), unique=True)
    state: Mapped[str] = mapped_column(String(30))
    expires_at: Mapped[int] = mapped_column(BigInteger)
    next_attempt_at: Mapped[int] = mapped_column(BigInteger)
    lease_until: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)

class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(240), unique=True)
    kind: Mapped[str] = mapped_column(String(60))
    state: Mapped[str] = mapped_column(String(30), index=True)
    available_at: Mapped[int] = mapped_column(BigInteger)
    lease_until: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON)


class AnalysisDemand(Base):
    """Why a native market needs T2; observation retention is independent."""
    __tablename__ = 'analysis_demands'
    __table_args__ = (Index('ix_analysis_demands_live', 'state', 'expires_at', 'instrument_id'),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey('instruments.id'), index=True)
    origin_type: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(180))
    state: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)


class AnalysisResult(Base):
    """Immutable, compact evidence for one native market/input calculation."""
    __tablename__ = 'analysis_results'
    __table_args__ = (Index('ix_analysis_results_recent', 'computed_at', 'work_kind'),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey('instruments.id'), index=True)
    work_kind: Mapped[str] = mapped_column(String(30))
    input_version: Mapped[str] = mapped_column(String(64))
    computed_at: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSON)


class MarketAnalysisState(Base):
    __tablename__ = 'market_analysis_states'
    instrument_id: Mapped[str] = mapped_column(ForeignKey('instruments.id'), primary_key=True)
    work_kind: Mapped[str] = mapped_column(String(30), primary_key=True)
    input_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_id: Mapped[str | None] = mapped_column(ForeignKey('analysis_results.id'), nullable=True)
    analysis_status: Mapped[str] = mapped_column(String(30))
    strategy_result: Mapped[str] = mapped_column(String(40))
    last_attempt_at: Mapped[int] = mapped_column(BigInteger)
    last_success_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    next_check_at: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSON)


# PD2 datasets carry explicit provenance/availability; null is distinct from zero.
class DataCapability(Base):
    __tablename__="data_capabilities"
    key: Mapped[str]=mapped_column(String(300),primary_key=True)
    provider: Mapped[str]=mapped_column(String(40),index=True)
    capability: Mapped[str]=mapped_column(String(60),index=True)
    subject: Mapped[str]=mapped_column(String(180),index=True)
    status: Mapped[str]=mapped_column(String(40))
    observed_at: Mapped[int | None]=mapped_column(BigInteger,nullable=True)
    received_at: Mapped[int]=mapped_column(BigInteger)
    expires_at: Mapped[int]=mapped_column(BigInteger)
    next_refresh_at: Mapped[int | None]=mapped_column(BigInteger,nullable=True)
    reason: Mapped[str | None]=mapped_column(String(400),nullable=True)
    raw_ref: Mapped[str | None]=mapped_column(String(64),nullable=True)
    # Successful value provenance is kept separate from failed attempts.
    last_success_at: Mapped[int | None]=mapped_column(BigInteger,nullable=True)
    last_success_raw_ref: Mapped[str | None]=mapped_column(String(64),nullable=True)
    last_attempt_at: Mapped[int | None]=mapped_column(BigInteger,nullable=True)
    last_attempt_raw_ref: Mapped[str | None]=mapped_column(String(64),nullable=True)
    payload: Mapped[dict]=mapped_column(JSON,default=dict)

class ProviderBudget(Base):
    __tablename__="provider_budgets"
    provider: Mapped[str]=mapped_column(String(40),primary_key=True)
    month: Mapped[str]=mapped_column(String(7),primary_key=True)
    used: Mapped[int]=mapped_column(Integer,default=0)
    blocked_until: Mapped[int]=mapped_column(BigInteger,default=0)

class IdentityLink(Base):
    __tablename__="identity_links"
    asset_id: Mapped[str]=mapped_column(String(100),primary_key=True)
    provider: Mapped[str]=mapped_column(String(40),primary_key=True)
    external_id: Mapped[str]=mapped_column(String(120))
    status: Mapped[str]=mapped_column(String(40))
    evidence: Mapped[dict]=mapped_column(JSON)
    known_at: Mapped[int]=mapped_column(BigInteger)

class NotificationTarget(Base):
    __tablename__="notification_targets"
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    chat_id: Mapped[str]=mapped_column(String(80))
    enabled: Mapped[bool]=mapped_column(Boolean,default=False)
    authorized_at: Mapped[int]=mapped_column(BigInteger)
    subscriptions: Mapped[dict]=mapped_column(JSON,default=dict)

class PlanOutcome(Base):
    __tablename__="plan_outcomes"
    plan_id: Mapped[str]=mapped_column(ForeignKey("plans.id"),primary_key=True)
    horizon_hours: Mapped[int]=mapped_column(Integer,primary_key=True)
    state: Mapped[str]=mapped_column(String(40))
    evaluated_at: Mapped[int]=mapped_column(BigInteger)
    payload: Mapped[dict]=mapped_column(JSON)

class UserPreference(Base):
    __tablename__="user_preferences"
    key: Mapped[str]=mapped_column(String(120),primary_key=True)
    payload: Mapped[dict]=mapped_column(JSON)
    updated_at: Mapped[int]=mapped_column(BigInteger)

class SnapshotDataset(Base):
    __tablename__='snapshot_datasets'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    created_at: Mapped[int]=mapped_column(BigInteger)
    payload: Mapped[dict]=mapped_column(JSON)


class CurrentSignal(Base):
    """Worker-owned list projection; heavy immutable evidence stays in snapshots."""
    __tablename__ = 'current_signals'
    __table_args__ = (Index('ix_current_signals_action_computed','action','computed_at'),)
    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(100), index=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey('instruments.id'))
    action: Mapped[str] = mapped_column(String(30), index=True)
    first_seen_at: Mapped[int] = mapped_column(BigInteger, index=True)
    computed_at: Mapped[int] = mapped_column(BigInteger)
    valid_until: Mapped[int] = mapped_column(BigInteger, index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class Observation(Base):
    __tablename__ = 'observations'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(180), index=True)
    asset_id: Mapped[str] = mapped_column(String(100), index=True)
    direction: Mapped[str] = mapped_column(String(10))
    first_at: Mapped[int] = mapped_column(BigInteger)
    last_at: Mapped[int] = mapped_column(BigInteger, index=True)
    visible_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    version: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSON)
