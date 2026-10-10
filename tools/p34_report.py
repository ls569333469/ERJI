"""Render the retained P34 evidence without claiming unavailable acceptance."""
from pathlib import Path
import json,html,re,datetime,xml.etree.ElementTree as ET

DEST=Path('docs/p34-validation')
def read(name):return json.loads((DEST/name).read_text(encoding='utf-8'))
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |',
                      *['| '+' | '.join(map(str,row))+' |' for row in rows]])

def run():
    data=read('acceptance.json');load=read('http-load.json');charts=read('real-chart-matrix.json')
    runtime=read('runtime-check.json');details=read('details-http.json');test=ET.parse(DEST/'tests.xml').getroot().find('testsuite')
    now=datetime.datetime.fromtimestamp(data['as_of']/1000,datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds')
    proxy=load.get('next_proxy',{});monitor=data['continuous_observation'];long=data['real_history']
    names={'box':'箱体','recent_high':'近期前高','ath':'合约ATH','retest':'突破后回踩','squeeze':'收缩突破',
        'double_bottom':'双底','early_strength':'多周期转强','trend_pullback':'趋势回踩','failed_breakdown':'假跌破收回','repeated_test':'多轮试探'}
    matrix=table(['入口','命中','等待','失效','缺数据'],[[names[k],*[('有留存观察' if v[s]['status']=='observed' else '无合格真实样本')
        for s in ('triggered','waiting','invalidated','missing_data')]] for k,v in data['real_matrix'].items()])
    coverage=table(['来源','适用市场','执行报价≤15s / 深度≤10s','数量OI≤6m','CMC身份 / 流通量','最新闭合1H / 4H / 1D / 1W'],[
        [v['venue'],v['markets'],f"{v.get('execution_quote_15s',0)} / {v.get('depth_10s',0)}",
         f"{v.get('oi_6m',0)} / {v['oi_applicable']}" if v.get('oi_applicable') else '需关联主合约',
         f"{v.get('identity_verified',0)} / {v.get('circulating_supply_current',0)}",
         ' / '.join(str(v.get('latest_'+tf,0)) for tf in ('60','240','1D','1W'))] for v in data['data_chain']['venues']])
    perf=table(['场景','行数','P50 ms','P95 ms','P99 ms'],[[name,r['samples'][0]['rows'],r['p50_ms'],r['p95_ms'],r['p99_ms']]
        for name,r in proxy.items() if isinstance(r,dict) and 'samples' in r])
    policies=table(['能力','计划最短间隔','背景允许年龄','参与用途'],[[k,str(v['refresh_ms']//60000)+'分钟',str(v['reference_max_age_ms']//60000)+'分钟','超过5分钟只作背景']
        for k,v in data['capability_usage_policies'].items()])
    status=table(['P34章节','本轮交付','验收状态'],[
        ['4.1 成功/失败溯源','最后成功与最后尝试分离；迁移；失败不刷新旧值时间或原始指针','回归通过；无法证明的旧指针清空并说明'],
        ['4.2 / 4.4 调度与预算','活跃计划优先；跨进程FIFO；付费参考配额节奏；缓存报价复用','代码通过；全市场新鲜率未达成，不能宣称全部数据已通'],
        ['4.3 OI/费率','单币及时入库；历史与结算费率独立队列；72H小时网格；26条费率；数量金额同窗及输入证据','真实小时网格已有留存；全市场覆盖仍待队列补齐'],
        ['4.5 / 4.6 身份和字段','链别名；Solana保留大小写；精确地址冲突阻断；原生OI/费率独立保留；未知源时间降级','大量CEX身份缺可审计对应证据；ByKaranteli密钥不可读'],
        ['4.7 逐层对账','运行页折叠覆盖表；逐资产字段/映射清单；每5分钟历史记录','可观测性已上线；空值原因不是采集完成'],
        ['5 历史连续性','复用既有引擎；新增低频4H上市历史分页游标和全区间断档检查；25万上限','真实页已入库；仍有断档；真实超过2万根样本未验收'],
        ['6 信号与追踪','取消无关周期全局阻断；OI/市值/费率及核验辅助身份变更刷新不可变快照；首次触发不改写','工程回归通过；40格观察与真实复盘见下，未全格闭环'],
        ['7 性能与持续运行','隔离真实字段300行的实际API及生产Next代理测试；正常采集并行','固定HTTP负载通过；自然非空列表、24H连续验收仍未完成'],
        ['8 验证与发布','重新运行五币因果识别和生命周期成本回放；核对200/30/90出处；九策略门槛保持关闭','18触发/36情景不满足有效性；未伪造审核报告或放行'],
        ['9 K线最后处理','主策略专属边界；箱体更清楚；触发/保护/目标标签和来源周期；保持原锚点','8种新增真实图+箱体通过；ATH真实样本及全部40格视觉验收仍缺']])
    text=f'''# P34 · 实施与验收记录

证据时间：{now}（北京时间）。实施目录：`D:/Axiangmu/erji/work/production-fix-20260929/src`。

**已完成本轮代码修复并在本地运行；P34 总体验收尚未全部通过。** 数据补齐、身份凭据、策略有效性和连续运行时长仍有实质缺口。下表逐项对应主方案，不把“已写代码”写成“所有数据可用”。

[打开本地机会页](http://127.0.0.1:18770/signals) · [运行记录](http://127.0.0.1:18770/operations) · [P34原方案](../../../../../docs/P34_ERJI_数据链路打通与P33未完成项专项完善方案.html)

## 1. 本轮结果与范围

{status}

保留A版长表、现有买入资格、原200/30/90门槛和原API总预算。未新增策略、复杂导航或手机版内容；未部署服务器、推送GitHub、发送通知或执行交易。

## 2. 实际覆盖与空页原因

{coverage}

分母是交易市场，不能相加称为去重代币。最新闭合柱入库不等于全部连续且完成指标预热。执行报价按同市场真实盘口ask或last检查，不将mark计入；深度是另一项10秒限制。

已记录的空页原因包括：候选结构有效期已过、当前闭合K线不足、原扣费后盈亏比不满足、新增策略发布门槛未通过。不会延长资格时间或降低阈值制造可买入数据。

逐资产身份依据、缺失原因、原生OI、费率、源时间及原始指针见 [acceptance.json](acceptance.json) 的 `asset_mapping_and_fields`。CMC当前可读密钥：{runtime['cmc']['configured_key_readable']}；ByKaranteli：{runtime['bykaranteli']['configured_key_readable']}。旧CMC missing_key记录不代表当前全局失效。

### 尚未打通的具体边界

- CEX未核验身份不能靠ticker补映射；需要官方对应关系或精确链/合约地址。历史迁移、包装和1000数量单位仍沿用原独立身份，不启发式去前缀。
- ByKaranteli未取得当前可读密钥，接口内容未验收；CMC部分能力返回unsupported，保留状态。
- Hyperliquid上下文没有可证明的源时间，保留原始数量/金额作观察，退出同窗实时OI聚合和定时历史端点；不会用接收时间造历史。
- 缺来源时间的REST盘口不进入可成交资格；真实逐笔/快照源时间与接收时间分开。缺一个字段不清空其余可用字段。
- 补历史仍在排队，最近4H收盘后全市场无法在现有配额内立刻补齐。本轮未承诺全市场30秒刷新。

## 3. 采集、预算和字段用途

币安family沿用300 weight/分钟、500ms最小间隔。持久队列按到期与管理需求选资产，共享请求层FIFO防止饥饿；活跃管理30秒、候选60秒、普通300秒是目标到期时间，不是完成SLA。未知源时间和未来源时间不能通过实时资格。

| 工作 | 本轮节奏 / 成本 | 预算或降级 |
| --- | --- | --- |
| 数量OI | 单币1 weight；20币批次各自写入；优先复用≤10秒WS标记价，必要时全市场mark请求10 weight | OI与价格偏差>60秒时保留数量，金额空；原始双输入留存 |
| 小时OI端点修复 | 每次最多2市场，完成后6H再复核；每请求按本地1 weight；窗口73点 | 缺格不确认完成，独立退避；不延迟实时OI |
| 结算费率 | 每次最多2市场，30分钟到期轮转并检查下个结算点；每次26条 | 1H/8H实际间隔分别计算，跨重启读已有FundingPoint |
| 收盘K线 | 每市场只补缺的已闭合周期；≤1000根5 weight，1500根10 weight | 保留真实缺口；不把未闭合改为闭合 |
| 冷历史 | 一页后至少90秒；最多1500根；最多25万根 | 复用原库和写入器，持久分页游标，全区间4H断档复查 |
| CMC身份 / 活跃估值 / 后台估值 | 6H / 30分钟 / 6H；每批≤100身份 | 同外部身份去重；缓存结果不重复登记成功时间；FX仍按原有效期 |
| 付费慢参考 | 当前剩余日额度45%分配参考，其余留估值/FX/身份/账户；额外credit顺延 | 每次请求核对实际credit，未提升总额度；超过实时年龄只作背景 |

容量反例：563个合约全按300秒采OI，理论已需112.6 weight/分钟；1887个市场只补每小时一根1H、每次5 weight，平均约157.3 weight/分钟。还未加4H/日周、目录、盘口、Alpha、重试和同IP其它进程，所以不能把所有目标周期宣称可同时保证。实际本地权重、上游reported weight、FIFO积压见 `request_budgets`，供应商剩余日额度见 `supplemental_credit_capacity`。

上表weight是现有本地保守计费，不冒充所有上游官方权重。重试同样计费。CMC credit以响应credit_count核销；账户使用量与本地账本取更保守值。45%为本轮调度分配，不是策略参数或新增付费额度。

{policies}

慢参考的计划期限会因额度/候选轮转顺延；不延长实时5分钟门槛。实时执行仍为报价15秒、深度10秒。P34尚未得到足够连续数据来承诺其它字段的新SLO，不能事后改门槛宣布通过。

## 4. 历史、修订和追踪验收

本轮Python回归：{test.attrib['tests']}项，失败{test.attrib['failures']}，错误{test.attrib['errors']}。TypeScript、生产Next隔离构建及两组前端指标测试通过。

重点回归覆盖：成功/失败/恢复证据链、未知时间、同窗金额空值、小时费率、快速市场先入库、首次触发冻结、辅助数据独立刷新、跨进程公平与额度上限、补历史游标不越过缺口；沿用并重跑P33的修订、超长分页、终态、断档恢复和案例无未来值测试。

当前最长真实样本：`{long['instrument_id']}`，周期`{long['timeframe']}`，{long['bars']}根；状态`{long['state']}`，完整复算：{long['full_sum_equal']}。输入哈希：`{long['source_sha256']}`。这是实际状态；正在补齐的历史有断档时不宣称完整。此前511根ETH 1H完整复算通过的中间取样不能代替真实超过20000根持续验收。

历史写入保留实际received_at；新补的历史不冒充当时已知数据。所有审计派生AVWAP记录在事务中回滚，不向生产添加测试事件。游标见 `history_cursors`，小时OI连续网格见 `oi_coverage`。

### 十入口40格留存观察矩阵

{matrix}

“有留存观察”仅表示找到了对应真实研究记录，**不是该格从输入到成本资格再到前端全部验收通过**；具体event/snapshot引用见acceptance。空格保留，不用合成数据填成真实通过。研究状态与正式参与始终分开。

五币归档重新识别18次触发，冻结计划与24H/72H共36个成本情景；这些记录存在missing_target、no_fill_in_entry_window、insufficient_net_r，不能作为200笔有效成交。详见 [识别回放](archive-replay.json)、[生命周期与成本](lifecycle-replay.json)。回放披露收盘即用假设、缺历史tick、事后选样和同柱顺序限制；不作为胜率证明。

### 发布门槛出处和统计定义

| 项目 | 出处 | 本轮处理 |
| --- | --- | --- |
| 200成熟情景 / 30资产 / 90天 | 当前ValidationConfig实现默认；未找到P33数字需求依据 | 数值未改；没有要求用户从今天再等90天 |
| mature_fills | 当前报告sample字段，现有gate未独立推导 | 必须绑定原始事件和实际可成交成本情景；同事件24H/72H不能当两个独立成交 |
| assets | 应按核验后的规范资产身份统计 | 现货/合约/多所同资产不重复凑样本 |
| days | 现有字段未区分历史跨度与前瞻跨度 | 验证报告分别列历史起止、实际前瞻起止，不能互相替代；协议口径未补齐前不放行 |
| 其它必备证据 | 原发布流程：因果、样本外、失败/下架、成本、基线、前瞻、图表、版本绑定 | 本轮未伪造审核人、approved_at或附件；9个门槛仍关闭 |

## 5. 性能和持续运行

{perf}

固定负载：300行真实字段克隆，身份与时间仅用于隔离压测；走实际FastAPI及单独生产Next代理。4并发20次，非空列表/翻页/筛选均通过P95≤2秒；正常后台采集同时运行。临时schema和测试进程均清理。此测试不修改运行中的开发构建。

真实运行页的另一次取样为空，因此 [live-http.json](live-http.json) 明确记为未通过非空验收，不能用其快速空响应证明真实满负载。真实历史快照另做4并发20次详情懒加载：快照P95 {details['snapshot']['p95_ms']}ms，技术分析{details['analysis']['p95_ms']}ms，合约历史{details['derivatives_history']['p95_ms']}ms，错误率0。见[详情HTTP证据](details-http.json)。该组为冻结历史详情；HTTP总时延已测，SQL分项未单独埋点计时。

持久覆盖观察目前{monitor['samples']}个采样，跨度{monitor['span_seconds']/3600:.2f}小时，最大间隔{monitor['max_gap_seconds']:.1f}秒；24小时完成：{monitor['complete']}。每5分钟仍由现有jobs运行，记录保存在数据库RawEvent(endpoint=p34_data_health)。本轮未实际跨越周界，不声称完成周界实时观察。

## 6. K线验收（最后处理）

已增强真实箱体边框、上下沿价格和突破确认线；保护红、目标橙、结构蓝，标明来源周期。箱体时间锚点不延长；1D仅为4H投影，1W尺度不足不画假框。默认不铺满其它未命中策略指标，远端日周目标仍主动展开。

真实新增策略截图覆盖：{', '.join(names.get(r['strategy_id'],r['strategy_id']) for r in charts['rows'])}。所有浏览器检查错误数：{len(charts['errors'])}。另有箱体1H/4H/1D坐标及1W不绘制检查。ATH缺合格完整上市历史的真实触发样本，仍未通过真实视觉验收。

![真实4H箱体及指标](verified-240.png)

![真实双底结构](real-double_bottom.png)

[真实图表矩阵](real-chart-matrix.json) · [箱体周期检查](browser.json)

## 7. 留档与回退

目录：`D:/Axiangmu/erji/backups/p34-implementation-20261002`。`before-changed.zip`为改动文件原版，`result.zip`为本轮代码，`changes.json`和`changes.patch`记录文件范围及哈希/差异。早期checkpoint是在第一批修改之后创建，不能称完整改前备份；storage、scheduler从旧归档恢复并与P34改前哈希匹配，models由旧归档核对仅差本轮四字段，已纳入原版包。未包含密钥。

回退顺序：停止本checkout登记的API/jobs/live/streams；先保存当前代码和数据库；仅按changes清单恢复原版/移除本轮新文件；再启动本地。保留其它未提交文件。不要把整个旧工程覆盖回来。

数据库已执行`p34_capability_provenance`（上一个版本`p33_native_tape_lookup`），新增四字段；迁移清空了无法证明对应payload的旧raw_ref并写原因。降级只移除新增字段，不能还原已清空指针；没有本轮迁移前数据库快照，因此不能宣称数据可完整无损回滚。优先保留兼容新增列进行代码回退。新历史、RawEvent和覆盖记录也不会因代码回退自动删除。

## 8. 仍未完成的验收项

1. 全市场实时字段和闭合历史连续覆盖、新鲜率SLO；身份对应证据与ByKaranteli密钥缺口。
2. 真实超过20000根完整历史及持续修订恢复；当前补齐任务继续在原预算内工作。
3. 十策略40格全部真实端到端验收；自然首次触发至结束的前瞻样本、ATH真实图表样本。
4. 自然非空列表持续性能、SQL分项、至少24小时及周界观察。
5. 每策略样本外、失败/下架、真实成本与基线/前瞻的充分有效性报告，及口径明确后的正式放行。

上述内容均有具体证据缺口，不能统一写成“代码全完成，只需等待行情”。本轮已修复和验证的内容见第一节；本文件是实际验收记录，不修改P34原要求来凑完成。
'''
    (DEST/'P34_实施与验收记录.md').write_text(text,encoding='utf-8')
    def inline(value):
        value=html.escape(value)
        value=re.sub(r'!\[([^]]*)\]\(([^)]+)\)',r'<img alt="\1" src="\2">',value)
        value=re.sub(r'\[([^]]*)\]\(([^)]+)\)',r'<a href="\2">\1</a>',value)
        value=re.sub(r'`([^`]+)`',r'<code>\1</code>',value)
        return re.sub(r'\*\*([^*]+)\*\*',r'<strong>\1</strong>',value)
    chunks=[];lines=text.splitlines();i=0
    while i<len(lines):
        line=lines[i]
        if line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].startswith('|'):rows.append(lines[i]);i+=1
            chunks.append('<div class="scroll"><table>')
            for n,row in enumerate(rows):
                if n==1:continue
                cell='th' if n==0 else 'td'
                chunks.append('<tr>'+''.join(f'<{cell}>{inline(v.strip())}</{cell}>' for v in row.strip('|').split('|'))+'</tr>')
            chunks.append('</table></div>');continue
        if line.startswith('#'):
            level=len(line)-len(line.lstrip('#'));chunks.append(f'<h{level}>{inline(line[level:].strip())}</h{level}>')
        elif line:chunks.append('<p>'+inline(line)+'</p>')
        i+=1
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>P34 · 实施与验收记录</title><style>
body{font:16px/1.8 "Microsoft YaHei",sans-serif;color:#18334a;background:#eef3f7;margin:0}main{max-width:1240px;margin:30px auto;padding:36px;background:#fff;border-radius:12px}h1{color:#114e78}h2{margin-top:40px;border-bottom:2px solid #dbe7ef}h3{margin-top:28px}a{color:#0866a5}code{font-size:13px;background:#edf2f6;overflow-wrap:anywhere}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:12px;border:1px solid #d8e3ec;text-align:left;vertical-align:top}th{background:#edf5fa}img{max-width:100%;border:1px solid #d8e3ec;border-radius:8px}strong{color:#a33b19}
</style><main>'''+''.join(chunks)+'</main></html>'
    (DEST/'P34_实施与验收记录.html').write_text(page,encoding='utf-8')
    print('Report written:',DEST/'P34_实施与验收记录.html')

if __name__=='__main__':run()
