"""Record completed code validation, without claiming production activation."""
from pathlib import Path
import json,html,xml.etree.ElementTree as ET
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/p39-validation/deadlock-fix-20261008';OUT.mkdir(exist_ok=True)
suite=ET.parse(ROOT/'docs/p39-validation/deadlock-fix-final-tests.xml').getroot().find('testsuite')
assert suite.attrib['failures']=='0' and suite.attrib['errors']=='0'
isolation=json.loads((ROOT/'docs/p39-validation/deadlock-fix-final-test-isolation.json').read_text('utf8'))
assert isolation['production_access'] is False and isolation['exit_code']==0
clusters=sorted((ROOT/'.test-tmp').glob('p39-pg-*'),key=lambda p:p.stat().st_mtime,reverse=True)
logs=[]
for p in clusters:
    f=p/'server.log'
    if not f.exists():continue
    t=f.read_text('utf8',errors='replace')
    if isolation['database'] in t or str(isolation['port']) in t:
        logs.append({'path':str(f),'deadlock_error_blocks':t.count('ERROR:  deadlock detected')});break
assert logs and logs[0]['deadlock_error_blocks']==0,logs
stamp=datetime.now(timezone(timedelta(hours=8))).isoformat()
result={'captured_at_bjt':stamp,'status':'code_validated_not_restarted','tests':int(suite.attrib['tests']),'failures':0,'errors':0,'duration_seconds':suite.attrib['time'],'isolation':isolation,'test_server_logs':logs,'production_restarted':False,'migration_required':False,'periodic_monitoring_resumed':False,'frontend_typecheck':'passed','changes':[
 '业务写入与角色/租约校验后，事务末尾按提交顺序分配并发布事件ID；通知Outbox保持原事务原子性',
 '需求同步/来源写入/结果写回统一按需求ID预留锁；未改变的来源不反复写锁',
 '候选唤醒先筛选新鲜报价和真实变化，最多64条一批；跨界任务传递失败后保留待唤醒标记',
 '40P01/40001整事务新Session有限重试；其他异常不盲目重试',
 '失败批次与未提交队列原样落本地文件，恢复时重放；保留原始时刻/raw_ref/去重键',
 '报价恢复使用原生symbol+事件类型双键，保留较新的数据并遵守容量',
 '数据库心跳/范围维护移出socket接收循环，分片失败仅重建失败分片',
 '合约异动窗口固定mark口径；记录5M/15M覆盖，缺覆盖时不再笼统ok',
 '待核实列表使用instrument_id作React key，保留同名现货和合约',
 '任务异常日志保存堆栈与SQLSTATE，便于激活后确认失败原因'
]}
(OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
rows=''.join('<li>'+html.escape(x)+'</li>' for x in result['changes'])
doc=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 死锁修复验收</title><style>body{{background:#eff3f7;color:#17283c;font:16px/1.8 system-ui,"Microsoft YaHei",sans-serif;margin:0}}main{{max-width:1050px;margin:24px auto;padding:32px;background:white;border-radius:12px}}h1{{font-size:28px}}h2{{font-size:21px;margin-top:28px;border-top:1px solid #dce5ee;padding-top:20px}}li{{margin:9px 0}}code,p,li{{overflow-wrap:anywhere}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;border:1px solid #dce5ee;padding:10px}}th{{background:#eef5ff}}@media(max-width:600px){{main{{padding:18px;margin:0}}h1{{font-size:23px}}}}</style><main>
<h1>P39 · 数据库死锁修复验收</h1><p>记录时间：{html.escape(stamp)}（北京时间）。状态：<strong>代码已修复并通过隔离验证，运行服务尚未重启激活。</strong></p>
<h2>修复内容</h2><ol>{rows}</ol>
<h2>验证结果</h2><table><tr><th>项目</th><th>结果</th></tr><tr><td>最终隔离回归</td><td>{result['tests']} 通过，0失败，0错误</td></tr><tr><td>真实并发覆盖</td><td>需求同步、候选唤醒、趋势结果写回与K线写入并发；事件游标顺序与租约失败回滚</td></tr><tr><td>测试数据库服务端日志</td><td>新增 deadlock ERROR：0</td></tr><tr><td>事实失败与重放</td><td>有限重试、耗尽保留、不提前确认、保留队列、成功重放后删除保留文件</td></tr><tr><td>前端类型检查</td><td>tsc --noEmit 通过</td></tr><tr><td>数据隔离</td><td>全新随机端口 PostgreSQL；无生产复制、无交易所请求</td></tr></table>
<p>关联回归中6项旧断言已对齐当前P39：同输入不因时间变化重算；过期输入返回superseded；独立趋势/结构通道可服务同一原生市场；复查间隔依据分级调度状态。业务选币门槛未因此变更。</p>
<h2>运行与恢复边界</h2><p>本轮未重启运行服务，未修改生产数据或交易配置，未恢复10分钟监控。无需数据库迁移。生产死锁是否消失，需要新代码激活后查看新批次日志及数据增量，不能用隔离通过替代生产结论。</p><p>本地失败记录位于 .runtime/failed-facts，按采集来源/分片隔离。重放按原去重键处理，允许故障恢复中重复尝试，不能承诺故障前未保存的历史强平可补回。异常发生后已保留的批次可重放；突然断电之前尚在内存的事实不属于本次失败保留保障。</p><p>合约mark缺采仍会形成真实窗口缺口，修复保留同一价格口径，不拼接last来伪造连续性。连续运行达到窗口长度后，再验收合格市场的5/15分钟覆盖、OI时效、报价排队年龄、T2/T3推进。</p>
<h2>激活后的有界验收</h2><ol><li>核验新run和进程启动身份，使用当前批次日志，不混入旧死锁。</li><li>核对新死锁为0、事件投影持续更新、任务完成增量推进。</li><li>核对failed-facts恢复情况，失败批次不丢弃，不将累计统计当新增币数。</li><li>核对报价/OI时效与5/15分钟窗口，真实缺口走原有补数/重算流程；不放宽买入规则。</li></ol>
<h2>证据</h2><p>docs/p39-validation/deadlock-fix-final-tests.xml<br>docs/p39-validation/deadlock-fix-final-test-isolation.json<br>docs/p39-validation/deadlock-fix-20261008/result.json<br>tests/test_p39_deadlock_fix.py</p><p><a href="P39_deadlock_investigation.html">原调查与完整根因</a></p></main></html>'''
(OUT/'P39_deadlock_fix_acceptance.html').write_text(doc,'utf8')
(ROOT/'apps/web/public/previews/P39_deadlock_fix_acceptance.html').write_text(doc,'utf8')
print(json.dumps({k:result[k] for k in ('status','tests','test_server_logs','production_restarted')},ensure_ascii=False))
