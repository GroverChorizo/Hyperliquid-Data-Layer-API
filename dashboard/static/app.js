/* Grover streaming dashboard client.
   - Live tab: WebSocket stream -> candlestick chart, instrument cards, sparklines,
     HLP gauge, Fear & Greed, liquidations, trade tape.
   - Feed tabs: themed grids that lazy-load related example endpoints via REST
     and auto-refresh while visible. */
'use strict';

const $ = (id) => document.getElementById(id);
const LWC = window.LightweightCharts;

// ---------- formatting ----------
const numFmt = (n, d = 2) => (n == null || isNaN(n)) ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
function usd(n) {
  if (n == null || isNaN(n)) return '—';
  const s = n < 0 ? '-' : ''; n = Math.abs(n);
  for (const [suf, sc] of [['T', 1e12], ['B', 1e9], ['M', 1e6], ['K', 1e3]]) if (n >= sc) return `${s}$${(n / sc).toFixed(2)}${suf}`;
  return `${s}$${n.toFixed(2)}`;
}
const pct = (n, d = 2) => (n == null || isNaN(n)) ? '—' : `${n >= 0 ? '+' : ''}${Number(n).toFixed(d)}%`;
const clsNum = (n) => n > 0 ? 'up' : n < 0 ? 'down' : '';
const hhmmss = (ms) => new Date(ms).toLocaleTimeString('en-GB', { hour12: false });
const shortAddr = (a) => (typeof a === 'string' && a.length > 12) ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

// sparkline path for an array of [ts, value] (or plain numbers)
function sparkPath(points, w, h, pad = 2) {
  const vals = points.map((p) => Array.isArray(p) ? p[1] : p).filter((v) => v != null && !isNaN(v));
  if (vals.length < 2) return '';
  const lo = Math.min(...vals), hi = Math.max(...vals), span = (hi - lo) || 1;
  const step = (w - pad * 2) / (vals.length - 1);
  return vals.map((v, i) => `${i ? 'L' : 'M'}${(pad + i * step).toFixed(1)} ${(h - pad - ((v - lo) / span) * (h - pad * 2)).toFixed(1)}`).join(' ');
}
function sparkSVG(points, w, h, color) {
  const d = sparkPath(points, w, h);
  if (!d) return '';
  const vals = points.map((p) => Array.isArray(p) ? p[1] : p);
  const c = color || (vals[vals.length - 1] >= vals[0] ? 'var(--up)' : 'var(--down)');
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" width="100%" height="100%"><path d="${d}" fill="none" stroke="${c}" stroke-width="1.5"/></svg>`;
}

// ============================================================
// TABS
// ============================================================
const FEED_TABS = [
  { id: 'liquidations', title: 'Liquidations', feeds: ['liq_multi', 'liq_stats', 'liq_binance', 'liq_bybit', 'liq_okx', 'liq_hip3'] },
  { id: 'hlp', title: 'HLP', feeds: ['hlp_sentiment', 'hlp_positions', 'hlp_delta', 'hlp_flips', 'hlp_timing', 'hlp_correlation', 'hlp_liquidator_status', 'hlp_trade_stats'] },
  { id: 'flow', title: 'Order Flow', feeds: ['orderflow', 'imbalance', 'trades', 'large_trades', 'ticks_latest', 'buyers'] },
  { id: 'positions', title: 'Positions & Whales', feeds: ['positions', 'whales', 'user_positions', 'user_fills', 'depositors'] },
  { id: 'smart', title: 'Smart Money', feeds: ['sm_leaderboard', 'sm_rankings', 'sm_signals'] },
  { id: 'market', title: 'Market & Chain', feeds: ['prices', 'events', 'contracts', 'hip3_meta', 'hip3_ticks_stats'] },
];

let activeTab = 'live';
let feedTimer = null;

function buildTabs() {
  const bar = $('tabbar');
  const mk = (id, title) => {
    const b = document.createElement('button');
    b.className = 'tab' + (id === activeTab ? ' active' : '');
    b.textContent = title; b.dataset.tab = id;
    b.onclick = () => selectTab(id);
    return b;
  };
  bar.appendChild(mk('live', 'Live Stream'));
  for (const t of FEED_TABS) bar.appendChild(mk(t.id, t.title));
}

