"""Publish the finite activation outcome without changing runtime settings."""
from pathlib import Path
import json,re
from html import escape

ROOT=Path(__file__).resolve().parents[1];workspace=ROOT.parents[2]
folder=ROOT/'docs/p39-validation/priority-capacity-implementation-20261009'
out=folder/'activation';public=ROOT/'apps/web/public/previews'
data=json.loads((out/'final.json').read_text('utf8'))
summary=json.loads((out/'final-summary.json').read_text('utf8'))
early=json.loads((out/'early.json').read_text('utf8'))
identities=json.loads((out/'process-identities.json').read_text('utf-8-sig'))
code=json.loads((folder/'implementation-result.json').read_text('utf8'))
run=data['run_id'];stamp=data['captured_at_beijing'];q=data['queries']
assert all(data['ports'].values()) and all(v.get('status')==200 for v in data['http'].values())
assert len(identities)==6 and all(r['identity_matches'] and r['run_id']==run for r in identities)
jobs_start=data['logs']['worker-jobs']['startup']['source_hashes']
assert all(jobs_start[p]==code['sha256'][p] for p in ('engine/p38/jobs.py','engine/p37/worker.py','engine/p39/pipeline.py','services/worker/main.py'))
beats=q['worker_heartbeats']
analysis=next(b for b in beats if b['role']=='analysis_owner')
slot_samples=[]
for name in ('early','first-cycle','progressing','final'):
    f=out/(name+'.json')
    sample=json.loads(f.read_text('utf8'))
    if sample.get('run_id')!=run or not isinstance(sample.get('queries'),dict):continue
    for beat in sample['queries'].get('worker_heartbeats',[]):
        if beat['role']=='analysis_owner' and beat['details'].get('lane_slots'):
            slot_samples.append((beat['updated_at'],f.name,beat['details']['lane_slots']))
assert slot_samples, 'No current-run dispatcher slot evidence'
slot_at,slot_file,slots=max(slot_samples,key=lambda s:s[0])
assert slots['structure']['limit']==slots['trend']['limit']==2
assert not summary['oi_schedule']['mismatched_native_intervals']
observed={k:0 for k in ('structure','trend')}
for f in out.glob('*-summary.json'):
    s=json.loads(f.read_text('utf8'))
    if s.get('run_id')!=run:continue
    for b in s.get('important_beats',[]):
        for k in observed:observed[k]=max(observed[k],b['details'].get('lane_slots',{}).get(k,{}).get('running',0))
assert observed=={'structure':2,'trend':2}
oi=q['oi_committed_this_run'][0]
coverage=next(r for r in q['oi_and_volume_coverage'] if r['venue']=='binance')
buy_api=json.loads((out/'final-buy-api.json').read_text('utf8'))
opportunities=buy_api.get('opportunity_counts',{})
discovery=next((b for b in beats if b['role']=='anomaly_discovery'),None)
windows=discovery['details'].get('window_coverage',{}) if discovery else {}
warnings=sum(v['long_connections'] for v in data['logs'].values())
errors={name:sum(v[name] for v in data['logs'].values()) for name in ('tracebacks','deadlocks','duplicate_key','rate_429','ban_418','timeouts')}
failure_count=len(q['failed_this_run'])
deadlocks=data['postgres_new_deadlocks']
state='activated_finite_validation_with_remaining_issues'
record={'status':state,'activated_at_utc':run.split('-')[0],'verified_at_beijing':stamp,'run_id':run,
    'ports':data['ports'],'http':data['http'],'registered_process_identity_matches':True,
    'acceptance_hashes_loaded':True,'migration_revision':q['revision'],
    'timing_version':'p39-priority-capacity-v1','configured_slots':{k:v['limit'] for k,v in slots.items()},
    'observed_analysis_slots':observed,'ordinary_oi_seconds':300,'hot_oi_seconds':60,
    'slot_evidence':{'sample':slot_file,'heartbeat_at':slot_at},
    'latest_analysis_owner':{'state':analysis['state'],'updated_at':analysis['updated_at'],'details':analysis['details']},
    'oi_schedule':summary['oi_schedule'],'oi_committed':oi,'coverage':coverage,
    'window_coverage':windows,'new_deadlocks':deadlocks,'new_failed_tasks':failure_count,
    'log_error_counts':errors,'long_connection_warnings':warnings,
    'validation_seconds':data['age_seconds'],'automation_resumed':False,'notify_started':False,
    'opportunity_counts':opportunities,
    'remaining':'Incomplete 5m/15m window coverage and structure history, long-connection warnings; no full-data-health claim'}
