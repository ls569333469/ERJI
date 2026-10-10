"""Render saved finite activation evidence; does not access production DB."""
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT.parents[2] / 'docs'
OUT = ROOT / 'docs/p40-validation/r4-activation-20261010'
def read(name):
    return json.loads((OUT / name).read_text('utf-8-sig'))
e = read('final.json')
d = read('final-detail.json')
ops = read('final-operations-api.json')
identities = read('final-process-identities.json')
workers = {w['role']: w['details'] for w in ops['workers']}
discovery = workers['anomaly_discovery']
assert discovery['snapshot_at'] >= e['run_start_ms'] + 900000, 'Wait for mature 15m snapshot'
now = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
coverage = discovery['window_coverage']
identity = e['queries']['demand_identity'][0]
result = {
    'status': 'activated_with_followup', 'run_id': e['run_id'],
    'code_generation': 'P40-reliability-r4', 'verified_at_bjt': now,
    'verification_scope': 'finite restart activation, not recurring monitoring or full production capacity test',
    'ports': e['ports'], 'http': e['http'], 'process_identities': identities,
    'source_hash_mismatches': e['source_hash_mismatches'],
    'blind_spots': e['blind_spots'] + d['blind_spots'],
    'postgres_new_deadlocks': e['postgres_new_deadlocks'],
    'price_snapshot_at': discovery['snapshot_at'], 'price_windows': coverage,
    'independent_oi_15m': discovery['oi_window_coverage'],
    'coverage': e['queries']['coverage'], 'active_demand_identity': identity,
    'results_this_run': e['queries']['results_this_run'],
    'failed_tasks_this_run': e['queries']['failed_this_run'],
    'blocking': e['queries']['blocking'],
    'app_long_transactions_detail_asof': d['queries']['app_long_transactions'],
    'closure_counts': d['closure_by_venue_channel_reason'],
    'long_holds_detail_asof': d['long_hold_summary'],
    'oi_probe': d['oi_probe_summary'],
    'genuine_event_result_tracking': d['genuine_event_result_tracking'],
    'monitoring_status': 'paused', 'evidence': str(OUT),
}
(OUT / 'activation-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf8')

lines = [
    '# P40 R4 · 重启激活与有限验证', '',
    f'验证时间：{now}（北京时间）。当前运行批次：`{e["run_id"]}`。', '',
    '**R4已重启加载，服务与身份修复通过；仍有行情背压、长连接持有及窗口缺失，不能判定全部数据正常。**', '',
    '本轮重启budget/API/web/live/jobs/streams；PostgreSQL保留运行与已有数据。持续监控保持暂停，没有交易启用或策略参数调整。', '',
    '## 1. 服务与代码身份', '',
    f'- 18770/18771/18772/55432监听：`{e["ports"]}`；本地健康、operations、观察、买入列表、网页与因果追踪接口均见保存的HTTP状态。',
    f'- 六服务PID/启动ticks复核：{sum(r["identity_match"] for r in identities)}/{len(identities)}匹配；源码哈希差异：{len(e["source_hash_mismatches"])}。',
    f'- 当前run新增数据库死锁：{e["postgres_new_deadlocks"]}；本轮失败任务：{len(e["queries"]["failed_this_run"])}；最终采样阻塞链：{len(e["queries"]["blocking"])}。这些是有限观察结果，不代表以后不会发生。',
    f'- 分级迁移：`{e["queries"]["revision"]}`；所查三张分级表及最新快照索引均以原始indexes记录为准。', '',
    '## 2. 价格窗口：等满15分钟后统计', '',
    '|采样|启动后秒数|5m ready/eligible|15m ready/eligible|',
    '|---|---:|---:|---:|',
]
for phase in ('initial','warmup','first-cycle','five-minute','ten-minute','final'):
    sample = read(phase+'.json')
    disc = next(x['details'] for x in sample['queries']['heartbeats'] if x['role']=='anomaly_discovery')
    c = disc.get('window_coverage',{})
    lines.append(f'|{phase}|{sample["age_seconds"]}|{c.get("5",{}).get("ready")}/{c.get("5",{}).get("eligible")}|{c.get("15",{}).get("ready")}/{c.get("15",{}).get("eligible")}|')
