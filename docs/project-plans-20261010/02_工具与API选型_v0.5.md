# 工具与 API 选型

版本 v0.5 · 2026-09-23 · 替代v0.4当前选型结论；旧调研保留为历史证据。



资产范围沿用两池：Binance Spot/Futures/Alpha；Binance Wallet Rising 发现的链上 Meme/FOMO（优先流通市值 ≥ $1M，供应量未知标待核，不能用 FDV 通过门槛）。Rising 负责发现，K 线来自明确池或市场。无需再同时堆叠 GMGN、DexScreener、GeckoTerminal、Surf 为发现入口。

Binance Spot/Futures 正式扫描建议 150–200 个资产（含仅现货），Alpha 最多 100，Rising 50–100；去重前约 300–400 个名额，实际并集取决于身份映射。深度跟踪共用最多 80 资产预算。目录全量不等于全量高频扫描，免费限频压测后调整。

| 数据职责 | 首选 | 当前证据与缺口 |
|---|---|---|
| K 线、成交、当前 OI、资金费 | Binance、Aster、Hyperliquid 原生公共 API | 前序已实测公共字段；按端点独立记录新鲜度，不由页面演示推断已接入 |
| 更广合约目录与汇总核对 | CMC 免费 Key 衍生品 latest 接口 | 官方 Free 可用；本次无 Key 返回 403/1005。尚未用 Key 验证具体 Aster/Hyperliquid 币种、非空 OI 与延迟 |
| 跨场复核 | OKX；Gate、Bitget 按需扩展 | 不要求首发一次接齐所有平台 |
| 长期 OI 历史 | 原生历史与公开归档 + 自行保存 | Binance REST 常用历史窗口较短，公开 metrics archive 已用于复盘；Aster 本次拿到 8 币历史 K 线与费率，不等于拿到同期历史 OI |
| 清算 | 原生公共推送，按源说明采样 | Binance/Aster 快照不等于全量成交；没有历史 tape 不补造清算金额 |
| 清算价分布 | Hyperliquid 已知地址集合（P1） | 不能枚举全市场地址；Aster 私有仓位不当公共接口。第三方估算热力图不是免费首发依赖 |
| 流通量／身份／持币标签 | CMC 等按缺口补充；独立链上证据 | 不用当前供应倒填历史，不自动将 Top10 视为同一控制实体 |

Hyperliquid 是平台，HYPE 是代币；Aster 是平台，ASTER 是代币。记录 `dex_namespace`、原生 instrument、挂牌与下架时间。同名 ticker、1000 倍面值、封装币、桥接币均须核对，映射不明不相加。某平台当前查不到，不代表历史从未上线。

### CMC 选型修正

官方 2026-06-04 更新包含：

- `/v5/cryptocurrency/derivatives/market-pairs/list/latest`：按代币查询各所衍生品交易对，包含 OI、funding、index、basis；`center_type=all|cex|dex`。
- `/v5/exchange/derivatives/market-pairs/list/latest`：按交易所批量查询，按身份映射在本地归并。
- `/v5/exchange/derivatives/list`：交易所整体数据，不能当某个币的全市场 OI。

文档注明 Free、60 秒缓存、每 250 个 market 计 1 credit（向上取整）；Basic 免费预算 15,000 credits/月、50 请求/分钟。100 币每 5 分钟逐币查询，30 天即 864,000 credits，免费不可持续。假设 3 所、每所 500 pairs、每 30 分钟抓取一次为 8,640 credits/月，尚需预留其他查询并按实际响应/计费复核。缓存 60 秒不等于每个上游字段都 60 秒更新。

**决定：原生接口负责重点实时变化，CMC 低频补充映射与覆盖核对，自存历史。** CMC 有免费 Key 后做只读验收，再决定是否承担批量主链路；未验收前显示“待验证”，不声称全网覆盖。供应商汇总不能与其中已包含的原生数据再次相加。这些 latest 接口不保证历史 OI。

来源：[CMC 衍生品文档](https://coinmarketcap.com/api/documentation/pro-api-reference/derivatives)、[更新记录](https://coinmarketcap.com/api/documentation/changelog)、[价格与配额](https://coinmarketcap.com/api/pricing/)。本项目 `docs/research/oi-2026-09-23/` 保留本次已下载的文档快照、探针和复盘报告。


## 确定采用的工具

| 层 | 工具 | 职责 |
|---|---|---|
| 后端采集与计算 | Python、原生HTTP/WebSocket适配器 | 公共行情、原始数据归档、规范化；核心不依赖付费研究服务 |
| 标准指标 | TA-Lib + 固定参数与容差 | ATR、EMA等；箱体/事件为明确规则，不用大模型猜测 |
| 历史研究 | 官方归档、原生历史、自存时间序列 | 保存时间、单位、身份与已知时间，支持可复算回放 |
| 前端图表 | TradingView Charting Library 32.1.0 | 沿用已验证固定提交，Datafeed接本系统行情，不运行Pine或会员策略 |
| 聚合补充 | CMC DerivativesAggregationProvider（验收候选） | 跨平台目录、OI核对、数据缺口补充；不是只做市值Enrichment |
| 可选研究 | Surf、CoinGlass、实体标签服务 | 不作为免费MVP强依赖，不把第三方热力图当真实全量仓位 |

## 运行核验记录的更新

- Aster：2026-09-23复盘已通过公开V3接口获取8个案例的历史K线和funding；以归档manifest中的实际URL为准。官方klines路径警告保留为版本兼容背景，不能再说本轮完全未验证K线。历史OI与全量清算没有因此获得。
- Binance：REST历史OI窗口受限，但公开metrics ZIP归档可覆盖已发布历史。此次已用于9币复盘，读取前排序、去重、checksum核验；不承诺所有币/月份齐全。
- Hyperliquid：本次allPerpMetas返回11组目录，未匹配这9个案例。只说明此次当前目录匹配结果，不能否定历史存在或同名/namespace映射缺口。
- CMC：Free文档已核对，Key认证、具体覆盖与数据时效尚待运行验收。无Key403不代表有Key免费套餐也失败。

新接口适配必须保存原生字段与响应引用。Binance强平的SELL通常对应多头仓位被卖出关闭，BUY通常对应空头买入关闭；每个平台仍以官方side语义为准，不能统一把SELL叫爆空。

## 官方快照

基础接口文档仍在 `docs/api/official`；本次补充 `docs/research/oi-2026-09-23/sources/cmc_*.html/.txt/.json`、`platform_probes.json` 和 `source-manifest.json`。这是前轮实际检索保存内容的同步，不声称本轮重抓了所有上游文档。
