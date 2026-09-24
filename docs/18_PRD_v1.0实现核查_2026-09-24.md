# ERJI · PRD v1.0 实现核查

核查日期：2026-09-24。API快照：2026-09-24T09:23:57.377Z；数据库快照：2026-09-24T09:24:44.968714+00:00。各请求不是原子快照，数量随市场变化。

当前版本是工程底座、部分原生行情与真实箱体扫描，尚未达到 PRD v1.0 的完整交付。42 个核查项已对照代码、实际 API、数据库及页面；另逐项映射 30 条 AC 和 37 项 OI 讨论。不能把页面存在、表已创建、接口200或样本验证当成相应业务已经上线。

## 直接结论

- **占比待接入：数据管线缺失。** 164个已保存市场的流通市值与OI比例全部为空。CMC样本成功不等于后台采集，更不等于供应分母已验收。
- **建议参与为空：功能尚未完成。** 深度/费用/净R未接，符合结构和价格位置也会停在execution_pending。本轮补上明确原因和逐币结构说明，没有绕过PRD条件发布建议。
- **1D不足：计算已有，24H端点不足。** 工作副本OI约6小时；157个历史不足、7个过期，无有效24H变化。
- **市场覆盖不完整。** 当前Binance永续150资产；HL/Aster各4市场；深采并集80资产。Spot、Alpha、Wallet Rising、链上池和CMC正式采集未上线。
- **当前17条主行：** 13待突破、3已确认、1失效；数据库18个episode包含多段事件。建议参与/试仓均0。
- **正式目录未同步。** 18770/18773运行C盘工作副本；D盘18771旧API仍仅4资产，public库0个campaign/episode。

## 方法与边界

基线：D:/Axiangmu/erji/docs/01_产品需求文档_v1.0.md（9月24日修订），配套任务矩阵、OI覆盖清单、CMC既有实测及M0验收。读取运行代码、本地API/OpenAPI、数据库表计数、OI跨度和周K样本；对照D盘旧服务。没有新增CMC请求、读取密钥正文、发送TG或交易。

状态区分：局部验证、部分实现、数据源未接入、业务未实现、实现偏差、样本不足、环境未同步、P1后续项。未完成的P0与原本计划P1的内容分开。

## 逐项核查

### 数据与OI

