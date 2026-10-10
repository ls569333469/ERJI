# ERJI 部署与更新运维手册 v1.0

> 适用项目：ERJI 多源加密市场雷达
>
> 生产入口：`https://xiexiu.io/erji/`
>
> 文档状态：已部署基线，供后续发布、更新、验收和回滚使用
>
> 更新时间：2026-09-29

## 1. 文档目的与生产边界

本手册记录 ERJI 的生产部署基线，并将 XBOT 项目的发布、备份、反向代理、健康检查和回滚经验固化为可重复执行的步骤。后续更新应以本手册为准，禁止把首次部署脚本直接用于日常更新。

ERJI 当前是数据采集、分析和信号展示系统。已部署不等于所有外部数据源均可用，也不等于具备自动交易安全性。当前未启用自动交易和 TG 通知；任何健康检查只能证明服务和数据链路状态，不能证明策略收益或交易安全。

文档中的命令使用占位变量表示生产主机和版本号。执行前应替换为本次发布值，并将实际值写入发布记录；密钥正文、管理员口令和响应中的敏感信息不得写入日志或文档。

## 2. 三项目共存说明

生产服务器 `107.172.78.150` 上目前共存三个项目，ERJI 是第三个项目：

| 项目 | 公网路径 | 运行隔离 | 维护边界 |
|---|---|---|---|
| XBOT | `https://xiexiu.io/xbot/` | 独立容器、服务和数据 | ERJI 更新不得停止、重建或迁移其容器和数据库 |
| TelegramForwarder | `https://xiexiu.io/tg/` | 独立容器、服务和数据 | ERJI 更新不得改变其服务、认证和转发状态 |
| ERJI | `https://xiexiu.io/erji/` | 独立 Docker Compose、PostgreSQL 数据卷和密钥目录 | 本手册适用范围 |

每次发布前后都要记录三个项目的服务状态。只允许在现有 `xiexiu.io` HTTPS server 中增加或更新 ERJI 的 location；不得覆盖主站、TLS、`/xbot/` 或 `/tg/` 配置。

## 3. 生产拓扑和目录

生产访问链路为：

```text
浏览器
  -> https://xiexiu.io/erji/
  -> Nginx HTTPS server（路径转发）
  -> 127.0.0.1:18770
  -> ERJI Web/API 容器
  -> ERJI Worker / Streams / PostgreSQL
```

ERJI Web 只绑定服务器回环地址 `127.0.0.1:18770`，API 和 PostgreSQL 不直接暴露公网。服务器上的具体项目目录、Compose 文件、备份目录和密钥挂载目录以生产部署记录为准；更新时先在服务器只读确认路径，禁止凭记忆猜测路径。

推荐目录职责如下：

```text
<ERJI_ROOT>/              当前生产 checkout 或发布目录
<ERJI_ROOT>/docker/       Dockerfile、Compose、启动配置
<ERJI_ROOT>/docs/         需求、部署和验收文档
<ERJI_DATA>/              PostgreSQL 数据卷及应用持久化数据
<ERJI_BACKUP>/            数据库备份和校验记录
<ERJI_SECRETS>/           生产密钥挂载目录，权限最小化
<ERJI_RELEASES>/          已发布版本和回滚快照
```

## 4. 容器、端口和资源限制

| 组件 | 作用 | 生产暴露方式 | 维护要求 |
|---|---|---|---|
| `web` / API | 页面和 API | 仅 `127.0.0.1:18770` | 更新后检查 HTTP、日志和导入 |
| `worker` | 定时采集、分析、信号计算 | 内网容器 | 检查进程状态和最近任务时间 |
| `worker-streams` | 实时行情/链路流 | 内网容器 | 检查重连和来源状态 |
| `postgres` | ERJI 数据库 | Compose 内部网络 | 只使用 ERJI 数据卷 |
| Nginx | HTTPS 和 `/erji/` 路径转发 | 公网 443 | 配置先备份、`nginx -t` 后 reload |

禁止把 PostgreSQL、内部 API 或管理端口加入公网监听。禁止使用 `docker compose down -v`，因为这会删除数据卷；也不得对 XBOT 或 TelegramForwarder 执行 `down`、重建、迁移或清理操作。

## 5. 密钥、权限和日志规范

本机密钥文件只记录路径，不复制到 Git：

```text
CMC：F:\key\CMC.txt
ByKaranteli：F:\key\bykaranteli.txt
ERJI 服务器管理员信息：F:\key\ERJI-server-admin.txt
```