(out/'activation-result.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),'utf8')

rows=[('新批次',run),('四端口 / 接口','18770 / 18771 / 18772 / 55432 均监听；预算、健康、观察、买入和页面均HTTP 200'),
    ('进程与代码','6个注册进程PID及启动ticks匹配；关键源文件SHA256与210项验收版本一致'),
    ('结构 / 趋势','配置各2槽；生产心跳已实际观测到各2个同时执行；保护/入场/投影各1槽'),
    ('普通 / 重点 OI',f"普通{summary['oi_schedule']['ordinary']}个、重点{summary['oi_schedule']['hot']}个；五分钟/一分钟原生间隔与当前T2/T3一致；fair_turn={summary['oi_schedule']['fair_turn']}，无错配"),
    ('本批 OI 实际落库',f"{oi['points']}个点、{oi['native_markets']}个独立原生市场；点数与市场数分别统计"),
    ('当前合约字段',f"已存{coverage['markets']}个市场：90秒行情{coverage['quote_90s']}，10分钟OI{coverage['oi_10m']}，10分钟成交额{coverage['volume_10m']}"),
    ('新错误',f"死锁{deadlocks}、失败任务{failure_count}、Traceback {errors['tracebacks']}、429 {errors['rate_429']}、418 {errors['ban_418']}"),
    ('仍需关注',f"长连接警告{warnings}条；最新采样阻塞等待{len(q['blocking'])}条、超过8秒事务{len(q['long_transactions'])}条。连续窗口与结构历史仍在恢复。"),
    ('5 / 15 分钟价格窗口',json.dumps(windows,ensure_ascii=False)),
    ('买入 API',f"当前可买{opportunities.get('l1',0)}条、等待条件{opportunities.get('l2',0)}条；当前合格买入数不作为启动故障判断")]
table='<div class="table"><table><thead><tr><th>项目</th><th>真实结果</th></tr></thead><tbody>'+''.join('<tr><td>'+escape(a)+'</td><td>'+escape(str(b))+'</td></tr>' for a,b in rows)+'</tbody></table></div>'
notice=f'''<div id="p39-priority-capacity-activation" class="activation" style="padding:18px;margin:18px 0;background:#e8f3fa;border-left:4px solid #247091"><strong>已重启激活 · {escape(stamp)}</strong><p>批次 {escape(run)}；四端口及接口可用，结构/趋势2+2已加载并实际执行。当前批次OI已落库 {oi['native_markets']} 个独立市场；新死锁{deadlocks}、失败任务{failure_count}。长连接警告{warnings}条，连续窗口和历史仍在恢复。</p><p><a href="P39_priority_capacity_activation.html">查看有限激活验证与剩余状态</a></p></div>'''
body=f'''<h1>P39 · 重点分级与分析容量激活验证</h1>{notice}<p>验证约{round(data['age_seconds']/60,1)}分钟；先核对启动身份及接口，再记录首轮REST恢复、OI提交和实际执行槽。本次只做有限验证，没有恢复十分钟持续监控。</p>{table}<h2>重启恢复过程</h2><p>首次预算检查点已过时，沿用既有五分钟恢复等待，未清空账本或改变预算。等待结束后REST成交额、OI和K线开始落库；持续WS行情在等待期间已接收。OI从首个采样0个市场推进到{oi['native_markets']}个，来自当前批次真实收到并提交的数据。</p><p>15分钟连续价格窗口必须由重启后的真实流重新建立。分析结果中的waiting_inputs与补数状态分别保留，旧观察/任务累计次数不算本轮新信号。缺少合格买入信号本身不判启动失败；仍有长连接警告和数据恢复项，因此不判定全部数据正常。</p><h2>代码与生产证据</h2><p>迁移头为p39_layered_analysis，无本轮新增schema迁移。210项隔离回归和12币/24任务回放证据保留于代码验收页；本页只使用新批次PID、心跳、注册日志和有界只读SQL/API。</p><p>证据目录：docs/p39-validation/priority-capacity-implementation-20261009/activation/；final.json、process-identities.json、port-owners.json、activation-result.json及各阶段增量日志。</p><p><a href="P39_priority_capacity_acceptance.html">代码验收</a> · <a href="P39_timing_consistency_plan.html#p39-priority-capacity-current">P39最新方案</a> · <a href="/observations">打开市场观察</a> · <a href="/signals">打开买入机会</a></p>'''
css='''*{box-sizing:border-box}body{margin:0;background:#eef3f7;color:#203247;font:16px/1.8 "Microsoft YaHei",system-ui,sans-serif}main{max-width:1140px;margin:30px auto;padding:32px 38px;background:white;border-radius:14px}h1{font-size:29px}h2{font-size:22px;margin-top:28px}a{color:#176b99}p,td{overflow-wrap:anywhere}.table{overflow:auto;border:1px solid #dce5ed;border-radius:8px}table{width:100%;min-width:600px;border-collapse:collapse;font-size:14px}th,td{text-align:left;vertical-align:top;padding:13px;border-bottom:1px solid #dce5ed}th{background:#eef5fa}td:first-child{min-width:140px}@media(max-width:680px){main{margin:0;padding:22px 16px}h1{font-size:25px}}'''
page='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 · 重点分级与分析容量激活验证</title><style>'+css+'</style></head><body><main>'+body+'</main></body></html>'
(out/'P39_priority_capacity_activation.html').write_text(page,'utf8')
(public/'P39_priority_capacity_activation.html').write_text(page,'utf8')

paths=[public/'P39_priority_capacity_acceptance.html',folder/'P39_priority_capacity_acceptance.html',
    public/'P39_timing_consistency_plan.html',ROOT/'docs/p39-validation/timing-audit-20261008/P39_timing_consistency_plan.html',
    public/'P39_plan_latest.html',workspace/'docs/P39_plan_latest.html',workspace/'docs/P39_ERJI_主链路收敛与数据可靠性整改方案.html']
for p in paths:
    if not p.exists():continue
    content=p.read_text('utf8')
    content=re.sub(r'<div id="p39-priority-capacity-activation".*?</div>','',content,flags=re.S)
    content=content.replace('<main>','<main>'+notice,1)
    content=content.replace('代码及隔离验证完成，生产尚未重启激活。','代码及隔离验证完成，本轮已重启激活；最新运行结果见上方激活记录。')
    content=content.replace('代码更新完成 · 210项隔离回归通过 · 真实2+2执行器回放通过 · 待生产重启激活','代码更新及重启激活完成 · 210项隔离回归通过 · 2+2生产执行已确认 · 数据恢复中')
    content=re.sub(r'<p>本机端口检查.*?本轮没有启动生产服务。HTML已通过本地文件的桌面/手机验证。</p>',
        '<p>激活前四端口未监听；本轮启动后四端口与页面均正常响应，详见激活记录。</p>',content,flags=re.S)
    p.write_text(content,'utf8')

note=f'''\n\n### 16.1 生产重启激活（{stamp}）\n\n批次 `{run}`，四端口与接口200，6个注册进程身份匹配。结构/趋势各2槽已实际执行，OI本批提交{oi['native_markets']}个独立原生市场。新增死锁{deadlocks}、失败任务{failure_count}；仍有{warnings}条长连接警告，连续窗口与历史数据恢复中。首轮REST在既有五分钟预算恢复等待结束后启动，未修改预算或清空账本。未恢复持续监控或启动通知。\n\n[激活验证](http://127.0.0.1:18770/previews/P39_priority_capacity_activation.html)；证据：priority-capacity-implementation-20261009/activation/activation-result.json。\n'''
for p in (ROOT/'docs/p39-validation/timing-audit-20261008/P39_全链路时间规则冲突审计与执行方案.md',workspace/'docs/P39_ERJI_主链路收敛与数据可靠性整改方案.md'):
    if p.exists():p.write_text(re.sub(r'\n\n### 16\.1 生产重启激活.*','',p.read_text('utf8'),flags=re.S)+note,'utf8')
design=ROOT/'docs/p39-validation/timing-audit-20261008/five-minute-schedule-design.json'
d=json.loads(design.read_text('utf8'));d.update(status=state,activation_run_id=run,activation_verified_at_beijing=stamp,
    activation_evidence='priority-capacity-implementation-20261009/activation/activation-result.json')
design.write_text(json.dumps(d,ensure_ascii=False,indent=2),'utf8')
code['activation']={'status':state,'run_id':run,'verified_at_beijing':stamp,'evidence':'activation/activation-result.json'}
code['production_restart']=True
(folder/'implementation-result.json').write_text(json.dumps(code,ensure_ascii=False,indent=2),'utf8')
print(json.dumps(record,ensure_ascii=False))
