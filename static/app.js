/* OpenTrade front-end: charts, indicators, strategy tester, compare, optimizer, editor. */
const LC = LightweightCharts;
const $ = (sel) => document.querySelector(sel);
const COLORS = ["#2962FF", "#FF9800", "#E040FB", "#00BCD4", "#FFEB3B", "#4CAF50", "#FF5252", "#9C27B0"];

const S = {
  symbol: "BTC", interval: "1d", candles: [], times: [], plugins: [],
  indicators: [],          // {uid, file, params, res, series[], chart?, el?}
  strategy: null,          // {file, params, res}
  strategySeries: [], strategyMarkers: [], indicatorMarkers: [],
  subCharts: [], equityChart: null, compareChart: null,
  editor: { kind: null, file: null },
};

// ------------------------------------------------------------------ utils
async function api(path, body, method) {
  const opt = body !== undefined ? { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
                                 : { method: method || "GET" };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ? (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)) : r.statusText);
  return j;
}
function status(msg, err) { const el = $("#status"); el.textContent = msg || ""; el.className = "status" + (err ? " err" : ""); }
function fmt(v, d = 2) { return v === null || v === undefined ? "—" : typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d }) : v; }
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function uid() { return Math.random().toString(36).slice(2, 9); }

const chartOpts = (extra = {}) => ({
  autoSize: true,
  layout: { background: { type: "solid", color: "#131722" }, textColor: "#d1d4dc", fontSize: 11 },
  grid: { vertLines: { color: "#1f2330" }, horzLines: { color: "#1f2330" } },
  rightPriceScale: { borderColor: "#363a45", minimumWidth: 70 },
  timeScale: { borderColor: "#363a45", timeVisible: true, secondsVisible: false },
  crosshair: { mode: LC.CrosshairMode.Normal },
  ...extra,
});

// Pad a plot to every candle time with whitespace so all panes share bar indices (keeps scrolling in sync).
function pad(data) {
  const m = new Map(data.map(p => [p.time, p.value]));
  return S.times.map(t => (m.has(t) ? { time: t, value: m.get(t) } : { time: t }));
}

// ------------------------------------------------------------------ main chart
const priceChart = LC.createChart($("#priceChart"), chartOpts());
const candleSeries = priceChart.addCandlestickSeries({
  upColor: "#26a69a", downColor: "#ef5350", borderVisible: false, wickUpColor: "#26a69a", wickDownColor: "#ef5350",
});
const volumeSeries = priceChart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
priceChart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

// Fit after the browser has laid out the (possibly just-unhidden) element, otherwise width is 0.
function fitLater(chart) { requestAnimationFrame(() => requestAnimationFrame(() => chart && chart.timeScale().fitContent())); }
function fitAll() {
  priceChart.timeScale().resetTimeScale();
  priceChart.priceScale("right").applyOptions({ autoScale: true });
  fitLater(priceChart);
}

let syncing = false;
function syncFrom(src) {
  src.timeScale().subscribeVisibleLogicalRangeChange(range => {
    if (syncing || !range) return;
    syncing = true;
    [priceChart, ...S.subCharts].forEach(c => { if (c !== src) c.timeScale().setVisibleLogicalRange(range); });
    syncing = false;
  });
}
syncFrom(priceChart);

priceChart.subscribeCrosshairMove(p => {
  const d = p.seriesData && p.seriesData.get(candleSeries);
  const c = d || S.candles[S.candles.length - 1];
  if (!c) return;
  const chg = ((c.close - c.open) / c.open) * 100;
  $("#ohlc").innerHTML = `${esc(S.symbol)} &nbsp; O <b>${fmt(c.open)}</b> H <b>${fmt(c.high)}</b> L <b>${fmt(c.low)}</b> C <b>${fmt(c.close)}</b> <span class="${chg >= 0 ? "pos" : "neg"}">${chg >= 0 ? "+" : ""}${fmt(chg)}%</span>`;
});

function refreshMarkers() {
  const all = [...S.indicatorMarkers, ...S.strategyMarkers].sort((a, b) => a.time - b.time);
  candleSeries.setMarkers(all);
}