生产服务器只挂载 ERJI 所需的密钥目录，权限应限制为运行用户可读。密钥文件、`.env`、数据库转储和发布包中的敏感配置必须加入 `.gitignore` 或发布排除清单。命令输出、HTTP 错误和监控日志必须隐藏 Key、口令、Authorization、URL 查询参数和完整响应体。

SSH 连接应沿用 XBOT 的安全约定：使用既有 SSH key，启用 `BatchMode=yes` 和 `StrictHostKeyChecking=yes`，禁止在命令行或文档中写入密码。

## 6. 更新前本机检查

### 6.1 确认工作区和版本

```powershell
Set-Location D:\Axiangmu\erji
git status --short
git branch --show-current
git log -1 --oneline
```

工作区必须能明确区分待发布提交和未提交文件。发布前不得把临时调试文件、密钥、数据库文件、构建缓存和本机日志带入包内。若需要修改代码，先提交到受控分支并记录 commit SHA。

### 6.2 运行本地检查

按仓库实际脚本执行格式、类型、单元测试和构建检查。至少确认 API、Web、Worker 和 Streams 的入口可导入；不要只依赖前端能打开作为发布依据。

重点检查：

- `requirements.lock` 覆盖 `pyproject.toml` 中所有运行依赖；此前遗漏 `websockets` 会导致 `worker-streams` 启动失败。
- Shell 脚本统一使用 LF 换行；CRLF 在 Linux 中可能产生 `set: pipefail\\r: invalid option name`。
- Dockerfile 构建上下文中所有模块目录对容器运行用户可读；此前目录权限不足会导致 `Could not import module "services.api.main"`。
- K 线批量写入以 `(instrument_id, resolution, open_time)` 去重，避免 PostgreSQL 报 `ON CONFLICT DO UPDATE command cannot affect row a second time`。

### 6.3 生成发布说明

记录本次变更范围、数据库迁移、配置变更、外部数据源影响、回滚版本和预计维护窗口。首次部署、代码更新、数据库迁移和 Nginx 变更要分开标记。

## 7. 服务器只读预检

更新服务器前先做只读检查，确认 SSH、磁盘、内存、Docker、Compose、Nginx 和三个项目状态。建议记录：

```bash
hostname
df -h
free -h
docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}'
docker compose version
systemctl is-active nginx
systemctl status <xbot-service> --no-pager
systemctl status <telegram-forwarder-service> --no-pager
ss -lntp | grep -E '18770|80|443'
```

对于 XBOT 和 TelegramForwarder，更新前后记录 `MainPID`、`NRestarts`、`ActiveState`。若已有项目异常，应先记录并停止 ERJI 发布，不能把既有问题归因给本次更新。

## 8. 发布包生成与校验

发布包必须来自 Git 提交，而不是直接压缩脏工作区。推荐流程：

```powershell
$Version = "<release-version>"
$Out = Join-Path $env:TEMP "erji-$Version"
Remove-Item -Recurse -Force $Out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $Out | Out-Null
git archive --format=tar --output="$Out\erji-$Version.tar" HEAD
tar -xf "$Out\erji-$Version.tar" -C $Out
Get-ChildItem $Out -Recurse -File |
  Where-Object { $_.Name -notlike "erji-$Version.tar" } |
  Get-FileHash -Algorithm SHA256 |
  Export-Csv "$Out\manifest-sha256.csv" -NoTypeInformation
```

上传前核对 manifest、commit SHA、文件数量和排除项；服务器解包后重新计算 SHA256，必须与本机 manifest 一致。发布目录应以不可变版本名保存，不能直接覆盖当前目录，以便回滚和审计。

## 9. 数据库备份和迁移检查

更新前先备份 ERJI PostgreSQL，并记录备份文件 SHA256、数据库版本、schema 版本和恢复验证结果。生产备份计划为每日北京时间 04:37，保留最近七份；发布前仍需额外做一次即时备份。

迁移检查顺序：

1. 读取迁移文件和目标 schema，确认向前迁移是否可回滚。
2. 在临时数据库或恢复副本上演练迁移。
3. 备份成功后再执行生产迁移。
4. 迁移完成后检查表、索引、唯一约束和采集任务。

禁止使用 `docker compose down -v`。禁止删除旧发布目录、旧备份和数据卷来“解决”迁移问题。已完成过真实 PostgreSQL 备份恢复演练的记录应保留在发布记录中。

## 10. Docker 镜像构建

在服务器或受控构建环境中使用已校验的发布目录构建镜像，并记录镜像 digest：

```bash
docker compose -f <compose-file> config
docker compose -f <compose-file> build --pull
docker image ls
docker inspect <image>:<tag> --format '{{.Id}}'
```

