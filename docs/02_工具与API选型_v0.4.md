# 接口工具与同类产品调研

版本 v0.4 选型冻结稿　编制日期：2026年9月23日

## 结论摘要

1. **Perp DEX 数据可以做成首版能力。** Hyperliquid 的公开 Info API 已实测返回市场目录、OI、资金费率、标记价、24h成交额、K线和 L2 盘口；Aster 的公开 Futures API 已实测返回 K 线、OI、资金费率、盘口和清算订单 WebSocket 文档。
2. **Hyperliquid 与 Aster 不能使用同一个“钱包仓位模型”。** Hyperliquid 可按已知地址查询仓位，但完整全市场地址集合不是一个免费接口直接给出的；Aster 官方定位是加密订单和仓位隐私，公开市场数据与授权账户数据必须分开。
3. **Surf 具备正式接入面。** 官方文档现在提供 Data API、Chat API、Hosted MCP 和 Skill；覆盖 Hyperliquid 地址级仓位、持币、地址标签、交易所市场和 DEX 数据。Surf 是付费/额度型供应商，不能把 MCP 当成免费的高频行情采集器。
4. **链上控盘只能作为证据链，不应作为事实标签。** Top10 地址、负资金费率或 OI 上升都可能有托管、做市、套保和拆分地址等解释。产品应输出“集中度证据”“空头拥挤证据”“流动性证据”和“反向证据”，不输出未经验证的“庄家已控盘”或“几十倍概率”。
5. **建议组合：** 交易所原生 API 做实时行情和衍生品基础字段；Hyperliquid/Aster 原生 API 做 Perp DEX；Surf/Birdeye/Nansen/Bubblemaps 做标签、持币、实体和研究补充；CoinGlass/Coinalyze 作为聚合和历史对照，必须标注授权、套餐、刷新和口径。

## 0. 免费优先结论

可以把第一版的**实时基础雷达**做到几乎全免费，但不能把“全市场地址级仓位、完整历史清算和实体控制权识别”承诺为免费能力。建议按以下边界实施：

| 目标 | 免费优先实现方式 | 能否满足 |
|---|---|---|
| CEX K线、价格、基础 OI/funding | Binance、OKX、Gate、Bitget 原生公共 REST/WS | 可以，需自建限频和历史存储 |
| Hyperliquid 市场总 OI/funding/K线/盘口 | 官方 `api.hyperliquid.xyz/info` 与 WS | 可以，不需要交易账户权限 |
| Aster 市场总 OI/funding/K线/盘口/清算事件 | 官方 `fapi.asterdex.com` 与 `fstream.asterdex.com` WS | 可以，先使用公开 V3 市场数据 |
| CMC 价格、身份、DEX/持币补充 | CMC Keyless Public API；文档列出 holders、DEX、OHLCV 等端点 | 可以做低频补充；共享配额和链覆盖需逐项测试 |
| 衍生品历史 OI/funding/liquidation | 自己持续保存原生快照；Coinalyze 免费 API 做对照 | 当前数据可以；历史长度取决于开始采集时间，第三方覆盖需核验 |
| Hyperliquid 已知地址仓位 | 原生 `clearinghouseState`，只监控自建地址观察池 | 可以；全市场地址发现不保证 |
| Aster 任意地址仓位 | 不能靠公开市场 API；仅做授权账户或第三方明确覆盖的数据 | 不能承诺全免费全市场 |
| “同一庄家控制多个地址” | 免费 RPC/事件索引 + 自建聚类，只能得到启发式置信度 | 可做研究版，不能做事实标签 |

**推荐的免费首版：** 每30–60秒轮询/订阅市场总 OI、funding、K线和盘口；每5–15分钟保存 CMC/链上持币快照；Hyperliquid 只监控人工确认的公开地址集合；Aster 先不做公开钱包仓位分布。这样能先验证 S1/S2 逼空候选逻辑，不会把付费地址数据依赖放进核心链路。

