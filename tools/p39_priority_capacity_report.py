"""Publish implemented P39 rules and isolated acceptance evidence."""
from pathlib import Path
import json,hashlib
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter
from xml.etree import ElementTree as ET

root=Path(__file__).resolve().parents[1]
workspace=root.parents[2]
out=root/'docs/p39-validation/priority-capacity-implementation-20261009'
public=root/'apps/web/public/previews'
stamp=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
test=ET.parse(root/'docs/p39-validation/p39-priority-capacity-tests.xml').getroot()
suites=list(test.iter('testsuite'))
counts={key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}
assert counts['tests']==210 and counts['failures']==counts['errors']==0
replay=json.loads((out/'real-executor-replay.json').read_text('utf8'))
statuses=Counter(r['status'] for r in replay['outcomes'])
files=['engine/p39/priority.py','engine/p39/timing.py','engine/p39/pipeline.py','engine/p37/worker.py',
    'engine/p38/jobs.py','engine/pd2/collection_schedule.py','engine/pd2/write_queue.py',
    'engine/adapters/binance_universe.py','engine/repository.py','engine/signal_service.py',
    'engine/pd2/service.py','engine/pd2/signal_projection.py','services/worker/main.py',
    'tests/test_p39_priority_capacity.py','tests/test_p39_layered_analysis.py',
    'tests/test_p34_logic_repair.py','tests/test_p39_remaining.py']
