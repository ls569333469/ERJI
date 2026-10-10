"""Reconcile saved P39 scope and finite read-only evidence; never imports the engine."""
import ast, hashlib, html, json, re, shutil, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'docs/p39-validation'
OUT = BASE / 'cleanup-review-20261007'
PUBLIC = ROOT / 'apps/web/public/previews'

def read(path):
    return json.loads(path.read_text('utf-8-sig'))

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def table(headers, rows):
    return '<div class="table"><table><thead><tr>' + ''.join('<th>'+html.escape(str(v))+'</th>' for v in headers) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'

def paragraph(text):
    return '<p>'+html.escape(text)+'</p>'

def section(title, body):
    return '<section><h2>'+html.escape(title)+'</h2>'+body+'</section>'

original = read(BASE/'implementation-status.json')['issues']
followups = read(BASE/'p39-current-followup-status.json')['items']
single = read(BASE/'binance-only-implementation/execution-state.json')['tasks']
manifest = read(OUT/'cleanup-manifest.json')
samples = [read(p) for p in sorted((OUT/'runtime').glob('acceptance-*.json'))]
first, latest = samples[0], samples[-1]
browser = read(OUT/'browser/browser-acceptance.json')
diagnostic_command = read(OUT/'database-diagnostic-command.json')
diagnostic = read(Path(diagnostic_command['path']))
tests = list(ET.parse(BASE/'p39-cleanup-final-tests.xml').iter('testcase'))
passed = {t.attrib['name'] for t in tests if t.find('failure') is None and t.find('error') is None and t.find('skipped') is None}
assert len(passed) == 505 and browser['status'] == 'passed'

# These two small patches were applied after the initial cleanup manifest.
# Reconstruct their immediate before-images by reversing only those exact patches.
for name in ('tests/test_p25_signals.py', 'tools/binance_only_acceptance.py'):
    path = ROOT/name
    before = path.read_text('utf8')
    if name.startswith('tests/'):
        start = before.index('    # Retained CMC history')
        end = before.index('\n\ndef test_alpha', start)
        before = before[:start] + "    assert aliases['aster:AAA']=='AAA' and len(admitted)==1\n" + before[end:]
    else:
        before = before.replace('import sys,json,time,socket,urllib.request,re,os','import sys,json,time,socket,urllib.request,re')
        before = before.replace("OUT=ROOT/os.environ.get('ERJI_ACCEPTANCE_OUT','docs/p39-validation/binance-only-implementation');OUT.mkdir(parents=True,exist_ok=True)","OUT=ROOT/'docs/p39-validation/binance-only-implementation';OUT.mkdir(exist_ok=True)")
        start = before.index('        # A timestamp millisecond')
        end = before.index('\n        excerpt=',start)
        before = before[:start] + "        patterns={k:len(re.findall(v,data,re.I)) for k,v in {'tracebacks':'Traceback','429':r'\\b429\\b','418':r'\\b418\\b','timeouts':'TimeoutError|timed out','role_lost':'worker_role_lost','connection_held':'database connection held'}.items()}" + before[end:]
    backup = OUT/'before'/name
    if not backup.exists():
        backup.parent.mkdir(parents=True,exist_ok=True)
        backup.write_text(before,encoding='utf8')
    if not any(r['path']==name for r in manifest['files']):
        manifest['files'].append({'path':name,'before_sha256':digest(backup),'before_lines':len(before.splitlines()),'backup':str(backup),'backup_method':'reverse exact current-turn patch','action':'edited'})
for record in manifest['files']:
    path = ROOT/record['path']
    record.update(after_sha256=digest(path) if path.exists() else None,after_lines=len(path.read_text('utf8').splitlines()) if path.exists() else 0)
manifest['removed_executable_lines_net']=sum(r['before_lines']-r['after_lines'] for r in manifest['files'])
manifest['verified_at_utc']=datetime.now(timezone.utc).isoformat()
save(OUT/'cleanup-manifest.json',manifest)

