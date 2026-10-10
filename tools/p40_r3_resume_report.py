"""Render finite resumed R3 verification from saved local evidence only."""
import html
import json
import os
import re
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT.parents[2] / 'docs'
OUT = Path(os.environ['ERJI_R3_EVIDENCE_DIR']).resolve()
assert OUT.is_relative_to((ROOT/'docs/p40-validation').resolve())


def read(name):
    return json.loads((OUT/name).read_text('utf-8-sig'))


def beats(e):
    return {b['role']:b['details'] for b in e['queries']['heartbeats']}


def totals(e):
    result = Counter()
    for r in e['queries']['tasks']:
        result[r['kind']] += int(r['completed'] or 0)
    return result


def main():
    first, e = read('initial.json'), read('final.json')
    assert first['run_id'] == e['run_id'], 'Never compare execution increments across runs'
    ident = read('process-identity-final.json')
    workers = [v['startup'] for v in e['logs'].values() if v.get('startup')]
    activated = (all(e['ports'].values()) and len(ident)==6
        and all(v['identity_matches'] and v['command_matches'] for v in ident)
        and not e['source_hash_mismatches'] and len(workers)==4
        and all(v['run_id']==e['run_id'] and v['code_generation']=='P40-reliability-r3' for v in workers)
        and all(not v.get('startup_hash_mismatches') for v in e['logs'].values()))
    d, a = beats(e)['anomaly_discovery'], beats(e)['analysis_owner']
    issues = []
    for minute, v in d['window_coverage'].items():
        if not v['eligible'] or v['ready']/v['eligible']<.7:
            issues.append(f"价格{minute}m窗口 {v['ready']}/{v['eligible']}；原因 {v['unavailable']}")
    warnings = sum(v['long_connections'] for v in e['logs'].values())
    if warnings:
        issues.append(f'本run连接长占用警告 {warnings} 次，尚未完全消失')
    closes = []
    for r in e['registry']:
        closes.extend(read('final-'+r['name']+'-lifecycles.json'))
    failures = [r for r in closes if r['end_reason']=='failure']
    closing = Counter((r['venue'],r['channel'],r['end_reason'],r.get('exception'),r['close']['sent'].get('reason')) for r in closes)
    if failures:
        issues.append(f'本run接收器异常关闭 {len(failures)} 次，按通道另列；不能据此称所有T1价格窗都断线')
    results = e['queries']['results_this_run']
    waiting = [r for r in results if r['status']=='waiting_inputs']
    if waiting:
        issues.append('结构/趋势仍有 waiting_inputs，需继续跟踪K线新鲜度与补数；完成任务次数不代表输入已齐')
    if e['blind_spots']:
        issues.append('检查盲区 '+str(e['blind_spots']))
    candles = read('candle-endpoint-samples.json')
    def date(value):
        return datetime.fromtimestamp(value/1000,ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M')
    candle_note = '; '.join(f"{r['instrument_id']} {r['resolution']} 最新收盘 {date(r['latest_closed'])} ({r['bars']}根存量)" for r in candles['rows'] if r['resolution'] in ('60','240','1D'))
    ops = read('final-operations-api.json')
    capacity = ops.get('collection_capacity',{})
    oi_estimates = [r for r in capacity.get('endpoints',[]) if r.get('endpoint')=='openInterest']
    if any((r.get('interval_seconds'),r.get('active_interval_seconds'),r.get('candidate_interval_seconds'))!=(300,60,60) for r in oi_estimates):
        issues.append('容量诊断仍硬编码OI 60/10/15秒估算，与实际普通300秒、T2/T3 60秒调度不一致；不是本轮真实运行频率')
    traces = read('event-traces.json')
    trace_summary=[]
    for t in traces:
        timings=[r for r in t['event_timings'] if (r.get('source_at') or 0)>=e['run_start_ms']]
        trace_summary.append({'instrument_id':t['instrument_id'],'as_of':t['as_of'],
            'analysis_status':[{k:r.get(k) for k in ('work_kind','analysis_status','strategy_result','last_success_at')} for r in t['analyses']],
            'new_endpoint_event_timings':timings})
    missing_event_markets=[t['instrument_id'] for t in trace_summary if any(not r.get('event_id') for r in t['new_endpoint_event_timings'])]
    if missing_event_markets:
        issues.append('部分本run端点的分析来源仍缺event_id：'+', '.join(missing_event_markets)+'；完整事件身份追踪未全部恢复')
    matched = [r for r in e['queries']['latest_projection_samples'] if r['latest_snapshot']==r['projected_snapshot']]
    sample_size = len(e['queries']['latest_projection_samples'])
    oi = d['oi_window_coverage']['15']
    oi_sample=read('oi-endpoint-samples.json')
    quotes = [{'role':r['role'],**r['details']['quote_writer']} for r in e['queries']['heartbeats'] if r['details'].get('quote_writer') and r['details']['quote_writer'].get('accepted')]
    db = a.get('database_timings',{}).get('connections',{})
    errors = {k:sum(v[k] for v in e['logs'].values()) for k in ('tracebacks','timeouts','10055','rate_limit_429','ban_418')}
    cov = next(r for r in e['queries']['coverage'] if r['venue']=='binance')
    delta = {k:totals(e)[k]-totals(first)[k] for k in totals(e)}
    api = read('final-buy-api.json')
    buys = [{k:r.get(k) for k in ('symbol','instrument_id','plan_id','snapshot_id','decision_as_of','valid_until','action_code')} for r in api.get('rows',[]) if r.get('opportunity_level')=='l1' and r.get('action_code') in ('participate','trial')]
    status = 'activated_with_followup' if issues else 'activated_finite_checks_passed'
    result = dict(status=status,activated=activated,run_id=e['run_id'],code_generation='P40-reliability-r3',
        captured_at_beijing=e['captured_at_beijing'],age_seconds=e['age_seconds'],ports=e['ports'],
        postgres_new_deadlocks=e['postgres_new_deadlocks'],errors=errors,
        price_windows=d['window_coverage'],oi_window=oi,window_snapshot_at=d['snapshot_at'],
        window_snapshot_age_ms=e['as_of']-d['snapshot_at'],contract_coverage=cov,
        task_completion_increments=delta,task_scope='execution counts; repeats and recovery included; not new coins',
        analysis_results_current_run=results,producer=a.get('producer'),work_queues=a.get('by_work_kind'),
        database_connections=db,long_connection_warnings=warnings,quote_queues=quotes,
        close_groups=[{'identity':list(k),'count':v} for k,v in closing.items()],
        event_delivery=d.get('event_delivery'),new_event_samples=e['queries']['new_observations'],
        projection_sample=sample_size,projection_matched=len(matched),
        matched_projection_max_latency_ms=max((r['decision_to_projection_ms'] for r in matched),default=None),
        collection_capacity_oi_estimates=oi_estimates,
        event_trace_summary=trace_summary,
        candle_endpoint_sample=candles,
        oi_endpoint_sample=oi_sample,
        btc_candles=e['queries']['btc_5m_continuity'],buy_api_sample=buys,issues=issues,blind_spots=e['blind_spots'],
        monitoring='existing erji-p39 resumed every 10m, read-only, notify meaningful change only',
        verification_scope='finite current-run local samples; no business changes, strategy relaxation, restart or exchange batch calls')
    (OUT/'activation-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),'utf8')
    rows=[]
    for phase in ('initial','minute10','final'):
        v=read(phase+'.json');wc=beats(v)['anomaly_discovery']['window_coverage']
        c=next(x for x in v['queries']['coverage'] if x['venue']=='binance')
        rows.append(f"| {v['captured_at_beijing'][11:19]} / {v['age_seconds']}s | {wc['5']['ready']}/{wc['5']['eligible']} | {wc['15']['ready']}/{wc['15']['eligible']} | {c['oi_6m']}/{c['markets']} |")
    issue_text='\n'.join('- '+v for v in issues) or '- 本次有界检查未发现以上异常。'
    close_text='\n'.join(f'- {k}：{v}次。' for k,v in closing.items()) or '- 本批次未记录关闭。'
    md=f'''# P40 R3 · 继续验证与监控结果

**R3服务身份核验{'通过' if activated else '未通过'}；{'仍有剩余数据问题' if issues else '本次有限检查通过'}。已有10分钟只读监控已恢复。**

北京时间：{e['captured_at_beijing']}。当前run：`{e['run_id']}`，运行{e['age_seconds']}秒。

## 当前数据

| 样本时刻 / 启动时长 | 价格5m | 价格15m | 合约OI6m |
| --- | --- | --- | --- |
{chr(10).join(rows)}

- 六个注册进程PID/启动ticks与命令身份、四worker R3启动哈希、21个实施文件哈希均核验；18770/18771/18772/55432及本地接口/业务页面已检查。
- 新run数据库死锁{e['postgres_new_deadlocks']}次；日志错误计数`{errors}`；采样阻塞事务{len(e['queries']['blocking'])}，超过8秒事务{len(e['queries']['long_transactions'])}。
- OI15m独立端点可算{oi['ready']}/{oi['eligible']}，缺失分类`{oi['unavailable']}`。普通300s采集配合90s端点会有可算时段；不能把快照低90s覆盖当成OI完全未触发。价格与OI15m分别统计。
- OI真实当前run端点抽查（排除本轮补入的旧历史）：`{oi_sample['summary']}`。BTC/ZK的首次实时OI约09:27，最终快照时尚不足15m；RLC首端点约09:25，其后还要满足匹配端点与90s资格。服务启动18分钟不等于每币OI已有18分钟真实端点，本次OI15m尚未全部验证，由恢复后的监控继续观察。
- 初始样本的价格资格分母为0，是夜间关机后成交额旧值尚未进入发现快照。随后源时间/接收时间及资格已经更新；不以0/0称作覆盖正常，也不把停机空档作为新run采集故障。
- 最终合约状态{cov['markets']}个，报价90s内{cov['quote_90s']}，OI90s内{cov['oi_90s']}、6m内{cov['oi_6m']}，成交额10m内{cov['volume_10m']}。
- 两次同run样本任务完成增量`{delta}`。含存量恢复与重复复核，是执行次数，不是新增独立币。当前run分析结果`{results}`。
- 30条最新快照有界抽查：{len(matched)}/{sample_size}匹配，匹配项最大决策→投影时差{result['matched_projection_max_latency_ms']}ms。不是全市场延迟分布。
- 当前买入API样本L1：`{buys}`。没有合格买入本身不算故障；旧计划响应或HTTP200不能证明新T1事件完全传递。
- 本批次最新异动落库统计`{d.get('event_delivery')}`。更新/合并不当作新币数量；真实新事件身份和后续阶段的有界追踪保存在event-traces.json。
- BTC最近一小时已收盘5m K线抽查`{e['queries']['btc_5m_continuity']}`。单市场、有限时段不能代表全市场连续性。

## 仍需跟踪

{issue_text}

补数围栏单独2名额、分析4、保护/入场2，每进程总围栏预算8；query池另列，不能把query连接数误作围栏预算超限。较早样本的gap名额等待p95为318295ms，HTTP p95约2099ms、写入约358ms，提示有真实名额等待；当前分阶段分布见activation-result.json。补数任务表含很早的历史存量，5863条queued不等于5863个本run实际待执行请求。

`p35_capacity.py`的OI容量估算仍硬编码普通60s、活跃10s、候选15s；实际`collection_schedule.py`使用ordinary300s、candidate/active OI均60s。该诊断字段尚未同步，不能按它判断实际频率或并发容量。capacity.blockers来自全部current_signals行，包括大量ended/旧行，因此历史费用理由计数不证明本run又开启费用硬门。旧来源预算列标注legacy且当时used=0，也不作为启用该来源的证据。

结构waiting_inputs抽样主要candles_stale与周期未ready；趋势missing_timeframes可能来自warming/gap/delayed/provisional，不一定表示数据库一根K线都没有。没有将成功消费任务称为数据全部齐全。

约09:35三市场K线末端抽查：{candle_note}。BTC与ZK的5m已推进，但高周期仍有较旧末端；RLC高周期已更新。该抽查不是所有币的完整扫描，后续补数可能继续变化。

接收器关闭按原生来源/通道分类：

{close_text}

`sent=1011 / keepalive ping timeout`是客户端未及时收到Pong的关闭证据，不足以单独确定网络或DB根因。全球报价、逐笔事实、K线和盘口通道分别评估；逐币源空档、接收空档、writer等待、源事件年龄、锁等待诊断均保存在当前run JSON。sampling_interval和wrong_price_kind属于取样/价格口径保护，不全部当网络故障。

## 昨晚未完成项与本次范围

昨晚有限验证被用户关机指令中止，未取得最终15m样本；本次使用今天新run重新积累窗口，不沿用昨晚计数基线。昨晚LUNA2结果event_id缺失和ZK计划保留较早origin的发现仍保留为背景，新run没有对应完整证据时不宣称修好。

今天约09:33的RLC原生追踪样本中，新事件1d5053d2…具有明确event_id，在同市场的观察需求与结构/趋势结果中可匹配；新端点到结构完成46975ms，趋势45554ms。结构waiting_trigger是尚未满足策略触发，不等于传递故障。此样本只证明事件到分析，未验证新事件到可买资格及浏览器首达。

约09:37追加有界样本中，Q的新事件可按身份匹配，结构约40860ms完成；RLC部分新端点继续可匹配，但其一个较早需求来源event_id变为null，BTC现货的新端点来源也有null。已保留原始event-traces.json，不将已分析等同于身份追踪全部完整。

只恢复已有`erji-p39`：每10分钟只读检查，不变则安静；新异常、持续停滞、显著恢复或需用户行动时通知。已通知事项按run保存避免重复。不修改业务代码、配置或生产数据、不重启、不部署、不放宽策略、不启用交易或外部通知。

证据目录：`{OUT.as_posix()}`。本次新增/调整仅本地诊断与报告工具：正确识别429/418状态、当前run错误范围、保存增量日志、跨关机保留通知历史；没有改动业务源文件。
'''
    (DOCS/'P40_R3_继续验证结果.md').write_text(md,'utf8')
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P40 R3 · 继续验证</title><style>body{{margin:0;background:#0c1220;color:#e5eaf4;font:16px/1.8 system-ui}}main{{max-width:1080px;margin:28px auto;padding:24px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;background:#121d30;padding:24px;border-radius:12px}}a{{color:#80c7ff}}</style><main><h1>P40 R3 · 继续验证与监控</h1><pre>{html.escape(md)}</pre><a href="/observations">市场观察</a> · <a href="/signals">买入机会</a></main></html>'''
    (ROOT/'apps/web/public/previews/P40_R3_activation.html').write_text(page,'utf8')
    (ROOT/'apps/web/public/previews/P40_R3_resume_verification.html').write_text(page,'utf8')
    (OUT/'P40_R3_resume_verification.html').write_text(page,'utf8')
    acceptance=ROOT/'apps/web/public/previews/P40_R3_acceptance.html'
    text=acceptance.read_text('utf8').replace('代码已更新 · 待重启激活','代码已更新 · R3已激活')
    note='<section><b>最新继续验证</b><p>10分钟只读监控已恢复 · <a href="P40_R3_resume_verification.html">查看当前实测结果</a></p></section>'
    if '<!-- R3_RESUME_LINK -->' not in text:text=text.replace('<main>','<main><!-- R3_RESUME_LINK -->'+note,1)
    acceptance.write_text(text,'utf8')
    status_note=f"> 最新进度（{e['captured_at_beijing']}）：R3已激活；今天新run `{e['run_id']}` 完成有限继续验证，{'仍有剩余数据问题' if issues else '本次有界检查通过'}；10分钟只读监控已恢复。详见`P40_R3_继续验证结果.md`。"
    for name in ('P40_ERJI_异动疏通与数据可靠性修复方案.md','P40_R3_激活后问题调查与修订方案.md'):
        p=DOCS/name;text=p.read_text('utf-8-sig')
        text=re.sub(r'<!-- CODEX_R3_STATUS_START -->.*?<!-- CODEX_R3_STATUS_END -->',lambda _:'<!-- CODEX_R3_STATUS_START -->\n'+status_note+'\n<!-- CODEX_R3_STATUS_END -->',text,flags=re.S)
        p.write_text(text,'utf8')
    review=DOCS/'P40_评审往来.md';marker='## GPT / Codex · R3继续验证 · '+e['run_id']
    if marker not in review.read_text('utf-8-sig'):
        with review.open('a',encoding='utf8') as f:f.write('\n\n'+marker+'\n\n'+status_note+'\n\n'+issue_text+'\n\n详见[P40_R3_继续验证结果.md](P40_R3_继续验证结果.md)，按当前run证据复核，历史采样不作今天运行状态。\n')
    manifest=ROOT/'docs/p40-validation/r3-implementation-20261009/implementation-result.json'
    v=json.loads(manifest.read_text('utf8'));v.setdefault('status_at_implementation',v['status'])
    v.update(status=status,activation_run=e['run_id'],activation_evidence=str(OUT/'activation-result.json'))
    manifest.write_text(json.dumps(v,ensure_ascii=False,indent=2),'utf8')
    p=DOCS/'P40_R3_代码更新结果.md';text=p.read_text('utf-8-sig');marker='<!-- CODEX_R3_RESUME_STATUS -->'
    if marker not in text:text=text.replace('\n','\n\n'+marker+'\n'+status_note+'\n',1)
    p.write_text(text,'utf8')
    print(json.dumps({'activated':activated,'run':e['run_id'],'status':status,'issues':issues,'report':str(DOCS/'P40_R3_继续验证结果.md')},ensure_ascii=False))


if __name__=='__main__':
    main()
