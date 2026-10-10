# 币安 API 与 Skills：ERJI 性能与数据可靠性优化研究

研究日期：2026-10-06。代码基线：`D:/Axiangmu/erji/work/production-fix-20260929/src`。运行采样：北京时间23:33:48，批次 `20261006T145326-f085e57d`。

本报告研究整个系统的效率与数据可靠性。SUPER仅用于验证历史覆盖，C98不是研究主线。结论来自官方文档、当前代码和一次性本地运行采样；建议尚未实施，不代表生产已经恢复。本次未安装或执行币安技能，未修改业务代码、配置或生产数据库，也未恢复已暂停的监控。

## 1. 先回答最重要的问题

**币安已有能够改善ERJI的官方能力，但安装更多Skill不会自动修好我们的链路。最优先的工作是正确使用已有数据能力，并缩短本地处理与发布延迟。**

| 问题 | 结论 | 对ERJI的意义 |
|---|---|---|
| 有没有官方能力提升采集效率？ | 有：实时流、原生历史分页、日/月归档、全市场行情、统计端点、官方SDK/CLI。[S2–S7] | 多数已经接入；重点是补全覆盖、复用采集与计算结果，避免重复处理 |
| 有没有现成技能直接算所有币前高、ATH、目标涨幅？ | 已核对的公开市场API与Skills未发现通用的此类结论接口 | 原始数据来自币安；前高、结构与目标仍要定义、计算和验收。截图内部AI流程算法未知 |
| 有没有官方“现成信号”？ | 有，Web3技能提供聪明钱事件、平台策略、自定义策略及部分回测。[S11–S13] | 主要是链上信号，不能等同于永续合约技术买入建议 |
| 现有工具有没有用错？ | 确认有历史范围使用不完整、两套阻力口径；还有待验证的时间单位等兼容隐患 | 数据不一致不能全部归因于API质量，更不能用新增指标掩盖 |
| 前端没有买入是不是缺一个Skill？ | 没有证据支持这个结论 | 必须逐级核对触发、执行条件、快照、投影、API可见和页面展示；无买入不自动证明故障或规则正确 |

建议顺序：**修正数据契约与历史范围 → 验证采集到页面延迟 → 统一分析结果 → 小范围评估新增参考能力。**

## 2. API、Skills、CLI和币安AI分别是什么

| 类型 | 实际作用 | 能否替代ERJI后台 |
|---|---|---|
| 市场API | 按确定参数返回K线、OI、费率、盘口、成交等事实 | 是数据输入；不替代策略与调度 |
| WebSocket | 持续推送最新事件，减少REST轮询与请求额度使用 | 是实时输入；仍需重连、补缺、入库和新鲜度处理 |
| 官方SDK | 封装HTTP/WS接口、模型及部分连接逻辑 | 可以减少重复封装；迁移后仍要保持现有共享预算和证据，不保证更快 |
| Skills | 指导AI如何选择接口、参数、CLI和解释结果 | 适合诊断、研究、人工操作；通常不是毫秒级后台引擎 |
| `binance-cli` | 官方交易所CLI，包含现货、合约、Alpha和其他账户能力 | 适合人工核查和契约验证，不建议每个币、每个事件启动一个CLI进程 |
| `baw` / Agentic Wallet | Web3信号、策略、回测、钱包与链上操作CLI | 与交易所CLI范围不同；有账户、链和权限边界 |
| 截图中的币安AI | 产品内部运行`bn-strategy-cases/kline-indicator-checkup`流程 | 公开仓库没有查到同名目录，无法确认内部代码、可调用接口或算法 |

公开Skills Hub快照固定到提交 `9960c675387bd27f8866645b83693c1fa87242f6`。[S1] 通用`binance`技能涵盖多个交易所业务；本研究深入市场数据、实时流、Alpha和Web3信号，支付、借贷、下单等仅核对范围，没有测试这些操作。

## 3. 官方API能力：哪些真正能帮助我们的系统

以下为官方SDK、官方REST/WS文档与Skills参考中的能力。表中路径以USDⓈ-M为主；“可用”表示文档存在，不代表当前账号、地区、市场和运行环境已经验收。

