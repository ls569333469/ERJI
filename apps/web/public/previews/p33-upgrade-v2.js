'use strict';
// Desktop-only preview: reuse the archived candles and example rows from p33-upgrade.js.
const p33OriginalPrices = { NOM: 1, RAVE: .84, MOVR: .56 };
const p33Time = value => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(value).replaceAll('/', '-');
const p33Money = value => value == null ? '待确认' : '$' + value.toFixed(value < .1 ? 6 : 2);
const p33Fact = (title, value) => `<div class="fact"><span>${title}</span><b>${value}</b></div>`;

for (const r of examples) {
  const factor = Number(r.price.slice(1)) / p33OriginalPrices[r.id];
  const originalEntry = { NOM: [.98, 1.02], RAVE: [.76, .79], MOVR: [.54, .57] }[r.id];
  r.entry = originalEntry.map(value => p33Money(value * factor)).join('–');
  r.archiveTime = archive[r.id]['4H'].at(-1).t;
  r.space = (Number(r.target1.slice(1)) / Number(r.price.slice(1)) - 1) * 100;
}

row = function (r) {
  const expanded = openId === r.id;
  return `<tr><td><div class="coin"><span class="coin-icon">${r.id.slice(0, 4)}</span><div><b>${r.id}</b><small>币安 USDT永续</small></div></div></td>
    <td><span class="action-tag">${r.action}</span><div class="opportunity-reason">${r.reason}</div><small>${r.strategy} · ${r.stage}</small></td>
    <td class="summary-price">${r.price}<small>${p33Time(r.archiveTime)} 北京时间</small></td>
    <td class="numeric"><b>${r.entry}</b><small>保护 ${r.protection === '触发后生成' ? '待确认' : r.protection}</small></td>
    <td class="numeric"><b class="summary-goal">${r.target1}</b><small>距现价 ${r.space >= 0 ? '+' : ''}${r.space.toFixed(1)}%</small></td>
    <td><div class="timeframes">${['1H','4H','1D','1W'].map((tf, i) => `<span class="${['转强','突破','向上'].includes(r.trend[i]) ? 'up' : 'turn'}">${tf}<small>${r.trend[i]}</small></span>`).join('')}</div></td>
    <td class="numeric"><b>${r.ratio}</b><small>资金费率 ${r.funding}</small></td>
    <td><button class="text-button" data-open="${r.id}" aria-expanded="${expanded}">${expanded ? '收起' : '查看详情'} <i data-lucide="chevron-${expanded ? 'up' : 'down'}"></i></button></td></tr>
    ${expanded ? `<tr class="detail-row"><td colspan="8">${detail(r)}</td></tr>` : ''}`;
};

render = function () {
  const visible = list();
  $('#count-all').textContent = visible.length;
  $('#count-buy').textContent = visible.filter(r => r.level === 'l1').length;
  $('#count-other').textContent = visible.filter(r => r.level !== 'l1').length;
  $('#stages').innerHTML = groups.map(group => {
    const rows = visible.filter(r => r.level === group.id);
    return `<section class="opportunity-stage ${group.id}"><div class="stage-head"><span>${group.id.toUpperCase()}</span><strong>${group.title}</strong><p>${group.desc}</p><b>${rows.length} 个</b></div>
      ${rows.length ? `<div class="table-scroll"><table class="opportunity-table"><thead><tr><th>代币</th><th>现在怎么做 / 策略</th><th>现价</th><th>参与区 / 保护</th><th>第一目标</th><th>1H / 4H / 1D / 1W</th><th title="OI 名义金额 / 流通市值；资金费率按8H结算周期">OI / 市值 · 资金费率 ⓘ</th><th>详情</th></tr></thead><tbody>${rows.map(row).join('')}</tbody></table></div>` : `<div class="stage-empty">当前筛选下没有“${group.title}”的示例信号</div>`}</section>`;
  }).join('');
  icons();
  if (openId && detailTab === 'chart') draw();
};

detail = function (r) {
  const tabs = [['evidence','机会详情'], ['chart','K线与指标'], ['history','策略与案例']];
  return `<div class="detail"><div class="detail-head"><h2>${r.id} · ${r.strategy}</h2><span class="stage-tag">${r.stage}</span><button class="close" data-open="${r.id}" aria-label="收起"><i data-lucide="x"></i></button></div>
    <div class="detail-tabs">${tabs.map(([id, label]) => `<button data-tab="${id}" class="${detailTab === id ? 'active' : ''}">${label}</button>`).join('')}</div>
    ${detailTab === 'evidence' ? `<div class="detail-overview"><div class="overview-column overview-data">${evidence(r)}${p33Contract(r)}</div><div class="overview-column overview-context">${p33Reason(r)}${tracking(r)}</div></div>` : detailTab === 'chart' ? chart(r) : history(r)}</div>`;
};

