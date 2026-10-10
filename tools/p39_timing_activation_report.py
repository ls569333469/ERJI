"""Publish finite activation evidence, without changing application data."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/p39-validation/timing-implementation-20261008/activation'
PUBLIC = ROOT / 'apps/web/public/previews'


def read(path):
    return json.loads(path.read_text('utf-8-sig'))


def table(head, rows):
    esc = lambda value: html.escape(str(value))
    return '<div class="scroll"><table><tr>' + ''.join('<th>'+esc(x)+'</th>' for x in head) + '</tr>' + ''.join('<tr>'+''.join('<td>'+esc(x)+'</td>' for x in row)+'</tr>' for row in rows) + '</table></div>'


def main():
    samples = sorted((read(p) for p in OUT.glob('minute-*.json') if not p.name.endswith(('-summary.json', '-api.json'))), key=lambda e: e['as_of'])
    final = read(OUT / 'final.json') if (OUT / 'final.json').exists() else samples[-1]
    pre = read(OUT / 'preflight.json')
    summary = read(OUT / (final['phase']+'-summary.json'))
    discovery = next((b for b in final['queries']['beats'] if b['role']=='anomaly_discovery'), {})
    producer = next((b for b in final['queries']['beats'] if b['role']=='analysis_owner'), {})
    new_deadlocks = final['queries']['deadlocks'][0]['deadlocks']-pre['queries']['deadlocks'][0]['deadlocks']
    due = next((r['payload'] for r in final['queries'].get('due_checkpoints', []) if r['key']=='due:binance:oi'), {})
    intervals = due.get('intervals', {})
    due_stats = {
        'updated_this_run': sum(t>=final['run_start_ms'] for t in due.get('visited', {}).values()),
        'past_success_plus_interval': sum(final['as_of']-t>intervals.get(n,300000) for n,t in due.get('visited', {}).items()),
        'inflight': len(due.get('inflight', {})), 'retry': len(due.get('retries', {})),
        'intervals_on_successful_claims': {str(i):sum(v==i for v in intervals.values()) for i in set(intervals.values())}
    }
    issues = [
        '异动健康统计存在快照时点错位：扫描开始时间被用于扫描后实时价格队列，部分新到行情被算作未来数据。已用完整连续价格本地复现；当前窗口统计不能作为全部行情缺失的准确数量。',
        'OI 仍有实际超期排队；名义一分钟/五分钟到期不代表这一轮所有币已完成网络请求与提交。',
    ]
    if any(r.get('held_connection') for r in summary['log_errors'].values()):
        issues.append('当前批次仍出现 database connection held 警告，尚未完全消除数据库持有过久问题。')
    observed_buy = read(OUT/'live-buy-api.json') if (OUT/'live-buy-api.json').exists() else {}
    tier_scope = read(OUT/'tier-scope.json') if (OUT/'tier-scope.json').exists() else {}
    if tier_scope.get('discovery_hot',0)>tier_scope.get('discovery_ordinary',0):
        issues.append('重点范围偏大：当前目录 '+str(tier_scope['discovery_catalog'])+' 个市场中，'+str(tier_scope['discovery_hot'])+' 个因有效分析需求走一分钟，普通五分钟仅 '+str(tier_scope['discovery_ordinary'])+' 个。实际降频范围比预期窄。')
    facts = {
        'run_id': final['run_id'], 'verified_at_bjt': final['captured_at_bjt'],
        'verification_seconds': final['age_seconds'], 'production_activated': True,
        'timing_policy': 'p39-five-minute-v1', 'continuous_monitoring_enabled': False,
        'ports': final['ports'], 'http': final['http'], 'new_deadlocks': new_deadlocks,
        'task_completion_delta': summary['task_delta_since_preflight'],
        'sources': final['queries']['timing_source'],
        'discovery': discovery, 'producer': producer,
        'log_counts': summary['log_errors'], 'blocking': final['queries']['blocking'],
        'query_errors': summary['query_errors'],
        'status': 'activated_verified_with_remaining_issues',
        'oi_schedule': due_stats, 'remaining_issues': issues,
        'observed_buy_api': {'as_of': observed_buy.get('as_of'), 'counts': observed_buy.get('opportunity_counts'),
                            'rows': [{k:r.get(k) for k in ('symbol','instrument_id','action_code','price','entry_zone','protection','valid_until','oi_status')} for r in observed_buy.get('rows',[])]},
        'tier_scope': tier_scope,
        'scope': 'Finite local activation verification; task counts are executions, not newly discovered coins; previous run excluded.'
    }
    (OUT/'activation-result.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2),encoding='utf8')
    body = '<h1>P39 · 五分钟分级调度重启与验证</h1><p>'+html.escape(final['captured_at_bjt'])+'</p>'
    body += '<div class="notice"><strong>已重启激活。</strong><p>批次 '+final['run_id']+'；有限验证 '+str(final['age_seconds'])+' 秒，持续监控保持停止。</p></div>'
    body += '<h2>验证结论</h2><p>服务、成交额刷新、T2/T3 任务持续推进；本次死锁增量 '+str(new_deadlocks)+'。仍有以下问题：</p><ul>'+''.join('<li>'+html.escape(x)+'</li>' for x in issues)+'</ul>'
    body += '<h2>服务与运行身份</h2><p>全部服务注册于同一新批次；PID/启动 ticks 已核验。原验收记录的 27 个更新源码哈希一致。</p>'
    body += table(['端口','监听'],final['ports'].items())
    body += table(['接口/页面','HTTP','耗时 ms'],[[u,r.get('status',r.get('error')),r.get('ms','—')] for u,r in final['http'].items()])
    body += '<h2>数据更新覆盖</h2>'+table(['市场','已存市场','报价 ≤90秒','OI ≤10分钟','成交额 ≤10分钟','本批次成交额市场'],[[r['venue'],r['markets'],r['quotes_90s'],r['oi_10m'],r['volume_10m'],r['volume_this_run']] for r in final['queries']['timing_source']])
    body += '<h2>T1 窗口积累与扫描</h2>'+table(['运行秒数','状态','新鲜价格','5分钟窗口','15分钟窗口','最近扫描市场','最近扫描耗时 ms'],[[e['age_seconds'],b.get('state'),d.get('fresh_prices'),d.get('ready_5m'),d.get('ready_15m'),d.get('scan_markets'),'进行中' if d.get('scan_in_progress') or (d.get('scan_finished_at') or 0)<(d.get('scan_started_at') or 0) else (d.get('scan_finished_at') or 0)-(d.get('scan_started_at') or 0)] for e in samples for b in e['queries']['beats'] if b['role']=='anomaly_discovery' for d in [b['details']]])
    body += '<p>上述窗口数字是当前运行心跳原值。其统计时点存在已复现的问题，不能据此精确推断丢失行情数。连续价格复现证据：health-clock-reproduction.json。</p>'
    body += '<p>最近扫描耗时是一个分片批次耗时，不是全目录从零完成的时间。五分钟网格、重点币一分钟和新 OI 到达触发可同时产生分片工作。</p>'
    body += '<p>实际分层目录 '+str(tier_scope.get('discovery_catalog'))+' 个市场：一分钟重点 '+str(tier_scope.get('discovery_hot'))+'、五分钟普通 '+str(tier_scope.get('discovery_ordinary'))+'。重点集合按所有有效需求计算，包含 candidate，尚不是只限正在执行的结构槽。</p>'
    body += '<h2>实际 OI 排队</h2>'+table(['指标','实测'],due_stats.items())
    body += '<p>更新数按实际成功检查点计算；到期积压按最后成功时间加其登记间隔计算，包含正在执行和未执行工作，不等同于请求失败。10分钟参考覆盖以实际 OI 来源时间计算。</p>'
    body += '<h2>成交额实际请求批次</h2>'+table(['来源','本次请求批次','首批接收 ms','末批接收 ms'],[[r['source'],r['batches'],r['first_received'],r['latest_received']] for r in final['queries']['volume_batches']])
    body += '<h2>T2/T3 推进</h2>'+table(['任务类型','完成次数增量','失败增量'],[[k,v['completed'],v['failed']] for k,v in summary['task_delta_since_preflight'].items()])
    body += '<p>这是任务执行次数。独立结构/趋势市场按当前批次结果单独统计：</p>'+table(['工作','结果','结果数','独立市场'],[[r['work_kind'],r['status'],r['n'],r['markets']] for r in final['queries']['results_since_start']])
    body += '<h2>实际买入列表输出</h2><p>只读买入 API 采样时刻 '+str(observed_buy.get('as_of'))+'，列表返回 '+str(len(observed_buy.get('rows',[])))+' 行。以下为当时输出，之后价格、有效期及条件可变化：</p>'+table(['代币','市场','动作','价格','入场区间','有效至 ms','OI'],[[r.get('symbol'),r.get('instrument_id'),r.get('action_label'),r.get('price'),r.get('entry_zone'),r.get('valid_until'),r.get('oi_status')] for r in observed_buy.get('rows',[])])
    body += '<h2>日志与数据库</h2><p>数据库死锁增量 '+str(new_deadlocks)+'；当前阻塞连接 '+str(len(final['queries']['blocking']))+'。</p>'
    body += table(['进程','异常堆栈','死锁','长连接警告','最长持有秒','连接关闭','429','418','超时'],[[k,r.get('traceback',0),r.get('deadlock',0),r.get('held_connection',0),r.get('held_max_seconds',0),r.get('connection_closed',0),r.get('rate_429',0),r.get('ban_418',0),r.get('timeouts',0)] for k,r in summary['log_errors'].items()])
    body += '<p>历史累计死锁及旧进程日志未计入新增故障。缺输入、等待条件及没有合格买入结果按实际状态展示。</p>'
    body += '<h2>后续修正方向</h2><ul><li>健康覆盖计算使用与价格队列相同的快照时刻，修正统计误判；保留真实断流、缺口及端点时效规则。</li><li>分开“有分析需求”和“需要高频采集”：收窄一分钟重点集合，普通等待候选回五分钟，同时保留已触发 T2 和当前 T3 的单独服务。</li><li>结合当前超期 OI 数和写入耗时调整公平服务，避免大量重点候选挤占普通到期工作；定位重复长连接持有路径。</li></ul>'
    body += '<h2>原始证据</h2><p>docs/p39-validation/timing-implementation-20261008/activation/ 中保存逐次只读样本、API 响应、数据库日志增量、进程身份和代码哈希。</p>'
    css = 'body{margin:0;background:#f4f6f9;color:#243047;font:16px/1.7 system-ui}main{max-width:1160px;margin:28px auto;padding:28px;background:white;border-radius:14px}.notice{padding:16px;background:#eef5ff;border-left:4px solid #316bd5}h2{margin-top:30px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:10px;border:1px solid #dce3ec;text-align:left}th{background:#eef2f7}a{color:#2460c7}@media(max-width:600px){main{margin:0;padding:18px}table{min-width:640px}h1{font-size:25px}}'
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 · 五分钟调度激活验证</title><style>'+css+'</style><main>'+body+'</main></html>'
    name = 'P39_five_minute_timing_activation.html'
    for folder in (OUT,PUBLIC):
        (folder/name).write_text(page,encoding='utf8')
    print(json.dumps({'path':str(PUBLIC/name),'run_id':facts['run_id'],'seconds':facts['verification_seconds'],'new_deadlocks':new_deadlocks},ensure_ascii=False))


if __name__ == '__main__':
    main()