| 能力 | 官方接口/方式 | 我们能获得什么 | 适合的优化 | 注意事项 |
|---|---|---|---|---|
| 合约目录和规格 | `/fapi/v1/exchangeInfo` | 交易状态、原生symbol、上市时间、价格/数量规则 | 统一身份、上市边界、退市处理、历史起点 | 交易所合约历史不等于代币全市场历史；不能只按显示ticker合并 |
| 原生历史K线 | `/fapi/v1/klines`，`interval/startTime/endTime/limit` | 同一执行市场的OHLCV与时间 | 为日线/周线/前高分析补齐确定范围，并缓存 | 不传时间默认近期数据；请求权重按limit分档，不是每请求相同 |
| 大规模冷历史 | `data.binance.vision`的daily/monthly文件及CHECKSUM | 批量历史K线与成交归档 | 减少逐页REST的冷历史成本 | 归档有发布延迟、可能修订；需校验和、单位、版本和缺口验证 |
| K线实时流 | `@kline_5m/1h/4h/1d/1w`等 | 当前形成中的K线及闭合状态 | 增量更新；收盘时驱动分析，不重复拉整窗 | 官方提供1m不代表我们应启用1m；保留用户确认的5/15分钟发现与现有分析周期 |
| 实时报价 | mark、miniTicker、bookTicker等 | 标记价、成交价或买卖报价 | 广度发现与入场复核分工，减少REST | 三种价格语义不同；标记价不能直接冒充可买成交价 |
| 盘口 | `/fapi/v1/depth`与partial/diff depth流 | 可见档位价格与数量 | 优先保障活动计划的可执行报价；减少重复快照拉取 | top20不是完整深度；diff流必须按序维护，不能混用算法；标准depth不含RPI订单[S2] |
| 当前OI | `/fapi/v1/openInterest` | 当前原生持仓量与时间 | 对重点追踪/活动计划优先轮询；本地保存历史 | 已核对公开流未见等价的全市场原生OI实时通道；不能假设所有OI可换成WS |
| OI历史 | `/futures/data/openInterestHist` | 按周期的持仓量与名义价值 | 断电后补近期基线、校验本地OI变化 | 官方仅保留最近约一个月；免费接口无法凭空恢复多年前OI[S2] |
| 资金费率 | `/fapi/v1/fundingRate`、`premiumIndex/markPrice` | 已结算费率、预测费率与下次时间 | 区分已发生事实与未来参考，避免错误累计 | 最新预测值不是已结算费用；费率不是任何币永远固定8小时 |
| 费率周期调整 | `/fapi/v1/fundingInfo` | 有调整的合约费率上/下限与结算周期 | 补充周期变更证据 | 只返回发生调整的symbol；缺席不能推成“没有资金费”[S2] |
| 主动成交方向 | `aggTrade`、`/futures/data/takerlongshortRatio` | 主动买卖数量/成交方向 | 复用已有成交流，或按需补统计口径 | `m=true`是买方为maker，主动方为卖方；统计端点有历史保留限制 |
| 多空结构 | global/top account ratio、top position ratio | 账户比例和持仓比例 | 可小范围增加结构参考，评估是否帮助解释OI | 账户数量比例不等于仓位比例，更不等于资金净流入；不能据此保证上涨 |
| 清算流 | `forceOrder` / all-market liquidation stream | 采样到的清算事件 | 补充冲击事件证据 | 原生流是采样，不保证完整；没收到不等于清算金额0 |
| Alpha | token-list、exchange-info、klines、ticker、depth与流 | Alpha目录与该市场行情 | 完善Alpha身份与本市场数据，降低混源 | Alpha、链上池、现货、永续必须分别保存价格和执行标的 |

关键限频例子：当前官方合约SDK列出K线limit为100–499时weight=2，500–1000时weight=5，超过1000时weight=10；不是一次拉得越多，每根成本就一定越低。OI/多空等statistics虽然某些weight=0，仍有1000请求/5分钟/IP限制；fundingRate/fundingInfo共享500请求/5分钟限制。[S2] 当前预算注册表已正确处理已用的OI历史、费率、K线和深度，新增统计端点应先补注册，不能落入未知端点的默认路径。

