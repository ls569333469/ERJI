"""Record the authorized activation without resuming paused automations."""
import html, json
from datetime import datetime, timezone, timedelta
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = root / 'docs/p39-validation'
samples = sorted(out.glob('remaining-activation-sample-*.json'))
sample = json.loads(samples[-1].read_text('utf8'))
run = sample['run_id']
stamp = datetime.now(timezone(timedelta(hours=8))).isoformat()
auto = Path('C:/Users/ls569/.codex/automations/erji-p39/automation.toml').read_text('utf8')
assert 'status = "PAUSED"' in auto or 'status="PAUSED"' in auto
assert all(sample['ports'].values()) and all(sample['source_manifest_matches'].values())
assert all(v.get('status') == 200 for v in sample['http'].values())
records = sample['queries']['dia_snapshots']
fx_verified = any((r.get('execution') or {}).get('quote_usd_rate') and '换算缺失' not in str((r.get('execution') or {}).get('reasons')) for r in records)
tasks = sample['queries']['tasks']
record = {'activated_at_bjt': '2026-10-06T21:01:22+08:00', 'verified_at_bjt': stamp, 'run_id': run,
          'status': 'activated_runtime_partial_verification', 'roles': ['api','live','jobs','streams'],
          'retained_roles': ['budget','web'], 'monitor_status': 'PAUSED', 'ports': sample['ports'],
          'all_source_manifest_matches': True, 'dia_quote_fx_recovery_verified': fx_verified,
          'evidence_paths': [str(p) for p in samples if json.loads(p.read_text('utf8'))['run_id'] == run],
          'limits': ['5/15分钟异动窗口重新暖机，未完成长期稳定性验收', '新日志仍有数据库连接占用12.1–17.7秒警告',
                     '部分保护/结构/独立投影任务仍排队，submitted_at年龄不等于已完成延迟或可买丢失事实',
                     'DIA身份映射与事件追踪降噪不在本次已修复激活范围'],
          'production_data_modified_by_verifier': False, 'exchange_calls_by_verifier': False}
record_path = out / ('remaining-activation-' + run + '.json')
record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf8')
status_path = out / 'p39-current-followup-status.json'
status = json.loads(status_path.read_text('utf8'))
status.update(updated_at_bjt=stamp, overall_status='all_confirmed_code_scope_complete_backend_activated_runtime_acceptance_partial',
              backend_new_code_activated=True, activated_at_bjt=record['activated_at_bjt'], activation_run_id=run,
              activation_record=record_path.name, monitor_status='PAUSED')
status['verification']['service_restart'] = True
for item in status['items']:
    if item['id'].startswith('P39-N'):
        item['status'] = 'implemented_verified_activated_runtime_acceptance_partial'
    if item['id'] == 'P39-N01':
        item['production_quote_fx_recovery_verified'] = fx_verified
        item['activation_evidence'] = '原DIA计划新复核market/execution均有USD汇率，实际拒绝原因转为low_net_r；随后原计划到期。未以无买入证明完整链路恢复。'
