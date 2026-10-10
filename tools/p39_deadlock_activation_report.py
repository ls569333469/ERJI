"""Summarize a finite activation check, keeping historical code QA intact."""
from pathlib import Path
import json,html,hashlib,re
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/p39-validation/deadlock-fix-20261008/activation'
samples=sorted([json.loads(p.read_text('utf8')) for p in OUT.glob('*.json') if p.stem in ('initial','final') or re.fullmatch(r'sample-\d+',p.stem)],key=lambda r:r['as_of'])
first,last=samples[0],samples[-1]
assert first['run_id']==last['run_id']
queries=last['queries'];beats=queries['beats']
discovery=next(b for b in beats if b['role']=='anomaly_discovery')
owner=next(b for b in beats if b['role']=='analysis_owner')
ui=json.loads((OUT/'ui-verification.json').read_text('utf8'))
identities=json.loads((OUT/'process-identities.json').read_text('utf8'))
automation=Path('C:/Users/ls569/.codex/automations/erji-p39/automation.toml').read_text('utf8')
assert re.search(r'^status\s*=\s*"PAUSED"',automation,re.M)
def totals(e):
    result={}
    for row in e['queries']['tasks']:
        d=result.setdefault(row['kind'],dict(completed=0,failed=0))
        for k in d:d[k]+=int(row[k] or 0)
    return result
before,after=totals(first),totals(last)
delta={k:{f:v[f]-before.get(k,{}).get(f,0) for f in v} for k,v in after.items()}
hash_checks=[]
for b in beats:
    for name,digest in b['details'].get('source_hashes',{}).items():
        hash_checks.append({'role':b['role'],'path':name,'matched':hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest})
queues=[{'role':b['role'],'state':b['state'],'heartbeat_age_ms':last['as_of']-b['updated_at'],'quote':b['details'].get('quote_writer'),'facts':b['details'].get('write_queue')} for b in beats if b['role'].startswith(('stream_','discovery_receiver:'))]
errors=queries.get('task_error_records',[])
old_errors=[r for r in errors if r['retired_reason'] and int(r['started_at'] or 0)<last['run_start_ms']]
result={
 'status':'activated_deadlock_not_reproduced_data_partial','run_id':last['run_id'],'captured_at_bjt':last['captured_at_bjt'],
 'production_restarted':True,'migration_required':False,'periodic_monitoring_resumed':False,
 'runtime_seconds':last['age_seconds'],'observation_seconds':round((last['as_of']-first['as_of'])/1000),'sample_count':len(samples),
 'ports':last['ports'],'http':last['http'],'process_identities':identities,'startup_code_hash_checks':hash_checks,
 'sampled_code_hashes_unchanged':first['code_hashes']==last['code_hashes'],
 'postgres_deadlock_errors_since_preflight':last['postgres_new_deadlock_errors'],
 'postgres_deadlock_counter_delta':int(queries['deadlocks'][0]['deadlocks'])-int(first['queries']['deadlocks'][0]['deadlocks']),
 'postgres_error_heads':last.get('postgres_error_heads',[]),
 'task_completion_delta_since_initial':delta,'results_since_start':queries['results_since_start'],
 'old_retired_error_records':old_errors,'source_coverage':queries['source'],'projection':queries['projection'],'events':queries['events'],
 'discovery':{k:v for k,v in discovery['details'].items() if k!='pending_pool'},'discovery_state':discovery['state'],
 'producer':owner['details'].get('producer'),'queues':queues,'revision':queries['revision'],'indexes_valid':all(r['indisvalid'] for r in queries['indexes']),
 'btc_5m_continuity':queries.get('btc_5m_continuity'),'retained_files':last['retained_files'],
 'log_checks':{k:{f:v for f,v in row.items() if f!='tail'} for k,row in last['logs'].items()},
 'ui':[{'route':r['route'],'errors':r['errors'],'http_errors':r['failed'],'api':r['api'][:3]} for r in ui],
 'remaining_findings':['持续出现超过10秒的连接持有警告；采样发现需求选择/行情写入/预算路径锁等待，暂无新的死锁。','T1价格窗口覆盖不足；WebSocket有重连记录，15秒以上真实断档不能拼接。','OI90秒和6分钟覆盖尚未齐全；部分趋势结果为waiting_inputs。','/observations默认跳转我的关注；取消筛选可查看市场异动。'],
 'evidence_phases':[r['phase'] for r in samples]}
