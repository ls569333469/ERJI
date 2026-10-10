"""Attach current implementation evidence without replacing P39 audit originals."""
from pathlib import Path
import json,html,re,xml.etree.ElementTree as ET
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/p39-validation';PROJECT=ROOT.parents[2]
plan=json.loads((OUT/'p39-findings-and-plan.json').read_text(encoding='utf8'))
static=json.loads((OUT/'static-review.json').read_text(encoding='utf8'))
ui=json.loads((OUT/'frontend-browser-check.json').read_text(encoding='utf8'))
smoke=json.loads((OUT/'startup-smoke.json').read_text(encoding='utf-8-sig'))
suite=ET.parse(OUT/'tests.xml');cases=[c.attrib['name'] for c in suite.iter('testcase')]
assert len(cases)==397 and not list(suite.iter('failure')) and not list(suite.iter('error'))
assert len(ui['checks'])==24 and ui['status']=='passed'
stamp=datetime.now(timezone(timedelta(hours=8))).isoformat()
# Every issue remains an independent row, including deferred decisions.
M={
'A01':('现货REST使用明确的快照接收时钟、lastUpdateId和RTT；未知来源不授资格；仍执行报价15秒、盘口10秒校验。','engine/pd2/book_clock.py engine/pd2/execution.py engine/pd2/storage.py','spot_receive_provenance','重启后核对实际现货盘口资格；未实现或声称WS增量盘口序列。'),
'A02':('趋势/结构只由p37_active名额准入；所有观察档案保留；原管理计划的入场、保护、投影独立推进。','engine/p38/jobs.py engine/p37/worker.py','ordinary_market_cannot,managed_plan_keeps,deep_capacity','生产队列收敛及50个深度名额运行分布待验证。'),
'A03':('移除三秒伪版本；按市场和工作类型去重，以真实闭合事实、计划和配置形成输入版本；行情变化不重建结构。','engine/p38/jobs.py','task_dedup_and_quote','实际重复提交率与吞吐待运行采样。'),
'A04':('已冻结计划走轻量refresh_current；过期不授新买入，原保护继续；P33使用独立原事件适配，不套箱体规则。','engine/pd2/service.py engine/p38/p33_current.py','current_refresh_frozen,p33_current_cannot,p33_checkpoint_refresh','真实历史计划追踪与T3时延待验证。'),
'A05':('发现流改用443；写队列重连/取消先排空；保留关闭原因、最后接收时间及循环滞后指标。','engine/p37/worker.py engine/p38/loop_health.py engine/pd2/write_queue.py','cancelled_fact_writer,stream_composition','历史断线根因未被此次隔离测试证明；稳定性须连续运行验证。'),
'A06':('角色锁使用独立连接；DB准入8、池5+5；保护真实写线程与提交；记录准入、会话、提交及写队列等待p95。','engine/db.py engine/pd2/scheduling.py engine/pd2/write_queue.py','thread_cancel,lost_role,concurrent_candle','不能把提交耗时解释成纯磁盘fsync；生产性能改善幅度尚未实测。'),
'A07':('保留2160/5400/900/450等已核实预算；分工作类型收敛无效重算，补窗最新尾部优先，深度成功页1秒可续补。','engine/p37/worker.py engine/p38/jobs.py engine/pd2/p35_capacity.py','priority_has_aged,window_reservation','90%是权重上限，非强制利用率；OI刷新目标60/15/10秒不等于已全部达到。'),
'A08':('完成14个现代接口、7个旧来源的预算隔离审计；未知来源保守限额单独标记，不借用币安UM额度。','engine/pd2/p35_capacity.py engine/pd2/request_budget.py tools/p39_static_review.py','scopes_and_parameter_costs','其他来源官方限额未核实，因此未提高其预算；不宣称全部接口已90%。'),
'A09':('趋势、结构和原扫描器统一读取可重建的committed窗口；按市场/周期/revision区分缓存，最多256项，返回副本。','engine/p38/windows.py engine/p37/analysis.py engine/pd2/service.py engine/signal_service.py','committed_window_rebuilds,partial_aggregates','当前历史图表依然使用冻结快照；真实图表一致性待有数据运行核对。'),
'A10':('窗口不足时先修应到最新闭合尾部，再补根数和中间缺口；任务状态与缺根、尾部迟到分开。','engine/p37/worker.py engine/p38/candle_gaps.py services/api/observations.py','partial_aggregates,gap_fetch_fence','不承诺新上市市场能补出不存在的历史；全市场缺页统计待启动。'),
'A11':('CMC目录去掉listing_status，符号请求有界；400记录invalid_request而非永久unsupported；保留严格身份、供给与FX口径。','engine/pd2/providers.py engine/pd2/identity.py engine/pd2/storage.py','zero_oi_valid,batch_preserves_freshness','未调用CMC验证400恢复，也未新增无官方依据的身份映射；市值覆盖扩大待验证。'),
'A12':('到期为主状态，行情/K线/追踪缺口作为附加原因；既有失效及保护规则继续优先处理。','engine/pd2/decision.py engine/strategies/p33_lifecycle.py','expiry_precedes,missing_candle_interval,tracking_gap_pauses','历史暂停快照不会回写成当前状态；旧快照保留原证据。'),
'A13':('健康判断采用与入场相同的盘口effective_time；现货接收快照与交易所时间分别标识，源时间和分析时间分开。','engine/pd2/data_health.py engine/pd2/book_clock.py services/api/observations.py','spot_receive_provenance','源年龄显示已检查；真实来源新鲜度恢复待验证。'),
'A14':('BTC背景预计算一次；unknown保持未确认，覆盖、连续性和分类版本传递至API，阈值不改。','engine/p37/detector.py engine/p37/store.py','comovement_requires,background_precompute,spot_comovement','unknown是否进入主雷达保留原行为，属于未决定产品项。'),
'A15':('普通coverage/projection不再绕进全分析；研究按20计划有界轮转，热协调按native小行更新；管理计划保护独立。','engine/p38/jobs.py engine/pd2/outcomes.py engine/pd2/scheduling.py','ordinary_market_cannot,priority_has_aged,outcome_rechecks','新任务分型耗时、最老等待及累计完成数已可记录，生产公平性仍需采样。'),
'A16':('新raw事实不可覆盖、重复幂等；轮转协调拆小行；提供只读体积、raw引用和增长采样工具。','engine/repository.py engine/pd2/scheduling.py tools/p39_capacity_inventory.py','atomic_fact_contract','未删除旧raw、未做分区/压缩/迁移；磁盘增长有界尚未证实，保留期和容量预算待决定。'),
'A17':('保留旧箱体和P33规则版本；原计划按各自冻结规则保护，P33单独适配。','engine/p38/p33_current.py engine/pd2/service.py','p33_checkpoint_refresh,current_refresh_frozen','是否停止新建旧策略未获产品决定，本轮不改变策略范围。'),
'A18':('返回独立任务状态not_analyzed；现货OI为not_applicable；等待分析不冒充事实数据缺失。','services/api/observations.py engine/p37/store.py engine/p37/contracts.py','api_contract_and_config','真实候选币补齐及分析结果仍需运行验证。'),
'B01':('Candle原子条件upsert：received_at不倒退、closed不退回forming，量价修订递增并通知；raw不可变，资金费率按真实主键更新。','engine/repository.py','atomic_fact_contract,concurrent_candle_revision','隔离库含两线程真实并发；生产旧事实未重写。'),
'B02':('提交前检查任务owner、lease、desired版本及本类型真实输入；窗口revision和配置变化拒绝旧提交，行情不废弃闭合结构任务。','engine/p38/jobs.py engine/p37/analysis.py engine/p38/windows.py','actual_executor_fence,p37_analysis_rejects,old_scoped_snapshot,task_dedup','持续高速行情下的饥饿比例和p95尚未实测。'),
'B03':('统一Writer关闭排空，取消先等待真实写入；接受、提交、失败和pending分别统计；关闭时保留失败证据。','engine/pd2/write_queue.py engine/p37/worker.py engine/pd2/native_streams.py','cancelled_fact_writer','正常取消排空已验；突然断电仍依赖重启后闭合边界REST缺口恢复，不能承诺内存零丢失。'),
'B04':('取消不提前释放fence；晚到连接必须清理；线程提交携带guard；取消明确标记，续租失效拒绝提交。','engine/p38/lifecycle.py engine/p38/jobs.py engine/p38/candle_gaps.py','resource_acquisition_cancel,thread_cancel,gap_fetch_fence,jobs_running_version','进程硬退出/跨机器网络故障恢复未做生产故障注入。'),
'B05':('Compose新增唯一私网预算owner、共享只读凭证和健康依赖；容器不再请求自身loopback owner。','compose.yml engine/pd2/budget_owner.py','budget_owner','35项静态检查含拓扑；真实容器启动及跨容器额度合并尚未演练。'),
'B06':('SSE打开时仍30秒对账；请求合并和取消保持；每秒只能撤销过期买入资格，不能自行授予。','apps/web/src/lib/use-refresh.ts apps/web/src/components/opportunity-board.tsx','', '实际构建UI假时钟验证；没有向生产事件泵注入故障。'),
'B07':('追踪缺口依据普通/活跃/降频对应采样间隔判断，正常降频不作为实时断流证据；源延迟单独记录。','engine/p37/store.py','cooling_archive','页面保留已有追踪极值说明；不回填旧缺失观测样本。'),
'B08':('首根边界、内部连续性和尾部完整性分别验证；依赖revision变化后实际重评，不把缺历史永久当未成交。','engine/pd2/outcomes.py','outcome_rechecks_database,outcome_missing_first','隔离库补首根和尾部后均恢复重评；生产历史缺口未额外补采。'),
'B09':('同资产主市场选择使用事务锁；三次确认须不同市场证据快照；原Plan执行市场不被换掉。','engine/pd2/market_selection.py','market_selection','原20%+2bps+三次门槛保留；多进程高负载切换待运行观察。'),
'B10':('owner带run_id/pid；定期校验角色连接与持锁身份；丢锁取消工作，捕获线程原owner且拒绝旧提交，接任共享commit gate。','engine/pd2/scheduling.py engine/db.py services/worker/main.py','lost_role_prevents,worker_role_loss','仅终止隔离库连接；未终止任何生产会话。'),
'B11':('WS、Hyperliquid、历史补齐、外部补充和入场盘口均检查collector_enabled，不设置隐藏保护采集例外。','engine/pd2/native_streams.py services/worker/main.py engine/p38/jobs.py','collection_loop_cancellation','关闭后原计划记录保留但行情可能过期；保护采集例外仍需显式产品决定。'),
'B12':('启动预检、依赖ready、本run_id心跳与进程出生核验；增量清单和独立日志；失败仅清理本次且核实归属的进程树。','tools/start-local.ps1 tools/restart-local-workers.ps1 tools/local_start_helpers.ps1','', '6项mock烟测通过；无实际生产启停，真实首次启动未验。'),
'B13':('每条临发送重验有效期、开关、授权及lease；结果CAS，delivery_unknown不盲重发。','engine/pd2/notifications.py','notification_each_send','4类mock批内失效回归通过；零真实消息，通知未启用。'),
}
assert set(M)=={v['id'] for v in plan['issues']}
issue_rows=[]
for issue in plan['issues']:
    ident=issue['id'];done,files,patterns,boundary=M[ident]
    tests=sorted({name for name in cases if any(p in name for p in patterns.split(',') if p)})
    status='engineering_verified_runtime_pending' if tests else 'code_reviewed_runtime_pending'
    if ident=='A08':status='audited_conservative_boundary'
    if ident=='A16':status='partial_storage_policy_pending'
    if ident=='A17':status='product_decision_pending_compatibility_preserved'
    locations=[]
    for name in files.split():
        assert (ROOT/name).exists(),name
        locations.append(name)
    issue_rows.append({'id':ident,'title':issue['title'],'priority':issue['level'],'actions':[a['id'] for a in plan['actions'] if ident in a['issues']], 'status':status,'implementation':done,'files':locations,'passed_regressions':tests,'remaining_boundary':boundary,'production_recovery_verified':False})