**免费不等于没有成本：** 需要自建数据库、WebSocket重连、历史存储、链上 RPC、数据校验和限流。真正的硬成本会从供应商账单转移到服务器、维护和数据质量工作。

## 0.1 本轮冻结的工具与 API 组合

| 层 | 首选 | 备用/扩展 | 首版使用边界 |
|---|---|---|---|
| Binance 生态 | Binance Spot/Futures/Alpha 原生 REST + WebSocket | OKX 复核；Gate、Bitget 扩展 | Spot/Futures 负责二级市场和衍生品；Alpha 单独负责早期 token 发现和市场数据，不推导 OI |
| Perp DEX | Hyperliquid Info API + WebSocket；Aster Futures API V3 + WebSocket | Surf 地址级补充 | 市场级数据走原生 API；地址级数据必须标注观察池范围 |
| 链上身份/池子 | DexScreener、CoinGecko、CMC | Surf、GoPlus | 低频补充；不替代交易所实时行情 |
| 地址标签/仓位研究 | Surf Data API（按需） | Birdeye、Nansen、Bubblemaps、CoinGlass/Coinalyze | 需要 key/额度时才启用，保存来源、时间、覆盖和 credits |
| 图表 | TradingView Lightweight Charts | 其他 Apache/MIT 图表库 | Apache 2.0；Advanced Charts 副本不进入核心依赖 |
| 标准指标 | TA-Lib | 本地纯 Python/TypeScript 复算 | 固定参数、预热长度和容差；形态规则自研并版本化 |
| 统一适配 | 自建 source adapter | CCXT（P1 长尾现货） | 不隐藏衍生品特有字段；核心层不依赖 CCXT |

**最终结论：** 第一阶段只实现 Binance + Hyperliquid + Aster，OKX 接入跨场复核；Gate/Bitget、CMC/CoinGecko/DexScreener、Surf 按增量接入。这个顺序兼顾公开免费数据、Perp DEX 重点和可控工程量。
**Aster V3 需要先做运行时探针：** 官方 V3 文档明确给出基础地址 `https://fapi.asterdex.com`、WebSocket `wss://fstream.asterdex.com`，但文档同时提示 `GET /fapi/v3/klines` 在当前服务器未完成验证，实际服务器可能暴露 `GET /fapi/v3/marketKlines`。因此 Aster 适配器上线前必须用公开请求验证 `exchangeInfo`、K 线、OI、funding 和 depth 的实际路径，把最终路径写入 source adapter 配置；不能因为文档出现某个路径就直接硬编码。

## 1. 调研口径

本文把结论分为：

- **现场实测：** 本轮用公开 HTTP 请求获得 200 和 JSON 响应；不代表长期稳定、生产限额或商业再分发授权。
- **官方文档：** 文档明确写出的接口、字段、权限或产品声明。
- **待认证/套餐核验：** 需要 API key、账户、商业合同、覆盖测试或历史样本才能确定。

GitHub 的源码公开、许可证公开和服务可商用是三个不同问题。SDK 能帮助接入，不等于有完整的行情历史、身份映射或可直接部署的雷达产品。

## 2. 原生数据源矩阵

