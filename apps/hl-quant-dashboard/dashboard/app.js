/* HL Quant Dashboard — vanilla JS, no build step, no CDN.
   All data comes from this app's /api/* (which proxies real MoonDev data
   server-side). The UI shows real data or an explicit error — never fakes. */
"use strict";

const $ = (id) => document.getElementById(id);
const COLORS = {
  cyan: "#2de2e6", green: "#3ce88a", red: "#ff5470",
  dim: "#6b7e93", grid: "#1d2a3a", txt: "#c7d4e1", amber: "#ffb454",
};

async function apiGet(fn, params = {}) {
  const qs = new URLSearchParams({ fn, ...params }).toString();
  const r = await fetch(`/api/data?${qs}`);
  return r.json();
}
async function apiPost(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

function setState(el, msg, kind = "") {
  el.textContent = msg || "";
  el.className = "state" + (kind ? " " + kind : "");
}
function fmtNum(x, d = 2) {
  if (x === null || x === undefined || isNaN(x)) return "—";
  const n = Number(x);
  if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(d) + "B";
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(d) + "M";
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(d) + "k";
  return n.toFixed(d);
}
function fmtPct(x) {
  if (x === null || x === undefined || isNaN(x)) return "—";
  return (Number(x) * 100).toFixed(2) + "%";
}
function clsSign(x) { return x > 0 ? "pos" : x < 0 ? "neg" : ""; }

/* ---------- canvas charts ---------- */
function prepCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const h = parseInt(canvas.getAttribute("height"), 10) || 240;
  canvas.width = rect.width * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: rect.width, h };
}

function drawCandles(canvas, candles, stateEl) {
  const { ctx, w, h } = prepCanvas(canvas);
  ctx.clearRect(0, 0, w, h);
  if (!candles || !candles.length) {
    setState(stateEl, "no candles to draw", "err");
    return;
  }
  const pad = { l: 56, r: 10, t: 10, b: 18 };
  const data = candles.map((c) => ({
    t: +c.t, o: +c.o, h: +c.h, l: +c.l, c: +c.c,
  }));
  const lo = Math.min(...data.map((d) => d.l));
  const hi = Math.max(...data.map((d) => d.h));
  const span = hi - lo || 1;
  const xw = (w - pad.l - pad.r) / data.length;
  const yOf = (p) => pad.t + (1 - (p - lo) / span) * (h - pad.t - pad.b);

  // grid + axis labels
  ctx.strokeStyle = COLORS.grid; ctx.fillStyle = COLORS.dim;
  ctx.font = "10px monospace"; ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const v = lo + (span * i) / 4;
    const y = yOf(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.fillText(fmtNum(v, 2), pad.l - 6, y + 3);
  }
  // candles (or thin lines if very dense)
  const drawBody = xw > 2.5;
  for (let i = 0; i < data.length; i++) {
    const d = data[i];
    const x = pad.l + i * xw + xw / 2;
    const up = d.c >= d.o;
    ctx.strokeStyle = up ? COLORS.green : COLORS.red;
    ctx.fillStyle = up ? COLORS.green : COLORS.red;
    ctx.beginPath(); ctx.moveTo(x, yOf(d.h)); ctx.lineTo(x, yOf(d.l)); ctx.stroke();
    if (drawBody) {
      const yo = yOf(d.o), yc = yOf(d.c);
      const top = Math.min(yo, yc), bh = Math.max(1, Math.abs(yc - yo));
      ctx.fillRect(x - xw * 0.3, top, xw * 0.6, bh);
    }
  }
  setState(stateEl, "", "ok");
}

