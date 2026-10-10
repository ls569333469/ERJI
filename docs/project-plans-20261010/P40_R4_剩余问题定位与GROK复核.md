# P40 R4 · 剩余问题定位与 GROK 复核

状态：**定位与拟修方案，待 GROK 复核。R4 是此次调查编号，当前业务代码仍为 P40-reliability-r3。**

用户要求：先定位问题、整理文档，再让 GROK 审核。本轮仅读取本地代码、数据库、API、日志和官方公开文档，保存证据；未修改业务代码、配置、生产数据，未重启，未恢复已暂停的 erji-p39 定时监控。

## 1. 结论与纠正

当前系统持续推进，近期没有新增死锁、任务失败、10055 或服务退出；价格窗口和事件追踪仍不完整。不能用端口正常推导全部数据正常，也不能把所有缺失都归结为重连。

| 编号 | 问题 | 定位程度 | 影响与优先级 |
|---|---|---|---|
| R4-01 | 周期同步覆盖需求 source，丢掉 trigger_event_id / evaluated_at | **源码、纯内存复现、同端点数据库证据一致，已确认** | 新事件与分析结果因果身份不完整；优先修。不能据此认定所有异动未入库或买入丢失 |
| R4-02 | 现货 changed-only 行情不保证逐币15s连续；K线辅助输入也出现逐币空档 | **来源语义及合格币断档已确认；缺失逐项来源仍待细分** | 部分现货价格5m/15m规则不可计算；优先核合格币输入，不只调重连 |
| R4-03 | market 通道反复本地 ping 超时关闭 | **失败类型已确认；网络、接收背压与调度各自贡献未确定** | 逐笔成交/高周期K线实时输入受扰，补数压力增加；不能等同全局mark断档 |
| R4-04 | 连接长持有；单次警告缺少占用阶段；checkout_ms 字段实际统计持有时间 | **长持有及诊断缺口已确认；每个62s警告的根因未全部确认** | 短时资源争用和分析延迟；不能用汇总p95把某次警告直接归到发布锁 |
| R4-05 | OI 即时 ready 低，与300s采集、90s端点、catalog刷新和统计时点相关 | **时间口径已确认；真实OI漏检尚未证明** | 页面即时资格波动；应核事实到重放的闭环，不能先改策略或升频 |
| R4-06 | 容量诊断仍按OI普通60s、活跃10s、候选15s估算 | **硬编码已确认** | 误导容量和频率判断；修诊断，不能据该估算改实际调度 |

**纠正此前交流中的过强判断：**“5分钟采集和90秒新鲜度矛盾，所以要统一TTL”不应直接当成已确诊的信号漏检问题。当前已有真实端点重放、新OI到达触发，以及重启时持久化OI种子恢复。优先核它们是否完整工作，保持OI15m/≥5%、普通300s、T2重点60s、T1价格连续15s、入场/盘口90s。

## 2. 证据批次和当前状态

实际源码：`D:/Axiangmu/erji/work/production-fix-20260929/src`。当前run：`20261010T012025-3d132d17`，09:20:25启动，generation `P40-reliability-r3`。

最近四轮为北京时间11:52、12:02、12:12、12:22；本次另做12:40:54有限采样、随后身份SQL及截至12:47:09注册日志增量。不同时间点分别记录，不将API查询时刻当成窗口snapshot_at。startup ready；此前六个launcher PID/启动ticks匹配；本轮21个实施清单文件哈希无变化，另保存14个调查文件哈希。

| 采样 | 价格5m ready/eligible | 价格15m ready/eligible | 合约OI6m | 现货报价90s |
|---|---:|---:|---:|---:|
| 11:52 | 480/643 | 470/643 | 560/563 | 626/765 |
| 12:02 | 475/642 | 464/642 | 560/563 | 733/765 |
| 12:12 | 496/641 | 474/641 | 560/563 | 733/765 |
| 12:22 | 485/641 | 471/641 | 560/563 | 518/765；12:23复查697/765 |
| 12:40 | 481/642 | 467/642 | 560/563 | 543/765 |

12:40价格窗口snapshot_at=`1791607234233`，即约12:40:34，较SQL采样旧约20秒。5m缺失83 interval_gap、52 latest_stale、26 history_mismatch；15m缺失81 interval_gap、52 latest_stale、42 history_mismatch。价格分母642、OI分母438、合约采集分母563不同，不能相互替换。

同一批次四端口18770/18771/18772/55432可用，API/页面200；schema仍为p39_layered_analysis，分级表及快照索引有效。近期30个最新快照抽样全部匹配投影，属于数据库证据，不证明浏览器首次收到新事件。producer约3.945s完成。BTC/ZK/RLC的5m推进，高周期末端符合当时已收盘周期；前面的整点补数延迟已追上。

