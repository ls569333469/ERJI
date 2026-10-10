"""Save R3 source identity, isolated evidence and the reviewable local preview."""
from pathlib import Path
import json,hashlib,difflib,ast,html,xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
DOCS=ROOT.parents[2]/'docs'
OUT=ROOT/'docs/p40-validation/r3-implementation-20261009'
before=json.loads((OUT/'before-hashes.json').read_text('utf-8-sig'))
extra=['tests/test_p40_r3.py','tests/test_p39_layered_analysis.py','tests/test_p39_timing_concurrency.py',
       'tests/test_p33_remaining.py','tools/p40_r3_capacity_replay.py','tools/p40_r3_diagnostic_benchmark.py',
       'tools/p40_r3_implementation_report.py']
files=sorted(set(before)|set(extra))
patch=[]
for name in files:
    p=ROOT/name;ast.parse(p.read_text('utf-8-sig'),filename=name)
    old=OUT/'before'/name
    if old.exists():patch.extend(difflib.unified_diff(old.read_text('utf-8-sig').splitlines(True),p.read_text('utf-8-sig').splitlines(True),fromfile='before/'+name,tofile='after/'+name))
(OUT/'source.patch').write_text(''.join(patch),'utf8')
tests=[];unique=set()
for prefix in ('p40-r3','p40-r3-extra'):
    xml=ROOT/('docs/p39-validation/'+prefix+'-tests.xml')
    root=ET.parse(xml).getroot();cases=root.findall('.//testcase')
    failures=sum(bool(c.findall('failure')+c.findall('error')) for c in cases)
    assert failures==0
    unique.update((c.get('classname'),c.get('name')) for c in cases)
    tests.append({'report':str(xml.relative_to(ROOT)),'cases':len(cases),'failures':failures,
        'isolation':json.loads((ROOT/('docs/p39-validation/'+prefix+'-test-isolation.json')).read_text('utf8'))})
replay=json.loads((OUT/'real-executor-replay.json').read_text('utf8'))
assert replay['completed_tasks']==24 and replay['deadlocks']==0
benchmark=json.loads((OUT/'diagnostic-benchmark.json').read_text('utf8'))
stamp=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
scope=[
 ('诊断','接收器跨重连保留最近16次关闭记录，包含run/connection身份、双向关闭码、实际输入空档、收包/写队列/循环耗时。新增逐币诊断只更新内存，按既有心跳聚合保存；导出30个市场样本。'),
 ('资源','每进程围栏预算仍为8：分析4、入场/保护2、补数2。跨事件循环的预算在连接借用前生效；等待不占后台线程。取消、异常均归还预算并清锁。'),
 ('结构','技术指标与识别准备移到mutation fence之前；写入时重新检查K线、精度、父事件及任务租约。盘口请求和补数持久化的专用锁仍覆盖全过程。'),
 ('投影','按原生市场固定相位分散300秒巡检。同一周期的新快照只补轻量投影；到期时先补轻量快照，再处理重型候选。候选渲染离开读连接，活跃OI聚合写入按排序分批，历史分支保留。'),
 ('排队','区分first_pending_at、latest_desired_at、available_at与实际开始/完成；保留累计尝试和延期次数，完成或新版本提交清理旧延期原因。'),
 ('退避','订阅确认不计作稳定行情。连续收到真实数据后才重置接收器退避，重试增加抖动，结束连接先清理队列与资源；沿用原有supervisor。'),
 ('接口','/v1/operations保留新增lifecycle与price_receipts；因果追踪补充排队时钟。启动日志使用P40-reliability-r3及新增源文件哈希。'),
]
pending=[
 '行情断档的网络、服务端关闭、GIL或落库背压根因仍须读取R3当前run的关闭、逐币空档、锁等待和写队列证据。没有先拆进程、更换现货流或增加接收连接。',
 '合约mark与清算仍沿用既有接收来源；本轮记录事实写入等待，未在缺少实测背压证据时拆接收通道。',
 '激活后需有限复查5m/15m连续窗口、OI15m端点/90s与6m覆盖、连接等待及最新快照匹配。真实新事件的浏览器首次送达须有实际事件证明，不能用旧计划或HTTP200代替。',
]
rules='普通300s、T2重点60s、结构/趋势2＋2、OI15m/≥5%、T1连续15s、报价/盘口90s保持原口径；费用仅参考，盘口硬门保留，1877101发布顺序保留。无数据库迁移，无重启、交易或持续监控。'
result={'updated_at':stamp,'status':'code_updated_not_activated','code_generation':'P40-reliability-r3',
    'production_root':str(ROOT),'changed_files':list(before),'support_files':extra,
    'after_hashes':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files},
    'tests':tests,'unique_test_cases':len(unique),'ast_check':'passed','replay':{
        k:replay[k] for k in ('completed_tasks','elapsed_seconds','deadlocks','observed_peak_inflight')},
    'diagnostic_benchmark':benchmark,'fence_budget':{'analysis':4,'urgent':2,'gap':2,'per_process_total':8},
    'strategy_rules':rules,'pending_activation_verification':pending,'scope':scope}
