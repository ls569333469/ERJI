"""Append activation evidence without rewriting pre-activation test history."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json,html
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/p39-validation'
state=json.loads((OUT/'activation-monitor-state.json').read_text('utf8'))
sample=json.loads(Path(state['last_sample']).read_text('utf8'))
processes=json.loads((ROOT/'.runtime/processes.json').read_text('utf-8-sig'))
result=json.loads((OUT/'startup-fix-result.json').read_text('utf8'))
beat=next(b for b in sample['queries']['beats'] if b['role']=='jobs')
loaded=beat['details'].get('source_hashes',{})
assert loaded.get('engine/p38/jobs.py')==result['files']['engine/p38/jobs.py']
assert sample['queries']['schema'][0]['version_num']=='p39_snapshot_latest'
assert sample['queries']['index'][0]['indisvalid']
activation={'status':'activated','activated_run_id':sample['run_id'],'worker_started_at_bjt':datetime.fromtimestamp(beat['updated_at']/1000,timezone(timedelta(hours=8))).isoformat(),
 'observed_at_bjt':sample['sampled_at_bjt'],'migration':sample['queries']['schema'],'index':sample['queries']['index'],
 'loaded_jobs_hash_verified':True,'latest_sample':state['last_sample'],'baseline_sampled_at':state['baseline']['sampled_at_bjt'],
 'completed_increment':sample['since_baseline'],'ports':sample['ports'],'source_coverage':sample['queries']['source'],
 'latest_projection':sample['queries']['projection'],'http':sample['http'],'processes':processes,
 'monitor':{'automation_id':'erji-p39','every_minutes':10,'status':'ACTIVE','scope':'read-only, meaningful changes only'},
 'recovery_status':'dispatch_and_projection_progress_confirmed; market_freshness_and_OI_incomplete',
 'no_current_buy_verified':sample['queries']['projection'][0]['unexpired_buy']==0}
(OUT/'activation-status.json').write_text(json.dumps(activation,ensure_ascii=False,indent=2),encoding='utf8')
result.update(activation_status='activated',activation_evidence='activation-status.json',active_processes=processes,production_recovery_verified='partial: dispatch and projection advancing; freshness remains incomplete',production_schema={'migration_version':'p39_snapshot_latest','latest_snapshot_index':'ix_signal_snapshots_plan_latest (valid)'})
(OUT/'startup-fix-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
date=sample['sampled_at_bjt'];increments=sample['since_baseline']
note=f'''## 激活追记（{date}，北京时间）

索引迁移已到p39_snapshot_latest，索引有效。API/live/jobs/streams已在新批次{sample['run_id']}重启；实际jobs进程报告的源码指纹与已测试文件一致。四个端口均可连接，API、预算健康、前端HTTP均返回200。

从{state['baseline']['sampled_at_bjt']}至{date}，入场完成增量{increments['p39_entry']['completed']}、投影{increments['p39_projection']['completed']}、保护{increments['p39_protect']['completed']}、结构{increments['p39_structure']['completed']}、趋势{increments['p39_trend']['completed']}；这些是任务完成次数，包含同币不同版本，不是独立代币数量。采样间各类失败计数未增加。

页面投影时刻已推进；当次仍无有效可买信号。合约报价15秒覆盖397/563、60秒560/563；OI90秒97/563、6分钟251/563。现货报价15秒48/761、60秒559/761。这是当前全部库存记录的覆盖统计，分母含未准入/未更新旧市场；不等同于准入集合的健康率。此前13:59的合约15秒覆盖543，表明覆盖仍有波动，不能声称完全恢复。

当前新run日志未检出Traceback、429/418或TimeoutError；仍有数据库连接持有10–18秒警告，接收心跳虽正常，落库与OI缺口需继续观察。统计窗口中的旧任务p95等待含激活前排队时间，不作为新调度单独耗时。

已创建erji-p39定时监控，每10分钟只读检查；状态未变不重复提醒，有新故障、显著恢复或现有规则下真实可买结果才通知。当前监控脚本只读生产，另保存证据和日志偏移；不会自动改代码、重启、放宽门槛或启用交易。5/15分钟实时窗口仍须积累真实连续数据。

证据：activation-status.json、activation-observations.jsonl、{Path(state['last_sample']).name}。
'''
title='P39_启动后阻塞修复与验证记录'
md=OUT/(title+'.md');text=md.read_text('utf8');md.write_text(text+'\n'+note,encoding='utf8')
page=OUT/(title+'.html');text=page.read_text('utf8')
text=text.replace('<strong>尚未重启激活，生产恢复未验证。</strong>','<strong>激活追记：已迁移并重启；任务与页面投影已推进，报价及OI覆盖仍待恢复。</strong>')
section='<section style="background:#eef6ff;padding:18px;margin:20px 0"><h2>激活追记</h2><pre>'+html.escape(note)+'</pre><a href="activation-status.json">当前激活证据</a></section>'
text=text.replace('<h2>8项问题的修复与验证</h2>',section+'<h2>8项问题的修复与验证</h2>')
page.write_text(text,encoding='utf8')
state['notified_issues']=[{'at_bjt':date,'topic':'activation_partial_recovery','details':'activated and dispatch/projection progressing; quote/OI incomplete, no eligible buy. Do not repeat unchanged.'}]
(OUT/'activation-monitor-state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'status':activation['status'],'evidence':str(OUT/'activation-status.json'),'monitor':'erji-p39'},ensure_ascii=False))