| 编号 / 状态 | 需求与现状 | 下一步 |
|---|---|---|
| D01 · 未接入 · P0 | **流通市值与 OI / 市值占比**（§4.1、§6.3、AC10）<br>164 个已保存市场的 circulating_cap 与 oi_cap_ratio 全部为 null。模型声明了字段，repository 未赋值；页面直接写死“占比待接入”。没有 supply_snapshots 表。 | 接流通供应/市值接口、身份映射、USD转换与同期分母；各字段带来源和时间，未知不得用FDV替代。 |
| D02 · 未接入 · P0 | **CMC 免费 API 正式采集**（§7.2、AC19）<br>既有报告验证过免费Key的衍生品/清算样本，但运行源 cmc=disabled；本次数据库没有CMC原始记录，正式环境的 cmc_key_file 引用未配置。用户指定密钥文件存在，本轮未读取正文或消耗CMC额度。 | 接后台密钥引用、最多5重点资产/30分钟预算起步、credit计数与70/85/95降级；供应/市值能力仍需独立验收。 |
| D03 · 未实现 · P0 | **跨平台 OI 合计、份额、去重与覆盖**（§6.2、AC09）<br>目前逐市场展示USDT/USDC名义OI，主行不是全站或已覆盖市场合计。没有USD汇率、统一合约/供应商映射、coverage revision及CMC冲突隔离管线。 | 实现原生优先的同合约去重、报价换算、纳入/排除明细，数量窗口按相同集合比较。 |
| D04 · 数据不足 · P0 | **数量 OI 1H / 4H / 1D**（§6.2.1、AC28）<br>单市场计算存在，1D正确复用24H；当前工作副本OI记录约6小时，24H窗口157个历史不足、7个过期，尚无有效24H值。未实现原生历史OI回补。 | 继续积累并按能力补历史OI；显示缺失原因、真实端点和覆盖。不能用24H价格或金额变化代替。 |
| D05 · 已验证局部 · P0 | **数量与价格导致的金额变化分解**（§6.2、AC08）<br>单市场已计算数量贡献与价格贡献，测试覆盖数量减少而名义金额增加；没有冒称资金流入。跨平台合计尚未实现。 | 保留现有语义，补跨平台同集合和规格测试。 |
| D06 · 部分实现 · P0 | **Binance 现货、永续、Alpha市场范围**（§1.2、§7.1）<br>只采USDT加密永续，按24H成交额选150资产，80资产深采。尚无现货/Alpha adapter；交割、币本位、仅现货资产没有目录。历史退榜记录导致库存156个Binance市场，不等于本轮扫描156。 | 接Spot及Alpha目录、身份和行情；列表分开显示库存、当前扫描、深采和信号数量。 |
| D07 · 未接入 · P0 | **链上 Meme / FOMO 及钱包飙升**（§1.2、§7.1）<br>wallet_rising=disabled，没有链+CA、Pool、链上OHLCV、市值≥1M/流动性筛选或可卖出性验证。 | 按已确认方案接Binance Wallet Rising发现，再接明确池子行情及质量校验。 |
| D08 · 部分实现 · P0 | **Hyperliquid / Aster 覆盖**（§1.3、§7.1）<br>两平台均只有BTC/ETH/SOL/HYPE各4市场，HL仅default namespace；结构引擎筛选venue==binance。尚不能用DEX长尾妖币数据触发信号。 | 从目录扩展已映射候选与活动事件，验收namespace、单位及必要成交字段后进入同一策略管线。 |
| D09 · 实现偏差 · P0 | **主市场流动性选择**（§6.4、AC07）<br>observation_groups优先有效Binance，再按平台名；没有同金额滑点、价差、±1%深度比较、15分钟评估或切换防抖。 | 接盘口和双向报价，同金额比较并保存选源理由；原计划继续绑定原市场。 |
| D10 · 部分实现 · P0 | **资金费结算、累计费用和压力**（§6.4、AC11）<br>已保存原生已结算费率和相邻结算间隔；没有1H/4H/24H累计、连续负费率时长、持仓情景费用、费用分位及综合费率。 | 基于实际结算序列计算完整窗口与缺口，展示主平台及异常平台。 |
| D11 · 未实现 · P0 | **OI异常分位与合约压力状态**（§6.1、§6.3）<br>现有价格/OI四象限和0.5%描述性过滤；无90天基线、300/60样本门槛、分位/防抖，也没有异常关注、持续费用挤压和高位再积累的正式触发器。 | 实现可比历史基线与分位规则，样本不足明确降级；技术路径独立运行。 |
| D12 · 未接入 · P0 | **盘口、成交差额/CVD、现货接力与基差**（§6.5、§7.1）<br>REST采mark、OI、funding和K线；未采spot_last/perp_last/index成套证据，未保存taker买量、成交流或L2。 | 分源接trades/depth/index与现货成交，检验时间对齐与缺口后再计算。 |
| D13 · 未接入 · P0 | **实际清算与爆空证据**（§6.5、AC12）<br>无原生清算订阅或CMC清算后台任务，数据库无清算表；“上涨减仓”只是解除描述。 | 原生采样事件与CMC窗口汇总分别展示，保留方向/窗口/覆盖，不重复相加。 |
| D14 · 部分实现 · P0 | **资产身份、倍数合约与同名币**（§1.3、AC09）<br>Asset id来自交易所base/ticker，1000类保持原单位，没有统一到代币的显式映射；无chain/CA、身份凭据或反向规范化。 | 建立稳定Asset与Instrument/Pool映射，不能去掉1000前缀后直接合并或套市值。 |
| D15 · 未接入 · P0 / P1 | **OKX复核与新增数据源契约**（§7.1、§7.3、AC17）<br>OKX的P0小范围复核未接；Gate/Bitget仍属P1。Source、adapter列表、UI平台字典及Binance专用扫描存在硬编码。 | 先补OKX小样本；注册capability与规格映射后再扩展，Gate/Bitget按P1实施。 |

证据：