| 来源 | 适合字段 | 现场状态 | 接入建议 | 主要限制 |
|---|---|---|---|---|
| Binance Spot/Futures | K线、成交、OI、资金费率、账户/大户多空、Taker | 前序已实测；网页样本 K线与现货接口逐根匹配 | 首发基准源；现货和永续分开建模 | 接口限频、数据口径多；大户账户比不是全市场仓位 |
| OKX | K线、SWAP OI、合约规格、标记价/资金费率 | 本轮前已实测 200/code0；K线倒序，有 `confirm` | 做跨场复核和合约规格校验 | 需要处理 `oi` 张数、`oiCcy` 和 `oiUsd` 的单位 |
| Gate | Spot K线、合约规格、持仓/费率等 | 本轮前已实测 200 | P1 接入；覆盖长尾标的 | 官方文档页出现 403，采用官方 SDK/接口测试补充 |
| Bitget | Spot K线、Mix OI | 本轮前已实测 200/code00000 | P1 接入，评估 UTA 迁移 | 官方正在从 Classic 向 UTA 过渡，需持续核验版本 |
| Hyperliquid | Perp 目录、OI、funding、mark/oracle、24h成交、K线、L2 | `POST https://api.hyperliquid.xyz/info` 多端点已实测 200 | P0 Perp DEX 源；WS 用于重点市场 | 原生 dex 与 HIP-3 namespace 必须分开；L2 文档只承诺最多20档 |
| Aster | Futures V3 K线、OI、mark/index、funding、depth、forceOrder WS | `fapi.asterdex.com` 公共端点已实测 200 | P0 Perp DEX 源；V3 优先 | Aster 账户/仓位隐私；公共 OI 不是地址级仓位分布 |
| CMC Keyless | 价格、market cap、OHLCV、DEX pairs、token security、holder/indices目录 | `simple/price` 无 key 已实测 200 | 身份、代表价格和市值补充；原型方便 | 无 key 是共享配额；完整目录和较高限额仍需 key/套餐核验 |

### 2.1 Hyperliquid 实测与字段

本轮使用以下只读请求：

```text
POST https://api.hyperliquid.xyz/info
{"type":"metaAndAssetCtxs","dex":""}
{"type":"perpDexs"}
{"type":"candleSnapshot","req":{"coin":"BTC","interval":"1h",...}}
{"type":"l2Book","coin":"BTC"}
```

代表性响应：市场目录 234 个；BTC `openInterest`、`funding`、`markPx`、`oraclePx`、`dayNtlVlm` 等字段均返回；K线包含开高低收、成交量和成交笔数；L2 两边各返回20档。官方文档的 `clearinghouseState` 需要地址，返回该地址仓位、保证金和可见清算价。`perpDexs` 表明存在多个 perp dex，缺省 `dex:""` 只代表原生/第一市场。

适配器必须保存：`dex_namespace`、`coin`、`open_interest_unit`、`mark_px`、`oracle_px`、`funding`、`day_ntl_vlm`、`book_depth_levels`。不要将 `openInterest` 直接当 USD；需乘以合约市场定义或使用名义量字段并保存换算方法。

### 2.2 Aster 实测与字段

官方 API 仓库文档显示当前新接入使用 **V3 (Recommended)**，公共 Futures base endpoint 为 `https://fapi.asterdex.com`，WebSocket market streams 为 `wss://fstream.asterdex.com`。本轮实测：

```text
GET /fapi/v3/klines?symbol=BTCUSDT&interval=1h&limit=2
GET /fapi/v3/premiumIndex?symbol=BTCUSDT
GET /fapi/v3/depth?symbol=BTCUSDT&limit=5
GET /fapi/v3/openInterest?symbol=BTCUSDT
GET /fapi/v3/exchangeInfo
```

返回了 `openInterest`、`markPrice`、`indexPrice`、`lastFundingRate`、`nextFundingTime`、盘口档位和合约规格。文档还列出 `@forceOrder` 与 `!forceOrder@arr` 清算订单快照流；每个市场约每1000ms推送最新一笔快照，不能解读为完整清算流水。

Aster 文档明确强调订单、仓位大小、入场价和清算位默认私密。API key 可配置读/写权限；新项目应使用 V3 并将私有账户接口与公共行情隔离。不要因为存在 `positionRisk` 或 builder 相关接口，就宣称能够枚举任意用户的全市场仓位。

## 3. 数据商与研究接口

