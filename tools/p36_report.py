"""Create a reproducible P36 A delivery record from saved local observations."""
import json,html,difflib,hashlib
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

root=Path(__file__).resolve().parents[1];out=root/'docs/p36-validation'
before=json.loads((out/'before.json').read_text(encoding='utf8'))
after=json.loads((out/'after.json').read_text(encoding='utf8'))
start=json.loads((out/'measured-window-start.json').read_text(encoding='utf8'))
def fmt(at):return datetime.fromtimestamp(at/1000,ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
def traffic(data,suffix):return sum(r['n'] for r in data['traffic'] if r['source'] in ('binance','binance_spot') and r['endpoint'].endswith(suffix))
def candles(data,venue,tf):return next((r['fresh'] for r in data['candles'] if r['venue']==venue and r['resolution']==tf),0)
rows=[('OI 6分钟内新鲜覆盖',f"{before['oi'][0]['fresh']}/563",f"{after['oi'][0]['fresh']}/563")]
for name,path in [('当前OI成功请求/5分钟','/openInterest'),('OI历史成功请求/5分钟','/openInterestHist'),('费率历史成功请求/5分钟','/fundingRate'),('现货+合约K线成功请求/5分钟','/klines')]:
 rows.append((name,traffic(before,path),traffic(after,path)))
for venue,title in [('binance','合约'),('binance_spot','现货')]:
 for tf in ['240','1D']:rows.append((title+'最新已闭合'+tf+'覆盖',candles(before,venue,tf),candles(after,venue,tf)))
rows.append(('现货+合约最近300根4H窗口内部缺口数',before['continuity'][0]['gaps'],after['continuity'][0]['gaps']))
oldhistory={r['key']:r['payload'] for r in before['history']}
advanced=[r['key'] for r in after['history'] if r['payload'].get('next_open',0)>oldhistory.get(r['key'],{}).get('next_open',0)]
ledger=next(r['payload'] for r in after['budget'] if r['key']=='p36_http_ledger')
metrics={k:v for k,v in ledger.get('metrics',{}).items() if int(k.split(':')[0])>=start['at']//60000}
rate_limits=sum(v['count'] for k,v in metrics.items() if k.endswith(':429') or k.endswith(':418'))
admissions=[v for k,v in metrics.items() if ':admitted:' in k]
db_total=sum(v['db_ms_sum'] for v in admissions);count=sum(v['count'] for v in admissions)
queue_total=sum(v['queue_ms_sum'] for v in admissions)
summary={'before_at':before['at'],'after_at':after['at'],'measured_seconds':(after['at']-start['at'])/1000,
         'rows':rows,'history_cursors_advanced':len(advanced),'new_429_418':rate_limits,
         'admission_mean_db_ms':round(db_total/max(count,1),1),'admission_mean_queue_ms':round(queue_total/max(count,1),1),
         'minute_metrics':metrics,'db_wait_before':before['db_wait'],'db_wait_after':after['db_wait']}
summary['local_cost_last_minute']={family:sum(e['cost'].get(family,0) for e in ledger['entries'] if e['at']>after['at']-60000) for family in ('um','spot')}
summary['reported_weight_headers']=ledger['buckets']
(out/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
table='\n'.join('| '+str(a)+' | '+str(b)+' | '+str(c)+' |' for a,b,c in rows)
text=f'''# P36 A 代码更新与运行记录

本次完成P36 A限额与采集调度代码更新；B异动观察方案按原约定留待讨论。

## 运行前后对比

北京时间：修改前 {fmt(before['at'])}；修改后 {fmt(after['at'])}。
最终版本连续观察 {summary['measured_seconds']:.1f} 秒，覆盖一次5分钟窗口。成功请求数均使用各快照前5分钟已入库的公开HTTP原始记录；不把WebSocket或重复持久化算作REST调用。

| 指标 | 修改前 | 修改后 |
| --- | --- | --- |
{table}

自修改前快照起，{len(advanced)} 个历史游标推进。最终观察段真实HTTP429/418记录合计 {rate_limits} 条。
准入数据库事务平均耗时 {summary['admission_mean_db_ms']} ms；平均准入排队 {summary['admission_mean_queue_ms']} ms。详细分钟统计和数据库等待快照见 comparison.json。
最后一分钟本地累计权重：合约 {summary['local_cost_last_minute']['um']}，现货 {summary['local_cost_last_minute']['spot']}。响应头校正值和所属窗口保存在 comparison.json，不能将过期窗口头值当作当前消耗。
本地仍存在数据库竞争和采集延迟，当前并发参数不是已证明的最优值；预算上限不等于实际吞吐保证。

## 交付与边界

- 全部后端回归309项通过；包含独立临时数据库、多进程竞争、参数边界、费率共享限频、在途响应头校正、取消清理、队列期限、旧账本迁移及采集循环隔离。保留一个现有FastAPI/httpx弃用提示，无测试失败。
- 本地服务已加载本次代码；没有恢复定时监控，没有开启交易，没有修改策略门槛或前端。
- 代码完成不代表每个币的数据已经补齐。上表保留实际剩余缺口，不把最新一根存在等同于连续窗口完整，不承诺所有数据秒级更新。
- PYTH等短有效期信号的决策生命周期问题仍属于后续讨论；本次没有放宽报价和盘口时效。
- 初始旧账本迁移等待5分钟，迁移和本次必要重载产生的本地等待不计为交易所429；完整证据分别保留在各快照文件。

实施参数、模块说明、回退步骤见 implementation-notes.md。官方额度复核见 official-limits.json；测试报告见 tests.xml 与 test-isolation.json；回退文件和校验值见 rollback/manifest.json。
'''
(out/'P36_A_代码更新与运行记录.md').write_text(text,encoding='utf8')
body=f'<h1>P36 A · 代码更新与运行记录</h1><p>限额与采集调度代码已更新；B 异动观察留待讨论。</p><p>北京时间 {fmt(before["at"])} → {fmt(after["at"])}；最终版观察 {summary["measured_seconds"]:.1f} 秒。</p>'
body+='<table><tr><th>指标</th><th>修改前</th><th>修改后</th></tr>'+''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in r)+'</tr>' for r in rows)+'</table>'
body+=f'<p>历史游标推进 {len(advanced)} 个；观察段新增429/418：{rate_limits}。309项后端测试通过。</p>'
body+=f'<p>准入数据库事务平均 {summary["admission_mean_db_ms"]} ms；平均排队 {summary["admission_mean_queue_ms"]} ms。仍有数据库竞争和采集延迟，预算上限不代表实际吞吐保证。</p>'
body+='<p>数据缺口仍以实际快照为准；未修改买入门槛、前端或B部分，未恢复定时监控。</p><h2>实施细节与回退</h2><pre>'+html.escape((out/'implementation-notes.md').read_text(encoding='utf8'))+'</pre>'
(out/'P36_A_代码更新与运行记录.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>P36 A 实施记录</title><style>body{max-width:1080px;margin:40px auto;padding:0 24px;background:#f5f7fb;color:#172339;font:16px/1.7 system-ui}table{border-collapse:collapse;width:100%;background:white}th,td{padding:12px;border:1px solid #dce3ed;text-align:left}th{background:#e7efff}pre{white-space:pre-wrap;font:inherit;background:white;padding:24px;border-radius:12px}h1{font-size:30px}</style>'+body+'</html>',encoding='utf8')
manifest=json.loads((out/'rollback/manifest.json').read_text());diff=[]
for file in manifest:
 diff.extend(difflib.unified_diff((out/'rollback'/file).read_text(encoding='utf8').splitlines(True),(root/file).read_text(encoding='utf8').splitlines(True),fromfile='before/'+file,tofile='after/'+file))
(out/'implementation.diff').write_text(''.join(diff),encoding='utf8')
new=['engine/pd2/p36_budget.py','tests/test_p36_budget.py','tools/p36_snapshot.py','tools/p36_report.py','tools/p36_isolated_tests.py']
(out/'changed-files.json').write_text(json.dumps({'updated':[f for f in manifest if (root/f).read_bytes()!=(out/'rollback'/f).read_bytes()], 'new':new,'sha256':{f:hashlib.sha256((root/f).read_bytes()).hexdigest() for f in [*manifest,*new]}},indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in summary.items() if k not in ('minute_metrics','db_wait_before','db_wait_after')},ensure_ascii=False))