- D01：[engine/contracts.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/contracts.py:68)；[engine/repository.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/repository.py:11)；[apps/web/src/components/phase-signals.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/phase-signals.tsx:11)。
- D02：[engine/config.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/config.py:1)；[engine/repository.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/repository.py:11)；[services/worker/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/worker/main.py:8)。
- D03：[engine/repository.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/repository.py:11)；[engine/models.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/models.py:124)；[engine/features/oi.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/features/oi.py:1)。
- D04：[engine/repository.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/repository.py:11)；[engine/features/oi.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/features/oi.py:1)。
- D05：[engine/features/observations.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/features/observations.py:40)；[tests/test_structure_signals.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/tests/test_structure_signals.py:1)。
- D06：[engine/universe.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/universe.py:1)；[engine/adapters/binance_universe.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/adapters/binance_universe.py:1)；[services/worker/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/worker/main.py:8)。
- D07：[engine/repository.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/repository.py:11)；[engine/models.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/models.py:124)；[services/worker/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/worker/main.py:8)。
- D08：[services/worker/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/worker/main.py:8)；[engine/adapters/hyperliquid.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/adapters/hyperliquid.py:1)；[engine/signal_service.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/signal_service.py:10)。
- D09：[engine/features/observations.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/features/observations.py:40)；[engine/adapters/base.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/adapters/base.py:1)。
- D10：[engine/adapters/cex.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/adapters/cex.py:1)；[engine/adapters/hyperliquid.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/adapters/hyperliquid.py:1)；[engine/adapters/binance_universe.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/adapters/binance_universe.py:1)；[engine/repository.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/repository.py:11)。
- D11：[engine/features/observations.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/features/observations.py:40)；[engine/signal_service.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/signal_service.py:10)。
- D12：[engine/adapters/binance_universe.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/adapters/binance_universe.py:1)；[engine/adapters/cex.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/adapters/cex.py:1)；[engine/models.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/models.py:124)。
- D13：[engine/models.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/models.py:124)；[services/worker/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/worker/main.py:8)。
- D14：[engine/universe.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/universe.py:1)；[engine/adapters/binance_universe.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/adapters/binance_universe.py:1)；[engine/models.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/models.py:124)。
- D15：[services/worker/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/worker/main.py:8)；[engine/repository.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/repository.py:11)；[engine/signal_service.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/signal_service.py:10)。

### 信号与策略

| 编号 / 状态 | 需求与现状 | 下一步 |
|---|---|---|
| S01 · 未完成 · P0 | **建议参与 / 建议试仓**（§3.3、AC01、AC29）<br>结构成立、现价在参与区时仍为execution_pending；没有深度/费用/净R评估，参与动作不会产生。空列表不是没有市场机会。本轮已增加未启用说明与逐币原因。 | 补齐成本后净R、执行金额/风险限制，满足早期净R≥1.5或确认≥1.8及其他规则才生成行动。 |
| S02 · 部分实现 · P0 | **四个P0策略模板**（§3.2、AC02）<br>box-20-60-v1包含1H提前、4H确认结构；只有box_breakout_research一个strategy_id。回踩确认、趋势再启动和独立策略开关未实现。 | 拆出四模板及独立计划，补回踩实际发生/收回确认与新箱体再启动。 |
| S03 · 实现偏差 · P0 | **长期追踪及退榜保护**（§1.2、§3.4、AC04）<br>campaign/episode与首报已保存；扫描限当前in_universe且deep_history，退榜或挤出深采后活动事件不再评估。无48小时结束/独立日线趋势保护，多策略主计划未实现。 | 活动计划、收藏与保护监控优先保留采集预算；补完整campaign结束和多episode规则。 |
| S04 · 部分实现 · P0 | **首报、首次确认、首次建议与不可变快照**（§3.4、§9.2、AC03、AC14）<br>首报时间和价格持久化，测试验证不改写；Plan与SignalSnapshot只有表，本次均0记录。证据直接改Episode JSON，缺首次建议价/config版本及统一冻结版本。 | 落Plan/Revision/Snapshot；列表、详情、图层、TG引用同一snapshot，保留历次状态。 |
| S05 · 未实现 · P0 | **已有仓位、风险预算及目标后管理**（§3.3–3.4、AC16）<br>无已有仓位/新参与双口径，无模拟本金风险额度、总开放风险、减仓/持有/退出计划或纸上成交。 | 补用户声明/纸上记录和风险测算，区分真实持仓与参考计划。 |
| S06 · 部分实现 · P0 | **基础指标、区间突破与箱体**（§5.2）<br>箱体、Wilder ATR、确认拐点与成交倍数在用。TA-Lib ATR/EMA20辅助函数存在但未接正式扫描；EMA50和独立N根高点突破模板缺失。 | 接入版本化基础特征；明确区间突破与箱体标签，不堆无关指标。 |
| S07 · 部分实现 · P0 | **历史案例、词条与可验证策略**（§6.1、§11、AC24）<br>9案例和16词条已展示；多数机制是说明文字，不是运行中的判定器。backtest目录为空，无负样本、对照、纸上收益或MFE/MAE报告。 | 机制逐项转规则并使用时间留出/失败样本/成本回放，单独评估加入OI的增量。 |

