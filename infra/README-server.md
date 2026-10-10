# ERJI /erji/ 部署执行说明

已批准 v0.3：目标 https://xiexiu.io/erji/，与现有 /xbot/、/tg/ 共存。独立新库，单独 CMC / ByKaranteli 密钥，通知不启动。

## 发布前状态

应用已支持构建期 NEXT_PUBLIC_BASE_PATH=/erji，直接 API、EventSource、登录跳转、TradingView 资源 URL 同步前缀。Next Link / router 由框架自动加前缀。API 的 ERJI_COOKIE_PATH=/erji 统一登录和退出 Cookie 路径；ERJI_WEB_ORIGIN 仅填写协议与主机。

当前脚本分为：只读预检 infra/preflight-server.sh；首次私有部署 infra/deploy-first-server.sh；供现有 HTTPS server 引用的 infra/nginx-erji-location.conf；备份 infra/backup-erji.sh。不自动猜测现有 Nginx 配置位置或替换主站。

## 先读服务器状态

沿用 XBOT 的 SSH key 和 StrictHostKeyChecking=yes。预检之后查看现有 xiexiu.io 的有效 server/location、两个项目实际后台状态、磁盘及内存峰值、Docker 网段和路由。SSH 不通时不得把本地通过描述成已部署。

服务器需要 Docker Compose v2、Python 3、curl。缺少时先评估在这台共存服务器安装的影响。compose.limits.yml 是审核方案的初始内存预算；设置 DB/API/WEB/LIVE/JOBS/STREAMS 六项 ERJI_*_CPUS 配额后，显式使用 compose.yml + infra/compose.limits.yml 两个文件。build 串行但不因此具有运行期容器内存上限，资源不足必须改用外部镜像构建。

## 首次私有部署

1. 固定最终提交，运行 tools/package_server.py 生成源码和本机图表资源包、逐文件 manifest 和压缩包 SHA-256。包中无 .env、数据库、私钥或 API Key。
2. 服务器创建独立 /opt/erji-releases/<时间-SHA>，在写入前检查不已存在。上传后先校验压缩包哈希，再解包。
3. 单独将 ERJI 的 CMC、ByKaranteli 密钥通过已核验 SSH 传至 root 私有 /opt/erji-incoming（0700，文件0600），不打进代码包。部署脚本复制为后端 UID 10001 的0400文件。确认正常后清理临时副本。
4. 确认预检、资源及路由后，设置 ERJI_PREFLIGHT_CONFIRMED=yes 和六项 CPU 配额，运行 bash infra/deploy-first-server.sh。新数据库卷 erji_pgdata；迁移先于 API 和 worker。
5. 脚本验证 localhost:18770/erji/ 的登录、401隔离、查询、图表文件和 SSE。尚未通过公开 HTTPS 和采集验收时只能标记私有启动完成。

## 接入 HTTPS

备份现有 Nginx 生效文件，在正确的 xiexiu.io HTTPS server 内 include ERJI location，不改变 TLS、/xbot/、/tg/。nginx -t 成功后 reload。

将 /opt/erji-shared/.env 的 ERJI_WEB_ORIGIN 设置为 https://xiexiu.io，沿用 ERJI_COOKIE_PATH=/erji 和原数据库密码。使用两个 Compose 文件重新创建 API 及 worker 使环境一致，保留数据卷。然后从 https://xiexiu.io/erji/ 验证登录、CSRF写入、图表、SSE、退出，以及两个原项目部署前后的状态。

管理员口令在 /opt/erji-shared/secrets/admin-token.txt，仅通过用户私有终端取得，不输出到部署日志。最终切换 HTTPS 后不以 HTTP 隧道登录结果代替公网验证。

## 备份和回滚

infra/backup-erji.sh 仅备份 ERJI 数据库并保留最近七份。首次启用调度前，先生成一次备份，在 ERJI PostgreSQL 内独立临时库做 pg_restore --exit-on-error 恢复和表/迁移版本核对；不对现有 XBOT/TG 数据库操作。归档能列目录不等于恢复演练成功。

恢复演练后安装仅执行该脚本的 root 定时任务，避开两个原项目备份时间，并配置 ERJI Nginx 日志轮转。未完成这一步不宣称备份已部署。

首次失败保留卷、秘密与日志。仅停止 ERJI，或撤销本次 ERJI Nginx include 并 nginx -t/reload。已有 ERJI 状态时首次脚本主动退出，后续更新需先做备份和迁移兼容检查。禁止 down -v、重启宿主机或停止其他项目来处理 ERJI 故障。
