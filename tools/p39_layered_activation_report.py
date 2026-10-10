"""Publish saved local activation evidence without touching production data."""
import html
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/p39-validation/layered-analysis-implementation-20261007/activation'
a=json.loads((OUT/'corrected-first-sample.json').read_text('utf8'))
b=json.loads((OUT/'stable-sample.json').read_text('utf8'))
process=json.loads((OUT/'process-check.json').read_text('utf-8-sig'))
run=process['run_id']
assert all(p['alive'] and p['identity_match'] for p in process['processes'])
assert {p['LocalPort'] for p in process['ports']}=={18770,18771,18772,55432}
assert b['revision']==['p39_layered_analysis'] and not b['code_hash_mismatches']
assert len(b['indexes'])==8 and all(i['valid'] for i in b['indexes'])
assert all(r.get('status')==200 for r in b['http'].values())
def owner(sample):
    return next(r for r in sample['heartbeats'] if r['role']=='analysis_owner' and r['details'].get('run_id')==run)
x,y=owner(a),owner(b)
changes={k:{metric:y['details']['by_work_kind'][k][metric]-x['details']['by_work_kind'][k][metric] for metric in ('completed','failed')}
    for k in y['details']['by_work_kind']}
logs=Path(b['processes'][0]['log_dir'])
errors=[];long_holds=[];manifests=[]
for file in logs.glob('*.log'):
    content=file.read_text('utf8',errors='replace')
    for line in content.splitlines():
        if 'Traceback' in line or 'loop failed' in line:errors.append({'file':file.name,'line':line})
        if 'database connection held' in line:long_holds.append({'file':file.name,'line':line})
        if 'startup {' in line:
            entry=json.loads(line.split('startup ',1)[1])
            if entry.get('run_id')==run:
                mismatches=[p for p,h in entry['source_hashes'].items() if __import__('hashlib').sha256((ROOT/p).read_bytes()).hexdigest()!=h]
                assert not mismatches,mismatches
                manifests.append({'role':entry['role'],'generation':entry['code_generation'],'hashes_match':True})
assert not errors,errors
counts=b['http']['/v1/signals?view=opportunities&page_size=5']['body']['opportunity_counts']
local=lambda s:datetime.fromisoformat(s).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
record={'status':'activated_with_data_readiness_limitations','run_id':run,'checked_at':b['checked_at'],
    'revision':b['revision'],'processes':process,'loaded_manifests':manifests,'completion_sample_window':[a['checked_at'],b['checked_at']],
    'task_completion_deltas':changes,'current_failed_tasks':b['task_errors'],'current_run_tracebacks':errors,
    'long_database_holds':long_holds,'states':b['states'],'demands':b['demands'],
    'opportunity_counts':counts,'entry_targets':y['details']['producer']['targets']['entry'],
    'limitations':['T1 memory windows warming; waiting_inputs and historical candle backfill remain',
    'Recovered sources include 1208 native markets; creation count on activation is recovery, not live arrival rate',
    'Two sampled task failure counter increments; latest failed-task list empty; overwritten reason cannot be reconstructed',
    'Protection/projection queue ages and 10-17s database holds observed; full latency target not established',
    'No currently eligible entry target or buy row; new T1-to-buy transition not observed in this short verification'],
    'epoch_fix':{'cause':'PostgreSQL JSON epoch millisecond cast to INTEGER overflow','change':'BIGINT for source, shadow and metric time fields','isolated_tests_passed':81},
    'monitoring_resumed':False,'notify_enabled':False,'trading_enabled':False}