证据：

- S01：[engine/signal_service.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/signal_service.py:10)；[apps/web/src/components/phase-signals.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/phase-signals.tsx:11)。
- S02：[engine/strategies/structures.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/strategies/structures.py:89)；[engine/signal_service.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/signal_service.py:10)。
- S03：[engine/signal_service.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/signal_service.py:10)；[engine/repository.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/repository.py:11)；[engine/universe.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/universe.py:1)。
- S04：[engine/models.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/models.py:124)；[engine/signal_service.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/signal_service.py:10)。
- S05：[engine/models.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/models.py:124)；[engine/contracts.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/contracts.py:68)；[engine/signal_service.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/signal_service.py:10)。
- S06：[engine/strategies/structures.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/strategies/structures.py:89)；[engine/features/indicators.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/features/indicators.py:1)。
- S07：[apps/web/src/lib/radar-content.ts](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/lib/radar-content.ts:1)；[engine/backtest/__init__.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/backtest/__init__.py:1)。

### 图表与页面

| 编号 / 状态 | 需求与现状 | 下一步 |
|---|---|---|
| C01 · 部分实现 · P0 | **TradingView K线与结构图层**（§5.1、AC13）<br>真实OHLCV和箱体、门槛、参与区、保护位、日周压力、触发箭头已接。缺图层独立开关、点击标记详情、事件时间线、保存手工画图及三个定位入口；默认固定4H。 | 补事件周期初始化、交互和图层标注；按需求保存用户绘图命名空间。 |
| C02 · 未实现 · P0 | **OI / 费用 / 清算联动副图**（§5.1、AC14）<br>图下只有当前数字卡片，无OI/费用/清算历史序列面板，也无时间轴或光标联动。 | 提供带断线/单位/来源的历史序列和同步面板；成交量独立副图需显式验收。 |
| C03 · 部分实现 · P0 | **日线/周线目标与长趋势扩展**（§5.2、AC15）<br>分别读取本周期确认高点+ATR缓冲，但未做阻力簇归并、日周前置条件、距离%、passed/failed状态或箱高测算。None统一显示“历史压力不足”，混合样本不足和无上方历史压力。 | 分别输出历史不足/价格发现/已穿越状态；补目标生命周期和继续跟踪。 |
| C04 · 实现偏差 · P0 | **周K边界与历史长度**（§5.2、AC15）<br>初始最多300根，各周期不按730日上限回补。实测HL BTC周K从2026-09-24周四00:00Z开始，Binance为09-21周一；代码直接采用上游1w，未执行PRD周一聚合。 | 以日K/较细数据聚合UTC周一完整周，保留原生周边界说明；分页补历史至需求预算。 |
| C05 · 实现偏差 · P0 | **图表与行动持续一致**（§5.1、AC14）<br>图表与详情各自首次请求一次结构事件，未订阅signal_update刷新其保护/阶段/目标；停留页面后可能落后于列表。当前也无统一snapshot。 | 统一snapshot并监听事件版本更新，明确原计划/最新状态，切市场继续禁止混点位。 |
| C06 · 部分实现 · P0 | **一行总览、列设置和稳定排序**（§4.1–4.2、AC05、AC06）<br>浅色全宽、单币单行、组内序号已有；当前只固定代币列，没有行动/右图表固定、列显隐/宽度保存、用户排序/导出、稳定分页或新数据待重排机制。 | 以服务端list_snapshot和稳定排序补分页；保存列与排序偏好。 |
| C07 · 实现偏差 · P0 | **阶段/建议URL、筛选与展开偏好**（§2、§4.3、AC30）<br>/signals?view=recommended等参数未被读取，设置default_view也未使用；父组件固定初始stages。阶段折叠/隐藏空阶段不持久，搜索切页丢失；缺市场池/策略/质量等筛选。 | 将view/筛选与URL和偏好同步；折叠不改快照，安全状态即时更新。 |
| C08 · 部分实现 · P0 | **收藏、我的观察与独立研究页**（§2、§4.2）<br>收藏API与星标可用；/watchlist与/research实测404。无备注、静音、隐藏、同名CA区分和观察历史。词条页不能替代策略研究页。 | 补对应路由及持久对象，保持稳定资产身份。 |
| C09 · 未实现 · P0 | **历史页面与追踪接口**（§2、§9.3、AC23）<br>已有17 campaign/18 episode，但/v1/history硬编码rows=[]并写“策略尚未启用”，与实际结构扫描相矛盾。无campaigns/outcomes/回放任务API。 | 按真实事件提供历史、状态变更和成熟度；不能将已有事件说成从未启用。 |
| C10 · 部分实现 · P0 | **参数、草稿、模拟影响与回滚**（§8、AC18）<br>运行schema只有7个字段，对比PRD登记66行；只支持市场/采集及有限发布。结构阈值写死；无单独保存草稿按钮、预算模拟、策略/风险/CMC/TG参数及回滚。 | 按登记表补schema元数据、草稿/校验/模拟/发布/回滚；已有事件绑定旧配置版本。 |
| C11 · 实现偏差 · P0 | **默认容量与参数约束**（§1.2、§8.2）<br>代码默认150且限150–200、深采限80；§8登记默认200、范围50–500、深采可到200。§1.2首轮150–200本身又较窄，需区分首轮运行预算与可调上限。 | 保留用户已确认容量目标，统一文档参数层次与服务端实际预算校验，不能直接把可调上限都开满。 |
| C12 · 部分实现 · P0 | **数据源诊断页面**（§7.3、§12、AC26）<br>有连接状态、成功时间和错误详情；无逐字段provider/received/engine延迟、缺口回补进度、credit预算、来源探针按钮或排除合计。 | 增能力/覆盖/字段质量诊断；“连接正常”不等于全部产品字段可用。 |