lines += ['', f'最终价格统计snapshot_at：`{discovery["snapshot_at"]}`，已晚于本run启动15分钟；此前15m=0属于未攒满窗口。', '']
for window,c in coverage.items():
    n=c['eligible']; pct=round(c['ready']/n*100,2) if n else None
    lines.append(f'- {window}m：{c["ready"]}/{n}（{pct}%）；未就绪原因：`{json.dumps(c["unavailable"],ensure_ascii=False)}`。')
lines += ['', '窗口为合格市场逐币统计。合约mark、现货last及成交额K线语义保持独立；进入T2/T3本身不等于价格历史清空。现货changed-only源不保证逐币15s连续；需要看分层样本的source_kind、源/接收空档，不能靠放宽门槛凑覆盖。', '', '## 3. 新诊断确认的剩余问题', '',
    f'关闭统计（从本run开始累计，sample范围）：`{json.dumps(d["closure_by_venue_channel_reason"],ensure_ascii=False)}`。主动subscription_change与failure分开。', '',
    'market通道有本地sent1011/pong超时；保存的连接时间线可见千帧级队列和十几至几十秒read pause。ping期间读暂停、pong未确认支持接收消费背压参与故障；不能据此断言全部是网络问题或DB问题。global价格源与5m discovery通道需各自核验，不能把market重连次数直接当价格断档次数。', '',
    '|连接池|长持有记录数（detail时点）|最长持有ms|最长例SQL ms|借出阶段|',
    '|---|---:|---:|---:|---|',
]
for h in d['long_hold_summary']:
    x=h['examples'][0]
    lines.append(f'|{h["pool"]}|{h["records"]}|{h["max_hold_ms"]}|{x["sql_ms"]}|{x["checkout_stage"]}|')
