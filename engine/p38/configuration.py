"""Sanitized effective values. Never serialize settings/env wholesale."""
from engine.repository import runtime_config,active_config
from engine.db import Session
from engine.models import UserPreference
from engine.pd2.p36_budget import GLOBAL_SLOTS,SLOTS,LIMITS
from engine.pd2.collection_schedule import OI_INTERVALS
from engine.p37.detector import DEFAULTS

def effective():
    cfg=runtime_config()
    with Session() as s:
        version=active_config(s);anomaly=s.get(UserPreference,'p37_config')
    fields=[]
    def add(name,value,source,consumer,effect):fields.append(dict(name=name,value=value,source=source,consumer=consumer,effect=effect))
    from engine.pd2.source_policy import VERSION,DISABLED
    from engine.p38.jobs import LANES
    add('sources.policy',VERSION,'source_policy.VERSION','采集 / 决策 / 投影 / 健康','进程重新加载')
    add('sources.disabled',sorted(DISABLED),'source_policy.DISABLED','实时请求边界','进程重新加载；历史仍可读')
    add('analysis.lanes',{k:list(v) for k,v in LANES.items()},'p38.jobs.LANES','有界执行通道','进程重新加载；同市场互斥')
    add('budget.global_slots',GLOBAL_SLOTS,'p36_budget.GLOBAL_SLOTS','budget_owner','进程重新加载')
    add('database.admission_slots',8,'engine.db._admission','数据库准入','进程重新加载；独立于请求权重')
    add('analysis.work_kinds',['trend','structure','entry','protect','projection'],'p38.jobs.KINDS','统一执行器','进程重新加载')
    for k,v in SLOTS.items():add('budget.slots.'+k,v,'p36_budget.SLOTS','budget_owner','进程重新加载')
    for k,(cap,window) in LIMITS.items():add('budget.limit.'+k,{'capacity':cap,'window_ms':window},'p36_budget.LIMITS (官方额度90%)','budget_owner','进程重新加载')
    for k,v in OI_INTERVALS.items():add('oi.target.'+k,v/1000,'collection_schedule.OI_INTERVALS','实时OI到期调度','进程重新加载；目标周期，不保证全部市场达标')
    for k in ('collector_enabled','collector_interval_seconds','asset_limit','deep_asset_limit'):
        add(k,getattr(cfg,k),'settings_versions v'+str(version.id if version else 0),'runtime_config / 采集器','发布后下一读取；旧轮次字段仅兼容')
    for k,v in DEFAULTS.items():add('anomaly.'+k,(anomaly.payload if anomaly else {}).get(k,v),'UserPreference:p37_config' if anomaly and k in anomaly.payload else 'detector.DEFAULTS','异动发现 / 观察调度','直接保存；发现器下一次读取，60秒兜底')
    from engine.p39 import timing
    add('timing.policy',timing.VERSION,'p39.timing','全链路工程时钟','进程重新加载')
    for name,value in [('ordinary',timing.ORDINARY_MS),('hot',timing.HOT_MS),('t3',timing.T3_MS),('reference',timing.REFERENCE_MS),('oi_trigger',timing.OI_TRIGGER_MS),('quote',timing.QUOTE_MS),('book',timing.BOOK_MS),('heartbeat',timing.HEARTBEAT_MS),('role_max_age',timing.ROLE_MAX_AGE_MS)]:
        add('timing.'+name+'_seconds',value/1000,'p39.timing','采集 / 调度 / 当前事实 / 健康','进程重新加载')
    add('default_view',cfg.default_view,'settings_versions (兼容)','无当前首页消费者','保留读取，不提供编辑')
    return {'fields':fields,'version':version.id if version else 0,'secrets_included':False}