证据：

- C01：[apps/web/src/components/chart.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/chart.tsx:12)。
- C02：[apps/web/src/components/asset-detail.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/asset-detail.tsx:1)；[apps/web/src/components/chart.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/chart.tsx:12)。
- C03：[engine/strategies/structures.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/strategies/structures.py:89)；[apps/web/src/components/phase-signals.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/phase-signals.tsx:11)。
- C04：[engine/adapters/hyperliquid.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/adapters/hyperliquid.py:1)；[engine/repository.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/repository.py:11)。
- C05：[apps/web/src/components/chart.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/chart.tsx:12)；[apps/web/src/components/asset-detail.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/asset-detail.tsx:1)。
- C06：[apps/web/src/components/signal-radar.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/signal-radar.tsx:1)；[apps/web/src/components/phase-signals.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/phase-signals.tsx:11)；[apps/web/src/app/radar.css](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/app/radar.css:1)。
- C07：[apps/web/src/components/signal-radar.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/signal-radar.tsx:1)；[apps/web/src/components/phase-signals.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/phase-signals.tsx:11)。
- C08：[services/api/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/api/main.py:97)；[apps/web/src/components/signal-radar.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/signal-radar.tsx:1)；[engine/models.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/models.py:124)。
- C09：[services/api/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/api/main.py:97)；[apps/web/src/components/system-pages.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/system-pages.tsx:11)。
- C10：[engine/contracts.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/contracts.py:68)；[apps/web/src/components/system-pages.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/system-pages.tsx:11)；[services/api/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/api/main.py:97)。
- C11：[engine/contracts.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/contracts.py:68)；[engine/universe.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/universe.py:1)。
- C12：[apps/web/src/components/system-pages.tsx](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/components/system-pages.tsx:11)；[engine/repository.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/repository.py:11)；[services/api/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/api/main.py:97)。

### 通知与工程