| 工具 | 能力 | 适合本项目的用途 | 认证/成本与风险 |
|---|---|---|---|
| Surf Data API | 120+ REST 命令；Exchange、Market、DEX、Hyperliquid、Token、Wallet、Onchain SQL | Hyperliquid 地址仓位/账户/成交；Token holders 与标签；多源行情和 DEX OHLCV | 官方文档要求 `Bearer API_KEY`；按 credits 计费，文档称每个 IP 每日可试用30 credits。匿名样本请求曾返回200，但生产仍按需要 key 设计 |
| Surf MCP/Skill | Hosted `https://mcp.asksurf.ai/mcp`、本地 package、100+ crypto commands | AI 按需解释、研究和交互式取数 | Hosted MCP 是只读 `data:read` 授权；MCP 不适合作为全市场秒级采集器。需保留原始 REST 数据快照 |
| Birdeye Data | Token/holder/profile/distribution、wallet flows、Perps、OHLCV、WebSocket、Agents | Solana/EVM 持币分布、标签、Perps 补充；候选筛选 | 覆盖、套餐和具体 endpoint 权限要做账户测试；旧链接已迁移，不能沿用旧文档路径 |
| Nansen API | 地址标签、Smart Money、Token God Mode、Portfolio、Hyperliquid、MCP | 实体/智能资金标签、地址聚类补充、历史研究 | 商业数据服务；需认证、套餐、覆盖和再分发核验 |
| Bubblemaps B2B API | Holders、transfers、clusters；另有 iFrame | 给集中度和地址关系提供可解释的聚类视图 | API/iframe 是不同产品；商业价格与链覆盖需洽谈，不应把聚类当法律意义上的同一控制人 |
| CoinGlass | OI、funding、清算、CVD、盘口、Hyperliquid whale/wallet/distribution | 聚合对照、清算/历史研究、验证自采数据 | 需认证和套餐；清算热力图是模型/预测上下文，不等于全市场待清算仓位 |
| Coinalyze | OI、funding、liquidation、long/short、OHLCV 历史 | 较低成本的衍生品历史基线 | API key；文档写40 calls/min，分钟级历史约保留1500–2000点；公开展示需引用来源 |
| CoinGecko/GeckoTerminal | REST、WS、Webhooks、链上 DEX 数据 | token identity、池子、市场和价格补充 | Demo/付费权限不同；不要把覆盖当作无限制免费 |
| DexScreener | Pair/price/liquidity、profiles、WebSocket 页面能力 | 链上池子发现、价格和流动性初筛 | 公共 API 有端点限额；前端有图不证明所有 OHLCV 都有公共 endpoint |
| GoPlus | Token security、合约权限、风险标签 | 过滤蜜罐、税费、可升级/黑名单等风险 | 链覆盖和字段缺失需要 fail-open/unknown；安全标签不等于控盘判定 |

### 3.1 Surf 在本项目中的正确位置

Surf 官方开发者文档列出了 `https://api.asksurf.ai/gateway`、Data API、Chat API、MCP 和 Skill。Hyperliquid Positions endpoint 需要地址，并返回仓位、账户价值、杠杆、未实现 PnL、清算价/距离等；Token holders endpoint 需要链和完整合约地址，并能返回实体名与标签。它可以减少自建索引成本，但地址级数据仍然是“给定地址集合”的观察，不解决全市场地址发现。

建议：实时行情由原生 WebSocket/REST 采集；Surf 每5–30分钟补充标签、地址仓位和研究信息；AI Chat 只在用户点开详情或事件升级时调用。每条 Surf 记录保存 `credits_used`、查询时间、缓存标记和供应商原始 ID。

## 4. 成熟开源组件与许可证