// ------------------------------------------------------------------ data
const SYMBOL_LABELS = { BTC: "BTC — Bitcoin / USD", GOLD: "GOLD — Gold / USD" };
const INTERVAL_LABELS = { "1h": "1 hour", "4h": "4 hours", "1d": "1 day", "1wk": "1 week" };

async function loadSymbols(select) {
  const { symbols, intervals } = await api("/api/symbols");
  $("#symbol").innerHTML = symbols.map(s => `<option value="${esc(s)}">${esc(SYMBOL_LABELS[s] || s)}</option>`).join("")
    + `<option value="__add">＋ Add ticker…</option>`;
  $("#symbol").value = select || S.symbol;
  if (!$("#interval").options.length) {
    $("#interval").innerHTML = intervals.map(i => `<option value="${i}">${INTERVAL_LABELS[i] || i}</option>`).join("");
    $("#interval").value = S.interval;
  }
}

async function refreshData(symbol) {
  const btn = $("#refreshData"); btn.disabled = true;
  status(`Downloading latest ${symbol} ${S.interval} prices…`);
  try {
    const r = await api("/api/refresh", { symbol, interval: S.interval });
    status(`${r.symbol}: ${r.bars} bars, up to ${r.last.slice(0, 16).replace("T", " ")}`);
    return r.symbol;
  } finally { btn.disabled = false; }
}

async function loadSymbol(sym) {
  status("Loading " + sym + "…");
  S.symbol = sym;
  const { candles } = await api(`/api/candles?symbol=${encodeURIComponent(sym)}&interval=${S.interval}`);
  S.candles = candles;
  S.times = candles.map(c => c.time);
  candleSeries.setData(candles);
  volumeSeries.setData(candles.map(c => ({ time: c.time, value: c.volume, color: c.close >= c.open ? "rgba(38,166,154,.35)" : "rgba(239,83,80,.35)" })));
  priceChart.timeScale().fitContent();
  // Re-run everything on the new data
  for (const ind of S.indicators) await runIndicator(ind);
  if (S.strategy) await selectStrategy(S.strategy.file, S.strategy.params);
  fitAll();
  status("");
}

$("#symbol").addEventListener("change", async e => {
  try {
    if (e.target.value === "__add") {
      const t = prompt("Yahoo Finance ticker to add (e.g. ETH-USD, AAPL, SI=F for silver, EURUSD=X):");
      if (!t) { e.target.value = S.symbol; return; }
      const sym = await refreshData(t.trim());
      await loadSymbols(sym);
      return loadSymbol(sym);
    }
    await loadSymbol(e.target.value);
  } catch (err) { e.target.value = S.symbol; status(err.message, true); }
});
$("#interval").addEventListener("change", async e => {
  const prev = S.interval;
  S.interval = e.target.value;
  try { await loadSymbol(S.symbol); }
  catch (err) { S.interval = prev; e.target.value = prev; status(err.message, true); }
});
$("#refreshData").addEventListener("click", async () => {
  try { await refreshData(S.symbol); const msg = $("#status").textContent; await loadSymbol(S.symbol); status(msg); }
  catch (err) { status(err.message, true); }
});
$("#csvUpload").addEventListener("change", async e => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail);
    await loadSymbols(j.symbol);
    await loadSymbol(j.symbol);
  } catch (err) { status(err.message, true); }
  e.target.value = "";
});

// ------------------------------------------------------------------ plugins list
async function loadPlugins() {
  S.plugins = (await api("/api/plugins")).plugins;
  renderIndicatorList();
  const strategies = S.plugins.filter(p => p.kind === "strategy");
  const opts = strategies.map(p => `<option value="${esc(p.file)}">${esc(p.name)}</option>`).join("");
  $("#strategySelect").innerHTML = `<option value="">— none —</option>` + opts;
  $("#optStrategy").innerHTML = opts;
  if (S.strategy) $("#strategySelect").value = S.strategy.file;
  $("#compareList").innerHTML = strategies.map(p =>
    `<label class="chip"><input type="checkbox" value="${esc(p.file)}" checked> ${esc(p.name)}</label>`).join("");
  $("#edFile").innerHTML = S.plugins.map(p => `<option value="${p.kind}/${esc(p.file)}">${p.kind === "indicator" ? "📈" : "🧪"} ${esc(p.file)}</option>`).join("");
  if (S.editor.file) $("#edFile").value = `${S.editor.kind}/${S.editor.file}`;
}