function selectTab(id) {
  activeTab = id;
  document.querySelectorAll('#tabbar .tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === id));
  const live = id === 'live';
  $('view-live').style.display = live ? '' : 'none';
  $('view-feeds').style.display = live ? 'none' : '';
  if (feedTimer) { clearInterval(feedTimer); feedTimer = null; }
  if (live) { if (chart) chart.timeScale().fitContent(); return; }
  renderFeedTab(FEED_TABS.find((t) => t.id === id));
}

// ============================================================
// LIVE VIEW — chart + WS
// ============================================================
let chart, candleSeries, volSeries;
const store = { candles: {}, spark: {}, active: null, symbols: [], interval: '5m' };

function initChart() {
  const elc = $('chart');
  chart = LWC.createChart(elc, {
    layout: { background: { color: 'transparent' }, textColor: '#8493ad', fontFamily: 'SF Mono, ui-monospace, monospace', fontSize: 11 },
    grid: { vertLines: { color: '#161e30' }, horzLines: { color: '#161e30' } },
    rightPriceScale: { borderColor: '#1d2740' },
    timeScale: { borderColor: '#1d2740', timeVisible: true, secondsVisible: false },
    crosshair: { mode: LWC.CrosshairMode.Normal },
  });
  candleSeries = chart.addCandlestickSeries({
    upColor: '#2dd4a7', downColor: '#ff5470', borderVisible: false,
    wickUpColor: '#2dd4a7', wickDownColor: '#ff5470',
  });
  volSeries = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
  volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
  new ResizeObserver(() => chart.applyOptions({ width: elc.clientWidth, height: elc.clientHeight })).observe(elc);
}

const toBar = (c) => ({ time: Math.floor(c.t / 1000), open: c.o, high: c.h, low: c.l, close: c.c });
const toVol = (c) => ({ time: Math.floor(c.t / 1000), value: c.v || 0, color: c.c >= c.o ? '#1b6b58' : '#7a2d3d' });

function setActiveSymbol(sym) {
  store.active = sym;
  document.querySelectorAll('#sym-tabs .tab').forEach((b) => b.classList.toggle('active', b.dataset.sym === sym));
  document.querySelectorAll('.icard').forEach((c) => c.classList.toggle('active', c.dataset.sym === sym));
  const map = store.candles[sym];
  if (!map) return;
  const arr = [...map.values()].sort((a, b) => a.t - b.t);
  candleSeries.setData(arr.map(toBar));
  volSeries.setData(arr.map(toVol));
  chart.timeScale().fitContent();
  updateQuote(sym);
}

function updateQuote(sym) {
  const sp = store.spark[sym]; if (!sp || !sp.price.length) return;
  const last = sp.price[sp.price.length - 1][1], first = sp.price[0][1];
  const chg = first ? ((last - first) / first) * 100 : 0;
  $('q-last').textContent = numFmt(last, last < 10 ? 4 : 2);
  const c = $('q-chg'); c.textContent = pct(chg); c.className = 'num chg ' + clsNum(chg);
}

function buildSymTabs() {
  const st = $('sym-tabs'); st.innerHTML = '';
  for (const s of store.symbols) {
    const b = document.createElement('button');
    b.className = 'tab'; b.dataset.sym = s; b.textContent = s;
    b.onclick = () => setActiveSymbol(s);
    st.appendChild(b);
  }
  const iv = $('iv-tabs'); iv.innerHTML = '';
  const b = document.createElement('button'); b.className = 'tab active'; b.textContent = store.interval; iv.appendChild(b);
}

function renderCards() {
  const wrap = $('cards'); wrap.innerHTML = '';
  for (const s of store.symbols) {
    const sp = store.spark[s] || { price: [], funding: [], oi: [] };
    const price = sp.price.length ? sp.price[sp.price.length - 1][1] : null;
    const first = sp.price.length ? sp.price[0][1] : null;
    const chg = first ? ((price - first) / first) * 100 : null;
    const funding = sp.funding.length ? sp.funding[sp.funding.length - 1][1] : null;
    const oi = sp.oi.length ? sp.oi[sp.oi.length - 1][1] : null;
    const fbps = funding != null ? funding * 100 : null;
    const card = document.createElement('div');
    card.className = 'icard' + (s === store.active ? ' active' : '');
    card.dataset.sym = s;
    card.innerHTML = `
      <div class="row1"><span class="sym">${s}</span><span class="chg num ${clsNum(chg)}">${pct(chg)}</span></div>
      <div class="price num">${price != null ? numFmt(price, price < 10 ? 4 : 2) : '—'}</div>
      <div class="spark">${sparkSVG(sp.price, 200, 34)}</div>
      <div class="row3">
        <span><span class="kv-dim">funding</span> <b class="${clsNum(fbps)}">${fbps != null ? pct(fbps, 4) : '—'}</b></span>
        <span><span class="kv-dim">oi</span> <b>${usd(oi)}</b></span>
      </div>`;
    card.onclick = () => setActiveSymbol(s);
    wrap.appendChild(card);
  }
}

// ----- rail panels -----
function renderHLPTicks() {
  const g = $('hlp-ticks'); if (g.childElementCount) return;
  for (let z = -3; z <= 3; z++) {
    const ang = Math.PI - ((z + 3) / 6) * Math.PI;
    const x1 = 100 + 72 * Math.cos(ang), y1 = 100 - 72 * Math.sin(ang);
    const x2 = 100 + 80 * Math.cos(ang), y2 = 100 - 80 * Math.sin(ang);
    const lx = 100 + 90 * Math.cos(ang), ly = 100 - 90 * Math.sin(ang);
    g.insertAdjacentHTML('beforeend', `<line class="tick" x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke-width="1.5"/><text class="ticklbl" x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle">${z > 0 ? '+' + z : z}</text>`);
  }
}
function renderHLP(h) {
  if (!h) return;
  const z = h.z_score;
  renderHLPTicks();
  const zc = Math.max(-3, Math.min(3, z || 0));
  const ang = Math.PI - ((zc + 3) / 6) * Math.PI;
  const nx = 100 + 72 * Math.cos(ang), ny = 100 - 72 * Math.sin(ang);
  const color = Math.abs(z) < 1 ? '#8493ad' : Math.abs(z) < 2 ? '#ffb454' : (z > 0 ? '#2dd4a7' : '#ff5470');
  const n = $('hlp-needle');
  n.setAttribute('x2', nx.toFixed(1)); n.setAttribute('y2', ny.toFixed(1)); n.setAttribute('stroke', color);
  $('hlp-z').innerHTML = `${z != null ? (z >= 0 ? '+' : '') + z.toFixed(2) : '—'}<small>σ</small>`;
  $('hlp-z').style.color = color;
  $('hlp-signal').textContent = h.signal || '—';
  $('hlp-delta').textContent = usd(h.net_delta);
  $('hlp-pct').textContent = h.percentile != null ? `${Number(h.percentile).toFixed(0)}` : '—';
}

function renderFNG(rows) {
  if (!rows || !rows.length) return;
  const v = Number(rows[0].value);
  $('fng-val').textContent = isNaN(v) ? '—' : v;
  $('fng-class').textContent = rows[0].value_classification || '';
  $('fng-marker').style.left = `${Math.max(0, Math.min(100, v))}%`;
  const vals = rows.map((r) => Number(r.value)).reverse();
  $('fng-spark').innerHTML = sparkSVG(vals, 280, 26, 'var(--attn)');
}

function renderLiqs(l) {
  if (!l) return;
  $('liq-total').textContent = usd(l.total_volume);
  const lo = l.long || 0, sh = l.short || 0, tot = lo + sh;
  if (tot > 0) {
    $('liq-long').style.width = `${(lo / tot) * 100}%`;
    $('liq-short').style.width = `${(sh / tot) * 100}%`;
  }
  $('liq-long-v').textContent = usd(lo || null);
  $('liq-short-v').textContent = usd(sh || null);
  const ex = l.by_exchange || {};
  const max = Math.max(1, ...Object.values(ex).map(Number));
  $('liq-ex').innerHTML = Object.entries(ex).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([k, v]) =>
    `<div class="exbar"><span class="ex">${esc(k)}</span><span class="track2"><span class="fill2" style="width:${(v / max) * 100}%"></span></span><span class="v">${usd(v)}</span></div>`
  ).join('');
}