evidence = function (r) {
  return `<section class="overview-section"><h3>参与价格与目标</h3><div class="facts">
    ${p33Fact('现价', r.price)}${p33Fact('参与区', r.entry)}${p33Fact('原保护', r.protection === '触发后生成' ? '待回踩触发后确定' : r.protection)}${p33Fact('失效条件', r.invalidation)}${p33Fact('第一目标', r.target1)}${p33Fact('日线条件目标', r.daily)}${p33Fact('周线条件目标', r.weekly)}${p33Fact('首次报告价', r.first)}${p33Fact('K线归档时间', p33Time(r.archiveTime) + ' 北京时间')}
    </div></section>`;
};

function p33Contract(r) {
  return `<section class="overview-section"><h3>合约参与</h3><div class="facts">${p33Fact('币安 OI', r.oi + '（样例）')}${p33Fact('OI / 流通市值', r.ratio + '（样例）')}${p33Fact('数量 OI · 1H', r.oi1 + '（样例）')}${p33Fact('数量 OI · 4H', r.oi4 + '（样例）')}${p33Fact('数量 OI · 1D', r.oiD + '（样例）')}${p33Fact('资金费率 · 8H', r.funding + '（样例）')}${p33Fact('逐笔清算', '暂无对齐数据')}</div></section>`;
}

function p33Reason(r) {
  return `<section class="overview-section"><h3>判断依据</h3><div class="overview-verdict"><b>${r.action}</b><p>${r.reason}。</p></div><ul class="evidence-list"><li class="ok"><b>当前阶段</b>${r.stage}（样例）</li><li class="wait"><b>待确认</b>${r.level === 'l1' ? '周线趋势' : r.level === 'l2' ? '价格回到参与区并形成回踩承接' : '4H 收盘突破前高'}</li><li><b>多周期</b>${['1H','4H','1D','1W'].map((tf,i) => `${tf} ${r.trend[i]}`).join(' · ')}</li></ul><p class="overview-note">价格和 OI 的变化不能单独证明发生了空头强平；需要同一时段的清算记录确认。</p></section>`;
}

chart = function (r) {
  return `<div class="chart-body"><div class="chart-wrap"><div class="chart-topline"><b>${r.id}USDT · 币安永续历史归档</b><div class="chart-mode"><button data-tf="4H" class="${chartTf === '4H' ? 'active' : ''}">4H</button><button data-tf="1D" class="${chartTf === '1D' ? 'active' : ''}">1D</button><button disabled title="本预览未导入完整1H归档">1H</button><button disabled title="本预览未导入完整1W归档">1W</button></div><div class="layer-menu"><button id="chart-layers" aria-expanded="${!!window.p33LayerOpen}">指标图层 <i data-lucide="layers-3"></i></button>${window.p33LayerOpen ? `<div class="layer-options"><label><input type="checkbox" id="bb-toggle" ${window.p33Bb === false ? '' : 'checked'}>布林带 20,2</label><label title="EMA20/50为默认价格参考">EMA20 / EMA50 · 默认</label><label title="归档缺基础成交量和事件锚点">事件 AVWAP · 缺锚点</label><label title="归档缺已确认且冻结的A/B/C波段">斐波目标 · 缺锚点</label></div>` : ''}</div></div><div class="chart-legend"><span style="--swatch:#2670aa">EMA20</span><span style="--swatch:#b6802d">EMA50</span>${window.p33Bb === false ? '' : '<span style="--swatch:#8a67b8">布林带</span>'}</div><div class="chart-readout" id="chart-readout"></div><canvas id="chart" aria-label="历史蜡烛K线、EMA、成交额与MACD或RSI"></canvas><div class="chart-extras"><b>副图</b><button data-sub="macd" class="${window.p33Sub === 'rsi' ? '' : 'active'}">MACD</button><button data-sub="rsi" class="${window.p33Sub === 'rsi' ? 'active' : ''}">RSI 14</button><span id="indicator-readout"></span></div><div class="chart-foot">归档OHLC计算指标。示例参与区、保护和目标没有同一事件证据，故不叠到真实K线；事件AVWAP与斐波缺合格锚点时不画。</div></div></div>`;
};

function p33Bollinger(values) { return values.map((_, i) => { if (i < 19) return null; const part = values.slice(i - 19, i + 1), mean = part.reduce((a, b) => a + b, 0) / 20, sd = Math.sqrt(part.reduce((a, b) => a + (b - mean) ** 2, 0) / 20); return [mean - sd * 2, mean, mean + sd * 2]; }); }
function p33Rsi(values) { const out = Array(values.length).fill(null); let gain = 0, loss = 0; for (let i = 1; i < values.length; i++) { const d = values[i] - values[i - 1]; if (i <= 14) { gain += Math.max(d, 0); loss += Math.max(-d, 0); if (i === 14) { gain /= 14; loss /= 14; } } else { gain = (gain * 13 + Math.max(d, 0)) / 14; loss = (loss * 13 + Math.max(-d, 0)) / 14; } if (i >= 14) out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss); } return out; }
function p33Macd(values) { const fast = ema(values, 12), slow = ema(values, 26), dif = fast.map((v, i) => v == null || slow[i] == null ? null : v - slow[i]), first = dif.findIndex(v => v != null), signal = Array(values.length).fill(null); if (first >= 0) ema(dif.slice(first), 9).forEach((v, i) => signal[first + i] = v); return { dif, signal }; }
function p33Atr(bars) { if (bars.length < 15) return null; const ranges = bars.slice(1).map((b, i) => Math.max(b.h - b.l, Math.abs(b.h - bars[i].c), Math.abs(b.l - bars[i].c))); let value = ranges.slice(0, 14).reduce((a, b) => a + b, 0) / 14; for (const range of ranges.slice(14)) value = (value * 13 + range) / 14; return value; }