function renderIndicatorList() {
  const inds = S.plugins.filter(p => p.kind === "indicator");
  $("#indicatorList").innerHTML = inds.map(p => {
    const active = S.indicators.filter(i => i.file === p.file);
    return `<div class="item">
      <span class="name" title="${esc(p.file)}">${esc(p.name)}</span><span class="lang">${p.lang === "pine" ? "PINE" : "PY"}</span>
      ${active.map(a => `<button title="Settings" data-gear="${a.uid}">⚙</button><button title="Remove" data-remove="${a.uid}">✕</button>`).join("")}
      <button title="Add to chart" data-add="${esc(p.file)}">＋</button></div>`;
  }).join("");
}

$("#indicatorList").addEventListener("click", async e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.add) await addIndicator(b.dataset.add).catch(err => status(err.message, true));
  if (b.dataset.remove) removeIndicator(b.dataset.remove);
  if (b.dataset.gear) {
    const ind = S.indicators.find(i => i.uid === b.dataset.gear);
    openParams(ind.res.title, ind.res.inputs, async vals => { ind.params = vals; await runIndicator(ind); });
  }
});

// ------------------------------------------------------------------ indicators
async function addIndicator(file, params = {}) {
  const ind = { uid: uid(), file, params, series: [] };
  S.indicators.push(ind);
  await runIndicator(ind);
  renderIndicatorList();
}

function clearIndicatorDrawing(ind) {
  if (ind.chart) {
    S.subCharts = S.subCharts.filter(c => c !== ind.chart);
    ind.chart.remove(); ind.el.remove(); ind.chart = null; ind.el = null;
  } else {
    ind.series.forEach(s => priceChart.removeSeries(s));
  }
  ind.series = [];
  ind.markers = [];
}

function removeIndicator(id) {
  const ind = S.indicators.find(i => i.uid === id);
  if (!ind) return;
  clearIndicatorDrawing(ind);
  S.indicators = S.indicators.filter(i => i.uid !== id);
  rebuildIndicatorMarkers();
  renderIndicatorList();
}

function rebuildIndicatorMarkers() {
  S.indicatorMarkers = S.indicators.flatMap(i => i.markers || []);
  refreshMarkers();
}

async function runIndicator(ind, code) {
  status("Running " + ind.file + "…");
  const res = await api("/api/run", { symbol: S.symbol, interval: S.interval, kind: "indicator", file: ind.file, params: ind.params, code });
  clearIndicatorDrawing(ind);
  ind.res = res;
  ind.params = Object.fromEntries(res.inputs.map(i => [i.name, i.value]));
  let chart = priceChart;
  if (!res.overlay) {
    ind.el = document.createElement("div");
    ind.el.innerHTML = `<span class="pane-label">${esc(res.title)}</span>`;
    $("#panes").appendChild(ind.el);
    chart = LC.createChart(ind.el, chartOpts({ timeScale: { visible: false, borderColor: "#363a45" } }));
    ind.chart = chart;
    S.subCharts.push(chart);
    syncFrom(chart);
    const r = priceChart.timeScale().getVisibleLogicalRange();
    if (r) setTimeout(() => chart.timeScale().setVisibleLogicalRange(r), 0);
  }
  res.plots.forEach(p => {
    const s = p.style === "histogram"
      ? chart.addHistogramSeries({ color: p.color, title: p.title, priceLineVisible: false })
      : chart.addLineSeries({ color: p.color, lineWidth: p.linewidth || 1, title: p.title, priceLineVisible: false, lastValueVisible: !res.overlay });
    s.setData(pad(p.data));
    ind.series.push(s);
  });
  if (res.hlines.length && ind.series[0]) {
    res.hlines.forEach(h => ind.series[0].createPriceLine({ price: h.price, color: h.color, lineStyle: 2, lineWidth: 1, axisLabelVisible: false, title: "" }));
  }
  ind.markers = res.markers;
  rebuildIndicatorMarkers();
  if (res.warnings.length) status(res.warnings.join(" · ")); else status("");
  return res;
}