function renderTape(rows, prepend) {
  const el = $('tape');
  const maxUsd = Math.max(1, ...rows.map((t) => t.usd || 0));
  const html = rows.map((t) => `
    <div class="tape-row ${t.side}"><span class="szbar" style="width:${((t.usd || 0) / maxUsd) * 100}%"></span>
      <span class="t">${hhmmss(t.time)}</span>
      <span class="c ${t.side === 'buy' ? 'up' : 'down'}">${esc(t.coin)}</span>
      <span class="${t.side === 'buy' ? 'up' : 'down'}">${t.side === 'buy' ? '▲' : '▼'} ${numFmt(t.px, t.px < 10 ? 4 : 2)}</span>
      <span class="u">${usd(t.usd)}</span></div>`).join('');
  if (prepend) { el.insertAdjacentHTML('afterbegin', html); while (el.childElementCount > 60) el.lastElementChild.remove(); }
  else el.innerHTML = html;
}

// ----- WS handling -----
function applySnapshot(s) {
  store.symbols = s.config.symbols; store.interval = s.config.interval;
  $('watch').textContent = `${s.config.symbols.join(' ')} · ${s.config.interval}`;
  $('datadir').textContent = s.config.data_dir;
  $('offline').classList.toggle('show', !s.config.has_key);
  for (const sym of store.symbols) {
    store.candles[sym] = new Map((s.candles[sym] || []).map((c) => [c.t, c]));
    store.spark[sym] = s.spark[sym] || { price: [], oi: [], funding: [] };
  }
  buildSymTabs(); renderCards();
  setActiveSymbol(store.active && store.candles[store.active] ? store.active : store.symbols[0]);
  if (s.panels) { renderHLP(s.panels.hlp); renderFNG(s.panels.fng); renderLiqs(s.panels.liqs); }
  if (s.tape) renderTape(s.tape, false);
  if (s.updated) $('updated').textContent = hhmmss(s.updated);
}

