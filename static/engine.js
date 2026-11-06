/* OpenTrade engine selector.
 *
 * "server"  mode: `python run.py` is running -> calls go to the FastAPI backend.
 * "browser" mode: plain static hosting (GitHub Pages) -> the SAME Python code runs inside the page
 *                 via Pyodide (Python compiled to WebAssembly). Nothing to install.
 *
 * In browser mode, scripts you save and data you download are kept in this browser (localStorage).
 * Use the editor's Download button to put new scripts into your GitHub repo permanently.
 */
const ENGINE = (() => {
  const PYODIDE_URL = window.OPENTRADE_PYODIDE_URL || "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
  const ROOT = "/home/pyodide/opentrade";
  const LS = "opentrade:";
  // Binance spot pairs used for live data in the browser (Yahoo blocks browser requests).
  // PAXG is a token backed 1:1 by a troy ounce of gold, so PAXG/USDT tracks the gold price.
  const BINANCE_PAIRS = { BTC: "BTCUSDT", GOLD: "PAXGUSDT" };
  const BINANCE_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"];

  let mode = "server", py = null, bridge = null, manifest = { files: [] };

  // ---------------------------------------------------------------- storage
  const store = {
    get(k) { try { return localStorage.getItem(LS + k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(LS + k, v); return true; } catch { return false; } },
    del(k) { try { localStorage.removeItem(LS + k); } catch { } },
    keys(prefix) {
      const out = [];
      try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (k.startsWith(LS + prefix)) out.push(k.slice(LS.length + prefix.length)); } } catch { }
      return out;
    },
  };

  // ---------------------------------------------------------------- detection
  async function detect() {
    if (new URLSearchParams(location.search).has("browser")) return "browser";
    if (location.hostname.endsWith("github.io") || location.protocol === "file:") return "browser"; // static hosting: no server to ask
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 2500);
      const r = await fetch("api/symbols", { signal: ctrl.signal });
      clearTimeout(t);
      if (r.ok && (r.headers.get("content-type") || "").includes("json")) return "server";
    } catch { }
    return "browser";
  }

  function loadScript(src) {
    return new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src; s.onload = res; s.onerror = () => rej(new Error("Could not load " + src));
      document.head.appendChild(s);
    });
  }

  function writeFile(path, text) {
    const full = `${ROOT}/${path}`;
    py.FS.mkdirTree(full.slice(0, full.lastIndexOf("/")));
    py.FS.writeFile(full, text);
  }

  async function initBrowser(progress) {
    progress("Loading Python engine (first visit downloads ~25 MB, then it's cached)…");
    if (!window.loadPyodide) await loadScript(PYODIDE_URL + "pyodide.js");
    py = await loadPyodide({ indexURL: PYODIDE_URL });
    progress("Loading numpy and pandas…");
    await py.loadPackage(["numpy", "pandas"]);

    progress("Loading OpenTrade files…");
    manifest = await (await fetch("manifest.json", { cache: "no-cache" })).json();
    const deleted = new Set(store.keys("deleted:"));
    await Promise.all(manifest.files.filter(f => !deleted.has(f)).map(async f => {
      const r = await fetch(f, { cache: "no-cache" });
      if (r.ok) writeFile(f, await r.text());
    }));
    // Files saved in this browser (your scripts, downloaded/uploaded data) override the repo copies.
    for (const f of store.keys("file:")) writeFile(f, store.get("file:" + f));

    py.runPython(`import sys, os\nsys.path.insert(0, "${ROOT}")\nos.chdir("${ROOT}")`);
    bridge = py.pyimport("app.browser");
    progress("");
  }

  async function init(progress = () => { }) {
    mode = await detect();
    if (mode === "browser") await initBrowser(progress);
    return mode;
  }

  // ---------------------------------------------------------------- live data (browser mode)
  function binancePair(symbol) {
    const s = symbol.toUpperCase();
    if (BINANCE_PAIRS[s]) return BINANCE_PAIRS[s];
    const clean = s.replace(/[-/_ ]/g, "").replace(/USD$/, "");
    return clean.endsWith("USDT") ? clean : clean + "USDT";
  }

  async function binanceKlines(pair, interval, pages) {
    let rows = [], endTime = null, lastErr = null;
    for (let p = 0; p < pages; p++) {
      let got = null;
      for (const host of BINANCE_HOSTS) {
        try {
          const url = `${host}/api/v3/klines?symbol=${pair}&interval=${interval}&limit=1000` + (endTime ? `&endTime=${endTime}` : "");
          const r = await fetch(url);
          const j = await r.json();
          if (!r.ok) throw new Error(j.msg || r.statusText);
          got = j; break;
        } catch (e) { lastErr = e; }
      }
      if (!got) throw new Error(`Could not download ${pair} from Binance: ${lastErr ? lastErr.message : "unknown error"}`);
      if (!got.length) break;
      rows = got.concat(rows);
      endTime = got[0][0] - 1;
      if (got.length < 1000) break;
    }
    return rows;
  }

  async function downloadAndStore(symbol, base) {
    const pair = binancePair(symbol);
    const rows = await binanceKlines(pair, base === "1h" ? "1h" : "1d", base === "1h" ? 6 : 4);
    if (rows.length < 10) throw new Error(`Not enough data for ${pair}`);
    const csv = "time,open,high,low,close,volume\n" + rows.map(k =>
      `${new Date(k[0]).toISOString().slice(0, 19)},${k[1]},${k[2]},${k[3]},${k[4]},${k[5]}`).join("\n");
    const res = JSON.parse(bridge.ingest_csv(symbol.toUpperCase(), base, csv));
    store.set("file:" + res.path, csv); // persist across visits (best effort, localStorage is ~5 MB)
    return { symbol: symbol.toUpperCase(), bars: res.bars, last: res.last, source: `Binance ${pair}` };
  }

  // ---------------------------------------------------------------- request API
  async function browserCall(method, path, body, retried = false) {
    const [p, qs] = path.split("?");
    const query = Object.fromEntries(new URLSearchParams(qs || ""));
    if (method === "POST" && p === "/api/refresh") {
      const intervals = { "1h": "1h", "4h": "1h", "1d": "1d", "1wk": "1d" };
      return downloadAndStore(body.symbol, intervals[body.interval] || "1d");
    }
    const out = JSON.parse(bridge.call(method, p, JSON.stringify(query), JSON.stringify(body ?? null)));
    if (out.ok) {
      afterWrite(method, p, body);
      return out.data;
    }
    if (out.need_download && !retried) {
      await downloadAndStore(out.need_download.symbol, out.need_download.base);
      return browserCall(method, path, body, true);
    }
    throw new Error(out.error || "Request failed");
  }

  // Keep scripts saved/deleted in the browser across page reloads.
  function afterWrite(method, p, body) {
    const m = p.match(/^\/api\/plugins\/(indicator|strategy)\/(.+)$/);
    if (!m) return;
    const path = `${m[1] === "indicator" ? "indicators" : "strategies"}/${decodeURIComponent(m[2])}`;
    if (method === "PUT") { store.set("file:" + path, body.code); store.del("deleted:" + path); }
    if (method === "DELETE") { store.del("file:" + path); if (manifest.files.includes(path)) store.set("deleted:" + path, "1"); }
  }

  async function request(path, body, method) {
    method = method || (body !== undefined ? "POST" : "GET");
    if (mode === "browser") {
      await new Promise(r => setTimeout(r, 0)); // let the UI paint "working…" before Python blocks
      return browserCall(method, path, body);
    }
    const opt = body !== undefined ? { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : { method };
    const r = await fetch(path.replace(/^\//, ""), opt);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail ? (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)) : r.statusText);
    return j;
  }

  async function upload(file) {
    if (mode === "browser") {
      const text = await file.text();
      const out = JSON.parse(bridge.upload(file.name, text));
      if (!out.ok) throw new Error(out.error);
      if (!store.set("file:" + out.data.path, text)) console.warn("CSV too large to remember after reload");
      return out.data;
    }
    const fd = new FormData(); fd.append("file", file);
    const r = await fetch("api/upload", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail);
    return j;
  }

  return { init, request, upload, get mode() { return mode; } };
})();