const p33BaseDraw = draw;
draw = function () {
  p33BaseDraw();
  const canvas = $('#chart'); if (!canvas) return;
  const bars = archive[openId][chartTf], closes = bars.map(b => b.c), bb = p33Bollinger(closes), last = bars.slice(-65), offset = bars.length - last.length, w = canvas.clientWidth, h = canvas.clientHeight, left = 12, right = w - 68, top = 16, base = h * .58, step = (right - left) / last.length, x = i => left + (i + .5) * step;
  const e20 = ema(closes, 20).slice(-65), e50 = ema(closes, 50).slice(-65), low = Math.min(...last.map(b => b.l), ...e20.filter(Boolean), ...e50.filter(Boolean)), high = Math.max(...last.map(b => b.h), ...e20.filter(Boolean), ...e50.filter(Boolean)), pad = (high - low) * .08, y = v => base - (v - (low - pad)) / (high - low + 2 * pad) * (base - top);
  const c = canvas.getContext('2d'); c.save(); c.scale(devicePixelRatio || 1, devicePixelRatio || 1); if (window.p33Bb !== false) { c.strokeStyle = '#8a67b8'; c.lineWidth = 1; c.setLineDash([3, 3]); for (const index of [0, 1, 2]) { c.beginPath(); let started = false; bb.slice(offset).forEach((v, i) => { if (!v) return; started ? c.lineTo(x(i), y(v[index])) : c.moveTo(x(i), y(v[index])); started = true; }); c.stroke(); } c.setLineDash([]); }
  if (window.p33Sub === 'rsi') { const topY = h * .78, bottomY = h - 15, rsi = p33Rsi(closes).slice(offset), ry = v => bottomY - v / 100 * (bottomY - topY); c.fillStyle = '#fff'; c.fillRect(0, topY - 12, w, h - topY + 12); for (const level of [30, 70]) { c.strokeStyle = '#d6dfe5'; c.setLineDash([3, 3]); c.beginPath(); c.moveTo(left, ry(level)); c.lineTo(right, ry(level)); c.stroke(); } c.setLineDash([]); c.strokeStyle = '#7554a6'; c.lineWidth = 1.5; c.beginPath(); let started = false; rsi.forEach((v, i) => { if (v == null) return; started ? c.lineTo(x(i), ry(v)) : c.moveTo(x(i), ry(v)); started = true; }); c.stroke(); c.fillStyle = '#68798b'; c.font = '10px system-ui'; c.fillText('RSI 14 · 70/30', left, topY - 3); }
  else { const m = p33Macd(closes), dif = m.dif.slice(offset), signal = m.signal.slice(offset), values = [...dif, ...signal].filter(v => v != null), max = Math.max(...values.map(Math.abs), 1e-9), middle = (h * .8 + h - 18) / 2, my = v => middle - v / max * (h * .2 - 18) * .4; for (const [series, color] of [[dif, '#2670aa'], [signal, '#b6802d']]) { c.strokeStyle = color; c.lineWidth = 1.2; c.beginPath(); let started = false; series.forEach((v, i) => { if (v == null) return; started ? c.lineTo(x(i), my(v)) : c.moveTo(x(i), my(v)); started = true; }); c.stroke(); } }
  c.restore();
  const readout = $('#chart-readout'); readout.textContent = readout.textContent.replace(/\d{4}-\d\d-\d\d \d\d:\d\d UTC/, p33Time(last.at(-1).t) + ' 北京时间');
  $('#indicator-readout').textContent = `布林中轨 ${p33Money(bb.at(-1)?.[1])} · ATR14 ${p33Money(p33Atr(bars))} · RSI14 ${p33Rsi(closes).at(-1)?.toFixed(1) || '预热不足'}`;
};
document.addEventListener('click', event => { const button = event.target.closest('button'); if (button?.dataset.sub) { window.p33Sub = button.dataset.sub; render(); } else if (button?.id === 'chart-layers') { window.p33LayerOpen = !window.p33LayerOpen; render(); } });
document.addEventListener('change', event => { if (event.target.id === 'bb-toggle') { window.p33Bb = event.target.checked; render(); } });

tracking = function (r) {
  return `<section class="overview-section"><h3>事件追踪</h3><div class="timeline"><div class="event"><strong>首次触发</strong><small>记录首报 ${r.first}、原保护和目标。</small></div><div class="event preview-future"><strong>结构确认</strong><small>对应周期收盘后更新阶段，保留首次记录。</small></div><div class="event preview-future"><strong>目标 / 保护 / 失效</strong><small>分别追加首次发生时间。</small></div></div><p class="tracking-note">本页为静态预览，尚未接入后台动态追踪。</p></section>`;
};

render();
