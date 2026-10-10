# P38 ERJI 全链路排查与架构整改方案

生成时间：2026-10-05T14:18:54+08:00（北京时间）。

用户授权两阶段：先排查并形成方案，后按已确定执行范围更新代码。第一阶段已完成，第二阶段可执行，不另等待确认。

## 结论与真实基线

当前数据链路在前进，并非前端完全没有数据：14:03 API46资产；14:08页面49资产。发现服务1020市场、713新鲜内存价格，但只有163具备5m价窗基准。MarketState和内存价格是独立时钟与价格来源，不能用713推断所有入场报价有效。

75项Python隔离回归与6项JS检查通过，但新增控制场景复现空cohort、首次基准价偏差和档案年龄误标延迟。已有回归未覆盖这些具体缺陷。

## 完整检查覆盖与限制

|步骤|链路|结果|证据|

|---|---|---|---|

|01|源码与运行版本|已记录：HEAD 0062c007…、262源码文件hash、现有dirty状态、实际监听PID与启动脚本配置来源；运行父进程manifest与监听子进程区分。|source-baseline.json / runtime-processes.json|

|02|服务 / DB健康|通过（采样）：18770/18771/18772/55432监听；正确接口/v1/health HTTP200。初次/health 404是诊断脚本路径错误，已更正，不列服务故障。|followup-api-db.json|

|03|交易所实时流 / OI / 费用|有数据缺口：发现心跳fresh_prices713/1020、ready_5m163；OI USDT市场90秒162/525、6分钟335/525。563旧基线包括不同范围，不能直接分母对比。|baseline-20261005.json|

|04|预算 / 限流 / 排队|运行采样+隔离通过：近期200反馈、3 pending/3 leases；现代参数和重启保护已核验；未外部压测，不把低利用率等于交易所故障。|followup-api-db.json / tests.xml|

|05|标准入库 / 幂等 / 来源时钟|隔离通过+设计问题：原生K线/OI/资金费率时钟回归；当前行情新鲜度与内存差异、长事务证据已记录。无生产写入。|tests.xml / age-lock-snapshot.json|

|06|异动 / BTC降噪|隔离通过+基准价缺陷：价格窗口、RVOL基准、OI原生数量、BTC缺证据保留、spot同资产合约规则回归通过；触发价存储偏差新增复现。|tests.xml / isolated-probes.json|

|07|观察持久化 / 长期跟踪|有缺陷：首次合并/归档/新事件/容量回归通过；样本刷新和来源年龄冲突已复现。不宣称采集完整。|isolated-probes.json / tests.xml|

|08|历史窗口 / 六周期|有数据缺口与设计问题：6个市场×4原生周期查看闭合数与间隔；部分5m17/21根，部分4H/1D停在旧边界。新上市日线36根可能是真实短历史。|baseline-20261005.json:sample_windows|

|09|策略 / 入场 / 风险|只读与隔离通过，执行受限：当前计划/快照/投影持续产生；真实buy=0，不能证明策略永不触发。生命周期和门槛在隔离测试；未下单、未制造买入信号。|baseline/followup / tests.xml|

|10|投影 / API分类 / 范围更新|隔离通过+编排问题：/signals机会HTTP200；当前页真实5等待、0买入。隔离scope与旧版本防覆盖通过；多个后台入口问题已确认。|browser-audit.json / tests.xml|

|11|API观察 / 增量 / 当前价|HTTP通过+查询问题：观察HTTP200约539ms；增量游标类型验证通过；全量聚合和时间语义问题。|baseline / tests.xml|

|12|SSE / 代理 / 刷新循环|通过（采样及隔离）：实际EventSource连通、收到reset；6项JS测试包含认证代理、慢请求、事件合并、失败恢复、隐藏页与轮询。|browser-audit.json / JS检查|

|13|前端雷达 / 观察|已复现显示缺陷：真实页46+/49资产显示，无pageerror；控制空→非空仍0/46，手动接受43/46。未访问用户原标签页React内存。|browser-audit.json / screenshot|

|14|机会 / 旧市场页|读取通过：两页真实数据与相应HTTP200；旧观察不是P37观察。严格买入分类保持，未调整阈值。|browser-audit.json|

|15|详情 / K线 / 指标 / 历史冻结|读取通过，保存控制检查限制：PUMPBTC六个读API21～118ms、图表iframe加载；生产PUT偏好被阻止，保存失败场景暴露Promise错误。快照一致性依赖隔离回归，未证明所有币所有周期画线正确。|followup-api-db.json / browser-details.json / tests.xml|

