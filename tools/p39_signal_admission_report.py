"""Record this fix separately from previous activation and monitoring records."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json, html, re, shutil, sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
OUT=ROOT/'docs/p39-validation'
TZ=timezone(timedelta(hours=8))
now=datetime.now(TZ).isoformat()
source=OUT/'risers-chain-audit-20261006-213741.json'
audit=json.loads(source.read_text(encoding='utf8'))
xml=ET.parse(OUT/'signal-admission-final-tests.xml').getroot()
suites=[xml] if xml.tag=='testsuite' else list(xml.iter('testsuite'))
checks={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
assert checks['failures']==checks['errors']==0
files=['engine/pd2/decision.py','engine/pd2/service.py','engine/pd2/signal_projection.py',
       'engine/pd2/portfolio.py','engine/strategies/p33_lifecycle.py','engine/strategies/p33_runtime.py',
       'engine/p37/store.py','engine/p37/worker.py','engine/p37/analysis.py','engine/p38/jobs.py',
       'tests/test_p25_signals.py','tests/test_p39_signal_admission.py']
fixes=[
 ('P39-S01','成本比值不再否决买入','execution_cost将成本判断记录为economic_status；原1.5/1.8仅作参考。缺费率不虚构零成本。decide依据原生盘口资格，不再把low_net_r标为不建议追入。','低比值、净收益为负的成本情景也不独立否决；过期、报价/盘口过期、区间外、保护触发仍撤回。'),
 ('P39-S02','目标和阻力估算不再充当额外入场审批','P33资格、旧计划初次判断与快复核统一取消target_missing、entry_obstructed的独立硬过滤。目标、原阻力、保护、入场区间仍保留。','ATH没有上方目标时不因缺目标暂停。无目标的仓位金额估算返回不可用，不中断信号发布。真实触发和结构依赖仍须有效。'),
 ('P39-S03','等待候选不再被1.8过滤掉','投影中的初步比值保留，但不再决定关联计划是否可见；目标缺失也不隐藏有有效参考入场/保护的等待候选。','低比值或缺目标仍是waiting，不能被提升为现在可以买。'),
 ('P39-S04','过期保护计划释放新入场优先名额','观察跟踪区分entry_open和protection_only；最新快照ended优先于尚未同步的Episode phase。','过期计划继续保护跟踪，trade_status不再active；新观察可获得名额。原计划、快照和业务数据不删除。'),
 ('P39-S05','结构生成先于附属趋势计算','deep lane在同等就绪条件下先消费structure再消费trend；保护/入场/投影独立lane不变。','先完成策略结构，再完成趋势任务；不把任务完成等同市场合格。实际生产队列p95和持续公平性待激活后核验。'),
 ('P39-S06','30分钟聚合窗口不再长期差一根','统一5m读取、补窗、输入版本及提交栅栏窗口为1806根，确保任一5分钟偏移可获得300根完整30m。','六种边界偏移及真实缺口回归通过。30m来自5m聚合；不把不完整桶当闭合K线。'),
 ('P39-S07','新政策使旧缓存重新判断','任务依赖版本及P33输入签名包含reference-only-v1。保留冻结事件身份与原配置、历史快照。','激活后按新政策重新复核现存有效计划，不用删除缓存/生产记录，不回填历史买入。'),
]
notes={
 'RLC':'合约正式结构记录仍旧，无当前计划；该合约未入选深度名额。不能据此断言同币现货也未分析。',
 'BR':'正式结构完成曾等待约23分钟；当前复算未触发，旧前高规则与短期反弹并非同一条件。',
 'CAP':'策略已正式触发；1H触发收盘价0.09327已超过原入场区间0.08044–0.08271。任务另等待约19分钟，但不能把全部错过归因于排队。区间/闭合触发的匹配属于后续策略讨论，本次不强行追涨。',
 'NMR':'当前闭合本地数据复算recent_high触发；正式结构仍旧，无新计划，该合约当时未入选。当前价格也已超过复算区间上沿，不能补造历史买入。',
 'ORCA':'当前复算未触发；正式结构记录较旧，曾因退出名额退役。',
 'US':'当前复算未触发；正式结构记录较旧，曾因退出名额退役。',
 'API3':'正式结构已更新，任务等待约20.6分钟；当前复算未触发，只有已失效旧计划。',
 'C98':'当前闭合本地数据复算recent_high触发，正式结构排队，无新计划。当前价格略高于复算区间，不能直接认定现在可以买。',
 'EDU':'当前复算未触发；本轮仅见过期计划，不能把旧保护跟踪算成新入场。',
 'ZRO':'正式early_strength触发，最新快照ask2.1837在2.17546–2.22463内；记录的唯一拒绝码execution_not_eligible，原因low_net_r=0.38867。是明确成本门槛否决案例。',
 'VTHO':'合约正式结构较旧，当前复算未触发，无新计划。',
 'RAYSOL':'合约正式结构较旧，当前复算未触发，无新计划。',
}
def stamp(ms):
 return datetime.fromtimestamp(ms/1000,TZ).strftime('%m-%d %H:%M:%S') if ms else '无'
rows=[]
for r in audit['rows']:
 market=r['market'][0] if r['market'] else {}
 formal=r['p33'][0]['updated_at'] if r['p33'] else None
 rows.append(dict(symbol=r['symbol'],screenshot_pct=r['screenshot_24h_pct'],
                  checked_price=market.get('payload',{}).get('price'),formal_updated_bjt=stamp(formal),
                  deep_slot=r['selected_for_deep'],buy_snapshots_24h=len(r['first_buy_24h']),note=notes[r['symbol']]))
name='P39_信号进入链路修复与验收记录'
report=OUT/(name+'.html')
status={'updated_at_bjt':now,'status':'code_complete_verified_pending_activation','backend_activated':False,
        'runtime_recovery_verified':False,'monitor_status':'PAUSED','tests':checks,
        'test_isolation':json.loads((OUT/'signal-admission-final-test-isolation.json').read_text()),
        'audit_evidence':source.name,'audit_as_of_bjt':audit['checked_at_bjt'],
        'cost_policy':'reference-only-v1','upside_time_estimation_changes':False,
        'activation_required':['api','worker-live','worker-jobs','worker-streams'],
        'report_path':str(report),'report_url':'http://127.0.0.1:18770/previews/P39_signal_admission_fix.html',
        'items':[{'id':f[0],'title':f[1],'change':f[2],'acceptance':f[3]} for f in fixes],
        'file_sha256':{p:sha256((ROOT/p).read_bytes()).hexdigest() for p in files},'risers':rows}
(OUT/'signal-admission-fix-status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf8')
md=[f'# P39 信号进入链路修复与验收记录\n\n更新时间：{now}',
    f'本批代码完成，完整隔离回归{checks["tests"]}项通过；后台未激活，不能宣称生产买入已恢复。定时监控保持PAUSED。',
    '用户本次要求先解决信号进入问题，上涨空间/预计时间/盈亏比算法另行讨论。本次成本政策沿用用户明确选择“改为参考，不作为买入硬门槛”。',
    '买入主条件：对应策略真实触发、当前报价在原入场区间、未到期、结构与依赖数据有效、原生报价/盘口可用。成本与目标估算仅作参考；不因目标缺失额外审批。OI/量能和各模板触发要求保留，不改成涨幅自动买入。',
    '## 修复与验收']
for key,title,change,acceptance in fixes:md.append(f'### {key} {title}\n\n{change}\n\n验收：{acceptance}')
md.extend(['## 逐币真实证据',f'只读取本地数据库和闭合K线。采样时间：{audit["checked_at_bjt"]}。截图涨幅是图片历史值，不是采样行情。',
           '| 合约 | 截图24H涨幅 | 正式结构更新时间（北京时间） | 原因 |','|---|---:|---|---|'])
for r in rows:md.append(f'| {r["symbol"]} | {r["screenshot_pct"]}% | {r["formal_updated_bjt"]} | {r["note"]} |')
md.extend(['这12个合约在采样前24小时均未查到trial/participate快照。此结论仅限本地记录和该时间窗，不代表全部市场或历史上从未有信号。',
 '最新复算只用于识别，不写计划或信号；未传完整ATH覆盖证明/父事件上下文，不能凭复算结果证明所有ATH或回踩模板缺数据。',
 '## 当前限制与激活后验收',
 '1. 当前API/live/jobs/streams仍是上一批运行代码。本批必须重启加载后才能核验实际推荐。旧激活status保留，不改写为本批已激活。',
 '2. 激活后按新鲜输入验证有效策略→计划→trial/participate快照→CurrentSignal→买入API，记录每段时刻及拒绝原因；没有新合格触发不能宣称实盘信号恢复。',
 '3. 核对旧保护计划不再占entry优先、结构队列年龄下降、30m矩阵完整。隔离优先级回归不能保证生产延迟/p95。',
 '4. CAP触发收盘已超区间，需要另行讨论策略入场定义；本轮保留区间约束，不把截图上涨当成现在可以买。历史收到时间、闭合因果和真实缺口检查仍保留。',
 '5. 固定盘口测试金额、价差上限仍为现有执行配置，本次未改；属于之后可单独审查的容量与流动性条件。',
 '6. 原详情与K线布局、上涨空间计算与时间预测未更新。历史回放保留其原固定成本研究版本，未作为在线发布门槛；本次未改写旧回测。',
 f'\n证据：{source.name}、signal-admission-final-tests.xml、signal-admission-final-test-isolation.json、signal-admission-fix-status.json。\n修改前备份：legacy-cleanup-before-20261006/。'])
(OUT/(name+'.md')).write_text('\n\n'.join(md),encoding='utf8')
esc=html.escape
fix_html=''.join(f'<article><h3>{esc(k)} · {esc(t)}</h3><p>{esc(c)}</p><p class="muted">验收：{esc(a)}</p></article>' for k,t,c,a in fixes)
table=''.join(f'<tr><th>{esc(r["symbol"])}</th><td>{esc(r["screenshot_pct"])}%</td><td>{esc(r["formal_updated_bjt"])}</td><td>{esc(r["note"])}</td></tr>' for r in rows)
page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{name}</title>
<style>body{{margin:0;background:#f4f6fa;color:#19283f;font:15px/1.8 "Microsoft YaHei",sans-serif}}main{{max-width:1180px;margin:auto;padding:32px}}h1{{font-size:28px}}h2{{margin-top:32px}}article,.status{{background:white;padding:18px 24px;border:1px solid #d8e1ec;border-radius:10px;margin:12px 0}}.status{{border-left:6px solid #e2a32d}}.muted{{color:#596b82}}table{{border-collapse:collapse;background:white;width:100%;font-size:14px}}td,th{{padding:12px;text-align:left;border-bottom:1px solid #dde5ed;vertical-align:top}}thead{{background:#e8eef7}}.scroll{{overflow:auto}}a{{color:#2362b8}}code{{overflow-wrap:anywhere}}</style>
<main><h1>P39 · 信号进入链路修复</h1><p class="muted">{esc(now)} · 先打通推荐，上涨空间与预计时间后续讨论</p>
<div class="status"><b>代码完成 · {checks['tests']}项隔离回归通过 · 后台待激活</b><p>这批代码尚未重启加载，不能把测试通过写成生产买入已恢复。定时监控保持暂停。</p></div>
<h2>买入资格与参考估算分开</h2><p>对应策略触发 → 价格在原买入区间 → 信号及依赖数据有效 → 原生报价/盘口可用 → 现在可以买。成本与目标估算不再额外否决。未触发的关联计划仍等待，不会被提升为买入。</p>
<h2>本次修复</h2>{fix_html}<h2>逐币证据</h2><p>本地采样：{esc(audit['checked_at_bjt'])}。截图涨幅仅是历史图片值。这12个合约采样前24小时均未查到可买快照。</p>
<div class="scroll"><table><thead><tr><th>合约</th><th>截图涨幅</th><th>正式结构更新</th><th>实际原因与边界</th></tr></thead><tbody>{table}</tbody></table></div>
<h2>尚待运行核验</h2><p>激活后逐段核对真实触发、计划、快照、投影、买入API；核验结构队列年龄、旧计划名额释放和30m完整性。当前规则保留超区间、过期、结构失效、报价过期和数据缺口限制。CAP触发收盘已超区间需后续讨论，不据截图补造买入。</p>
<p>固定盘口测试金额与价差配置未改；原详情、K线布局及上涨空间/预计时间算法未改。历史研究回放不作为在线发布门槛。</p>
<p>原始证据和备份保留在 <code>docs/p39-validation</code>。<a href="P39_plan_latest.html#p39-signal-admission-fix">返回P39最新方案</a></p></main></html>'''
report.write_text(page,encoding='utf8')
public=ROOT/'apps/web/public/previews'
(public/'P39_signal_admission_fix.html').write_text(page,encoding='utf8')
section=f'''<!-- P39-SIGNAL-ADMISSION-FIX --><section id="p39-signal-admission-fix" style="padding:24px;background:#fff8e8;border:2px solid #dea73a;margin:24px"><h2>最新追加：信号进入链路修复 · 后台待激活</h2><p>{esc(now)}。成本比值、目标缺失及额外阻力估算不再独立否决合格策略；清理候选1.8过滤、过期保护计划入场优先名额，结构任务先于趋势计算，30m源窗改为1806根5m，版本签名使旧政策缓存重新复核。</p><p>{checks['tests']}项完整隔离回归通过，覆盖真实P33识别/资格/发布/投影/买入API；生产尚未加载本批代码，持续延迟和真实买入恢复未验。保留原策略触发、买入区间、有效期、结构失效和原生盘口检查；上涨空间、时间预测后续讨论。</p><p>21:37本地证据：ZRO在原参与区内，仅因成本比值0.389拒绝；CAP触发收盘已超区间，C98/NMR复算触发而正式记录未同步，需分别看待。<a href="http://127.0.0.1:18770/previews/P39_signal_admission_fix.html">打开本批修复与逐币验收记录</a></p></section><!-- /P39-SIGNAL-ADMISSION-FIX -->'''
plans=[Path('D:/Axiangmu/erji/docs/P39_plan_latest.html'),Path('D:/Axiangmu/erji/docs/P39_ERJI_主链路收敛与数据可靠性整改方案.html'),public/'P39_plan_latest.html']
backup=OUT/'legacy-cleanup-before-20261006/plan-docs';backup.mkdir(parents=True,exist_ok=True)
for index,p in enumerate(plans):
 if not p.exists():continue
 old=p.read_text(encoding='utf8')
 before=backup/(str(index)+'-'+p.name)
 if not before.exists():shutil.copy2(p,before)
 old=re.sub(r'<!-- P39-SIGNAL-ADMISSION-FIX -->.*?<!-- /P39-SIGNAL-ADMISSION-FIX -->','',old,flags=re.S)
 # Place the current state first; all older activation records remain history.
 updated=re.sub(r'(<main[^>]*>)',lambda m:m.group(0)+section,old,count=1)
 p.write_text(updated,encoding='utf8')
planmd=Path('D:/Axiangmu/erji/docs/P39_ERJI_主链路收敛与数据可靠性整改方案.md')
if planmd.exists():
 before=backup/planmd.name
 if not before.exists():shutil.copy2(planmd,before)
 old=planmd.read_text(encoding='utf8')
 marker='<!-- P39-SIGNAL-ADMISSION-FIX -->'
 if marker not in old:planmd.write_text(old+'\n\n'+marker+'\n\n'+'\n\n'.join(md[:4])+f'\n\n完整记录：{report}\n<!-- /P39-SIGNAL-ADMISSION-FIX -->\n',encoding='utf8')
print(json.dumps({'report':str(report),'url':status['report_url'],'tests':checks,'status':status['status']},ensure_ascii=False))