function applyTick(t) {
  for (const [sym, e] of Object.entries(t.symbols || {})) {
    const sp = store.spark[sym]; if (!sp) continue;
    if (e.price != null) { sp.price.push([t.ts, e.price]); if (sp.price.length > 180) sp.price.shift(); }
    if (e.funding != null) { sp.funding.push([t.ts, e.funding]); if (sp.funding.length > 180) sp.funding.shift(); }
    if (e.oi != null) { sp.oi.push([t.ts, e.oi]); if (sp.oi.length > 180) sp.oi.shift(); }
  }
  if (t.candles) {
    for (const [sym, bar] of Object.entries(t.candles)) {
      const m = store.candles[sym]; if (!m) continue;
      m.set(bar.t, bar);
      if (sym === store.active) { candleSeries.update(toBar(bar)); volSeries.update(toVol(bar)); }
    }
  }
  renderCards();
  if (store.active) updateQuote(store.active);
  if (t.hlp) renderHLP(t.hlp);
  if (t.fng) renderFNG(t.fng);
  if (t.liqs) renderLiqs(t.liqs);
  if (t.tape && t.tape.length) renderTape(t.tape, true);
  $('updated').textContent = hhmmss(t.ts);
}

function connect() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onopen = () => { $('dot').className = 'dot live'; $('conn').textContent = 'streaming'; };
  ws.onclose = () => { $('dot').className = 'dot off'; $('conn').textContent = 'reconnecting…'; setTimeout(connect, 2000); };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === 'snapshot') applySnapshot(m);
    else if (m.type === 'tick') applyTick(m);
  };
}

// ============================================================
// FEED GRIDS (example tabs)
// ============================================================
function renderFeedTab(tab) {
  const view = $('view-feeds');
  view.innerHTML = tab.feeds.map((f) => `<section class="panel feed-panel" id="feed-${f}"><div class="phead"><span class="label" id="ftitle-${f}">${f}</span><span class="sub" id="fsub-${f}">loading…</span></div><div class="feed-body" id="fbody-${f}">…</div></section>`).join('');
  const load = () => tab.feeds.forEach(loadFeed);
  load();
  feedTimer = setInterval(load, 15000);
}

