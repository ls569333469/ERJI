"""Record verified remaining implementation; preserve earlier audit history."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json,html,re,hashlib,xml.etree.ElementTree as ET

root=Path(__file__).resolve().parents[1]
out=root/'docs/p39-validation';plans=Path('D:/Axiangmu/erji/docs')
now=datetime.now(timezone(timedelta(hours=8))).isoformat()
tests=ET.parse(out/'remaining-final-tests.xml').getroot()
count=sum(int(s.get('tests','0')) for s in tests.findall('testsuite'))
assert count==422 and all(int(s.get('failures','0'))==0 and int(s.get('errors','0'))==0 for s in tests.findall('testsuite'))
browser=json.loads((out/'frontend-layout-browser-check-20261006.json').read_text(encoding='utf8'))
assert browser['status']=='passed' and len(browser['checks'])==37
live=json.loads((out/'remaining-live-check-20261006.json').read_text(encoding='utf8'))
assert live['front_loaded'] and not live['errors'] and live['observation_rows']==10
report_name='P39_剩余代码更新与验收记录_2026-10-06'
changes={
 'P39-N01':('P33快速复核汇率传递',
 '复核时从有效本地能力记录读取quote_usd_rate；不继承旧快照汇率。同步当前qualification，资格截止同时受报价15秒、盘口10秒和原计划到期约束。',
 ['engine/p38/p33_current.py'],
 '真实资格计算回归：有效汇率可产生participate并到达投影/API；缺失或过期汇率仍暂停。冻结计划和旧快照不改。'),
 'P39-N02':('观察名额与结构任务一致性',
 '只用趋势时钟轮换会使尚未完成的结构任务退出名额。名额现在等待已入队趋势/结构完成后公平轮换；两类任务独立按输入版本提交。未准入的已领取任务显式退役，不记成功次数；同输入可再准入。结构完成记录单独更新时间和市场准入状态，任务成功不冒充市场已分析。',
 ['engine/p37/worker.py','engine/p38/jobs.py','engine/p37/contracts.py','apps/web/src/components/observation-board.tsx'],
 '隔离验证趋势先完成时结构仍有名额；完成后其它资产可进入；无名额退役不增加完成统计，同输入重入可执行。生产BAND/API3/RLC结构更新时间需激活后核对。'),
 'P39-N03':('买入判断至投影的秒级时效',
 '结构、入场、保护结果在同一市场执行栅栏内直接发布投影，省去第二条队列等待。独立投影按最新决策快照版本及30秒修复时钟对账，不再随每个行情tick重做。快复核一次查各计划最新快照，同市场P33共享K线读取；入场/保护包含1D/1W依赖和提交栅栏。行情tick不取消闭合结构计划计算；原始窗口/config变化仍拒绝旧提交。',
 ['engine/p38/jobs.py','engine/pd2/service.py'],
 '真实entry执行器完成后CurrentSignal和安全API可见participate，无单独projection任务；10秒截止后撤回。另验证新决策能改投影版本、仅行情tick不改变版本，报价跳动不取消结构计划。生产吞吐、队列p95、数据库负载和逐币延迟尚未实测，不保证所有市场都能在10秒内完成。'),
 'P39-N04':('历史可知时间、数据缺口及代表市场',
 '保留received_at≤原触发时间的因果限制。现已补齐但原触发前未收到的数据标为history_not_known_before_trigger；与history_insufficient、candle_gap、closed_candles_stale分开，不伪称源日线过期、不回填历史买入。观察主行与调度共用同一原生市场代表排序，关联市场保留，计划执行市场不替换。',
 ['engine/strategies/p33.py','engine/p37/selection.py','engine/p37/worker.py','engine/p38/observation_query.py','engine/p37/contracts.py'],
 '晚收到历史不能生成原触发；未来received_at仍排除；真实缺口和闭合过期分别回归。置顶现货比更新合约优先时，页面和selector一致且保留合约关联事件。真实数据缺口必须由采集补齐后新触发验证。'),
 'P39-UI01':('观察折叠和计划迁移',
 '沿用此前已实施默认10条、展开全部/收回10条、整表折叠及刷新保持，关联计划仅在买入页共享一次加载。新增主行分别显示趋势和结构任务状态，保留所有主要数据。',
 ['apps/web/src/components/observation-board.tsx'],
 '本轮浏览器再次验证10/12条切换、整表折叠、刷新保持和分页同快照；真实观察页显示10条。'),
 'P39-UI02':('买入机会双层级',
 '顶部L1现在可以买始终保留，即使0条；空态属于L1。下方金色L2关联计划·等待条件，两区可折叠并保持刷新状态，共用搜索、分页、筛选和SSE。participate/trial只合并为可买列表，仓位方式仍属于信号策略。原八列、OpportunityRow及原详情/K线实现保持。',
 ['apps/web/src/components/opportunity-board.tsx','apps/web/src/app/opportunity-board.css'],
 '浏览器验证0条L1不消失、L2折叠/刷新/展开、一次共享请求、有效期退出及原详情snapshot_id请求。真实页面L1为0、L2为3、无渲染异常；该数值来自仍运行旧后端，不用于证明后台已恢复。')
}
state_path=out/'p39-current-followup-status.json'
state=json.loads(state_path.read_text(encoding='utf8'))
for item in state['items']:
    title,implementation,files,verification=changes[item['id']]
    item.update(status='implemented_verified_activation_pending' if item['id'].startswith('P39-N') else 'implemented_browser_verified',
        implementation=implementation,files=files,verification=verification,
        production_recovery_verified=False if item['id'].startswith('P39-N') else None)
state.update(updated_at_bjt=now,overall_status='all_confirmed_code_scope_complete_backend_activation_and_runtime_acceptance_pending',
    completed_at=now,report_path=str(out/(report_name+'.html')),backend_new_code_activated=False,monitor_status='PAUSED',
    verification={'backend_full_passed':count,'browser_checks_passed':len(browser['checks']),'typecheck':'passed','build':'passed',
    'production_database_write':False,'exchange_batch_calls':False,'service_restart':False},
    limits='已确定代码项全部实施；原31项历史和未决定的产品/存储范围保留。真实队列、采集缺口、逐币恢复待后台激活后验收。')
state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf8')
md=f'''# P39 剩余代码更新与验收记录

更新时间：{now}（北京时间）。用户要求完成P39剩余所有代码更新；本轮完成已确定的N01–N04与UI01–UI02。原31项审计和此前实施证据保留，原待决定事项不冒充已实施。

完整隔离数据库回归 **{count} passed / 0 failed / 0 skipped**；前端类型检查、生产构建通过；正式构建浏览器 **{len(browser['checks'])}项通过**。专项用例包含在完整回归中，数量不相加。数据库使用测试自有临时PostgreSQL，零生产复制/写入、无交易所批量请求。

前端开发服务已热加载：本地买入页明确显示L1 0条、L2 3条，观察默认10条，无pageerror。**后台API、live/jobs/streams仍是旧激活批次20261006T055811-7c29b0a6，本轮未重启；新增后端代码尚未加载。** 因旧API不含新结构状态，当前观察主行仍显示“结构等待分析”，不能据此判断新逻辑恢复失败。budget/web原注册批次保留。监控持续暂停。

'''
for ident,(title,implementation,files,verification) in changes.items():
    md+=f'## {ident} · {title}\n\n{implementation}\n\n代码：'+', '.join(files)+f'。\n\n验收：{verification}\n\n'
md+='''## 激活后的验收与保留边界

下一步加载后台新代码后，核对新run日志、50个名额的结构完成增量、waiting/leased/failed真实年龄、入场到投影端到端延迟，以及BAND/API3/RLC原生市场的新结构结果。只看任务complete和累计数不足以证明恢复。

买入资格仍受真实报价、盘口、原入场区和冻结到期时间约束。超出区间/过期撤可买，符合规则的关联计划继续保留；不能靠延长时效或降低门槛制造信号。无真实合格买入也不能单独证明链路正常。

其他来源官方额度、raw历史保留/压缩/迁移和增长有界、是否停止旧策略新建、BTC unknown可见策略、保护采集开关例外仍是原未决定项。真实容器烟测、辅助表单长期使用和稳定运行p95覆盖仍未由本轮代码测试代替。

原OpportunityRow及详情包装与修改前逐字对比一致；OpportunityDetail、Chart、资产详情未修改。K线因果判断保留，未修改图表界面和冻结历史。

## 原始证据

- remaining-final-tests.xml / remaining-final-test-isolation.json：422项完整回归及隔离实例。
- tests/test_p39_remaining.py：10项新增回归，属于完整回归。
- frontend-layout-browser-check-20261006.json：37项构建页面检查。
- remaining-live-check-20261006.json / remaining-live-buy-20261006.png / remaining-live-observations-20261006.png：真实页面只读证据。
- remaining-detail-preservation.json / remaining-before-20261006：原行详情不变及修改前文件。
- p39-current-followup-status.json：最新逐项实施与激活边界。
'''
(out/(report_name+'.md')).write_text(md,encoding='utf8')
style='body{font:15px/1.8 system-ui,"Microsoft YaHei";background:#f5f7fb;color:#243348;margin:0}main{max-width:1120px;margin:32px auto;background:white;padding:32px;border:1px solid #dce3ef}h1{font-size:26px}h2{font-size:19px;border-top:1px solid #e3e8f0;padding-top:22px}p{overflow-wrap:anywhere}a{color:#3158b8}.badge{background:#e7f5ed;color:#187050;padding:8px 12px}.note{background:#fff8e8;padding:14px;border-left:4px solid #cca549}'
report_html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 剩余代码更新与验收</title><style>'+style+'</style><main>'
for block in md.split('\n\n'):
    if block.startswith('# '):report_html+='<h1>'+html.escape(block[2:])+'</h1>'
    elif block.startswith('## '):report_html+='<h2>'+html.escape(block[3:])+'</h2>'
    else:report_html+='<p>'+html.escape(block).replace('\n','<br>')+'</p>'
report_html+='</main></html>'
(out/(report_name+'.html')).write_text(report_html,encoding='utf8')
table='\n'.join(f'| {ident} · {entry[0]} | 代码/隔离验收完成'+('，后台待激活' if ident.startswith('P39-N') else '，页面已加载')+' | '+entry[1]+' |' for ident,entry in changes.items())
section_md=f'''<!-- P39-CURRENT-FOLLOWUP -->
## 当前总状态与剩余项收尾（{now}，北京时间）

**P39已确定的剩余代码更新全部完成；后台激活和真实运行验收尚未完成。** 原31项已确定工程范围保留。今天新增N01–N04、确认的UI01–UI02均已实施，完整隔离回归422项通过，浏览器37项通过，typecheck/build通过。专项与全套重叠，不相加。

| 项目 | 最新状态 | 已实施范围 |
|---|---|---|
{table}

前端开发服务已热加载两个层级；后台仍是20261006T055811-7c29b0a6批次，本轮未重启，不能将旧后台买入为空当作新代码运行结论。监控保持暂停，原详情与K线保持。

日线“原触发前未收到”与当前源缺口分开；原received_at因果限制不取消，补录历史不补造过去的可买。入场资格继续受报价15秒/盘口10秒和冻结原计划约束。

原延期/待决定：其他来源官方额度、raw保留/压缩迁移和增长有界、停止旧策略新建、BTC unknown展示、保护采集开关例外。真实队列p95、持续采集和逐币恢复需激活后验收。

[本轮代码更新与验收记录](../work/production-fix-20260929/src/docs/p39-validation/{report_name}.html) · [最新状态JSON](../work/production-fix-20260929/src/docs/p39-validation/p39-current-followup-status.json)
<!-- /P39-CURRENT-FOLLOWUP -->'''
plan_md=plans/'P39_ERJI_主链路收敛与数据可靠性整改方案.md'
text=plan_md.read_text(encoding='utf8');assert '<!-- P39-CURRENT-FOLLOWUP -->' in text
plan_md.write_text(re.sub(r'<!-- P39-CURRENT-FOLLOWUP -->.*?<!-- /P39-CURRENT-FOLLOWUP -->',lambda _:section_md,text,flags=re.S),encoding='utf8')
section_html=f'<!-- P39-CURRENT-FOLLOWUP --><section id="p39-current-followup" class="evidence"><h2>最新总状态 · 已确定代码范围完成，后台待激活</h2><p>北京时间：{now}。原31项已确定工程实施保留；新增N01–N04与UI01–UI02已完成。完整隔离回归422项通过，浏览器37项通过，类型检查与构建通过。</p><p><strong>前端已热加载，后台新增代码尚未加载。</strong> 本轮没有重启，监控继续暂停。隔离验收不等于所有运行数据已恢复。</p>'
for ident,(title,implementation,files,verification) in changes.items():
    section_html+=f'<details class="issue"><summary><strong>{ident} · {html.escape(title)}</strong><span class="tag">代码/隔离验收完成'+(' · 后台待激活' if ident.startswith('P39-N') else ' · 页面已验')+f'</span></summary><p>{html.escape(implementation)}</p><p>代码：{html.escape(", ".join(files))}</p><p>验收：{html.escape(verification)}</p></details>'
section_html+=f'<p>原待决定的额度、raw留存与增长有界、旧策略新建、BTC unknown和保护采集例外保留；真实队列p95、持续采集和逐币恢复仍待运行验收。</p><p><a href="P39_remaining_acceptance.html">本轮剩余代码更新与验收记录</a> · <a href="P39_买入机会双层级预览.html">已确认预览</a></p></section><!-- /P39-CURRENT-FOLLOWUP -->'
for filename in ['P39_plan_latest.html','P39_ERJI_主链路收敛与数据可靠性整改方案.html']:
    p=plans/filename;text=p.read_text(encoding='utf8');assert '<!-- P39-CURRENT-FOLLOWUP -->' in text
    p.write_text(re.sub(r'<!-- P39-CURRENT-FOLLOWUP -->.*?<!-- /P39-CURRENT-FOLLOWUP -->',lambda _:section_html,text,flags=re.S),encoding='utf8')
(plans/'P39_remaining_acceptance.html').write_text(report_html,encoding='utf8')
public=root/'apps/web/public/previews';public.mkdir(parents=True,exist_ok=True)
for filename in ['P39_plan_latest.html','P39_remaining_acceptance.html']:(public/filename).write_bytes((plans/filename).read_bytes())
# Clearly label the old implementation report as a historical stage without
# rewriting any earlier measurements or test results.
old_md=out/'P39_代码更新与验收记录.md';old_text=old_md.read_text(encoding='utf8')
marker='<!-- P39-REMAINING-LATEST -->'
if marker not in old_text:
    old_md.write_text(old_text.replace('# P39 代码更新与验收记录',f'# P39 代码更新与验收记录\n\n{marker}\n最新更新：{now}，剩余代码范围已完成；见[{report_name}]({report_name}.html)。下文端口未启动等为原记录时刻，不代表当前。新增后端本轮仍待激活。\n<!-- /P39-REMAINING-LATEST -->',1),encoding='utf8')
old_html=out/'P39_代码更新与验收记录.html';old_text=old_html.read_text(encoding='utf8')
if marker not in old_text:
    pos=old_text.find('>',old_text.find('<body'))+1
    note=f'{marker}<p style="padding:14px;background:#fff8e8">最新收尾：剩余代码范围已完成，422项回归/37项浏览器检查通过。<a href="{report_name}.html">本轮验收记录</a>。下文未启动等是历史时刻；新增后端本轮仍待激活。</p><!-- /P39-REMAINING-LATEST -->'
    old_html.write_text(old_text[:pos]+note+old_text[pos:],encoding='utf8')
changed=['engine/p38/p33_current.py','engine/p38/jobs.py','engine/p37/worker.py','engine/p37/selection.py','engine/p38/observation_query.py','engine/strategies/p33.py','engine/pd2/service.py','engine/p37/contracts.py','apps/web/src/components/opportunity-board.tsx','apps/web/src/components/observation-board.tsx','apps/web/src/app/opportunity-board.css','apps/web/src/lib/api.generated.ts','packages/contracts/openapi.json','tests/test_p39_remaining.py','tests/test_p39_reliability.py','tests/test_p39_startup_fix.py','tests/test_p38_pipeline.py','tools/p39_ui_check.cjs']
(out/'remaining-code-manifest.json').write_text(json.dumps({'checked_at_bjt':now,'sha256':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in changed}},ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'report':str(out/(report_name+'.html')),'backend_tests':count,'browser_checks':len(browser['checks']),'backend_activated':False},ensure_ascii=False))
