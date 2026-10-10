"""Document a stage-by-stage review; reads saved evidence, never alters runtime."""
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json


def build(root,out,ref,table):
    params=json.loads((out/'time-parameter-design.json').read_text('utf-8'))
    old=json.loads((out/'time-rule-inventory-full.json').read_text('utf-8'))
    changed=[f['path'] for f in old['files'] if hashlib.sha256((root/f['path']).read_bytes()).hexdigest()!=f['sha256']]
    if changed:raise RuntimeError('Parameter inventory drift: '+', '.join(changed))
    items=[]
    def issue(ident,layer,title,result,impact,direction,sources):
        items.append(dict(id=ident,layer=layer,title=title,result=result,impact=impact,
                          direction=direction,source=[ref(*s) for s in sources]))
    issue('F01','采集','成交额周期长于消费时效','先修：已复现',
          '30分钟更新、6分钟过期会周期性缩掉T1合约检查集合；17:42样本522条全部stale。',
          '目录和成交额分离，用一次批量ticker或合适流更新成交额；刷新周期与成交额TTL单独讨论，不随collector连带变。',[
              ('engine/adapters/binance_universe.py','>= 1800_000'),('engine/p37/store.py','max_age=max(360000')])
    issue('F02','采集','OI目标与实际交付不匹配','先修：交付延迟已复现',
          '写了10/15/60秒但当前链路存在多分钟交付；90秒触发条件容易拿不到新OI，不能用due目标冒充真实覆盖。',
          '拆due/预算/HTTP/写入耗时，缓存优先级membership，候选与普通采集分开调度；具体降频后联动trigger/参考时效和15M端点网格。',[
              ('engine/pd2/collection_schedule.py','OI_INTERVALS ='),('engine/pd2/current_market_fields.py','CHANGE_MAX_AGE=')])
    issue('F03','采集','参考字段和资金费时钟混用','统一：状态逻辑差异',
          '资金费普通30分钟查历史却3分钟失去尾部确认；结算值宽限3/5分钟不一致。FX与供应量周期/TTL目前配合合理。',
          '分别判结算值、查询覆盖和下次结算；参考未知不作准入门槛。FX/供应量可以慢调度，但不需要随T3拉取。',[
              ('engine/pd2/derivatives.py','def settled_costs'),('engine/repository.py','(interval+300)*1000'),('engine/pd2/binance_facts.py','FX_MAX_AGE =')])
    issue('F04','T1','价格采样、断档与评估循环职责不同','优化：高频评估可合并',
          'T1每2秒evaluate/persist，目录15秒载入；但内存1秒采样、15秒断档是5/15分钟证据条件。统一降到30秒采样会使窗口失效。',
          '持续被动接收保留内存连续性；评估、配置重查、统计和DB持久化降频/按dirty集合合并，原始事实时刻保留。',[
              ('engine/p37/worker.py',"health_state='ok' if healthy"),('engine/p37/worker.py','now-loaded>=15000'),('engine/p37/detector.py','if any(b[0]-a[0]>15000')])
    issue('F05','T1','观察走势缓存成为第二个可买时效条件','先修：确定逻辑错配',
          '普通tracking30秒，读取可买却要求cached buy_eligible及tracked≤15秒。影响观察行/计数一致性，未证明某币被实际漏买。',
          '可买行和计数按同一当前信号函数读；tracking只用于走势与历史。此项可以先修，独立于之后选30秒还是60秒。',[
              ('engine/p37/store.py','interval=case('),('engine/p38/observation_query.py',"row['buy_eligible']=bool"),('engine/p38/observation_query.py','tracked>=now-15000')])
    issue('F06','T1','事件存活与需求/信号寿命混淆','保留：明确用途',
          '30分钟事件合并、2小时冷却、7天归档决定观察事件管理；默认T2 observation需求寿命也是来源时间+2小时。它们不是买入许可。',
          '保留语义、分开展示/诊断；只有新真实来源更新需求，不用轮询延长旧事件。不为性能改动历史窗口。',[
              ('engine/p37/detector.py','cool_ms=7200000'),('engine/p39/pipeline.py',"keep(iid, 'observation', ident, last + config['cool_ms']")])
    issue('F07','T2','复查时钟和一刀切判旧','先修：逻辑差异及排队证据',
          '成功5分钟复查、390秒显示delayed，仅留90秒排队余量；结构排队P95约583秒。日周输入未变也可能被计算年龄判旧。',
          '按输入版本/应有收盘/原结构失效条件判可用；计算和复核时刻分开；新依赖唤醒、输入未变复用、等待输入释放槽位。',[
              ('engine/p39/pipeline.py','state.next_check_at = at +'),('engine/p38/observation_query.py','now-checked<=390000')])
    issue('F08','T2','收盘边界与策略版本时钟差异','统一：按用途和版本',
          '趋势在收盘边界零余量，通用结构给3分钟；兼容箱体又给1H/4H收盘10分钟余量。P33.observe在兼容报价检查之前调用，并非所有策略共用后面的门槛。',
          '共享UTC expected_closed_at和待到达/缺口状态；明确兼容箱体与P33版本，不用一个TTL推翻全部结构语义。',[
              ('engine/p37/analysis.py',"rows[-1]['close_time']<now//step*step"),('engine/pd2/analysis.py','DURATIONS[resolution]+180000'),('engine/signal_service.py','p33_result=observe('),('engine/signal_service.py','>15000_000')])
    issue('F09','T2','选中优先期短于排队时间','待量化：确认轮换路径',
          '120秒内queued/leased获得held优先；过窗仅失去优先，并不立刻取消运行。换出active的queued/failed任务可被retire。近期排队比120秒长。',
          '按task ID记录入队→换出→再入队次数，检查待服务需求公平性；按实际未服务/输入版本保留需求，避免单靠固定时间轮换造成反复。',[
              ('engine/p39/pipeline.py','at - admissions.get(iid, 0) < 120000'),('engine/p38/jobs.py','def retire_unneeded')])
    issue('F10','T2','重计算与高频调度开销混在一起','优化：事件/完成驱动',
          '已有按闭合K线/版本复用的缓存，并非每次task都完整重算；producer目标3秒扫全量、统计5秒读全表、executor0.5秒查任务仍带来开销。',
          '保留缓存；新需求/版本事件合并入队，完成立即补位，空闲才慢兜底。不要在每个完成任务后强制等30秒。',[
              ('engine/pd2/analysis.py','if cached is None:'),('engine/p38/jobs.py','3-(now_ms()-started)/1000'),('engine/p38/jobs.py','now_ms()-reported>=5000')])
    issue('F11','T3','10秒盘口与15秒报价的多处硬编码','先对齐：降频必须成套讨论',
          '配置上限、book_clock、DataCapability期限、qualify、p33_current、投影与API都消费这些时效；旧计划还可能冻结旧execution_config。',
          '先统一用途policy和引用，讨论推荐更新频率时同时决定事实可接受年龄；如改时间版本，兼容当前活计划但不重写历史快照或策略区间。',[
              ('engine/pd2/config.py','depth_max_age_seconds:'),('engine/pd2/book_clock.py','max_age=10000'),('engine/p38/p33_current.py','valid_until=min(plan.expires_at')])
    issue('F12','T3','同币重任务等待可能吞掉短资格窗口','优化：架构耦合已确认',
          '不同lane仍共享同币fence和exclude_subjects；结构执行P95约24秒。entry已刷新后直接发布，已有快速交接，不能说它还必须等第二投影队列。当前entry无延迟样本。',
          'T2读/计算移出关键写入保护，T3只读确认结果+最新行情做轻检查；保留版本一致性。继续沿用结构完成立即交接与entry直接发布。',[
              ('engine/p38/jobs.py',"key='p38_execute:'+iid"),('engine/p38/jobs.py','exclude_subjects=excluded'),('engine/p38/jobs.py',"'plan_handoff'"),('engine/p38/jobs.py','decision_list,instrument_ids=[iid],fast=True')])
    issue('F13','T3','计划到期、当前资格和参考空间应分开','保留：原策略语义',
          'P33入场plan为known+触发周期；当前valid_until取计划、报价、盘口期限的最小值。价格越界/触保护也可让资格更早结束。',
          '本轮不为降频延长或缩短原计划寿命；成本/空间参考不另加时效硬门槛。若要调整入场寿命，单独讨论策略版本。',[
              ('engine/strategies/p33.py','expires_at=known+DURATIONS[tf]'),('engine/p38/p33_current.py','valid_until=min(plan.expires_at')])
    issue('F14','投影/API/页面','多页加载和兜底周期消耗短窗口','待验证：已有防护',
          'SSE1秒合并、兜底30秒、每页8秒、全部页到齐再显示；本地每秒隐藏过期资格已存在。接口还复核价格15秒与原区间。',
          '当前可买小列表优先/增量返回，等待计划单独慢加载；刷新进展与服务端as_of联动。调慢页面时须联动T3窗口，保留过期隐藏。',[
              ('apps/web/src/components/opportunity-board.tsx','timeoutMs:8000'),('apps/web/src/lib/use-refresh.ts','setBusy,30000'),('engine/pd2/signal_projection.py','m.as_of < now-15000')])
    issue('F15','投影/API/页面','图表高频读取与行情接收混淆','优化：页面查询可降频',
          '图表每5秒查最新两根K线，分析15秒拉取；1H/4H/1D/1W已闭合结构不需要按秒重复取完整数据。',
          '区分未收盘走势与闭合指标；合并重复请求，闭合或revision变化触发指标更新，页面不可见停查；详情/K线内容与格式保留。',[
              ('apps/web/src/components/chart.tsx','}},5000)'),('apps/web/src/components/chart.tsx','setInterval(()=>void load(),15000)')])
    issue('F16','共享执行','采集预留和失败退避共用期限','先对齐：静态错配',
          '采集预留240秒、预算最长420秒；预留同时携带failures递增。单dispatcher pending已有去重，当前没有重复HTTP的实证。',
          'inflight owner续租与next_retry分开；按排队+请求+提交覆盖期限，完成/取消释放；实时旧请求过期应撤销并重取最新。',[
              ('engine/pd2/collection_schedule.py',"retries[n]={'until'"),('engine/pd2/p36_budget.py','QUEUE_SECONDS=420')])
    issue('F17','共享执行','心跳频率和健康截止不一致','统一：降频联动',
          '5秒上报配30/300秒多个consumer；partial也可能overall ok，旧run重连状态可能残留。统计上报包含全表读库。',
          '按当前run/PID及角色职责核对；心跳、数据进展、覆盖分别汇总。上报降频后同步consumer期限，统计增量维护。',[
              ('engine/p38/jobs.py','now_ms()-reported>=5000'),('services/api/main.py','now-owner.updated_at<=30000'),('services/api/main.py','overall="ok" if all')])
    issue('F18','共享执行','连接持有阈值、租约和协议时钟不是查询频率','保留：按职责',
          '120秒task/20秒续租本身合理；HTTP90秒lease覆盖45秒传输；10秒连接持有日志是诊断。DB准入未显式设等待timeout，需记录该阶段耗时。',
          '优化长事务、锁内扫描、空闲轮询；续租如降频则联动租约。协议PING、Retry-After、提交owner/version检查按职责保留，不统一改成分钟。',[
              ('engine/p38/jobs.py','ttl=120000'),('engine/adapters/base.py','asyncio.timeout(45)'),('engine/db.py','time.monotonic()-started>10'),('engine/db.py','if depth==0:_admission.acquire()')])
    issue('F19','策略/历史/兼容','研究/历史/认证时间不应作为性能旋钮','保留或独立清理',
          '指标bars回看、24/72小时回放、30/45日前高、会话8小时、分页快照1/24小时与实时候选刷新无关；外源和通知时间字段有禁用/兼容项。',
          '回看与原计划按策略版本保留；关闭模块不恢复、不用旧时间约束挡入。仅清理确认无活动调用的兼容参数，历史记录仍可读。',[
              ('engine/strategies/p33.py','recent_high_days'),('engine/pd2/config.py','disable_retired_sources'),('engine/pd2/config.py','cooldown_seconds:'),('engine/config.py','session_seconds:')])

    stage_specs=[
        ('采集','采集与参考字段','Binance事实 → 独立字段时钟 → 本地状态','先恢复T1所需成交额与OI交付，再讨论普通/重点采集降频。'),
        ('T1','T1异动与观察','连续内存窗口 → 轻评估 → 观察事件/T2需求','先移除tracking的第二道资格条件，再合并重复评估；原5/15分钟窗口保留。'),
        ('T2','T2分析与任务调度','活来源需求 → 分类型任务 → 闭合K线版本 → 结构/趋势结果','修结果可用状态和收盘判定；减少重查；量化120秒选中轮换，不假定已漏信号。'),
        ('T3','T3买入与投影','确认的策略计划 + 当前价格/盘口 → 资格复核 → 立即发布','先对齐全部时效消费者、缩短重任务耦合，再一起讨论检查周期和资格年龄。'),
        ('投影/API/页面','页面与接口','当前投影 → API当下复核 → 可买/等待分层 → 展示','可买优先返回，复用等待计划/图表数据；页面刷新与资格窗口共同讨论。'),
        ('共享执行','网络、任务租约与服务健康','预算/网络/数据库/所有者/健康，贯穿各级','消除等待期限错配和长锁范围，空闲轮询降频；保留协议与一致性职责。'),
        ('策略/历史/兼容','策略与兼容参数','冻结策略、历史证据、研究、认证及禁用模块','不将策略窗口当普通刷新参数；关停兼容路径单列，避免混进生产判定。'),
    ]
    stages=[]
    for ident,group,flow,conclusion in stage_specs:
        related=[x for x in items if x['layer']==ident]
        ps=[p for p in params['parameters'] if p['group']==group]
        stages.append(dict(id=ident,group=group,flow=flow,conclusion=conclusion,parameter_count=len(ps),
                           parameter_names=[p['name'] for p in ps],findings=related))
    result=dict(captured_at=datetime.now(timezone.utc).isoformat(),mode='review_only',business_changes=False,
                source_hash_check={'files':len(old['files']),'changed':changed},
                evidence_note='参数源码复核；运行证据引用2026-10-08 17:42保存采样，不视为当前健康状态。',
                scope=params['scope'],stages=stages,findings=items,
                review_coverage={'listed_parameter_groups':sum(s['parameter_count'] for s in stages),
                                 'unique_parameter_groups':len({n for s in stages for n in s['parameter_names']}),
                                 'total_parameter_groups':len(params['parameters'])})
    if result['review_coverage']['listed_parameter_groups']!=len(params['parameters']) or result['review_coverage']['unique_parameter_groups']!=len(params['parameters']):
        raise RuntimeError('Stage assignment must cover each curated parameter group once')
    (out/'time-layer-review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    summary=['## 11. 逐级时间参数问题与优化方向',
             '先检查、再讨论，不决定新周期。本轮将全部'+str(len(params['parameters']))+'组参数主题归入7个审核范围，逐项核对19个问题/优化/保留结论；补查新增了T2需求寿命、选中优先窗口、兼容箱体K线期限3组参数。源码与上一轮清单一致。',
             '本节按影响排序：先恢复输入和统一判定，再减少重复查询与重计算，最后讨论低频值。17:42采样只支持当时的交付/队列证据，本轮没有重新判断系统实时健康；没有证明某币被漏推荐的地方明确保留为待量化。',
             table(['审核层级','参数主题数','当前主要问题 / 职责','处理重点'],[
                 [s['id'],s['parameter_count'],'；'.join(x['title'] for x in s['findings']),s['conclusion']] for s in stages]),
    ]
    for s in stages:
        summary += ['### '+s['id']+'：'+s['flow']]
        for f in s['findings']:
            refs='；'.join('`'+r['path']+':'+str(r['line'])+'`' for r in f['source'])
            summary += [f"**{f['id']} · {f['title']}｜{f['result']}**",f['impact'], '**优化或保留方向：**'+f['direction'], '**依据：**'+refs]
    summary += ['### 11.1 贯穿各级的时间关系',
                table(['关系','应检查的逻辑','本次结果'],[
                    ['生产 → 消费','实际送达年龄 + 下游等待，不能稳定超过所用事实TTL','成交额明确错配；OI实际交付不足；先看实际年龄，不只看计划周期'],
                    ['需求 → 服务','需求存活和任务优先保持须覆盖合理服务等待','2小时需求≠120秒选中优先≠task lease；选中轮换损耗待量化'],
                    ['输入 → 计算 → 复核','输入未变可复用，输入变更才重算；freshness按事实判断','已有缓存保留；390秒计算年龄一刀切应收敛'],
                    ['等待 → owner租约','进行中续租或预留覆盖等待+执行+提交；失败retry另算','240秒预留<420秒预算等待；task120秒/续租20秒本身配合合理'],
                    ['当前资格 → 页面','页面/API加载延迟不得经常消耗整个可买窗口','SSE有快路，仍需测多页/丢事件；本地过期隐藏已实现'],
                ]),
                '这些关系是工程设计检查，不是新的选币/买入门槛。不能为了满足它们凭空延长真实来源时间，也不能把所有不同用途时间强行设为同一数值。',
                '### 11.2 讨论与处理顺序',
                '第一批先讨论确定错配：F01成交额、F05观察计数、F07分析显示、F16采集预留、F17健康汇总。这些可以减少错误状态，不需要先决定全部市场多久查询一次。',
                '第二批讨论性能结构：F02/F04/F10/F12，分别是分层OI交付、T1合并评估、T2事件/完成驱动、T3轻检查隔离。先减少无效SQL和重复计算，再选低频周期。',
                '第三批成套讨论“推荐多快更新”：T3检查周期、价格/盘口最大事实年龄、页面事件合并与兜底周期；再确定普通/T2/T3 OI频率和trigger时效。原计划寿命若要调整，作为独立策略问题。',
                '合理保留项：被动收包与内存证据、真实收盘与历史可知时间、策略回看、必要短提交保护/owner续租、协议PING与交易所Retry-After、分页/认证身份TTL。它们不因出现10秒/20秒等数值就自动构成高频开销。',
                '逐级原始结果见time-layer-review.json；每组现状、职责、源码及完整表达式仍在第10节与可搜索总表。本轮未改业务代码、生产配置、数据或服务，自动监控保持暂停。']
    md='\n\n'.join(summary)+'\n'
    detail=[md,'## 12. 各级完整参数归属清单',
            '每组人工归并参数只列一个主要审核层；跨层消费者和所有使用点继续保存在全量源码索引。表中的当前值是源代码默认/公式，已发布值和冻结计划差异按第10节说明。']
    for s in stages:
        ps=[p for p in params['parameters'] if p['group']==s['group']]
        detail += ['### '+s['id']+' · '+str(len(ps))+'组',table(['参数','当前值 / 默认 / 公式','职责','源码'],[
            [p['name'],p['current'],p['current_parameter_kind'],'`'+p['source']['path']+':'+str(p['source']['line'])+'`'] for p in ps])]
    full='\n\n'.join(detail)+'\n'
    (out/'P39_逐级时间参数问题与优化核对.md').write_text(full,'utf-8')
    return md,full,result