现货WS文档明确24小时连接周期、20秒ping、每连接1024 streams和每秒5个入站控制消息等约束。[S4] 这些数值不能未经核对直接套到合约或Alpha。现有合约代码已分`/market/stream`与`/public/stream`，不应把这一分离重新当作尚未实现的优化。

## 4. 官方Skills：适用范围与产品价值

| 官方技能 | 已查到的能力 | 对ERJI的潜在帮助 | 推荐位置/优先级 | 尚待验证 |
|---|---|---|---|---|
| `binance` | 现货、USDS合约、Alpha、Convert及多项账户业务CLI | 官方参数参考、人工交叉检查、接口契约测试 | 研发诊断；P1 | 安装不是提速手段；不迁移现有鉴权或启动交易 |
| `query-token-info` | search/meta/dynamic/kline，支持ETH/BSC/Base/Solana | 地址与元数据、链上流动性、链上K线补充 | 链上/Alpha补充；P2 | 明确返回价格是USD/池口径，不覆盖永续历史 |
| `crypto-market-rank` | trending/Alpha、热度、聪明钱流入、meme和地址PnL榜 | 丰富发现输入，减少只依赖一个Rising榜 | 发现参考；P2 | 榜单不是全市场完整数据；各子命令链支持不同 |
| `meme-rush` | 新发行、即将迁移、已迁移与热点话题 | 链上新币发现、生命周期解释 | 链上扩展；P2 | 支持链按命令区分；不是合约形态分析接口 |
| `trading-signal` | BSC/Solana聪明钱买卖事件、触发价、最大涨幅、退出率 | 独立外部事件与回顾参考 | 独立来源标签；P2 | 事件不是当前仍可买；不能迁成SUPER/C98永续技术信号 |
| `binance-trading-signal` | 智能钱、平台/用户策略、meme/fomo策略管理、部分回测和credits | 学习信号生命周期与结果统计，研究链上扩展 | 产品研究；P2 | 高级skill链表比简单smart-money端点更广；需按具体子命令验证；fomo不支持同样的回测 |
| `query-token-audit` | 合约、交易、诈骗风险及买卖税信息 | 链上标的事实补充 | 链上资产详情；P2 | `hasResult=false`或`isSupported=false`不能当低风险；不增加永续入场硬门槛 |
| `query-address-info` | 指定链地址的持仓快照 | 地址持仓查询 | 辅助研究；P3 | 地址快照不是合约市场OI |
| `binance-wallet-tracker` | 地址/群组监测、买卖事件、回放分析、WS推送 | 链上聪明钱事件发现与复盘 | 链上独立扩展；P2/P3 | CLI/账户/权限与具体链能力需验收；不能当交易所盘口替代品 |
| `academy-skill` | 官方知识、课程、教育内容检索 | 术语帮助与说明 | 帮助层；P3 | 不改善核心行情延迟，也不是选币策略 |
| 钱包、支付、借贷、订单执行类 | 通用技能目录中存在相关能力 | 与当前“告诉我现在可以买什么”的数据分析目标关系有限 | 本轮研究不建议接入执行 | 目录确认不等于全部功能已深入核验或可用 |

**Skill中“已有信号”与“预测涨幅”不是一回事。** `highestPrice/maxGain/peakArrivalCostMs`是触发后的历史结果字段，不是未来收益或预计到达时间。可借鉴这些字段建立我们自己的结果统计，但不要把事后赢家数据倒灌为当时的买入依据。[S12]

## 5. 当前系统哪些地方需要处理：确认程度和优先级

这里区分“已复现”“代码确认”“运行采样”“兼容隐患”“待评估”。不是按文件名旧来认定无用代码。

