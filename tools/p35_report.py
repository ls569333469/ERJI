"""Generate the P35 delivery index from retained test and runtime evidence."""
from pathlib import Path
from zipfile import ZipFile
from datetime import datetime
from zoneinfo import ZoneInfo
import json, hashlib, html
import xml.etree.ElementTree as ET

root=Path(__file__).resolve().parents[1]
out=root/'docs/p35-validation'
project=root.parents[2]
now=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
read=lambda name:json.loads((out/name).read_text(encoding='utf8'))
runtime=read('after-runtime.json');before=read('before-runtime.json');live=read('live-browser.json');final=read('chart-final.json');matrix=read('real-chart-matrix.json')
suite=ET.parse(out/'tests.xml').getroot().find('testsuite')
tests={k:suite.attrib[k] for k in ('tests','failures','errors','skipped','time')}
assert int(tests['failures'])==int(tests['errors'])==0
assert not final['errors'] and not matrix['errors'] and not live['errors']
backup=project/'backups/p35-before-20261003-234558.zip'
with ZipFile(backup) as z:old={n:hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if not n.endswith('/')}
changes=[]
for parent in ['engine','services','apps/web/src','tests','tools']:
 for p in (root/parent).rglob('*'):
  if not p.is_file() or p.suffix not in {'.py','.ts','.tsx','.css','.json','.cjs','.ps1'} or '__pycache__' in p.parts:continue
  name=p.relative_to(root).as_posix();digest=hashlib.sha256(p.read_bytes()).hexdigest()
  if digest!=old.get(name):changes.append({'file':name,'kind':'modified' if name in old else 'added','before_sha256':old.get(name),'after_sha256':digest})
