from pathlib import Path
import re
base=Path(__file__).resolve().parent
out=base/'staging'
out.mkdir(parents=True,exist_ok=True)
source=(base/'build-light.py').read_text(encoding='utf-8')
template=source.split("document=f'''",1)[1].split("'''\ncurrent.write_text",1)[0]
css='\n'.join((base/f).read_text(encoding='utf-8') for f in ['base.css','light-workspace.css','oi-workspace.css'])
core=(base/'core.js').read_text(encoding='utf-8')
core=core.replace("theme:'dark'","theme:'light'").replace("'paneProperties.background':'#111821'","'paneProperties.background':'#ffffff','paneProperties.vertGridProperties.color':'#eef2f7','paneProperties.horzGridProperties.color':'#eef2f7'")
core=core.replace("[e.high,'区间上沿 · 示例','#efcf86'],[e.low,'失效参考 · 示例','#ee919b']","[e.high,'4H 箱体上沿','#7794c3'],[e.low,'4H 箱体下沿','#c88794']")
core=core.replace("text:[a.label+' · 预设事件'","text:[(episode(a).trigger?'4H箱体突破确认':'4H箱体观察')+' · 合成事件'")
icons=(base/'lucide.min.js').read_text(encoding='utf-8')
scripts=core+'\n'+(base/'oi-workspace.js').read_text(encoding='utf-8')
doc=template.replace('{css}',css).replace('{icons}',icons).replace('{scripts}',scripts)
doc=doc.replace('ERJI · 信号总览 / 浅色工作台','ERJI · 信号总览 / 跨平台持仓')
doc=doc.replace('先看阶段，再看等待条件与关键点位。','结构告诉你等哪里，持仓与费用解释行情由什么推动。')
doc=doc.replace('<button class="filter-checkbox compact-only" id="bothFilter"','<div class="view-toggle" aria-label="总览视图"><button class="active" data-view="decision" aria-pressed="true">信号判断</button><button data-view="derivatives" aria-pressed="false">合约数据</button></div><button class="filter-checkbox compact-only" id="bothFilter"')
doc=doc.replace('<option value="distance">距关注区间近 → 远</option>','<option value="oi">已覆盖持仓大 → 小</option>')
doc=doc.replace('<div id="results"></div>','<div class="intro-strip"><span><b>OI</b> 未平仓合约规模</span><span><b>数量变化</b> 排除涨价影响</span><span><b>负费率</b> 空头付费，仍需价格确认</span><span>所有数值与平台分布为合成演示</span></div><div id="results"></div>')
doc=doc.replace('合成演示 · 关注价不是到价买入，需等待对应周期收盘确认 · 规则参数待验证','未接实时 API · 金额只含已覆盖市场，非全网 · 参与需收盘确认')
doc=doc.replace('条件满足后才生成参与候选；不预测某个钟点一定上涨。','到达第一阻力后继续跟踪；失效事件保留，不随反弹恢复。')
doc=doc.replace('<div class="chart-engine" id="chartEngine"></div></div><aside','<div class="chart-engine" id="chartEngine"></div><section class="venue-block"><h3>持仓在哪里 · 各平台证据</h3><div id="venueEvidence"></div></section></div><aside')
start=doc.index('<h2 id="rulesTitle">');end=doc.index('</dialog>',start)
doc=doc[:start]+'''<h2 id="rulesTitle">先看结构，再核对合约证据</h2>
<p><b>信号判断：</b>每币一行，明确4H箱体、当前阶段、关注价、确认与撤销条件。持仓数字直接显示，长解释放在图表侧边。</p>
<p><b>合约数据：</b>比较数量OI、美元OI、流通市值比例、持仓平台占比、资金费负担和清算。金额单位M为百万美元、B为十亿美元。</p>
<p><b>数量 ≠ 美元：</b>币价上涨会抬高美元OI；数量不变不能说新增仓位。每份合约同时有多空，不把总量当空头金额。</p>
<p><b>费用 ≠ 爆仓：</b>负费率通常由空头向多头支付，需保留结算周期；持续成本可能造成主动平空，不能当成已经强平。</p>
<p><b>比例 ≠ 概率：</b>OI/流通市值仅表示持仓密度，无统一的控盘阈值；没有供应依据时应留空。</p>
<p><b>缺口明确保留：</b>过期、缺失、身份待核、已查范围无匹配分开显示。覆盖变化暂停比较，不把缺失记成0。</p>
<p><b>两条失效线：</b>突破后4H收盘跌回上沿缓冲下方，撤销该次突破；跌破箱体下沿是更深层的结构失守。旧事件失效后需新结构。</p>
<p><b>图表与文本同一事件：</b>箱体、回踩带与撤销线由同一冻结快照生成。切换1H/4H/1D只改变查看周期。暂无数据时不绘制虚构清算痛点。</p>
<p>研究参数：前20根区间、ATR14的0.25倍缓冲、报价成交额1.5倍。均待历史负样本与前向验证；本页行情、费率、挂牌和覆盖为合成场景，不是交易建议或实时扫描。</p>'''+doc[end:]
(out/'signals-a.html').write_text(doc,encoding='utf-8')
(out/'signals-runtime.js').write_text(scripts,encoding='utf-8')
print(out/'signals-a.html')