| 编号 / 状态 | 需求与现状 | 下一步 |
|---|---|---|
| N01 · 未实现 · P0 | **Telegram Bot 全流程**（§10、AC20–22）<br>通知接口固定enabled=false；notify worker只写not_enabled心跳。无Token引用/getMe/绑定码/订阅/命令/发送/429/TTL/未知结果处理；outbox为0。 | 按授权绑定具体收件目标后接文本提醒与审计；开发阶段不自动发送测试消息。 |
| N02 · 实现偏差 · P0 | **真实行情刷新与执行时效**（§8.2、§12.1）<br>所有基础/深度OI同为整轮完成再等60秒，未分60/300秒，无上游WS。报价和OI共用至少180秒阈值；本次报价年龄中位数约115秒，未达到参与价格15秒/盘口10秒要求。 | 分层调度与价格/盘口WS，逐用途TTL、权重限频和引擎延迟监控；不能用页面15秒刷新当源数据15秒。 |
| N03 · 部分实现 · P0 | **数据库任务、outbox和幂等恢复**（§9.1、AC23）<br>PostgreSQL、事务事件、SSE和心跳已有；Task/Outbox仅建表无调度/发送业务，记录为0。K线补数只取最近最多300根，未扫描中间缺口；原始与K线revision有局部支持。 | 落实租约任务、checkpoint、缺口补数、重启/更正回放及首报幂等端到端测试。 |
| N04 · 部分实现 · P0 | **API与冻结数据契约**（§9.3、AC14）<br>部分Pydantic/生成契约已用；信号返回普通dict，缺plan/snapshot/config身份、source_refs和统一quality。all/derivatives视图没有独立业务语义；若干PRD接口不存在。 | 完成统一Signal DTO、冻结读取与分页游标，生成契约和前端使用一致。 |
| N05 · 未完成 · P0 | **归档、备份恢复和运行验收**（§12.2、AC23、AC26）<br>原始响应持续写PostgreSQL；未见定时备份/恢复演练、30天压缩、事件证据长期保留、分区与容量告警的运行任务。P95/7天连续运行目标未验收。 | 先做备份恢复与容量基线，再按PRD保留/归档；单测通过不能代替运行或策略收益验收。 |
| N06 · 部分实现 · P0 | **服务器部署、权限与进程退出**（§9.1、§12.3）<br>Compose/启动脚本已准备，Linux实际构建部署及登录/session/用户隔离未验收。本机有Origin/Header/Host防护，不是服务器认证。stop-local仅停止记录父PID，曾遗留子进程。 | 建立单一启动入口、进程树退出、服务端认证和真实容器部署验收。 |
| N07 · 环境未同步 · 阻塞 | **C盘工作副本与D盘正式项目不同步**（实施交付）<br>18770前端和18773API运行C盘工作副本/隔离schema；18771仍是D盘旧API（4资产、0事件）。目录授权刷新失败尚未修复，源码同步包仍未应用。 | 完成已提供的目录所有者修复，再验hash/备份/切换；迁移真实首报和历史，避免直接切回空的public schema。 |

证据：

- N01：[services/worker/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/worker/main.py:8)；[services/api/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/api/main.py:97)；[engine/models.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/models.py:124)。
- N02：[services/worker/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/worker/main.py:8)；[engine/repository.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/repository.py:11)；[engine/adapters/base.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/adapters/base.py:1)。
- N03：[engine/models.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/models.py:124)；[engine/repository.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/repository.py:11)；[services/worker/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/worker/main.py:8)。
- N04：[engine/contracts.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/contracts.py:68)；[services/api/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/api/main.py:97)；[engine/signal_service.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/signal_service.py:10)。
- N05：[engine/repository.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/repository.py:11)；[services/worker/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/worker/main.py:8)。
- N06：[services/api/main.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/services/api/main.py:97)。
- N07：[docs/17_阶段信号与案例词条_实施说明.md](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/docs/17_阶段信号与案例词条_实施说明.md:1)。

### 后续范围

| 编号 / 状态 | 需求与现状 | 下一步 |
|---|---|---|
| P01 · P1未做 · P1 | **清算价格区、地址集中度、实验形态及TG图片**（§5.2、§6.5、§13）<br>公开地址清算区、未来清算分布、圆弧底/双底/三角形、Gate/Bitget广覆盖及TG图片均未上线；这部分本就不是全部P0承诺。无TradingView Pine/会员策略执行能力。 | P0数据与建议闭环验收后，按独立来源、成本与策略证据扩展。 |