manifest={'backup':str(backup),'changes':changes}
(out/'change-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
items=[
 ('P35-01','取消助手发布硬门槛','日常识别、计划、列表、图层不再受样本数/报告/审核人阻断；9个策略开关接入原设置页。保留真实触发、成本、时效和未经绩效验证说明。','engine/strategies/p33_delivery.py; engine/strategies/p33_runtime.py; engine/pd2/chart_contract.py; apps/web/src/components/system-pages.tsx'),
 ('P35-02','多周期与候选调度','周日背景缓存、已收盘量价异动理由、候选与活动事件优先；按实际模板获取1H依赖；保持普通市场公平发现。背景不直接产生买入。','engine/pd2/p35_schedule.py; engine/repository.py; services/worker/main.py'),
 ('P35-03','近期窗口与冷历史分流','近期依赖独立于全历史；冷历史最多共享预算15%、实时积压时让出；校验官方ZIP与CHECKSUM、失败回退原生接口；保留断点和真实缺口，不提高共享配额。','engine/pd2/history_backfill.py; engine/pd2/p35_archive.py; engine/pd2/request_budget.py'),
 ('P35-04','OI、市值与费率口径','候选优先修复OI/费率，币安经核实主合约固定口径；不偷换辅助合约金额，费率区分结算与观测时间。供应商排队时仍明确过期，不填零或改时间。','engine/pd2/collection_schedule.py; engine/pd2/derivatives.py; engine/strategies/p33_runtime.py'),
 ('P35-05','形态到列表及持续追踪','新9策略候选进入A表，给出周期、价位、量比；首报独立冻结、等待持续追踪、终态不复活。盘口到达及时重评并更新投影；扫描按25市场分段提交，事件锁延后获取，避免积压与连接池互等；个人仓位仅影响测算。','engine/strategies/p35_candidates.py; engine/strategies/p33_delivery.py; engine/signal_service.py; engine/repository.py; engine/pd2/service.py; services/worker/main.py'),
 ('P35-06','独立模板与历史案例','10个独立模板接口及可读页面，显示运行版本/依赖/触发/保护/目标/图层；案例列出差异，最多3个，不强行匹配。按方案保留无百分比，未另加未经确认的评分算法。','engine/strategies/p35_templates.py; engine/strategies/oi_paths.py; services/api/main.py; apps/web/src/components/case-evidence.tsx'),
 ('P35-07','轻量读取与运行原因','GET列表仅查本地投影；复用筛选ID、合并统计，减少重复JSON/排名查询；原运行页增加容量、端点权重、队列、冷历史和可重叠阻断原因。','engine/pd2/signal_projection.py; engine/pd2/p35_capacity.py; apps/web/src/components/operations-dashboard.tsx'),
 ('P35-08','逐策略K线与设置','只画匹配策略；原形成段与右延伸分开；短标签、避让、长文字留白、深浅主题；周期来源、用户显式偏好、最多3个历史结构、稳定对象增量更新、真实行情时间。','apps/web/src/components/chart.tsx; apps/web/src/lib/backend-studies.ts; engine/pd2/chart_contract.py; services/api/main.py'),
]
summary={'written_at_beijing':now,'code_status':'P35-01 through P35-08 implemented','checks':{'pytest':tests,'next_build':'passed including TypeScript','browser_errors':[]},'runtime':runtime['list_summary'],'scope':'local code and local API/live/jobs reload only','external_and_accumulation_conditions':'OI freshness, queued history, provider credentials/identity evidence, natural ATH samples; not represented as complete data'}
(out/'delivery-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
title='P35 代码更新与运行记录 · 2026-10-04'
e=html.escape
def link(file,label=None):return '<a href="'+e(file,quote=True)+'">'+e(label or file)+'</a>'
table='<table><thead><tr><th>项目</th><th>已完成内容</th><th>主要代码</th></tr></thead><tbody>'+''.join('<tr><td><b>'+a+'</b><br>'+b+'</td><td>'+c+'</td><td>'+''.join(link('../../'+p.strip(),p.strip())+'<br>' for p in d.split(';'))+'</td></tr>' for a,b,c,d in items)+'</tbody></table>'
current=runtime['list_summary'];prior=before['signals']['data'];queues=runtime['operations']['data']['collection_capacity']['queues'];binance=next(q for q in queues if q['source']=='binance_family')
time_label=runtime['beijing'][:19].replace('T',' ')
stats=f"采样时间：北京时间 {time_label}。正式机会由更新前的 {len(prior['rows'])} 条变为 {current['rows']} 条；当前现在可以买 {current['opportunity_counts']['l1']} 条、等待机会 {current['opportunity_counts']['l2']} 条、小仓提前 {current['opportunity_counts']['l3']} 条。候选增加不代表可以买。"
strategy_text='、'.join(k+' '+str(v) for k,v in current['strategies'].items())
data_text=f"563个币安合约中，6分钟内OI为 {runtime['binance_oi']['fresh_6m']} 个；当前列表OI/流通市值 {current['oi_cap_available']}/{current['rows']} 条有值。结算费率 {current['funding_present']} 条有历史值，其中当前观测新鲜 {current['funding_fresh']} 条。未达到持续全覆盖，旧费率有值不等于新鲜。"
queue_text=f"币安本地共享预算上限 {binance['budget']} weight/分钟；当前窗口登记使用 {binance['used']}、排队 {binance['queued']}、最长等待 {binance['oldest_wait_seconds']} 秒（包含等待中的冷历史）。服务器返回权重和退避也会约束可用额度；本地剩余额度不等于可以绕过退避。"
latencies=runtime.get('final_http_latencies_ms',[])
perf=f"实际前端代理列表请求：前一轮 {', '.join(map(str,live['latencies']))} ms；最终加载后 {', '.join(map(str,latencies)) or str(runtime['signals']['ms'])} ms。早先并发核对出现2351 ms，已减少重复查询；保留此记录，不拿单次结果承诺所有负载下持续低于2秒。"
freeze=runtime['first_event_freeze']
evidence='<ul>'+''.join('<li>'+link(f,l)+'</li>' for f,l in [('tests.xml','本轮完整测试原始结果'),('test-isolation.json','独立测试数据库及只读采样说明'),('before-runtime.json','更新前运行快照'),('after-runtime.json','最终加载后运行与请求证据'),('real-chart-matrix.json','8类真实历史图表坐标检查'),('live-browser.json','当前箱体/前高、设置、模板及请求检查'),('chart-final.json','周期偏好、缩放、断网恢复与ATH构造检查'),('change-manifest.json','逐文件前后SHA256与备份路径')])+'</ul>'
before_image=final['same_event']['before'];same_id=final['same_event']['snapshot_id']
gallery='<div class="comparison"><figure><img src="'+before_image+'"><figcaption>P34：同一真实冻结事件</figcaption></figure><figure><img src="same-event-after.png"><figcaption>P35：同一事件；旧首报文案保留，不代表当前发布规则</figcaption></figure></div>'
body=f'''<header><small>ERJI · P35-01—P35-08</small><h1>{title}</h1><p>八组代码更新已完成，本地 API / live / jobs 已加载；前端保留 A 版布局。</p><p>{e(now)} · 北京时间</p></header><main>
<section><h2>本次交付</h2><p>按P35 v1.2及第9节实施。取消助手自设报告硬门槛，保持真实行情、条件、源时间和原始首报。代码完成不等于所有外部数据已齐或策略收益已验证。</p><p>{link('../../../../../docs/P35_ERJI_用户主导审核与有效信号链路完善方案.html','返回P35主方案')} · {link('http://127.0.0.1:18770/signals','打开本地机会列表')} · {link('http://127.0.0.1:18770/v1/strategy-templates/recent_high/document','查看独立模板示例')}</p>{table}</section>
<section><h2>必要检查</h2><p>Python：{tests['tests']} 项通过，失败 {tests['failures']}、错误 {tests['errors']}；Next生产构建及TypeScript通过。保留现有依赖弃用提示和仓库根目录警告，未扩大范围处理。</p><p>8类真实冻结策略图 + 当前箱体/前高图已检查，浏览器未捕获页面异常。日线显式选择刷新后保留、默认不混画4H结构；短时断网恢复通过。缩放检查中对象数15/15/15，没有累积复制；仅证明本轮短时检查，不冒充多日挂机结果。</p><p>ATH暂无可用于本轮展示的真实触发，已用明确标注的浏览器构造图层检查，复用真实历史K线和价位，不写数据库、不伪造ATH信号。其他ATH历史完整性判断由自动测试覆盖。</p><p>核对 {freeze['checked']} 个留存事件的首次条件与原计划，发现变更 {len(freeze['mismatches'])} 个；最近10分钟新增 {runtime['snapshots_last_10m']} 个追踪快照，当前新策略异常记录 {len(runtime['recent_strategy_errors'])} 个。</p><p>{e(perf)}</p></section>
<section><h2>真实运行数据与尚存条件</h2><p>{e(stats)}</p><p>本次列表策略分布：{e(strategy_text)}。行情推进后会变化。</p><p>{e(data_text)}</p><p>{e(queue_text)}</p><p>持久历史游标：{e(json.dumps(runtime['history_statuses'],ensure_ascii=False))}。这是已建游标状态，不能把complete数量当作全市场全部历史已齐。冷历史在剩余配额中继续推进；本轮没有产生已验证归档下载样例时，仅报告解析/校验/回退测试通过。</p><p>ByKaranteli订阅/密钥、供应商不可返回历史、缺乏准确身份依据的资产、自然ATH与完整生命周期样例属于外部或数据条件。既有真实买入门槛未放宽；没有当前买入信号时不强行生成。OI持续覆盖仍受采集容量约束，不能写成“全部数据稳定可用”。</p></section>
<section><h2>同一事件的图表前后对比</h2><p>事件：<code>{same_id}</code>，SOLUSDT趋势回踩。两张图取同一冻结事件；旧版审核文案作为原始证据保留并标明。</p>{gallery}<details><summary>当前图表、主题与ATH构造图</summary><img src="live-box.png"><img src="dark-box.png"><img src="ath-drawing-fixture.png"><p>最后一张仅为ATH绘图检查，图片顶部已标明“构造样例”。</p></details></section>
<section><h2>本轮发现并处理的问题</h2><p>约01:02起的一次额外回归沿用了旧测试的运行库事务，出现锁等待，已停止并回滚该测试连接。进一步运行核对发现扫描提前持有全局事件锁时还会与连接池互等；已把信号事件发布移至扫描事务末尾，同进程扫描先串行，每25市场提交一次，积压不再合成大事务。最后完整回归在独立临时数据库中通过，随后清理测试库。该时间段的采集延迟属于本轮检查及修复影响，不能误写成自然市场缺数据或交易所故障。</p></section>
<section><h2>文件与恢复依据</h2><p>代码变更清单共 {len(changes)} 个文件（含测试及交付工具）。更新前备份：<code>{e(str(backup))}</code>。原始数据和旧证据没有覆盖；备份用于定位与逐项恢复代码，不自动回退数据库或用户数据。</p>{evidence}<p>本轮只实施P35：未新增策略种类、审批系统、移动端改版、自动交易、通知发送或服务器部署。后续专项审核按用户要求单独进行。</p></section></main>'''
css='body{margin:0;background:#f3f6f9;color:#213649;font:15px/1.8 "Microsoft YaHei",sans-serif}header{padding:32px max(28px,calc((100vw - 1280px)/2));background:#183c52;color:#fff}main{max-width:1280px;margin:auto;padding:24px}section{background:white;border:1px solid #d9e3ec;border-radius:12px;padding:24px;margin-bottom:22px}h1{font-size:29px}h2{font-size:21px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #d9e3ec;padding:12px;text-align:left;vertical-align:top}th{background:#edf3f7}td:first-child{width:170px}td:last-child{max-width:370px;overflow-wrap:anywhere}a{color:#076e9b}code{overflow-wrap:anywhere}.comparison{display:grid;grid-template-columns:1fr 1fr;gap:12px}figure{margin:0}img{width:100%;height:auto;border:1px solid #d9e3ec}figcaption{padding:8px}details{margin:15px 0}summary{cursor:pointer}li{margin:6px 0}'
stem='P35_代码更新与运行记录_2026-10-04'
(out/(stem+'.html')).write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'+title+'</title><style>'+css+'</style><body>'+body+'</body></html>',encoding='utf8')
md=f'# {title}\n\n更新时间：{now}\n\nP35八组代码已完成，本地服务已加载。\n\n'
for a,b,c,d in items:md+=f'## {a} {b}\n\n{c}\n\n代码：{d}\n\n'
md+=f'## 检查和运行限制\n\n{tests["tests"]}项测试通过；Next构建和TypeScript通过；浏览器检查无页面异常。\n\n{stats}\n\n{data_text}\n\n{queue_text}\n\n{perf}\n\n完整图表与证据见[{title}]({stem}.html)。\n'
(out/(stem+'.md')).write_text(md,encoding='utf8')

# Keep the original requirements and historical baselines, update only status and delivery index.
plan=project/'docs/P35_ERJI_用户主导审核与有效信号链路完善方案'
record='../work/production-fix-20260929/src/docs/p35-validation/'+stem+'.html'
replacements={
 'v1.2 · 2026-10-03 · 北京时间 · 方案已整理，产品代码尚未按 P35 修改':'v1.3 · 2026-10-04 · 北京时间 · P35-01—P35-08 代码已完成，本地服务已加载；数据覆盖见实施记录',
 '方案政策已更新；代码限制仍在，待实施':'代码已取消无用报告阻断，保留真实条件',
 '调度修改待实施':'分层调度与冷历史分流已实现',
 '本页列出八项收尾工作':'八项代码完成；运行证据见第10节',
 '核对后的未完成工作：八项':'八项实施清单（代码已完成）',
 '本表是可执行工作分组，不是估计工时。历史数据取样会变化；本次未重新跑全量测试，也不把以前通过的测试当作本轮通过。':'本表保留实施前基础及原交付要求；八组代码已完成，逐项修改和本轮检查见第10节。下方历史取样为方案制定时基线，不代表最新运行状态。',
 '仍未完成的交付':'已完成交付（原要求）',
 '本次只新增 P35 的 Markdown/HTML 和参数出处附件，并在旧入口增加指向 P35 的说明。未更改产品代码、数据库策略开关、采集预算或运行服务。':'方案制定阶段仅新增文档和参数附件。随后按用户授权实施P35全部八组代码，并让本地服务加载；未提高总配额，未启用自动交易或通知发送。',
 '此次仅更新方案，以下要求尚未视为代码完成。':'本节已随P35-08实施，真实图表与构造检查样例见第10节。',
 '“方案废止旧门槛”不等于“运行代码已解除门槛”。':'旧报告门槛已从运行链路解除；真实触发与数据条件继续保留。',
}
for ext in ('.md','.html'):
 p=plan.with_suffix(ext);content=p.read_text(encoding='utf8')
 for old_text,new_text in replacements.items():content=content.replace(old_text,new_text)
 if ext=='.md':
  if '\n## 10. ' in content:content=content.split('\n## 10. ')[0]
  content+=f'\n## 10. 代码实施记录 · 2026-10-04\n\nP35-01至P35-08代码已完成并加载到本地。{tests["tests"]}项测试通过，前端构建及TypeScript通过；真实图表、同事件前后对比和ATH构造样例已留存。\n\n[{title}]({record})\n\nOI/市值覆盖、供应商缺口和历史积累按记录中的实时采样披露，不把代码完成等同于全部数据已齐；不新增审核流程。\n'
 else:
  content=content.replace('<a href="#s3">八项实施清单（代码已完成）</a>','<a href="#s3">八项实施清单</a>')
  if '<a href="#s10">' not in content:content=content.replace('</nav>','<a href="#s10">代码实施记录</a></nav>')
  section=f'<section id="s10"><h2>10. 代码实施记录 · 2026-10-04</h2><p>P35-01至P35-08代码已完成并加载到本地。{tests["tests"]}项测试通过，前端构建及TypeScript通过。</p><p><a href="{record}">{title}：逐项代码、数据缺口、同事件前后对比及检查证据</a></p><p>代码完成不等于全部OI/市值与历史数据已齐；当前覆盖按实施记录披露。未新增审核流程。</p></section>'
  if '<section id="s10">' in content:content=content[:content.index('<section id="s10">')]+content[content.index('</main>',content.index('<section id="s10">')):]
  content=content.replace('</main>',section+'</main>')
 p.write_text(content,encoding='utf8')
print(json.dumps({'report':str(out/(stem+'.html')),'changed_files':len(changes),'tests':tests},ensure_ascii=False))