构建后至少执行一次容器运行用户导入检查和依赖检查。确认 `requirements.lock` 已包含所有运行依赖，确认 Shell 文件为 LF，确认容器内应用目录可读。构建失败时保留日志，不要切换到未审核的临时镜像。

## 11. ERJI 容器更新

建议采用“新发布目录 + 显式 Compose 项目名”更新：

```bash
cd <ERJI_RELEASES>/<release-version>
docker compose -p erji config
docker compose -p erji up -d --no-build postgres
docker compose -p erji run --rm <migration-service-or-api> <migration-command>
docker compose -p erji up -d web worker worker-streams
docker compose -p erji ps
```

实际服务名以生产 Compose 为准。更新期间只操作 ERJI 的 Compose 项目。先启动数据库并完成迁移，再更新 API/Web 和 Worker；如果迁移或健康检查失败，应保留现场并执行回滚流程。

发布后查看最近日志，确认没有导入、权限、依赖、数据库冲突或流重连错误：

```bash
docker compose -p erji logs --since=10m --no-color web worker worker-streams
```

容器日志单文件限制为 10 MiB、保留 5 份。日志中不得出现 Key、口令或完整请求 URL。

## 12. Nginx / HTTPS 更新

Nginx 更新必须严格按以下顺序执行：

1. 备份当前生效配置和 ERJI location。
2. 只在现有 `xiexiu.io` HTTPS server 中 include 或更新 `/erji/` location。
3. 检查上游仍为 `127.0.0.1:18770`，路径重写、静态资源和 WebSocket 规则与应用一致。
4. 执行 `nginx -t`，成功后才 `systemctl reload nginx`。
5. 检查 `/xbot/`、`/tg/` 和主站仍返回原状态。

示例：

```bash
sudo cp <active-nginx-config> <backup-dir>/nginx-$(date +%Y%m%d-%H%M%S).conf
sudo nginx -t
sudo systemctl reload nginx
```

禁止直接重启 Nginx 替代配置检查；禁止修改 TLS、主站、`/xbot/` 或 `/tg/` 配置来解决 ERJI 路径问题。

## 13. 更新后验收

### 13.1 进程和 HTTP

```bash
docker compose -p erji ps
curl -fsS https://xiexiu.io/erji/
curl -fsS https://xiexiu.io/erji/api/health
```

验收页面、API、静态资源、反向代理、WebSocket（如启用）和数据库连接。检查 API 错误时只保留状态码和脱敏后的错误类别，不记录参数、Key 或响应体。

### 13.2 数据和来源状态

确认市场列表不再只有 BTC、ETH、SOL、HYPE 等少数大市值资产，检查 Binance 现货/合约、Alpha、链上热门池和已接入的 Aster、Hyperliquid 等来源是否按能力返回数据。没有数据的来源或代币要明确标记“无数据/权限不足/过期/来源时间未知”，不能伪装成正常值。

来源页不能只显示初始化的 `pending`；应依据实际 `DataCapability` 统计有效、过期、来源时间未知、权限不足和其他缺口。

### 13.3 关键业务检查

- K 线批量写入按 `(instrument_id, resolution, open_time)` 去重。
- OI、OI/市值比例、资金费率、清算数据、首次信号价格和多周期技术指标能显示当前值与时间。
- 信号页能显示结论、依据、阶段、参与区、保护价、目标区、失效条件和可信度，而不是只显示“可关注”。
- 图表能显示已确认箱体、支撑/阻力、斐波那契或其他启用指标；指标无数据时显示原因。
- 触发和撤销逻辑能在测试数据上重演，并且不会重复生成同一 K 线的信号。

### 13.4 三项目回归

逐一访问：

```text
https://xiexiu.io/xbot/
https://xiexiu.io/tg/
https://xiexiu.io/erji/
```

对 XBOT 和 TelegramForwarder 对比更新前后的 `MainPID`、`NRestarts`、`ActiveState`、HTTP 状态和关键日志，确保 ERJI 发布没有改变它们的运行状态。

## 14. 外部数据源验收与当前限制

当前已接入或已验证存在数据能力的来源包括 Aster、Hyperliquid、Binance Alpha、OKX，以及 CMC、ByKaranteli 的独立 Key 挂载。Aster 原生流已验证连接；币安钱包采集已修复批量重复 K 线写入问题。

当前已知限制必须在发布记录中保留：服务器访问 Binance 现货和合约 REST 返回 HTTP 451，Binance 原生流会重连。该限制不得被标记为“全量正常”，应展示来源不可用或数据延迟，并允许其他来源提供替代数据。能支持的币种应尽量纳入；没有覆盖的数据要明确标记，不要用其他资产数据替代。

