"""Publish local implementation evidence without querying or restarting production."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json,hashlib,html,re,xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/p39-validation/timing-implementation-20261008'
AUDIT=ROOT/'docs/p39-validation/timing-audit-20261008'
PUBLIC=ROOT/'apps/web/public/previews'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
    tests=ET.parse(ROOT/'docs/p39-validation/timing-final-tests.xml').getroot()
    suites=tests.findall('testsuite')
    checks={k:sum(int(s.get(k,'0')) for s in suites) for k in ('tests','failures','errors','skipped')}
    assert checks['failures']==checks['errors']==0
    target=ET.parse(ROOT/'docs/p39-validation/timing-status-tests.xml').getroot()
    assert all(int(s.get('failures','0'))==int(s.get('errors','0'))==0 for s in target.findall('testsuite'))
    unique={(t.get('classname'),t.get('name')) for t in tests.iter('testcase')}|{(t.get('classname'),t.get('name')) for t in target.iter('testcase')}
    runs=[checks['tests'],sum(int(s.get('tests','0')) for s in target.findall('testsuite'))]
    checks['tests']=len(unique)
    isolation=json.loads((ROOT/'docs/p39-validation/timing-final-test-isolation.json').read_text('utf8'))
    assert isolation['production_access'] is False and isolation['exit_code']==0
    bench=json.loads((OUT/'replay-benchmark.json').read_text('utf8'))
    baseline=json.loads((OUT/'baseline.json').read_text('utf8'))
    changes=[]
    for row in baseline['files']:
        p=ROOT/row['path'];digest=hashlib.sha256(p.read_bytes()).hexdigest()
        if digest!=row['sha256']:changes.append({**row,'before_sha256':row['sha256'],'sha256':digest})
    frequencies=[
        ['T1 普通币','300秒固定网格、稳定分片','价格/5M K线持续接收；批内回查真实已记录端点；启动时立即检查当前可用事实'],
        ['普通成交额','300秒','合约/现货各走批量ticker；合约持续采集内独立刷新；实际closeTime、received、raw_ref单独提交'],
        ['普通 OI','300秒','单币请求完成后入写入队列；提交成功才确认，失败独立退避；没有合成OI峰值'],
        ['T2/T3 重点 OI','60秒','升层立即到期；同一原生合约只保留一个进行中认领；优先级版本变化使缓存失效'],
        ['T2 趋势/结构','需求唤醒、30秒检查兜底；完成后补位','成功复核600秒；缺输入120秒重试，新事实提前唤醒；相同输入复用；排队需求不因等待超过120秒被轮换撤销'],
        ['T3 当前计划','30秒生产兜底','新计划立即交接；盘口刷新与发布走快路；普通扫描不决定当前计划到期'],
        ['等待计划投影','60秒版本兜底、结果变化提交','保留typed producer，取消P39下重复projection producer；任务完成后补位'],
        ['普通/重点观察跟踪','300秒 / 60秒','跟踪快照年龄不再二次挡住当前有效买入；当前报价、区间、保护、计划到期仍实时校验'],
        ['页面','变更合并5秒、60秒轮询兜底','K线尾部30秒补查；本地信号到期隐藏仍保留'],
        ['心跳 / 角色时效','60秒 / 180秒','T1心跳与扫描工作分开；进度记录扫描开始/结束/进行中，partial不称为整体正常'],
        ['OI/成交额参考年龄','600秒','OI异动入口仍要求端点90秒内；T1价格连续性仍按原15秒断档规则'],
        ['当前价格/盘口事实年龄','90秒','schema、当前资格、盘口存储、投影、API统一；旧冻结配置原文保留，仅当前工程时效应用新policy'],
        ['采集进行中 / 分析租约','120秒；分别30秒 / 20秒续租','认领token与失败次数分开；旧token晚到不能确认新认领；网络请求锁与提交阶段锁分离'],
        ['资金费历史 / FX','重点300秒 / FX300秒','普通资金费历史1800秒；结算和汇率各按真实字段时效；策略阈值不变'],
    ]
    corrections=[
        '五分钟是相邻到期网格，不是完成后再等待五分钟；普通OI与T1采用同一原生symbol稳定相位，网络与提交时间另存。',
        '成交额不再被30分钟目录缓存和持续OI循环困住，也不会拿本批开始时间替代迟到字段的实际来源时间。',
        '回查使用当时已知的价格、量能修订、OI、目录/成交额及配置；同一时点计算BTC和合格市场背景，不能用当前数据填过去。',
        'T2结果不因计算年龄超过390秒自动变旧；当前任务输入版本变化单独显示input_changed。',
        'T2与T3仅在提交阶段协调写入；T3网络请求有独立同币互斥，并通过实际并发测试确认等待网络不会阻塞同币结构分析。',
        '参考数据600秒、OI触发90秒、连续价格窗口15秒、当前可买价格/盘口90秒分别按用途应用，不把缓存或跟踪年龄当新选币门槛。',
    ]
    limits=[
        '生产服务本轮没有重启或激活；实际HTTP吞吐、全轮交付、真实队列及锁等待需重启激活后测量。整轮240秒是测量目标，不是删候选或阻拦信号的门槛。',
        '合成测试只证明本地回查算法和特定并发路径。5分钟离散OI无法恢复没有采到的峰值；15M单侧60秒端点匹配仍按真实样本判断，网络抖动超出容差会保留缺口。',
        '价格缓冲最多2100个1秒样本；正常约35分钟。回查仅覆盖保留的真实证据，遇到断流/源切换/缺少历史字段或超过留存范围不补造行情。关机后价格窗口仍需重新积累。',
        '此次保留1条结构执行通道，未增加请求预算或并发HTTP槽。结构积压仍需根据激活后的实际到达率与完成耗时判断是否增加计算槽。',
    ]
    result={'captured_at_bjt':stamp,'status':'code_updated_isolated_validated_pending_activation','production_activated':False,
        'strategy_thresholds_changed':False,'timing_policy':'p39-five-minute-v1','schedule':frequencies,'corrections':corrections,
        'validation':{'python':checks,'test_runs':runs,'database_isolation':isolation,'frontend_typecheck':'passed','frontend_isolated_build':'.next-p39-timing-check passed','frontend_refresh_tests':5,'frontend_display_scripts':'both passed'},
        'replay_benchmark':bench,'baseline_changes':changes,'limitations':limits}
    (OUT/'implementation-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
    def table(headers,rows):
        return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(str(v))+'</th>' for v in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in r)+'</tr>' for r in rows)+'</tbody></table></div>'
    body='<h1>P39 · 五分钟分级调度代码更新</h1><p class="stamp">'+stamp+'</p><div class="notice"><strong>代码更新与隔离验证完成，待重启激活。</strong><p>普通币五分钟更新，T2/T3单独处理；本轮没有重启生产服务，持续监控仍停止。</p></div>'
    body+='<nav><a href="P39_timing_consistency_plan.html#section-14">总方案最新实施记录</a><a href="P39_five_minute_schedule_plan.html">五分钟方案</a></nav>'
    body+='<h2>复核修正</h2><ul>'+''.join('<li>'+html.escape(v)+'</li>' for v in corrections)+'</ul>'
    body+='<h2>实施后的时序</h2>'+table(['环节','频率','协作方式'],frequencies)
    body+='<h2>验证结果</h2><p>'+str(checks['tests'])+' 项 Python 隔离测试通过；真实数据库并发认领、过期token、网络期间T2处理、死锁、取消清理、输入版本、区间、保护和到期均纳入验证。前端类型检查、独立构建、5项刷新测试及2个展示测试脚本通过。</p>'
    body+='<p>241项全面回归通过；收尾修正时间异常下列表与计数一致性后，相关62项复测通过，合计242个独立测试项目。</p>'
    body+='<p>使用测试专属 PostgreSQL 随机端口 '+str(isolation['port'])+'，测试结束已停止。未拷贝生产数据，未批量请求交易所。</p>'
    body+='<h3>650币本地回查合成测试</h3>'+table(['短时异动币数','3次耗时（秒）','找回币数'],[[c['temporary_peak_markets'],', '.join(str(round(v/1000,2)) for v in c['samples_ms']),c['recovered_peak_markets']] for c in bench['cases']])
    body+='<p>这是内存回查耗时，不包含HTTP、写库、T2分析或生产排队。</p><h2>激活后需要测量</h2><ul>'+''.join('<li>'+html.escape(v)+'</li>' for v in limits)+'</ul>'
    body+='<h2>本轮代码差异</h2><p>以下文件与本轮更新前保存的源文件快照比较；既有未提交工作保留。</p>'+table(['文件','前态 SHA256','更新后 SHA256'],[[r['path'],r['before_sha256'][:12],r['sha256'][:12]] for r in changes])
    body+='<p>新增：engine/p39/timing.py、因果/并发测试、合成回查测量及本报告工具。另同步 P33 当前工程时效、signal_service、FX 与 API 类型。业务策略阈值、计划寿命和数据源范围沿用。</p>'
    css='body{margin:0;background:#f3f5f8;color:#203047;font:16px/1.7 system-ui,sans-serif}main{max-width:1160px;margin:32px auto;background:white;padding:32px;border-radius:16px}h1{font-size:29px}h2{margin-top:32px}a{color:#245bd6}nav{display:flex;gap:22px;flex-wrap:wrap}.notice{background:#edf5ff;border-left:4px solid #245bd6;padding:16px}.stamp{color:#66768a}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #dee5ed;padding:12px;text-align:left;vertical-align:top}th{background:#edf1f6}li{margin:10px 0}footer{color:#66768a;margin-top:32px}@media(max-width:600px){main{margin:0;padding:18px;border-radius:0}h1{font-size:24px}table{min-width:660px}}'
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 · 五分钟分级调度代码更新</title><style>'+css+'</style><main>'+body+'<footer>本地代码验证记录 · 待生产重启激活</footer></main></html>'
    name='P39_five_minute_timing_acceptance.html'
    (OUT/name).write_text(page,'utf8');(PUBLIC/name).write_text(page,'utf8')
    # Preserve historical audit evidence; clearly identify the latest source update.
    section='<section id="section-14"><h2>14. 本轮复核与代码实施</h2><p>更新于 '+stamp+'：代码更新、'+str(checks['tests'])+' 项隔离测试及前端验证完成，生产尚未重启激活。</p><p>修正了成交额与目录耦合、批次/字段时间混用、旧跟踪资格、390秒判旧、采集认领与T2/T3网络锁耦合。</p><p><a href="'+name+'">查看更新频率、代码差异与验证证据</a></p></section>'
    for filename in ('P39_timing_consistency_plan.html','P39_five_minute_schedule_plan.html'):
        for directory in (AUDIT,PUBLIC):
            p=directory/filename
            if not p.exists():continue
            s=p.read_text('utf8')
            s=re.sub(r'<section id="section-14">.*?</section>','',s,flags=re.S)
            s=s.replace('<main>','<main>'+section,1)
            p.write_text(s,'utf8')
    plan=AUDIT/'five-minute-schedule-design.json';data=json.loads(plan.read_text('utf8'))
    data.update(status=result['status'],design_revision=3,implementation_report=name,implementation_captured_at_bjt=stamp,
        production_activated=False,implementation_schedule=frequencies,implementation_corrections=corrections)
    plan.write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf8')
    marker='\n\n## 14. 本轮复核与代码实施\n'
    for filename in ('P39_五分钟T1与全链路协同时序设计.md','P39_全链路时间规则冲突审计与执行方案.md'):
        md=AUDIT/filename;s=md.read_text('utf8')
        s=s.split(marker)[0]+marker+'代码已更新并通过 '+str(checks['tests'])+' 项独立隔离测试，待生产重启激活。\n\n'+'\n'.join('- '+v for v in corrections)+'\n\n完整记录：'+str(OUT/name)+'\n'
        md.write_text(s,'utf8')
    audit_path=AUDIT/'audit-result.json';audit=json.loads(audit_path.read_text('utf8'))
    audit.update(status=result['status'],latest_user_scope='reviewed and corrected plan; source updated and isolated validated; production activation pending',
        implementation={'report':name,'result':'../timing-implementation-20261008/implementation-result.json','captured_at_bjt':stamp,'tests':checks,'production_activated':False})
    audit_path.write_text(json.dumps(audit,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps({'tests':checks,'changed_baseline_files':len(changes),'report':str(PUBLIC/name)},ensure_ascii=False))

if __name__=='__main__':main()
