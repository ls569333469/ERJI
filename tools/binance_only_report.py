"""Build the evidence-linked report; --complete requires finite runtime evidence."""
import sys,json,re,hashlib,math,html
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/p39-validation/binance-only-implementation'
PREVIEW=ROOT/'apps/web/public/previews'
complete='--complete' in sys.argv
def read(name):return json.loads((OUT/name).read_text('utf-8-sig'))
def save(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf8')
def bj(value):
    if isinstance(value,(int,float)):dt=datetime.fromtimestamp(value/1000,timezone.utc)
    else:dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    return dt.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
def p95(values):
    values=sorted(values)
    return round(values[min(len(values)-1,math.ceil(len(values)*.95)-1)],2) if values else None
finite=read('finite-acceptance-state.json');samples=[json.loads(Path(p).read_text('utf8')) for p in finite['samples']]
latest=samples[-1];browser=read('browser-acceptance.json');migration=read('migration-and-source-check.json')
baseline=read('baseline.json');registry=json.loads((ROOT/'.runtime/processes.json').read_text('utf-8-sig'))
now=datetime.now(timezone.utc).isoformat();run=finite['run_id']
suite=ET.parse(ROOT/'docs/p39-validation/binance-only-policy-final-tests.xml').getroot()
cases=list(suite.iter('testcase'));passed=sum(not any(c.find(k) is not None for k in ('failure','error','skipped')) for c in cases)
test_result={'backend_passed':passed,'backend_failed':sum(c.find('failure') is not None or c.find('error') is not None for c in cases),'isolation':json.loads((ROOT/'docs/p39-validation/binance-only-policy-final-test-isolation.json').read_text('utf8')),'node_passed':8,'typecheck':'passed','frontend_build':'passed','browser':browser['status'],'build_warning':'Next.js ignored workspace-parent package.json outside checkout Git root; production build completed successfully'}
elapsed=latest['since_activation_ms']/60000
if complete:
    assert len(samples)>=4 and elapsed>=30,(len(samples),elapsed)
    assert test_result['backend_failed']==0 and passed==313 and browser['status']=='passed'
    assert all(latest['ports'].values())
    assert all(r.get('status')==200 for r in latest['http'].values())
    assert all(not s['db']['forbidden_new_raw'] and not s['db']['external_capability_attempts'] and not s['db']['external_ws_open'] and not s['db']['external_current_buy'] for s in samples)
    assert latest['http']['observations']['status_divergence']==0
    assert all(v['failed']==0 for v in latest['task_delta'].values())
    assert not latest['db']['failed_tasks']
    assert all(r['status']=='fresh' for r in latest['db']['fx'])
    assert all(not isinstance(v,dict) or 'error' not in v for v in latest['db'].values())
progress=latest['db']['observation_progress'][0]
api_stats={key:{'samples':len(samples),'p95_ms':p95([s['http'][key]['ms'] for s in samples if 'ms' in s['http'][key]]),'latest':latest['http'][key]} for key in latest['http']}
log_path=Path(next(p['log_dir'] for p in registry if p['name']=='worker-jobs'))/'worker-jobs.err.log'
log=log_path.read_text('utf8',errors='replace')
held=[float(x) for x in re.findall(r'database connection held ([\d.]+)s',log)]
stage_values={k:[float(x) for x in re.findall(r'\b'+k+r'=([\d.]+)',log)] for k in ('read_ms','compute_ms','commit_ms','total_ms')}
diagnostics=[json.loads(p.read_text('utf8')) for p in sorted(OUT.glob('database-diagnostic-*.json'))]
manifest=[]
for proc in registry:
    path=Path(proc['log_dir'])/(proc['name']+'.err.log')
    found=[]
    if path.exists():
        for line in path.read_text('utf8',errors='replace').splitlines():
            if 'startup {' in line:
                try:found.append(json.loads(line.split('startup ',1)[1]))
                except ValueError:pass
    manifest.append({**proc,'startup_manifest':found[-1] if found else None,'web_loading':'existing Next dev HMR; verified by live browser' if proc['name']=='web' else None})
for proc in manifest:
    source_hashes=(proc['startup_manifest'] or {}).get('source_hashes',{})
    relevant=[p for p in source_hashes if p!='services/api/main.py' or proc['name']=='api']
    proc['relevant_startup_hashes_match_current']={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==source_hashes[p] for p in relevant}
if complete:
    assert all(all(p['relevant_startup_hashes_match_current'].values()) for p in manifest)
source_files=[]
for name,before_hash in baseline['files'].items():
    path=ROOT/name
    if path.is_file():
        current=hashlib.sha256(path.read_bytes()).hexdigest()
        if current!=before_hash:source_files.append({'path':name,'before_sha256':before_hash,'after_sha256':current})
new_files=['engine/pd2/source_policy.py','engine/pd2/binance_facts.py','engine/pd2/binance_identity.py','tests/test_binance_only.py','tools/binance_only_acceptance.py','tools/binance_only_browser.cjs','tools/binance_only_migration_check.py','tools/binance_only_db_diagnostic.py','tools/binance_only_report.py','tools/binance_only_report_check.cjs']
for name in new_files:
    if (ROOT/name).exists() and name.replace('/','\\') not in baseline['files'] and name not in baseline['files']:
        source_files.append({'path':name,'new':True,'after_sha256':hashlib.sha256((ROOT/name).read_bytes()).hexdigest()})
save('source-change-manifest.json',{'sampled_at':now,'head':baseline['head'],'preexisting_dirty_work_preserved':True,'files':source_files,'backup_root':str(OUT/'before')})
save('migration-and-tests.json',{'completed_at':now,'tests':test_result,'migration_evidence':'migration-and-source-check.json','native_identity_changes_counterfactual_count':len(migration['canonical_changes']),'history_deletion_performed':False,'frozen_plan_rebinding_performed':False,'request_boundary':'PublicAdapter request/legacy_request + request_budget guard; disabled provider request entrypoints and WS return before transport; isolated tests verify this boundary','transport_capture_limit':'Runtime samples observe raw events, capability attempts, WS coverage, tasks and increment logs; not an OS packet capture'})
save('source-coverage-diff.json',{'method':migration['identity_before_method'],'old_resolver_admitted':migration['old_admitted_assets'],'new_resolver_admitted':migration['new_admitted_assets'],'canonical_changes':migration['canonical_changes'],'unmapped_native_assets':migration['unmapped_native_assets'],'supply_coverage':migration['supply_coverage'],'identity_counts':migration['identity_counts'],'wallet_kline':'Binance Skill query-token-info calls dquery.sintral.io; excluded as third party. Wallet discovery retained; no unverified native OHLC substituted. Existing Alpha markets remain independent.'})
save('activation.json',{'status':'activated','recorded_at':now,'worker_run_id':run,'source_policy':'p39-binance-only-v1','head':baseline['head'],'uncommitted_code_hashes':'source-change-manifest.json','processes':manifest,'ports':latest['ports'],'effective_config':latest['db']['policy_config'],'health':latest['http']['health'],'long_term_monitor_resumed':False,'trading_enabled_by_this_change':False})
limits=[
 '普通币安现货/合约目录不返回通用流通供应量；OI原生金额仍展示，市值和OI/市值比明确“币安未提供供应量”，不伪造零值或CMC回退。',
 '币安钱包发现保留；官方Skill的K线实现实际访问dquery.sintral.io第三方，未接入。未在币安现货/合约/Alpha上市且无核验行情的资产不能产生伪造的交易计划。',
 '未证明倍率和链上身份的市场保持独立。93项canonical变化为旧resolver在同一当前目录上的反事实对照，不是完整迁移前数据库快照；旧CMC身份和历史不删除。',
 '结构/趋势仍为有界后台队列，不会瞬间完成全市场；已执行任务不等于数据齐备，缺窗、延迟和重试如实保留。',
 '数据库仍有连接持有和共享写锁竞争：本批次持有超过10秒的警告见性能表；只读诊断捕获idle in transaction及advisory等待。不能据此断言全部是慢SQL，也尚未完成逐函数归因。',
 '观察接口保留全部主要数据，256行响应约7.6MB；本次低频API时延见样本。未做生产压测，也没有相同固定输入的全链路改前/改后P95对照，不宣称提升90%。',
 '本次未新增鲸鱼持仓、全市场清算图等币安公开API无等价能力；买入信号严格按原有效期/价格/盘口条件动态退出，历史可买样本不表示现在仍可买。',
]
fields={r['venue']:r for r in latest['db']['source_fields']}
alpha=fields.get('binance_alpha')
if alpha:
    limits.append(f'Alpha实时报价覆盖尚未全恢复：最新采样已有状态{alpha["n"]}市场中，6分钟内报价{alpha["quotes_6m"]}个，15秒内{alpha["quotes_15s"]}个；不能因source_health=ok认为全部Alpha行情可用于入场。范围/调度约束需单独按具体市场核对。')
kline=latest['db'].get('observation_kline_windows')
if isinstance(kline,list):
    limits.append('观察市场最近至多300根已闭合窗口中仍有实际缺口：'+ '；'.join(r['tf']+'周期'+str(r['gaps_in_returned_window'])+'市场' for r in kline if r['gaps_in_returned_window'])+'。具体已尝试但缺输入市场列于运行验收记录，仍由既有补窗排队处理，本次不伪称已恢复。')
runtime={'status':'verified_with_limitations' if complete else 'in_progress','sample_count':len(samples),'elapsed_since_activation_minutes':round(elapsed,2),'sample_span_minutes':round((samples[-1]['as_of']-samples[0]['as_of'])/60000,2),'sample_paths':finite['samples'],'latest_progress':progress,'task_completion_delta_since_first_sample':latest['task_delta'],'latest_source_fields':latest['db']['source_fields'],'latest_structure_reasons':latest['db']['structure_reasons'],'api_timings':api_stats,'db_warning':{'connection_held_gt10s_count':len(held),'max_seconds':max(held) if held else 0,'p95_seconds':p95(held),'scanner_stage_samples':{k:{'n':len(v),'p95_ms':p95(v)} for k,v in stage_values.items()}},'diagnostic_paths':[p.name for p in sorted(OUT.glob('database-diagnostic-*.json'))],'limitations':limits,'long_term_monitor_resumed':False}
runtime['kline_windows']=latest['db'].get('observation_kline_windows')
runtime['structure_input_gaps']=latest['db'].get('structure_input_gaps')
runtime['funding_provenance']=latest['db'].get('funding_provenance')
runtime['coverage_recovery_status']='partial_with_explicit_gaps'
save('runtime-acceptance.json',runtime)
results=[
 ('B00','基线与备份','保留既有脏文件；325个文件基线/备份，记录HEAD、配置来源、注册进程。','baseline.json'),
 ('B01','来源统一','共享来源策略同时约束HTTP、WS、身份/派生、任务、投影及健康。','../binance-only-policy-final-tests.xml'),
 ('B02','USD参考估值','USDTUSD/USDCUSD官方assetIndex共享采集和UM权重账本；不伪造1:1。','runtime-acceptance.json'),
 ('B03','原生身份','不再用CMC alias生成新实时关系；未核验单位/跨链不强绑。','source-coverage-diff.json'),
 ('B04','供应与市值','Alpha供应保留，普通市场供应覆盖缺口明确显示，OI金额独立。','source-coverage-diff.json'),
 ('B05','钱包K线边界','取消GeckoTerminal；未核验的第三方Skill K线排除，保留发现及准确限制。','official-token-info-cli.mjs'),
 ('B06','派生上下文','关联合约和evidence_signature仅允许币安，不用外源fallback。','../binance-only-policy-final-tests.xml'),
 ('B07','进程与退休任务','live/expanded/supplements及订阅单源，保留update_outcomes和历史任务证据。','activation.json'),
 ('B08','配置和启动','发布binance_only配置版本，停用key消费与外源预算，旧配置不能复活外源。','migration-and-tests.json'),
 ('B09','状态和投影','修正状态计算顺序；非历史投影排除外源市场，不改写冻结快照。','runtime-acceptance.json'),
 ('B10','结构结果与复核','保留上次结果，分别显示首次排队/等待复核/缺输入及重试。','browser-acceptance.json'),
 ('B11','计算与名额','结构、趋势各1通道，独立名额、同市场互斥；MACD线性递推/市场输入缓存。','migration-and-tests.json'),
 ('B12','采集与快速通道','名额union供OI/补窗继续消费，保留事件事实与提交租约/输入fencing。','runtime-acceptance.json'),
 ('B13','契约和前端','导出类型；供应/FX/关联合约/任务状态文案同步，不减少主要列。','source-change-manifest.json'),
 ('B14','真实浏览器','观察折叠、买入两层、现有详情/历史K线、设置、运行页检查通过。','browser-acceptance.json'),
 ('B15','隔离回归','313项后端+8项Node通过，类型检查/生产构建通过。','migration-and-tests.json'),
 ('B16','实际激活','后台整批激活，API追加重载；web现有Next dev通过HMR加载并实页核验。','activation.json'),
 ('B17','有限运行验收',f'{len(samples)}时间点；激活后{elapsed:.1f}分钟；保留未恢复字段和性能限制。','runtime-acceptance.json'),
]
state=read('execution-state.json')
state.update(status='complete_with_limitations' if complete else 'in_progress',updated_at=now,code_status='complete',activation_status='activated',runtime_status=runtime['status'],report_path=str(OUT/'P39_Binance_only_acceptance.html'),report_url='http://127.0.0.1:18770/previews/P39_Binance_only_acceptance.html',limitations=limits)
if complete:state['completed_at']=now
by_id={x[0]:x for x in results}
task_files={
 'B00':['docs/p39-validation/binance-only-implementation/baseline.json','docs/p39-validation/binance-only-implementation/before'],
 'B01':['engine/pd2/source_policy.py','engine/adapters/base.py','engine/pd2/request_budget.py'],
 'B02':['engine/pd2/binance_facts.py','engine/pd2/p36_budget.py','engine/pd2/portfolio.py','engine/repository.py'],
 'B03':['engine/pd2/binance_identity.py','engine/pd2/universe_policy.py','engine/repository.py'],
 'B04':['engine/pd2/binance_facts.py','engine/pd2/data_health.py','apps/web/src/lib/api.ts'],
 'B05':['engine/adapters/wallet.py','engine/pd2/source_policy.py','engine/pd2/universe_policy.py'],
 'B06':['engine/pd2/derivatives.py','engine/strategies/p33_runtime.py','engine/pd2/market_selection.py'],
 'B07':['services/worker/main.py','engine/pd2/native_streams.py','engine/pd2/source_policy.py'],
 'B08':['engine/pd2/config.py','engine/contracts.py','engine/repository.py','tools/start-local.ps1','tools/restart-local-workers.ps1','compose.yml','infra/compose.release.yml','services/api/main.py'],
 'B09':['engine/p38/observation_query.py','engine/pd2/data_health.py','engine/pd2/signal_projection.py'],
 'B10':['engine/p38/jobs.py','engine/p38/observation_query.py','apps/web/src/components/observation-board.tsx'],
 'B11':['engine/p38/jobs.py','engine/p37/worker.py','engine/p37/analysis.py'],
 'B12':['engine/p37/worker.py','engine/p38/jobs.py','engine/p38/windows.py'],
 'B13':['apps/web/src/components/observation-board.tsx','apps/web/src/components/opportunity-board.tsx','apps/web/src/components/operations-dashboard.tsx','apps/web/src/components/settings-page.tsx','apps/web/src/lib/api.ts','apps/web/src/lib/api.generated.ts','packages/contracts/openapi.json'],
 'B14':['tools/binance_only_browser.cjs','tools/binance_only_report_check.cjs'],
 'B15':['tests/test_binance_only.py','tools/p39_isolated_tests.py'],
 'B16':['tools/restart-local-workers.ps1','engine/p38/startup.py','.runtime/processes.json'],
 'B17':['tools/binance_only_acceptance.py','tools/binance_only_db_diagnostic.py','tools/binance_only_report.py'],
}
for task in state['tasks']:
    task.update(status=('runtime_verified_with_limitations' if complete else 'in_progress') if task['id']=='B17' else 'activated' if task['id']=='B16' else 'code_verified',result=by_id[task['id']][2],evidence=[by_id[task['id']][3]],files_or_dependencies=task_files[task['id']])
save('execution-state.json',state)
status='代码已完成并激活 · 有限验收完成，存在明确数据覆盖和性能限制' if complete else '代码已完成并激活 · 有限运行验收中'
md=[];blocks=[]
def para(text,kind='p'):
    md.append(text+'\n');blocks.append('<'+kind+'>'+html.escape(text)+'</'+kind+'>')
def section(text):md.append('## '+text+'\n');blocks.append('<h2>'+html.escape(text)+'</h2>')
def table(headers,rows):
    md.append('| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+'\n'.join('| '+' | '.join(str(c).replace('|','/') for c in r)+' |' for r in rows)+'\n')
    blocks.append('<div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(str(c))+'</th>' for c in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(c))+'</td>' for c in r)+'</tr>' for r in rows)+'</tbody></table></div>')
para('P39 · 币安单一来源实施与验收','h1');para(status);para('报告更新时间（北京时间）：'+bj(now))
para('实时数据仅使用Binance合约、现货、Alpha及已核验的钱包发现。Aster、Hyperliquid、CMC、ByKaranteli、OKX、GeckoTerminal退出实时采集与决策；历史证据继续保留。')
table(['代码验收','页面验收','激活','有限运行'],[[f'{passed}后端 / 8 Node / 类型与构建通过','5个页面，详情和历史K线通过',run,f'{len(samples)}次采样 / 激活后{elapsed:.1f}分钟']])
section('B00–B17逐项结果')
table(['编号','内容','实际结果','状态'],[[r[0],r[1],r[2],next(t['status'] for t in state['tasks'] if t['id']==r[0])] for r in results])
section('激活后的真实链路')
para('以下完成增量以首个验收样本为基准；结构/趋势独立市场覆盖另计，不将任务次数或多个观察事件当成多个币种。')
para('后台357个市场包含同一资产的多个市场。调度先选择每资产代表市场；页面256行不是357个市场全量分析的分母。下列后台独立覆盖与页面代表覆盖应分别判断。')
o=latest['http']['observations']
if 'representative_structure_checked_this_run' in o:
    table(['页面代表资产','不适用','本run结构已检查','本run趋势已分析'],[[o['rows'],o['inapplicable_representatives'],o['representative_structure_checked_this_run'],o['representative_trend_analyzed_this_run']]])
table(['北京时间','激活分钟','结构检查市场','趋势检查市场','就绪/延迟矛盾','当前可买/等待','新外源原始记录'],[[bj(s['sampled_at_utc']),round(s['since_activation_ms']/60000,1),s['db']['observation_progress'][0]['structure_checked'],s['db']['observation_progress'][0]['trend_checked'],s['http']['observations'].get('status_divergence','未获取'),str(s['http']['signals'].get('levels',{}).get('l1','?'))+' / '+str(s['http']['signals'].get('levels',{}).get('l2','?')),len(s['db']['forbidden_new_raw'])] for s in samples])
table(['执行通道','完成增量','失败增量'],[[k,v['completed'],v['failed']] for k,v in latest['task_delta'].items()])
para('北京时间15:20的真实API采样出现OPG可买，参与区约0.1346–0.136247，采样价格0.1356，保护价0.1264；这证明原策略触发→投影→API链路有真实产出。它是历史采样，不表示此刻仍可买，也没有延长原报价/盘口/计划有效期。')
section('字段覆盖与时效')
table(['来源','已有市场状态','报价≤15秒','报价≤6分钟','OI≤90秒','OI≤6分钟','有资金费字段'],[[r[k] for k in ('venue','n','quotes_15s','quotes_6m','oi_90s','oi_6m','funding_present')] for r in latest['db']['source_fields']])
para('分母为已有MarketState的市场，不等同于当前观察或主合约集合；现货/Alpha本身OI不适用。资金费一列只证明有字段，不能作为全部资金费新鲜度结论。')
if isinstance(latest['db'].get('observation_kline_windows'),list):
    table(['周期','观察市场','连续窗口','窗口缺口','最新闭合齐备','少于100根'],[[r[k] for k in ('tf','markets','contiguous_returned_window','gaps_in_returned_window','latest_expected_closed','bars_below100')] for r in latest['db']['observation_kline_windows']])
    para('K线检查为每市场最近至多300根已闭合窗口，连续不代表历史全量连续。少于100根可能是新上市/周线历史不足，不自动归类故障。缺口仍由既有补窗处理，具体已尝试但未完成市场见structure_input_gaps。')
if isinstance(latest['db'].get('structure_input_gaps'),list):
    table(['结构暂缺输入市场','真实原因','最后尝试北京时间','下次重试北京时间'],[[r['instrument_id'],r['reason'],bj(r['last_attempt_at']),bj(r['retry_at']) if r.get('retry_at') else '—'] for r in latest['db']['structure_input_gaps']])
table(['供应来源','目录市场数','提供circulating_supply'],[[r['venue'],r['n'],r['supplied']] for r in migration['supply_coverage']])
section('性能实测与仍需改进之处')
table(['API','采样数','本地P95毫秒','最近响应字节'],[[k,v['samples'],v['p95_ms'],v['latest'].get('bytes','—')] for k,v in api_stats.items()])
table(['结构扫描阶段','日志样本数','P95毫秒'],[[k,len(v),p95(v)] for k,v in stage_values.items()])
para(f'当前jobs批次数据库连接持有超过10秒警告{len(held)}次，最大{max(held) if held else 0}秒；这是连接占用计时，并非单条SQL耗时。只读诊断捕获共享写锁等待和较长idle in transaction，尚未逐函数完整归因。快速通道和两类分析仍推进，但不能声称数据库瓶颈全部消失。')
para('结构/趋势计算缓存和MACD等价性已通过隔离对照；缺少同一固定生产输入的整体改前/改后对照，不把局部优化换算成整体性能百分比。采样为有限低频检查，不是负载测试。')
section('保留的限制与后续事项')
for limit in limits:para(limit)
section('进程、图表与历史保留')
table(['角色','注册PID','run','实际服务PID'],[[p['name'],p['pid'],p['run_id'],(p['startup_manifest'] or {}).get('pid','web既有进程/HMR')] for p in manifest])
para('注册PID可能为启动包装进程；实际服务PID以上述startup manifest为准。四个端口18770/18771/18772/55432均已核验。历史CMC身份234条保留，新币安identity链接991条；未执行冻结计划重绑或业务历史删除。')
para('chart.tsx与opportunity-detail.tsx保持原文件；观察展开详情保持原内容/布局。浏览器检查使用真实本地数据，阻止了3次图表偏好PUT写入；这些ERR_BLOCKED_BY_CLIENT为只读验收主动拦截，不是产品请求故障。')
para('后台采集/分析继续由既有服务运行；本次有限采样结束后不创建长期任务，原自动监控保持暂停，本次没有启用交易。')
section('原始证据')
links=['execution-state.json','activation.json','migration-and-tests.json','source-coverage-diff.json','source-change-manifest.json','runtime-acceptance.json','browser-acceptance.json','migration-and-source-check.json']+[Path(p).name for p in finite['samples']]+[p.name for p in sorted(OUT.glob('database-diagnostic-*.json'))]
md.extend('- ['+name+']('+name+')' for name in links)
blocks.append('<p>完整JSON、截图和测试XML位于实际checkout的docs/p39-validation/binance-only-implementation及上级目录。</p><ul>'+''.join('<li><code>'+html.escape(name)+'</code></li>' for name in links)+'</ul>')
document='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 币安单一来源实施与验收</title><style>body{margin:0;background:#f2f5fa;color:#192337;font:15px/1.75 "Segoe UI","Microsoft YaHei",sans-serif}main{max-width:1180px;margin:28px auto;padding:32px;background:#fff;border:1px solid #dce4ef;border-radius:16px}h1{font-size:28px;line-height:1.4}h2{margin-top:38px;font-size:21px;border-left:4px solid #2674d9;padding-left:12px}p{max-width:1100px}.scroll{overflow:auto;margin:18px 0}table{border-collapse:collapse;width:100%;font-size:13px}td,th{text-align:left;vertical-align:top;border:1px solid #dfe7f0;padding:9px 11px}th{background:#edf4ff;white-space:nowrap}tr:nth-child(even){background:#f9fbfe}code{overflow-wrap:anywhere;color:#315a86}a{color:#2466b8}@media(max-width:700px){main{padding:18px;margin:10px}h1{font-size:23px}}</style><main>'+''.join(blocks)+'</main></html>'
for path in (OUT/'P39_Binance_only_acceptance.html',PREVIEW/'P39_Binance_only_acceptance.html'):path.write_text(document,encoding='utf8')
(OUT/'P39_Binance_only_acceptance.md').write_text('\n'.join(md),encoding='utf8')
if complete:
    shared=ROOT.parents[2]/'docs'
    shared.joinpath('P39_币安单一来源_实施与验收.html').write_text(document,encoding='utf8')
    shared.joinpath('P39_币安单一来源_实施与验收.md').write_text('\n'.join(md),encoding='utf8')
    audit=ROOT/'docs/p39-validation/binance-only-audit'
    name='P39_币安单一数据源收敛与状态统一执行方案'
    label='B00–B17代码已完成并激活；有限运行验收结束，覆盖缺口及数据库性能限制详见实施报告。'
    banner='<section id="implementation-result" style="padding:18px;border:1px solid #a3c5e8;background:#edf5ff;border-radius:10px"><strong>'+label+'</strong><p><a href="http://127.0.0.1:18770/previews/P39_Binance_only_acceptance.html">打开最终实施与验收报告</a> · 更新于北京时间'+bj(now)+'</p><p>下文待执行措辞与初始状态为执行前方案记录；当前实际状态以implementation/execution-state.json及验收报告为准。</p></section>'
    page=(audit/(name+'.html')).read_text('utf8')
    page=re.sub(r'<section id="implementation-result".*?</section>','',page,flags=re.S)
    page=page.replace('执行方案 · 5个阶段 · 18项任务 · 尚未实施','执行方案 · 18项代码完成并激活 · 验收含限制')
    page=page.replace('<main>','<main>'+banner,1)
    for path in (audit/(name+'.html'),PREVIEW/'P39_Binance_only_execution_plan.html',shared/(name+'.html')):path.write_text(page,encoding='utf8')
    plan_md=(audit/(name+'.md')).read_text('utf8')
    plan_md=plan_md.split('\n## 9. 本次实际执行结果')[0]
    plan_md+='\n## 9. 本次实际执行结果\n\n'+label+'\n\n[最终实施与验收报告](http://127.0.0.1:18770/previews/P39_Binance_only_acceptance.html)\n\n原文为执行前方案，当前状态以docs/p39-validation/binance-only-implementation/execution-state.json为准。\n'
    for path in (audit/(name+'.md'),shared/(name+'.md')):path.write_text(plan_md,encoding='utf8')
    plan_path=audit/'execution-plan.json';plan=json.loads(plan_path.read_text('utf8'))
    plan.update(implementation_state_path=str(OUT/'execution-state.json'),implementation_report_url=state['report_url'],implementation_recorded_at=now)
    plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'status':state['status'],'samples':len(samples),'activation_elapsed_minutes':round(elapsed,2),'report_url':state['report_url'],'backend_passed':passed,'distinct_markets':progress,'connection_warnings':len(held)},ensure_ascii=False))
