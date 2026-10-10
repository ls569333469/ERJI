"""Persist current activation evidence without changing business data/monitoring."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json,html,re

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/p39-validation'
paths=sorted(OUT.glob('signal-admission-activation-sample-*.json'))
s=json.loads(paths[-1].read_text('utf8'));run=s['run_id']
assert s['startup']['status']=='ready'
assert all(s['ports'].values()) and all(s['manifest_matches'].values())
assert all(v.get('status')==200 for v in s['http'].values())
assert s['monitor_status']=='PAUSED'
evidence=[p for p in paths if json.loads(p.read_text('utf8'))['run_id']==run]
buys=s['queries']['buys_new'];now=datetime.now(timezone(timedelta(hours=8))).isoformat()
startup_matches=[]
for log in s['logs']:
 for line in log['startup_lines']:
  if 'startup {' not in line:continue
  manifest=json.loads(line.split('startup ',1)[1])
  expected=s['manifest_matches']
  startup_matches.append({'role':log['name'],'run_id_match':manifest['run_id']==run,
    'jobs_hash_match':manifest['source_hashes']['engine/p38/jobs.py']==json.loads((OUT/'signal-admission-fix-status.json').read_text('utf8'))['file_sha256']['engine/p38/jobs.py']})
assert len(startup_matches)==3 and all(x['run_id_match'] and x['jobs_hash_match'] for x in startup_matches)
api=s['http']['http://127.0.0.1:18771/v1/signals?view=buy&page_size=25']['body']
total=api['pagination']['total']
detail=json.loads((OUT/'signal-admission-activation-render-ar.json').read_text('utf8'))
projected=[v for v in detail['current'] if v['action']=='participate']
intervals=[{'instrument_id':v['instrument_id'],'computed_at':v['computed_at'],'valid_until':v['valid_until'],
            'remaining_ms_at_projection_timestamp':v['valid_until']-v['computed_at']} for v in projected]
record={'verified_at_bjt':now,'run_id':run,'activated_at_bjt':datetime.fromtimestamp(s['run_start_ms']/1000,timezone(timedelta(hours=8))).isoformat(),
        'status':'activated_runtime_acceptance_partial','backend_activated':True,'monitor_status':'PAUSED',
        'ports':s['ports'],'http_all_200':True,'manifest_all_match':True,'startup_manifest_checks':startup_matches,
        'observed_buy_decisions':[{'instrument_id':b['instrument_id'],'snapshot_id':b['id'],'as_of':b['as_of'],'cost_policy':b['execution'].get('cost_policy')} for b in buys],
        'buy_projection_observed':intervals,'current_buy_api_total':total,'production_full_recovery_verified':False,
        'evidence':[p.name for p in evidence]+['signal-admission-activation-render-ar.json','signal-admission-activation-buy-api.json'],
        'limits':['买入API各次采样为0；仅证明生成并写入投影，不证明前端稳定可见。',
                  'AR/RENDER投影timestamp距盘口截止仅4433/705ms；数据库提交和HTTP可见时间不能由timestamp直接证明。',
                  '新批次数据库连接仍长占用10.1–22.9秒，入场和保护/独立投影仍排队。',
                  'structure优先策略本批尚未完成trend任务，30m运行恢复及趋势公平性未验证。',
                  '5/15分钟异动窗口重启后暖机，长期采集稳定性未验。']}
rp=OUT/('signal-admission-activation-'+run+'.json')
rp.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
sp=OUT/'signal-admission-fix-status.json';state=json.loads(sp.read_text('utf8'))
state.update(updated_at_bjt=now,status=record['status'],backend_activated=True,activated_run_id=run,
             activated_at_bjt=record['activated_at_bjt'],activation_record=rp.name,
             runtime_recovery_verified=False,buy_decision_production_verified=bool(buys),buy_projection_production_verified=bool(projected),
             buy_api_current_total=total,monitor_status='PAUSED',runtime_limits=record['limits'])
sp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf8')
name='P39_信号进入修复激活与运行核验'
md=f'''# P39 信号进入修复激活与运行核验

激活时间：{record['activated_at_bjt']}。核验时间：{now}。新批次：{run}。

API、live、jobs、streams已重启就绪。本批450项隔离回归的12项文件指纹匹配；3个worker的启动记录和jobs指纹匹配。四端口18770/18771/18772/55432可连接，本地健康、买入/关联计划API和前端页面HTTP200。定时监控仍PAUSED。

## 实际运行结果

新批次已生成AR与RENDER的participate决策，cost_policy=reference-only-v1，经济估算below_reference不再拦买入。AR快照成本比值0.01157，RENDER为0.28215，均成功生成可买判断并写入CurrentSignal。

观察记录实际区分entry_open（24条）和protection_only（202条）；后者trade_status=no_entry，不再把全部旧保护计划作为新入场优先。条数是观察记录，不等于独立资产或新增信号。

## 尚未证明完整恢复

最后买入API采样仍为{total}条；四次额外22:57:10–22:57:23采样也为0。因此本次证明代码激活、真实推荐生成和投影写入，不能宣称前端稳定看得到买入。

投影记录显示AR computed_at→valid_until剩4433ms，RENDER仅705ms；这是数据库记录的逻辑时间差，不是已证明的外部可见时长。后续RENDER曾超出原区间，AR也有同币其它no_chase计划。行情变化和决策过期会撤回推荐，但复核间隔和发布延迟仍需继续定位。

当前批次新日志未见Traceback、429/418或TimeoutError，但jobs有14.3/22.9/16.5/17.6秒数据库连接长占用，streams有14.7/10.1秒。入场、保护、独立投影仍排队，统计中的20分钟p95含旧批次最近完成记录，不当作本run纯增量。structure先运行，尚无本run趋势完成；30m补窗运行恢复及趋势公平性未验。

不延长过期盘口以制造可买，不写历史买入，不把上涨截图当入场资格。上涨空间/预计时间的算法留待讨论。本次核验只读本地数据库、日志及少量HTTP，不额外批量调用交易所。

原始证据：{', '.join(record['evidence'])}。激活记录：{rp.name}。此前激活记录保留。
'''
(OUT/(name+'.md')).write_text(md,encoding='utf8')
parts=[]
for p in md.split('\n\n'):
 if p.startswith('# '):parts.append('<h1>'+html.escape(p[2:])+'</h1>')
 elif p.startswith('## '):parts.append('<h2>'+html.escape(p[3:])+'</h2>')
 else:parts.append('<p>'+html.escape(p)+'</p>')
page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P39信号修复激活核验</title><style>body{font:15px/1.8 "Microsoft YaHei",system-ui;background:#f4f6fa;color:#1d304a}main{max-width:1050px;margin:24px auto;padding:28px;background:white}p{overflow-wrap:anywhere}h2{font-size:20px}</style><main>'+''.join(parts)+'</main></html>'
(OUT/(name+'.html')).write_text(page,encoding='utf8')
public=ROOT/'apps/web/public/previews';(public/'P39_signal_admission_activation.html').write_text(page,encoding='utf8')
section=f'''<!-- P39-SIGNAL-ACTIVATION --><section id="p39-signal-admission-activation" style="background:#edf8f1;border:2px solid #559878;padding:24px;margin:24px"><h2>最新状态：本批已激活 · 运行验收仍有未恢复项</h2><p>{html.escape(record['activated_at_bjt'])}，新批次{run}。API/live/jobs/streams已加载已验收代码。实际已生成AR/RENDER可买决策并写投影，费用比值不再否决。</p><p>最后买入API采样仍0；投影到期前剩余时间短，当前run仍有数据库长占用和排队。尚未证明前端稳定可见、30m运行恢复和长期时延达标。监控保持暂停。</p><p><a href="http://127.0.0.1:18770/previews/P39_signal_admission_activation.html">查看激活与真实运行核验</a>。下方“后台待激活”属于21:51代码验收历史，本批以本节为准。</p></section><!-- /P39-SIGNAL-ACTIVATION -->'''
for p in [OUT/'P39_信号进入链路修复与验收记录.html',public/'P39_signal_admission_fix.html',public/'P39_plan_latest.html',
          Path('D:/Axiangmu/erji/docs/P39_plan_latest.html'),Path('D:/Axiangmu/erji/docs/P39_ERJI_主链路收敛与数据可靠性整改方案.html')]:
 old=p.read_text('utf8');old=re.sub(r'<!-- P39-SIGNAL-ACTIVATION -->.*?<!-- /P39-SIGNAL-ACTIVATION -->','',old,flags=re.S)
 p.write_text(re.sub(r'(<main[^>]*>)',lambda m:m.group(0)+section,old,count=1),encoding='utf8')
for p in [OUT/'P39_信号进入链路修复与验收记录.md',Path('D:/Axiangmu/erji/docs/P39_ERJI_主链路收敛与数据可靠性整改方案.md')]:
 old=p.read_text('utf8')
 if '<!-- P39-SIGNAL-ACTIVATION -->' not in old:p.write_text('<!-- P39-SIGNAL-ACTIVATION -->\n\n'+md.split('## 实际运行结果')[0]+'\n完整核验：http://127.0.0.1:18770/previews/P39_signal_admission_activation.html\n\n<!-- /P39-SIGNAL-ACTIVATION -->\n\n'+old,encoding='utf8')
print(json.dumps({'run':run,'status':record['status'],'buy_api_total':total,'real_buy_decisions':len(buys),'projection_records':len(projected),'report_url':'http://127.0.0.1:18770/previews/P39_signal_admission_activation.html'},ensure_ascii=False))
