# ERJI 多源加密市场雷达

## 当前代码：v0.1.0 工程底座

已建立 Next.js / FastAPI / PostgreSQL 正式工程，接入 Binance、Hyperliquid、Aster 各4个真实永续市场，共12市场。产品基线已同步9月24日修订稿。下方旧资料段落保留历史背景；当前实现状态以此段及验收记录为准。

- [真实市场页面](http://127.0.0.1:18770/markets)
- [信号总览](http://127.0.0.1:18770/signals)
- [API文档](http://127.0.0.1:18771/docs)
- [首批验收及待开发项](docs/15_M0工程搭建验收.md)
- [本地运行与服务器部署](docs/14_本地运行与服务器部署.md)

本机启动：在项目目录运行 `powershell -File tools/start-local.ps1`；停止应用运行 `powershell -File tools/stop-local.ps1`。数据库复用本机PostgreSQL程序，但使用项目独立数据目录和55432端口，不修改原有服务。密码仅存忽略文件.env。

首次恢复代码：Python创建.venv，`pip install -r requirements.lock`、`pip install --no-deps -e .`、`npm ci`，然后运行 `python tools/vendor_chart.py` 补齐固定图表资源，再执行启动脚本。所有Python命令使用项目.venv。

检查：`python -m pytest -q`、`python -m alembic check`、`python tools/verify-chart.py`、`npm run typecheck`、`npm run build`。集成测试要求已迁移数据库且至少完成一轮真实采集；会发布一份内容不变的新设置版本，不删除行情。

当前是M0及M1小池数据链路：真实K线、原生OI、已结算费率、设置发布、收藏、SSE更新可用。策略尚未启用，参与列表为空；自动箱体/目标、链上热门池、CMC正式采集、TG发送及回测待开发。每市场USDT/USDC名义金额分别标注，流通市值占比未知；1D OI复用近24H。图表目录固定2,277文件，运行时无需GitHub。

当前仅本机单管理员。Linux Compose已准备并静态检查，尚未在Docker或服务器上实际运行；服务器先用SSH隧道私有测试，公网登录后续完成。

## 原文档交付记录（历史）

当前实施基线：**PRD v1.0 · 2026-09-23**。目标是两市场池、跨平台OI与明确参与计划的研究工具。项目当前包含文档、API/历史研究证据及HTML演示；正式Next.js/FastAPI服务尚未建立。

## 当前入口

- [最终产品需求文档](docs/01_产品需求文档_v1.0.md) / [HTML](docs/01_产品需求文档_v1.0.html)
- [实施任务与验收矩阵](docs/11_实施任务与验收矩阵_v1.0.md)
- [需求讨论覆盖清单](docs/09_需求讨论覆盖清单_v1.0.md)
- [API与工具选型v0.6](docs/02_工具与API选型_v0.6.md)
- [统一数据契约v0.6](docs/03_数据字段与统一接口.md)
- [CMC免费Key实测](docs/12_CMC免费API实测_2026-09-23.md)
- [官方API本地资料索引](docs/api/README.md)

本地预览服务运行时：[最终需求HTML](http://127.0.0.1:18766/requirements-v1.html)、[现有v0.7合成信号页](http://127.0.0.1:18766/signals-a.html)。v1规划的首报列、日周目标、设置与TG等将在正式代码阶段实现，不代表当前页面已经接实时。

CMC用户免费Key已成功完成8次只读样本请求，响应计8 credits；支持本次4币跨平台市场和3币清算，包含Aster/Hyperliquid OI。正式采集仍须去重、冲突隔离、时效和原生单位核对。密钥仅在用户受限文件/后端引用，不进入仓库。

## 历史与研究

v0.7及更早PRD、文档04/05/06/08、ADR001–003和旧预览为历史研究/设计，不作为冲突参数的当前默认。文档07保留图表版本采用证据；文档10记录旧预览验收；9币复盘保留在`docs/research/oi-2026-09-23`，不等于收益策略已验证。

下一实施顺序为M0工程/契约→M1真实数据→M2信号计划→M3总览图表设置→M4TG运行→M5前向验证。任务依赖与验收在文档11，具体规则和参数在PRD唯一登记表。
