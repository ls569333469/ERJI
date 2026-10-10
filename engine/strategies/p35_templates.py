"""Read-only individual strategy templates, tied to the executing rule version."""
from engine.strategies.p33 import NAMES, VERSION, CONFIG_HASH, PARAMETERS
from engine.strategies.p33_lifecycle import DEPENDENCIES
from engine.strategies.structures import VERSION as BOX_VERSION
RULES = {
'box_breakout':('4H原始整理区及20/60根结构；保留原箱体规则。','箱体、上下沿、触发线、确认柱'),
'recent_high':('触发前30根日线最高价；4H收盘超过该价及缓冲。','冻结前高、突破标记'),
'ath':('同市场上市以来连续历史证明；4H收盘突破已证实最高位。历史不足不称ATH。','历史高点、突破标记'),
'squeeze':('4H布林带宽120根基线20%分位，前5根至少3根收缩；突破20根上沿且带宽扩大。','布林带、收缩时段、突破边界'),
'double_bottom':('已确认的两个低点间隔至少5根，价差不超过0.5 ATR；突破两低点之间颈线。','两低点、颈线、连接线、突破标记'),
'early_strength':('4H确认低点抬高；1H MACD柱由非正转正并突破确认前高。周线偏空不一票否决。','确认前高、确认柱、MACD'),
'trend_pullback':('日线高低点抬高且EMA20在EMA50之上；4H回踩EMA20附近后收回，1H突破回踩柱高点。','EMA、回踩参考、低点、恢复标记'),
'failed_breakdown':('4H刺破已确认支撑后收回；1H收盘突破收回柱高点。盘中刺穿不等于反转确认。','原支撑、跌破低点、收回与确认标记'),
'retest':('继承同市场有效父突破；6根4H以内出现回踩承接，1H再次收盘突破原边界。','父突破位、回踩点、再次确认标记'),
'repeated_test':('45根日线高点附近至少两次确认高点试探，间隔至少3根并有1 ATR回撤；4H再突破。','同一阻力、试探点、最终确认标记'),
}
ALIASES={'early_breakout':'box_breakout','confirmed_breakout':'box_breakout','trend_restart':'box_breakout','retest_entry':'retest'}
def template(rule):
    rule=ALIASES.get(rule,rule)
    if rule not in RULES:return None
    description,drawing=RULES[rule]
    return {'id':rule,'name':NAMES.get(rule,'箱体突破'),'version':VERSION if rule!='box_breakout' else BOX_VERSION,
        'config_hash':CONFIG_HASH if rule!='box_breakout' else None,'description':description,
        'dependencies':DEPENDENCIES.get(rule,{'240':60,'60':52}),
        'confirmation':'收盘越过边界+max(2个最小价格单位,0.1 ATR)，报价成交额相对前20根至少1.5倍。' if rule!='box_breakout' else '使用既有箱体早期/确认触发规则，实际数值以事件证据为准。',
        'participation':'触发后检查当前参与带、真实保护位、上方确认阻力、最新报价与盘口成本；离开参与带等待回踩。等待结构不等于买入。',
        'exit':'保护触及、结构失效或参与窗口结束时更新原事件；首次边界与计划保留，旧事件不重新冒充新触发。',
        'targets':'优先取入场上沿之上的已确认4H、日线、周线摆动阻力，区域为确认点 ± 0.25 ATR；最近者为第一目标。没有目标时如实缺失，不造ATH上方阻力。','parameter_source':'本系统当前实现默认；未声明为行业有效参数。','drawing':drawing,'research_status':'未经绩效验证','case_policy':'同版本、同周期及同规则证据比较；列出差异和缺失，不强配案例，不输出未经标定的相似百分比。'}
def catalog():return [template(rule) for rule in RULES]
def document(rule):
    import html
    row=template(rule)
    if not row:return None
    labels={'version':'规则版本','dependencies':'必需周期与最低根数（另需指标预热）','description':'识别形态','confirmation':'确认条件','participation':'如何参与','targets':'目标依据','parameter_source':'参数出处','exit':'持续追踪与退出','drawing':'图上画什么','research_status':'研究状态','case_policy':'历史案例比较'}
    body=''.join('<section><h2>'+label+'</h2><p>'+html.escape(str(row[key]))+'</p></section>' for key,label in labels.items())
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'+row['name']+'</title><style>body{max-width:900px;margin:48px auto;padding:24px;font:16px/1.8 system-ui;color:#183444;background:#f4f7fa}section{background:white;padding:16px 24px;margin:16px 0;border:1px solid #d9e2ea;border-radius:10px}h2{font-size:18px}</style><h1>'+row['name']+'</h1><p>P35 · 独立策略模板 · 价格阈值以当前事件为准</p>'+body+'</html>'