status['limits'] = '全部已确定工程范围已实施并激活；本轮确认端口/API、当前run心跳及DIA汇率恢复。排队、数据库长占用、采集缺口与长期逐币恢复仍待运行验收；未恢复定时监控。'
status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf8')
dia = records[0]
dia_execution = dia.get('execution') or {}
md = f'''# P39 剩余修复激活记录

激活时间：北京时间2026-10-06 21:01；核验时间：{stamp}。新批次：{run}。

API、live、jobs、streams已重启加载新代码。budget/web沿用现有进程，前端已热加载。18项文件指纹与已验收清单完全匹配；工作进程启动心跳中的jobs/observation_query指纹也匹配。四个端口18770/18771/18772/55432可连接，本地健康、signals API及前端均HTTP 200。数据库最新快照索引有效。本次定时监控仍为PAUSED。

## DIA实际复核

原计划p33-cff654d61f47d61a4bcc838a:p33在新批次21:02及21:03生成复核快照。market与execution均带有效quote_usd_rate={dia_execution.get('quote_usd_rate')}，未再被“报价币USD换算缺失”阻止。最新报价{dia_execution.get('quote_price')}，扣费后盈亏比{dia_execution.get('net_r')}，真实结果是low_net_r（扣费后盈亏比低于本模板门槛），没有转为可买。该原计划于21:03:13.186到期，不重新延长或补造历史买入。DIA流通市值身份映射与事件追踪显示问题未在本轮额外修改。

## 启动核验与尚存边界

两次采样间entry/protect/projection/trend/structure的最近完成时间持续更新，投影继续刷新。完成累计数不是新增独立币数；可能退役/删除的任务不能用聚合计数变化推算吞吐。当前无有效可买资格，这一事实不能单独证明全链路正常。

新启动日志未发现Traceback、429/418或超时异常，但jobs出现三条database connection held警告：17.7秒、15.8秒、12.1秒。第二次采样独立投影最老submitted_at约563秒、保护116秒、结构234秒，包含重启前遗留任务；该年龄不等于真实执行延迟，也不证明可买被丢。秒级链路p95与这些排队是否消退仍未完成验收，不将“激活成功”写成“所有瓶颈恢复”。

本次重启会重建5/15分钟异动内存窗口，初期warming属于暖机。合约报价15秒覆盖560/563；OI90秒96/563、6分钟190/563，分母含全市场，并非仅在分析/保护名单，不能据此直接判定所有关注币缺数据。长期稳定性和逐币数据恢复尚待后续验证。

仅通过本地只读数据库、当前批次日志及少量本地HTTP核验，无额外交易所请求，无手动改生产数据、无策略阈值调整。证据：{record_path.name}，{samples[-2].name}，{samples[-1].name}。旧激活记录保留；未改写监控基线或恢复自动化。
'''
report_md = out / 'P39_剩余修复激活与运行核验_2026-10-06.md'
report_md.write_text(md, encoding='utf8')
parts = []
for paragraph in md.split('\n\n'):
    if paragraph.startswith('# '): parts.append('<h1>' + html.escape(paragraph[2:]) + '</h1>')
    elif paragraph.startswith('## '): parts.append('<h2>' + html.escape(paragraph[3:]) + '</h2>')
    else: parts.append('<p>' + html.escape(paragraph) + '</p>')
report_html = out / 'P39_剩余修复激活与运行核验_2026-10-06.html'
report_html.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>P39 修复激活核验</title><style>body{font:15px/1.8 system-ui,"Microsoft YaHei";background:#f5f7fb;color:#243348}main{max-width:1080px;margin:30px auto;padding:28px;background:white}p{overflow-wrap:anywhere}h2{font-size:20px}</style><main>' + ''.join(parts) + '</main></html>', encoding='utf8')
public = root / 'apps/web/public/previews'
(public / 'P39_remaining_activation.html').write_bytes(report_html.read_bytes())
notice = f'【最新激活补充 {stamp}】API/live/jobs/streams已加载新批次{run}，四个端口及API可用，DIA报价币USD换算缺失已恢复。仍有数据库连接长占用及排队，长期运行验收未完成。以下“未激活”表述保留为20:33历史记录。监控保持暂停。'
for path in (out/'P39_剩余代码更新与验收记录_2026-10-06.html', public/'P39_remaining_acceptance.html',
             root.parents[2]/'docs/P39_ERJI_主链路收敛与数据可靠性整改方案.html', root.parents[2]/'docs/P39_plan_latest.html', public/'P39_plan_latest.html'):
    content = path.read_text('utf8')
    if 'id="p39-remaining-activation"' not in content:
        section = '<section id="p39-remaining-activation" style="background:#edf8f1;padding:16px;margin:16px"><h2>已修复代码激活</h2><p>' + html.escape(notice) + '</p><p><a href="http://127.0.0.1:18770/previews/P39_remaining_activation.html">查看激活与运行核验</a></p></section>'
        if '<main>' in content: content = content.replace('<main>', '<main>' + section, 1)
        else: content = content.replace('</body>', section + '</body>', 1)
        path.write_text(content, encoding='utf8')
for path in (out/'P39_剩余代码更新与验收记录_2026-10-06.md', root.parents[2]/'docs/P39_ERJI_主链路收敛与数据可靠性整改方案.md'):
    content = path.read_text('utf8')
    path.write_text(notice + '\n\n' + content, encoding='utf8')
print(json.dumps({'record':str(record_path),'report':str(report_html),'backend_activated':True,'monitor':'PAUSED','fx_recovered':fx_verified}, ensure_ascii=False))