(OUT/'activation-result.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),'utf8')
rows=''.join('<tr><td>'+html.escape(k)+'</td><td>'+str(v['completed'])+'</td><td>'+str(v['failed'])+'</td></tr>' for k,v in changes.items())
page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 · T1 / T2 / T3 生产激活</title>
<style>body{{font:15px/1.8 "Microsoft YaHei",sans-serif;color:#18304d;background:#f3f6fb;margin:0}}main{{max-width:1000px;margin:24px auto;background:white;padding:30px;border-radius:12px}}h1{{font-size:27px}}h2{{font-size:20px;margin-top:30px}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;border:1px solid #dfe7f1;padding:10px}}code{{word-break:break-all}}.status{{background:#e8f6ef;padding:14px;border-radius:7px}}a{{color:#2563eb}}@media(max-width:700px){{main{{margin:0;padding:18px}}}}</style><main>
<h1>P39 · T1 / T2 / T3 生产迁移与重启激活</h1>
<p class="status">已激活，有限运行验收通过；市场数据补齐和窗口暖机仍在继续。</p>
<p>北京时间 {local(b['checked_at'])} · 批次 <code>{run}</code></p>
<h2>迁移与进程</h2><p>生产库版本：<code>p39_layered_analysis</code>。新增三表、七个索引及原快照最新索引均有效。六个登记进程存活且启动身份匹配；18770 / 18771 / 18772 / 55432 均监听。启动日志确认四个 Worker 加载 P39-layered-analysis，源码哈希一致。</p>
<h2>首次激活阻塞已修复</h2><p>第一次启动遇到毫秒时间戳转32位整数溢出，分析循环报错。已将来源、影子和容量统计的JSON时间字段转为BIGINT，新增真实时间戳回归；81项隔离测试通过。重新启动后当前批次没有分析循环异常堆栈。保留第一次失败批次及迁移前后证据。</p>
<h2>实际完成增量</h2><p>采样：{local(a['checked_at'])} → {local(b['checked_at'])}。以下为任务完成次数，不是新增独立代币，也不是全部输入齐全的市场数。</p>
<table><tr><th>通道</th><th>完成增量</th><th>失败计数增量</th></tr>{rows}</table>
<p>最新 analysis_owner 为 <code>{y['state']}</code>；当前没有failed状态的任务。保护与结构各有一次失败计数增加，最新任务已经退出failed；错误字段会被后续完成覆盖，未留存原失败原因，不能断言全程零失败。</p>
<h2>来源、分析结果和页面</h2><p>恢复的有效需求覆盖1208个原生市场，包含候选与观察来源；这属于激活时恢复存量，不能当作每分钟新异动到达率。真实T2结果已持久化，并通过只读追踪API返回来源、输入版本与结果引用。结构、趋势都已有completed结果，其他结果明确标记waiting_inputs。</p>
<p>市场观察、买入列表、分级追踪API、前端页面和预算服务均HTTP200。当前可买 {counts.get('l1',0)} 条、等待 {counts.get('l2',0)} 条；有效entry目标为0，本次短时验收未观察到一条新信号贯穿至可买，不用影子结果或模拟信号替代。</p>
<h2>仍需如实保留的运行状态</h2><p>T1的5/15分钟窗口处于重启暖机；部分市场缺少新鲜或连续K线，历史补数运行中。保护/投影队列仍有数分钟等待，日志出现10–17秒数据库连接占用，尚未证明全市场数据齐全或延迟达标。本次完成迁移和新代码加载，不将这些状态标为已全部解决。</p>
<p>本次未恢复长期监控，未启用交易或外部通知。</p>
<p><a href="/previews/P39_T1_T2_T3_execution_plan.html">分级执行方案</a> · <a href="/previews/P39_T1_T2_T3_acceptance.html">代码验收</a> · <a href="/signals">打开工具</a></p></main></html>'''
for target in (ROOT/'apps/web/public/previews/P39_T1_T2_T3_activation.html',OUT/'P39_T1_T2_T3_activation.html'):
    target.write_text(page,'utf8')
banner='<p style="padding:14px;background:#e8f6ef">生产迁移与重启已完成。<a href="/previews/P39_T1_T2_T3_activation.html">查看当前激活状态及运行限制</a>；下文保留代码验收当时的历史结论。</p>'
for target in (ROOT/'apps/web/public/previews/P39_T1_T2_T3_acceptance.html',):
    content=target.read_text('utf8')
    if banner not in content:target.write_text(content.replace('<body>','<body>'+banner,1),'utf8')
source=ROOT/'docs/p39-validation/layer-flow-audit-20261007/P39_T1_T2_T3分级与数据传递执行方案.md'
addition=f'''\n\n## 15. 生产迁移与重启激活（2026-10-08）\n\n已执行生产迁移至p39_layered_analysis；当前批次{run}，六进程和四端口通过身份及运行核验。首次激活发现JSON毫秒时间戳32位整数溢出，已修正为BIGINT并通过81项隔离回归后重新启动。分级来源、结果持久化及追踪接口已上线，趋势、结构、保护和投影持续完成。\n\n本次属于有限运行验收：T1窗口暖机、部分K线补齐和保护/投影队列等待仍存在；未观察到新信号贯穿至可买，不声称市场数据全部齐全。详细采样、失败计数及限制见 /previews/P39_T1_T2_T3_activation.html。长期监控保持暂停，交易及通知未启用。\n'''
if '## 15. 生产迁移与重启激活' not in source.read_text('utf8'):source.write_text(source.read_text('utf8')+addition,'utf8')
print(json.dumps({'run_id':run,'completion_deltas':changes,'activation_record':str(OUT/'activation-result.json'),'page':'http://127.0.0.1:18770/previews/P39_T1_T2_T3_activation.html'},ensure_ascii=False))
