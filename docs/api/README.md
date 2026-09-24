# 官方 API 文档快照

本目录保存本项目选型中使用的官方文档、官方 GitHub 仓库文件和官方 SDK 参考。快照用于开发时查阅和审计，运行时仍以供应商当前 API 响应为准。

## 文件结构

- `official/`：抓取的原始 HTML/Markdown/源码文件。
- `official/SHA256SUMS.txt`：每个成功抓取文件的 SHA-256。
- `manifest.json`：每个文件的来源、抓取状态、最终 URL、大小、时间、校验值和备注。
- `../../tools/download_official_docs.py`：刷新脚本。

## 本轮来源（共 24 份快照/官方 SDK 文件）

| 来源 | 本地文件 | 选型用途 |
|---|---|---|
| Binance Spot | `binance-spot-rest-api.md`、`binance-spot-websocket-streams.md` | 现货 K 线、成交、盘口 |
| Binance Alpha | `binance-alpha-sdk-readme.md`、`binance-alpha-market_data_api.py`、`binance-alpha-websocket_streams.py` | Alpha token list、K 线、ticker、depth、trades |
| Binance USDⓈ-M Futures | `binance-sdk-usds-futures-readme.md`、`binance-sdk-usds-futures-market_api.py` | OI、funding、mark、合约规格 |
| OKX | `okx-api-v5.html` | 跨场复核 |
| Gate | `gate-api-futures.md`、`gate-api-python-readme.md` | P1 扩展；官方页面在本机抓取为 403 |
| Bitget | `bitget-uta-introduction.html` | P1 UTA 扩展 |
| Hyperliquid | `hyperliquid-info-api.html`、`hyperliquid-websocket.html` | Perp DEX 市场级数据和 WS |
| Aster | `aster-futures-v3.md`、`aster-api-documentation.html`、`aster-connector-python-readme.md` | Perp DEX V3 市场数据 |
| CMC/CoinGecko/DexScreener | 对应 HTML；CMC新证据见下方 | 身份/供应/池子；CMC另承担跨平台美元OI与清算补充 |
| Surf | `surf-data-api.html`、`surf-hyperliquid-positions.html` | 地址级标签/仓位按需补充 |
| Lightweight Charts | `tradingview-lightweight-charts-docs.html` | 历史备选参考；主图已选Charting Library32.1.0 |
| TA-Lib | `ta-lib-functions.html` | 标准技术指标 |

## 更新规则

运行刷新脚本后先检查 `manifest.json`：

- `status=ok` 且 `http_status=200` 才算本次成功抓取；
- `source_url` 是用户查阅的官方页面，`fetch_url` 是实际下载地址；
- 官方 GitHub raw 文件可以作为页面被保护或动态渲染时的替代抓取源，但必须查看 `notes`；
- 页面可能包含后续 JavaScript 才加载的内容，HTML 快照不保证完整渲染结果；
- 供应商 API 的配额、许可证、再分发和历史保留政策需要在上线前重新核对；
- 更新后如内容被替换，旧版 SHA-256 只在 Git 历史或外部归档中可追溯，不能把新文件当成旧证据。

刷新命令：

```powershell
python D:\Axiangmu\erji\tools\download_official_docs.py
```





## 2026-09-23实施基线新增

- `official-2026-09-23/telegram-bot-api.html`及同名JSON：本轮新下载的官方Bot API与抓取时间/SHA-256。核对polling/webhook互斥、secret_token、retry_after与消息长度；没有发送消息。
- `cmc-auth-check-2026-09-23/`：8次带Key只读实测的脱敏响应、manifest及重新计算的coverage-summary；4币市场及3币清算成功。Key不在目录内。
- `../research/oi-2026-09-23/sources/`：CMC衍生品文档/套餐/更新记录与前轮研究快照。latest接口不保证历史能力。
- [CMC实测报告](../12_CMC免费API实测_2026-09-23.md)、[当前工具选型](../02_工具与API选型_v0.6.md)、[PRD](../01_产品需求文档_v1.0.md)。

原manifest仍只描述原24份文件，本轮证据用各自manifest，不混称重新下载全部24份。新供应商文档刷新后必须重新核对字段和限频；HTTP200不保证聚合字段新鲜或无重复。