证据：

- P01：[apps/web/src/lib/radar-content.ts](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/apps/web/src/lib/radar-content.ts:1)；[engine/strategies/structures.py](C:/Users/ls569/Documents/Codex/2026-09-22/https-ow8888-org-https-ow8888-org/work/market-expansion/engine/strategies/structures.py:89)。

## 30条验收对应

“局部通过”不是完整功能认证。本轮38个测试不等于30个AC全部通过。

| AC | 当前结论 | 对应项 | 范围 |
|---|---|---|---|
| AC01 | 未通过 | S01 | 建议动作发布与成本校验未完成 |
| AC02 | 部分实现 | S02,S04 | 1H/4H规则已有；独立首次触发/确认历史和全部策略未完成 |
| AC03 | 部分实现 | S04 | 首报冻结可复算；切源/完整升级恢复未端到端验收 |
| AC04 | 部分实现 | S03 | 新episode/旧失效保护已有；退榜、48小时结束和日线持续缺失 |
| AC05 | 部分实现 | D01,C06 | 一行字段位置已有；真实占比、主市场等核心数据未接 |
| AC06 | 未通过 | C06 | 无稳定快照分页与冻结排序 |
| AC07 | 未通过 | D09 | 未按可成交流动性选主市场 |
| AC08 | 局部通过 | D05 | 单市场数量/金额分离有测试；不外推聚合通过 |
| AC09 | 未通过 | D03,D14 | 跨场去重/规格/覆盖转换缺失 |
| AC10 | 部分实现 | D01 | 未知不填0/FDV已有；可靠分母未接 |
| AC11 | 部分实现 | D10 | 原生结算已接，累计费用和周期变化完整性未验收 |
| AC12 | 未通过 | D13 | 清算未接；没有伪称爆空 |
| AC13 | 部分实现 | C01 | 真K线和箱体箭头可见，独立图层/成交量布局验收不完整 |
| AC14 | 未通过 | S04,C05 | 未形成列表/详情/图层/TG同快照 |
| AC15 | 部分实现 | C03,C04 | 日周独立计算已有，周边界/历史/目标状态不完整 |
| AC16 | 部分实现 | S05 | 结构保护撤销已有，完整交易风险/成本/回放缺失 |
| AC17 | 部分实现 | D15 | 有adapter底座，注册启用和业务去硬编码未完成 |
| AC18 | 部分实现 | C10,C11 | 参数发布局部可用，预算模拟和回滚缺失 |
| AC19 | 未通过 | D02 | 只有历史Key样本测试，无采集预算系统 |
| AC20 | 未通过 | N01 | 无绑定及身份验证业务 |
| AC21 | 未通过 | N01 | 无发送/重试/TTL/未知结果处理 |
| AC22 | 未通过 | N01 | 无实际消息模板及链接生成路径 |
| AC23 | 部分实现 | N03,N05 | 有持久行情和首报，无完整恢复/任务/通知演练 |
| AC24 | 未通过 | S07 | 回放引擎与负样本验证未实现 |
| AC25 | 部分实现 | C09,N07 | 研究标识与未知值存在；历史接口状态和交付范围表述有偏差 |
| AC26 | 部分实现 | C12,N02 | 源隔离与部分质量已有；时效/覆盖/预算诊断缺失 |
| AC27 | 部分实现 | S01,C01,C06 | 点位和真图可核对，正式建议与完整管理未完成 |
| AC28 | 部分实现 | D04 | 1D=24H语义正确，当前历史不足，跨集合比较未实现 |
| AC29 | 未通过 | S01 | 分列表和明确空态已补，缺有效建议全流程 |
| AC30 | 部分实现 | C07,S03 | 一币一组与计数已有，持久折叠/稳定分页/跨策略与TG未完成 |

## 37项OI讨论映射

编号沿用《09_需求讨论覆盖清单_v1.0.md》，讨论要求仍然保留。