|16|设置生效 / 登录 / 通知 / 健康|读取与隔离通过，旧说明缺陷：设置真实参数说明错误；版本v39读取；登录与CSRF在隔离验证；来源与通知页HTTP200，全局通知关闭。未对生产发布配置/登录/绑定目标或发消息。|browser-details.json / browser-audit.json / tests.xml|

|17|锁等待 / 性能 / 连续性|局部证据，完整剖析未覆盖：采样advisory等待、长连接和多个市场锁；未捕获所有SQL分布，不能把全部等待归因某锁；未进行整小时观察或交易所故障注入。|age-lock-snapshot.json / jobs-current-tail.txt|

|18|垃圾文件 / 死代码 / 依赖|静态候选，删除未授权于本阶段：已有276文件审计、模块图和引用候选；未删除、未重构。第二阶段按R12核验后仅清理已证明项。|architecture-review-findings.json / source-baseline.json|

## 第二阶段必须执行的整改任务

本范围包含R01～R12。先完成P0数据到页面缺陷与单一编排，再做P1采集归属、事务边界、读模型与配置，最后完成P2错误处理和模块整理。每项记录实施状态；不能用“新增一个类”代替旧执行入口已真正收口。

### R01 · P0 · 空列表转为非空仍不显示

证据类别：已复现缺陷。

观察/复现：浏览器控制响应：首轮0/0，刷新后API46资产但页面0/46；点击新增后43/46（另3资产被上涨/放量筛选排除）。

证据文件：browser-audit.json:controlled；repro-empty-cohort.png

代码位置：`apps/web/src/components/observation-board.tsx:31–44`

执行范围：首次空→非空自动建立cohort；已有列表仍保留防跳行；显式刷新重建cohort；筛选为空与数据为空使用不同提示。

验收条件：回归覆盖空→非空、已有行→新增提示、手动刷新、切换归档/重点、增量删除和方向筛选；请求失败保留已显示结果。

### R02 · P0 · 首次价格引用较旧目录快照

证据类别：隔离库已复现缺陷。

观察/复现：触发内存价110，catalog旧价100；persist保存visible_price=100，之后收益跟踪基准已偏离触发时刻。

证据文件：isolated-probes.json:first visible basis price

代码位置：`engine/p37/detector.py:facts；engine/p37/store.py:persist；engine/p37/worker.py:discover`

执行范围：评估时冻结来源价格、价格时间、来源类型与触发窗口，一并传入persist。OI独立触发没有新鲜价时保留null，不用旧价或后来的价格补造首次基准；保持老档案不被重写。

验收条件：110/100案例首次价必须110；乱序、重复、压制转可见、OI无价、后续触发均保留明确来源；历史档案不批量重算。

### R03 · P0 · 观察降频被误判为采集延迟

证据类别：隔离库已复现缺陷。

观察/复现：普通档案30秒更新；20秒读API时接收器有0秒新价，档案已标delayed。冷却300秒与统一15秒判断同样冲突。

证据文件：isolated-probes.json:normal tracking interval vs read freshness；baseline/browser API

代码位置：`engine/p37/store.py:refresh_tracking/read_observations；engine/p37/contracts.py`

执行范围：拆分真实来源新鲜度、档案更新时间、分析时间与观察档位；读侧可用批量MarketState当前价或独立有界行情读模型更新展示，明确来源时间。降频样本不等于实时高低点，保留tracking_gap及样本口径。买入许可仍使用真实来源15秒门槛。

验收条件：档案20/30/300秒但来源新鲜显示降频/等待更新；真正旧来源仍延迟；旧档案不能变相赋予buy_eligible；未来时钟拒绝。

### R04 · P0 · 新旧分析/决策/投影重复入口

证据类别：源码确认且运行存在排队。

观察/复现：jobs run_signals/run_pd2/run_projection和P37 analyze_loop均推动策略；基线queued41、多个观察分析年龄超过1小时。不能仅据此认定全部延迟都由重复入口造成。

证据文件：age-lock-snapshot.json；baseline-20261005.json；A01/A03

代码位置：`services/worker/main.py:290/323/381；engine/p37/worker.py:analyze_loop`

执行范围：建立单一按instrument+依赖版本去重的业务任务入口、租约与公平调度；P37产出观察分析需求，旧定时器提交补漏需求。执行报价/盘口采集、结构扫描、计划更新、投影的所有权明确到一个编排器，保留受管理计划的持续风险检查和旧覆盖补漏。不可直接关闭旧任务导致没有替代执行。

验收条件：并发提交同市场只执行一个分析版本；更新到达运行中保留下一版本；失败重试、进程退出租约恢复、无观察但有活跃计划仍推进；不同市场并行；范围投影不删除他币或覆盖新版本。

