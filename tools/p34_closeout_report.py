"""Consolidated handoff; preserve earlier reports and explicit unmet acceptance."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json,html,xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1];DEST=ROOT/'docs/p34-validation'
NAME='P34_本轮统一收尾记录_2026-10-03'

def load(path):return json.loads((DEST/path).read_text(encoding='utf8'))
def table(headers,rows):
    return '| '+' | '.join(headers)+' |\n| '+' | '.join('---' for _ in headers)+' |\n'+ '\n'.join('| '+' | '.join(map(str,r))+' |' for r in rows)

def render(md):
    import re
    def inline(value):
        value=html.escape(value)
        value=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',r'<a href="\2">\1</a>',value)
        value=re.sub(r'\*\*(.+?)\*\*',r'<strong>\1</strong>',value)
        return re.sub(r'`([^`]+)`',r'<code>\1</code>',value)
    lines=md.splitlines();out=[];i=0
    while i<len(lines):
        line=lines[i]
        if line.startswith('|'):
            group=[]
            while i<len(lines) and lines[i].startswith('|'):group.append(lines[i]);i+=1
            out.append('<div class="scroll"><table>')
            for n,row in enumerate(group):
                if n==1:continue
                tag='th' if n==0 else 'td';out.append('<tr>'+''.join(f'<{tag}>{inline(c.strip())}</{tag}>' for c in row.strip('|').split('|'))+'</tr>')
            out.append('</table></div>');continue
        if line.startswith('#'):
            level=len(line)-len(line.lstrip('#'));out.append(f'<h{level}>'+inline(line[level:].strip())+f'</h{level}>')
        elif line.startswith('!['):
            match=re.fullmatch(r'!\[([^]]*)\]\(([^)]+)\)',line)
            if match:out.append(f'<img alt="{html.escape(match[1])}" src="{html.escape(match[2])}">')
        elif line:out.append('<p>'+inline(line)+'</p>')
        i+=1
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>P34 本轮统一收尾记录</title><style>body{font:16px/1.8 "Microsoft YaHei",sans-serif;color:#19334a;background:#eff4f8;margin:0}main{max-width:1160px;margin:32px auto;padding:36px;background:white;border-radius:12px}h1,h2{color:#12567c}h2{border-bottom:1px solid #d7e1eb;margin-top:32px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #d8e3ee;padding:10px;text-align:left;vertical-align:top}th{background:#eef5fc}a{color:#166cc0}img{max-width:100%}code{overflow-wrap:anywhere;background:#f1f4f7}strong{color:#824216}.scroll{overflow:auto}</style><main>'+''.join(out)+'</main></html>'

def run():
    audit=load('closeout-audit.json');browser=load('closeout-browser.json');acceptance=load('acceptance.json')
    archive=load('official-long-history/acceptance.json');studies=load('study-export.json');charts=load('real-chart-matrix.json')
    suites=ET.parse(DEST/'closeout-tests.xml').getroot();tests=sum(int(s.get('tests','0')) for s in suites.findall('testsuite'))
    observed=sum(v['status']=='observed' for row in acceptance['real_matrix'].values() for v in row.values())
    date=datetime.fromtimestamp(audit['as_of']/1000,timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S 北京时间')
    sql=audit['sql_route_profile']['samples'];history=acceptance['continuous_observation']
    md=f'''# P34 · 本轮统一收尾记录

{date} · 本地 ERJI · 延续原 A 版，不新增策略范围

**本轮已完成下表中的代码修复、验证和回退留档。本地服务已更新运行；P34 总体验收仍有明确缺口，见最后一节。** 本文件统一替代此前分散的进度说明；原始方案的验收要求保持不变。

## 1. 已完成的代码修复

{table(['问题','本轮处理','结果与边界'],[
['盘口接收积压','完整 top20 快照按市场保留最新帧，接收与批量落库分开；逐笔成交/清算不使用覆盖丢弃机制',f"最新取样：币安盘口 {audit['books']['fresh_10s']} 个满足10秒时限；源时间、接收时间和序号保留"],
['现货报价与逐笔流互相拖慢','现货全市场 miniTicker 与各市场成交/K线分开连接','仍验证每条报价的真实来源时间，不以接收时间替代'],
['OI历史与消费窗口不匹配','72小时1H播种 + 5分钟网格端点，按候选/普通市场到期调度；同集合原生数量历史回填到展示链路','每次轮到仍只发一个历史请求；缺历史外汇不补造美元涨幅'],
['费率结算间隔错误','保留毫秒源时间，规范秒级间隔，允许1秒内时间抖动；不靠上一结算间隔猜测下一次更新','4H不再误记14399秒；真实断档、未知区间和过期尾部仍阻断'],
['旧行情覆盖新证据','新价格连同原始证据指针、时间保留；OI、费率各用自己的时钟','避免新价格指向旧 REST 响应'],
['历史补数卡住','每页释放调度位置；缺口保留并指数退避；增加独立1H长历史游标，与4H轮转','不跳过缺口伪称完整 ATH，不提高共享 API 预算'],
['无用重复请求','旧暂停/过期记录仅在有活动计划时保留快速管理；不支持源时间的盘口采用有界重试','未提高交易资格时限或降低买入条件'],
['发布报告计数不可信','从留存明细核对独立事件、规范资产、历史天数/前瞻天数；核验源附件哈希和事前冻结协议','24H/72H不重复计数；9个策略发布门槛继续按原条件审核'],
['图表刷新改变EMA值','实时绘图统一读取后端分页指标序列；分析摘要刷新不再覆盖指标预热起点；历史快照仍用冻结输入',f"自动刷新前后 {studies['checked_values']} 个 EMA/MACD 数值逐点一致"]])}

## 2. 真实验证结果

{table(['检查','证据'],[
['后端回归',f'{tests} 项全量测试通过；最后现货通道拆分后另有15项定向回归通过'],
['前端','TypeScript 和生产构建通过；后端指标 JS 检查通过'],
['真实机会页',f"{browser['visible_rows']} 行；4并发20次请求，P95 {browser['p95_ms']:.2f}ms；页面错误 {len(browser['errors'])} 个"],
['API / SQL分项',f"10次同一路由、真实非空数据；SQL平均 {sum(r['sql_ms'] for r in sql)/len(sql):.2f}ms、每次 {sql[0]['sql_count']} 条；此项是隔离诊断进程，HTTP与Next代理时延以浏览器取样为准"],
['首次触发后追踪',f"{audit['frozen_event_audit']['first_events']} 个真实首报；仍保留的 {audit['frozen_event_audit']['retained_active_events']} 个活动事件原始信息/原计划一致，差异 {len(audit['frozen_event_audit']['mismatches'])} 项"],
['超长真实历史',f"官方28份归档，{archive['bars']} 根连续BTCUSDT 1H；每份ZIP均核对官方SHA256；21页AVWAP与全量复算一致；跨会话续算通过"],
['长历史修订恢复','在隔离库对真实输入注入早期修订，再恢复原值；三代重建、旧快照不变、恢复后值一致。注入故障不是上游实际修订'],
['图表',f"{len(charts['rows'])} 种真实新策略图表 + 箱体通过；箱体1H/4H/1D坐标与源锚点对应，1W不强画无法表示的框；ATH真实触发图仍缺样本"]])}

**超长历史验收已经不再只有合成数据。** 官方归档范围为 2023-01-01 至 2025-05-01（UTC，末端不含），是事后下载的真实历史，不冒充当时已知信号。校验、分页、AVWAP和受控修订均在隔离数据库进行，测试库已清理。生产历史仍按原预算补齐，不能把该验收结果说成所有资产历史都已补齐。

[全量测试](closeout-tests.xml) · [真实页面和耗时](closeout-browser.json) · [追踪与SQL核对](closeout-audit.json) · [官方历史来源与验收](official-long-history/acceptance.json) · [指标逐点对照](study-export.json) · [图表矩阵](real-chart-matrix.json)

## 3. 调度与口径

{table(['任务','到期目标 / 上限','消费限制'],[
['原生实时 OI','原30/60/300秒分级，批次20；实际受共享预算与积压影响','≤6分钟及各策略同窗要求不变'],
['OI历史','候选5分钟、普通6小时；批次2；6小时内轮换小时播种和5分钟端点','72小时覆盖与24小时滚动端点分别记录，不以播种成功代表永远完整'],
['资金费率','候选2分钟、普通30分钟；批次2；与OI历史任务独立','历史尾部实时消费仍要求3分钟内证明；普通市场可降级背景参考'],
['完整盘口 WS','最多每市场每秒持久化，接收端合并完整快照','10秒时限不变；过期数据不因排队后落库而变新'],
['历史K线','每次最多1500根；既有90秒轮转；缺口最长24小时退避','不增加REST配额；4H与1H游标分别保存'],
['官方历史归档验证','固定28个月；56次静态文件/校验下载，单并发并间隔至少500ms','不访问账户/交易接口，不占用实时REST数据做批量回放'],
['已有共享预算','币安family 300 weight/分钟，最小间隔500ms；418/429及上游权重反馈退避保留','这些是本地预算，不宣称是平台官方最高额度']])}

## 4. 当前仍未满足的总体验收

{table(['未完成项','当前事实','完成条件'],[
['全资产数据就绪率','下表仍存在OI过期、闭合柱待补和身份未核验；不能写全部打通','后台补齐后达到原新鲜度SLO；未映射资产获得可审计身份与流通量证据'],
['ByKaranteli补充能力','实际检查为未配置密钥；CMC密钥可读取','有效密钥/订阅并取得符合时间与身份要求的数据'],
['自然长期观察',f"已留存 {history['samples']} 个样本，跨度 {history['span_seconds']/3600:.2f} 小时；最大间隔 {history['max_gap_seconds']:.1f}秒",'完成至少24小时及周界观察；运行记录持续写入现有RawEvent'],
['策略充分验收及放行',f"10策略×4状态当前只有 {observed}/40 格找到真实样本；9种新策略没有充分审核报告",'补足缺失真实场景、ATH完整上市历史/触发、样本外/失败下架/成本/基线及前瞻证据；协议明确天数口径并审核'],
['真实持仓全生命周期','已有真实首报和后续追踪，尚不能把未成交/未成熟/缺入场历史算作完整成交案例','自然首次触发至终态完整证据；不能用受控测试冒充实际持仓记录']])}

当前候选不满足买入条件时仍显示等待。没有为了让页面出现买入信号而降低条件、补造市值或启用未经验证的策略。

### 最新全范围取样（市场数量，不是独立代币数）

{table(['来源','市场数','OI≤6分钟','核验身份/流通量','最近1H / 4H闭合柱'],[
[v['venue'],v['markets'],v.get('oi_6m','不适用'),str(v['identity_verified'])+' / '+str(v['circulating_supply_current']),str(v['latest_60'])+' / '+str(v['latest_240'])] for v in acceptance['data_chain']['venues']])}

[完整字段覆盖与缺项](acceptance.json) · [密钥引用状态，不含密钥](key-reference-status.json)

## 5. 留档与回退

代码备份：`D:/Axiangmu/erji/backups/p34-closeout-20261003-003905`。`before.zip`保留本轮修改前代码，`result.zip`为本轮修改后文件，`changes.json`/`changes.patch`给出精确范围，`evidence.zip`保存本次验收。

本轮没有新增数据库结构迁移。回退时按changes清单恢复旧文件、移除本轮新增文件，再重启本地登记服务；不要整目录覆盖其它已有工作。已采集的真实历史、RawEvent、游标不会因代码回退自动删除。未部署服务器、未推送GitHub、未发送通知、未启用自动交易。

本地入口：[买入机会](http://127.0.0.1:18770/signals)

## 6. 当前真实图表

以下为用户主动开启EMA/MACD后的真实图，默认仍只画命中策略要求的图层。数值核对包含自动刷新前后两个阶段。

![真实指标、箱体与成交量](study-loaded.png)
'''
    (DEST/(NAME+'.md')).write_text(md,encoding='utf8');(DEST/(NAME+'.html')).write_text(render(md),encoding='utf8')
    original=DEST/'P34_实施与验收记录.md';value=original.read_text(encoding='utf8')
    marker='## 最新统一收尾记录（2026-10-03）'
    if marker not in value:
        parts=value.split('\n',1);original.write_text(parts[0]+'\n\n'+marker+'\n\n本轮修复、真实2万根历史和指标对照见 ['+NAME+']('+NAME+'.html)。下文保留较早实施记录，以新记录判断当前状态。\n\n'+parts[1],encoding='utf8')
        page=DEST/'P34_实施与验收记录.html';value=page.read_text(encoding='utf8')
        value=value.replace('<main>','<main><p><strong>最新统一收尾：</strong><a href="'+NAME+'.html">本轮修复与验收记录（2026-10-03）</a>。下文为较早记录。</p>',1);page.write_text(value,encoding='utf8')
    print(DEST/(NAME+'.html'))

if __name__=='__main__':run()
