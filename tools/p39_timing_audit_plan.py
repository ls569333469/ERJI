"""Build a static timing audit plan from saved local evidence; no DB/network writes."""
from pathlib import Path
import hashlib
import html
import json
import re

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/p39-validation/timing-audit-20261008'
EVIDENCE = ROOT / 'docs/p39-validation/deadlock-fix-20261008/activation'


def read_json(path):
    return json.loads(path.read_text('utf-8-sig'))


def ref(path, needle):
    lines = (ROOT / path).read_text('utf-8-sig').splitlines()
    matches = [(n, line.strip()) for n, line in enumerate(lines, 1) if needle in line]
    if not matches:
        raise ValueError(f'Missing source evidence: {path}: {needle}')
    n, snippet = matches[0]
    return {'path': path, 'line': n, 'snippet': snippet}


def issue(ident, title, status, current, impact, solution, refs):
    return dict(id=ident, title=title, evidence_level=status, current=current,
                impact=impact, solution=solution, source=[ref(*r) for r in refs])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    audit = read_json(EVIDENCE / 'timing-audit.json')
    recovery = read_json(EVIDENCE / 'recovery-detail.json')
    snapshot_dir = OUT / 'evidence'
    snapshot_dir.mkdir(exist_ok=True)
    evidence_paths = list(EVIDENCE.glob('timing-audit*')) + [EVIDENCE / 'recovery-detail.json']
    for evidence_path in evidence_paths:
        if evidence_path.is_file():
            (snapshot_dir / evidence_path.name).write_bytes(evidence_path.read_bytes())
    owner = next(b['details'] for b in audit['queries']['beats'] if b['role'] == 'analysis_owner')
    issues = [
        issue('T01', '合约成交额：30分钟生产，6分钟消费期限', '运行复现',
              '合约目录与24小时成交额一同每1800秒刷新；T1在当前配置下仅接受360秒内的成交额。价格流与单币OI更新不会刷新该成交额时钟。',
              '17:42采样的522条合格USDT合约全部成交额过期，T1将其liquidity设为None；这些市场被排除出异动检查，而不是没有价格或没有涨幅。',
              '将成交额从目录和OI轮次拆出，使用批量ticker或经官方路由/字段/权重复核的Binance ticker流。目录与成交额各自定周期；具体刷新与时效先按本轮参数清单讨论，按自己的source/received时间判断，禁止重贴当前时间。',
              [('engine/adapters/binance_universe.py', '>= 1800_000'), ('engine/p37/store.py', 'max_age=max(360000'), ('engine/p37/store.py', "liquidity=float(v)")]),
        issue('T02', 'OI：目标10/15/60秒，实际交付多分钟', '运行复现',
              '普通60秒、候选15秒、重点10秒是due调度目标，不代表实际送达。当前连续采集、请求预算、数据库和优先级扫描共用部分资源。',
              '560条合格合约中，90秒内OI仅101条、6分钟内395条；采集访问年龄中位数216.4秒。已有延迟，但各阶段占比仍需埋点归因，不能全部归咎于网络或限流。',
              '先拆解到期→调度→预算→HTTP→落库耗时；把优先级全量扫描移出持锁区并缓存membership版本。T2候选/近买点与普通冷数据分级分配预算；现有10/15秒高频设置不作为必需标准，降低频率与消费者时效成套讨论；不盲目提高并发、不绕过429/418。',
              [('engine/pd2/collection_schedule.py', 'OI_INTERVALS ='), ('engine/pd2/collection_schedule.py', 'def due('), ('engine/adapters/binance_universe.py', 'async def collect')]),
        issue('T03', 'OI同名状态：90秒、180秒、360秒混用', '确定逻辑差异',
              '实时OI异动/观察15分钟变化用90秒，历史面板用180秒，当前参考OI用360秒；历史端点容差又有60秒/300秒。',
              '实时触发与参考展示可以有不同期限；问题是同名fresh/stale没有用途，页面和API难以对应。稀疏采点会使15分钟变化缺起点，不能用当前值补造。',
              '共用按用途命名的时间策略：oi_trigger、oi_reference、oi_history_endpoint。API携带真实起止时间、跨度和有效状态，同用途跨接口一致；修采集间隔后再评估端点容差，禁止插值制造策略证据。',
              [('engine/pd2/current_market_fields.py', 'OI_MAX_AGE='), ('engine/pd2/current_market_fields.py', 'CHANGE_MAX_AGE='), ('engine/pd2/service.py', '"oi_status":"fresh" if oi')]),
        issue('T04', '通用采集配置连带改变价格/OI/成交额时效', '确定逻辑耦合',
              'list_markets和T1 catalog用max(6分钟, collector_interval_seconds×3)，并将同一值用于多个数据用途与历史端点容差。',
              '调整普通采集频率会同时改变其他字段的过期判断，导致不同入口对同一值给出不同状态。',
              '采集频率与有效期限分开配置，时效按字段和用途明确；先迁移现有有效值，所有生产者/消费者读取同一policy，避免一次更改扩大买入资格。扩展已有p38.configuration.effective，不另造第二套配置中心。',
              [('engine/repository.py', 'max_age=max(360_000'), ('engine/p37/store.py', 'max_age=max(360000'), ('engine/p38/configuration.py', 'def effective()')]),
        issue('T05', '资金费：同一已结算费率的宽限3/5分钟不一致', '确定逻辑差异',
              'current字段/衍生品聚合按实际结算周期+180秒；markets接口按结算周期+300秒。',
              '结算边界附近同一个已结算费率可能一个接口有效、另一个过期；不同于实时预测资金费。',
              '同一种已结算费率共用边界判定；分别保存settled_at、received_at、next_settlement_at（取得时）与funding_kind。旧结算值保留其真实时间，边界优先刷新；不把已结算值当预测值，也不用于成本硬拦截。',
              [('engine/pd2/derivatives.py', 'interval*1000+180000'), ('engine/repository.py', '(interval+300)*1000'), ('engine/pd2/current_market_fields.py', 'def funding_clock')]),
        issue('T06', '资金费历史：30分钟刷新，3分钟后尾部未知', '确定逻辑错配',
              '普通funding_history每30分钟调度；settled_costs要求observed_through距当前不超过180秒才确认尾部。重点历史2分钟刷新。',
              '普通市场一轮中约27分钟的参考成本尾部可能变成未知；这是参考信息质量问题，未证明它正在阻止买入。',
              '按实际结算边界判断历史尾部是否已覆盖，不机械要求每3分钟重查一次没有新结算的历史。边界到达时优先更新；未知仅标在参考字段，不改变策略准入。',
              [('engine/pd2/collection_schedule.py', "kind=='funding_history'"), ('engine/pd2/derivatives.py', 'def settled_costs'), ('engine/pd2/derivatives.py', 'tail_known=')]),
        issue('T07', 'K线：新收盘边界，趋势立即延迟、结构给3分钟', '确定逻辑差异',
              '趋势在expected close边界立即判旧结果delayed；结构按周期+180秒处理；data_health又要求observed≥expected。',
              '刚收盘尚未送达的正常补数阶段会被不同模块解释为不同故障，重复唤醒或等待。',
              '共享expected_closed_at，按UTC日线及周一UTC周线锚点计算；统一pending_close/补数中/真实缺口。3分钟作为现有结构补数余量起点进行验证，不能将未收盘K线当已收盘，也不能用旧输入满足新策略确认。',
              [('engine/p37/analysis.py', "rows[-1]['close_time']<now//step*step"), ('engine/pd2/structural_history.py', 'DURATIONS[tf]+180000'), ('engine/pd2/data_health.py', 'current=at is not None'), ('engine/repository.py', "offset=345600000 if resolution=='1W'")]),
        issue('T08', '分析结果：5分钟复查与6.5分钟一刀切过期', '逻辑差异及运行延迟',
              '成功分析300秒后复查，waiting/error60秒后复查；观察接口按checked/analyzed时间390秒判delayed，与依赖K线是否变化无关。',
              '结构队列近期P95为583秒，高于390秒。日周输入没有变化也可能只因排队而被标延迟；不等同于其策略计划过期。',
              '依据输入版本、最新已收盘K线及策略自身失效条件判断可用性；computed_at、last_checked_at、next_review_at分开。输入变更立即唤醒，输入未变复用；waiting_inputs在真实输入就绪时唤醒并有界退避，不刷新计算时间伪装重算。',
              [('engine/p39/pipeline.py', 'state.next_check_at = at +'), ('engine/p38/observation_query.py', 'now-checked<=390000'), ('engine/p38/jobs.py', "p['next_review_at']=at+300000")]),
        issue('T09', '观察可买计数：普通缓存30秒，却要求15秒内', '确定逻辑错配',
              '普通tracking每30秒刷新、重点5秒；观察可买状态/计数同时依赖cached buy_eligible和tracked_at≤15秒，以及当前信号与报价。',
              '当市场未进入重点刷新且当前信号成立时，旧观察缓存可以压掉可买计数。尚未定位到真实漏买币，不能把这个条件风险当成已证明的漏信号。',
              '观察与买入页共用当下CurrentSignal+真实报价+原买入区间的资格函数；tracking仅供历史涨幅/走势和统计，不再作为第二个缓存许可条件。保留原策略/保护/失效条件。',
              [('engine/p37/store.py', 'interval=case('), ('engine/p38/observation_query.py', "row['buy_eligible']=bool"), ('engine/p38/observation_query.py', 'tracked=func.coalesce')]),
        issue('T10', '快速买入与重分析：不同lane仍有同币串行', '确定架构耦合',
              '已有trend/structure/entry/protect/projection独立lane；scheduler仍排除所有进行中subject，同币执行fence为p38_execute:<iid>。报价15秒、盘口接收10秒有效。',
              '结构执行近期P95为24秒；同币重任务可能耽误快速判定。当前entry无排队/耗时样本，不能据此声称已遗漏一条真买入。protect/projection确有多分钟排队。',
              'T2读快照和计算移到短提交保护之外；T3只读取已确认结构+最新盘口做轻检查，由新报价/跨区间事件唤醒并合并。提交时检查输入版本，变更则作废旧结果；保留必要写入互斥和防重复发布。低频产品的盘口TTL可以配套讨论，但不能仅改TTL而留下慢队列。',
              [('engine/p38/jobs.py', "key='p38_execute:'+iid"), ('engine/p38/jobs.py', 'exclude_subjects=excluded'), ('engine/p38/p33_current.py', 'valid_until=min(plan.expires_at'), ('engine/pd2/signal_projection.py', "'received_at', 0) + 10000")]),
        issue('T11', '健康摘要：心跳新鲜与数据进展被混在一起', '确定逻辑缺口',
              'health与operations使用不同默认心跳期限；overall主要看必需role心跳及error/reconnecting，partial仍可能overall ok。supervisor重连与receiver连接状态分开，旧批次残留需要按身份过滤。',
              '进程仍活并不代表OI、成交额、异动窗口正常；历史reconnecting又可能在已有接收时误导。',
              '统一role心跳deadline，按当前run/PID身份读取；分别汇总进程存活、数据持续进展和合格集合覆盖。receiver成功后更新同一supervisor状态。单币有效数据继续按原规则运行，不因全局partial新增一刀切禁止。',
              [('services/api/main.py', 'ttl=expected.get(role,300_000)'), ('services/api/main.py', 'overall="ok" if all'), ('engine/pd2/native_streams.py', 'reconnecting')]),
    ]
    risks = [
        issue('R01', '采集预留4分钟，预算队列最长7分钟', '待验证风险',
              '采集due以240秒预留防重复，budget QUEUE_SECONDS=420。单dispatcher的pending字典已有保护。',
              '不能断言已经发了重复HTTP；重启/多consumer/长等待可能出现预留先到期。',
              '将owner inflight租约和失败重试next_retry分开；预算等待/HTTP/提交的总deadline明确，进行中续租，取消/完成显式释放。先用trace证明所有者边界。',
              [('engine/pd2/derivative_collection.py', 'capacity,240000'), ('engine/pd2/collection_schedule.py', "retries[n]={'until'"), ('engine/pd2/p36_budget.py', 'QUEUE_SECONDS=420')]),
        issue('R02', '前端30秒兜底刷新与10秒买入窗口', '待验证风险',
              'SSE信号事件1秒合并刷新，兜底轮询30秒；页面已有每秒valid_until隐藏，分页每页8秒，整批加载完再显示。',
              '不能说过期信号仍显示30秒；风险是SSE丢事件/无进展或多页加载时，短窗口可能在显示前结束。',
              '现在可买小列表/增量接口先返回，与等待计划慢列表分开；仅近买点、有可买或SSE落后时提高轻量刷新频率，并使用服务端as_of校时。先测事件到达→API→可见延迟。保留原详情/K线格式。',
              [('apps/web/src/lib/use-refresh.ts', 'setBusy,30000'), ('apps/web/src/components/opportunity-board.tsx', 'timeoutMs:8000'), ('apps/web/src/components/opportunity-board.tsx', 'setNow(Date.now()),1000'), ('apps/web/src/components/opportunity-board.tsx', 'refresh()},1000')]),
        issue('R03', '历史OI整点边界与周线缓存锚点', '待验证风险',
              '历史OI按当前整点/5分钟格点期待端点，需核实供应商发布时延；1小时73点每6小时seed，其间5分钟500点修补。部分周线缓存signature按epoch周计算，K线按周一UTC。',
              '可能造成短暂incomplete或额外缓存重算；未证明原始K线数据错误，也未核实Binance发布时延。',
              '先核官方timestamp语义及本地raw接收证据，区分待发布与真缺口；周线所有expected/cache key共用UTC周一锚点。不得补造OI或让未来数据重写历史决策。',
              [('engine/pd2/derivative_collection.py', 'def recent_coverage'), ('engine/pd2/derivative_collection.py', 'def history_request'), ('engine/pd2/p35_schedule.py', 'now//(604800000')]),
    ]
    scanned = []
    inventory = []
    pattern = re.compile(r'(interval|timeout|ttl|lease|renew|retry|backoff|expires|valid_until|next_check|next_review|fresh|stale|as_of|received_at|closed_at|expected_close|sleep|setInterval|setTimeout)', re.I)
    excluded = ('__pycache__', 'node_modules', '.next', 'tests', 'test', 'generated')
    for base in ('engine', 'services', 'apps/web/src'):
        for path in sorted((ROOT / base).rglob('*')):
            if path.suffix not in ('.py', '.ts', '.tsx') or any(p in excluded for p in path.parts):
                continue
            rel = path.relative_to(ROOT).as_posix()
            content = path.read_text('utf-8-sig')
            scanned.append({'path': rel, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
            for n, line in enumerate(content.splitlines(), 1):
                if pattern.search(line) and re.search(r'\d', line):
                    inventory.append({'path': rel, 'line': n, 'text': line.strip()})
    result = dict(title='P39 · 全链路时间规则冲突审计与执行方案', run_id=audit['run_id'],
                  evidence_time_bjt=audit['captured_at_bjt'], mode='inspection_and_plan_only',
                  business_changes=False, monitor='PAUSED', issues=issues, risks=risks,
                  inventory_scope={'roots':['engine','services','apps/web/src'], 'files':len(scanned),
                                   'numeric_time_lines':len(inventory), 'excluded':list(excluded),
                                   'method':'关键字+数值扫描；关键传递链人工核对。清单命中不是故障数量，非形式化证明。'},
                  runtime={'audit':audit['phase'], 'ports':audit['ports'],
                           'postgres_deadlock_errors':audit['postgres_new_deadlock_errors'],
                           'postgres_error_heads':audit['postgres_error_heads'],
                           'source':audit['queries']['source'], 'liquidity':recovery['liquidity'],
                           'oi':recovery['oi'], 'discovery':[b for b in audit['queries']['beats'] if b['role']=='anomaly_discovery'],
                           'owner':owner},
                  evidence_files=['evidence/' + p.name for p in evidence_paths if p.is_file()])
    (OUT/'audit-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2), 'utf-8')
    (OUT/'time-rule-inventory.json').write_text(json.dumps({'scope':result['inventory_scope'],'files':scanned,'matches':inventory},ensure_ascii=False,indent=2), 'utf-8')
    metrics = [
        ['运行批次', audit['run_id'], '当前启动约141分钟；运行证据采样时刻固定'],
        ['服务', '18770 / 18771 / 18772 / 55432开放；本地HTTP 200', '服务存活不等于数据全正常'],
        ['新死锁 / PostgreSQL ERROR', f"{audit['postgres_new_deadlock_errors']} / {len(audit['postgres_error_heads'])}", '本次run的已读取日志；现场仍有advisory锁等待'],
        ['合约成交额', '522 / 522条过期', '本次合格USDT合约集合；T1资格直接受影响'],
        ['合约OI', '90秒内101 / 560；6分钟内395 / 560', '560为合格活动合约；563为原market_state记录，不能混分母'],
        ['T1合格窗口', '5分钟66 / 201；15分钟47 / 201', '同次discovery心跳；全目录589 / 1020、570 / 1020不能替代该覆盖率'],
        ['趋势 / 结构排队P95', '149.3秒 / 583.0秒', '近20分钟每subject/kind最新完成样本，不是独立新增币吞吐'],
        ['保护 / 投影排队P95', '368.5秒 / 525.9秒', '实际排队已存在；累计完成/失败数不当成本run新增'],
        ['结构执行P95 / producer一轮', '24.0秒 / 8.6秒', '重分析耗时超过盘口10秒有效期；跨lane同币等待需拆分'],
        ['当前entry样本', '排队0；近20分钟耗时样本0', '不能推断已经漏掉某个可买币'],
    ]
    parts = ['# P39 · 全链路时间规则冲突审计与执行方案',
             f"证据时间：2026-10-08 17:42（北京时间）｜批次 `{audit['run_id']}`｜检查与方案阶段", 
             '当前先检查与统计全部时间参数及其逻辑关系，再讨论调整；不决定或激活新数值。用户已确定的策略成立、到达买入区间后进入买入推荐；成本、盈亏比和上涨空间是参考。现有10/15秒高频不是必须保留的标准，降低频率要与消费时效、任务等待和页面刷新成套讨论。',
             '## 1. 当前结论与范围',
             '死锁修复后，本次run新死锁为0，但系统数据尚未全部正常。最明确的阻断点是合约成交额刷新晚于T1消费期限，其次是OI实际交付延迟、分析复查与显示判旧错配。锁等待仍会增加耗时；它与死锁、业务时间规则是不同问题，不能用删除全部锁解决。',
             f"扫描 engine、services、apps/web/src 的 {len(scanned)} 个Python/TypeScript文件，提取 {len(inventory)} 条带数值的时间规则候选，再人工核对采集→T1→T2→T3→投影→API→页面以及预算、心跳、重试路径。命中数不是问题数，扫描不保证捕获所有间接或动态时间规则。已关闭的外源兼容代码不作为当前活动原因。", 
             '本轮仅保存诊断证据和方案文档，未修改业务代码、配置、生产数据，未重启。10分钟自动监控保持暂停。',
             '## 2. 当前运行证据', table(['项目','采样结果','解释范围'], metrics),
             '注册PID及启动ticks已核对；采样源文件与启动manifest记录的对应哈希一致。source quote数据显示合约15秒内报价560条，而成交额522条过期，因此不能把T1合约缺席解释成市场没有行情。',
             '## 3. 逐项冲突与处理']
    for item in issues:
        parts += issue_md(item)
    parts += ['## 4. 尚需验证的风险']
    for item in risks:
        parts += issue_md(item)
    parts += [
        '## 5. 重新梳理后的数据传递',
        'Binance价格/成交额/实时OI → T1轻量异动检查 → 按市场+分析类型合并T2需求 → 已收盘K线结构/趋势结果 → T3近买点轻量资格检查 → 当前可买投影 → API与前端。市值、参考OI、资金费、成本和目标空间从旁路加入展示；不把参考字段的未知或延迟变成未经约定的买入硬门槛。',
        'T1不等待全市场结构完成，修复成交额之后才有正确合约检查集合；T2只对真实需求进行结构/趋势分析，输入未变复用，已有多lane保留；T3不再等待同币冷历史和重计算，应在新报价、入区间、关键收盘/结果变更后及时复核。失效或离开区间时按原规则退出当前可买，关联计划和历史记录仍保留。',
        '## 6. 时间规则统一入口',
        '建议新增engine/pd2/time_policy.py（纯函数），扩展现有engine/p38/configuration.py.effective展示实际值。先收敛消费者，不增加新配置页面。相同用途共用判定，不同用途保留差异。',
        table(['字段 / 时钟','含义','使用方式'], [
            ['source_at / event_time','供应商数据对应的事实时间','价格、OI、成交额分别有自己的时钟'],
            ['received_at / known_at','本机接收及决策时可知时间','落库/历史证据，不因补数改写过去'],
            ['closed_at / expected_closed_at','真实K线收盘 / 当前应有收盘','UTC锚点，待到达与真缺口分开'],
            ['computed_at / checked_at','重计算 / 复核时刻','复用缓存不改重计算时间'],
            ['next_review_at / next_retry_at','复查 / 失败重试计划','依赖就绪优先事件唤醒，轮询兜底'],
            ['lease_until / owner/version','任务所有者及其预留期限','覆盖等待阶段并续租，完成释放'],
            ['plan_expires_at / valid_until','策略寿命 / 当前资格有效期','分开；盘口过期不能删除策略计划'],
            ['server_as_of / frontend_now','接口计算 / 页面显示时间','服务端校时，过期隐藏与数据拉取分开'],
        ]),
        '上一版保留价格15秒、盘口10秒、OI10/15/60秒的数值建议不再作为确定实施标准。当前只保留原始现状用于追溯；本轮新增参数清单及逻辑关系见第10节。具体低频周期、对应有效期及原计划寿命须分开讨论，不能只改一个数字留下旧硬编码。',
        '## 7. 执行顺序与改动文件',
        table(['批次','具体工作','主要文件 / 交付'], [
            ['A · 先恢复T1输入','独立合约成交额采集；OI分阶段耗时；调度membership扫描出锁，热点与冷数据分配预算','binance_universe / native_streams / collection_schedule；T1成交额覆盖和真实OI送达证据'],
            ['B · 收敛状态判定','同用途时间policy；通用collector设置与字段时效解绑；资金费边界、K线expected close及OI真实跨度统一','time_policy / current_market_fields / derivatives / repository / data_health / structural_history / p37.analysis'],
            ['C · 修T2→T3传递','输入版本唤醒；取消计算时间一刀切过期；缩短同币互斥至关键提交；观察可买直读当前资格','p39.pipeline / p38.jobs / p38.observation_query / p33_current / signal_projection'],
            ['D · 保证页面看得到','快买列表与等待计划分离加载；SSE事件进展与短窗口刷新；健康摘要按当前run和用途输出','API / use-refresh / opportunity-board；保留详情与K线内容格式'],
            ['E · 激活与实测','隔离验证后在用户授权激活时重启；按新run核对事实时钟、延迟和覆盖，保留可回滚记录','动态验证记录；不自动恢复持续监控'],
        ]),
        '各批次独立提交、独立核对，先完成A再观察合约是否重新进入T1，避免同时修改很多时钟后无法归因。现阶段没有必须删除历史数据的迁移；若lease/诊断确需新增字段，先给出向后兼容迁移及回滚，不能在写代码时顺便清空任务、计划或行情。',
        '## 8. 工程验证方式（不是选币门槛）',
        '1. 用同一now与同一原始字段检查各API：同用途状态应一致，不同用途能解释其真实时间。覆盖收盘前后、UTC日/周边界、资金费结算前后和重启暖机。',
        '2. 分阶段记录due→预算→请求→写入→T1发现→T2结果→T3发布→页面可见的延迟，区分队列等待和执行；累计次数与新增独立市场分开计数。',
        '3. 在隔离测试中构造原策略成立/进入区间/离开区间/真实数据过期的事件，确认进入和退出符合原规则；只做工程自测，不向生产制造买入。真实市场没有合格信号不算测试失败。',
        '4. 核查合约成交额能在独立周期更新、T1合格集合不因目录30分钟时钟周期性消失，OI按层级报告送达覆盖；异动5/15分钟窗口按合格集合核算，不用全目录分母冒充恢复。',
        '5. 验证重分析耗时不再占住同币T3轻检查，提交仍可防旧版本覆盖、新owner丢失和重复发布。前端SSE正常/断开/无进展、多页加载都验证短窗口可见；已有每秒过期隐藏继续工作。',
        '## 9. 保留、回滚与证据',
        '保留必要的短事务写入互斥、任务owner续租、防重复提交、交易所请求预算与Retry-After、真实K线连续性和历史可知时间。避免重复全局扫描、长时间持连接和锁内计算；不是取消数据库一致性。FX每60秒刷新/1小时TTL、供应量6小时刷新/24小时TTL暂未发现周期冲突，保留。',
        '不把全部时效改成30分钟，不伪造source_at/received_at，不插值制造OI，不用未闭合K线确认策略，不把成本/盈亏比重新变成硬门槛。回滚按批次恢复代码/必要配置，真实行情、事件与计划继续保留。',
        '证据文件：本目录audit-result.json、time-rule-inventory.json；evidence子目录保留本次timing-audit.json、timing-audit-*-api.json、recovery-detail.json及日志增量副本，避免后续重查覆盖本次基线。原始采样在deadlock-fix-20261008/activation。方案中的数字是2026-10-08 17:42快照，非持续实时看板。尚未逐项证明某个代币的漏买原因，后续应按实际事件ID追溯。',
    ]
    from tools.p39_timing_parameter_plan import build
    parameter_md, parameter_result = build(ROOT, OUT, ref, table)
    from tools.p39_timing_layer_review import build as build_layer_review
    layer_md, layer_full_md, layer_result = build_layer_review(ROOT, OUT, ref, table)
    result['parameter_design']={k:parameter_result[k] for k in ('captured_at','scope','profile_status','candidate_profile_status')}
    result['layer_review']={k:layer_result[k] for k in ('captured_at','source_hash_check','review_coverage','evidence_note')}
    result['layer_review']['findings']=len(layer_result['findings'])
    result['latest_user_scope']='document user direction: initialization separated from 5-minute ordinary T1 scans; T2/T3 independent; no runtime changes in this discussion'
    result['user_direction']={'ordinary_t1_scan_seconds':300,'ordinary_oi_seconds':300,'ordinary_volume_seconds':300,'ordinary_tracking_seconds':300,'status':'documented_not_implemented','initialization_separate':True,'t2_t3_independent':True,'supporting_new_intervals_confirmed':False,'between_scan_replay':'proposed_requires_retention_and_replay_changes'}
    from tools.p39_five_minute_schedule_plan import build as build_schedule
    schedule_md, schedule_result = build_schedule(ROOT, OUT, ref, table)
    result['five_minute_schedule']={k:schedule_result[k] for k in ('captured_at','status','design_revision','reference_snapshot','ordinary_t1_interval_seconds','ordinary_oi_seconds','ordinary_volume_seconds','ordinary_tracking_seconds','reference_age_seconds','oi_trigger_age_seconds','ordinary_batch_target_seconds','supersedes','cadence','execution_time_note','capacity_estimates')}
    result['latest_user_scope']='optimize P39 plan: ordinary OI/volume/T1/tracking unified300s; prioritize T2/T3; reconcile all timing consumers, batch capacity and failures; document only'
    (OUT/'audit-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2), 'utf-8')
    parts.append(parameter_md)
    parts.append(layer_md)
    parts.extend([
        '## 12. 本轮讨论更新：初始化、5分钟T1扫描、独立T2/T3',
        '用户提出普通T1在初始数据准备后每5分钟扫描一次，已触发或满足分级要求的币单独处理；后续又明确普通OI和成交额也统一五分钟。第13节第2版为最新配套设计，覆盖本节原先只讨论扫描的安排；当前程序仍运行原周期。',
        table(['阶段','后续安排','数据传递与职责'],[
            ['启动初始化','读取本地存量、核验缺口并恢复任务；初始化与常态扫描分开','已有K线、观察记录、OI点、需求和结果可以复用；关机期间没有的连续价格样本不能由旧K线代替'],
            ['行情接收','保持独立接收，不随T1改成5分钟拉取一次','持续留存真实行情时刻，为5/15分钟窗口提供证据；实时采集和异动评估分开'],
            ['普通T1全盘扫描','目标每5分钟一轮','全盘只做异动筛选，不对普通全目录重复做结构/趋势分析；新合格异动本轮写观察事件和T2需求'],
            ['已触发币 / T2','独立需求队列；不等待下一轮T1全盘扫描','合并同币需求、读取输入版本、复用未变结果；新K线/依赖就绪/任务完成驱动处理，具体检查频率另定'],
            ['T3当前买入','独立轻量复核与发布','已有确认计划结合当前价格/盘口处理，沿用原策略；不绑定T1五分钟节拍'],
            ['观察更新 / 页面 / 心跳','独立于T1检测调度','不能把tracking、健康上报、目录/OI载入一并延后5分钟；页面显示本轮扫描和处理状态'],
        ]),
        '### 12.1 关机后恢复的实际情况',
        '数据保存在本机PostgreSQL，不是所有数据只存在内存。seed_memory会恢复每币近期5分钟K线，seed_oi_memory会恢复最近16分钟真实OI点；价格deque重新建立，因此5/15分钟连续价格条件在重启后分别需要重新积累对应时长。某个价格窗口未准备好，不应让已有可恢复的需求、结构结果和其他已就绪事实全部等待。',
        '**代码依据：**'+ '；'.join('`'+r['path']+':'+str(r['line'])+'`' for r in [ref('engine/p37/worker.py','def seed_memory'),ref('engine/p37/worker.py','def seed_oi_memory'),ref('engine/p37/detector.py','deque(maxlen=1000)'),ref('engine/p37/worker.py','async def analyze_loop')]),
        '### 12.2 五分钟扫描的准确含义',
        '建议按相邻两次扫描开始的目标间隔300秒调度，例如10:00、10:05、10:10；不是每輪扫描完成后再额外睡5分钟。单轮超过5分钟时不重叠启动，也不补跑多轮陈旧全盘任务；记录超时后合并到最新扫描。该调度方式是实现建议。',
        '首次发现通常比现在晚，五分钟等待之外还要加实际采集、扫描/落库耗时；T2收到需求后立即进入独立排队，不再额外等一个T1周期。扫描目标间隔不是完整全目录采集/计算耗时，也不是行情数据有效期。',
        '若只调用现有evaluate检查扫描时刻，扫描间曾成立但后来消失的异动可能被漏掉。建议批次补查上次成功检查后出现的真实触发证据，保留首次证据时刻与本次发现时刻；这需要新增回查逻辑，并不由调整sleep自动实现。延后发现的历史异动可以送入T2，但不能自动认定当前仍然可买。',
        '回查最近5分钟内任一时刻的15分钟价格窗口，至少需要保留20分钟价格样本，再加扫描/写入余量。当前每币最多1000点，1秒采样时约16分40秒，不足以保证完整回查；应按时间保留并设置合理容量，连同OI端点和成交量证据一起核对，不能补造关机空档。完整回查方案待进一步讨论。',
        '### 12.3 代码改造必须拆开的职责',
        'discover目前同一循环里包含目录载入、OI点载入、evaluate/persist、refresh_tracking和heartbeat；analyze_loop已有独立T2调度。应把全盘检测节拍从前述共享循环抽出，补数、行情接收、观察跟踪与心跳独立推进，T2需求提交/唤醒不依赖下一次全盘扫描。',
        '五分钟是本次提出的普通T1检测方向，不代表将事实TTL统一延长到五分钟。成交额30分钟更新/6分钟消费、观察缓存30秒/可买判定15秒、结果390秒判旧等已确认错配仍需各自解决。T2/T3提速不是必须，但必须独立处理，不能被普通T1降频连带减速。',
        '本轮交付为讨论方案：未修改业务代码、运行配置、生产数据或服务，自动监控保持暂停。',
    ])
    parts.append(schedule_md)
    md = '\n\n'.join(parts) + '\n'
    (OUT/'P39_全链路时间规则冲突审计与执行方案.md').write_text(md, 'utf-8')
    body = render_md(md)
    page = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 · 全链路时间规则冲突审计与执行方案</title><style>
    :root{color-scheme:light;--ink:#172c3f;--muted:#556b7b;--line:#dae5ec}*{box-sizing:border-box}body{margin:0;background:#eef3f7;color:var(--ink);font:16px/1.8 "Microsoft YaHei",sans-serif}main{max-width:1140px;margin:36px auto;padding:36px 44px;background:#fff;border:1px solid var(--line);border-radius:14px}h1{font-size:30px;line-height:1.45;margin:0 0 18px}h2{margin:38px 0 16px;border-bottom:2px solid #e1ebf2;padding-bottom:9px;font-size:23px}h3{font-size:19px;margin:26px 0 12px;color:#164e75}p{margin:12px 0;overflow-wrap:anywhere}code{font-size:13px;background:#edf3f7;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere}.table{overflow:auto;margin:18px 0;border:1px solid var(--line);border-radius:8px}table{width:100%;border-collapse:collapse;min-width:650px;font-size:14px}th,td{text-align:left;vertical-align:top;padding:12px 14px;border-bottom:1px solid var(--line)}th{background:#eaf2f7;white-space:nowrap}tr:last-child td{border-bottom:0}nav{display:flex;flex-wrap:wrap;gap:8px;margin:22px 0}nav a{color:#17658d;text-decoration:none;background:#eaf4f9;border-radius:6px;padding:6px 12px;font-size:14px}.flow{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:22px 0}.flow div{background:#edf6fa;border:1px solid #c9dee9;border-radius:8px;padding:14px;font-size:14px}.flow strong{display:block;font-size:17px}.tag{display:inline-block;background:#f0f5f8;color:#526575;border-radius:4px;font-size:12px;padding:2px 8px}.notice{background:#fef5e6;border-left:4px solid #de9c39;padding:14px 18px;font-size:15px}footer{font-size:13px;color:var(--muted);margin-top:32px}@media(max-width:650px){main{margin:0;padding:22px 18px;border-radius:0;border:0}h1{font-size:24px}h2{font-size:20px}.flow{grid-template-columns:1fr 1fr}table{min-width:600px}body{font-size:15px}}
    </style></head><body><main>'''
    scope=parameter_result['scope']
    page_shell=page
    insert = '<div class="notice"><strong>最新第2版：普通币OI、成交额、T1及观察更新统一五分钟，T2/T3独立优先。</strong><br>第13节同步批次交付、状态时效、所有者续租和失败处理；撤回旧普通三分钟建议，所有新值尚未实施或激活。'+str(scope['curated_parameter_groups'])+'组参数主题 · '+str(scope['logical_relations'])+'组逻辑关系 · 7个审核范围 · 19项核对结论。<br><a href="P39_five_minute_schedule_plan.html">打开五分钟协同时序设计</a> · <a href="P39_timing_layer_review.html">逐级核对</a> · <a href="P39_time_parameter_inventory.html">参数总表</a></div><nav>' + ''.join(f'<a href="#section-{i}">{label}</a>' for i,label in [(13,'五分钟全链路时序'),(12,'T1与独立T2/T3方向'),(11,'逐级问题与优化'),(10,'时间参数与逻辑关系'),(1,'当前结论'),(2,'历史运行证据'),(3,'逐项检查'),(5,'数据传递'),(7,'处理方向')]) + '</nav><div class="flow"><div><strong>普通T1 · 五分钟同轮</strong>成交额批量 / OI分片<br>采完即筛选、交接</div><div><strong>T2 · 独立分析</strong>重点数据每分钟<br>输入未变复用、完成即补位</div><div><strong>T3 · 当前可买</strong>30秒轻检查候选<br>确认结果 + 最新行情</div><div><strong>API → 页面</strong>同用途同状态<br>可买快路独立返回</div></div>'
    body = body.replace('<h2 id="section-1">', insert + '<h2 id="section-1">', 1)
    page += body + '<footer>静态审计方案 · 本轮未修改业务代码或生产配置 · 自动监控保持暂停</footer></main></body></html>'
    report = OUT/'P39_timing_consistency_plan.html'
    report.write_text(page, 'utf-8')
    public = ROOT/'apps/web/public/previews/P39_timing_consistency_plan.html'
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text(page, 'utf-8')
    layer_page=page_shell.replace('<title>P39 · 全链路时间规则冲突审计与执行方案</title>','<title>P39 · 逐级时间参数问题与优化核对</title>')
    layer_page+='<h1>P39 · 逐级时间参数问题与优化核对</h1><nav><a href="P39_timing_consistency_plan.html#section-11">返回总方案</a><a href="P39_time_parameter_inventory.html">检索参数与源码</a><a href="#section-12">各级完整参数归属</a></nav>'
    layer_page+=render_md(layer_full_md)+'<footer>仅核对与讨论 · 未改业务参数 · 运行证据来自17:42保存采样</footer></main></body></html>'
    (OUT/'P39_timing_layer_review.html').write_text(layer_page,'utf-8')
    (public.parent/'P39_timing_layer_review.html').write_text(layer_page,'utf-8')
    schedule_page=page_shell.replace('<title>P39 · 全链路时间规则冲突审计与执行方案</title>','<title>P39 · 五分钟T1与全链路协同时序设计</title>')
    schedule_page+='<h1>P39 · 五分钟T1与全链路协同时序设计</h1><nav><a href="P39_timing_consistency_plan.html#section-13">返回总方案</a><a href="P39_time_parameter_inventory.html">检索参数与源码</a></nav>'
    schedule_page+=render_md(schedule_md)+'<footer>候选时序设计 · 尚未实施或激活 · 耗时依据为17:42保存采样</footer></main></body></html>'
    (OUT/'P39_five_minute_schedule_plan.html').write_text(schedule_page,'utf-8')
    (public.parent/'P39_five_minute_schedule_plan.html').write_text(schedule_page,'utf-8')
    print(json.dumps({'report':str(report),'public':str(public),'scope':result['inventory_scope'],
                      'issues':len(issues),'risks':len(risks),'parameter_scope':scope,'layer_review':result['layer_review'],'business_changes':False},ensure_ascii=False,indent=2))


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(str(c).replace('|',' / ') for c in row)+' |' for row in rows])


def issue_md(item):
    refs = '；'.join(f"`{r['path']}:{r['line']}`" for r in item['source'])
    return [f"### {item['id']} · {item['title']}〔{item['evidence_level']}〕",
            '**现状：**'+item['current'], '**影响：**'+item['impact'],
            '**处理：**'+item['solution'], '**代码依据：**'+refs]


def inline(text):
    text = html.escape(text)
    text = re.sub(r'`([^`]+)`',r'<code>\1</code>',text)
    return re.sub(r'\*\*([^*]+)\*\*',r'<strong>\1</strong>',text)


def render_md(md):
    out=[]
    lines=md.splitlines()
    i=0
    while i<len(lines):
        line=lines[i]
        if not line:
            i+=1
            continue
        if line.startswith('| '):
            rows=[]
            while i<len(lines) and lines[i].startswith('| '):
                rows.append([c.strip() for c in lines[i].strip('| ').split('|')]);i+=1
            out.append('<div class="table"><table><thead><tr>'+''.join('<th>'+inline(c)+'</th>' for c in rows[0])+'</tr></thead><tbody>')
            out += ['<tr>'+''.join('<td>'+inline(c)+'</td>' for c in row)+'</tr>' for row in rows[2:]]
            out.append('</tbody></table></div>');continue
        m=re.match(r'^(#{1,3}) (.*)$',line)
        if m:
            n=len(m[1]);ident=''
            if n==2:
                num=re.match(r'(\d+)\.',m[2]);ident=f' id="section-{num[1]}"' if num else ''
            out.append(f'<h{n}{ident}>'+inline(m[2])+f'</h{n}>')
        else:out.append('<p>'+inline(line)+'</p>')
        i+=1
    return '\n'.join(out)


if __name__=='__main__':
    main()