自12:22最后监控日志偏移至12:47:09：market异常关闭99次，未新增global或发现5m K线异常关闭；jobs长连接警告23次、streams4次。计数是连接关闭次数，不是丢失币数。注册增量无截断，未新增Traceback/TimeoutError/10055/DeadlockDetected；PG当前run错误记录为空。12:40无锁阻塞，随后SQL捕捉到一次market_states行锁等待，有限复查已消失。

## 3. R4-01：事件身份丢失的确定路径

### 3.1 源码链路

1. `engine/p37/store.py:77–93`：异动保存到Observation时写trigger_event_id；向AnalysisDemand调用upsert前也写入trigger_event_id与evaluated_at。
2. `engine/p39/pipeline.py:43–45`：observation_source返回基础事实字段，本身不含这两个字段。
3. `pipeline.py:96–103`：synchronize从Observation读标量字段重建source，未读取trigger_event_id/trigger_evaluated_at。
4. `pipeline.py:80–81`：upsert用重建的value替换整个source子对象，旧source中的事件身份被覆盖。
5. `pipeline.py:212–218`：recover_demands每300s允许同步一次；sources与origin再把缺身份的source冻结到AnalysisResult。
6. `engine/p39/diagnostics.py:50–58`：event_timings只能从冻结source提取event_id，因而为空。

### 3.2 当前数据库证据

活跃观察需求177条，对应Observation全部带trigger_event_id；175条需求source没有该字段，并且这175条的source_at与Observation完全相同。它们不是“后来发生了新事件所以ID对不上”。六个样本的需求updated_at都等于p39_demand_sync检查点`1791607321244`（12:42:01.244），支持同步覆盖路径。样本包括合约XRP、现货BTC、现货/合约PIXEL等原生市场。

最近200个本run分析结果内，观察来源引用234条，其中226条缺event_id。这是引用条数，既不是独立币数，也不是失败任务数；candidate/manual/plan_recovery来源不要求伪造异动ID，未算入该统计。

纯内存调用现有observation_source复现：相同source_at，初次持久化包装有ID，重建包装没有ID，同时丢evaluated_at。没有调用写入生产库的upsert。

### 3.3 拟修

- 让首次persist与synchronize采用同一个完整来源构造方式，明确传入真实trigger_event_id和evaluated_at；不要在不同调用点各自补字段。
- 保持来源单调更新。只有同一个原生Observation、同一source_at/事件端点可以恢复身份；不把当前Observation最新ID挂到历史分析结果上。
- 补测试：persist后同步一次及多次不丢ID；同端点不因同步产生假来源变化；新端点正确替换；缺历史身份保留unknown；分析/计划/快照沿用被冻结的真实来源。
- 不批量回写旧AnalysisResult；历史修复须另列可证明的映射范围。

## 4. R4-02：现货连续性，不能只看连接是否在线

### 4.1 已确认机制

`native_streams.py:245`使用`!miniTicker@arr`。Binance官方文档原文：**“only tickers that have changed will be present in the array”**，Update Speed=1000ms。含义是每秒发布变化币集合，不能据此假定每个币每秒都有真实观测。

`Detector.delta / PriceSlice.delta`要求窗口最新点≤15s、起点误差≤5s、区间相邻源时间不得超过15s；这与changed-only来源对低变化币的更新保证不一致。`worker.py:360–363`在每次真实5m K线收包时先ingest，再只把已闭合K线送写队列，所以“只持久化闭合K线”并不等于“只每5分钟向价格内存喂一次”。应继续查逐币K线实际输入与source_age_invalid拒收。