| 编号 | 问题与证据等级 | 当前证据/代码位置 | 影响 | 建议 |
|---|---|---|---|---|
| B01 | 日线历史需求没有覆盖目标用途；已复现，P0 | 本地SUPER日线约305根，从2025-12-05起；官方一次返回1000根，从2024-01-11起。`p35_schedule.windows`最低300根，非全历史 | 漏前高，中间阻力缺失，图表历史与周线目标不一致 | 为“指标预热”“近期触发”“上方阻力”“ATH”分别定义覆盖需求；提交/验收日线冷历史，不只增加一个limit常量 |
| B02 | 长历史入库却未参与目标分析；已复现，P0 | `pd2/service.load_bars`默认400根4H；本地完整SUPER4H有6270根 | 已经下载的旧高点被短分析窗排除；只补库仍不能解决 | 实时指标用小窗；历史结构用独立索引/范围查询；缓存已确认结构，避免每次全历史重算 |
| B03 | 图表结构与计划结构算法不统一；代码确认，P0 | `pd2/analysis.technical`支持配置pivot及角色转换；`p33_lifecycle.freeze_plan`用另一pivot实现、固定宽度和跨周期选择；`p35_candidates`把跨周期t1放进`targets_4h` | 图表与列表可能不同，周线目标容易被误读为近端目标 | 统一结构事实和来源，保留冻结计划；统一前要回放对比，不能直接改历史计划 |
| B04 | 归档覆盖以1H/4H为主；代码确认，P1 | `history_backfill.target`只在60/240间轮换，1H只选代表性标的；`p35_archive`interval/step只适配1H/4H | 已有归档工具无法直接完成所有币原生1D长期覆盖 | 扩展原生1D需求和解析；按活动标的先补，再分冷通道覆盖；周线聚合须有完整UTC日线 |
| B05 | 现货归档时间单位兼容隐患；待复现，P1 | 官方public-data：2025-01-01起现货归档用微秒；`p35_archive.parse`直接按毫秒使用整数 | 若该解析器扩展到现货新归档，可能过滤空、时间错位或拒绝 | 对输入来源/日期做显式单位契约；用官方小样验证，不能只按数字大小盲猜。当前主要冷任务筛binance合约，未证明生产已发生此故障 |
| B06 | 本地发布仍有延迟风险；运行采样，P0 | 23:33采样：投影queued142、保护106、趋势49；一市场分片队列pending226，queue-wait P95约7.06秒 | 10秒级执行盘口有效期可能在处理和发布间被耗尽 | 分开测接收、入库、分析、快照、投影、API可见时间；优先执行/保护，减少重复投影与重算。数量是任务行，不是币数；单样本不能证明长期卡死 |
| B07 | 报价pending不能直接当采集故障；运行采样，P1 | 合约pending233、最老1.44秒、写P95约2.11秒；现货pending154、最老2.97秒、写P95约2.45秒 | 若只看累计合并量或队列数量，会误判丢数据并错误调参 | 最新报价允许合并，真实成交事实不能合并；看年龄、overflow、commit和覆盖。此次未见quote overflow，不能据此证明所有标的均新鲜 |
| B08 | 旧输入被更新时取消，统计成error；代码确认/瞬时采样，P1 | `p38/jobs.execute`把`analysis_inputs_superseded`走failed/error；采样见SEI，下一次查询failed任务已消失 | 正常版本取消和真实异常混在一起；若频繁发生，也可能白做分析 | 单列superseded/cancelled与真正failed；测浪费CPU、重试次数及是否饥饿，不能看到一次就删除版本保护 |
| B09 | fundingInfo周期调整尚未使用；代码检索确认，P1 | 已采mark/premiumIndex与fundingRate；预算识别fundingInfo，但未找到采集调用 | 结算间隔推导缺少官方调整对照，尤其周期变化时 | 按目录/低频缓存加入周期调整证据；仍区分预测与结算；费用保持参考，不恢复硬门槛 |
| B10 | 新统计API接入需完善预算登记；代码确认，P1 | `p36_budget.specification`仅将openInterestHist路由到statistics；暂未找到多空/taker统计调用 | 新增能力若绕过统一预算，会造成频繁限流或默认串行 | 接端点前先注册配额家族、权重、窗口、并发及429退避，复用现有aggTrade数据优先 |
| B11 | 新Skill字段存在官方文档不一致；文档确认，P2 | `trading-signal/SKILL.md`说maxGain是百分数字符串；其reference和高级skill说小数比例；status枚举也不一致 | 可能出现100倍涨幅、旧信号仍可买、错误生命周期 | 保留raw、按接口版本建立字段契约和样本测试；未验接口不要接入正式买入链路 |
| B12 | 标记价、成交价、盘口价混用风险需要持续验收；现有分层已实现，P1 | `native_streams`存price_kind，`execution`存真实盘口；发现与执行分开 | 后续合并接口/显示时可能丢语义，造成价格不一致 | 保留price_kind、market_id、event_time；用同时间同市场对照。不能因为价格不同就直接断言某一源错误 |