syntax_errors=[]; modules=0; retired_references=[]
retired_modules={'engine.adapters.cex','engine.adapters.hyperliquid'}
retired_hooks={'stream_hyperliquid','save_hl_context','seed_verified_identity','map_request_symbols','save_map'}
for directory in ('engine','services','tools','tests'):
    for path in (ROOT/directory).rglob('*.py'):
        if path.name=='p39_cleanup.py':continue  # executed migration; its source preserves the retired implementation
        try:
            tree=ast.parse(path.read_text('utf-8-sig'),filename=str(path));modules+=1
        except Exception as error:
            syntax_errors.append({'path':str(path.relative_to(ROOT)),'error':str(error)});continue
        for node in ast.walk(tree):
            imports=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
            hooks=[a.name for a in node.names] if isinstance(node,ast.ImportFrom) else []
            if any(x in retired_modules for x in imports) or any(x in retired_hooks for x in hooks):
                retired_references.append({'path':str(path.relative_to(ROOT)),'line':node.lineno})
assert not syntax_errors and not retired_references
save(OUT/'source-check.json',{'python_modules_parsed':modules,'syntax_errors':syntax_errors,'retired_imports':retired_references,'scope':'engine/services/tools/tests; historical docs/archives excluded'})

prior_manifest=read(BASE/'binance-only-implementation/source-change-manifest.json')
expected=read(BASE/'binance-only-implementation/baseline.json')['files'].copy()
expected.update({r['path']:r['after_sha256'] for r in prior_manifest['files']})
front=[{'path':path,'after_sha256':sha} for path,sha in expected.items() if path.replace('\\','/').startswith('apps/web/src/')]
front_unchanged=all((ROOT/r['path']).exists() and digest(ROOT/r['path'])==r['after_sha256'] for r in front)
save(OUT/'frontend-source-check.json',{'unchanged_since_binance_only_acceptance':front_unchanged,'files':[{'path':r['path'],'matches':digest(ROOT/r['path'])==r['after_sha256']} for r in front], 'current_browser_acceptance':'browser/browser-acceptance.json','build':'Reuse prior successful build only if these source hashes match; no frontend source edits in this cleanup'})
assert front_unchanged

matrix=[]
for item in original:
    matches=[n for n in item.get('passed_regressions',[]) if n in passed]
    missing=[f for f in item.get('files',[]) if not (ROOT/f).exists()]
    assert not missing and len(matches)==len(item.get('passed_regressions',[]))
    state='工程回归通过；生产条件见边界' if matches else '代码复核；本轮未做破坏性/容器故障演练'
    if item['id']=='A16':state='部分实现；raw容量/留存策略未决定'
    if item['id']=='A17':state='产品待决定；有效策略兼容保留'
    if item['id'] in ('A08','A15'):state='外源实时支路已由币安单源方案停用'
    matrix.append({**item,'current_review':state,'current_regressions_passed':len(matches),'source_files_exist':True})
save(OUT/'p39-reconciliation.json',{'original_31':matrix,'followups':followups,'binance_only_18':single,'note':'原31项与单源专项B编号不同；原历史验收状态保留，本文件作为本次核对叠加记录'})

at=datetime.fromtimestamp(latest['as_of']/1000,timezone(timedelta(hours=8))).isoformat(timespec='seconds')
minutes=round((latest['as_of']-first['as_of'])/60000,2)
fields=latest['db']['source_fields']; obs=latest['http']['observations']; sig=latest['http']['signals']
queued=[r for r in diagnostic['snapshots'][0]['queue'] if r['state']=='queued']
db_activity=[r for snap in diagnostic['snapshots'] for r in snap['activity']]
reconnect=[{'role':r['role'],'state':r['state'],'age_seconds':round((latest['as_of']-r['updated_at'])/1000,1),'last_receive_at':r['details'].get('last_receive_at')} for r in latest['db']['heartbeats'] if r['role'].startswith('stream_') and r['state']!='connected']
state={'status':'review_and_cleanup_complete_runtime_degraded','checked_at_bjt':at,'original_issue_count':31,'single_source_task_count':18,'backend_tests_passed':len(passed),'browser_status':browser['status'],'python_modules_parsed':modules,'frontend_source_unchanged':front_unchanged,'runtime_run':latest['run_id'],'cleanup_loaded_by_existing_processes':False,'services_restarted':False,'production_database_written':False,'automations_resumed':False,'new_failures_in_finite_interval':{k:v['failed'] for k,v in latest['task_delta'].items()},'remaining':['Database connection/commit and task queue delay','OI/quotes freshness and Alpha coverage','Kline gaps/staleness','Raw retention policy undecided','Old strategy scope undecided','Historical two structure failures root cause unproven'], 'report_url':'http://127.0.0.1:18770/previews/P39_code_review_cleanup.html'}
save(OUT/'review-state.json',state)