F={
'F01':('layout_verified','24项页面检查、截图保留单层表头、图标、原八列及周期2×2；OpportunityRow正文沿用。'),
'F02':('layout_verified','市场观察关联等待计划懒加载；原八列、参与/估算保护、目标和详情入口完整。'),
'F03':('representative_states_verified_remaining_live','已验触发价单独存在、参与区为空、保护估算和目标缺失；预热和unsupported真实样本仍待运行。'),
'F04':('native_currency_verified_mapping_runtime_pending','原生OI报价币与缺USD换算已验；严格身份关联仍依赖真实映射，不宣称现货参考合约覆盖恢复。'),
'F05':('null_and_interval_verified','空资金费率不转0%，未知周期不转0H；保留原结算状态字段。'),
'F06':('format_verified','微小tick、非10次方tick、负涨跌、单点/重复/空目标通过；仍使用计划参考价口径。'),
'F07':('age_and_expiry_verified','源年龄补充在列表，报价及观察时间区分；一秒撤资格、30秒SSE对账验证，原展开详情/K线沿用。'),
'F08':('navigation_integrated_forms_runtime_pending','辅助四入口合并并保留原Sources、OperationsDashboard、SettingsPage及授权投递组件；全表单实际运行和真实图表不在24项fixture覆盖内。')}
contract=json.loads((OUT/'p39-final-frontend-contract.json').read_text(encoding='utf8'))
for gap in contract['gap_review']:gap.update(status=F[gap['id']][0],verification=F[gap['id']][1])
contract.update(status='implemented_isolated_acceptance_complete_live_pending',checked_at=stamp)
contract['final_review'].update(status='isolated_verified_with_explicit_limits',checks=ui['checks'],limits=[ui['detail_fixture_limit'],'辅助设置全表单未在真实生产运行验收'],production_fix_claim=False)
contract['documentation_sync']['final_acceptance']='engineering_verified_live_pending'
(OUT/'p39-final-frontend-contract.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf8')
report={'checked_at':stamp,'status':'authorized_engineering_complete_with_explicit_deferred_items','execution_order':'R01-R11 reliability and pipeline; frontend last','issues':issue_rows,'backend':{'passed':len(cases),'failed':0,'skipped':0,'isolated':True,'external_requests':0},'frontend':{'typecheck':'passed','production_build':'passed','refresh_loop_tests':5,'display_helpers':'passed','backend_studies_helpers':'passed','browser_checks':24,'production_api_access':False,'contracts':[{'id':k,'status':v[0],'evidence':v[1]} for k,v in F.items()]},'startup':{'mock_checks':len(smoke['checks']),'real_processes_started':0,'real_processes_stopped':0,'compose_static_checks':len(static['checks']),'container_runtime_smoke':False},'current_ports':static['production_ports'],'processes_loaded_new_code':False,'storage_retention_policy':'pending_user_decision','strategy_scope_change':'pending_user_decision; original strategies retained','performance_targets_verified':False,'notifications_sent':0,'source_hashes':static['source_hashes'],'original_findings_overwritten':False,'tracked_preimplementation_diff':'pre-implementation.patch (does not contain prior untracked files)'}
(OUT/'implementation-status.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
labels={'engineering_verified_runtime_pending':'代码/隔离回归通过 · 运行待验','code_reviewed_runtime_pending':'代码核对 · 运行待验','audited_conservative_boundary':'审计完成 · 未核实额度保守保留','partial_storage_policy_pending':'部分工程完成 · 留存/迁移待决定','product_decision_pending_compatibility_preserved':'兼容保留 · 产品选择待决定'}
intro=f'''# P39 代码更新与验收记录

记录时间：{stamp}（北京时间）。按原31项问题及R01–R11逐项落实，最后完成前端版块合并与数据保留。原始审计证据不覆盖。工程实现和隔离验收已完成；不能把它等同于31项生产故障全部恢复。

后端完整回归 **397 passed / 0 failed / 0 skipped**，仅测试自有PostgreSQL临时实例，零生产数据复制、零交易所请求。前端类型检查、生产构建、5项刷新循环测试与两组显示/指标辅助检查通过，构建页面24项浏览器检查通过。启动mock 6项、Compose/预算静态核对35项通过。

**当前18770/18771/18772/55432均未监听；生产没有加载新代码。** 本轮不启动/重启现有生产服务、不部署、不启用交易、不发送真实通知。隔离测试的Web与PostgreSQL已关闭。需启动后验证OI/资金费率新鲜度、CMC400与身份覆盖、WS连续性、队列吞吐及60分钟p95目标；构建成功不能代替运行数据恢复。

其他来源官方额度未核实，仍保持保守限制；raw仅完成新事实幂等、小行协调和只读采样工具，未确定保留期、分区/压缩或证明增长有界；旧策略是否停止新建、BTC unknown可见策略和保护行情开关例外仍留作产品决定。未经决定不删除旧数据或放宽买入/异动门槛。

## 31项问题逐项实施与边界
'''
md=[intro]
cards=[]
esc=html.escape
for row in issue_rows:
    tests=row['passed_regressions'];test_text='；'.join(tests) if tests else '静态核对；页面/启动专项证据见对应记录，不补造专项后端测试'
    md.append(f"\n### {row['id']} · {row['title']}\n\n状态：{labels[row['status']]}。整改：{' / '.join(row['actions'])}。\n\n{row['implementation']}\n\n代码：{', '.join(row['files'])}。\n\n验收：{test_text}。\n\n运行/决定边界：{row['remaining_boundary']}\n")
    links=' · '.join(f'<a href="{(ROOT/p).as_uri()}">{esc(p)}</a>' for p in row['files'])
    cards.append(f'<details class="issue"><summary><b>{row["id"]} · {esc(row["title"])}</b><span>{esc(labels[row["status"]])}</span></summary><p>{esc(row["implementation"])}</p><p class="paths">{links}</p><p><strong>验收：</strong>{esc(test_text)}</p><p class="limit"><strong>边界：</strong>{esc(row["remaining_boundary"])}</p></details>')
md.append('\n## 前端最后验收：不减少主要数据、不重做展开详情\n\n默认买入机会；participate/trial归入一张现在可以买表，仓位/分批仍是单条信号策略。市场观察合并自动发现与手动关注，关注是筛选。等待计划保留原八列。其他四入口合并辅助页面，原发布/回滚/授权与投递功能组件保留。\n')
for k,(status,evidence) in F.items():md.append(f'\n- {k}：{evidence} 状态：{status}。\n')
md.append('\n详情/K线未重新设计，保留冻结detailSnapshot及更新详情行为。浏览器fixture返回snapshot:null，只证实请求携带原冻结参数，不能声称真实K线已恢复。辅助表单全部实际操作尚未做运行验收。刷新循环原测试名仍含connected SSE stops fallback polls，它验证基础循环可关闭轮询；实际useRefresh正常连接仍30秒对账，以浏览器检查为据。\n')
md.append('\n## 证据与激活\n\n- tests.xml / test-isolation.json：397项完整隔离回归与测试实例退出结果。\n- frontend-browser-check.json / frontend-buy.png / frontend-observations.png：24项真实构建页面fixture检查与截图，2033日期仅为隔离假时钟。\n- startup-smoke.json：6项mock，实际启停进程为0。\n- static-review.json：35项拓扑/接口核对、4个生产端口只读检查与源码hash；不是真实容器烟测。\n- implementation-status.json：31项机器可读映射、未决定项和运行限制。\n- p39-final-frontend-contract.json：F01–F08最后核对状态。\n\n后续激活使用tools/start-local.ps1；如有既有进程则先核实归属并按用户重启指令处理。启动后只读验证真实数据链路，不恢复已停止的旧长期监控。原大批未提交工作保留，pre-implementation.patch只包含此前tracked差异，不能当作所有untracked文件完整备份。\n')
name='P39_代码更新与验收记录';(OUT/(name+'.md')).write_text(''.join(md),encoding='utf8')
front=''.join(f'<p><b>{k}</b> {esc(v[1])}<small>{esc(v[0])}</small></p>' for k,v in F.items())
page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 · 代码更新与验收记录</title><style>body{{margin:0;background:#f3f6fa;color:#16263e;font:15px/1.8 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1180px;margin:auto;padding:36px 22px}}h1{{font-size:30px}}h2{{margin-top:36px}}.hero,.issue,.front{{background:white;border:1px solid #dce4ee;border-radius:10px;padding:18px;margin:12px 0}}.hero{{border-left:5px solid #3970df}}.limit{{background:#fff3dd;padding:12px}}summary{{cursor:pointer;display:flex;justify-content:space-between;gap:18px}}summary span,small{{font-size:12px;color:#63768e}}small{{display:block}}a{{color:#2467bb;overflow-wrap:anywhere}}.paths{{font-size:12px}}.stats{{display:flex;gap:12px;flex-wrap:wrap}}.stats b{{padding:12px 20px;background:#eaf2ff;border-radius:8px}}nav a{{margin-right:20px}}@media(max-width:700px){{summary{{display:block}}}}</style><main><h1>P39 · 代码更新与验收记录</h1><p>{esc(stamp)} · 北京时间 · 原31项在前，前端验收在后</p><nav><a href="#issues">31项整改</a><a href="#frontend">前端最后验收</a><a href="implementation-status.json">机器可读清单</a></nav><section class="hero"><div class="stats"><b>后端 397 / 397</b><b>页面 24 项</b><b>启动 mock 6 项</b><b>静态核对 35 项</b></div><p>工程实现和隔离验收已完成；不代表31项生产故障已全部恢复。四个生产端口均未监听，新代码尚未激活。本轮零生产数据更改、零交易所请求、零真实通知。</p><p class="limit">启动后需验证OI/费率与K线新鲜度、CMC400和身份覆盖、WS连续性、队列吞吐及60分钟p95。旧来源官方额度未核实；raw留存/容量/分区策略、旧策略新建范围仍待决定。不删除历史、不放宽门槛。</p></section><h2 id="issues">31项问题逐项实施与边界</h2>{''.join(cards)}<h2 id="frontend">前端最后验收</h2><section class="front"><p>默认买入机会；买入策略属于单条信号。市场观察合并自动发现与手动关注，等待计划保留八列和原详情。展开详情及K线沿用。</p>{front}<p class="limit">真实构建页面使用隔离fixture；snapshot:null只验证冻结请求参数，未证实真实K线恢复。辅助全表单仍待实际运行验收。2033年时间仅为测试假时钟。</p><a href="frontend-buy.png">买入页面截图</a> · <a href="frontend-observations.png">观察页面截图</a></section><h2>证据与激活</h2><p><a href="tests.xml">完整回归</a> · <a href="test-isolation.json">隔离实例</a> · <a href="frontend-browser-check.json">页面验收</a> · <a href="startup-smoke.json">启动mock</a> · <a href="static-review.json">静态审计/当前端口</a></p><p>生产尚未启动；tools/start-local.ps1可用于后续激活。按用户重启指令处理既有服务，不自动恢复旧长期监控。保留此前未提交工作与原审计证据。</p></main></html>'''
(OUT/(name+'.html')).write_text(page,encoding='utf8')
report_path=str(OUT/(name+'.html'))
addition=f'''\n\n<!-- P39-IMPLEMENTATION-STATUS -->
## 最新实施与验收状态（{stamp}，北京时间）

原31项逐项映射已建立，先完成R01–R11工程整改，最后核对前端F01–F08。后端完整隔离回归397项、前端构建/类型检查、24项页面检查、6项启动mock及35项静态核对通过。前端保留原八列与展开详情/K线，合并入口不减少主要数据。

四个生产端口18770/18771/18772/55432均未监听，新代码尚未激活。运行数据恢复、CMC400/映射覆盖、OI/费率新鲜度、WS稳态及p95须启动后验证；真实容器烟测和辅助全表单实际运行验收未完成。不能把工程测试通过当作31项运行问题全部恢复。

A08：其他来源官方限额未核实，保持保守预算；A16：只完成新事实幂等、小行协调与只读采样工程，raw保留期/迁移/磁盘增长有界尚未决定或实测；A17：保留原策略，是否停止新建待决定。BTC unknown可见策略及保护行情采集例外仍未变更。

验收记录：[P39_代码更新与验收记录.html]({(OUT/(name+'.html')).as_uri()})；机器清单：[implementation-status.json]({(OUT/'implementation-status.json').as_uri()})。

原始31项审计证据、旧采样和提案阶段文字继续留存，描述的是当时状态；本节为当前实施状态。本轮未自动启动/重启生产、部署、启用交易或发送真实消息。
<!-- /P39-IMPLEMENTATION-STATUS -->
'''
main_md=PROJECT/'docs/P39_ERJI_主链路收敛与数据可靠性整改方案.md'
text=main_md.read_text(encoding='utf8');text=re.sub(r'\n*<!-- P39-IMPLEMENTATION-STATUS -->.*?<!-- /P39-IMPLEMENTATION-STATUS -->','',text,flags=re.S)
text=text.replace('方案状态：已形成，尚未实施。','方案状态：工程整改与隔离验收完成，运行激活及明确待决定项未完成（最新状态见文末）。',1)
main_md.write_text(text+addition,encoding='utf8')
main_html=main_md.with_suffix('.html');text=main_html.read_text(encoding='utf8')
text=re.sub(r'<!-- P39-IMPLEMENTATION-STATUS -->.*?<!-- /P39-IMPLEMENTATION-STATUS -->','',text,flags=re.S)
text=text.replace('方案状态：已形成，尚未实施。','方案状态：工程整改与隔离验收完成；运行激活及待决定项见最新验收记录。',1)
block=f'<section id="implementation-current" style="padding:24px;border:2px solid #3871dd;margin:24px 0;background:#edf4ff"><h2>最新实施状态 · 31项在前，前端最后验收</h2><p>{esc(stamp)}。397项后端隔离回归、24项页面、6项启动mock和35项静态核对通过。生产四端口未监听，新代码尚未激活。CMC/OI/WS及p95运行恢复未验；其他来源官方预算、raw留存/迁移和旧策略范围保留明确待决定边界。</p><p><a href="{(OUT/(name+".html")).as_uri()}">打开31项逐条代码更新与验收记录</a></p><p>原审计和方案阶段文字是历史记录，当前实施以本节和验收清单为准；不声称31项生产缺口已全部恢复。</p></section>'
banner=f'<div style="padding:16px;background:#edf4ff;border:1px solid #3871dd"><b>P39已完成工程更新和隔离回归，尚未激活生产。</b> <a href="#implementation-current">查看最新实施状态</a> · <a href="{(OUT/(name+".html")).as_uri()}">31项逐项验收记录</a></div>'
text=re.sub(r'<!-- P39-STATUS-BANNER -->.*?<!-- /P39-STATUS-BANNER -->','',text,flags=re.S)
text=re.sub(r'(<main[^>]*>)',lambda m:m.group(1)+'<!-- P39-STATUS-BANNER -->'+banner+'<!-- /P39-STATUS-BANNER -->',text,count=1)
text=text.replace('</body>','<!-- P39-IMPLEMENTATION-STATUS -->'+block+'<!-- /P39-IMPLEMENTATION-STATUS --></body>')
main_html.write_text(text,encoding='utf8');(PROJECT/'docs/P39_plan_latest.html').write_text(text,encoding='utf8')
print(json.dumps({'issue_rows':len(issue_rows),'passed':len(cases),'report':report_path,'plans_synchronized':True},ensure_ascii=False))