本轮采样还见一个市场分片`ConnectionClosedError`正在重连；一次重连不足以判断持续故障，应核对掉线时长、恢复、K线缺口和重放。`current_signals`采样有效buy行数为0，也不能单独证明策略没触发或数据传输正确。记录位于`runtime-one-shot.json`。

## 6. 已经用对的能力，保留并完善

| 已有能力 | 实际实现 | 判断 |
|---|---|---|
| 广度行情WS与5分钟发现 | `native_streams.py`、`p37/worker.py`、`p37/detector.py` | 已有，不应重新改回逐币REST广度扫描；5/15分钟内存窗口重启后需要真实暖机 |
| 最新报价合并、事实分流 | `LatestQuotes`、独立写队列 | 合理；合并数不是丢失币数；调优需看年龄和实测写入速度 |
| 合约实时盘口top20 | 合约depth走`/public/stream`，保存原子top20快照 | 已有；不要套用完整diff簿同步算法，也不能假装top20可成交无限金额 |
| OI原生量与历史 | `binance_universe.py`、`derivative_collection.py` | 已有；名义价值增长不一定代表加仓，要区分价格变化与数量变化 |
| 主动成交方向 | `native_streams.trade_event`使用`m`判断主动方 | 当前方向解释正确；补新统计前先评估复用这套事实 |
| 清算采样标注 | `force_event`及窗口`sample_observed/not_observed_not_zero` | 当前没有把未观察填成0，保留这种语义 |
| 持久补窗/共享限频 | `p38/candle_gaps.py`、`p36_budget.py` | 已有；缺的是对应数据用途的需求和验收，不是再建一套平行调度 |
| 冷归档checksum | `p35_archive.py`校验SHA-256 | 已有；扩展1D/现货时保留校验和并修正时间单位 |
| 成本参考政策 | `decision.py`为`reference-only-v1` | 已按用户确认取消固定费率/盈亏比的独立入场否决；本报告不建议恢复 |

## 7. 哪些优化最可能提升性能

### 7.1 用已有事实，减少重复采集与计算

让每个原生市场/周期的K线、盘口、OI各有清楚的数据所有者。前端、观察、计划、图表读取同一事实版本，不各自取数或计算。历史请求按instrument、周期和时间范围合并，保留现有补窗租约；新Skill作为补充来源，不能绕过这一机制。

实时EMA/MACD/ATR用稳定预热窗和增量缓存；历史前高、ATH、角色转换用独立的结构索引，按新闭合K线更新。不能为了看三年前阻力，就每次报价都把所有历史重新计算。增量计算必须与批量基准对比，并处理修订、断档、重启恢复和版本变化。

### 7.2 把短有效期的结果先发布出来

现有entry/protect已经在同一次围栏执行内调用投影，不应再泛泛建议“增加立即投影”。需要继续验证这个实现是否实际足够快：同一币的围栏是否被慢结构分析占用、重复projection任务是否必要、数据库连接占用是否拖慢盘口提交、输入不断更新是否导致重做。

Skill/CLI不是解决上述延迟的工具。优先队列、连接与计算路径，需要以本地阶段耗时证明原因；扩线程、加并发或把预算开到90%都不能代替这个检查。

### 7.3 用官方归档补冷历史，REST补近期，WS更新实时

归档适合已发布的冷历史，REST适合最新尾部与小缺口，WS适合实时更新。日线长历史先补观察/活动计划相关标的，再覆盖广度池；只有完整覆盖证据通过，才能称“已完成历史前高/ATH分析”。API失败或归档404应保留缺口，不造K线。

### 7.4 增强数据可以增加参考，但应先证明收益