CMC 和 ByKaranteli 主要用于综合市场/清算等数据补充，不能据此声称交易所逐笔仓位全部公开。Hyperliquid 等链上可公开查询的仓位与清算数据，应与多交易所估算数据分开标注来源和精度。

## 15. 回滚流程

触发条件包括：容器持续重启、API 500、数据库迁移失败、关键数据写入错误、ERJI location 导致其他项目异常、或验收关键项失败。

回滚顺序：

1. 记录当前版本、容器状态、最近日志和失败时间，不先删除现场。
2. 若 Nginx 变更导致问题，恢复最后一个已验证配置，执行 `nginx -t` 后 reload。
3. 停止或切换 ERJI 应用容器到上一个已验证镜像/发布目录；不停止 XBOT 和 TelegramForwarder。
4. 若涉及 schema 迁移，按迁移回滚方案恢复，必要时从最近一次已验证备份恢复到 ERJI 专用数据卷。
5. 验证 `/xbot/`、`/tg/`、`/erji/` 和关键数据链路。
6. 将根因、影响、恢复时间、备份文件和后续修复写入发布记录。

除非恢复方案明确要求，禁止删除生产数据卷、备份和旧发布目录。

## 16. 备份恢复与日志保留

每日 04:37（北京时间）执行数据库备份，保留最近七份；每次发布额外创建即时备份。至少定期验证：备份文件可读、SHA256 可复算、可恢复到临时数据库、schema 和关键数据完整。

Docker 日志单文件 10 MiB、保留 5 份。发布日志至少包含：发布版本、commit SHA、镜像 digest、开始/结束时间、操作者、迁移结果、备份路径、验收结果、回滚结果和已知限制。

## 17. 常见故障排查

| 症状 | 优先检查 | 处理方向 |
|---|---|---|
| `Could not import module "services.api.main"` | 容器工作目录和目录权限 | 修正 Dockerfile 中运行用户可读权限，重新构建并执行导入检查 |
| `set: pipefail\\r: invalid option name` | Shell 文件换行符 | 转为 LF，重新生成发布包 |
| `worker-streams` 启动失败 | `requirements.lock` 是否包含 `websockets` 等运行依赖 | 补齐锁文件、重建镜像 |
| `ON CONFLICT DO UPDATE command cannot affect row a second time` | 同一批次是否有重复 `(instrument_id, resolution, open_time)` | 入库前去重并补充唯一约束测试 |
| HTTP 500 | 容器日志、数据库迁移、环境变量 | 仅记录状态码和脱敏错误类别，必要时回滚 |
| Binance REST 451 / 流重连 | 服务器出口和来源限制 | 标记来源不可用/延迟，使用已验证替代源；不要伪造完整数据 |
| `/erji/` 打不开 | Nginx location、上游端口、静态资源路径 | 恢复配置备份，`nginx -t` 后 reload |
| 更新后 XBOT/TG 异常 | 三项目服务状态和 Nginx diff | 立即停止 ERJI 变更，恢复 Nginx 或 ERJI 版本，不操作其他项目数据卷 |

## 18. 发布记录模板

```text
项目：ERJI
环境：production
公网入口：https://xiexiu.io/erji/
服务器：107.172.78.150
发布版本：
Git commit SHA：
镜像 digest：
发布人：
开始时间（Asia/Shanghai）：
结束时间（Asia/Shanghai）：

变更摘要：
数据库迁移：无 / 有（迁移编号）
即时备份：路径、SHA256、恢复演练结果
发布包 manifest：路径、SHA256
Nginx 备份：路径

更新前：
- ERJI 容器状态：
- XBOT MainPID/NRestarts/ActiveState：
- TG MainPID/NRestarts/ActiveState：
- 磁盘与内存：

更新后：
- ERJI HTTP/API/WS 验收：
- K 线、OI、清算、信号和图表验收：
- 数据源有效/过期/权限不足/无数据统计：
- XBOT 回归：
- TG 回归：

已知限制：
回滚版本：
异常与处理：
操作者签名：
```

## 19. 后续版本维护规则

每次发布前更新本手册的版本、生产拓扑变化、端口、数据源限制、备份策略和新增故障案例。若新增交易所、数据源、容器、域名或数据库迁移，必须同时补充“生产拓扑”“密钥与权限”“验收”“回滚”和“故障排查”章节。

首次部署步骤只用于建立生产基线；日常更新必须使用本手册的发布包、备份、迁移、更新和验收流程。任何可能影响 XBOT、TelegramForwarder、Nginx 主站或共享服务器资源的变更，都要先进行只读预检并在发布记录中单独说明。