| 项目 | 许可证/证据 | 可借鉴内容 | 使用提醒 |
|---|---|---|---|
| CCXT | MIT，已读取官方 LICENSE | CEX REST/WebSocket 适配、统一 symbol | 统一接口会隐藏交易所差异；OI、合约乘数和限频仍要读原生字段 |
| cryptofeed | AGPL-3.0，含额外署名条款 | 多交易所 WebSocket、order book、trade feed | 若作为网络服务修改/提供，先评估 AGPL 义务和部署边界 |
| Freqtrade | GPL-3.0 | 策略、回测、交易所适配参考 | 本项目只读雷达不应直接继承其执行语义；GPL 合规需单独评估 |
| vectorbt | Apache 2.0 + Commons Clause | 向量化研究、回测和指标实验 | Commons Clause 限制把软件功能直接作为收费服务；商用前做法律核验 |
| TradingView lightweight-charts | Apache 2.0，含 TradingView attribution/NOTICE 要求 | 前端 K 线、标记、指标面板 | 保留 attribution 和 NOTICE；它是图表库，不是行情源 |
| Hyperliquid Python SDK | MIT，官方仓库 README/LICENSE 可读 | Info API、交易签名、类型化调用参考 | 本项目只读部分可借鉴；不要把交易签名依赖带入公开采集服务 |
| Aster connector-python | MIT，官方组织仓库有 LICENSE | Aster API 调用和签名参考 | 优先 V3；公共采集不要配置写权限 |
| Aster MCP | 官方 GitHub 可读，当前抓取未确认 root LICENSE | MCP 工具封装、HMAC/V3 EIP-712 适配参考 | 在确认许可证前，不要复制其实现到商业闭源核心 |

另外，`nhovongoc0-max/meme-radar` 已核验 AGPL-3.0-only，采用本地 GMGN、多链和 GoPlus/DexScreener 复核；`2458283786-blip/yaobi-radar` 无 LICENSE，不能当可授权开源底座。两者可借鉴筛选流程和字段编排，不能认定为杰尼君原站源码或官方实现。

## 5. 同类产品借鉴

| 产品/项目 | 可借鉴点 | 不应直接照搬 |
|---|---|---|
| 杰尼君 Meme Radar | 分级信号、首次告警价、OI/成交量联动、风险标签、K线详情 | 原站评分公式、后台代码、视觉素材未公开；已抽检来源口径不等于完整实现证据 |
| CoinGlass | OI/funding/liquidation 多市场总览和历史上下文 | 热力图/估算清算区间必须标注模型性质；套餐与再分发限制需核验 |
| Hyperliquid 生态看板 | 地址级仓位、排行榜、资金和清算观察 | 地址集合、标签质量和全市场覆盖不能默认完整 |
| Bubblemaps | 将持币和地址关系可视化，降低“Top10=庄家”误读 | 聚类是分析置信度，不是控制权证明；API和iframe商业条件不同 |
| Birdeye / Nansen | 钱包、标签、智能资金和链上筛选 | 标签新鲜度、链覆盖和供应商算法要进入数据质量面板 |
| Freqtrade/vectorbt | 回测、规则版本和样本实验 | 不把回测收益直接变成实时信号承诺；应保留交易成本和幸存者偏差 |

## 6. 推荐的数据分层

### L0 原生实时层

Binance、OKX、Hyperliquid、Aster 的 REST/WebSocket：价格、已闭合 K线、成交、OI、funding、mark/index、盘口和实际清算流。写入原始事件，保存 event time 与 received time。

### L1 归一与质量层

统一 Asset/Instrument/Pool 身份、合约乘数、张数/币数/USD 单位、周期、闭合标记、市场 namespace。对断线、顺序、重复、跨源冲突和来源延迟做状态标记。

### L2 链上结构层

Surf/Birdeye/Nansen/Bubblemaps/GoPlus：持币分布、实体标签、转账关系、流通情景、合约安全和链上池流动性。原始集中度、调整后集中度和实体聚类集中度分开保存。

### L3 研究和解释层

Surf Chat/MCP、CMC Agent Hub 或自建规则解释：只引用已经保存的结构化证据，输出事实、推断、反向证据和数据缺失。服务失败时 L0–L2 仍可运行。

## 7. “妖币/逼空”信号的可验证版本

### S1 拉升前结构

- 供应：实体/钱包集中、已识别托管容器、锁仓/解锁和可流通量情景。
- 衍生品：`OI_USD / 流通估值`、`OI_USD / 可执行卖盘深度`、资金费率/贴水、OI 变化分位数。
- 流动性：指定名义买卖冲击、盘口价带深度、现货池深度、跨场价差。
- 限制：集中度没有共同控制证据，或地址/供应覆盖低时，只输出“结构候选”。