### R05 · P1 · 三条K线补齐任务缺少统一缺口归属

证据类别：源码确认并有当前补齐证据。

观察/复现：P37补窗、jobs增量、P34远期补齐共同维护Candle；14:03仍在补AVAXUSDC历史，部分当前观察5m仅17/21根。并未逐请求证明每条都重复发送。

证据文件：baseline-20261005.json:sample_windows/heartbeat；A02

代码位置：`engine/p37/worker.py:collect_windows/window_targets；engine/pd2/history_backfill.py；services/worker/main.py:run_candles`

执行范围：统一K线需求仓储与规范化缺口区间声明/租约，分别保留实时收盘、发现11根基准、观察深窗、远期历史优先级；重复需求合并，不同原生市场不合并。WS已有闭合K线到达使对应缺口失效；待补范围以实际覆盖和上市日期为准。

验收条件：同instrument/timeframe重叠范围不并发重复补；高优先级不被远期历史饿死，低优先级有保底；租约到期/429/网络异常可恢复；新币不足300根不无限追问不存在历史；未来/乱序/未收盘检查保留。

### R06 · P1 · 扫描事务一次占多币锁，行情写入受共享资源牵制

证据类别：源码与只读运行证据。

观察/复现：结构扫描一个事务预先拿一批市场锁后逐币计算；14:12快照同一PID持多个advisory锁8秒。jobs本次启动多条10–35.5秒连接占用；基线MarketState15秒新鲜13/525合约、0/503现货，而P37内存713/1020。不同价源不能视为一对一等价。

证据文件：age-lock-snapshot.json:locks；jobs-current-tail.txt；baseline-20261005.json

代码位置：`engine/signal_service.py:_scan_signals；engine/pd2/service.py:refresh_plans/decision_list；engine/pd2/native_streams.py；engine/repository.py`

执行范围：先增加脱敏的DB入场/读取/计算/锁等待/提交计时；按市场提交，避免批量市场锁跨长计算；批量只读准备，业务计算尽可能在事务外，提交前校验输入版本。行情快照用按市场合并的有界写队列，事件时间不重置；交易/清算事实不合并丢弃。保留事件提交顺序保证。

验收条件：慢市场不持有其他市场锁；旧输入不得覆写新行情或新计划；每原生market OI、价格、funding独立时钟；写失败传播并有心跳；隔离并发测试证明无死锁/乱序。性能改善由阶段时延记录评估，不能承诺固定秒数。

### R07 · P1 · 预算重启每次都加300秒恢复期

证据类别：源码确认；当前等待已结束。

观察/复现：budget_owner.serve无条件设置migration_until=now+300秒。14:08保存账本已超恢复期；采样UM reported462、spot315、pending3/leases3，近期响应为200，不能把当前排队统称限流。

证据文件：followup-api-db.json:budget；A05

代码位置：`engine/pd2/budget_owner.py:serve；engine/pd2/p36_budget.py:initial/clean`

执行范围：按可信持久账本、新鲜checkpoint、未决lease/已发送请求与未知占用区分恢复状态；可证明的窗口恢复按剩余风险保留；未知账本仍完整保护。保留90%额度、独立statistics/funding限额、真实429/418冷却，禁止简单取消等待。

验收条件：隔离owner重启覆盖可信账本、旧/损坏checkpoint、悬挂lease、真实ban、未归因旧账本；不超额度；恢复原因与剩余时间可查。生产预算不做破坏性重启测试。

### R08 · P1 · 观察增量接口仍读全量JSON

证据类别：源码确认；API接口已实测。

观察/复现：GET观察基线539ms，先加载全部可见档案再过滤changed_ids。规模增长性能未做压力实测。

证据文件：baseline-20261005.json:http；A07

代码位置：`engine/p37/store.py:read_observations；engine/p37/contracts.py`

执行范围：按变更市场和asset定位当前主观察，返回资产级更新、删除与游标；全量初始页和历史事件可分页，计数用独立聚合；避免仅ID定向查导致相关市场替补丢失。必要时建立明确的当前观察读模型和索引，保留原始档案。

验收条件：同asset多市场、主记录变更、归档、pin、删除、游标失效/reset和全量/增量等价；大量历史下定向查询不装载所有payload；合约类型和OpenAPI同步。

### R09 · P1 · 设置说明与实际调度不符

证据类别：真实页面复现。

观察/复现：页面仍写REST4槽、OI30/60/300秒；代码现代预算20总槽、UM/spot各8；当前OI普通60/候选15/活跃10秒。首页固定radar但保留旧default_view。

证据文件：browser-audit.json:/settings；A09/A10