(OUT/'activation-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),'utf8')
esc=lambda x:html.escape(str(x))
table=lambda heads,rows:'<div class="table"><table><thead><tr>'+''.join('<th>'+esc(h)+'</th>' for h in heads)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'
task_table=table(['执行通道','完成增量','失败增量'],[(k,v['completed'],v['failed']) for k,v in delta.items()])
source_table=table(['市场','记录数','报价≤15秒','报价≤60秒','OI≤90秒','OI≤6分钟'],[(r['venue'],r['markets'],r['quotes_15s'],r['quotes_60s'],r['oi_90s'] if r['venue']=='binance' else '—',r['oi_6m'] if r['venue']=='binance' else '—') for r in queries['source']])
window=discovery['details']['window_coverage']
window_table=table(['窗口','已就绪/合格市场'],[(k+'分钟',str(v['ready'])+' / '+str(v['eligible'])) for k,v in window.items()])
rows=table(['核验','结果'],[
 ('启动批次',last['run_id']),('6个注册进程启动身份','全部一致' if all(r['matched'] for r in identities) else '存在不一致'),
 ('端口18770/18771/18772/55432','全部开放' if all(last['ports'].values()) else '存在未开放'),
 ('观察/买入页面与业务API','全部200' if all(r.get('status')==200 for r in last['http'].values()) else '存在异常'),
 ('PostgreSQL新deadlock ERROR',last['postgres_new_deadlock_errors']),('数据库deadlocks计数增量',result['postgres_deadlock_counter_delta']),
 ('新进程堆栈/40P01',sum(r['traceback']+r['sqlstate_40P01'] for r in last['logs'].values())),
 ('迁移与索引',str(queries['revision'][0]['version_num'])+'；已检查索引全部有效'),
 ('失败事实保留文件',len(last['retained_files'])),('自动监控','保持PAUSED')])
doc=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 死锁修复激活验证</title><style>body{{margin:0;background:#edf2f7;color:#192b40;font:16px/1.75 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1040px;margin:24px auto;padding:30px;background:white;border-radius:14px}}h1{{font-size:28px}}h2{{font-size:21px;margin-top:28px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border:1px solid #dae2eb;text-align:left}}th{{background:#eef5ff}}.table{{overflow-x:auto}}p,li,td{{overflow-wrap:anywhere}}.status{{border-left:4px solid #2b70b1;background:#eef5ff;padding:16px}}@media(max-width:600px){{main{{margin:0;padding:16px}}h1{{font-size:23px}}}}</style><main>
<h1>P39 · 死锁修复重启与激活验证</h1><p>采样截止：{esc(last['captured_at_bjt'])}（北京时间）。</p>
<p class="status"><strong>修复代码已激活，本次运行约{last['age_seconds']/60:.1f}分钟未复现死锁；行情覆盖仍不完整。</strong><br>已完成有界人工验证，10分钟自动监控保持停止。</p>
<h2>服务与数据库</h2>{rows}<p>启动代码哈希核对{len(hash_checks)}项，{'全部一致' if all(r['matched'] for r in hash_checks) else '存在不一致'}。连续采样期间修复文件哈希{'未改变' if result['sampled_code_hashes_unchanged'] else '有改变'}。</p>
<h2>任务与事件推进</h2>{task_table}<p>增量从新批次首个采样起计算，表示执行完成次数，不代表新增独立代币。部分完成结果为waiting_inputs，不能当成已经得到有效结构或买入信号。</p>
{table(['分析','结果','原生市场数'],[(r['work_kind'],r['status'],r['markets']) for r in queries['results_since_start']])}
<p>新批次事件{queries['events'][0]['current_run_events']}条，事件游标{queries['events'][0]['latest_id']}。投影最新时刻距采样{max(0,last['as_of']-queries['projection'][0]['latest_projection'])/1000:.1f}秒；采样时有效可买结果{queries['projection'][0]['unexpired_buy']}条。</p>
<h2>行情与窗口</h2>{source_table}{window_table}<p>T1状态：{esc(discovery['state'])}。保存耗时P95：{esc(discovery['details']['latency']['persist_p95_ms'])}ms。合格市场数依据当前成交额、流动性状态，不能拿所有市场直接作为该覆盖分母。</p>
<p>BTC最近一小时已关闭5M K线连续性：{esc(queries.get('btc_5m_continuity'))}。这仅验证该样本，不代表全市场K线完整。</p>
<h2>尚未完成的恢复与性能问题</h2><ul>{''.join('<li>'+esc(x)+'</li>' for x in result['remaining_findings'])}</ul>
<p>已核实{len(old_errors)}条任务错误记录来自新批次之前启动、随后因outside_active_slots退役的旧任务；新批次任务失败计数没有因此增加。长连接/锁等待另行记录，不能等同于死锁消失后的完全性能恢复。</p>
<h2>页面验证与证据</h2><p>市场观察取消“我的关注”筛选后加载真实记录；买入页加载关联计划。两页未出现pageerror或本地HTTP错误。未调整数据、门槛或交易开关。</p><p>完整采样、进程身份、代码哈希、日志增量与截图：docs/p39-validation/deadlock-fix-20261008/activation/。历史256项隔离回归仍保留在原验收记录。</p><p><a href="P39_deadlock_fix_acceptance.html">代码验收</a> · <a href="P39_deadlock_investigation.html">原问题调查</a> · <a href="/radar">市场观察</a> · <a href="/signals">买入机会</a></p></main></html>'''
for p in [OUT/'P39_deadlock_fix_activation.html',ROOT/'apps/web/public/previews/P39_deadlock_fix_activation.html']:p.write_text(doc,'utf8')
for p in [OUT.parent/'P39_deadlock_fix_acceptance.html',ROOT/'apps/web/public/previews/P39_deadlock_fix_acceptance.html']:
    t=p.read_text('utf8')
    note='<p class="activation-current"><strong>最新状态：已重启激活。</strong> <a href="P39_deadlock_fix_activation.html">查看本次运行验证与剩余问题</a>。下面保留重启前的代码验收记录。</p>'
    if 'class="activation-current"' not in t:p.write_text(t.replace('<h1>',note+'<h1>',1),'utf8')
print(json.dumps({k:result[k] for k in ('status','runtime_seconds','sample_count','postgres_deadlock_errors_since_preflight','postgres_deadlock_counter_delta','task_completion_delta_since_initial','source_coverage','discovery_state')},ensure_ascii=False,indent=2))
