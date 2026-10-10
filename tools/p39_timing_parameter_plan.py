"""Read-only parameter inventory and a proposed lower-frequency timing profile."""
from pathlib import Path
from datetime import datetime, timezone
import ast
import hashlib
import html
import json
import re


def build(root, out, ref, table):
    rows=[]
    def add(group,name,current,proposed,handling,path,needle):
        rows.append(dict(group=group,name=name,current=current,proposed=proposed,
                         handling=handling,source=ref(path,needle),state='proposed_not_activated'))
    # Every value is a source default/formula unless the effective settings file
    # explicitly supplies its published value. Proposed values never write config.
    def a(group,items):
        for item in items:add(group,*item)
    a('采集与参考字段',[
        ('普通/候选/重点实时OI','60 / 15 / 10秒','300 / 60 / 60秒','普通与T1/成交额同轮固定分片；重点独立到期，升层只提升同一任务，不复制请求', 'engine/pd2/collection_schedule.py','OI_INTERVALS ='),
        ('普通/候选/重点price类REST','300 / 60 / 30秒','300 / 60 / 60秒','WS优先；只对缺流或相关市场补REST，保留完成即释放槽位', 'engine/pd2/collection_schedule.py','INTERVALS ='),
        ('合约目录与成交额共用刷新','1800秒','目录1800秒；普通成交额与OI/T1统一300秒','批量ticker同轮更新，字段状态/重试独立；不被目录30分钟时钟拖住', 'engine/adapters/binance_universe.py','>= 1800_000'),
        ('成交额消费时效','max(360秒,通用采集周期×3)','600秒','独立volume policy配五分钟刷新；批次更新失败保留真实上次时间，超时不伪装', 'engine/p37/store.py','max_age=max(360000'),
        ('通用collector周期','默认60秒；当前v40为60秒','保留60秒兼容，不再控制各字段TTL','实际采集频率取按用途policy；配置名称清理放入实现', 'engine/contracts.py','collector_interval_seconds:'),
        ('重点/普通OI历史刷新','300秒 / 21600秒','300秒 / 21600秒','历史格点是5分钟，继续按需求补缺，不改成实时批量请求', 'engine/pd2/collection_schedule.py',"kind=='oi_history'"),
        ('OI历史长周期seed','首次及每21600秒：1H×73点','保留6小时，seed完成后增量修补','不是每个币重跑完整历史；未完成记录真实缺口', 'engine/pd2/derivative_collection.py',"hourly=not previous.get('hourly_seed_at')"),
        ('重点/普通资金费历史刷新','120 / 1800秒','300 / 1800秒；结算边界另唤醒','与已有流中的结算时间配合；不假定结算周期永远固定', 'engine/pd2/collection_schedule.py',"kind=='funding_history'"),
        ('资金费结算宽限','180秒与300秒两种','同用途统一300秒','周期沿用接口实际值；已结算值与预测值分开', 'engine/repository.py','(interval+300)*1000'),
        ('成本历史尾部确认时效','180秒','取消固定180秒重查要求；展示真实observed_through','新结算/周期变化优先补查；历史未覆盖不能假装已确认，成本不硬挡买入', 'engine/pd2/derivatives.py','def settled_costs'),
        ('FX刷新/有效期','60秒 / 3600秒','300秒 / 3600秒','参考币种转换按自身时钟；USDT原生字段无需等待其他转换', 'engine/pd2/binance_facts.py','next_ms=received+60000'),
        ('流通供应量刷新/有效期','21600秒 / 86400秒','保留6小时 / 24小时','市值随当前价格计算；不重拉慢变供应量', 'engine/pd2/binance_facts.py','next_ms=received+21600000'),
        ('供应量失败重试','3600秒','保留1小时','失败不高频打供应商', 'engine/pd2/binance_facts.py','next_ms=now_ms()+3600000'),
        ('参考数据worker轮询','60秒','300秒；完成/输入事件唤醒','FX与供应量的到期查询共用慢调度', 'services/worker/main.py','await asyncio.sleep(60)'),
    ])
    a('T1异动与观察',[
        ('T1 evaluate及持久化扫描','每2秒','普通全盘每300秒；详见总方案第13节','内存接收与评估分开；批内回查需30分钟事实缓冲；完成即交接T2，不将其他工作一起sleep300秒', 'engine/p37/worker.py',"health_state='ok' if healthy"),
        ('T1目录/配置载入','每15秒','配置事件唤醒；60秒兜底','本地缓存catalog，不每轮全量读库', 'engine/p37/worker.py','now-loaded>=15000'),
        ('T1价格异动窗口','5分钟 / 15分钟','保留5分钟 / 15分钟','窗口与检查频率不是一回事；涨幅、量能阈值本轮不改', 'engine/p37/detector.py',"[(5,c['price_5m']),(15,c['price_15m'])]"),
        ('价格窗口内存采样/端点/断档','最小1秒采样 / 5秒端点容差 / 15秒断档','保留，属于内存证据处理','WS被动接收不增加REST；不可将内存只采30秒却保留15秒断档规则', 'engine/p37/detector.py','def delta('),
        ('实时OI触发及15M显示时效','90秒','触发沿用90秒；15M参考显示600秒','普通OI五分钟采完即触发评估，展示与trigger分用途；不拿过旧OI当新触发', 'engine/pd2/current_market_fields.py','CHANGE_MAX_AGE='),
        ('OI参考显示/历史面板时效','360秒 / 180秒','同用途600秒','不是无限期fresh；分别报告采集/事实时刻', 'engine/pd2/current_market_fields.py','OI_MAX_AGE='),
        ('OI15M端点容差','60秒','保留原60秒和消费者实际匹配方向','5分钟/1分钟名义整除15分钟不保证HTTP实际端点配对；按真实起点+900秒补采，不能插值/改源时刻', 'engine/pd2/current_market_fields.py',"end-duration,60000 if label=='15M'"),
        ('OI1H/4H/1D端点容差','300秒','保留300秒，统一历史接口','展示真实端点与跨度；用途时效与端点匹配容差分开', 'engine/pd2/current_market_fields.py',"end-duration,60000 if label=='15M'"),
        ('5分钟量能数据时效','90秒','保留90秒','来自被动K线流，降评估频率不应断接收', 'engine/p37/detector.py',"now-current.get('at',0)<=90000"),
        ('tracking调度轮询','每5秒检查','普通随300秒批次；重点60秒独立','只更新到期/变化集合，不扫描全部没到期的观察', 'engine/p37/worker.py','now-tracked>=5000'),
        ('重点/普通/抑制/冷却tracking','5 / 30 / 60 / 300秒','60 / 300 / 300 / 300秒','普通与五分钟T1同轮，不用于当前买入许可；行情窗口继续独立留样', 'engine/p37/store.py','interval=case('),
        ('同事件最小重复更新/轨迹采样','10秒 / 60秒','30秒 / 60秒','状态改变保留；相同原因合并，不重复创建T2计算', 'engine/p37/store.py',"source-row.payload.get('source_at',0)<10000"),
        ('异动合并窗口','1800000毫秒＝30分钟','保留30分钟','事件语义参数，非轮询；修改会改变事件分组', 'engine/p37/detector.py','merge_ms=1800000'),
        ('观察冷却时间','7200000毫秒＝2小时','保留2小时','与数据fresh无关', 'engine/p37/detector.py','cool_ms=7200000'),
        ('观察归档时间','604800000毫秒＝7天','保留7天','保留真实历史，不因降频删记录', 'engine/p37/detector.py','archive_ms=604800000'),
        ('T2选中membership新鲜期限','120秒','300秒','随selection兜底60秒和心跳60秒同步；不能刚更新就失效', 'engine/pd2/collection_schedule.py','now-active_observations.updated_at<120000'),
    ])
    a('T2分析与任务调度',[
        ('观察来源T2需求寿命','source_at + cool_ms；默认2小时；手动/pinned来源可无expires','本轮保留来源语义','这是分析需求可被处理的寿命，不是买入许可；新真实来源更新才续期', 'engine/p39/pipeline.py',"keep(iid, 'observation', ident, last + config['cool_ms']"),
        ('T2选中任务优先保持窗口','120秒','先核对排队/轮换，再讨论保持策略','不是task lease；过窗失去held优先，换出active的queued任务可被retire', 'engine/p39/pipeline.py',"at - admissions.get(iid, 0) < 120000"),
        ('兼容箱体路径K线年龄上限','1H闭合70分钟 / 4H闭合250分钟','按策略版本与统一收盘到达语义核对','P33已在此前observe；这组10分钟余量不是所有P33的全局阻断', 'engine/signal_service.py','>15000_000'),
        ('深度分析选择载入','15秒','60秒；需求变更合并唤醒','同市场同类型只留最新输入版本', 'engine/p37/worker.py','now-selected_at>15000'),
        ('分析producer全量扫描','目标3秒；实测一轮8.6秒','60秒兜底；新需求入队事件唤醒','event路径先合并，不将所有层级每30秒重分析', 'engine/p38/jobs.py','3-(now_ms()-started)/1000'),
        ('执行器空闲查任务','0.5秒','完成/通知即补位；空闲30秒兜底','不能执行完每项再sleep30秒，否则会降低队列吞吐', 'engine/p38/jobs.py','await asyncio.sleep(.5)'),
        ('分析完成复查','300秒','600秒兜底；依赖K线/版本变化唤醒','输入未变复用，参考计算时间不一刀切过期', 'engine/p39/pipeline.py','state.next_check_at = at +'),
        ('缺输入/失败后的复查','60秒','120秒兜底；真实输入就绪优先唤醒','等待释放计算名额，不反复重算', 'engine/p39/pipeline.py','state.next_check_at = at +'),
        ('输入过时结果重调度','250毫秒','下一30秒合并批次，最新版本只处理一次','不连续抢占、不覆盖新结果', 'engine/p39/pipeline.py','250 if superseded'),
        ('观察分析统一判旧','390秒','去掉按compute age一刀切；按输入版本/期望收盘判定','10分钟兜底未到不代表结构过期', 'engine/p38/observation_query.py','now-checked<=390000'),
        ('K线收盘到达工程余量','趋势0秒 / 结构180秒','同用途统一180秒补数状态','保留真实收盘条件；超余量显示真实缺口', 'engine/pd2/structural_history.py','DURATIONS[tf]+180000'),
        ('缺K线补数重查','重点1秒 / 普通60秒 / 错误15秒','完成即下一缺口；无进展60秒 / 错误60秒','一页完成续作不是高频重复分析；没有进展才等待', 'engine/p37/worker.py','retry_ms=15000 if batch.errors'),
        ('分析统计全表读库/上报','每5秒','每60秒；计数增量维护','5秒统计比实际业务进度更频繁，没有必要', 'engine/p38/jobs.py','now_ms()-reported>=5000'),
        ('滚动性能样本窗','1200000毫秒＝20分钟','保留20分钟','统计用途；不改变信号时效', 'engine/p39/pipeline.py','window_ms=1200000'),
        ('background异常优先期','14400000毫秒＝4小时','保留4小时；新证据才更新','轮询不得延长旧需求寿命', 'engine/pd2/p35_schedule.py','priority_until'),
        ('baseline shadow周期/需求寿命','60秒 / 1小时','周期300秒 / 寿命1小时；仍低优先级','先处理真实T2需求，不提升shadow为买入', 'engine/p39/pipeline.py',"'next_at': at + 60000"),
    ])
    a('T3买入与投影',[
        ('当前买入轻检查','producer驱动，目标3秒','每30秒；每市场每批只一次','有结构且近买点的集合，复用T2结果；新行情标dirty', 'engine/p38/jobs.py','3-(now_ms()-started)/1000'),
        ('当前报价有效期','配置及硬编码15秒','90秒','同步decision、zone检查、投影、观察、订阅选择；不是价格一定90秒不变', 'engine/pd2/config.py','quote_max_age_seconds:'),
        ('执行盘口有效期','配置及硬编码10秒','90秒','同步模型ge/le、book_clock、DataCapability.expires_at和所有派生valid_until', 'engine/pd2/config.py','depth_max_age_seconds:'),
        ('近买点盘口获取/持久化','合约depth20@100ms；DB每0.5秒drain','只订阅近买点；30秒复核批取最新并按变化提交','不串联60秒快照持久化与30秒entry排队；最新原始source/receive时间保留', 'engine/pd2/native_streams.py','async def write_depth('),
        ('当前买入资格valid_until','min(计划到期,报价+15秒,盘口事件+15秒,盘口接收+10秒)','min(原计划到期,报价+90秒,盘口有效时间+90秒)','30秒重新判定，不续贴旧资格；离开原区间/触保护按原条件退出', 'engine/p38/p33_current.py','valid_until=min(plan.expires_at'),
        ('观察缓存对可买的附加时效','tracked_at≤15秒且cached buy_eligible','移除该缓存附加条件','两页共用当下信号判定，tracking不再二次挡入', 'engine/p38/observation_query.py',"row['buy_eligible']=bool"),
        ('投影/等待列表依赖时间桶','30秒','投影30秒；等待计划60秒批更新','发布相同结果不新增事件，可买小列表单独返回', 'engine/p38/jobs.py','at//30000'),
        ('旧投影循环（兼容路径）','1秒','P39 owner活跃时不得并行；兜底60秒','需要按实际启用路径去重，不能启动第二owner', 'services/worker/main.py','await asyncio.sleep(1)'),
    ])
    a('页面与接口',[
        ('观察/机会列表兜底刷新','30秒；loop默认15秒','统一60秒','事件变更30秒内合并刷新；页面隐藏停止请求，恢复可见立即一次读取', 'apps/web/src/lib/use-refresh.ts','setBusy,30000'),
        ('事件后API刷新合并间隔','1秒','实质变化5秒内合并刷新','与第13节可买快路配合；并非每5秒固定轮询，等待计划独立慢加载', 'apps/web/src/components/opportunity-board.tsx','refresh()},1000'),
        ('机会页本地过期计时','1秒','保留到期定时器；或到期时一次触发','纯本地隐藏，不发REST；不是每秒重新分析', 'apps/web/src/components/opportunity-board.tsx','setNow(Date.now()),1000'),
        ('图表分析刷新/事件合并','15秒 / 1秒','60秒 / 30秒','只改拉取时钟，详情内容、K线格式和历史视图不改', 'apps/web/src/components/chart.tsx','setInterval(()=>void load(),15000)'),
        ('图表最新两根K线查询','5秒','30秒','原生WS持续接收；页面只降展示查询，不改实际闭合事实', 'apps/web/src/components/chart.tsx','}},5000)'),
        ('列表每页HTTP超时','8秒','保留8秒；可买先返回，整批另设30秒deadline','8秒是失败等待期限，不是8秒刷新；避免多页拖完短信号', 'apps/web/src/components/opportunity-board.tsx','timeoutMs:8000'),
        ('operations HTTP超时','2秒','5秒','单次失败保留旧结果，不每2秒重新查询', 'apps/web/src/components/operations-dashboard.tsx','timeoutMs:2000'),
        ('普通API默认HTTP超时','45秒','保留45秒；大查询按用途单独限定','不是45秒一次采集', 'apps/web/src/lib/api.ts','timeoutMs=45000'),
        ('SSE取事件/保活等待','15秒','协议保活保留15秒；取事件改通知驱动','保活不会启动业务分析，数据事件在批次内合并', 'services/api/main.py','queue.get(),timeout=15'),
        ('机会页分页排序快照TTL','3600000毫秒＝1小时','保留1小时','排序身份不是买入资格；每次读仍按当前valid_until判断', 'engine/pd2/signal_projection.py',"'expires_at':now+3600000"),
        ('通用列表分页快照TTL','86400000毫秒＝24小时','保留24小时','列表身份缓存，不延长行情事实', 'engine/pd2/pagination.py','expires_at=now+86400000'),
    ])
    a('网络、任务租约与服务健康',[
        ('连续采集空队列轮询','1秒；等待完成最长1秒','完成即补位；空闲30秒兜底或next_due唤醒','不把每项执行后强制暂停30秒', 'engine/pd2/continuous_collection.py','await asyncio.sleep(1)'),
        ('due空闲/退避唤醒','0.1～5秒；默认5秒','next_due/通知唤醒；空闲30秒兜底','inflight保持原子，输入到期才调度', 'engine/pd2/collection_schedule.py','return max(.1,min(5,min(waits)/1000))'),
        ('报价DB drain','有数据0.05秒 / 无数据0.5秒','每30秒合并最新报价落库','原始接收和T1价格窗口继续实时入内存；聚合成交/强平事实不得只留last丢明细', 'engine/pd2/native_streams.py','buffer.pending else .5'),
        ('WS健康维护读写','10秒','60秒','每分片/角色合并，不能每帧读membership', 'engine/pd2/stream_maintenance.py','interval=10'),
        ('WS订阅集合重查','60秒','120秒；集合变更通知优先','订阅变更去重，重建时记录连续性空档', 'engine/pd2/native_streams.py','tick-checked>=60000'),
        ('live Binance/衍生历史心跳','5秒','60秒','effective、health和UI同步改到180秒心跳期限', 'engine/adapters/binance_universe.py','now_ms()-last_health>=5000'),
        ('health/operations心跳期限','300秒 / 30秒或按role 120～600秒','活跃role180秒；慢批任务按周期另定','进程存活与数据覆盖分开；禁止60秒心跳仍用30秒判旧', 'services/api/main.py','ttl=expected.get(role,300_000)'),
        ('owner数据库身份定时check','2秒','60秒；关键提交仍检查owner','进程身份lease是连接寿命，没有统一秒数TTL可删', 'services/worker/main.py','await asyncio.sleep(2)'),
        ('分析/补K线task租约与续租','120秒 / 20秒','保留120秒 / 20秒','进行中续租；不要将租约与五分钟批次/整轮目标混为同一值', 'engine/p38/jobs.py','ttl=120000'),
        ('采集inflight预留','240秒且与失败退避混用','120秒租约 / 30秒续租；失败next_retry另存','预算等待与HTTP全过程续租；历史仍可能等420秒，不靠固定预留猜完成时间', 'engine/pd2/derivative_collection.py','capacity,240000'),
        ('HTTP已发请求lease','90000毫秒＝90秒','保留90秒','传输总超时45秒；无需随买入频率改变HTTP所有者租约', 'engine/pd2/p36_budget.py','LEASE_MS=90000'),
        ('预算排队上限','420秒所有lane共用','热点60秒；普通按300秒批次稳定相位；冷补数420秒','普通各币到期后排队目标60秒，跨周期合并最新待办；五分钟批次目标240秒非删任务门槛', 'engine/pd2/p36_budget.py','QUEUE_SECONDS=420'),
        ('预算迁移恢复保护窗','300秒（检测到旧ledger时）','保留300秒，仅迁移时生效','这不是每次业务分析都等5分钟', 'engine/pd2/p36_budget.py','migration_until'),
        ('预算等待轮询','最高每1秒再次admit','owner通知/next_admission_at等待','不得按固定30秒让空闲额度空转；准入检查与业务刷新不同', 'engine/pd2/p36_budget.py','min(delay,1,remaining)'),
        ('HTTP阶段/整次传输超时','20秒 / 45秒','保留20秒 / 45秒','超时预算为单次请求边界，非采集频率', 'engine/adapters/base.py','AsyncClient(timeout=20'),
        ('WS连接/PING/收包等待','15秒 / 20秒 / 20秒','先保留协议设置','不因业务降频改变Binance心跳协议；异常记录为链路状态', 'engine/pd2/native_streams.py','open_timeout=15'),
        ('WS重连退避','1秒起、封顶30秒','保留指数退避封顶30秒','异常恢复时钟，不是正常业务轮询', 'engine/pd2/native_streams.py','backoff=min(backoff*2,30)'),
        ('采集失败退避','30秒指数递增至300秒','保留30～300秒，单独next_retry','不因一次timeout全市场暂停', 'engine/pd2/collection_schedule.py','30000*2**min(failures,4)'),
        ('任务失败/版本合并退避','错误1～60秒；deferred250毫秒','错误30～300秒；版本变化合并30秒','取消旧版本不是业务失败；输入ready可唤醒', 'engine/p38/jobs.py','row.available_at=at+250'),
        ('数据库死锁重试','4次；25毫秒指数+随机扰动','保留；先消除长事务与锁顺序冲突','毫秒退避不是反复选币；重试不持旧事务', 'engine/db_retry.py','time.sleep(.025*2**attempt'),
        ('数据库连接长占用日志阈值','10秒','保留10秒诊断阈值','这是耗时告警线，不是10秒查询周期或买入门槛', 'engine/db.py','time.monotonic()-started>10'),
        ('交易所预算统计窗','UM/spot/archive 60秒；raw/statistics/funding 300秒','保留现有窗和额度实现','限流窗不是自行设计的采集频率，不能为降频统一改掉', 'engine/pd2/p36_budget.py','LIMITS='),
        ('启动就绪总等待/探测','60秒 / 500毫秒 / 单次2秒','保留60秒总等待；探测2秒','仅启动时运行，不是常驻业务负担', 'tools/local_start_helpers.ps1','function Wait-LocalReady'),
        ('本地DB启动探测','500毫秒','2秒','只启动阶段探测；不改服务端事务锁语义', 'tools/local_db.py','time.sleep(0.5)'),
        ('compose健康探测','5秒/15秒；超时3秒/5秒','30秒；保留合理单次超时','本地当前PowerShell进程不是容器；容器配置列作部署路径备选', 'compose.yml','interval:'),
        ('Nginx SSE/代理读超时','3600秒','保留3600秒','代理兼容路径；连接保持时间不是刷新频率', 'infra/nginx-erji-location.conf','proxy_read_timeout'),
        ('登录会话有效期','28800秒＝8小时','保留8小时','认证用途不与行情时效混用', 'engine/config.py','session_seconds:'),
    ])
    a('策略与兼容参数',[
        ('分析/K线周期','模型允许15/30/60/120/240/480/720分钟、1D、1W；当前v40启用1H/4H/1D/1W','保留已启用周期','周期粒度不是获取频率；周线UTC周一锚点统一', 'engine/pd2/config.py','周期须已注册'),
        ('原P33入场计划寿命','1根策略触发周期K线；expires_at=known+周期','本轮保留；如需延长另讨论策略版本','1H为1小时、4H为4小时等；不同于当前资格90秒', 'engine/strategies/p33.py','entry_lifetime_bars'),
        ('近期前高/事件高点窗口','30日 / 45日；特征30日','保留','来自策略结构定义，缩采集负担不缩历史', 'engine/strategies/p33.py','recent_high_days'),
        ('挤压/箱体回看','120根 / 最近5根 / 边界20根','保留','按K线输入版本计算一次，不按秒重复重算', 'engine/strategies/p33.py','squeeze_lookback'),
        ('双底间隔与候选超时','最小5根；超时60根4H＝10天','保留','结构成立条件，不是任务deadline', 'engine/strategies/p33.py','double_bottom_timeout'),
        ('回踩/收复窗口','6根4H＝24小时 / 3根4H＝12小时','保留','真实策略时窗；不沿用盘口TTL', 'engine/strategies/p33.py','retest_timeout_4h'),
        ('指标/保护回看','ATR14 / EMA20、50 / MACD12、26、9 / BB20 / trailing22根','保留','指标周期以bars计；所有相关源代码位置留在完整索引', 'engine/strategies/p33.py','"ema":'),
        ('事件研究回放周期','24小时 / 72小时','保留研究用途','不作为生产推荐前置等待', 'engine/strategies/p33_replay.py','horizons_hours'),
        ('研究样本时间要求','min_days默认90天','保留研究字段，非买入发布门槛','本轮不恢复旧研究报告硬门槛', 'engine/pd2/config.py','min_days:'),
        ('关闭的外源刷新默认值','1800秒','不启用；兼容读取保留，待独立清理','CMC/ByK等不是活跃采集路径，不能当它们仍在请求', 'engine/pd2/config.py','interval_seconds:int=Field(1800'),
        ('通用provider缓存TTL/refresh默认','300秒 / 1800秒','按实际启用用途拆；关闭外源不恢复','默认函数参数≠每条来源在运行；全量索引保留位置', 'engine/pd2/providers.py','ttl=300000,refresh=1800000'),
        ('通知冷却/消息TTL','1800秒 / 300秒（enabled=False）','保留禁用','不是本轮10分钟自动监控；不启用任何外部通知', 'engine/pd2/config.py','cooldown_seconds:'),
    ])

    # Broader than the original numeric-line search: include indirect named time
    # policies, call-site timers, strategy bar windows, startup and proxy paths.
    files=[];matches=[]
    pattern=re.compile(r'interval|timeout|ttl|lease|renew|retry|backoff|expire|valid_until|next_(?:at|check|review|retry|due)|fresh|stale|as_of|received_at|closed_at|known_at|expected_close|sleep|setInterval|setTimeout|cooldown|lookback|lifetime|horizon|_(?:days|hours|seconds|minutes|ms)\b|ping_|keepalive|poll|deadline|delay|duration|window|period|step|age_ms|timestamp|monotonic',re.I)
    exclusions={'__pycache__','node_modules','.next','tests','test','generated','.venv'}
    candidates=[]
    for base in ('engine','services','apps/web/src','infra'):
        candidates.extend((root/base).rglob('*'))
    candidates += [root/p for p in ('compose.yml','tools/start-local.ps1','tools/local_start_helpers.ps1','tools/local_db.py','tools/stop-local.ps1','tools/restart-local-workers.ps1')]
    for p in sorted(set(candidates)):
        if not p.is_file() or p.suffix not in ('.py','.ts','.tsx','.ps1','.yml','.yaml','.conf') or any(x in exclusions for x in p.parts) or p.name=='api.generated.ts':continue
        text=p.read_text('utf-8-sig');rel=p.relative_to(root).as_posix()
        files.append({'path':rel,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
        for n,line in enumerate(text.splitlines(),1):
            if not pattern.search(line):continue
            matches.append({'path':rel,'line':n,'text':line.strip(),
                            'has_numeric_literal':bool(re.search(r'\d',line)),
                            'scope':'deprecated_or_reference' if any(x in line.lower() for x in ('cmc','bykaranteli','aster','hyperliquid','geckoterminal','shadow')) else 'requires_call_path_review'})
    definitions=[]
    for file in files:
        path=root/file['path']
        if path.suffix!='.py':continue
        definitions.extend(_python_definitions(path,root,pattern))
    relations=_relations(ref)
    for row in rows:
        row['related_source_relations']=[r['id'] for r in relations if any(s['path']==row['source']['path'] for s in r['source'])]
        row['current_parameter_kind']=_kind(row['name'])
    captured=datetime.now(timezone.utc).isoformat()
    effective_path=out/'time-effective-settings.json'
    effective=json.loads(effective_path.read_text('utf-8')) if effective_path.exists() else None
    scope={'roots':['engine','services','apps/web/src','infra','compose.yml','named local startup tools'],
           'files':len(files),'time_related_lines':len(matches),'curated_parameter_groups':len(rows),
           'syntax_definitions':len(definitions),'logical_relations':len(relations),
           'excluded':['third-party/generated code','tests','historical docs/reports','secrets and .env','other checkouts'],
           'method':'时间关键词、间接时钟引用及调用点扫描；人工归并有明确语义的参数。代码匹配行不是全部独立参数或全部活动路径。'}
    payload={'captured_at':captured,'profile_status':'inspection_before_discussion','business_changes':False,
             'effective_settings':effective,'scope':scope,'parameters':rows,'logical_relations':relations,
             'candidate_profile_status':'revision2: ordinary T1/OI/volume/tracking300s; proposal only; latest coherent design in section13'}
    (out/'time-parameter-design.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),'utf-8')
    (out/'time-rule-inventory-full.json').write_text(json.dumps({'captured_at':captured,'scope':scope,'files':files,'definitions':definitions,'matches':matches},ensure_ascii=False,indent=2),'utf-8')
    groups=list(dict.fromkeys(r['group'] for r in rows))
    summary=table(['用途','建议周期','配套时效 / 更新方式'],[
        ['普通T1 + OI + 成交额 + 观察更新','统一300秒一轮','稳定分片，完成一批交接一批；整轮240秒目标未测'],
        ['普通 / T2 / T3实时OI','300 / 60 / 60秒','触发沿用90秒、采完即评估；参考字段及15M显示600秒'],
        ['成交额 / 目录','300秒 / 1800秒','成交额600秒时效，目录不拖住成交额；现货/合约各用批量ticker'],
        ['T2结构与趋势','新闭合K线/输入版本唤醒；600秒兜底','输入未变复用；缺输入120秒兜底'],
        ['T3当前买入 / 投影','30秒合并检查','报价/盘口90秒有效；原策略寿命不改'],
        ['近买点盘口 / 报价快照','30秒复核取最新，按变化提交','不串联60秒落库再等30秒检查；原始事实时刻保留'],
        ['观察走势 / 列表页面','重点60秒、普通300秒 / 60秒兜底','实质事件刷新5秒合并；当前资格不依赖走势缓存'],
        ['统计与心跳','60秒','活跃role心跳期限180秒；慢任务按用途单列'],
    ])
    parts=['## 10. 时间参数全量检索与低频配置建议（本轮新增）',
           '候选稿已同步第13节第2版：普通T1/OI/成交额/观察统一5分钟，重点1分钟，T3 30秒。旧问题现状及17:42证据保留，不能把建议值写成激活结果；旧普通3分钟建议撤回。',
           f"扩展扫描 {scope['files']} 个源代码/基础设施文件、{scope['time_related_lines']} 条时间相关行，归并为 {len(rows)} 组可审阅参数。包括未带数字的间接时钟引用，补充启动、代理、容器、策略回看；完整行索引见time-rule-inventory-full.json，可检索参数名、路径和行号。源码默认/公式与当前发布配置分开记录。",
           '18:05（北京时间）只读核对本地effective接口：发布配置v40仍为OI普通60秒、候选15秒、重点10秒，通用collector为60秒；目前没有激活本节的新值。其他未通过effective接口暴露的硬编码值按当前源码标注，不冒充生产配置。',
           '### 10.1 建议采用的低频配置',summary,
           '普通T1五分钟批次加OI采集/排队会延后录入；批内回查保留已有证据，不能恢复未采到的OI峰值。T3每30秒当前复核、实质事件页面5秒合并，快速穿越买点仍可能被错过；同用途参考600秒和报价/盘口90秒是配套候选，不冒充原已发布值。',
           '新valid_until = min(原策略到期, 真实报价时刻+90秒, 真实盘口有效时刻+90秒)，每30秒按原价格区间、保护和策略条件复核。最长90秒是事实可接受年龄，不是承诺推荐固定保留90秒：价格越界、策略失效、保护触发或真实数据超时都可能更早退出；条件持续成立则用新事实持续更新。',
           '### 10.2 降低的是查询与重计算频率',
           'Binance主动推送、内存收包、1秒价格证据采样、协议PING和提交owner检查按职责保留。普通采集/检测/写库五分钟分批合并，重点压缩全量SQL和重复重算。回查15分钟窗口内最近5分钟触发至少需20分钟样本，本设计保留30分钟事实缓冲。',
           '任务执行完成立即补位；空闲才慢轮询。预算owner按可用额度/事件唤醒；不在每个任务后强制sleep30秒，不放大原交易所额度。聚合成交、强平事件和已收盘K线事实不按last-only丢弃，改批量持久化须保留原始明细和时间。',
           '### 10.3 参数总表：现状、建议值、配套改动',
           '表中“保留”表示该时间承担窗口、网络等待、历史或协议职责，不代表它是高频业务刷新。每项可追溯到源码；完整重复使用位置保存在全量索引。']
    for group in groups:
        parts += [f'### {group}',table(['参数','现状 / 默认','建议','配套处理与源码'],[
            [r['name'],r['current'],r['proposed'],r['handling']+'；`'+r['source']['path']+':'+str(r['source']['line'])+'`'] for r in rows if r['group']==group])]
    parts += [
        '### 10.4 实现时必须一次改齐的引用',
        table(['参数族','必须同时更新'],[
            ['报价90秒','ExecutionConfig的默认和le上限；decision/zone；p33_current；p33_delivery；service中的兼容路径；signal_projection；observation_query；p39.pipeline的跨区间/订阅选择；前端过期与接口schema'],
            ['盘口90秒','ExecutionConfig.depth_max_age_seconds；book_clock.fresh默认；native_streams写入DataCapability期限；execution_cost/qualify；所有valid_until派生；快照与APIschema'],
            ['OI300/60/60秒 + trigger90秒/reference600秒','普通批次/重点独立调度；采完即T1判断；current_market_fields的15M展示用途拆分；repository/derivatives/service同用途统一；前端360秒硬编码；effective标签'],
            ['心跳60秒 / 有效180秒','各采集器/owner/receiver上报；health和operations；p38_active判断；observation-board 30秒发现服务判旧；所有按当前run读取的诊断工具'],
            ['T2复查600秒','pipeline成功next_check；jobs.next_review；取消observation_query390秒一刀切；依赖版本与收盘事件唤醒'],
            ['task120/20秒保留；采集inflight120/30秒候选','jobs/candle_gaps按原角色续租；due中inflight与retry拆分；等待阶段续租、取消释放、owner/version提交保护'],
            ['前端低频','use-refresh默认；事件minGap；observation/opportunity/chart各自定时器；SSE进展；可见/隐藏/分页；保留详情布局及K线内容'],
        ]),
        '不得只改配置数字留下硬编码上限：当前模型quote≤15、depth≤10，单改新配置会验证失败；即使绕过模型，旧book_clock、投影或观察函数仍可能让新信号10秒后过期。schema生成、有效值报告和测试期望必须随真实消费者更新。',
        '### 10.5 预计收益与测量方式',
        '仅按计划周期估算：重点OI10→60秒减少约83%调度需求，候选15→60秒减少75%，普通60→300秒减少80%。T1原每轮结束睡2秒，实际周期还有计算/落库/心跳，因此不能用2→300秒比例当CPU收益；新批内回查也增加计算量。所有收益须测HTTP、SQL、等待与用户可见延迟。',
        '结构重算次数按输入版本计数；未变K线复用比统一十分钟重算更有效。固定5分钟/1分钟采点仍要按真实时间检查单侧60秒端点，HTTP抖动和层级切换可导致缺口，不偷扩大窗口。记录两次扫描间的有证据触发及当前区间穿越。',
        '### 10.6 更新步骤与回滚',
        '① 先落地唯一时间policy、用途名称及引用清单，使用现有值做一致性验证。② 拆成交额/目录、合并空闲SQL扫描、T2输入版本复用和T3轻检查。③ 将该低频配置作为一组版本发布，成套迁移默认值、校验上限、硬编码与有效期；不得每改一个数就重启一次。④ 用户要求激活时再重启，核对新run的真实送达/覆盖/页面可见延迟。',
        '回滚也按同一组配置与代码版本恢复，保持生产数据和历史计划。既有计划里冻结的execution_config若仍是10/15秒，会与新公共policy冲突：对未结束计划用明确的timing_profile版本决定工程时效，技术策略/买入区间/目标/保护继续冻结；保留旧快照原时间语义，避免批量篡改历史JSON。此项须在实现中列出兼容路径。',
        '交付：更新后的HTML/Markdown方案、time-parameter-design.json、time-rule-inventory-full.json、time-effective-settings.json。这里只更新方案；未发布低频配置、未重启、未恢复自动监控。',
    ]
    candidates_md='\n\n'.join(parts)+'\n'
    appendix=out/'P39_时间参数总表与低频配置建议.md'
    appendix.write_text(candidates_md,'utf-8')
    inventory_parts=[
        '## 10. 全量时间参数现状与逻辑关系（先检查，再讨论）',
        '本轮按用户最新要求先整理现状，不决定新周期、不发布参数、不修改业务逻辑。上一版的10/15秒高频保留建议不作为确定实施标准；30秒、60秒等低频数值只是尚未讨论的候选。当前方案的执行部分暂作问题处理方向，不作为立即实施指令。',
        f"本轮扩展到 {scope['files']} 个源代码、服务与基础设施文件，提取 {scope['time_related_lines']} 条时间相关行和 {scope['syntax_definitions']} 条Python语法定义/派生式；人工归并 {len(rows)} 组参数主题、核对 {len(relations)} 组逻辑关系。计数口径不同，不能把语法行当成独立配置项。完整索引同时保留默认参数、字典/Field定义、派生公式及其源码行号。",
        '范围包括engine、services、apps/web/src、infra、compose.yml及本地启动脚本；第三方、生成文件、测试、其他checkout和历史报告不计入活动源码。包含兼容路径但单列；没有读取密钥或.env。关键词/语法检索不能证明不存在未命名的间接时间规则，因此未确认的调用路径在索引中保留待核对状态。',
        '18:05（北京时间）本地effective接口只读核验：发布配置v40，普通/候选/重点OI为60/15/10秒，通用collector为60秒。18:19补核active接口：报价15秒、盘口10秒；启用1H/4H/1D/1W，通知仍关闭。其他表项标的是当前代码默认、硬编码或公式；冻结计划可能持有旧execution_config，不能把源码默认当成所有旧计划的实际值。17:42运行数字仍为前次审计快照，本轮不是新的运行健康检查。',
        '### 10.1 六类时间不能混为一个刷新周期',
        table(['类别','含义','实例与关系'],[
            ['采集/业务刷新频率','多久发请求、读库、重新计算','OI10秒、producer3秒；这类是本次重点讨论降频的对象'],
            ['事实有效期','数据年龄多大才仍可用于某种判断','book10秒、quote15秒；采集降频必须联动检查消费者'],
            ['窗口/策略寿命','统计覆盖多久、事件可以参与多久','5/15分钟异动、1根K线入场期限；不等于行情TTL'],
            ['租约/排队/超时','任务所有者和单次操作的最长等待','task120秒续租20秒、预算420秒、HTTP45秒'],
            ['协议/本地计时','长连接保活、内存采样、界面到期隐藏','WS PING20秒、内存1秒、本地隐藏1秒；不直接发REST或重算'],
            ['历史/缓存/认证期限','复用身份、历史回看与登录会话','列表快照1小时、供应量24小时、会话8小时'],
        ]),
        '### 10.2 当前参数总表（值、职责、代码）',
        '每组列当前值和具体职责；建议数值不混入现状表。所有重复使用位置和间接表达式另在可检索源码索引中展开。',
    ]
    for group in groups:
        inventory_parts += [f'### {group}',table(['参数主题','当前值 / 默认 / 公式','时间职责 / 同源关联','代码位置'],[
            [r['name'],r['current'],r['current_parameter_kind']+'；同源关系 '+(', '.join(r['related_source_relations']) or '完整索引'),
             '`'+r['source']['path']+':'+str(r['source']['line'])+'`'] for r in rows if r['group']==group])]
    inventory_parts += ['### 10.3 当前逻辑关系与冲突依据']
    for relation in relations:
        refs='；'.join('`'+s['path']+':'+str(s['line'])+'`' for s in relation['source'])
        inventory_parts += [f"### {relation['id']} · {relation['name']}〔{relation['classification']}〕",
                            '**公式或数据传递：**'+relation['relationship'],
                            '**检查结果：**'+relation['finding'],
                            '**涉及位置：**'+refs]
    inventory_parts += [
        '### 10.4 讨论前可确定的事实',
        '1. 真正的频率错配已存在：成交额30分钟生产/6分钟消费、观察30秒缓存/15秒可买许可、分析5分钟复查/390秒显示期限、预算420秒等待/采集240秒预留。',
        '2. 同一数据的不同用途可以有不同期限，OI实时触发90秒和参考360秒不是天然错误；同名状态缺少用途以及同用途API不一致才需要收敛。',
        '3. 10秒盘口TTL、10秒连接告警和10秒维护循环是三个完全不同的参数。前两个不是“每10秒请求一次”，维护循环会重复读写；如果取消高频，应先决定要降低哪一类业务查询/重计算。',
        '4. 只改OI10秒无助于producer3秒、统计5秒、executor0.5秒的全量SQL开销；只把买入检查调到30秒却保留book10秒，会产生新的过期冲突。',
        '5. 策略原入场寿命、当前资格有效期和页面停留必须分开讨论。原计划长期存在不等于一直可买；报价、盘口接收、结构确认、价格区间共同派生当前valid_until。',
        '### 10.5 下一步讨论项（不预设答案）',
        table(['需要决定的问题','讨论时涉及的参数'],[
            ['希望多久更新一次当前推荐','T3检查、报价/盘口事实TTL、页面事件合并、页面兜底刷新'],
            ['观察池与T2重点是否采用不同OI频率','普通/T2/T3采集、trigger年龄、15分钟端点网格、历史覆盖'],
            ['哪些计算只在输入变更后执行','T2结构/趋势、K线收盘、next_review、输入版本、超时状态'],
            ['哪些定时查询改通知或完成驱动','producer、executor、budget、心跳统计、receiver维护、DB合并落库'],
            ['计划入场寿命是否单独调整','既有策略expires_at、冻结版本；与10秒盘口有效期分开'],
        ]),
        '完整交付包括time-parameter-design.json、time-rule-inventory-full.json、time-effective-settings.json与可检索HTML。低频候选稿单独保存，当前没有确认或激活新值。业务服务和持续监控状态不变。',
    ]
    md='\n\n'.join(inventory_parts)+'\n'
    (out/'P39_时间参数现状与逻辑关系.md').write_text(md,'utf-8')
    _inventory_html(root,out,rows,matches,definitions,scope)
    return md,payload


def _inventory_html(root,out,rows,matches,definitions,scope):
    cards=[]
    for r in rows:
        vals=[r['group'],r['name'],r['current'],r['current_parameter_kind']+'；同源关系 '+(', '.join(r['related_source_relations']) or '完整索引'),r['source']['path']+':'+str(r['source']['line'])]
        cards.append('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in vals)+'</tr>')
    raw=['<tr><td>'+html.escape(m['path'])+':'+str(m['line'])+'</td><td><code>'+html.escape(m['text'])+'</code></td></tr>' for m in matches]
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39 · 时间参数可检索总表</title><style>body{margin:24px;font:15px/1.6 "Microsoft YaHei",sans-serif;color:#213849}h1{font-size:25px}input{padding:12px;width:min(600px,90%);font:inherit;border:1px solid #9bb5c5;border-radius:6px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #d7e3eb;padding:10px;text-align:left;vertical-align:top}th{background:#eaf2f7}code{white-space:pre-wrap;overflow-wrap:anywhere}section{overflow:auto}td{overflow-wrap:anywhere}table{min-width:900px}summary{cursor:pointer;margin:20px 0}#count{margin:12px 0;color:#526575}[hidden]{display:none!important}</style><h1>P39 · 时间参数可检索总表</h1>'''
    page+=f'<p>{len(rows)}组归并参数；{scope["files"]}个文件；{len(matches)}条源码时间相关行。当前只检查、统计并讨论；新值尚未确定。匹配行不等于独立参数或正在运行路径。</p>'
    page+='<p><a href="P39_timing_consistency_plan.html#section-10">返回现状与逻辑关系方案</a></p><input id="query" placeholder="搜索参数、当前值、关联、源码路径"><div id="count"></div><section><table id="params"><thead><tr>'+''.join('<th>'+s+'</th>' for s in ['分类','参数','当前/默认','时间职责 / 同源关联','源码'])+'</tr></thead><tbody>'+''.join(cards)+'</tbody></table></section>'
    declared=[]
    for d in definitions:
        vals=[d['name'],d['kind'],d['path']+':'+str(d['line']),d['expression'],', '.join(d['depends_on'])]
        declared.append('<tr>'+''.join('<td><code>'+html.escape(str(v))+'</code></td>' for v in vals)+'</tr>')
    page+='<details><summary>Python时间默认值、字段定义与派生表达式（'+str(len(definitions))+'条；含动态事实派生，不全是可编辑参数）</summary><section><table id="defs"><thead><tr><th>名称</th><th>定义类别</th><th>位置</th><th>表达式</th><th>依赖名称</th></tr></thead><tbody>'+''.join(declared)+'</tbody></table></section></details>'
    page+='<details><summary>完整源码行索引（含间接引用、历史/兼容路径；展开后同样可搜索）</summary><section><table id="raw"><thead><tr><th>源码位置</th><th>时间相关语句</th></tr></thead><tbody>'+''.join(raw)+'</tbody></table></section></details>'
    page+='''<script>const input=document.getElementById('query');function filter(){const q=input.value.toLowerCase();for(const id of ['params','defs','raw'])for(const row of document.querySelectorAll('#'+id+' tbody tr'))row.hidden=!row.textContent.toLowerCase().includes(q);const n=[...document.querySelectorAll('#params tbody tr')].filter(r=>!r.hidden).length;document.getElementById('count').textContent='参数组匹配 '+n+' / '+document.querySelectorAll('#params tbody tr').length}input.addEventListener('input',filter);filter()</script></html>'''
    (out/'P39_time_parameter_inventory.html').write_text(page,'utf-8')
    (root/'apps/web/public/previews/P39_time_parameter_inventory.html').write_text(page,'utf-8')


def _python_definitions(path,root,pattern):
    source=path.read_text('utf-8-sig')
    tree=ast.parse(source)
    results=[]
    parents={child:parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    def record(name,node,kind):
        expression=ast.get_source_segment(source,node) or ast.unparse(node)
        numbers=[n.value for n in ast.walk(node) if isinstance(n,ast.Constant) and isinstance(n.value,(int,float)) and not isinstance(n.value,bool)]
        contexts=[];parent=parents.get(node)
        while parent is not None:
            if isinstance(parent,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):contexts.append(parent.name)
            parent=parents.get(parent)
        results.append({'path':path.relative_to(root).as_posix(),'line':node.lineno,'name':name,'kind':kind,'context':'.'.join(reversed(contexts)),
                        'expression':expression,'numeric_literals':numbers,
                        'depends_on':sorted({n.id for n in ast.walk(node) if isinstance(n,ast.Name)})})
    for node in ast.walk(tree):
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            args=node.args.posonlyargs+node.args.args
            for arg,value in zip(args[-len(node.args.defaults):],node.args.defaults):
                if pattern.search(arg.arg):record(node.name+'.'+arg.arg,value,'function_default')
            for arg,value in zip(node.args.kwonlyargs,node.args.kw_defaults):
                if value is not None and pattern.search(arg.arg):record(node.name+'.'+arg.arg,value,'keyword_default')
        elif isinstance(node,ast.AnnAssign) and node.value is not None:
            name=ast.unparse(node.target)
            if pattern.search(name):record(name,node.value,'annotated_assignment_or_field')
        elif isinstance(node,ast.Assign):
            for target in node.targets:
                name=ast.unparse(target)
                if pattern.search(name):record(name,node.value,'assignment_or_derivation')
        elif isinstance(node,ast.Dict):
            for key,value in zip(node.keys,node.values):
                if isinstance(key,ast.Constant) and isinstance(key.value,str) and pattern.search(key.value):record(key.value,value,'dictionary_field_or_derivation')
    return results


def _kind(name):
    if any(s in name for s in ('寿命','回看','窗口','周期','间隔与候选超时','归档','冷却')):
        if any(s in name for s in ('刷新周期','回放周期','策略','入场','观察冷却','分析/K线','异动','双底','回踩','挤压','前高','指标')):return '统计窗口 / 策略时窗（不是秒级刷新）'
    if any(s in name for s in ('租约','续租','预留','lease','owner')):return '所有者 / 任务预留（需与等待时间联动）'
    if any(s in name for s in ('有效期','时效','TTL','判旧','valid_until','宽限','期限')):return '事实或缓存有效期限（非请求周期）'
    if any(s in name for s in ('超时','排队上限','恢复保护','退避','告警','探测','PING','收包等待')):return '操作等待 / 协议 / 诊断阈值'
    if any(s in name for s in ('关闭','通知','研究样本')):return '禁用或参考 / 研究路径（需核对启用状态）'
    if any(s in name for s in ('内存','过期计时','预算统计窗','策略','回看','窗口')):return '证据采样 / 窗口或本地计时'
    return '业务查询 / 更新或检查周期（重复请求需区分事件路径）'


def _relations(ref):
    data=[]
    def add(name,classification,relationship,finding,sources):
        data.append({'id':'L'+str(len(data)+1).zfill(2),'name':name,'classification':classification,
                     'relationship':relationship,'finding':finding,'source':[ref(*s) for s in sources]})
    add('成交额生产与T1资格','已复现冲突','目录及volume刷新1800秒 → source/received_age≤max(360秒,collector×3) → liquidity → T1 evaluate',
        '当前360秒有效期短于1800秒生产周期，17:42的522条合约全部过期；WS价格和单币OI不能更新volume时钟。',[
            ('engine/adapters/binance_universe.py','>= 1800_000'),('engine/p37/store.py','max_age=max(360000')])
    add('OI调度与触发/参考时效','交付延迟已复现，用途差异可合理','OI due普通60/候选15/重点10秒 → 预算/HTTP/DB交付 → detector OI90秒、参考OI360秒、历史面板180秒',
        '目标小于TTL不代表实际达标；17:42仅101/560在90秒内。降频时应一起决定触发可接受年龄，不能只改due。',[
            ('engine/pd2/collection_schedule.py','OI_INTERVALS ='),('engine/pd2/current_market_fields.py','CHANGE_MAX_AGE='),('engine/pd2/service.py','"oi_status":"fresh" if oi')])
    add('15分钟OI端点与采点网格','既有约束，降频联动项','target=end_time-900秒；start∈[target-60秒,target] → delta=(q_end/q_start-1)',
        '拟定普通5分钟、重点1分钟名义上整除15分钟，单侧60秒匹配仍受真实HTTP时刻与层级切换影响；需实际配对，不能放大容差偷换窗口。',[
            ('engine/pd2/current_market_fields.py',"end-duration,60000 if label=='15M'"),('engine/p37/store.py','detector.delta(q,now,900000,60000,90000)')])
    add('通用采集配置影响多个TTL','确定耦合','max_age=max(360秒,collector_interval_seconds×3) → price、OI、volume、历史端点容差',
        '调普通采集周期时会连带改变不同字段状态；相同配置值不代表同一业务职责。',[
            ('engine/repository.py','max_age=max(360_000'),('engine/p37/store.py','max_age=max(360000')])
    add('观察tracking与可买许可','确定逻辑错配','普通tracking30秒 / active5秒 → cached buy_eligible；读取同时要求tracked≤15秒及当前actionable',
        '普通观察每轮后半段可能因缓存过期压掉计数；当前未定位实际漏买币。tracking统计不应额外授予或撤销最新资格。',[
            ('engine/p37/store.py','interval=case('),('engine/p38/observation_query.py',"row['buy_eligible']=bool")])
    add('结构复查与显示过期','逻辑差异及队列延迟','成功next_check/next_review=+300秒；观察checked_age>390秒 → delayed',
        '只留下90秒排队余量；近期结构排队P95=583秒，且日周输入不变也可能被compute年龄一刀切判旧。',[
            ('engine/p39/pipeline.py','state.next_check_at = at +'),('engine/p38/observation_query.py','now-checked<=390000')])
    add('K线收盘与分析/健康','确定判定差异','expected_close按周期UTCfloor；trend要求latest≥expected；structure允许close_age≤周期+180秒',
        '新收盘边界的待到达状态不一致；周线锚点还必须统一UTC周一，不得拿日周received_age当秒级行情TTL。',[
            ('engine/p37/analysis.py',"rows[-1]['close_time']<now//step*step"),('engine/pd2/structural_history.py','DURATIONS[tf]+180000'),('engine/pd2/data_health.py','def expected_close')])
    add('采集预留、预算等待和HTTP','静态不匹配，重复请求未证实','due预留240秒；budget队列≤420秒；已发请求lease90秒；传输≤45秒',
        '最长预算等待大于预留；单dispatcher pending已有保护，不能说已经重复HTTP。inflight、retry和已发HTTP租约不同。',[
            ('engine/pd2/derivative_collection.py','capacity,240000'),('engine/pd2/p36_budget.py','QUEUE_SECONDS=420'),('engine/adapters/base.py','asyncio.timeout(45)')])
    add('任务租约与续租','当前配合合理','task租约120秒、每20秒续租；提交验证owner/version；任务完成释放',
        '续租远短于租约，本身不是冲突；续租降低频率时联动租约，防止旧owner结果写入。',[
            ('engine/p38/jobs.py','ttl=120000'),('engine/p38/jobs.py','await asyncio.sleep(20)'),('engine/p38/candle_gaps.py','await asyncio.sleep(20)')])
    add('同币互斥与10秒盘口','架构耦合，漏信号未证实','不同lane仍按p38_execute:<iid>同币互斥；结构执行P95=24秒；book有效10秒',
        '长计算可占住轻检查；当前entry无延迟样本。只放大TTL无法消除排队，锁范围与计算阶段也需检查。',[
            ('engine/p38/jobs.py',"key='p38_execute:'+iid"),('engine/p38/jobs.py','exclude_subjects=excluded'),('engine/pd2/book_clock.py','max_age=10000')])
    add('当前资格派生与计划寿命','不同时间合理，但引用重复','valid_until=min(plan.expires_at,market_at+15秒,book_effective+15秒,book_received+10秒)',
        '10秒是当前资格事实时效，不是整个策略只活10秒；各函数有硬编码与冻结config，改一个入口不会改齐。',[
            ('engine/p38/p33_current.py','valid_until=min(plan.expires_at'),('engine/strategies/p33_delivery.py','depth_max_age_seconds*1000'),('engine/pd2/signal_projection.py',"'received_at', 0) + 10000")])
    add('资金费刷新、已结算值与成本覆盖','确定逻辑差异','普通历史1800秒/重点120秒；尾部observed_through≤180秒；结算值≤实际interval+180或300秒',
        '三种时钟不同：查询时刻、结算时刻、周期边界。普通成本尾部确认只能保持短段时间；当前成本是参考，未证明直接挡买入。',[
            ('engine/pd2/collection_schedule.py',"kind=='funding_history'"),('engine/pd2/derivatives.py','def settled_costs'),('engine/repository.py','(interval+300)*1000')])
    add('心跳上报与健康截止','汇总判定缺口','analysis/live上报5秒；operations活跃owner≤30秒；role默认300秒；health默认300秒',
        '不同入口时效不同；如果上报降到60秒但不改30秒consumer，就会周期性误报。必须另报数据进展和覆盖。',[
            ('engine/p38/jobs.py','now_ms()-reported>=5000'),('services/api/main.py','now-owner.updated_at<=30000'),('services/api/main.py','ttl=expected.get(role,300_000)')])
    add('前端刷新、短资格与分页','静态风险，已有过期隐藏','SSE1秒合并；poll30秒；单页8秒；全部页到齐才返回；本地每1秒按valid_until隐藏',
        '正常SSE有快速路径；无事件/多页延迟可能消耗资格时间。每秒隐藏不发请求，不能误判成1秒REST。',[
            ('apps/web/src/components/opportunity-board.tsx','timeoutMs:8000'),('apps/web/src/components/opportunity-board.tsx','refresh()},1000'),('apps/web/src/lib/use-refresh.ts','setBusy,30000')])
    add('T1内存连续性与数据库合并','当前证据职责，降频重点','内存价格最小1秒、窗口端点5秒容差、15秒断档；报价writer0.05/0.5秒落库',
        '降DB写入不等于降内存接收；若内存改30秒而不改gap条件会让所有窗口失效。成交/强平事实不能只留latest。',[
            ('engine/p37/detector.py','if any(b[0]-a[0]>15000'),('engine/pd2/native_streams.py','buffer.pending else .5')])
    add('任务完成补位与空闲轮询','避免引入新吞吐损失','executor每0.5秒检查完成与取新；dispatcher每1秒检查；producer3秒全量扫描',
        '工作可完成即补位；空闲可慢兜底。所有循环统一sleep30秒会将每项额外拖慢，不能这样降频。',[
            ('engine/p38/jobs.py','await asyncio.sleep(.5)'),('engine/pd2/continuous_collection.py','asyncio.FIRST_COMPLETED'),('engine/p38/jobs.py','3-(now_ms()-started)/1000')])
    add('连接长占用告警与数据锁','诊断阈值，不是刷新/业务门槛','数据库连接占用>10秒 → warning；admission acquire未显式设timeout；会话结束释放',
        '10秒日志阈值不阻止买入；没有显式准入等待上限也需要在阶段耗时里识别。短事务/锁顺序问题不能仅靠降低告警频率解决。',[
            ('engine/db.py','time.monotonic()-started>10'),('engine/db.py','if depth==0:_admission.acquire()')])
    add('参考FX与供应量','当前刷新与时效配合合理','FX刷新60秒、TTL1小时；供应量刷新6小时、TTL24小时；cap=真实供应量×当前原生价',
        '慢变字段无需随买入秒级重拉；同时不可用一次行情更新给旧供应量重贴fresh。',[
            ('engine/pd2/binance_facts.py','FX_MAX_AGE ='),('engine/pd2/binance_facts.py','next_ms=received+21600000')])
    add('策略入场期限与历史回看','策略语义，不是性能旋钮','P33 expires_at=known+DURATIONS[触发tf]；近期高30日，双底timeout60根4H等',
        '改变这些值会改变策略识别与参与寿命，不能作为性能优化附带修改；先决定是否单独讨论策略版本。',[
            ('engine/strategies/p33.py','expires_at=known+DURATIONS[tf]'),('engine/strategies/p33.py','entry_lifetime_bars')])
    add('历史索引、时间锚点与冻结证据','边界风险及合理约束','OI期待整点/5分钟端点；week cache部分按epoch周；真实周线用UTC周一；历史known_at≤decision',
        '供应商发布时延未复核，不把短暂缺端点当数据错误；补数不应让未来事实出现在过去决策。',[
            ('engine/pd2/derivative_collection.py','def recent_coverage'),('engine/pd2/p35_schedule.py','now//(604800000'),('engine/repository.py',"offset=345600000 if resolution=='1W'")])
    add('身份缓存与事实期限','不同用途合理','排序快照1小时/24小时；会话8小时；每次可买仍按当前事实资格检查',
        '这些TTL保证分页或登录身份，不应降低到10秒，也不能用于放大当前行情fresh。',[
            ('engine/pd2/signal_projection.py',"'expires_at':now+3600000"),('engine/pd2/pagination.py','expires_at=now+86400000'),('engine/config.py','session_seconds:')])
    add('观察来源需求与T2服务时间','来源寿命合理，需与处理延迟区分','observation需求expires=source_at+cool_ms（默认2小时）；manual/pinned可无到期；live_filter按来源状态判断',
        '需求存活允许T2处理，不授予买入；selector轮询不应延长过时需求。需求结束也不意味着删除真实历史。',[
            ('engine/p39/pipeline.py',"keep(iid, 'observation', ident, last + config['cool_ms']"),('engine/p39/pipeline.py','def live_filter')])
    add('120秒选中优先与排队任务轮换','已确认路径，实际损耗待量化','queued/leased且admission年龄<120秒获得held优先；换出active后retire_unneeded仅结束queued/failed，保留leased',
        '近期结构排队P95=583秒超过优先保持窗口；可能轮换尚未开始的任务，造成重新入队。过120秒本身不会立即终止正在执行的任务，须按task ID统计轮换次数后归因。',[
            ('engine/p39/pipeline.py','at - admissions.get(iid, 0) < 120000'),('engine/p38/jobs.py','def retire_unneeded')])
    add('兼容结构10分钟余量与P33收盘判定','按版本区分的边界差异','signal_service先P33.observe，再对兼容箱体检查1H close_age≤4200秒、4H≤15000秒；通用technical周期+180秒',
        '兼容路径10分钟余量和3分钟工程余量不一致，但不能声称P33全被兼容报价检查挡住。统一判定须显式保留各策略版本语义。',[
            ('engine/signal_service.py','p33_result=observe('),('engine/signal_service.py','>15000_000'),('engine/pd2/analysis.py',"DURATIONS[resolution]+180000")])
    return data
