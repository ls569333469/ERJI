"""PD2 settings are versioned separately from visual preferences."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Strict(BaseModel):
    model_config=ConfigDict(extra="forbid")
class AnalysisConfig(Strict):
    style: Literal["aggressive","confirmed"]="aggressive"
    timeframes: list[str]=Field(default_factory=lambda:["60","240","1D","1W"],min_length=1,max_length=12)
    pivot_left:int=Field(2,ge=1,le=10)
    pivot_right:int=Field(2,ge=1,le=10)
    zone_atr_multiplier:float=Field(.25,gt=0,le=1)
    trailing_mode:Literal["off","chandelier"]="off"
    trailing_period:int=Field(22,ge=5,le=100)
    trailing_atr_multiple:float=Field(3,ge=1,le=10)
    @model_validator(mode="after")
    def registered(self):
        if len(set(self.timeframes))!=len(self.timeframes) or any(v not in {"15","30","60","120","240","480","720","1D","1W"} for v in self.timeframes):
            raise ValueError("周期须已注册且不重复：15/30/60/120/240/480/720/1D/1W")
        return self
class ExecutionConfig(Strict):
    probe_notional_usd:float=Field(1000,ge=10,le=100000)
    early_min_net_r:float=Field(1.5,ge=.5,le=10)
    confirmed_min_net_r:float=Field(1.8,ge=.5,le=10)
    taker_fee_rates:dict[str,float]=Field(default_factory=lambda:{"binance":.0005,"aster":.0005,"hyperliquid":.00045,"binance_spot":.001,"binance_alpha":.001,"okx":.0005})
    funding_scenario_rate:float=Field(.001,ge=0,le=.1)
    fee_source:str="研究情景：常见基础taker档位；不代表用户账户实际费率，可在设置中调整"
    quote_max_age_seconds:int=Field(15,ge=1,le=15)
    depth_max_age_seconds:int=Field(10,ge=1,le=10)
    max_spread_bps:float=Field(30,gt=0,le=100)
    @model_validator(mode="after")
    def fees(self):
        if any(not 0<=v<=.05 for v in self.taker_fee_rates.values()):raise ValueError("手续费须为0至0.05的小数")
        return self
class SourceBudget(Strict):
    enabled:bool=True
    month_budget:int=Field(10000,ge=100,le=1000000)
    external_used:int=Field(0,ge=0)
    reserve_pct:int=Field(10,ge=10,le=50)
    interval_seconds:int=Field(1800,ge=60,le=86400)
class SourcesConfig(Strict):
    cmc:SourceBudget=Field(default_factory=SourceBudget)
    bykaranteli:SourceBudget=Field(default_factory=lambda:SourceBudget(month_budget=15000))
    wallet_enabled:bool=True
    min_market_cap_usd:int=Field(1000000,ge=0)
    @model_validator(mode="after")
    def free_limits(self):
        if self.bykaranteli.month_budget>15000:raise ValueError("免费方案ByKaranteli月预算不得超过15000")
        return self
class NotificationConfig(Strict):
    enabled:bool=False
    cooldown_seconds:int=Field(1800,ge=60,le=86400)
    ttl_seconds:int=Field(300,ge=30,le=3600)
    public_app_url:str=""
class ValidationConfig(Strict):
    horizons_hours:list[int]=Field(default_factory=lambda:[24,72])
    min_mature_fills:int=Field(200,ge=30)
    min_assets:int=Field(30,ge=5)
    min_days:int=Field(90,ge=30)
    probability_enabled:Literal[False]=False
