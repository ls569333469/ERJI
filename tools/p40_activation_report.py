"""Render saved activation evidence; never contacts the exchange or database."""
import json, sys, html
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DOCS=ROOT.parents[2]/'docs'
OUT=ROOT/'docs/p40-validation/activation-20261009'
phase=sys.argv[1] if len(sys.argv)>1 else 'final'
e=json.loads((OUT/(phase+'.json')).read_text('utf8'))
first=json.loads((OUT/'initial.json').read_text('utf8'))
ident=json.loads((OUT/'process-identity-after.json').read_text('utf-8-sig'))
startup=json.loads((Path(e['registry'][0]['log_dir'])/'startup.json').read_text('utf-8-sig'))
workers={k:r['startup'] for k,r in e['logs'].items() if r.get('startup')}
activated=(startup['status']=='ready' and all(e['ports'].values()) and
           all(r['identity_matches'] for r in ident) and not e['source_hash_mismatches'] and
           len(workers)==4 and all(r['run_id']==e['run_id'] and r['code_generation']=='P40-reliability-v1' for r in workers.values()) and
           all(not r.get('startup_hash_mismatches') for r in e['logs'].values()))
assert activated, 'Activation evidence incomplete; do not label activated'
beats={b['role']:b for b in e['queries']['heartbeats']}
disc=beats['anomaly_discovery']['details']
owner=beats['analysis_owner']['details']
initial_buy=json.loads((OUT/'initial-buy-api.json').read_text('utf8'))
latest_buy=json.loads((OUT/(phase+'-buy-api.json')).read_text('utf8'))
buy=lambda data:[{k:r.get(k) for k in ('symbol','instrument_id','action_code','plan_id','snapshot_id','decision_as_of','valid_until','opportunity_level')} for r in data['rows'] if r.get('action_code') in ('participate','trial') and r.get('opportunity_level')=='l1']
task_totals=lambda sample:{k:sum(int(r['completed'] or 0) for r in sample['queries']['tasks'] if r['kind']==k) for k in {r['kind'] for r in sample['queries']['tasks']}}
before=task_totals(first);after=task_totals(e)
deltas={k:v-before.get(k,0) for k,v in after.items()}
receivers=[b for b in e['queries']['heartbeats'] if b['role'].startswith(('discovery_receiver','stream_binance'))]
quote=[{'role':b['role'],'state':b['state'],**(b['details'].get('quote_writer') or {})} for b in receivers if b['details'].get('quote_writer')]
warnings=sum(r['long_connections'] for r in e['logs'].values())
oi=e['queries']['oi_committed_this_run'][0]
cov=next(r for r in e['queries']['coverage'] if r['venue']=='binance')
result={'status':'activated_with_followup','run_id':e['run_id'],'captured_at_beijing':e['captured_at_beijing'],
 'activated':activated,'code_generation':'P40-reliability-v1','age_seconds':e['age_seconds'],
 'ports':e['ports'],'source_hashes_match':True,'process_identities_match':True,
 'postgres_new_deadlocks':e['postgres_new_deadlocks'],'current_run_long_connection_warnings':warnings,
 'new_10055_log_count':sum(r['10055'] for r in e['logs'].values()),'blind_spots':e['blind_spots'],
 'price_window_coverage':disc.get('window_coverage'),'oi_window_coverage':disc.get('oi_window_coverage'),
 'latest_detection_batch':disc.get('event_delivery'),
 'window_statistics_at':disc.get('snapshot_at'),'database_connections':owner.get('database_timings',{}).get('connections'),
 'task_completion_increments_between_samples':deltas,'task_increment_scope':'executions, not new independent coins',
 'oi_committed_this_run':oi,'native_contract_coverage':cov,'quote_queues':quote,
 'first_sample_buy_api':buy(initial_buy),'latest_sample_buy_api':buy(latest_buy),
 'validation_scope':'finite local read-only samples; not recurring monitoring; no trading enabled',
 'production_migration_required':False,'database_restarted':False}