代码位置：`apps/web/src/components/system-pages.tsx；engine/pd2/collection_schedule.py；engine/pd2/p36_budget.py；engine/contracts.py`

执行范围：提供脱敏effective-config查询：参数实际值、来源、消费者、生效方式；前端显示真实预算/OI周期。隐藏无消费者default_view编辑项，保留老配置读取兼容。异动设置与计划设置可以分区，但保存/生效反馈明确。

验收条件：从API设置视图验证实际20/8/8和10/15/60；不得声称OI目标保证每币达标；旧配置发布与回滚不丢字段；P37直接保存下一读取生效；不展示凭据。

### R10 · P1 · SSE连通与业务进度混用

证据类别：只读实时链路已通过，诊断缺口源码确认。

观察/复现：Next代理SSE实际打开并收到reset；真实雷达连接正常时仍大量延迟。EventHub异常静默，成功UI连接不能说明端到端正常。

证据文件：browser-audit.json:sse/live；A14

代码位置：`services/api/main.py:EventHub；engine/repository.py:emit；engine/p37/worker.py:discover；前端各列表`

执行范围：分开SSE连接、行情覆盖、基准窗口、观察排队/完成、投影与结果更新时间；后台输出发现资格/触发/压制/可见/分析/计划漏斗和阶段时间。EventHub错误保留最近异常类型与恢复心跳，日志限频且脱敏。心跳明示当前任务身份，旧角色不计当前故障。

验收条件：接收事件→GET→列表增量完整闭环；队列溢出/reset/回放/断线轮询有效；每种等待有对应状态与时间；日志旧错误不影响当前健康判断。

### R11 · P2 · 图表偏好自动保存失败时有未处理Promise

证据类别：控制检查发现未处理错误路径。

观察/复现：详情PUMPBTC页面HTTP200、图表iframe加载，多个读API返回200；浏览器阻止生产PUT偏好时出现Failed to fetch。这是主动阻止写入的控制场景，不是正常页面失败证据。

证据文件：browser-details.json:blockedWrites/result

代码位置：`apps/web/src/components/chart.tsx:342/365；apps/web/src/components/asset-detail.tsx`

执行范围：自动偏好保存、周期切换和手绘保存的失败统一捕获并有可读提示，不能让图表读数据失败与偏好保存失败混淆；检查snapshot冻结、revision和当前周期图形锚点一致性。

验收条件：模拟PUT失败无pageerror；K线GET正常仍显示；snapshot不订阅实时；不同周期/market不复用错误结构线；真实页面检查不得写生产偏好，保存测试用隔离fixture。

### R12 · P2 · 模块边界与历史兼容缺少归属清单

证据类别：静态依赖确认，删除资格未证明。

观察/复现：5组依赖环，含局部导入。pagination/features.indicators无生产静态引用，仍有测试；p33_release仍有历史工具引用。没有证明初始化崩溃。

证据文件：architecture-static-inventory.json；A03/A08/A12/A13/A15/A16

代码位置：`engine/p37；engine/pd2；services/api/main.py；tools/`

执行范围：在前述单一编排和缺口仓储中明确模块方向、配置/任务/偏好仓储接口；提取重复纯指标基础函数但保留RVOL10/RVOL20语义；拆观察/诊断/配置领域接口和过大前端页面；产出生产入口/历史工具清单。只删除经动态入口、测试和兼容核验的死代码，不做大规模文件搬迁。

验收条件：导入图不新增跨采集/策略循环；规则输出回归等价；启动脱敏打印checkout/版本/配置来源；每个清理项附引用核验；迁移/历史证据/运行数据保留。

## 验证与激活边界

代码完成、隔离测试通过、当前进程加载以及实盘数据恢复分别报告。受影响前端做构建与真实页面检查；数据库和保存操作使用隔离环境。单一编排与采集队列必须有并发、重试、旧版本与失效租约回归。

当前授权不自动重启/停止服务；后台更新后列出需要重新加载的进程。不能删除生产数据、原始证据或放宽买入门槛。

数据库质量与发现窗口不足若由真实上市历史造成，显示明确限制；不靠填零、未来数据或降低阈值制造信号。

## 留作后续讨论

全面微服务迁移、历史raw_events容量/保留政策、阈值优化、策略利润与市场覆盖产品取舍，不列入本次未决定的执行范围。

## 证据目录

实际目录：D:/Axiangmu/erji/work/production-fix-20260929/src/docs/p38-validation。覆盖全部111条图连接的源码关系到检查组映射保存在p38-audit-findings.json；组验证不是每条边的全天生产证明。

原始证据保留；本轮诊断脚本路径错误和测试库名前缀保护错误已修正，不能列为产品故障。