record={'updated_at_beijing':stamp,'status':'code_verified_pending_production_activation',
    'timing_version':'p39-priority-capacity-v1','tests':counts,
    'replay':{k:replay[k] for k in ('completed_tasks','elapsed_seconds','deadlocks','observed_peak_inflight')},
    'analysis_outcomes':dict(statuses),'production_restart':False,'production_migration':False,
    'automation_resumed':False,'business_files':files,
    'sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}}
port_evidence=out/'preview-port-check.json'
runtime=json.loads(port_evidence.read_text('utf8')) if port_evidence.exists() else None
if runtime:record['runtime_port_check']=runtime
(out/'implementation-result.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),'utf8')

rules='''
<div class="table"><table><thead><tr><th>层级 / 环节</th><th>本次代码规则</th><th>交接与执行</th></tr></thead><tbody>
<tr><td>T1 普通币</td><td>价格/成交额/OI 异动检测每五分钟；成交额批量五分钟，OI逐币轮转五分钟</td><td>连续 WS 价格/5m K线继续接收；按原生市场稳定相位到期，保留五分钟期间真实历史回查。只做异动数据，自动后台结构影子轮转停止。</td></tr>
<tr><td>T2 真实观察</td><td>已触发且仍有效的观察需求；包括排队、等待输入，不以已选中50个市场代替全部T2</td><td>重点 OI 和观察检查一分钟；历史缺口按需补齐。结构2槽、趋势2槽分别执行；缺输入释放准入，输入变化通知重新申请。</td></tr>
<tr><td>T3 有效计划 / 持仓</td><td>未到期且未结束计划、登记中的真实持仓</td><td>OI一分钟；保护/入场/投影30秒生产者检查，三个通道各1槽，完成直接发布。同币快路互斥、租约和输入版本核对继续有效。</td></tr>
<tr><td>普通名单 / 旧候选</td><td>Watchlist、配置symbol、旧plan、P35提示不再自动升频；同资产其他现货/合约不连带升级</td><td>未越线候选保持轻量监测，五分钟检查价格越线才申请既有确认。用户明确点分析/置顶保留手动分析需求，不把整个关注名单当重点。</td></tr>
<tr><td>OI 公平补位</td><td>跨调用持久化3重点:1普通的服务顺序；任一队列无到期任务时借用空闲容量</td><td>即使每次只释放1个采集槽，普通到期币仍获得服务；累计轮次不因新调用重置。不将网络接受数当成功落库数。</td></tr>
<tr><td>OI 落库</td><td>目录先入同一FIFO队列；逐点OI不重复写资产/目录/身份。原生映射独立保存并合并</td><td>请求进行中与待提交写队列分别续租；只有落库和ACK结束才释放所有权。ACK核对owner；续租失败传播并保留可恢复事实。</td></tr>
<tr><td>数据库与缓存</td><td>完整需求恢复由15秒改为启动及五分钟，并移出selector事务；按T2/T3集合变化刷新优先级缓存</td><td>实时异动仍在原事务直接登记需求，无需等待五分钟恢复。相同成员的心跳/轮询不触发重建；共享心跳改成原子UPSERT。</td></tr>
<tr><td>健康与容量统计</td><td>同一内存快照取当前时钟；复制后在锁外计算5/15m窗口，每窗一次</td><td>缺口仍判缺口。写队列显示未提交数量及最老年龄；T2/T3独立计数，休眠候选不算计算需求，恢复旧观察不冒充实时到达率。</td></tr>
</tbody></table></div>'''
proof=f'''<p>隔离回归 <strong>{counts['tests']} 项通过</strong>；使用独立临时 PostgreSQL，未访问生产数据库。覆盖原生分级、等待输入保留T2、公平补位、真实双槽dispatcher、首次并发心跳、待提交OI续租、死锁回归、信号准入、计划生命周期和写入恢复。</p>
<p>新版真实执行器：复用先前保存的12个币、70003根K线，24个结构/趋势任务完成，耗时 <strong>{replay['elapsed_seconds']} 秒</strong>，观测到结构2、趋势2、合计4个任务同时进行，死锁0。分析结果：{statuses.get('completed',0)}个完成，{statuses.get('waiting_inputs',0)}个等待输入；任务结束并不等于策略一定匹配或数据全部具备。</p>
<p>此前低负载对比每类1/2/4/8槽分别8.362/7.420/8.546/8.746秒，支持先用2+2。本次11.554秒属于另一轮系统负载，不能据此宣称吞吐翻倍，也不能据此给全市场固定完成时限。<a href="P39_analysis_capacity_replay.html">容量回放对比</a></p>'''
runtime_note=(f'<p>本机端口检查 {runtime["checked_at"]}：18770、18771、18772、55432 均未监听。本轮没有启动生产服务。HTML已通过本地文件的桌面/手机验证。</p>'
    if runtime and all(v=='not_listening' for v in runtime['ports'].values()) else '')
status=f'''<section id="p39-priority-capacity-current"><h2>16. 最新实施：分级重点、公平补位与2+2执行槽</h2><p>更新于 {stamp}。<strong>代码及隔离验证完成，生产尚未重启激活。</strong>本节覆盖旧稿中“待实施”和候选参数的状态，其他带日期的记录保留为历史证据。</p>{runtime_note}{rules}{proof}<p>生产激活后仍需核验实际OI超期、队列最老年龄、持续结构/趋势耗时和数据库长连接。本轮结果不能当作生产积压已经消失。持续监控保持停止。</p><p><a href="P39_priority_capacity_acceptance.html">本轮代码更新与验收记录</a> · <a href="P39_timing_consistency_plan.html#p39-priority-capacity-current">P39时间主方案最新规则</a></p></section>'''
css='''*{box-sizing:border-box}body{margin:0;background:#eef3f7;color:#203247;font:16px/1.8 "Microsoft YaHei",system-ui,sans-serif}main{max-width:1160px;margin:30px auto;padding:32px 38px;background:white;border-radius:14px}h1{font-size:30px}h2{margin-top:30px;font-size:23px}a{color:#176b99}.table{overflow:auto;border:1px solid #dce5ed;border-radius:8px;margin:20px 0}table{width:100%;min-width:700px;border-collapse:collapse;font-size:14px}th,td{text-align:left;vertical-align:top;padding:13px;border-bottom:1px solid #dce5ed}th{background:#eef5fa}p,td{overflow-wrap:anywhere}.status{background:#e8f3fa;border-left:4px solid #247091;padding:16px}code{font-size:13px}@media(max-width:680px){main{margin:0;padding:22px 16px}h1{font-size:25px}}'''
page='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 · 重点分级与分析容量更新验收</title><style>'+css+'</style></head><body><main><h1>P39 · 重点分级与分析容量更新验收</h1><p class="status">代码更新完成 · 210项隔离回归通过 · 真实2+2执行器回放通过 · 待生产重启激活</p>'+status+'<h2>代码与证据</h2><p>统一重点：engine/p39/priority.py；采集：collection_schedule.py、binance_universe.py、write_queue.py；分析：pipeline.py、p37/worker.py、p38/jobs.py；共享心跳：repository.py及结构/计划/投影调用。</p><p>证据目录：docs/p39-validation/priority-capacity-implementation-20261009/；implementation-result.json保存源码SHA256、回归结果和回放结果。测试XML及test-isolation.json位于docs/p39-validation/。不需要新的数据库schema迁移。</p></main></body></html>'
(out/'P39_priority_capacity_acceptance.html').write_text(page,'utf8')
(public/'P39_priority_capacity_acceptance.html').write_text(page,'utf8')

# Keep the main P39 plan and the chronological time-rule plan in sync.
paths=[public/'P39_timing_consistency_plan.html',root/'docs/p39-validation/timing-audit-20261008/P39_timing_consistency_plan.html',
    public/'P39_plan_latest.html',workspace/'docs/P39_plan_latest.html',workspace/'docs/P39_ERJI_主链路收敛与数据可靠性整改方案.html']
import re
for p in paths:
    if not p.exists():continue
    before=p.read_text('utf8')
    (out/('before-'+p.parent.name+'-'+p.name)).write_text(before,'utf8')
    before=re.sub(r'<section id="p39-priority-capacity-current">.*?</section>','',before,flags=re.S)
    before=before.replace('<main>','<main>'+status,1)
    before=before.replace('后续优化依据此修订方案，尚未实施。','修订代码已完成并通过隔离验证，生产待激活；最新规则见第16节。')
    p.write_text(before,'utf8')

markdown=f'''\n\n## 16. 分级重点、公平补位与分析容量实施（{stamp}）\n\n代码更新和210项隔离回归完成，生产待重启激活。T1普通币五分钟，只做异动；真实T2观察（包括等待输入/排队）及有效T3计划/登记持仓独立优先。旧候选、关注名单、配置symbol、P35提示和同资产其他native市场不自动升频。用户明确手动分析/置顶仍可请求分析。未越线候选只做低频越线检查，后台普通结构影子轮转停止。\n\n结构2槽、趋势2槽；保护/入场/投影各1槽。同币快路互斥、原生任务去重、租约和输入版本仍有效。OI跨调用持久化3重点:1普通的公平顺序，单槽补位同样有效；队列空时借用。目录先提交，逐点OI不重复目录/身份写入；待提交事实持续续租，提交后按owner确认ACK。\n\n完整需求恢复启动及每五分钟，放到独立事务；实时观察直接登记，不等恢复轮。优先级缓存按成员语义变化；健康快照使用复制时的当前时钟，在锁外每窗计算一次。共享心跳原子UPSERT；写队列记录最老待提交年龄；恢复存量观察不计实时到达率。\n\n真实执行器回放12币/24任务，耗时11.554秒，结构2+趋势2实际并行，死锁0；16结果完成、8等待输入。该轮不能作为全市场固定耗时或生产提升比例。激活后再核验OI超期、队列年龄、持续耗时及长连接。无需schema迁移，自动监控保持停止。\n\n[代码验收](http://127.0.0.1:18770/previews/P39_priority_capacity_acceptance.html)；证据目录：docs/p39-validation/priority-capacity-implementation-20261009/。\n'''
for p in (root/'docs/p39-validation/timing-audit-20261008/P39_全链路时间规则冲突审计与执行方案.md',workspace/'docs/P39_ERJI_主链路收敛与数据可靠性整改方案.md'):
    if p.exists():
        text=p.read_text('utf8');text=re.sub(r'\n\n## 16\. 分级重点、公平补位与分析容量实施.*','',text,flags=re.S)
        p.write_text(text+markdown,'utf8')
design=root/'docs/p39-validation/timing-audit-20261008/five-minute-schedule-design.json'
data=json.loads(design.read_text('utf8'));data.update(design_revision=4,status='priority_capacity_code_verified_pending_activation',
    business_changes=True,updated_at_beijing=stamp,timing_version=record['timing_version'],
    analysis_slots={'structure':2,'trend':2,'protect':1,'entry':1,'projection':1},
    priority_scope='native valid observation demands; valid plans and open positions; no inherited watchlist/candidate/config/P35 priority',
    recovery_interval_seconds=300,ordinary_fair_share='one in every four admissions when both tiers due; persisted across calls',
    evidence='priority-capacity-implementation-20261009/implementation-result.json')
design.write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf8')
print(json.dumps(record|{'preview':str(public/'P39_priority_capacity_acceptance.html')},ensure_ascii=False,indent=2))
