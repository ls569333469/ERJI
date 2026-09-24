from pathlib import Path
import shutil

base=Path(__file__).resolve().parent
output=Path('D:/Axiangmu/erji/preview')
current=output/'signals-a.html'
legacy=output/'signals-a-dark.html'
if not legacy.exists() and current.exists():
    shutil.copyfile(current,legacy)
css='\n'.join((base/name).read_text(encoding='utf-8') for name in ['base.css','refinements.css','light-workspace.css','compact-workspace.css','plan-workspace.css'])
core=(base/'core.js').read_text(encoding='utf-8')
core=core.replace("theme:'dark'","theme:'light'")
core=core.replace("'paneProperties.background':'#111821'","'paneProperties.background':'#ffffff','paneProperties.vertGridProperties.color':'#f0f3f7','paneProperties.horzGridProperties.color':'#f0f3f7'")
core=core.replace('<rect width="100%" height="100%" fill="#111821"/>','<rect width="100%" height="100%" fill="#ffffff"/>')
core=core.replace('stroke="#293542"','stroke="#e9edf4"').replace("'#70d6b7':'#ee919b'","'#27997b':'#cf7582'")
scripts='\n'.join([core,(base/'light-workspace.js').read_text(encoding='utf-8'),(base/'compact-workspace.js').read_text(encoding='utf-8'),(base/'plan-workspace.js').read_text(encoding='utf-8'),(base/'button-icons.js').read_text(encoding='utf-8')])
icons=(base/'lucide.min.js').read_text(encoding='utf-8')
document=f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>ERJI · 信号总览 / 浅色工作台</title><style>{css}</style></head>
<body class="mode-table workspace-light" data-design="table">
<header class="topbar"><a class="brand" href="signals-a.html" aria-label="ERJI 信号首页"><span>↗</span>ERJI</a><nav class="topnav" aria-label="主导航"><a class="active" href="signals-a.html">信号总览</a><a href="index.html">图表工作区</a></nav><div class="topbar-meta"><span class="prototype-label">演示数据</span><span>Binance · Hyperliquid · Aster · Wallet</span></div></header>
<main class="container">
<section class="page-heading"><div><h1>信号总览</h1><p>先看阶段，再看等待条件与关键点位。</p></div><div class="heading-tools"><div class="snapshot">演示快照<br><b class="mono">2026-09-22 16:00 UTC</b></div><span class="heading-divider"></span><button class="watch-overview" id="watchFilter" aria-pressed="false" data-icon-ready="true"><i data-lucide="star"></i>我的观察 <b id="watchCount">0</b></button></div></section>
<section class="signal-board" aria-label="信号总览工作台">
<div class="category-bar"><nav class="category-nav" aria-label="信号分类"><button class="active" data-family="all">全部信号 <span>8</span></button><button data-family="tech">技术结构 <span>4</span></button><button data-family="perp">合约异动 <span>3</span></button><button data-family="liquidity">流动性风险 <span>1</span></button></nav><div class="category-state"><i></i>固定样本 · 非实时扫描</div></div>
<div class="filters"><div class="market-tabs" aria-label="市场范围"><button class="active" data-pool="all">全部市场</button><button data-pool="binance">币安生态</button><button data-pool="chain">链上 Meme / FOMO</button></div><button class="filter-checkbox compact-only" id="bothFilter" aria-pressed="false">量价 + 合约</button><button class="filter-checkbox compact-only" id="levelColumns" aria-pressed="false">显示点位</button><input class="search" id="search" aria-label="搜索信号" placeholder="搜索代币、信号或来源"><select id="sort" aria-label="排序方式"><option value="default">默认示例顺序</option><option value="distance">距关注区间近 → 远</option><option value="name">代币名称 A–Z</option></select></div>
<div class="result-header"><span class="result-label">显示 <strong id="resultCount">8</strong> 条信号</span><span class="result-separator">/</span><span id="filterDescription">全部市场</span><span class="result-separator">/</span><span id="viewMode" class="view-hint">做多观察条件 · 待确认</span><button class="help-rules" id="helpRules"><i data-lucide="circle-help"></i>这些判断怎么看</button><button id="reset" class="textbutton">清除筛选</button></div>
<div id="results"></div><div class="table-caption"><span><i data-lucide="info"></i>合成演示 · 关注价不是到价买入，需等待对应周期收盘确认 · 规则参数待验证</span><span><span id="emptyCount">8 个预设场景</span> · 可横向滚动</span></div></section>
<footer class="footer"><span>条件满足后才生成参与候选；不预测某个钟点一定上涨。</span><div class="footer-links"><a href="signals-a-metrics.html">原指标视图</a><a href="signals-b.html">卡片版</a><span>ERJI / 设计预览</span></div></footer></main>
<dialog id="detailDialog" aria-labelledby="detailTitle"><header class="dialog-head"><div><h2 id="detailTitle"></h2><p id="detailSubtitle"></p></div><button id="closeDetail" class="close" aria-label="关闭图表">×</button></header><div class="detail-layout"><div class="chart-side"><div class="chart-controls"><button data-tf="60">1H</button><button class="active" data-tf="240">4H</button><button data-tf="1D">1D</button><button id="locate">⌖ 定位事件</button><small>查看周期与原始信号周期独立</small></div><div class="chart-stage"><div id="portableChart"></div><div id="nativeChart" style="visibility:hidden"></div></div><div class="chart-engine" id="chartEngine"></div></div><aside id="detailEvidence" class="detail-evidence"></aside></div><footer class="detail-bottom"><span id="eventTime"></span><span>价格、点位及所有证据均为合成示例</span></footer></dialog><div class="toast" id="toast" role="status" aria-live="polite"></div>
<dialog id="rulesDialog" aria-labelledby="rulesTitle"><button id="closeRules" aria-label="关闭说明"><i data-lucide="x"></i></button><h2 id="rulesTitle">读懂这一行</h2><p>主表回答现在怎样、接下来等什么。原始数据在“看计划”里展开。</p><div class="rule-row"><b>当前阶段</b><p>区间整理：尚未站上旧区间。突破后延伸：已经离上沿较远，不能把涨幅当进入依据。原结构失效：旧计划停止，等待新结构。</p></div><div class="rule-row"><b>关注价 / 区间</b><p>从事件发生时保存的区间上沿推导。回踩观察使用上沿附近的一段价格带；突破观察使用需要收盘越过的门槛。价格到了，仍需满足确认条件。</p></div><div class="rule-row"><b>何时考虑</b><p>不设“几小时后必涨”。回踩计划等回踩发生后完整 1H 收盘收回上沿；突破计划等完整 4H 收盘站上门槛。两者都要求成交额达到此前 20 根中位数的 1.5 倍，且风险检查通过。</p></div><div class="rule-row"><b>失效点</b><p>原区间下沿。4H 收盘跌破后，撤下旧计划。它是结构判断标准；实际交易的盘中止损、滑点与仓位需另设，不能把它直接当自动止损单。</p></div><div class="rule-row"><b>资金线索</b><p>OI 就是还没平仓的合约规模。增加表示参与规模上升，不知道是主动做多还是做空；价格涨、持仓减可能有空头回补，但需成交与清算数据核实。单一 OI 比例不能证明控盘或逼空。</p></div><div class="legend-note">本版是做多观察计划的交互样例，不是已验证策略。参数、胜率、参与时机和目标空间仍需历史回放与前向观察检验。数据或链上风险未核实时，先观察，不生成参与候选。</div></dialog>
<script>{icons}</script><script>{scripts}</script></body></html>'''
current.write_text(document,encoding='utf-8')
print(current)
print('Legacy: '+str(legacy))