`fundingInfo`对周期解释较直接，可低频补充；taker统计与多空比例可先用隔离样本观察，评估对误报率、解释性和接口成本的影响。Web3排名、聪明钱、合约审计适合对应链上标的；不要把它们变成所有合约买入必须通过的审批。

## 8. 优化实施建议与验收条件

以下是研究建议，不是已经执行的变更或新的既定策略。时间/覆盖目标需在实施前落实为明确配置和验收口径。

| 阶段 | 范围 | 必须验证 |
|---|---|---|
| 第一阶段：数据使用纠正 | B01–B05，覆盖需求、原生日线、结构索引、两套阻力对齐、单位兼容 | 同市场同时间OHLC一致；覆盖起止/缺口可查；旧阻力可追溯；图表/候选/计划区分冻结与实时；修订不伪造历史known_at |
| 第二阶段：发布性能 | B06–B08，阶段耗时、重复作业、版本取消、共享围栏、写入排队 | 真实有效触发能在现有有效期内进入快照、投影、API、页面；失效原因有记录；性能改善不靠放宽门槛或延长TTL掩盖 |
| 第三阶段：官方能力补充 | B09–B12，费率周期、按需统计及可选Web3参考 | 预算注册正确；单位/字段有真实样本；账号/链范围可验证；不会将链上事件冒充永续信号，也不会恢复成本硬门槛 |

建议建立一条可复核的计数链：**真实触发 → 执行条件合格 → 买入快照 → 有效投影 → API有效期内可见 → 前端显示。** 每次退出对应一个具体原因和时间；同时记录等待计划、区间外、数据缺失、规则未满足等正常结果。发现事件、观察条数、已完成任务数不能代替买入产出数量。

性能验收要包含：event-to-commit、quote/book年龄、ready-queue wait、分析耗时、snapshot-to-projection、projection-to-API、API-to-screen的P50/P95及最大值；持续至少一个完整5/15分钟发现窗并覆盖一次4H边界更有代表性。具体采样时长按测试场景确定，本报告的一次采样不构成持续验收。

预算使用率只是资源约束；不能承诺必须消耗90%才有效率。可用信号数与误报率也不能仅凭他币事后上涨比较；使用当时已知数据回放，记录未进入、迟进入、撤回、保护触发和到达目标的情况，避免未来信息泄漏。

产品约束继续保留：买入机会为主要结果；已确认的展开详情和K线内容/格式尽量保持；T1/T2/T3是后台资源与流程分层，不需重新堆成前端多级审批页面；本轮不新增1分钟数据，不自动交易，不把固定费用或目标空间重新变成买入硬门槛。

## 9. API使用中的具体误读清单

| 容易误读 | 正确处理 |
|---|---|
| 最近300根等于完整历史 | 指标预热与历史结构范围分别声明；上市以来ATH必须有完整范围证据 |
| `1m`在所有API都表示一分钟 | 交易所API用`1m`分钟、`1M`月；Web3 token kline文档用`1min`分钟、`1m`月，不能共享不加区分的映射 |
| 链上K线数组与交易所K线一样 | Web3数组为`[O,H,L,C,V,timestamp,count]`；交易所K线首项是open_time，不能用同一个解析器 |
| `pm=m`仍是价格 | Web3 token kline的`m`是市值序列，`p`才是价格 |
| maxGain“0.25”到处都是0.25% | references说小数比例=25%；同仓库其他说明不同，必须按端点/版本和真实响应验证 |
| 标记价就是当下买价 | 买入需参考执行市场ask/盘口；标记价适合合约定价背景，保留price_kind |
| OI名义价值上升等于新增仓位 | 同时看原生数量与价格，并对齐时间；不要跨市场重复相加 |
| 多空账户比例代表多空金额 | 分开账户数、持仓数、主动成交量，均不能单独证明资金方向 |
| forceOrder没来就是清算0 | 采样/未观察与0分开 |
| 预测费率就是已经扣费 | 预测、下次结算、已结算分开，按实际周期解释 |
| WS连接成功等于数据连续新鲜 | 看接收、提交、年龄、覆盖、断线补缺；心跳字段含义必须清楚 |
| `X-MBX-USED-WEIGHT`是我们的请求量 | 是IP维度已使用权重；其他程序可能共用IP，结合本地账本和响应头 |
| weight=0代表无限调用 | statistics仍有独立IP频率限制 |
| Skill参数说明就是生产验收 | 仍需真实响应、业务错误码、权限、分页、时间和单位校验 |
| maxGain/peakArrivalCostMs可以预测未来涨幅和用时 | 是历史结果，不是预测。预测需要单独方法与回测验证 |