官方来源：[Binance Spot WebSocket Streams](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md#all-market-mini-tickers-stream)。本次已读取官方仓库正文并保存摘录；官方网页首次读取未返回正文，未以空页面当作语义证据。

### 4.2 当前样本与诊断偏差

price_receipts只按“最后接受源时间最老”导出30个样本，并非随机样本，也没有按窗口eligible筛选。**本次30个样本中27个不在流动性合格分母内**，不能据它们解释全部642个合格币覆盖。

仍有3个合格样本：现货ONT/ACE/LSK。ONT在当前global连接`d38ee6aad3c64fc7a5d93366584d16ec`以及稳定发现K线连接上记录约17/20/27/35秒逐币源空档；最大源空档177.717s为启动以来最大值，不能说是这分钟新断档。该global连接从本run启动至本次采样未重连。它证明“连接在线≠逐币价格连续”，没有证明所有空档均由miniTicker造成。

合约全局mark连接自10:01重连后ID一直为`6722c356fa28446e9735605f2990e130`。10:01异常关闭后的价格窗口大幅下降及10:22恢复另有历史证据；不要把之后的market逐笔重连算成新的全局mark故障。

### 4.3 拟修/补证

- 诊断优先输出eligible中各venue、各缺失原因的有界样本，并保留source kind、connection_id、源/接收时刻、入内存与入库的拒绝原因。
- 先判断每个缺失币是源没有更新、到达过晚、接收停读、detector拒收，还是写库迟；流稳定但逐币不更新和连接故障分列。
- 若真实5m K线逐币更新本来可满足15s，优先修传递/时钟/拒收链路；若来源确不保证所需采样，提出保持last语义的来源候选及实际负载后复核。
- 保持普通币300s评估；实时输入是窗口依据，不等于每秒执行全市场结构分析。不得复制旧价伪造tick、用合约last替代mark、自动放宽15s或把滚动价格窗换成OHLC窗。

## 5. R4-03：market ping超时的范围和待证因果

本轮新增失败为本地sent=1011 / keepalive ping timeout，rcvd没有关闭码；因此可确认“本地没有及时确认pong”，不能确认“服务端主动要求关闭”，也不能据此排除网络丢包。

`stream_route:243–248`的market通道承载aggTrade与1h/4h/1d/1w K线；合约mark已在独立global通道，发现5m K线也独立。失败会干扰逐笔事实及高周期实时输入，可能增加补数压力，但不是T1合约mark窗缺失的等价指标。

`native_streams.py:313–335`收包循环仍await有界fact_writer.put。websockets16.1.1默认max_queue=16；安装库Assembler超过高水位会pause接收，这是潜在背压链路。当前关闭记录没有接收帧队列深度、pause时刻、ping发送/pong接收时间，也没有同一连接的阶段完整时间线。现有loop_lag约数秒及writer_wait p95不足单独证明20秒pong超时。

拟先补有界记录：connection_id、ping/pong/传输暂停时刻、接收帧队列水位、事实put等待、源事件年龄、线程队列等待和loop_lag，按关闭窗口关联。确认背压后再评估缩减订阅负载/隔离写入；若是网络或服务端输入延迟，则按相应证据处理。**不先关闭ping、不盲目延长timeout、不取消队列上限、不先增加分片或进程。**

## 6. R4-04：长连接和锁的实际含义

- `engine/db.py:89–93`的warning在checkin触发，意味着那个连接刚归还，不能直接说它此刻还卡住。
- `p40/database.py:45–57,89–91`将checkout到checkin的持有时长存进held，再输出为checkout_ms。字段名容易被误解为借用前排队时间，应改明确的hold_ms并兼容旧字段；借用前预算等待用fence_admission单列。
- 12:40 jobs/query有129次>10s持有，异常持有分布p95约52.061s；这些是本进程启动以来累计。当前池错误为空，分析/urgent/gap峰值分别4/2/2；不能把query峰值10理解成围栏8预算失效。
- 同一采样structure:scan_signals阶段p95≈14.529s，gap_admission p95≈22.507s，发布锁等待p95≈186.73ms。阶段分布不等于单条62s警告归因。
- `_scan_signals:106–144`短准备之后仍在事务里重新读K线、检查候选、background、p33 observe；这是可继续定位的长事务范围。R3已移出指标与识别准备，不能重复声称“计算尚未移出”。

拟补单次长持有的run/PID/pool/阶段摘要、backend PID、借用/事务/SQL/guard/commit分时，并采样有限指纹。然后只把证实耗时的纯计算或批量读移出事务，写入时继续核对K线、父事件及租约。保持事件发布排序1877101；目前没有证据支持删锁、扩池、或直接杀连接。

## 7. R4-05：OI低即时覆盖与真实事件是否遗漏

12:40独立OI15m ready=31/438；404 latest_stale、3 history_mismatch；该snapshot合格OI6m=437/438。同期MarketState全合约OI6m=560/563、OI90s=185/563。分母、内存来源与snapshot不同，不将这些数字直接相减当丢失数。

源码已经具备：

- `worker.py:70–95`从真实OiPoint恢复最近16分钟数量端点；重启不需要伪造停机时段。
- `worker.py:246,252–263`通过catalog摄入最新OI，并对未消费且≤90s的新OI加入due；新OI不必等普通300s周期。
- `detector.py:173–260`按真实received/known时点重放历史端点，OI比较15m、误差60s、最新端点90s。

因此，快照低ready不能证明真实到达时未计算。另有**待证明的风险**：运行中catalog只取MarketState最新OI，加载/计算落后期间若多次更新，可能跨过中间OiPoint。重放只能处理已摄入内存的端点；本次没有逐OI摄入游标，尚未量化是否真的遗漏。

拟先记录每个已持久化OI端点的源/接收/摄入/评估/回执身份，核对普通及T2样本；如果证实跨端点，再考虑按游标有界读取OiPoint增量，不重复造重启seed。保留真实源时间/known时间，不能用当前时间改写旧端点来满足90s。

报告区分“现在能算”“最近6m采集到”“真实端点评估回执”，不要把低ready写成OI采集全面积压。调整采集频率或策略TTL属于另外的讨论，本轮不预设必须改。

## 8. R4-06：容量诊断纠正

`engine/pd2/p35_capacity.py`硬编码openInterest普通60s、active10s、candidate15s。真实普通300s、T2/T3采集60s；T3执行30s不等于OI采集30s；盘口/报价90s是有效期，不是采集频率。

拟让容量展示复用实际collection_schedule与原生tier集合，分别显示目标周期、真实完成率、排队年龄、消耗预算；无法计算的项标明估算范围。名额并发、请求额度90%、实际源更新时间分开，不按旧估算认定资源耗尽。

## 9. 建议实施顺序与工程检查

1. **先修确定问题**：R4-01来源构造与同步身份保全；R4-06容量口径；R4-04指标命名与单次归因；R4-02合格币分类诊断。没有业务策略变更或数据库迁移的必要证据。
2. **再按定位结果修数据源**：R4-03关闭前暂停/背压时间线与R4-02逐币真实输入；只有证明瓶颈后才隔离/调整订阅，不一开始换进程。
3. **OI事实闭环**：核实际端点是否均摄入并评估；必要时加有界增量游标，保持策略参数。
4. **端到端真实事件核对**：原生instrument+event_id串起观察、需求、分析结果、计划、快照、投影/API。有no_match/waiting等正常分支；不要求每个异动必须买入。实际合格买入出现才核浏览器首次接收，旧计划不得替代。

检查只证明对应工程行为，不增加70%覆盖才准入等产品门槛。必要的隔离回归覆盖身份保全、取消归还、源时间有效性和同一snapshot匹配；源码检查/有限样本不能替代生产因果证据。修完后再按用户明确要求决定激活；本轮授权止于调查与文档复核。

## 10. 请 GROK 逐条审核

请在`D:/Axiangmu/erji/docs/P40_评审往来.md`追加“GROK · R4审核”，每条给同意/不同意、代码位置、证据与替代方案。不要覆盖本报告或把待审建议写成已实施。

1. 是否认可R4-01同步丢ID的路径、175/177同端点证据及统一来源构造修法？同端点恢复如何避免给历史分析冒挂最新事件ID？
2. 是否认可changed-only不保证逐币15s，以及30个诊断样本27个在分母外的偏差？合格币如何补来源kind/拒收/空档证据；怎样保留last与原连续性规则？
3. 是否认可sent1011不是服务端主动关闭证明？哪些额外暂停/帧队列/ping/pong字段能区分网络、接收背压和本地调度；不要据汇总p95直接定根因。
4. 是否认可warning在归还触发及checkout_ms实际为持有时长？下一步哪些结构写入阶段可以拆，哪些一致性校验/排序必须保留？
5. 是否认可OI已有seed与重放，低ready不等于漏检？如何核MarketState最新值摄入是否跳过事实端点；是否在发现真实遗漏后再用游标？
6. 是否认可先纠正容量诊断、保持普通300s/重点60s和现有2+2名额，暂不升频或放宽TTL？

**审核状态：待 GROK 回复。已按双方约定将此文档和问题写入固定评审往来文件；这不是已收到审核结果。**

## 11. 证据与复核入口

全部证据位于`D:/Axiangmu/erji/work/production-fix-20260929/src/docs/p40-validation/r4-investigation-20261010/`：

- `investigation.json`、`investigation-operations-api.json`及对应API文件：12:40有限采样、当前run、源码身份、PG错误、连接阶段、窗口及状态。
- `identity-and-input-evidence.json`：身份SQL、结果、可复查查询、纯内存复现与14文件哈希。初次分析引用统计SQL混用了JSON类型，已显式cast为jsonb并成功重查；该诊断错误不是生产故障，最终无查询盲区。
- `qualified-price-examples.json`：30样本的eligible筛选、3个合格币原始逐币空档；不能外推所有市场缺失比例。
- `current-run-log-increment.json`及`*-since-last-monitor.log`：自最后注册偏移至12:47的日志关闭/警告，未改监控偏移。
- `official-spot-stream-semantics.json`：官方changed-only说明。
- `websocket-library-semantics.json`：安装库16.1.1的max_queue默认值及pause机制；这是机制证据，不是生产pause发生证明。

历史对照在`r3-resume-20261010T012025-3d132d17/`下的`heartbeat-1152-assessment.json`、`heartbeat-1202-assessment.json`、`heartbeat-1212-assessment.json`、`heartbeat-1222-assessment.json`及关联样本，另含10:01全局关闭与10:22恢复记录。

限制：目前没有每个合格币的完整内存样本导出、单次62s占用阶段、接收pause/pong时间线或逐OI摄入回执；本报告没有将这些未知项写成已定位完毕。未确认新事件的浏览器买入送达。
