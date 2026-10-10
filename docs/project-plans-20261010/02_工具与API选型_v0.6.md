# 工具与API选型 v0.6

2026-09-23。当前选型以[PRD v1.0](01_产品需求文档_v1.0.md)为准，v0.5及更早为历史。免费API优先；服务器与存储费用另计。正式服务尚未建立，以下区分样本验证与接入实现。

| 职责 | 采用来源/工具 | 当前验证 / 下一步 |
|---|---|---|
| Binance生态目录 | Spot、USDⓈ-M Futures、Alpha各自原生接口 | 前序公共样本；全目录规格/限频验收待开发 |
| 链上Meme/FOMO发现 | Binance Wallet Rising | 前序核验；启动时重新验证官方契约/覆盖。Alpha归生态池 |
| K线、成交、深度 | 对应执行市场原生接口；链上对应明确Pool | 不混市场OHLCV；链上免费历史不足则候选未扫描 |
| 核心Perp DEX | Hyperliquid Info/WS、Aster公共V3 | 原生数量/费用/流动性；HL namespace独立，Aster8币历史K线/费用已有证据 |
| 跨平台美元OI/清算 | CMC免费Key | 8次带Key样本成功；Aster/HL实际有数据。详见文档12 |
| 当前供应与流通cap | CMC等已有可信身份来源按需补充 | 本轮衍生品验证未验证供应端点；不能拿FDV补流通cap |
| 小范围复核/后续扩展 | OKX P0小池；Gate、Bitget P1 | 每源capability和规格对照，Bitget核对UTA迁移 |
| 基础指标 | Python TA-Lib | ATR/EMA固定参数及预热；形态/计划规则由后台实现 |
| 图表 | Charting Library 32.1.0固定已核验提交 | Datafeed接本系统；无Pine/会员策略执行；现有包为已测试资源 |
| 正式工程 | Next.js/React/TS + FastAPI/Pydantic + PostgreSQL | API/worker分进程；SQLAlchemy/Alembic；稳定版本在实施时锁定 |
| 通知 | Telegram Bot API | 官方文档新下载；P0文本、绑定、outbox；本轮未发送 |
| 可选研究 | Surf/CoinGlass/地址标签/其他池数据商 | 非首版免费必需依赖，按缺口验收 |

CMC可调用标准路径：`/v5/cryptocurrency/derivatives/market-pairs/list/latest`及`/v5/derivatives/liquidations/cryptocurrency/list/latest`。按所批量接口只完成官方文档核查，尚未用本Key调用。官方配额与实际响应credits分开，不假设一请求恒等于一credit。

用户不必串联多个热门榜。Rising为第二池发现入口；行情adapter对接明确Instrument/Pool，来源缺失就展示缺失。当前不承诺所有链上币都有免费的完整周线或可卖出性验证。

去重前扫描名额：Binance150–200、Alpha最多100、Rising50–100；深度共享80资产。预算按资产下的市场数和端点权重计，不仅按ticker数。CMC30分钟只是初始调度间隔，还必须通过月预算：100币每30分钟各耗1credit也需144,000/月，不能默认全部轮询。

同源冲突组隔离、跨源同instrument只计一次；CMC的当前美元广度不能伪装成数量历史。当前funding缺周期与已结算标志，原生补齐后才进入费率累计。清算窗口汇总不能与其已包含的原生清算相加。

官方文档索引见[api/README](api/README.md)，固定图表证据见[采用记录](07_ChartingLibrary_32.1.0_采用与验收.md)。后续交易所统一接入流程和参数以PRD §7–8为准。
