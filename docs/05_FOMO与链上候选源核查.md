# FOMO 与链上候选源核查

日期：2026-09-23

## 结论

`fomo.family` 的页面可以看到热门代币、持有人、交易员观点和 K 线，但不应直接视为稳定公共数据 API。

本轮对 token 页面公开 HTML 和前端 JavaScript 做了只读核查，发现：

- 后端主域名为 `https://prod-api.fomo.family`。
- 前端使用 `/proxy/trendingTokens`、`/proxy/tokenDetails` 等内部路由。
- K 线历史使用 `/api/2/token/ohlcv-history`，实时 OHLCV 通过 `wss://prod-api.fomo.family/ws`。
- 请求通过 Privy access token 认证，并携带 `X-Supported-Chains`。
- 未认证请求被返回 `unauthorized`，说明直接复制网页请求不能构成稳定、可授权的接入方案。

## 在本项目中的位置

1. **首选发现源：** Binance Alpha 官方 token list/市场数据；再用 DexScreener、GeckoTerminal、CMC、CoinGecko 做跨源确认。
2. **热度参考源：** `fomo.family`、其他 FOMO 榜和社交数据可以作为候选发现线索，字段命名为 `discovery_rank` 或 `social_heat`，不能直接当作市值、成交量或流动性事实。
3. **正式信号门槛：** 价格、K 线、成交量、池流动性和合约风险必须来自可验证的链上池/交易数据；热榜最多参与排序或加速刷新。
4. **授权边界：** 未取得明确 API 授权前，不绕过登录、不复用用户令牌、不高频调用其内部接口。

## 候选池建议

- 默认流通市值 `>= 1,000,000 USD`；无法确认流通供应时标记 `unknown`。
- 主要池流动性达到可评估门槛。
- 最近 24 小时存在真实交易。
- 保存 `chain_id`、完整合约地址、池地址、报价币、来源、时间和数据质量。
- 同名 ticker 不合并；同一资产跨 Alpha、链上池和 Binance 上市市场通过 `Asset` 关联。

## 适合的系统结构

```text
Binance Alpha token list ─┐
DexScreener/GeckoTerminal ├─> 候选发现与身份映射 ─> 市值/池深度/安全门槛
CMC/CoinGecko/Surf ───────┘                              │
链上池交易与 K 线 ──────────────────────────────────────┘
                                                          ├─> 技术形态引擎
Binance/Hyperliquid/Aster 永续数据 ────────────────────────┘    + 妖币/逼空引擎
```

`fomo.family` 可以保留为人工对照和未来正式授权接入候选，但不进入免费核心链路。