function drawLine(canvas, values, opts = {}) {
  const { ctx, w, h } = prepCanvas(canvas);
  ctx.clearRect(0, 0, w, h);
  if (!values || !values.length) return;
  const pad = { l: 56, r: 10, t: 10, b: 18 };
  const lo = Math.min(...values), hi = Math.max(...values);
  const span = hi - lo || 1;
  const xw = (w - pad.l - pad.r) / (values.length - 1 || 1);
  const yOf = (p) => pad.t + (1 - (p - lo) / span) * (h - pad.t - pad.b);
  ctx.strokeStyle = COLORS.grid; ctx.fillStyle = COLORS.dim;
  ctx.font = "10px monospace"; ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const v = lo + (span * i) / 4, y = yOf(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.fillText((opts.fmt || fmtNum)(v), pad.l - 6, y + 3);
  }
  // baseline at 1.0 (equity) if in range
  if (opts.baseline !== undefined && opts.baseline >= lo && opts.baseline <= hi) {
    ctx.strokeStyle = COLORS.dim; ctx.setLineDash([4, 4]);
    const yb = yOf(opts.baseline);
    ctx.beginPath(); ctx.moveTo(pad.l, yb); ctx.lineTo(w - pad.r, yb); ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.strokeStyle = opts.color || COLORS.cyan; ctx.lineWidth = 1.5;
  ctx.beginPath();
  values.forEach((v, i) => {
    const x = pad.l + i * xw, y = yOf(v);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.stroke(); ctx.lineWidth = 1;
}

/* ---------- generic table render ---------- */
function renderTable(table, rows, columns) {
  const thead = `<thead><tr>${columns.map((c) => `<th>${c.label}</th>`).join("")}</tr></thead>`;
  const body = rows.map((row) =>
    `<tr>${columns.map((c) => {
      const v = c.get(row);
      const cls = c.sign ? clsSign(c.signOf ? c.signOf(row) : v) : "";
      return `<td class="${cls}">${v}</td>`;
    }).join("")}</tr>`).join("");
  table.innerHTML = thead + `<tbody>${body}</tbody>`;
}

/* ---------- config / boot ---------- */
let CONFIG = null;
async function loadConfig() {
  const r = await fetch("/api/config").then((x) => x.json());
  CONFIG = r;
  const keyChip = $("chip-key");
  keyChip.textContent = "key: " + (r.key_present ? "set" : "MISSING");
  keyChip.className = "chip " + (r.key_present ? "ok" : "bad");
  $("chip-vault").textContent = "vault: " + (r.vault_path || "?").split("/").slice(-2).join("/");
  $("chip-vault").title = r.vault_path;

  const intervals = r.intervals || ["1h"];
  for (const sel of ["mkt-interval", "bt-interval"]) {
    $(sel).innerHTML = intervals.map((i) =>
      `<option ${i === "1h" ? "selected" : ""}>${i}</option>`).join("");
  }
  const liqTfs = ["10m", "1h", "4h", "12h", "24h", "2d", "7d", "14d", "30d"];
  $("liq-tf").innerHTML = liqTfs.map((t) =>
    `<option ${t === "1h" ? "selected" : ""}>${t}</option>`).join("");

  const strat = $("bt-strategy");
  strat.innerHTML = Object.keys(r.strategies || {}).map((s) =>
    `<option>${s}</option>`).join("");
  strat.onchange = renderParams;
  renderParams();
}

function renderParams() {
  const name = $("bt-strategy").value;
  const spec = CONFIG.strategies[name];
  const box = $("bt-params");
  box.innerHTML = `<div class="sub">${spec.doc}</div>`;
  for (const [k, meta] of Object.entries(spec.schema)) {
    const def = spec.defaults[k];
    if (meta.type === "bool") {
      box.innerHTML += `<label class="pcheck"><input type="checkbox" data-p="${k}" ${def ? "checked" : ""}/> ${k}</label>`;
    } else {
      box.innerHTML += `<label>${k}<input type="number" data-p="${k}" value="${def}" min="${meta.min ?? ""}" max="${meta.max ?? ""}" step="1"/></label>`;
    }
  }
}
function collectParams() {
  const out = {};
  document.querySelectorAll("#bt-params [data-p]").forEach((el) => {
    out[el.dataset.p] = el.type === "checkbox" ? el.checked : Number(el.value);
  });
  return out;
}

/* ---------- panels ---------- */
async function loadMarkets() {
  const coin = $("mkt-coin").value.trim().toUpperCase() || "BTC";
  const interval = $("mkt-interval").value;
  setState($("mkt-chart-state"), "loading candles…");
  $("mkt-candle-meta").textContent = `${coin} ${interval}`;
  const env = await apiGet("candles", { coin, interval });
  if (!env.ok) {
    setState($("mkt-chart-state"), `error: ${env.error}`, "err");
    drawCandles($("mkt-chart"), [], $("mkt-chart-state"));
    return;
  }
  const candles = env.data || [];
  drawCandles($("mkt-chart"), candles, $("mkt-chart-state"));
  if (candles.length) {
    const last = candles[candles.length - 1];
    setState($("mkt-chart-state"), `${candles.length} bars · last close ${fmtNum(+last.c, 2)}`, "ok");
  }
}

async function loadPrices() {
  setState($("mkt-prices-state"), "loading prices…");
  const env = await apiGet("prices");
  if (!env.ok) { setState($("mkt-prices-state"), `error: ${env.error}`, "err"); return; }
  let rows = env.data;
  // normalize: accept {COIN:{...}} or [{coin,...}] or {data:[...]}
  if (rows && rows.data) rows = rows.data;
  let list = [];
  if (Array.isArray(rows)) list = rows;
  else if (rows && typeof rows === "object")
    list = Object.entries(rows).map(([k, v]) =>
      (v && typeof v === "object") ? { coin: k, ...v } : { coin: k, value: v });
  if (!list.length) { setState($("mkt-prices-state"), "empty payload", "err"); return; }
  const pick = (o, keys) => { for (const k of keys) if (o[k] !== undefined) return o[k]; return undefined; };
  list = list.slice(0, 60);
  renderTable($("mkt-prices"), list, [
    { label: "coin", get: (r) => r.coin || r.symbol || "?" },
    { label: "price", get: (r) => fmtNum(pick(r, ["price", "mid", "mark", "markPx", "value"]), 4) },
    { label: "funding", get: (r) => { const f = pick(r, ["funding", "fundingRate", "funding_rate"]); return f === undefined ? "—" : fmtPct(f); }, sign: true, signOf: (r) => Number(pick(r, ["funding", "fundingRate", "funding_rate"]) || 0) },
    { label: "OI", get: (r) => fmtNum(pick(r, ["oi", "openInterest", "open_interest"]), 2) },
  ]);
  setState($("mkt-prices-state"), `${list.length} symbols`, "ok");
}

async function loadLiquidations() {
  const tf = $("liq-tf").value;
  setState($("liq-state"), "loading…");
  const env = await apiGet("liquidations", { timeframe: tf });
  if (!env.ok) { setState($("liq-state"), `error: ${env.error}`, "err"); return; }
  let data = env.data;
  let list = Array.isArray(data) ? data : (data && (data.liquidations || data.data || data.events)) || [];
  if (!Array.isArray(list)) list = [];
  $("liq-meta").textContent = `${tf} · ${list.length} events`;
  if (!list.length) { setState($("liq-state"), "no events / unexpected shape", "err"); $("liq-table").innerHTML = ""; return; }
  const pick = (o, ks) => { for (const k of ks) if (o[k] !== undefined) return o[k]; };
  renderTable($("liq-table"), list.slice(0, 200), [
    { label: "coin", get: (r) => pick(r, ["coin", "symbol", "asset"]) || "?" },
    { label: "side", get: (r) => pick(r, ["side", "dir"]) || "—" },
    { label: "size $", get: (r) => fmtNum(pick(r, ["usd", "notional", "value", "size_usd", "sz"]), 1) },
    { label: "price", get: (r) => fmtNum(pick(r, ["price", "px"]), 4) },
  ]);
  setState($("liq-state"), "", "ok");
}

async function loadWhales() {
  setState($("whale-state"), "loading…");
  const env = await apiGet("whales");
  if (!env.ok) { setState($("whale-state"), `error: ${env.error}`, "err"); return; }
  let data = env.data;
  let list = Array.isArray(data) ? data : (data && (data.whales || data.trades || data.data)) || [];
  if (!Array.isArray(list)) list = [];
  if (!list.length) { setState($("whale-state"), "no whale trades / unexpected shape", "err"); $("whale-table").innerHTML = ""; return; }
  const pick = (o, ks) => { for (const k of ks) if (o[k] !== undefined) return o[k]; };
  renderTable($("whale-table"), list.slice(0, 200), [
    { label: "coin", get: (r) => pick(r, ["coin", "symbol", "asset"]) || "?" },
    { label: "side", get: (r) => pick(r, ["side", "dir"]) || "—" },
    { label: "size $", get: (r) => fmtNum(pick(r, ["usd", "notional", "value", "size_usd"]), 1) },
    { label: "price", get: (r) => fmtNum(pick(r, ["price", "px"]), 4) },
  ]);
  setState($("whale-state"), `${list.length} trades`, "ok");
}

async function loadJsonInto(fn, params, outId, stateId) {
  setState($(stateId), "loading…");
  const env = await apiGet(fn, params);
  if (!env.ok) { setState($(stateId), `error: ${env.error}`, "err"); $(outId).textContent = ""; return; }
  $(outId).textContent = JSON.stringify(env.data, null, 2);
  setState($(stateId), "", "ok");
}

/* ---------- backtest ---------- */
let LAST_RESULT = null;
async function runBacktest() {
  const body = {
    coin: $("bt-coin").value.trim().toUpperCase() || "BTC",
    interval: $("bt-interval").value,
    strategy: $("bt-strategy").value,
    params: collectParams(),
    fee_bps: Number($("bt-fee").value),
    slippage_bps: Number($("bt-slip").value),
    cost_mult: Number($("bt-costmult").value),
    oos_frac: Number($("bt-oos").value),
  };
  setState($("bt-state"), "running on real candles…");
  $("bt-export").disabled = true;
  const r = await apiPost("/api/backtest/run", body);
  if (!r.ok) {
    setState($("bt-state"), `failed (${r.stage || "?"}): ${r.error}`, "err");
    return;
  }
  LAST_RESULT = r.result;
  renderResult(r.result);
  setState($("bt-state"), `done · status=${r.result.status} · ${r.result.n_bars} bars`, "ok");
  $("bt-export").disabled = false;
}

function renderResult(res) {
  $("bt-result-meta").textContent =
    `${res.symbol} ${res.interval} · ${res.strategy} · ${res.n_bars} bars`;
  drawLine($("bt-chart"), res.equity_curve, {
    color: COLORS.cyan, baseline: 1.0,
    fmt: (v) => v.toFixed(3),
  });
  const mi = res.metrics_is || {}, mo = res.metrics_oos || {};
  const row = (name, a, b) => `<tr><td>${name}</td><td>${a}</td><td>${b}</td></tr>`;
  $("bt-metrics").innerHTML =
    `<thead><tr><th>metric</th><th>IS</th><th>OOS</th></tr></thead><tbody>` +
    row("return", fmtPct(mi.return), fmtPct(mo.return)) +
    row("CAGR", fmtPct(mi.cagr), fmtPct(mo.cagr)) +
    row("Sharpe", fmtNum(mi.sharpe, 2), fmtNum(mo.sharpe, 2)) +
    row("max DD", fmtPct(mi.max_drawdown), fmtPct(mo.max_drawdown)) +
    row("trades", fmtNum(mi.n_trades, 0), "—") +
    row("win rate", fmtPct(mi.win_rate), "—") +
    row("exposure", fmtPct(mi.exposure), fmtPct(mo.exposure)) +
    `</tbody>`;
  if ((mo.sharpe || 0) > 3) {
    setState($("bt-state"), "OOS Sharpe > 3 — treat as a likely bug.", "err");
  }
}

async function exportVault() {
  if (!LAST_RESULT) return;
  $("bt-export-state").textContent = "writing…";
  const r = await apiPost("/api/backtest/export", {
    result: LAST_RESULT, notes: $("bt-notes").value,
  });
  $("bt-export-state").textContent = r.ok ? `saved → ${r.path}` : `error: ${r.error}`;
}

/* ---------- wiring ---------- */
function initTabs() {
  document.querySelectorAll(".tab").forEach((t) => {
    t.onclick = () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      document.querySelectorAll(".panel-page").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      $("page-" + t.dataset.tab).classList.add("active");
    };
  });
}
function startClock() {
  const tick = () => {
    $("chip-clock").textContent =
      new Date().toISOString().slice(11, 19) + " UTC";
  };
  tick(); setInterval(tick, 1000);
}

window.addEventListener("DOMContentLoaded", async () => {
  initTabs(); startClock();
  await loadConfig();
  $("mkt-load").onclick = loadMarkets;
  $("mkt-refresh-prices").onclick = loadPrices;
  $("liq-load").onclick = loadLiquidations;
  $("whale-load").onclick = loadWhales;
  $("hlp-load").onclick = () => loadJsonInto("hlp_positions", {}, "hlp-out", "hlp-state");
  $("hlp-sent").onclick = () => loadJsonInto("hlp_sentiment", {}, "hlp-out", "hlp-state");
  $("sm-rank").onclick = () => loadJsonInto("smart_money_rankings", {}, "sm-out", "sm-state");
  $("sm-board").onclick = () => loadJsonInto("smart_money_leaderboard", {}, "sm-out", "sm-state");
  $("bt-run").onclick = runBacktest;
  $("bt-export").onclick = exportVault;
  // initial paint
  loadMarkets(); loadPrices();
});
