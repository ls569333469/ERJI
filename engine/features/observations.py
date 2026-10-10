"""Arithmetic evidence, kept distinct from strategy stages and recommendations."""
from decimal import Decimal as D

GROUPS = [
    ("up_build", "上涨伴随增仓", "价格与持仓数量同向上升，继续核对结构与买盘", "green"),
    ("up_unwind", "上涨伴随减仓", "价格上涨、仓位解除，核对费用与清算证据", "blue"),
    ("down_build", "下跌伴随增仓", "价格下降、未平仓规模增加，关注反向压力", "amber"),
    ("down_unwind", "下跌伴随减仓", "价格与持仓数量同时下降，核对是否有清算", "red"),
    ("price_move", "价格异动", "价格明显变化，数量持仓相对平稳", "blue"),
    ("oi_move", "持仓异动", "数量持仓明显变化，价格相对平稳", "amber"),
    ("mixed", "变化较小 / 方向未明", "尚无明显的价格与持仓组合", "gray"),
    ("insufficient", "等待可比数据", "当前窗口缺少有效起点，暂不解释方向", "gray"),
    ("stale", "数据已过期", "等待行情恢复后重新判断", "gray"),
]

def paired_changes(q0, p0, q1, n1, quote_asset):
    if q0 is None or p0 is None or n1 is None or q0<=0 or p0<=0 or q1<=0:
        return {}
    # Use the price paired with the OI sample, not a later display quote.
    p1=n1/q1
    return {"price_change_pct":(p1/p0-1)*100,"notional_change_pct":(n1/(q0*p0)-1)*100,
            "quantity_contribution_quote":(q1-q0)*p0,"price_contribution_quote":q1*(p1-p0),"quote_asset":quote_asset}

def describe_market(market, period):
    change=next((c for c in market.oi_changes if c.period==period),None)
    if market.quality.status!="fresh" or market.oi_quality.status=="stale":
        return {"group":"stale","summary":"行情或持仓已过期，等待恢复","reason":"stale"}
    if change is None or change.quality.status!="fresh" or change.value_pct is None or change.price_change_pct is None:
        return {"group":"insufficient","summary":"缺少同窗口价格与持仓端点，方向待判断","reason":change.quality.status if change else "missing"}
    price,oi=change.price_change_pct,change.value_pct
    # Display noise filter only, not an anomaly threshold or a buy/sell rule.
    if abs(price)<D("0.5") and abs(oi)<D("0.5"):
        return {"group":"mixed","summary":"两项变化均小于 0.5%，暂不归入方向组合","reason":"small_change"}
    if abs(oi)<D("0.5"):
        return {"group":"price_move","summary":"价格异动，数量OI相对平稳","reason":"price_only"}
    if abs(price)<D("0.5"):
        return {"group":"oi_move","summary":"数量OI异动，价格相对平稳；不直接代表买入","reason":"oi_only"}
    if price>0 and oi>0:
        return {"group":"up_build","summary":"价格与持仓同步增加；尚需确认突破结构","reason":"paired_samples"}
    if price>0 and oi<0:
        return {"group":"up_unwind","summary":"价格上涨但持仓减少；尚不能区分主动平仓与强平","reason":"paired_samples"}
    if price<0 and oi>0:
        return {"group":"down_build","summary":"下跌时持仓仍增加；不能据此认定新增空头","reason":"paired_samples"}
    return {"group":"down_unwind","summary":"下跌伴随仓位解除；清算方向尚待验证","reason":"paired_samples"}

def observation_groups(markets, period):
    by_asset={}
    for market in markets:
        if market.in_universe:
            by_asset.setdefault(market.asset_id,[]).append(market)
    rows=[]
    for asset, members in by_asset.items():
        members.sort(key=lambda m:(m.quality.status!="fresh",m.venue!="binance",m.venue))
        reference=members[0]
        facts=describe_market(reference,period)
        negative=[m.venue for m in members if m.funding_quality.status=="fresh" and m.funding_rate is not None and m.funding_rate<0]
        rows.append({"asset_id":asset,"reference_instrument_id":reference.instrument_id,
                     "venue_count":len(members),"negative_funding_venues":negative,
                     "period":period,"action":"plan_pending","first_signal_price":None,**facts})
    return rows