intro='P39已确定工程范围和币安单源更新仍在代码中；清理后505项后端隔离回归全部通过，真实页面检查通过。当前主链路持续推进，但存在数据库提交、排队和数据新鲜度问题，不能标为全部正常。'
scope='核对原P39的31项、N01–N04/UI01–UI02后续项及单源B00–B17共18项。后续用户授权的币安单源范围覆盖旧外源相关条目；原始问题和旧验收证据没有改写。'
activation='运行版本是此前已激活的币安单源版，worker run '+latest['run_id']+'。本次清理在源码完成，未重启API/live/jobs/streams，因此不能声称现有进程已加载清理后的文件；前端业务源码未改。原长期自动监控仍暂停。'
cleanup_text='从执行目录移出9个不再被活跃代码调用的旧模块/工具：Aster/Hyperliquid重复适配器、P25外源探针、CMC刷新与旧PD2诊断入口。删除Supplemental旧HTTP实现、return后的外源采集/订阅/盘口分支、无调用身份写入钩子和外源缓存消费者。保留停用兼容返回、纯历史payload解析、身份记录、原始证据、冻结计划、仍在生产使用的箱体/P33策略，以及原详情/K线。'
test_text='首轮504通过/1失败：旧P25用例仍要求verified CMC链接合并Aster资产。按已授权币安单源契约改为CMC历史不能生成新实时alias，并增加verified Binance同资产合并的正向断言；未放宽买入门槛。最终505通过，1项httpx/Starlette依赖弃用警告。数据库测试为独立PostgreSQL集群，不读取或复制生产数据库。'
runtime_text=f'北京时间{at}，四端口18770/18771/18772/55432可连接，本地7个HTTP检查均200。观察API返回{obs["rows"]}个代表资产，ready/delayed互相矛盾记录0。{minutes}分钟有限间隔内entry/projection/protect/structure/trend分别新增12/25/21/6/19次完成，新增失败均0；这是任务完成数，不是独立币数。此刻L1可买{sig["levels"]["l1"]}、L2等待{sig["levels"]["l2"]}，没有合格信号本身不能证明故障或没有市场机会。'
failure_text='本run在早先采样中structure累计发生过2次失败；当前无state=failed任务，随后仍持续完成，有限间隔未增加失败。当前任务error已被后续状态覆盖、日志未留下对应堆栈，本轮无法确定这两次的准确根因，不把它们声明为彻底解决。流分片曾出现ConnectionClosedError自动重连；本次末采样非connected分片为 '+json.dumps(reconnect,ensure_ascii=False)+'。只描述当前证据，不将整条币安流声称全部断开或全部恢复。'
remaining_rows=[
 ['高优先','数据库与队列延迟','6次只读DB快照出现12条有blockers记录，advisory锁与idle-in-transaction并存；jobs日志连接占用曾135.1秒、结构commit曾16.6秒。结构queued最大17.7分钟，P95约17.4分钟；投影queued最大约9.9分钟。','定位具体锁持有事务、缩短共享事务/批次与热行提交；保留提交栅栏。按逐任务排队/执行/提交P95验收，不能只加并发。'],
 ['高优先','报价/OI与Alpha覆盖','下表是真实当前覆盖；报价15秒、OI90秒仅部分市场满足。Alpha 6分钟报价覆盖很低，不能冒称所有Alpha都具备实时资格。','按活跃名额优先刷新并量化提交延迟；目录数量不能当活跃采集需求，不伪造估值或延长有效期。'],
 ['高优先','K线连续/闭合新鲜度','403观察市场中1H返回窗口36处有缺口，4H 9处；日/周返回窗口连续，但部分缺最新闭合或历史根数不足。','按具体市场/周期补窗和重试验收；历史上市不足与丢失区分，不把晚补历史回填成过去的买入。'],
 ['未决定','raw长期容量与旧策略范围','原A16原始数据留存/压缩、A17是否停止新建旧策略仍未决定。','保留证据和旧计划，需产品范围决定后另行实施；本轮不删除历史数据或有效策略。'],
 ['验证边界','容器与长期恢复','本地HTTP和回归不能代替跨容器部署、故障注入、长期覆盖/P95验收；两次历史结构失败根因未证明。','保留证据；本轮无生产故障注入、部署、自动监控恢复或交易。'],
]
evidence=['cleanup-manifest.json','source-check.json','frontend-source-check.json','p39-reconciliation.json','review-state.json','browser/browser-acceptance.json','runtime/acceptance-'+latest['run_id']+'-00.json','runtime/acceptance-'+latest['run_id']+'-01.json','database-diagnostic-command.json']
body=section('核对结论',paragraph(intro)+paragraph(scope)+paragraph(activation))
body+=section('清理范围与恢复方式',paragraph(cleanup_text)+paragraph(f'本次编辑{sum(r["action"]=="edited" for r in manifest["files"])}个文件、移出9个旧文件，净减少{manifest["removed_executable_lines_net"]}行。每个原文件在before目录，退休代码在retired-code的.txt归档；恢复按manifest逐文件核对，避免覆盖后续工作。')+table(['文件','动作','原行数','现行数'],[[r['path'],r['action'],r['before_lines'],r['after_lines']] for r in manifest['files']]))
body+=section('代码与真实页面验证',paragraph(test_text)+paragraph(f'{modules}个Python文件语法解析通过；活跃源码未发现退休模块/钩子的import。前端src文件与上一轮已通过构建的哈希一致，复用该构建结论；本轮重新验证买入、观察、来源、设置、运行记录5个页面均200，双层级、观察收拉及现有详情/K线无渲染错误。浏览器主动拦截2次图表偏好PUT保证只读，ERR_BLOCKED_BY_CLIENT属于本次拦截。没有符合条件的冻结历史行可用于本轮历史图表自动检查，因此历史图表结论仅沿用此前验收，不能算本轮重测。'))
body+=section('当前运行与数据',paragraph(runtime_text)+paragraph(failure_text)+table(['来源','库内市场','报价≤15秒','报价≤6分钟','OI≤90秒','OI≤6分钟'],[[r['venue'],r['n'],r['quotes_15s'],r['quotes_6m'],r['oi_90s'],r['oi_6m']] for r in fields])+paragraph('现货/Alpha无原生合约OI并非采集故障；普通币安市场不返回完整供应量，市值缺口不允许用FDV替代。停用外源自本run没有新增raw及capability采集尝试，USDTUSD/USDCUSD参考估值fresh。')+table(['周期','市场','缺整个窗口','连续窗口','有缺口','最新闭合齐全','少于100根'],[[r['tf'],r['markets'],r['absent'],r['contiguous_returned_window'],r['gaps_in_returned_window'],r['latest_expected_closed'],r['bars_below100']] for r in latest['db']['observation_kline_windows']])+paragraph('采样工具原先只匹配裸数字429/418，会把日志毫秒和commit_ms误识为限流。本轮改为带HTTP/status/response上下文，并分目录保存新采样；上下文匹配没有命中，不声称从所有日志格式排除了所有限流。'))
body+=section('仍需解决或补验收',table(['顺序','事项','证据/边界','建议处理'],remaining_rows))
body+=section('原P39 31项逐条核对',paragraph('下表保留原编号和未验证边界。“回归通过”指本轮相同测试名实际通过，并非宣称该项所有生产场景均恢复。')+table(['编号','问题','本次结论','本轮对应回归数','运行/产品边界'],[[r['id'],r['title'],r['current_review'],r['current_regressions_passed'],r['remaining_boundary']] for r in matrix]))
body+=section('后续新增项及币安单源专项',table(['后续编号','事项','原实施记录'],[[r['id'],r['title'],r['status']] for r in followups])+table(['单源编号','实施范围','原验收状态','结果'],[[r['id'],r['work'],r['status'],r.get('result','')] for r in single]))
body+=section('原始证据', '<ul>'+''.join('<li><code>'+html.escape(p)+'</code></li>' for p in evidence)+'</ul>'+paragraph('完整回归：../p39-cleanup-tests.xml（首轮失败保留）、../p39-cleanup-final-tests.xml、../p39-cleanup-final-test-isolation.json。历史报告、数据库和业务记录保留。'))
style='body{margin:0;background:#f3f5f8;color:#24334a;font:15px/1.65 "Microsoft YaHei",system-ui,sans-serif}main{max-width:1400px;margin:auto;padding:26px}h1{font-size:27px}h2{font-size:20px}section{background:white;border:1px solid #dde3ec;border-radius:12px;padding:20px;margin:20px 0}p{overflow-wrap:anywhere}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #dde3ec;padding:10px;text-align:left;vertical-align:top;min-width:85px}th{background:#edf2fb}code{overflow-wrap:anywhere}a{color:#2459bc}.tag{color:#8a5600;background:#fff2cc;padding:8px 12px;border-radius:8px} @media(max-width:600px){main{padding:12px}section{padding:12px}h1{font-size:23px}}'
document='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 方案代码复核与旧代码清理</title><style>'+style+'</style><main><h1>P39 · 方案代码复核与旧代码清理</h1><div class="tag">工程回归通过 · 运行仍有延迟 · 本次清理尚未重启加载</div><p>核对时刻（北京时间）：'+at+'</p>'+body+'</main></html>'
name='P39_方案代码复核与旧代码清理记录'
(OUT/(name+'.html')).write_text(document,encoding='utf8')
markdown='# P39 方案代码复核与旧代码清理记录\n\n'+at+'\n\n'+ '\n\n'.join((intro,scope,activation,cleanup_text,test_text,runtime_text,failure_text))+'\n\n## 仍需解决或补验收\n\n'+ '\n\n'.join('**'+r[1]+'**：'+r[2]+' '+r[3] for r in remaining_rows)+'\n\n## 原31项逐条核对\n\n|编号|事项|本次结论|对应回归|剩余边界|\n|---|---|---|---|---|\n'+'\n'.join('|'+ '|'.join(str(v).replace('|','/') for v in (r['id'],r['title'],r['current_review'],r['current_regressions_passed'],r['remaining_boundary']))+'|' for r in matrix)+'\n\n详细后续6项、单源18项、清理文件列表、行情/K线覆盖和全部证据参见同名HTML及p39-reconciliation.json。\n'
(OUT/(name+'.md')).write_text(markdown,encoding='utf8')
PUBLIC.mkdir(exist_ok=True)
(PUBLIC/'P39_code_review_cleanup.html').write_text(document,encoding='utf8')
shared=Path('D:/Axiangmu/erji/docs')
for suffix in ('.html','.md'):shutil.copy2(OUT/(name+suffix),shared/(name+suffix))
# Add a current review entry without rewriting historical acceptance conclusions.
banner='<aside id="p39-cleanup-review-20261007" style="padding:14px;margin:16px;border:1px solid #e2ba69;background:#fff6de;color:#563d0c">2026-10-07 当前复核：505项隔离回归通过；仍有数据库/排队与数据缺口，本次清理尚未重启加载。<a href="http://127.0.0.1:18770/previews/P39_code_review_cleanup.html">打开方案代码复核与旧代码清理记录</a></aside>'
for path in (PUBLIC/'P39_plan_latest.html',PUBLIC/'P39_Binance_only_acceptance.html',shared/'P39_plan_latest.html',shared/'P39_ERJI_主链路收敛与数据可靠性整改方案.html'):
    if not path.exists():continue
    content=path.read_text('utf8')
    if 'id="p39-cleanup-review-20261007"' in content:continue
    backup=OUT/'before-plan-links'/path.name
    if backup.exists():backup=backup.with_name(str(len(list(backup.parent.iterdir())))+'-'+backup.name)
    backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,backup)
    content,count=re.subn(r'(<body[^>]*>)',lambda m:m.group(1)+banner,content,count=1,flags=re.I)
    if not count:
        content,count=re.subn(r'(<main[^>]*>)',lambda m:m.group(1)+banner,content,count=1,flags=re.I)
    assert count==1
    path.write_text(content,encoding='utf8')
print(json.dumps(state,ensure_ascii=False))