(OUT/'implementation-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
md='# P40 R3 · 代码更新结果\n\n'+f'记录时间（北京时间）：{stamp}。状态：代码已更新，尚未重启激活。\n\n'
md+='## 已完成\n\n'+'\n'.join(f'- **{title}**：{body}' for title,body in scope)+'\n\n'
md+='## 验证\n\n'+f'- {len(unique)}项不同回归用例全部通过（主批次{tests[0]["cases"]}项、补充批次{tests[1]["cases"]}项，含重叠复核）；两批均使用独立临时数据库。\n'
md+=f'- 保存的12市场公开K线回放：24项实际执行器任务，{replay["elapsed_seconds"]}秒，零死锁；实际并发结构2、趋势2。该有限夹具不等同生产吞吐量。\n'
md+=f'- 750个市场的CPU模拟收包：原版中位{benchmark["median_before_ms"]}ms/轮、R3中位{benchmark["median_r3_ms"]}ms/轮；一次窗口健康统计{benchmark["health_snapshot_ms"]}ms；30个诊断样本JSON {benchmark["bounded_receipt_json_bytes"]}字节。模拟逐tick写库0次，不包含网络、生产写库负载或交易所数据质量。\n'
md+='- 已修改Python文件AST解析通过；修改前源码、哈希、源代码差异、隔离记录及回放结果保存在`'+str(OUT)+'`。\n\n'
md+='## 激活后核验\n\n'+'\n'.join('- '+p for p in pending)+'\n\n'+rules+'\n'
(DOCS/'P40_R3_代码更新结果.md').write_text(md,'utf8')
suffix='\n\n## R3实施记录 · '+stamp+'\n\n'+md.split('## 已完成\n\n',1)[1]
for name in ('P40_ERJI_异动疏通与数据可靠性修复方案.md','P40_R3_激活后问题调查与修订方案.md'):
    path=DOCS/name;original=path.read_text('utf-8-sig')
    begin='<!-- CODEX_R3_IMPLEMENTATION_START -->';end='<!-- CODEX_R3_IMPLEMENTATION_END -->'
    block=begin+suffix+'\n'+end
    if begin in original and end in original:
        prefix,rest=original.split(begin,1);_,tail=rest.split(end,1);original=prefix+block+tail
    else:original=original+'\n\n'+block+'\n'
    status_begin='<!-- CODEX_R3_STATUS_START -->';status_end='<!-- CODEX_R3_STATUS_END -->'
    status=status_begin+'\n> 最新进度（'+stamp+'）：R3代码已更新，'+str(len(unique))+'项不同回归用例通过；待重启激活后核验实际行情恢复。详见`P40_R3_代码更新结果.md`。\n'+status_end
    if status_begin in original and status_end in original:
        prefix,rest=original.split(status_begin,1);_,tail=rest.split(status_end,1);original=prefix+status+tail
    else:
        heading,separator,body=original.partition('\n');original=heading+separator+'\n'+status+'\n'+body
    path.write_text(original,'utf8')
review=DOCS/'P40_评审往来.md'
with review.open('a',encoding='utf8') as f:
    f.write('\n\n## Codex · R3实施反馈 · '+stamp+'\n\n用户已授权“执行吧”；代码更新和隔离验证已完成，未重启。结果见`P40_R3_代码更新结果.md`。\n\n'+
        '\n'.join(f'- {title}：{body}' for title,body in scope)+'\n\n'+
        f'验证：{len(unique)}项不同用例通过，12市场24任务回放{replay["elapsed_seconds"]}秒、零死锁，实际2＋2。\n\n'+
        '请重点复核：跨循环借用前预算与取消清理；prepare_scan提交重验证；固定相位与快照补发布；候选分批写入的原生范围；新增指标开销。行情根因及实际恢复继续按激活后的新run证据判断。\n')
rows=''.join('<tr><th>'+html.escape(title)+'</th><td>'+html.escape(body)+'</td></tr>' for title,body in scope)
limits=''.join('<li>'+html.escape(p)+'</li>' for p in pending)
preview='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P40 R3 · 代码更新结果</title><style>body{font:16px/1.7 system-ui;background:#eef2f6;color:#172537;margin:0}main{max-width:1000px;margin:auto;padding:36px 24px}h1{font-size:30px}section{background:white;border:1px solid #dce3eb;border-radius:14px;padding:22px;margin:20px 0}table{border-collapse:collapse;width:100%}th,td{padding:14px;border-bottom:1px solid #e4eaf1;text-align:left;vertical-align:top}th{width:80px;color:#1957a5}.status{display:inline-block;background:#fff0c5;padding:6px 16px;border-radius:20px}.metrics{display:flex;gap:24px;flex-wrap:wrap}.metrics b{font-size:25px;display:block;color:#1957a5}small{color:#57677a}li{margin:12px 0}a{color:#1957a5}@media(max-width:600px){main{padding:18px 12px}th,td{padding:10px}section{padding:14px}}</style><main><h1>P40 R3 · 代码更新结果</h1><div class="status">代码已更新 · 待重启激活</div><p>记录时间（北京时间）：STAMP</p><section><div class="metrics"><div><b>TESTS 项</b>不同回归用例通过</div><div><b>24 / 24</b>真实执行器任务完成</div><div><b>SECONDS 秒</b>12市场保存夹具回放</div><div><b>0</b>回放死锁</div></div><p>实际并发：结构 2 ＋ 趋势 2。测试均在临时数据库进行，回放使用已保存的公开K线。</p></section><section><h2>已完成改动</h2><table>ROWS</table></section><section><h2>诊断开销实测</h2><p>750个市场每轮输入：原版 BEFORE ms，R3 AFTER ms；一次窗口健康统计 HEALTH ms。诊断导出30个市场样本，JSON约 BYTES 字节，逐tick写库0次。</p><small>三轮有限CPU回放；这些数字不等同生产吞吐量，也不能证明网络断档已经恢复。</small></section><section><h2>激活后仍需核验</h2><ul>LIMITS</ul><p>RULES</p></section><p><a href="P40_R3_review.html">返回 R3 评审方案</a></p></main></html>'''
values={'STAMP':stamp,'TESTS':str(len(unique)),'SECONDS':str(replay['elapsed_seconds']),'ROWS':rows,
    'BEFORE':str(benchmark['median_before_ms']),'AFTER':str(benchmark['median_r3_ms']),
    'HEALTH':str(benchmark['health_snapshot_ms']),'BYTES':str(benchmark['bounded_receipt_json_bytes']),
    'LIMITS':limits,'RULES':html.escape(rules)}
for key,value in values.items():preview=preview.replace(key,value)
(ROOT/'apps/web/public/previews/P40_R3_acceptance.html').write_text(preview,'utf8')
print(json.dumps({'status':result['status'],'unique_test_cases':len(unique),'replay':result['replay'],'files':len(files)},ensure_ascii=False))
