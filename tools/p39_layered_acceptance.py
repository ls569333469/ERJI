"""Build a reviewable, local P39 implementation record from saved evidence."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime, timezone
import difflib, html, json, re
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/p39-validation/layered-analysis-implementation-20261007'
baseline=json.loads((OUT/'before-manifest.json').read_text('utf8'))
new_files=['engine/p39/__init__.py','engine/p39/pipeline.py','engine/p39/diagnostics.py',
    'migrations/versions/p39_layered_analysis.py','tests/test_p39_layered_analysis.py']
changed=[];patch=[]
for name,old_hash in baseline.items():
    path=ROOT/name
    if not path.exists():raise RuntimeError('Missing baseline file: '+name)
    digest=sha256(path.read_bytes()).hexdigest()
    if digest==old_hash:continue
    changed.append({'path':name,'before_sha256':old_hash,'after_sha256':digest})
    patch.extend(difflib.unified_diff((OUT/'before'/name).read_text('utf8').splitlines(True),
        path.read_text('utf8').splitlines(True),fromfile='before/'+name,tofile='after/'+name))
for name in new_files:
    path=ROOT/name
    changed.append({'path':name,'before_sha256':None,'after_sha256':sha256(path.read_bytes()).hexdigest()})
    patch.extend(difflib.unified_diff([],path.read_text('utf8').splitlines(True),fromfile='/dev/null',tofile='after/'+name))
# This pre-existing test was outside the initial selected baseline. Record it
# explicitly, without claiming it is new or inventing a pre-update fingerprint.
name='tests/test_pd2_plan_lifecycle.py'
changed.append({'path':name,'before_sha256':None,'after_sha256':sha256((ROOT/name).read_bytes()).hexdigest(),
    'change_type':'modified_test','baseline_available':False,'change':'add native venue to fixture; assert origin through box Plan/Snapshot/compact'})

detail='apps/web/src/components/observation-board.tsx'
old_detail=(OUT/'before'/detail).read_text('utf8').split('function ObservationDetail',1)[1]
new_detail=(ROOT/detail).read_text('utf8').split('function ObservationDetail',1)[1]
assert old_detail==new_detail,'Observation detail was changed'
chart_files=['apps/web/src/components/chart.tsx','apps/web/src/components/opportunity-detail.tsx']
checks={'observation_detail_unchanged':True,'opportunity_board_change':'business labels only; row fields unchanged'}
for name in chart_files:
    checks[name]={'sha256':sha256((ROOT/name).read_bytes()).hexdigest(),'modified_in_this_round':False}
    if name in baseline:assert checks[name]['sha256']==baseline[name]

test_root=ET.parse(ROOT/'docs/p39-validation/layered-analysis-full-tests.xml').getroot()
suites=list(test_root.iter('testsuite'))
tests={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
assert tests['tests']==199 and not tests['failures'] and not tests['errors']
isolation=json.loads((ROOT/'docs/p39-validation/layered-analysis-full-test-isolation.json').read_text('utf8'))
assert isolation['production_access'] is False and isolation['exit_code']==0
evidence={'recorded_at':datetime.now(timezone.utc).isoformat(),'scope':'code update; production migration/restart not executed',
    'tests':tests,'isolation':isolation,'frontend':{'typecheck':'passed','build':'passed; .next-p39-layered-check',
    'display_test_files':3,'display_tests':'passed'},'preservation':checks,'changed_files':changed}
(OUT/'implementation-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),'utf8')
(OUT/'implementation.diff').write_text(''.join(patch),'utf8')

text='''# P39 · T1 / T2 / T3 代码更新与验收

代码已按二次复核方案更新，199项隔离后端回归通过，前端类型检查、独立构建与三组显示测试通过。状态：代码完成；本轮未执行生产迁移、重启激活、交易启用或恢复长期监控。

## 1. 更新后的主链路

Binance事实 → T1轻量异动 / 候选唤醒 → 持久化分析需求 → T2趋势、结构分析 → 策略Episode → 冻结Plan → T3入场 / 保护核验 → SignalSnapshot → CurrentSignal → API → 页面。

T1不是所有策略的共同买入门槛。人工关注、已有有效候选、有效计划恢复都有独立来源；低频发现先以影子模式运行，不发布候选、计划或买入信号。

## 2. 方案A至E落实情况

| 批次 | 已更新的代码与行为 | 验收依据 |
| --- | --- | --- |
| A 需求与来源 | 新增analysis_demands、analysis_results、market_analysis_states；保存完整原生市场、来源、触发事实、配置指纹、输入版本、结果引用。OI数量方向与价格方向分开；24h成交额使用自己的来源与接收时钟，别名同步 | 来源隔离、字段口径、原生市场去重、旧计划legacy_unlinked、迁移升降往返 |
| B T2准入 | 可见记录与分析需求分离；无需求冷却记录不占深度名额；有效候选/人工关注/有效计划独立恢复；缺输入释放准入，闭合K线/修订、候选价格跨界和退避重试唤醒 | 冷却退出、候选保留、取消关注、补数唤醒、不同原生市场独立、真实计划恢复 |
| B 补充发现 | 现有主池USDT原生现货/合约按60秒轮转1个市场；仅结构通道没有待办实盘需求时提交影子任务；与现有结构执行槽共用，实盘任务优先 | 不新增进程/交易所请求通道；影子计算前后Episode、Plan、SignalSnapshot、CurrentSignal无新增 |
| C 结果复用 | 去除结构一分钟、趋势五分钟计算版本桶；版本依据K线修订、市场精度、算法和配置；计算时刻与复核时刻分开；同输入复用已有指标/趋势，不把过期K线变新鲜 | 时间推进不取消租约、复核不重复计算、旧K线仍标延迟、阶段间修订拒绝旧结果 |
| C 缩短事务 | Plan生成预读原生K线和配置，指标计算移出写事务；单市场提交；结构任务仅生成新Plan，已有Plan由entry/protect快通道管理 | 冻结计划/配置保持、报价越区与回区、保护触及、过期及终态回归 |
| D T3交接 | P33与箱体/PD2两条入口保存origin；新Plan即时交接entry；快照及列表投影保留Plan/结果来源；T3不等待观察六周期matrix全部完成 | 两条真实Plan生成测试都核对Plan、Snapshot和compact来源；现有入场资格回归 |
| E 页面与契约 | 主列表区分降频监测、等待输入、已分析无匹配、候选待触发和已有计划；买入页使用可买/等待业务标记；新增有界只读追踪API；同步OpenAPI/TypeScript | 199项后端回归、TS类型检查、独立构建、三组显示逻辑测试；详情与K线保持 |

## 3. 哪些行为保持

原异动参数保持：5m价格3%、15m价格5%、RVOL3倍且窗口成交额10万USDT、OI15m数量变化5%、24h成交额100万USDT。成交额缺失、来源不明或过期单独列为流动性待核实，不用最新报价替它续期。

策略必要依赖、买入区、保护、有效期与报价/原生盘口新鲜度沿用。成本、目标空间和盈亏比继续只作参考。历史计划不被同币新行情改写，终态不复活；原入场期结束后的保护依既有策略管理生命周期继续。

观察详情函数与本轮基线逐字一致；买入列表行字段保留，OpportunityDetail及Chart组件本轮没有修改。没有恢复Aster、Hyperliquid、CMC、OKX、GeckoTerminal或ByK。

## 4. 数据与状态如何核对

新增GET /v1/analysis-pipeline/{完整instrument_id}?limit=20，最多50条。仅查本地数据库，返回需求、趋势/结构状态、分析结果、Episode、Plan和最新Snapshot；GET不运行分析、不请求交易所。没有迁移或开关关闭时返回not_activated，不伪造正常结果。

来源记录冻结需求时的触发事实、source_at、规则/配置及价格依据；persisted_at是入库时刻。旧记录没有确切网络received_at时保留空值，不把入库时刻伪称交易所接收时刻。旧计划无真实关联时保留legacy_unlinked。

结果状态区分completed、waiting_inputs、failed、superseded；输入被修订替换属于重新排队，不计普通故障。正常未匹配、等待触发与执行故障分别记录。退休任务和影子任务不算新增独立市场完成。

## 5. 验证范围与证据

| 检查 | 结果 | 保存位置 |
| --- | --- | --- |
| 后端针对性回归 | 199通过，0失败；覆盖分级、租约/修订、信号准入、计划生命周期、Binance字段与既有三项优化 | ../layered-analysis-full-tests.xml |
| 测试隔离 | 新建临时PostgreSQL、随机端口、合成K线；未复制/读写生产数据，未请求交易所；结束后停止测试数据库 | ../layered-analysis-full-test-isolation.json |
| 前端类型检查 | npm.cmd run typecheck通过 | 本轮工具输出 |
| 前端独立构建 | ERJI_NEXT_DIST_DIR=.next-p39-layered-check，Next.js构建通过；未覆盖当前开发构建 | 本轮工具输出 |
| 显示逻辑 | opportunity-display / current-market-display / backend-studies三组通过 | tests/*.cjs |
| 本轮代码差异 | 按保存的124文件基线比较，另列新增文件；不是整个仓库既有修改 | implementation.diff、implementation-evidence.json |

测试中的成功买入是隔离回放结果，不是当前市场真实推荐。此前运行中的12至16秒任务耗时属于旧批次；本轮没有将其当成新代码实测，也没有宣称达到90%性能。

## 6. 激活与回退条件

生产迁移为p39_layered_analysis，基于p39_snapshot_latest，只新增三张表及索引，不补造历史来源。新表未建立时新需求选择器不启用。ERJI_P39_LAYERED_ANALYSIS=0可关闭新需求/API路径，继续兼容旧选择器；该开关不等于恢复所有旧源码，完全源码回退以before基线和implementation.diff为准。

下一次激活需先核对当前运行批次、数据库迁移头和注册进程，再迁移、按真实启动方式加载新代码；激活后比对有效需求与新结果，做有限运行验收。已有用户暂停的长期监控保持暂停。本轮未执行这些生产操作。

## 7. 激活后要测什么

分析心跳增加20分钟窗口：有效原生市场需求数、来源分布、新需求市场数、按市场/输入版本去重的完成数、任务服务耗时均值/P95。需求到达的市场单位与完成的市场/版本单位不同，不能直接把两个数字当同口径λ/μ；应再按固定需求集和首入队到完成时间测量。

依旧保留同市场租约及执行互斥，entry/protect与T2通道独立，但同市场提交仍遵守互斥。没有开启两个结构并发；剩余事务和同市场互斥对T3延迟的实际影响，要在激活后测量。排队P95≤30秒、完成P95≤120秒没有在本次隔离测试里冒充生产达标。

低频发现仅先影子验证；一分钟一个市场是保守初始预算，不代表全市场每分钟完整分析。启用真实候选发布、改变异动阈值、增加结构并发均需以后根据影子结果和服务能力单独处理。
'''
(OUT/'P39_T1_T2_T3代码更新与验收.md').write_text(text,'utf8')

def inline(value):
    value=html.escape(value)
    value=re.sub(r'\*\*(.+?)\*\*',r'<strong>\1</strong>',value)
    return value
lines=text.splitlines();parts=[];i=0
while i<len(lines):
    line=lines[i]
    if not line.strip():i+=1;continue
    if line.startswith('# '):parts.append('<h1>'+inline(line[2:])+'</h1>');i+=1;continue
    if line.startswith('## '):parts.append('<h2>'+inline(line[3:])+'</h2>');i+=1;continue
    if line.startswith('|'):
        rows=[]
        while i<len(lines) and lines[i].startswith('|'):
            cells=[c.strip() for c in lines[i].strip('|').split('|')]
            if not all(re.fullmatch(r':?-+:?',c) for c in cells):rows.append(cells)
            i+=1
        parts.append('<div class="scroll"><table><thead><tr>'+''.join('<th>'+inline(c)+'</th>' for c in rows[0])+'</tr></thead><tbody>')
        parts.extend('<tr>'+''.join('<td>'+inline(c)+'</td>' for c in r)+'</tr>' for r in rows[1:]);parts.append('</tbody></table></div>');continue
    parts.append('<p>'+inline(line)+'</p>');i+=1
file_rows=''.join('<tr><td>'+html.escape(r['path'])+'</td><td>'+('修改测试（单列）' if r.get('change_type')=='modified_test' else '新增' if r['before_sha256'] is None else '修改')+'</td></tr>' for r in changed)
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 · T1 / T2 / T3 代码更新与验收</title><style>*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:#18304d;font:15px/1.85 "Segoe UI","Microsoft YaHei",sans-serif}header{padding:18px 5%;background:#fff;border-bottom:1px solid #dce4ee;display:flex;justify-content:space-between;gap:12px}main{max-width:1250px;margin:24px auto;background:white;border:1px solid #dce4ee;border-radius:12px;padding:32px;min-width:0}h1{font-size:28px}h2{font-size:21px;margin-top:36px;border-bottom:1px solid #dce4ee;padding-bottom:8px}p{overflow-wrap:anywhere}.badge{color:#965e00;background:#fff3d6;padding:2px 12px;border-radius:5px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #dce4ee;padding:10px;vertical-align:top;overflow-wrap:anywhere}th{background:#edf3fb;text-align:left}tr:nth-child(even){background:#fafcff}.flow{display:flex;flex-wrap:wrap;gap:12px;margin:24px 0}.stage{flex:1;min-width:180px;background:#f5f8fd;border-top:4px solid #2563eb;padding:16px;border-radius:6px}.stage:nth-child(2){border-color:#895cda}.stage:nth-child(3){border-color:#d9941d}.stage b{display:block}.stage small{color:#65758b}@media(max-width:680px){main{padding:18px;margin:12px}header{padding:12px}.badge{font-size:12px}h1{font-size:23px}}</style><header><b>ERJI / P39</b><span class="badge">代码完成 · 尚未生产激活</span></header><main>'''+''.join(parts[:2])+'''<div class="flow"><div class="stage"><b>T1 · 发现 / 唤醒</b><small>异动事实、人工关注、候选跨界</small></div><div class="stage"><b>T2 · 有效需求分析</b><small>趋势 / 结构 → 结果与来源 → Plan</small></div><div class="stage"><b>T3 · 当前买入核验</b><small>买入区、保护、报价 / 盘口 → 页面</small></div></div>'''+''.join(parts[2:])+'''<h2>8. 本轮代码文件</h2><div class="scroll"><table><thead><tr><th>文件</th><th>变更</th></tr></thead><tbody>'''+file_rows+'</tbody></table></div></main></html>'
for target in (OUT/'P39_T1_T2_T3代码更新与验收.html',ROOT/'apps/web/public/previews/P39_T1_T2_T3_acceptance.html'):
    target.write_text(page,'utf8')
print(json.dumps({'tests':tests,'changed_files':len(changed),'preservation':checks,'html_bytes':len(page.encode())},ensure_ascii=False))
