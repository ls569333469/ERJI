"""Render only saved, finite R3 activation evidence. No DB or exchange access."""
import html
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT.parents[2] / 'docs'
OUT = ROOT / 'docs/p40-validation/r3-activation-20261010'


def read(name):
    return json.loads((OUT / name).read_text('utf-8-sig'))


def beats(e):
    return {b['role']: b for b in e['queries']['heartbeats']}


def total_tasks(e):
    sums = Counter()
    for row in e['queries']['tasks']:
        sums[row['kind']] += int(row['completed'] or 0)
    return sums


def main():
    e, first = read('final.json'), read('initial.json')
    ident = read('process-identity-final.json')
    startup = json.loads((Path(e['registry'][0]['log_dir']) / 'startup.json').read_text('utf-8-sig'))
    workers = [v['startup'] for v in e['logs'].values() if v.get('startup')]
    activated = (startup['status'] == 'ready' and all(e['ports'].values())
                 and not e['source_hash_mismatches'] and len(ident) == 6
                 and all(v['identity_matches'] and v['command_matches'] for v in ident)
                 and len(workers) == 4
                 and all(v['code_generation'] == 'P40-reliability-r3' and v['run_id'] == e['run_id'] for v in workers)
                 and all(not v.get('startup_hash_mismatches') for v in e['logs'].values()))
    assert activated, 'Incomplete activation identity; must not report activated'
    b = beats(e)
    d, owner = b['anomaly_discovery']['details'], b['analysis_owner']['details']
    pools = owner.get('database_timings', {}).get('connections', {})
    task_delta = dict(total_tasks(e) - total_tasks(first))
    cov = next(v for v in e['queries']['coverage'] if v['venue'] == 'binance')
    closes = []
    for r in e['registry']:
        closes += read('final-' + r['name'] + '-lifecycles.json')
    close_reasons = Counter((r.get('end_reason'), r.get('exception'),
                             r.get('close', {}).get('sent', {}).get('reason')) for r in closes)
    closing_pipeline = {}
    for r in closes:
        key = r.get('venue', '') + ':' + r.get('channel', '')
        c = closing_pipeline.setdefault(key, {'closes':0, 'max_event_age_ms':0, 'max_writer_wait_p95_ms':0, 'max_loop_lag_ms':0})
        c['closes'] += 1
        pipe = r.get('pipeline', {}); timing = pipe.get('receive_timings', {})
        c['max_event_age_ms'] = max(c['max_event_age_ms'], timing.get('event_age_ms', {}).get('max') or 0)
        c['max_writer_wait_p95_ms'] = max(c['max_writer_wait_p95_ms'], timing.get('writer_wait_ms', {}).get('p95') or 0)
        c['max_loop_lag_ms'] = max(c['max_loop_lag_ms'], pipe.get('loop_lag', {}).get('max_ms') or 0)
    rows = e['queries']['latest_projection_samples']
    matched = [r for r in rows if r['latest_snapshot'] == r['projected_snapshot']]
    quote = [{ 'role': v['role'], **v['details']['quote_writer']}
             for v in e['queries']['heartbeats'] if v['details'].get('quote_writer')]
    api = read('final-buy-api.json')
    buy_rows = [{k: r.get(k) for k in ('symbol', 'instrument_id', 'plan_id', 'snapshot_id', 'action_code', 'decision_as_of', 'valid_until')}
                for r in api.get('rows', []) if r.get('opportunity_level') == 'l1' and r.get('action_code') in ('participate', 'trial')]
    warnings = sum(v['long_connections'] for v in e['logs'].values())
    faults = {k: sum(v[k] for v in e['logs'].values()) for k in ('tracebacks', 'timeouts', '10055', 'rate_limit_429', 'ban_418')}
    issues = []
    for minutes, v in d['window_coverage'].items():
        if not v['eligible'] or v['ready'] / v['eligible'] < .7:
            issues.append(f"价格{minutes}m窗口 {v['ready']}/{v['eligible']}，缺失分类 {v['unavailable']}")
    if warnings:
        issues.append(f'新批次数据库连接长占用警告 {warnings} 次，尚未消失')
    failed_closes = [r for r in closes if r.get('end_reason') == 'failure']
    if failed_closes:
        issues.append(f'新批次接收器异常关闭 {len(failed_closes)} 次；主动订阅变化另列')
    btc = e['queries']['btc_5m_continuity'][0]
    if btc['gaps']:
        issues.append(f"BTC最近一小时已收盘5m K线有 {btc['gaps']} 个间隔缺口（含重启前后历史，不能全部归因R3）")
    if e['blind_spots']:
        issues.append(f"检查盲区 {e['blind_spots']}")
    traces = {symbol:read(symbol+'-event-trace.json') for symbol in ('luna2','zk')}
    luna_times = [v for v in traces['luna2']['event_timings'] if (v.get('source_at') or 0)>=e['run_start_ms']]
    if any(not v.get('event_id') for v in luna_times):
        issues.append('LUNA2本轮新端点已有分析完成结果，但结果来源的event_id缺失，完整事件身份追踪尚不完整')
    status = 'activated_with_followup' if issues else 'activated_finite_checks_passed'
    result = dict(status=status, activated=activated, code_generation='P40-reliability-r3',
                  run_id=e['run_id'], captured_at_beijing=e['captured_at_beijing'], age_seconds=e['age_seconds'],
                  ports=e['ports'], source_hashes_match=True, process_identities_match=True,
                  database_restarted=False, migration_required=False,
                  postgres_new_deadlocks=e['postgres_new_deadlocks'], current_run_error_counts=faults,
                  current_run_long_connection_warnings=warnings,
                  price_window_coverage=d['window_coverage'], oi_window_coverage=d['oi_window_coverage'],
                  window_statistics_at=d['snapshot_at'], window_snapshot_age_ms=e['as_of']-d['snapshot_at'],
                  contract_coverage=cov, oi_committed_this_run=e['queries']['oi_committed_this_run'],
                  task_completion_increments_between_samples=task_delta,
                  task_scope='execution counts, including repeats and recovery; not new independent coins',
                  lane_slots=owner.get('lane_slots'), producer=owner.get('producer'),
                  work_queues=owner.get('by_work_kind'), database_connections=pools,
                  close_records=len(closes), close_reasons=[{'reason':list(k),'count':v} for k,v in close_reasons.items()],
                  closing_pipeline_by_channel=closing_pipeline, price_receipts=d.get('price_receipts'),
                  quote_queues=quote, projection_sample=len(rows), projection_matched=len(matched),
                  matched_projection_max_latency_ms=max((r['decision_to_projection_ms'] for r in matched), default=None),
                  buy_api_sample=buy_rows, buy_sample_as_of=api.get('as_of'),
                  blocking_transactions=e['queries']['blocking'], long_transactions=e['queries']['long_transactions'],
                  btc_5m_continuity=btc, current_run_analysis_results=e['queries']['results_this_run'],
                  new_or_updated_event_samples=e['queries']['new_observations'],
                  sampled_event_traces={k:{'as_of':v['as_of'],'instrument_id':v['instrument_id'],
                      'analyses':v['analyses'],'event_timings':v['event_timings']} for k,v in traces.items()},
                  issues=issues, blind_spots=e['blind_spots'],
                  scope='finite read-only local verification after authorized application restart; no recurring monitor or trading enabled')
    (OUT / 'activation-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), 'utf8')
    stages = []
    for phase in ('initial', 'minute4', 'minute5', 'minute10', 'final'):
        v = read(phase + '.json'); vd = beats(v)['anomaly_discovery']['details']
        wc = vd['window_coverage']; vc = next(c for c in v['queries']['coverage'] if c['venue']=='binance')
        stages.append(f"| {phase} / {v['age_seconds']}s | {wc['5']['ready']}/{wc['5']['eligible']} | {wc['15']['ready']}/{wc['15']['eligible']} | {vc['oi_6m']}/{vc['markets']} | {v['as_of']-vd['snapshot_at']}ms |")
    issues_md = '\n'.join('- ' + s for s in issues) or '- 本次有界检查未发现上述异常；不代表长期运行已验证。'
    close_md = '\n'.join(f"- {k}：{v} 次。" for k,v in close_reasons.items()) or '- 新批次未记录关闭。'
    window = d['oi_window_coverage']['15']
    verdict = '仍有数据问题，不能判定全部恢复' if issues else '本次有限检查通过'
    md = f'''# P40 R3 · 重启激活与验证

**R3已重启激活；{verdict}。**

核验时刻：{e['captured_at_beijing']}（北京时间）。运行批次：`{e['run_id']}`。已运行 {e['age_seconds']} 秒。

## 激活身份与服务

- 六个注册服务的PID、启动ticks、命令身份匹配，均属本批次；四个worker启动版本为`P40-reliability-r3`，启动源文件哈希与磁盘一致，21个实施文件哈希无变化。
- 18770 / 18771 / 18772 / 55432均可连接；健康、运行状态、观察、买入、因果追踪API及两张业务页面均返回200。HTTP响应不作为行情全部正常的证明。
- 数据库原进程保留；版本仍`p39_layered_analysis`，分级表和快照查询索引有效。本轮没有新增迁移。
- 新批次PostgreSQL死锁 {e['postgres_new_deadlocks']} 次；错误计数`{faults}`。采样时阻塞事务 {len(e['queries']['blocking'])}、超过8秒事务 {len(e['queries']['long_transactions'])}。

## 实测数据

| 样本 / 启动时间 | 价格5m覆盖 | 价格15m覆盖 | 合约OI6m覆盖 | 窗口快照距采样 |
| --- | --- | --- | --- | --- |
{chr(10).join(stages)}

- 最终合约状态 {cov['markets']} 个，报价90s内 {cov['quote_90s']}、OI90s内 {cov['oi_90s']}、OI6m内 {cov['oi_6m']}、成交额10m内 {cov['volume_10m']}。
- OI15m独立窗口可算 {window['ready']}/{window['eligible']}，分类`{window['unavailable']}`；OI端点90s资格、6m采集新鲜、价格15m连续性分别统计，不混用。
- 当前run OI提交：`{e['queries']['oi_committed_this_run']}`。不是独立异动币数量。
- 两次样本之间任务完成增量：`{task_delta}`；含重复复核与存量恢复，是执行次数。当前槽位`{owner.get('lane_slots')}`。
- 最新快照匹配抽查 {len(matched)}/{len(rows)}，匹配项决策到投影最大 {result['matched_projection_max_latency_ms']}ms。样本先限定300个较晚到期计划再取30个最新快照，不当作全市场分布。
- 最终买入API前5行中L1：`{buy_rows}`。这是采样时有效的策略结果，不等于新T1异动已完整穿过全部链路。
- BTC最近一小时已收盘5m K线 {btc['bars']} 根，间隔缺口 {btc['gaps']}，最新开盘时间 {btc['latest_open']}。此为单市场抽查，包含本轮重启之前的历史。
- 报价pending必须结合最老年龄：本次报价写队列样本最大最老年龄 {max((v.get('oldest_pending_age_ms',0) for v in quote),default=0)}ms；合并计数不作为丢包数。

## 剩余问题

{issues_md}

接收器关闭原因：

{close_md}

R3诊断已记录双向关闭码、每连接源事件年龄、writer等待、写队列及事件循环延迟。`sent=1011 / keepalive ping timeout`表示客户端未及时收到Pong而关闭，并非证明交易所主动拒绝。市场逐笔通道的源事件年龄与writer等待一并升高时，消费者追赶不及是需要优先隔离验证的候选；仅凭关闭码不能确定网络或数据库单一根因。global报价通道、market事实通道、depth盘口通道必须分别评估，不能将逐笔通道重连次数直接等同于T1价格窗断档次数。

按通道分组的关闭管线实测最大值：`{closing_pipeline}`。source_age_invalid表示收到时源时间已超过现有口径；sampling_interval与wrong_price_kind分别为取样合并及阻止混用价格口径，不能全部算作网络故障。

连接长占用警告在归还时记录，不等于连接泄漏。query与analysis/urgent/gap围栏分别保留用途与阶段分布，详见`activation-result.json`。当前run阶段计数保留；by_work_kind的最近20分钟分布可能含上一run，不作为纯R3耗时分布。

新事件有界样本和原生因果追踪响应均已保存。约00:33的原生追踪样本中，LUNA2新端点已进入结构/趋势完成结果，结构为waiting_trigger，但event_id缺失。ZK新端点事件887a10fb…的分析完成耗时为62376ms；其当时计划/投影仍保留较早c27e5df1…事件来源，且动作no_chase，因此不能称为该新事件已成为有效买入。重启前事件的source_at即使在本轮完成分析，也不能称为本轮新发现；没有满足现有规则的信号不单独算故障。未对真实新事件做浏览器首达计时，不声称该项已验证。

## 下一步

先依据本批次关闭记录和逐币空档区分源更新稀疏、客户端接收背压、写入等待与网络重连；针对同一原生市场的连接身份和时间轴取证。保持15m OI、T1连续15s、买入盘口90s和现有策略口径，不通过缩窗或延长资格掩盖问题。

证据目录：`{OUT.as_posix()}`。本次仅应用重启与有限本地只读验证，没有恢复持续监控、启用交易或外部通知。
'''
    (DOCS / 'P40_R3_激活验证结果.md').write_text(md, 'utf8')
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P40 R3 · 激活验证</title><style>body{{font:16px/1.8 system-ui;background:#0c1220;color:#e5eaf4;margin:0}}main{{max-width:1080px;margin:32px auto;padding:24px}}h1{{font-size:30px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#121d30;padding:24px;border-radius:12px;font-family:inherit}}a{{color:#80c7ff}}</style><main><h1>P40 R3 · 激活验证</h1><p>{html.escape(verdict)}</p><pre>{html.escape(md)}</pre><a href="/signals">买入机会</a> · <a href="/observations">市场观察</a></main></html>'''
    (ROOT / 'apps/web/public/previews/P40_R3_activation.html').write_text(page, 'utf8')
    (OUT / 'P40_R3_activation.html').write_text(page, 'utf8')
    acceptance = ROOT / 'apps/web/public/previews/P40_R3_acceptance.html'
    text = acceptance.read_text('utf8').replace('代码已更新 · 待重启激活', '代码已更新 · R3已激活')
    marker = '<!-- R3_ACTIVATION_LINK -->'
    if marker not in text:
        text = text.replace('<main>', '<main>'+marker+'<section><b>最新激活验证</b><p>'+html.escape(verdict)+' · <a href="P40_R3_activation.html">查看实测结果</a></p></section>', 1)
    acceptance.write_text(text, 'utf8')
    status_note = f"> 最新进度（{e['captured_at_beijing']}）：R3已重启激活，版本/源码/端口核验通过；{verdict}。详见`P40_R3_激活验证结果.md`。"
    for name in ('P40_ERJI_异动疏通与数据可靠性修复方案.md', 'P40_R3_激活后问题调查与修订方案.md'):
        p = DOCS / name; text = p.read_text('utf-8-sig')
        text = re.sub(r'<!-- CODEX_R3_STATUS_START -->.*?<!-- CODEX_R3_STATUS_END -->', '<!-- CODEX_R3_STATUS_START -->\n'+status_note+'\n<!-- CODEX_R3_STATUS_END -->', text, flags=re.S)
        marker = '<!-- CODEX_R3_ACTIVATION_START -->'
        section = marker+'\n\n## R3重启激活验证 · '+e['captured_at_beijing']+'\n\n'+status_note+'\n\n'+issues_md+'\n\n批次`'+e['run_id']+'`；前文尚未激活为实施历史状态。完整数字、关闭分类与证据见[P40_R3_激活验证结果.md](P40_R3_激活验证结果.md)。\n\n<!-- CODEX_R3_ACTIVATION_END -->'
        if marker in text:
            text = re.sub(r'<!-- CODEX_R3_ACTIVATION_START -->.*?<!-- CODEX_R3_ACTIVATION_END -->', lambda _:section, text, flags=re.S)
        else:
            text += '\n\n'+section+'\n'
        p.write_text(text, 'utf8')
    review = DOCS / 'P40_评审往来.md'
    marker = '## GPT / Codex · R3激活验证 · '+e['run_id']
    if marker not in review.read_text('utf-8-sig'):
        with review.open('a', encoding='utf8') as f:
            f.write('\n\n'+marker+'\n\n'+status_note+'\n\n'+issues_md+'\n\n请依据本run证据复核关闭原因、逐币空档和写入等待，勿把历史任务分布或旧事件当作新run实测；详见激活验证结果与原始JSON。\n')
    imp = ROOT / 'docs/p40-validation/r3-implementation-20261009/implementation-result.json'
    value = json.loads(imp.read_text('utf8'))
    value.setdefault('status_at_implementation', value['status'])
    value.update(status=status, activation_evidence=str(OUT / 'activation-result.json'), activation_run=e['run_id'])
    imp.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf8')
    p = DOCS / 'P40_R3_代码更新结果.md'
    text = p.read_text('utf-8-sig')
    stamp = '<!-- CODEX_R3_ACTIVATION_STATUS -->'
    if stamp not in text:
        lines = text.splitlines();lines[1:1]=['', stamp, status_note, '']
        p.write_text('\n'.join(lines)+'\n', 'utf8')
    print(json.dumps({'status': status, 'run': e['run_id'], 'report': str(DOCS/'P40_R3_激活验证结果.md'), 'issues':issues},ensure_ascii=False))


if __name__ == '__main__':
    main()