(OUT/'activation-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
windows='；'.join(f"价格 {m}m：{v['ready']}/{v['eligible']}（{v['unavailable']}）" for m,v in disc['window_coverage'].items())
oiwin=disc['oi_window_coverage']['15']
current=', '.join(r['symbol'] for r in buy(latest_buy)) or '本次前五行未出现有效 L1 买入'
md=f'''# P40 · 重启激活与验证结果

**P40 已重启激活；仍有后续问题，不能判定全部数据正常。**

核验时刻：{e['captured_at_beijing']}（北京时间）。运行批次：`{e['run_id']}`，已启动 {e['age_seconds']} 秒。

## 已确认

- 六个注册服务进程 PID/启动 ticks 身份匹配；四个 worker 的启动 manifest 为 `P40-reliability-v1`，源码哈希与实施清单一致。
- 18770 / 18771 / 18772 / 55432 均可连接；健康、运行状态、观察、买入 API，以及观察/买入页面 HTTP 均返回 200。原生因果追踪 API 返回 200。
- 数据库仍为 `p39_layered_analysis`，与代码 head 一致；无需新增迁移，保留原数据库进程与数据。
- 启动后 PostgreSQL 新死锁 {e['postgres_new_deadlocks']}；当前日志 10055 {result['new_10055_log_count']}。采样时阻塞事务 {len(e['queries']['blocking'])}，超过八秒的事务 {len(e['queries']['long_transactions'])}；没有将旧任务累计失败当成新错误。
- 新批次提交 OI {oi['points']} 条、覆盖 {oi['native_markets']} 个原生合约市场；采样合约状态 {cov['markets']} 个，报价90s内 {cov['quote_90s']}，OI6m内 {cov['oi_6m']}，成交额10m内 {cov['volume_10m']}。
- 两次样本之间任务完成增量：`{json.dumps(deltas,ensure_ascii=False)}`。这是执行次数，包含重复更新和存量需求恢复，不能作为新增独立币数量。
- 当前提交异动批次：`{json.dumps(disc.get('event_delivery'),ensure_ascii=False)}`。2 个合格事件来自同一 LYN 原生市场，1 次更新、1 次同条件合并，不是2个新增币；本批次未解释事件为0。
- 21:00:22 样本的买入 API 已有 BTW / CYBER 的 L1“建议买入”，含 plan_id/snapshot_id，投影在当时 valid_until 内可见。最终前五行的 L1：{current}。此为历史采样结果，信号会随条件和有效期变化；不把已有计划复核称作新 T1 事件成功。

## 尚未确认恢复的部分

- 当前批次查询连接长占用日志共 {warnings} 次。日志警告在 checkin/归还时输出，说明对应连接已归还；仍是耗时问题，不能等同于连接泄漏或死锁，也不能当作已解决。
- 围栏连接是另一个独立池，持有时间另列于 `activation-result.json` 的 database_connections；部分任务持有较久，不能把短查询连接的 p95 当作所有围栏的耗时。
- {windows}。统计时刻为 `{disc.get('snapshot_at')}`；价格窗口须按该快照时刻判断暖机，不能直接用报告时刻推算。15m 尚需足够运行时间，不能判定连续性已全面恢复。
- OI15m 独立窗口可计算 {oiwin['ready']}/{oiwin['eligible']}，缺失原因 `{oiwin['unavailable']}`；OI6m采集新鲜与OI90s信号端点不是同一指标。
- BTC 合约最近一小时已收盘5m K线抽查：{e['queries']['btc_5m_continuity'][0]['bars']} 根，间隔异常 {e['queries']['btc_5m_continuity'][0]['gaps']}。此为单市场抽查，不能代表全市场。
- 报价队列按 oldest_pending_age_ms 判断，不用合并次数或 pending 数量推断丢失；详细接收与写入指标已保留在样本 JSON。
- 本次没有完整的新实时合格事件→观察→需求→原生计划→API/浏览器因果证据；已有买入 API 与页面200不证明所有事件都已成功传递。

本次仅重启激活与有限采样，未启用交易、外部通知或持续监控。盲区：`{e['blind_spots']}`。

证据目录：`{OUT.as_posix()}`。包含 preflight、初始/后续 JSON、注册日志尾部、PostgreSQL 增量、API 响应及 activation-result.json。
'''
(DOCS/'P40_激活验证结果.md').write_text(md,'utf8')
body=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P40 · 重启激活验证</title><style>body{{font:16px/1.8 system-ui;background:#0c1220;color:#e5eaf4;margin:0}}main{{max-width:1040px;margin:40px auto;padding:24px}}h1{{font-size:30px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#121d30;padding:24px;border-radius:12px;font-family:inherit}}a{{color:#80c7ff}}</style><main><h1>P40 · 重启激活验证</h1><p>已激活 · {html.escape(e['run_id'])}</p><pre>{html.escape(md)}</pre><a href="/signals">买入机会</a> · <a href="/observations">市场观察</a></main></html>'''
(OUT/'P40_activation.html').write_text(body,'utf8')
(ROOT/'apps/web/public/previews/P40_activation.html').write_text(body,'utf8')
plan=DOCS/'P40_ERJI_异动疏通与数据可靠性修复方案.md'
text=plan.read_text('utf8')
text=text.replace('性质：**代码更新及隔离验证完成，尚未重启激活**；生产效果等待新批次核验','性质：**P40 已重启激活；仍有长连接警告及窗口覆盖待验证**；详见文末 §11')
text=text.replace('详见文末 §10','详见文末 §10 / §11',1)
text=text.replace('run_id=`20261009T065941-292cfb30`','当前 run_id=`'+e['run_id']+'`；审计历史批次另见原始样本',1)
section=f'''\n\n## 11. P40 重启激活结果（2026-10-09，北京时间）

用户明确授权：“重启激活”。已启动批次 `{e['run_id']}`，四个 worker 启动 provenance 为 `P40-reliability-v1`，六个服务进程身份及源码哈希匹配，四端口/API/页面正常响应。无需新增迁移，原数据库未重启。

截至 {e['captured_at_beijing']}，新死锁 {e['postgres_new_deadlocks']}、10055 {result['new_10055_log_count']}；长连接日志仍有 {warnings} 次。分析与 OI 在推进，但窗口仍须核验暖机/连续性，不宣称全部问题恢复。21:00 样本买入API有 BTW/CYBER L1，属当时有效计划复核，不当成新异动事件链证明。

详细当前窗口、连接指标、任务增量及证据见 [P40_激活验证结果.md](P40_激活验证结果.md)。前文“尚未激活”是本轮重启之前的实施历史状态。未开启交易、通知或持续监控。
'''
if '## 11. P40 重启激活结果' not in text: text+=section
plan.write_text(text,'utf8')
review=DOCS/'P40_评审往来.md'
marker='## GPT / Codex · P40 重启激活验证 · '+e['captured_at_beijing']
if marker not in review.read_text('utf8'):
    with review.open('a',encoding='utf8') as f:f.write('\n\n'+marker+'\n\n'+section.split('\n\n',2)[-1])
resultdoc=DOCS/'P40_代码更新结果.md'
text=resultdoc.read_text('utf8').replace('状态：**代码已更新，尚未重启激活。**','状态：**代码已更新并重启激活；当前验证见 [P40_激活验证结果.md](P40_激活验证结果.md)。**',1)
resultdoc.write_text(text,'utf8')
print(json.dumps({'status':result['status'],'run':e['run_id'],'report':str(DOCS/'P40_激活验证结果.md'),'warnings':warnings},ensure_ascii=False))