| 编号 | 当前状态 | 核查项 |
|---|---|---|
| OI-01 | 部分实现 | D04,D05 |
| OI-02 | 部分实现 | D14 |
| OI-03 | 未实现 | D03,D08 |
| OI-04 | 未实现 | D03 |
| OI-05 | 部分实现 | D03,C12 |
| OI-06 | 未接入 | D01 |
| OI-07 | 未实现 | D11 |
| OI-08 | 未实现 | D01,D11 |
| OI-09 | 未接入 | D12 |
| OI-10 | 部分实现 | D10 |
| OI-11 | 未实现 | D10 |
| OI-12 | 未实现 | D10,D11 |
| OI-13 | 未接入 | D12 |
| OI-14 | 未接入 | D12 |
| OI-15 | 未接入 | D13 |
| OI-16 | 表述已落实/证据未接 | D05,D13 |
| OI-17 | 未接入 | D12 |
| OI-18 | 未接入 | P01 |
| OI-19 | P1未做 | P01 |
| OI-20 | P1未做 | P01 |
| OI-21 | P1未做 | P01 |
| OI-22 | 部分实现 | S02,D11 |
| OI-23 | 未实现 | D12,D13,D11 |
| OI-24 | 部分实现 | S03,C03 |
| OI-25 | 部分实现 | S02,C03 |
| OI-26 | 已实现局部 | C01,S02 |
| OI-27 | 已实现局部 | S03 |
| OI-28 | 部分实现 | D05,D13 |
| OI-29 | 部分实现 | C03 |
| OI-30 | 复盘已保留/未策略化 | S07 |
| OI-31 | 研究资料保留/未回放 | S07 |
| OI-32 | 未实现 | S07 |
| OI-33 | 样本已验证/未采集 | D02 |
| OI-34 | 历史研究保留/生产覆盖有限 | D08 |
| OI-35 | 部分实现 | C01,C02,C05 |
| OI-36 | 部分实现 | C06,S01 |
| OI-37 | 部分实现 | D06,D07,D02 |

## 文档状态需要同步

1. §0“当前目录只有文档”及§13“当前授权是完成方案”是历史基线状态，已被后续工程实施和用户授权改变；不能继续作为当前运行状态。产品功能目标仍保留。
2. §1.2首轮Binance 150–200与§8.2默认200、可调50–500需明确“初期运行预算”和“参数能力上限”；当前代码150–200只是受限首批实现。
3. 《09_需求讨论覆盖清单》NEW15仍写27条AC，但PRD与任务表已扩展至30条，应统一记录。
4. 《15_M0工程搭建验收》证明的是M0/小池路径，不是v1.0全部完成；后续应逐个AC保存证据，避免页面存在被当作功能已上线。

## 建议实施顺序

1. **统一正式运行目录：** 修复权限后备份、同步和切换，保留真实首报与历史；现阶段按M0加部分M1/M2/M3交付记录。
2. **补合约数据：** 资产规格、USD换算、流通分母、CMC预算采集、DEX候选扩容、跨场OI、数量历史及费用累计。验收每个数字可追溯。
3. **完成真实建议闭环：** 价格/盘口时效、深度/滑点/费用、净R、冻结计划与快照；先早期/确认，再回踩/再启动。
4. **完成第二市场池及保护监控：** Spot/Alpha、Wallet Rising和明确池子行情；活动事件退榜仍跟踪。
5. **页面与图表：** 占比/份额/异常费用、目标状态、OI副图联动、URL/排序/折叠、设置/历史，同屏同快照。
6. **TG和运行验收：** 明确授权绑定收件目标后实现文本提醒，补成本回放/失败样本/前向跟踪/备份恢复/服务器部署。

## 本轮改动和验证

- 工作副本建议页新增“建议发布功能尚未完成”、三个待接入校验、逐币未入选原因；仍保留执行门槛。
- 浏览器实测FLOCK现价低于原参与区，FIGHT/NOM超区且原窗口到期。名单随行情变化。
- 38项后端测试与TypeScript检查通过；浏览器验证诊断卡片与列表。
- /watchlist、/research实测404；历史接口固定空，通知接口disabled。
- evidence.json保存脱敏API/数据库快照及源码SHA256；本报告没有把单元测试替代完整功能或收益验收。
- D盘源码仍未同步；当前改动只对C盘工作副本生效。