async function loadFeed(id) {
  try {
    const r = await fetch(`/api/feed/${id}`);
    const j = await r.json();
    const title = $(`ftitle-${id}`), sub = $(`fsub-${id}`), body = $(`fbody-${id}`);
    if (!body) return;
    if (title && j.title) title.textContent = j.title;
    if (sub) sub.textContent = j.example ? `example ${j.example}` : '';
    body.innerHTML = j.ok ? renderAny(j.data) : `<div class="feed-err">${esc(j.error || 'error')}</div>`;
  } catch (e) { const b = $(`fbody-${id}`); if (b) b.innerHTML = `<div class="feed-err">${esc(e.message)}</div>`; }
}

const USD_KEYS = /(value|volume|usd|notional|pnl|account|exposure|delta|size_usd|liquidation)/i;
const PCT_KEYS = /(pct|percent|percentile|rate|ratio|change|funding)/i;

function fmtVal(key, v) {
  if (v == null) return '—';
  if (typeof v === 'number') {
    if (USD_KEYS.test(key)) return usd(v);
    if (PCT_KEYS.test(key)) return numFmt(v, 4);
    return numFmt(v, Number.isInteger(v) ? 0 : 2);
  }
  if (typeof v === 'string') {
    if (/^0x[0-9a-f]{8,}$/i.test(v)) return shortAddr(v);
    const lc = v.toLowerCase();
    if (lc === 'long' || lc === 'buy' || lc === 'b') return `<span class="up">${esc(v)}</span>`;
    if (lc === 'short' || lc === 'sell' || lc === 'a') return `<span class="down">${esc(v)}</span>`;
    return esc(v.length > 48 ? v.slice(0, 47) + '…' : v);
  }
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  return '';
}

// mini bar for a {long,short} or {buy,sell} pair
function pairBar(a, b, la, lb) {
  const t = (a || 0) + (b || 0); if (t <= 0) return '';
  return `<div class="split"><div class="s-long" style="width:${(a / t) * 100}%"></div><div class="s-short" style="width:${(b / t) * 100}%"></div></div>
    <div class="split-lbl"><span class="up">${esc(la)} ${usd(a)}</span><span class="down">${esc(lb)} ${usd(b)}</span></div>`;
}

function renderObj(o) {
  let pre = '';
  const keys = Object.keys(o);
  const has = (k) => keys.some((x) => x.toLowerCase().includes(k));
  if (has('long') && has('short')) {
    const lk = keys.find((k) => /long/i.test(k) && /vol|value|usd/i.test(k)) || keys.find((k) => /long/i.test(k));
    const sk = keys.find((k) => /short/i.test(k) && /vol|value|usd/i.test(k)) || keys.find((k) => /short/i.test(k));
    if (typeof o[lk] === 'number' && typeof o[sk] === 'number') pre += pairBar(o[lk], o[sk], 'long', 'short');
  }
  const rows = keys.map((k) => {
    const v = o[k];
    let cell;
    if (Array.isArray(v)) cell = renderArr(v);
    else if (v && typeof v === 'object') cell = `<div class="nested">${renderObj(v)}</div>`;
    else cell = `<b class="num">${fmtVal(k, v)}</b>`;
    return `<tr><td class="k">${esc(k)}</td><td>${cell}</td></tr>`;
  }).join('');
  return pre + `<table class="kvt">${rows}</table>`;
}

function renderArr(arr) {
  if (!arr.length) return '<span class="dimtxt">empty</span>';
  if (typeof arr[0] !== 'object') return `<span class="dimtxt">${arr.length} items:</span> ${arr.slice(0, 24).map((x) => esc(x)).join(', ')}${arr.length > 24 ? ' …' : ''}`;
  const cols = [...new Set(arr.flatMap((r) => Object.keys(r || {})))].slice(0, 7);
  const head = cols.map((c) => `<th>${esc(c)}</th>`).join('');
  const body = arr.slice(0, 40).map((r) => `<tr>${cols.map((c) => `<td class="num">${fmtVal(c, r[c])}</td>`).join('')}</tr>`).join('');
  return `<div class="tscroll"><table class="rowt"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>${arr.length > 40 ? `<div class="dimtxt">+${arr.length - 40} more</div>` : ''}`;
}

function renderAny(data) {
  if (data == null) return '<span class="dimtxt">no data</span>';
  if (Array.isArray(data)) return renderArr(data);
  if (typeof data === 'object') return renderObj(data);
  return `<b class="num">${esc(data)}</b>`;
}

// ============================================================
window.addEventListener('DOMContentLoaded', () => {
  buildTabs();
  initChart();
  connect();
});