// ------------------------------------------------------------------ strategy
function clearStrategyDrawing() {
  S.strategySeries.forEach(s => priceChart.removeSeries(s));
  S.strategySeries = [];
  S.strategyMarkers = [];
  refreshMarkers();
}

function drawStrategyPlots(res) {
  clearStrategyDrawing();
  res.plots.forEach(p => {
    const s = priceChart.addLineSeries({ color: p.color, lineWidth: p.linewidth || 1, title: p.title, priceLineVisible: false, lastValueVisible: false, lineStyle: 0 });
    s.setData(pad(p.data));
    S.strategySeries.push(s);
  });
  S.strategyMarkers = [...(res.markers || []), ...(res.trade_markers || [])];
  refreshMarkers();
}

function renderInputs(container, inputs) {
  container.innerHTML = inputs.map(i => {
    const n = esc(i.name);
    if (i.type === "bool") return `<label class="check"><input type="checkbox" data-name="${n}" ${i.value ? "checked" : ""}> ${n}</label>`;
    if (i.options) return `<label>${n}<select data-name="${n}">${i.options.map(o => `<option ${o == i.value ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></label>`;
    const type = i.type === "int" || i.type === "float" ? "number" : "text";
    return `<label>${n}<input type="${type}" step="${i.type === "int" ? 1 : "any"}" data-name="${n}" value="${esc(i.value ?? "")}"></label>`;
  }).join("");
}

function readInputs(container) {
  const out = {};
  container.querySelectorAll("[data-name]").forEach(el => {
    out[el.dataset.name] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
  });
  return out;
}

async function selectStrategy(file, params = {}, code) {
  if (!file) { S.strategy = null; $("#strategyInputs").innerHTML = ""; clearStrategyDrawing(); return; }
  const res = await api("/api/run", { symbol: S.symbol, interval: S.interval, kind: "strategy", file, params, code });
  S.strategy = { file, params: Object.fromEntries(res.inputs.map(i => [i.name, i.value])), res };
  $("#strategySelect").value = file;
  renderInputs($("#strategyInputs"), res.inputs);
  drawStrategyPlots(res);
}

$("#strategySelect").addEventListener("change", e => selectStrategy(e.target.value).catch(err => status(err.message, true)));
$("#clearStrategy").addEventListener("click", () => { $("#strategySelect").value = ""; selectStrategy(""); });

function readSettings() {
  const out = {};
  $("#settings").querySelectorAll("[name]").forEach(el => {
    out[el.name] = el.type === "checkbox" ? el.checked : el.value;
  });
  return out;
}

async function runBacktest(code) {
  const file = $("#strategySelect").value;
  if (!file) { status("Choose a strategy first", true); return; }
  const btn = $("#runBacktest"); btn.disabled = true;
  status("Backtesting…");
  try {
    const st = readSettings();
    const { start, end, ...settings } = st;
    const res = await api("/api/backtest", { symbol: S.symbol, interval: S.interval, file, params: readInputs($("#strategyInputs")), settings, start: start || null, end: end || null, code });
    S.strategy = { file, params: Object.fromEntries(res.inputs.map(i => [i.name, i.value])), res };
    renderInputs($("#strategyInputs"), res.inputs);
    drawStrategyPlots(res);
    renderTester(res);
    switchTab("tester");
    status(`${res.title}: ${res.metrics.total_trades} trades`);
  } catch (err) { status(err.message, true); }
  finally { btn.disabled = false; }
}
$("#runBacktest").addEventListener("click", () => runBacktest());

const METRIC_LABELS = [
  ["net_profit", "Net profit", 2, true], ["total_return_pct", "Return %", 2, true], ["buy_hold_return_pct", "Buy & hold %", 2, true],
  ["cagr_pct", "CAGR %", 2, true], ["max_drawdown_pct", "Max drawdown %", 2, true], ["sharpe", "Sharpe", 2, true],
  ["sortino", "Sortino", 2, true], ["profit_factor", "Profit factor", 2], ["total_trades", "Trades", 0],
  ["win_rate_pct", "Win rate %", 1], ["avg_trade", "Avg trade", 2, true], ["avg_bars_in_trade", "Avg bars held", 1],
  ["largest_win", "Largest win", 2, true], ["largest_loss", "Largest loss", 2, true], ["exposure_pct", "Time in market %", 1],
  ["final_equity", "Final equity", 2],
];

function renderTester(res) {
  $("#testerEmpty").hidden = true; $("#testerContent").hidden = false;
  const m = res.metrics;
  $("#metrics").innerHTML = METRIC_LABELS.map(([k, label, d, signed]) => {
    const v = m[k]; const cls = signed && typeof v === "number" ? (v >= 0 ? "pos" : "neg") : "";
    return `<div class="metric"><div class="k">${label}</div><div class="v ${cls}">${fmt(v, d)}</div></div>`;
  }).join("");

  if (!S.equityChart) {
    S.equityChart = LC.createChart($("#equityChart"), chartOpts());
    S.equitySeries = S.equityChart.addAreaSeries({ lineColor: "#2962FF", topColor: "rgba(41,98,255,.35)", bottomColor: "rgba(41,98,255,0)", lineWidth: 2, title: "Equity" });
    S.bhSeries = S.equityChart.addLineSeries({ color: "#787B86", lineWidth: 1, lineStyle: 2, title: "Buy & hold", priceLineVisible: false });
  }
  S.equitySeries.setData(res.equity);
  const c0 = res.equity.length ? S.candles.find(c => c.time >= res.equity[0].time) : null;
  const cap = Number(res.settings.initial_capital);
  if (c0) {
    const closeByTime = new Map(S.candles.map(c => [c.time, c.close]));
    S.bhSeries.setData(res.equity.map(e => ({ time: e.time, value: (closeByTime.get(e.time) / c0.close) * cap })));
  }
  fitLater(S.equityChart);

  const rows = res.trades.slice().reverse().map((t, i) => `<tr>
      <td>${res.trades.length - i}</td><td class="${t.direction === "long" ? "pos" : "neg"}">${t.direction}</td>
      <td>${t.entry_time.slice(0, 16).replace("T", " ")}</td><td>${fmt(t.entry_price)}</td>
      <td>${t.exit_time.slice(0, 16).replace("T", " ")}</td><td>${fmt(t.exit_price)}</td>
      <td class="${t.pnl >= 0 ? "pos" : "neg"}">${fmt(t.pnl)}</td><td class="${t.pnl >= 0 ? "pos" : "neg"}">${fmt(t.pnl_pct)}%</td>
      <td>${t.bars}</td><td>${t.exit_reason.replace(/_/g, " ")}</td></tr>`).join("");
  $("#tradesTable").innerHTML = `<thead><tr><th>#</th><th>Side</th><th>Entry</th><th>Price</th><th>Exit</th><th>Price</th><th>P&amp;L</th><th>%</th><th>Bars</th><th>Reason</th></tr></thead><tbody>${rows || `<tr><td colspan="10">No trades</td></tr>`}</tbody>`;
  $("#btWarnings").textContent = (res.warnings || []).join(" · ");
}

// ------------------------------------------------------------------ compare
$("#compareRun").addEventListener("click", async () => {
  const files = [...$("#compareList").querySelectorAll("input:checked")].map(i => i.value);
  if (!files.length) { status("Tick at least one strategy", true); return; }
  status("Comparing " + files.length + " strategies…");
  const { start, end, ...settings } = readSettings();
  try {
    const strategies = files.map(f => ({ file: f, params: S.strategy && S.strategy.file === f ? readInputs($("#strategyInputs")) : {} }));
    const res = await api("/api/compare", { symbol: S.symbol, interval: S.interval, strategies, settings, start: start || null, end: end || null });
    renderCompare(res);
    status("");
  } catch (err) { status(err.message, true); }
});

function renderCompare(res) {
  if (S.compareChart) S.compareChart.remove();
  S.compareChart = LC.createChart($("#compareChart"), chartOpts());
  const ok = res.results.filter(r => !r.error);
  ok.forEach((r, i) => {
    r.color = COLORS[i % COLORS.length];
    S.compareChart.addLineSeries({ color: r.color, lineWidth: 2, title: r.title, priceLineVisible: false }).setData(r.equity);
  });
  S.compareChart.addLineSeries({ color: "#787B86", lineWidth: 1, lineStyle: 2, title: "Buy & hold", priceLineVisible: false }).setData(res.buy_hold);
  fitLater(S.compareChart);

  const cols = [["total_return_pct", "Return %"], ["cagr_pct", "CAGR %"], ["max_drawdown_pct", "Max DD %"], ["sharpe", "Sharpe"],
    ["sortino", "Sortino"], ["profit_factor", "PF"], ["total_trades", "Trades"], ["win_rate_pct", "Win %"], ["exposure_pct", "Exposure %"]];
  const best = ok.length ? ok.reduce((a, b) => ((b.metrics.sharpe ?? -1e9) > (a.metrics.sharpe ?? -1e9) ? b : a)) : null;
  const body = res.results.map(r => r.error
    ? `<tr><td>${esc(r.title)}</td><td colspan="${cols.length}" class="neg" style="text-align:left">${esc(r.error)}</td></tr>`
    : `<tr class="${r === best ? "best" : ""}"><td><span class="swatch" style="background:${r.color}"></span>${esc(r.title)}${r === best ? " ★" : ""}</td>
       ${cols.map(([k]) => { const v = r.metrics[k]; const signed = ["total_return_pct", "cagr_pct", "max_drawdown_pct", "sharpe", "sortino"].includes(k);
         return `<td class="${signed && v !== null ? (v >= 0 ? "pos" : "neg") : ""}">${fmt(v, k === "total_trades" ? 0 : 2)}</td>`; }).join("")}</tr>`).join("");
  $("#compareTable").innerHTML = `<thead><tr><th>Strategy (★ = best Sharpe)</th>${cols.map(c => `<th>${c[1]}</th>`).join("")}</tr></thead><tbody>${body}</tbody>`;
}

// ------------------------------------------------------------------ optimize
async function loadOptFields() {
  const file = $("#optStrategy").value;
  if (!file) return;
  try {
    const res = await api("/api/run", { symbol: S.symbol, interval: S.interval, kind: "strategy", file, params: {} });
    $("#optGrid").innerHTML = res.inputs.filter(i => i.type === "int" || i.type === "float").map(i =>
      `<label>${esc(i.name)} (default ${esc(i.default)})<input type="text" data-opt="${esc(i.name)}" data-type="${i.type}" placeholder="e.g. ${i.type === "int" ? `${i.value}:${i.value * 3}:${Math.max(1, Math.round(i.value / 2))}` : "1, 2, 3"}"></label>`).join("")
      || `<span class="hint">This strategy has no numeric inputs.</span>`;
  } catch (err) { $("#optGrid").innerHTML = `<span class="neg">${esc(err.message)}</span>`; }
}
$("#optStrategy").addEventListener("change", loadOptFields);

function parseValues(text, type) {
  text = text.trim();
  if (!text) return null;
  let vals;
  if (text.includes(":")) {
    const [a, b, step = 1] = text.split(":").map(Number);
    vals = [];
    for (let v = a; v <= b + 1e-9 && vals.length < 200; v += step) vals.push(+v.toFixed(6));
  } else vals = text.split(/[,\s]+/).filter(Boolean).map(Number);
  return vals.filter(v => !isNaN(v)).map(v => (type === "int" ? Math.round(v) : v));
}

$("#optRun").addEventListener("click", async () => {
  const grid = {};
  $("#optGrid").querySelectorAll("[data-opt]").forEach(el => { const v = parseValues(el.value, el.dataset.type); if (v && v.length) grid[el.dataset.opt] = v; });
  if (!Object.keys(grid).length) { status("Enter values for at least one parameter", true); return; }
  const { start, end, ...settings } = readSettings();
  status("Optimizing…");
  try {
    const res = await api("/api/optimize", { symbol: S.symbol, interval: S.interval, file: $("#optStrategy").value, grid, settings, sort_by: $("#optSort").value, start: start || null, end: end || null });
    const keys = Object.keys(grid);
    const cols = [["total_return_pct", "Return %"], ["max_drawdown_pct", "Max DD %"], ["sharpe", "Sharpe"], ["profit_factor", "PF"], ["total_trades", "Trades"], ["win_rate_pct", "Win %"]];
    $("#optTable").innerHTML = `<thead><tr>${keys.map(k => `<th>${esc(k)}</th>`).join("")}${cols.map(c => `<th>${c[1]}</th>`).join("")}<th></th></tr></thead><tbody>` +
      res.rows.map((r, i) => `<tr class="${i === 0 ? "best" : ""}">${keys.map(k => `<td>${r.params[k]}</td>`).join("")}
        ${r.error ? `<td colspan="${cols.length}" class="neg">${esc(r.error)}</td>` : cols.map(([k]) => `<td>${fmt(r.metrics[k], k === "total_trades" ? 0 : 2)}</td>`).join("")}
        <td><button class="btn ghost" data-apply='${esc(JSON.stringify(r.params))}'>Use</button></td></tr>`).join("") + "</tbody>";
    status(`Tested ${res.count} combinations — beware of over-fitting: confirm on a different date range.`);
  } catch (err) { status(err.message, true); }
});

$("#optTable").addEventListener("click", async e => {
  const b = e.target.closest("[data-apply]"); if (!b) return;
  const params = JSON.parse(b.dataset.apply);
  await selectStrategy($("#optStrategy").value, { ...(S.strategy && S.strategy.file === $("#optStrategy").value ? S.strategy.params : {}), ...params });
  runBacktest();
});

// ------------------------------------------------------------------ editor
const TEMPLATES = {
  "indicator.pine": `//@version=5\nindicator("My Indicator", overlay=true)\nlength = input.int(20, "Length")\nplot(ta.sma(close, length), "SMA", color=color.orange)\n`,
  "strategy.pine": `//@version=5\nstrategy("My Strategy", overlay=true)\nfast = ta.ema(close, input.int(9, "Fast"))\nslow = ta.ema(close, input.int(21, "Slow"))\nplot(fast, "Fast", color=color.lime)\nplot(slow, "Slow", color=color.red)\n\nif ta.crossover(fast, slow)\n    strategy.entry("Long", strategy.long)\nif ta.crossunder(fast, slow)\n    strategy.close("Long")\n`,
  "indicator.py": `from app.core import ta\n\nNAME = "My Indicator"\nOVERLAY = True\nPARAMS = {"length": 20}\n\n\ndef compute(df, length=20):\n    return {"SMA": ta.sma(df["close"], length)}\n`,
  "strategy.py": `from app.core import ta\n\nNAME = "My Strategy"\nPARAMS = {"fast": 9, "slow": 21}\n\n\ndef generate_signals(df, fast=9, slow=21):\n    f, s = ta.ema(df["close"], fast), ta.ema(df["close"], slow)\n    return {\n        "long_entry": ta.crossover(f, s),\n        "long_exit": ta.crossunder(f, s),\n        "plots": {"Fast": f, "Slow": s},\n    }\n`,
};

const code = $("#edCode"), gutter = $("#edGutter");
function updateGutter() {
  const n = code.value.split("\n").length;
  gutter.textContent = Array.from({ length: n }, (_, i) => i + 1).join("\n");
  gutter.scrollTop = code.scrollTop;
}
code.addEventListener("input", updateGutter);
code.addEventListener("scroll", () => (gutter.scrollTop = code.scrollTop));
code.addEventListener("keydown", e => {
  if (e.key === "Tab") {
    e.preventDefault();
    const { selectionStart: s, selectionEnd: en } = code;
    code.setRangeText("    ", s, en, "end"); updateGutter();
  }
  if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); $("#edRun").click(); }
});

function consoleMsg(msg, kind) { const c = $("#edConsole"); c.textContent = msg; c.className = "console " + (kind || ""); }

async function openInEditor(kind, file) {
  const { code: src } = await api(`/api/plugins/${kind}/${encodeURIComponent(file)}`);
  S.editor = { kind, file };
  code.value = src; updateGutter();
  $("#edFile").value = `${kind}/${file}`;
  consoleMsg("");
}
$("#edFile").addEventListener("change", e => { const [k, ...f] = e.target.value.split("/"); openInEditor(k, f.join("/")); });

$("#edNew").addEventListener("click", async () => {
  const name = prompt("File name — end with .pine or .py and prefix with 'indicator:' or 'strategy:'\nExample: strategy:my_breakout.pine", "indicator:my_indicator.pine");
  if (!name) return;
  const [kind, file] = name.includes(":") ? name.split(":") : ["indicator", name];
  if (!["indicator", "strategy"].includes(kind) || !/\.(pine|py)$/.test(file)) { consoleMsg("Use indicator:name.pine / strategy:name.py", "err"); return; }
  const tpl = TEMPLATES[`${kind}.${file.split(".").pop()}`];
  try {
    await api(`/api/plugins/${kind}/${encodeURIComponent(file)}`, { code: tpl }, "PUT");
    await loadPlugins();
    await openInEditor(kind, file);
  } catch (err) { consoleMsg(err.message, "err"); }
});

async function saveEditor() {
  const { kind, file } = S.editor;
  if (!file) throw new Error("Open or create a file first");
  await api(`/api/plugins/${kind}/${encodeURIComponent(file)}`, { code: code.value }, "PUT");
}

$("#edSave").addEventListener("click", async () => {
  try { await saveEditor(); await loadPlugins(); consoleMsg("Saved " + S.editor.file, "ok"); }
  catch (err) { consoleMsg(err.message, "err"); }
});

$("#edRun").addEventListener("click", async () => {
  const { kind, file } = S.editor;
  try {
    await saveEditor();
    await loadPlugins();
    if (kind === "indicator") {
      let ind = S.indicators.find(i => i.file === file);
      if (ind) await runIndicator(ind);
      else await addIndicator(file);
      renderIndicatorList();
      consoleMsg("✓ Applied to chart", "ok");
    } else {
      await selectStrategy(file);
      await runBacktest();
      switchTab("editor");
      consoleMsg(`✓ Backtest done — ${S.strategy.res.metrics.total_trades} trades, return ${fmt(S.strategy.res.metrics.total_return_pct)}%. See Strategy Tester tab.`, "ok");
    }
  } catch (err) { consoleMsg(err.message, "err"); status(""); }
});

$("#edDelete").addEventListener("click", async () => {
  const { kind, file } = S.editor;
  if (!file || !confirm(`Delete ${file}? This removes the file from your project.`)) return;
  await api(`/api/plugins/${kind}/${encodeURIComponent(file)}`, undefined, "DELETE");
  S.indicators.filter(i => i.file === file).forEach(i => removeIndicator(i.uid));
  if (S.strategy && S.strategy.file === file) selectStrategy("");
  S.editor = { kind: null, file: null }; code.value = ""; updateGutter();
  await loadPlugins();
  const first = S.plugins[0]; if (first) openInEditor(first.kind, first.file);
});

// ------------------------------------------------------------------ params modal
let paramCallback = null;
function openParams(title, inputs, cb) {
  $("#paramTitle").textContent = title + " — settings";
  renderInputs($("#paramForm"), inputs);
  paramCallback = cb;
  $("#paramModal").hidden = false;
}
$("#paramCancel").addEventListener("click", () => ($("#paramModal").hidden = true));
$("#paramApply").addEventListener("click", async () => {
  $("#paramModal").hidden = true;
  try { await paramCallback(readInputs($("#paramForm"))); } catch (err) { status(err.message, true); }
});

// ------------------------------------------------------------------ tabs
function switchTab(name) {
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-body").forEach(el => (el.hidden = el.id !== "tab-" + name));
  if (name === "optimize" && !$("#optGrid").innerHTML) loadOptFields();
}
$("#tabs").addEventListener("click", e => { const b = e.target.closest("button"); if (b) switchTab(b.dataset.tab); });

// ------------------------------------------------------------------ boot
(async function boot() {
  try {
    await loadSymbols();
    await loadPlugins();
    await loadSymbol($("#symbol").value || "BTC");
    const first = S.plugins[0];
    if (first) await openInEditor(first.kind, first.file);
    const ema = S.plugins.find(p => p.file === "ema_cross.pine");
    if (ema) { await selectStrategy(ema.file); }
  } catch (err) { status(err.message, true); }
})();
