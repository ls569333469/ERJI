"""Record repair evidence; production access is restricted to schema metadata."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import hashlib,html,json,sys,xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
OUT=ROOT/'docs/p39-validation'
stamp=datetime.now(timezone(timedelta(hours=8))).isoformat()
files=['engine/p38/jobs.py','engine/p38/windows.py','engine/p38/plan_queries.py',
       'engine/pd2/p35_schedule.py','engine/pd2/native_streams.py','engine/pd2/service.py',
       'engine/p37/detector.py','engine/p37/worker.py','engine/p37/store.py','engine/models.py',
       'migrations/versions/p39_snapshot_latest.py','tools/p39_isolated_tests.py','tests/test_p39_startup_fix.py']
checks=[]
for prefix in ('startup-fix-final','startup-fix-migration'):
    root=ET.parse(OUT/(prefix+'-tests.xml')).getroot()
    suites=list(root.iter('testsuite'))
    item={k:sum(int(s.get(k,'0')) for s in suites) for k in ('tests','failures','errors','skipped')}
    item.update(report=prefix+'-tests.xml',isolation=json.loads((OUT/(prefix+'-test-isolation.json')).read_text('utf8')))
    assert item['failures']==item['errors']==0 and item['isolation']['exit_code']==0
    checks.append(item)
schema={}
from sqlalchemy import create_engine,text
from engine.config import settings
db=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=3000 -c lock_timeout=1000'})
try:
    with db.connect() as c:
        schema['migration_version']=c.execute(text('SELECT version_num FROM alembic_version')).scalar()
        schema['latest_snapshot_index']=c.execute(text("SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND indexname='ix_signal_snapshots_plan_latest'")).scalar()
except Exception as exc:schema['read_error']=type(exc).__name__
finally:db.dispose()
manifest={'recorded_at_bjt':stamp,'code_status':'complete','activation_status':'not_restarted_in_this_repair',
          'production_schema':schema,'tests':checks,
          'files':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files},
          'processes':json.loads((ROOT/'.runtime/processes.json').read_text('utf-8-sig')),
          'production_recovery_verified':False,'exchange_requests_from_validation':0}
(OUT/'startup-fix-result.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
changes=[
    ('快慢任务互相挤占','保护、入场、投影、深度各一条执行通道，总并发仍为4，深度初期上限1；通道轮转，同币互斥；移除60秒后快任务优先级失效规则。','旧深度积压优先级回归；生产者阻塞时四类消费仍能启动。'),
    ('单币任务拉全库大JSON','先检查深度名额；单币仅读取Instrument。补窗/盘口依赖读取必要字段，单币不再调用全库execution_assets。','隔离库含1MB无关evidence，依赖查询返回正确集合且SQL不选择完整evidence。'),
    ('入场过期仍排队','有效入场、原保护、投影分别生成集合，最新ended快照也参与准入；轻量结束无名额/过期排队任务，保留租约、历史与重入能力。','过期计划不再被entry领取；保护和历史继续保留；同依赖重获名额可重新入队。'),
    ('提交阻塞消费循环','生产者、消费者、统计独立协程。先比较版本，变化任务最多32条一批更新，使用主键去重和行锁。K线依赖按有界窗口聚合。','真实PostgreSQL并发提交/领取去重；批量window token与单币token一致；取消和旧owner保护回归。'),
    ('最新快照查询缺索引','投影经Plan.execution_instrument_id定位，再取每计划最新ID；新增plan_id/as_of索引和并发创建迁移。','按币过滤与最新ended语义通过；隔离库实际执行并发索引删除、创建和重复创建。'),
    ('报价固定64条小批','接收报价时过滤准入符号，每10秒刷新；按实测提交耗时自适应64–512条一批，目标约1秒，积压时缩短固定等待。记录报价独立指标。','符号过滤、范围变化、原始事件/接收时刻不变和事实队列保全回归；不放宽15秒校验。'),
    ('实时更新取消快任务、失败难定位','深度任务保留desired_version一致性；快任务将新报价合并到下一轮，仍检查owner/租约、K线、配置以及实际报价/盘口有效期。保存受控错误码和阶段。','执行中到达新报价：当前投影完成且保留下一轮需求；配置版本变化：analysis_source_changed；旧owner不得写策略。'),
    ('合约mark价遗漏发现窗口','接入markPriceUpdate；新鲜mark优先，K线继续更新成交额；价格口径切换时重建连续窗口，并在触发依据保留price_kind。','901条合成mark消息形成5/15分钟窗口；过期/未知币丢弃；mark/last切换不造成假连续；原BTC降噪回归通过。'),
]
lines=['# P39 启动后阻塞修复与验证记录','',f'记录时间：{stamp}（北京时间）。','',
       '代码修复与隔离验证已完成；本轮未重启现有服务，未在生产执行新索引迁移，尚不能宣称运行数据已经恢复。','',
       '原问题依据为2026-10-06本次启动后13:18–13:33的采样。凌晨01:06是关机前历史记录，关机空档不计入故障。','',
       '## 修复范围','', '| 问题 | 修改 | 验证 |','|---|---|---|']
lines.extend('| '+' | '.join(row)+' |' for row in changes)
lines+=['','## 测试证据','',
        '- 完整后端回归：411项通过，0失败，26.44秒；startup-fix-final-tests.xml。',
        '- 增加迁移检查后的专项回归：13项通过，0失败；startup-fix-migration-tests.xml。这13项与全套有12项重叠，共覆盖412个不同测试。',
        '- 两次验证均使用新建隔离PostgreSQL cluster与测试库；未复制生产数据，未额外请求交易所。',
        '- 首轮全套的两处SQLAlchemy转换错误已修正；保留失败及成功XML，不覆盖原P39的399项历史证据。',
        '- 原策略、保护、不可变历史、K线内容、租约和通知权限回归通过；仅有现有FastAPI TestClient依赖弃用警告。',
        '- 前端文件未在本轮更改，因此本轮没有重建前端。','',
        '## 激活与运行验收','',f'- 本次只读生产元数据：迁移版本 `{schema.get("migration_version","未读取")}`；最新快照索引 `{schema.get("latest_snapshot_index") or "尚未检出"}`。',
        '- 后续激活需先使用实际启动环境执行 alembic upgrade head，再重启 api、live、jobs、streams。budget/web没有本轮必需的代码更新。现有按角色restart脚本不会自动迁移，不能只重启漏掉索引。',
        '- 新索引迁移采用CONCURRENTLY，保留业务数据；本轮在隔离库验证了实际DDL，尚未在生产执行。',
        '- 激活后比较同一启动批次的entry/projection完成增量、投影时刻、protect持续推进、各通道占用和producer.batch_ms。',
        '- 检查报价quote_writer.pending/committed/skipped_write/write_p95_ms/oldest_pending_age_ms、15秒内行情覆盖、OI90秒与6分钟覆盖和连续5/15分钟发现窗口。',
        '- 5/15分钟发现窗口需真实连续采样重新积累；历史K线不能伪造重启期间的内存实时连续性。',
        '- 测试通过不等于生产恢复；是否有可以买的代币仍由当前市场事实和原策略决定。','',
        '## 边界与未验证项','',
        '- 不放宽异动、流动性、BTC降噪、买入、新鲜度门槛，不启用交易，不删除历史证据，不恢复旧定时监控。',
        '- 深度初期并发1，快任务各保留1；没有实现闲置通道借用。需先观察真实运行耗时，再决定是否增加复杂调度。',
        '- 新报价批次目标约1秒是控制目标，实际数据库锁等待可能超过目标；本轮不能给出真实吞吐提升倍数。',
        '- 本轮未改变稳定非买入状态的快照去重语义。其与90秒展示新鲜度的关系应在激活后的投影验收检查；不靠刷新旧时间戳把旧结论包装成新判断。',
        '- OI采集未在本轮重写，现有覆盖缺口须激活后单独核验。',
        '- 本轮修复前8个关键文件备份在startup-fix-before；最终文件SHA256、进程注册记录与只读schema状态在startup-fix-result.json。','',
        '## 修改文件','']
lines.extend('- `'+f+'`' for f in files)
md='\n'.join(lines)+'\n';title='P39_启动后阻塞修复与验证记录'
(OUT/(title+'.md')).write_text(md,encoding='utf8')
rows=''.join('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in row)+'</tr>' for row in changes)
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 启动后阻塞修复与验证记录</title><style>body{margin:0;background:#f4f6fa;color:#1f2937;font:16px/1.7 "Microsoft YaHei",sans-serif}main{max-width:1150px;margin:36px auto;padding:32px;background:white;border-radius:16px}h1{font-size:28px}h2{font-size:21px}p{margin:12px 0}.status{background:#eaf5ee;border-left:4px solid #198754;padding:18px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:14px;text-align:left;vertical-align:top;border-bottom:1px solid #e5e7eb}th{background:#f1f5f9}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.8 "Microsoft YaHei",sans-serif}a{color:#2563eb}</style><main>'''
page+='<h1>P39 · 启动后阻塞修复</h1><p>'+html.escape(stamp)+'（北京时间）</p><div class="status">代码已修复；完整回归411项通过，新增迁移专项13项通过。<br><strong>尚未重启激活，生产恢复未验证。</strong></div><h2>8项问题的修复与验证</h2><table><thead><tr><th>问题</th><th>修改</th><th>验证</th></tr></thead><tbody>'+rows+'</tbody></table><h2>完整记录与运行验收要求</h2><pre>'+html.escape(md)+'</pre><p><a href="startup-fix-result.json">结果及代码指纹</a> · <a href="startup-fix-final-tests.xml">完整测试</a> · <a href="startup-fix-migration-tests.xml">迁移专项</a></p></main></html>'
(OUT/(title+'.html')).write_text(page,encoding='utf8')
print(json.dumps({'report':str(OUT/(title+'.html')),'tests':checks,'production_schema':schema},ensure_ascii=False))