### S2 启动确认

- 现货真实成交与主动买压支持，不能只有合约报价跳动。
- 价格、OI 数量、成交方向和跨场市场至少两类独立来源同步。
- 已观测空头清算或公开地址空头减少可加分；没有这些数据不能写“全市场空头爆仓”。

### S3 持续与退潮

- 跟踪新增仓、回补、资金费率、价差、盘口恢复/撤单、交易所流入和已标记实体变化。
- `价格升 + OI降` 可能是空头回补，也可能是混合平仓；`价格升 + OI升` 可能新多、接力空或套保。两种情况都需要成交与仓位证据。
- 退潮是风险状态，不是一个价格阈值：现货承接消失、集中实体转入交易所、冲击成本恶化、来源冲突或清算/价差异常都应单列。

产品不提供针对某个账户或市场进行操纵、逼仓或交易执行的建议。它只研究公开数据中可能出现的拥挤和流动性反馈，并保留反例。

## 8. 首版接入顺序

1. **第一个迭代：** Binance Spot + Binance Futures + Binance Alpha + Hyperliquid + Aster；完成三类 Binance 市场的独立资产目录，以及 K线/OI/funding/mark/盘口和数据质量。
2. **第二个迭代：** OKX 跨场复核，Surf 或 Birdeye 任选一个完成持币/标签/链上流动性补充；实现 S1/S2 规则标签。
3. **第三个迭代：** 公开地址观察池、Nansen/Bubblemaps 之一、Coinalyze/CoinGlass 历史对照；保存足够快照后再做 S3 回放。
4. **第四个迭代：** Gate/Bitget 扩大覆盖，CMC/CoinGecko 做身份和市值补充，Surf Chat/MCP 做按需解释。

## 8.1 技术指标与现成形态工具：增补讨论

技术扫描与妖币/逼空筛选并列。标准指标优先采用成熟库；复杂形态先对比现成工具，未决定从零自研。以下核查为2026年9月22日官方功能页、仓库README和许可证读取，未安装或完成实盘准确率评测。