lines += ['', 'warning在归还连接时记录持有历史。SQL、guard、commit及发布锁时间与持有时间分开；结构scan_signals路径的非SQL跨度需要继续定位，不能凭警告删锁、扩池或杀连接。此轮只验证，没有新增业务修复。', '',
    f'OI最新采集覆盖：`{json.dumps(e["queries"]["coverage"],ensure_ascii=False)}`。独立OI15m：`{json.dumps(discovery["oi_window_coverage"],ensure_ascii=False)}`。普通300s采集与90s最新端点资格会产生就绪率摆动，6m采集覆盖与15m触发窗口不可混算。', '',
    f'OI有界持久化对照：`{json.dumps(d["oi_probe_summary"],ensure_ascii=False)}`。缺失的是抽样端点，不是漏信号或独立币数量；须结合≥5%穿越、known时间和评估回执再判断。', '',
    '## 4. 身份、任务与投影', '',
    f'- 最终活跃观察需求：{identity["active_demands"]}；需求事件ID：{identity["demand_ids"]}；同端点身份不一致：{identity["same_endpoint_identity_mismatches"]}。存量同步恢复不算新到达率。',
    '- 本轮真实source_at在重启之后的事件按instrument+event_id查询分析回执，见final-detail.json。ASTER、APR、BULLA等已获得对应分析结果；waiting_inputs或被新事件覆盖不能单独视为丢失。未证明新事件完整走到买入投影，不拿旧计划补作成功。',
    f'- 当前run分析结果：`{json.dumps(e["queries"]["results_this_run"],ensure_ascii=False)}`；结果数、独立市场数、累计执行数分别记录。',
    f'- Producer：`{json.dumps(workers.get("analysis_owner",{}).get("producer",{}),ensure_ascii=False)}`；2+2名额与真实tier采集频率见operations。',
    f'- BTC最近一小时已闭5m K线：`{json.dumps(e["queries"]["btc_5m_continuity"],ensure_ascii=False)}`；这是单币抽查，不证明全市场高周期补数完成。',
    '- 最新snapshot→projection的对应及延迟保存于final.json；报价队列年龄与writer提交见final-detail.json channels。没有合格买入、expired/no_chase不单独作为故障。', '',
    '## 5. 证据范围与下一步', '',
    f'- SQL/API/日志访问盲区：`{json.dumps(result["blind_spots"],ensure_ascii=False)}`。早期detail工具oi_tiers错列已修正，后续样本读取成功，不属于业务数据库异常。',
    '- final.json的采样起始、operations.snapshot_at和final-detail.json的detail_as_of分别保存；长持有列表可能晚于主采样，不能强行当同一时点。',
    '- 优先用连接时间线核market消费阻塞；按逐币样本核价格缺失源；将结构纯计算与持有阶段对齐后再设计事务调整；对OI抽样端点确认是否真实遗漏触发。普通300s、重点60s、OI15m/≥5%、15s价格连续、90s报价/盘口与发布顺序1877101保持。',
    f'- 原始证据：`{OUT}`。持续监控未恢复；本轮验证到此结束。', '',
]
report = DOCS / 'P40_R4_激活验证结果.md'
report.write_text('\n'.join(lines), 'utf8')
implementation = ROOT/'docs/p40-validation/r4-implementation-20261010/implementation-result.json'
m=json.loads(implementation.read_text('utf8'))
m.update(status=result['status'],activation_run=e['run_id'],activation_verified_at_bjt=now,activation_evidence=str(OUT/'activation-result.json'))
implementation.write_text(json.dumps(m,ensure_ascii=False,indent=2),'utf8')
code_report=DOCS/'P40_R4_代码更新结果.md'
content=code_report.read_text('utf8')
content=content.replace('**代码已更新并通过隔离验证，尚未重启激活。**','**代码已更新并通过隔离验证，已于2026-10-10重启激活；生产仍有待解决问题。**')
content+='\n\n## 6. 本轮激活结果\n\n当前批次 `'+e['run_id']+'`。完整有限验证见 `P40_R4_激活验证结果.md`；定时监控保持暂停。\n'
code_report.write_text(content,'utf8')
append = '\n\n## Codex R4 重启激活验证 '+now+'\n\n批次 `'+e['run_id']+'`，端口/API及同端点事件身份核验通过；未发现本run新增死锁或失败任务。\n\n成熟价格窗5m '+str(coverage['5']['ready'])+'/'+str(coverage['5']['eligible'])+'，15m '+str(coverage['15']['ready'])+'/'+str(coverage['15']['eligible'])+'。market背压重连和结构阶段连接长持有仍存在；OI有界样本端点缺失尚不能等同漏触发。具体时间线、分层缺失、SQL/非SQL跨度及新事件回执见 `P40_R4_激活验证结果.md` 和原始证据目录。\n\n请GROK复核根因与后续修复边界；本轮没有顺手扩池/删锁/升频或放宽门槛。监控仍暂停。\n'
for filename in ('P40_评审往来.md','P40_ERJI_异动疏通与数据可靠性修复方案.md'):
    path=DOCS/filename
    with path.open('a',encoding='utf8') as f:f.write(append)
statepath=ROOT/'docs/p39-validation/activation-monitor-state.json'
state=json.loads(statepath.read_text('utf-8-sig'))
state['p40_r4_latest_verification']=str(OUT/'activation-result.json')
assert state.get('monitoring_status')=='paused'
statepath.write_text(json.dumps(state,ensure_ascii=False,indent=2),'utf8')
print(json.dumps({'report':str(report),'status':result['status'],'price_windows':coverage,'monitoring_status':state['monitoring_status']},ensure_ascii=False))