## 10. 官方来源、证据和研究限制

本轮下载27份官方资料，26份成功，1份大型SDK总入口下载不完整；针对市场数据的SDK模块成功取得，足以核对本报告具体端点。manifest保存来源、时间、大小及SHA-256。旧官方网页直接抓取曾返回202空内容，相关结论使用官方GitHub文档/SDK交叉核对，不将空响应说成文档不存在。

| 编号 | 官方来源 | 本报告用途 |
|---|---|---|
| S1 | [Binance Skills Hub](https://github.com/binance/binance-skills-hub/tree/9960c675387bd27f8866645b83693c1fa87242f6) | 目录、版本与技能边界 |
| S2 | [官方合约SDK MarketDataApi](https://github.com/binance/binance-connector-python/blob/master/clients/derivatives_trading_usds_futures/src/binance_sdk_derivatives_trading_usds_futures/rest_api/api/market_data_api.py) | 原生接口、权重、保留范围与语义 |
| S3 | [官方Spot REST文档](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md) | REST字段、时间单位、限频、429/418 |
| S4 | [官方Spot WebSocket文档](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md) | 流、连接生命周期、控制消息与时间单位 |
| S5 | [官方Public Data](https://github.com/binance/binance-public-data) | 日/月历史、校验和、现货微秒及归档修订 |
| S6 | [USDS市场能力参考](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance/binance/references/futures-usds.md) | Skills市场端点范围 |
| S7 | [USDS实时流参考](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance/binance/references/futures-usds-streams.md) | 市场/盘口实时流范围 |
| S8 | [Alpha参考](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance/binance/references/alpha.md) | Alpha行情范围 |
| S9 | [Query Token Info](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/query-token-info/SKILL.md) | 链上metadata、行情与kline |
| S10 | [Crypto Market Rank](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/crypto-market-rank/SKILL.md) | 发现排名与过滤 |
| S11 | [Trading Signal](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/trading-signal/SKILL.md) | 简单聪明钱端点及说明差异 |
| S12 | [Binance Trading Signal](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/binance-trading-signal/SKILL.md) | 高级信号、生命周期、事后表现、策略与回测 |
| S13 | [策略参数说明](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/binance-trading-signal/knowledge.md) | 单位、策略类型和fomo回测边界 |
| S14 | [Meme Rush](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/meme-rush/SKILL.md) | 发行生命周期与话题 |
| S15 | [Token Audit](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/query-token-audit/SKILL.md) | 链上合约与交易风险信息 |
| S16 | [Wallet Tracker](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance-web3/binance-wallet-tracker/SKILL.md) | 链上跟踪与事件流 |
| S17 | [Academy](https://github.com/binance/binance-skills-hub/blob/9960c675387bd27f8866645b83693c1fa87242f6/skills/binance/academy-skill/SKILL.md) | 教育内容边界 |

本地证据目录：`D:/Axiangmu/erji/work/production-fix-20260929/src/docs/p39-validation/binance-official-research-20261006`。其中`manifest.json`为来源快照清单，`runtime-one-shot.json`为本次只读运行样本。日线实测原始数据见相邻`binance-super-1d-history-verification.json`，分析窗对照见`super-chart-target-audit-confirmation.json`。

限制：没有测新增Web3端点、账号权限、全部链范围或官方CLI性能；没有运行生产压力测试、数据库写入测试或新增策略回测；当前队列样本不是长期监控结论。针对阻力算法的预测质量、未来空间与时间范围，仍需单独讨论及回放，不从官方接口目录推断策略有效。


## 三项优先优化的实施状态

三项代码更新及459项隔离回归已完成，后台已按用户指令激活并通过本批次检查；日线补齐仍在进行。验收记录：http://127.0.0.1:18770/previews/Binance_three_optimizations_acceptance.html