| 工具 | 已核对能力 | 费用/许可与适用边界 |
|---|---|---|
| [TA-Lib](https://ta-lib.org/functions/) / [Python wrapper](https://github.com/TA-Lib/ta-lib-python) | EMA、MACD、RSI、ATR、布林带和蜡烛形态 | 免费；核心BSD三条款、wrapper BSD两条款。没有现成圆弧底确认函数，适合标准指标基础层 |
| [TradingView](https://www.tradingview.com/features/) | 指标、筛选、Auto chart patterns | 现成产品；高级功能受套餐限制。不等同Lightweight Charts，也未确认有免费形态结果API可接入自己的雷达；圆弧底具体支持尚未确认 |
| [TrendSpider](https://trendspider.com/product/) | 技术扫描、形态识别；官网列出Crypto行情 | 订阅产品，可人工比较；目标代币/Perp DEX、圆弧底及外部API授权仍需核验 |
| [TradingPatternScanner](https://github.com/white07S/TradingPatternScanner) | README列头肩、多重顶底、双底、三角形、楔形、通道等 | LICENSE.md为CC BY-NC-SA 4.0，限制商业用途。README的最高84.5%是头肩形态的合成数据实验结果，不是圆弧底准确率或实盘胜率 |
| [chart_patterns](https://github.com/zeta-zetra/chart_patterns) | README列双顶底、旗形、头肩、三角形、尖旗 | 公开代码；本次未确认明确许可证，不能直接批准商业复用。功能列表未列圆弧底，无实盘质量结论 |

评估分三层：指标计算一致性（固定数据/参数/预热与容差）；图形识别质量（人工标注样本的误报、漏报和发现延迟）；信号后价格表现（含失败案例、时间切分及成本）。识别正确不等于后面上涨，库的截图或合成数据成绩不足以证明可用于实盘告警。

免费优先的建议路线：免费原生K线 + TA-Lib；N日高点突破采用可复算的简单规则；复杂形态用候选识别器与人工样本对照，覆盖/许可/质量缺口再决定是否定制。先保留“圆弧底候选”和“颈线突破确认”两种状态，不输出未经校准的准确率。

## 9. 仍需用户参与的产品决定

下一轮最值得先确定的是：选出3至5个真实的“高控盘后连续逼空”历史案例，提供代币和大概日期/市场。案例决定我们究竟优先优化 S1 拉升前、S2 启动，还是 S3 中段跟踪，也能用来区分真正想捕捉的结构和普通牛市上涨。

还需确认：首批关注链与标的数、个人使用还是公开服务、月度数据预算、是否接受第三方标签、通知渠道和历史保存期限。

## 10. 主要来源

- [Hyperliquid Info API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
- [Hyperliquid Perpetuals API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals)
- [Aster API Docs](https://docs.asterdex.com/for-developers/aster-api/api-documentation)
- [Aster 官方 API 仓库](https://github.com/asterdex/api-docs)
- [Surf Data API](https://docs.asksurf.ai/data-api/overview)
- [Surf Hyperliquid Positions](https://docs.asksurf.ai/data-api/hyperliquid/positions)
- [Surf MCP](https://docs.asksurf.ai/cli/mcp)
- [Birdeye Data API](https://data.birdeye.so/docs/)
- [Nansen API](https://docs.nansen.ai/)
- [Bubblemaps B2B API](https://docs.bubblemaps.io/)
- [CoinGlass API](https://docs.coinglass.com/reference/getting-started)
- [Coinalyze API](https://api.coinalyze.net/v1/doc/)
- [CoinMarketCap Keyless API](https://coinmarketcap.com/api/documentation/pro-api-reference/keyless-public-api)
- [CCXT](https://github.com/ccxt/ccxt)
- [cryptofeed](https://github.com/bmoscon/cryptofeed)
- [Freqtrade](https://github.com/freqtrade/freqtrade)
- [vectorbt](https://github.com/polakowo/vectorbt)
- [TradingView lightweight-charts](https://github.com/tradingview/lightweight-charts)
- [Hyperliquid Python SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk)


## 11. 官方文档快照与更新方式

本项目已将 21 份官方页面、官方 GitHub 文档或官方 SDK 参考保存到 `docs/api/official/`。每份文件的原始 URL、实际抓取 URL、最终 URL、HTTP 状态、抓取时间、文件大小和 SHA-256 位于 `docs/api/manifest.json`；纯文件校验值位于 `docs/api/official/SHA256SUMS.txt`。

刷新命令：

```powershell
python D:\Axiangmu\erji	ools\download_official_docs.py
```

Binance 和 Gate 的开发者页面在本次抓取环境中分别出现空响应或 403，因此清单同时保存了官方 GitHub 文档/SDK 版本，并在 `notes` 字段标明替代关系。OKX、Hyperliquid、Aster 等动态页面保存的是 HTML 快照；页面快照不替代运行时 API 响应，也不代表页面后续 JavaScript 加载内容全部包含在本地文件中。

## 12. 选型风险和禁止误用

- 不把任何 GitHub 上的 TradingView Advanced Charts/Charting Library 副本称为 TradingView 开源版，也不在未核实授权前把它打包进产品。
- 不用 CMC、CoinGecko、DexScreener 或 Surf 的低频/聚合字段替代交易所原生 OI、funding、清算和盘口。
- 不把 Hyperliquid 已知地址仓位外推成全市场仓位，不把 Aster 私有账户接口外推成任意地址扫描。
- 不把 `price up + OI down` 单独解释为“空头爆仓”，不把集中度单独解释为“庄家控盘”。所有推断都显示反向证据和数据缺失。
- 不在尚未保存足够历史样本前承诺形态准确率、信号胜率或逼空倍数。


