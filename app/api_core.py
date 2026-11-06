"""All API logic, free of any web framework.

The same `handle()` router is used by:
  * app/server.py   - FastAPI, when you run `python run.py`
  * app/browser.py  - Pyodide (Python in WebAssembly), when the site runs on GitHub Pages
so both modes behave identically.
"""
from __future__ import annotations

import itertools
import re

import numpy as np
import pandas as pd

from .core import data as datamod
from .core import plugins
from .core.backtest import run_backtest
from .pine.interpreter import PineRuntimeError
from .pine.lexer import PineSyntaxError

USER_ERRORS = (plugins.PluginError, PineSyntaxError, PineRuntimeError, ValueError)


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ------------------------------------------------------------------ helpers
def _times(df: pd.DataFrame) -> list[int]:
    return [int(t.timestamp()) for t in df.index]


def _series(times: list[int], values) -> list[dict]:
    arr = np.asarray(values, dtype=float)
    return [{"time": t, "value": round(float(v), 6)} for t, v in zip(times, arr) if np.isfinite(v)]


def _iso_to_ts(s: str) -> int:
    return int(pd.Timestamp(s).timestamp())


def _load(symbol: str, interval: str = "1d", refresh: bool = False) -> pd.DataFrame:
    try:
        df = datamod.load(symbol, interval, refresh)
    except datamod.NeedDownload:
        raise  # browser mode: JavaScript fetches the data, then retries
    except Exception as e:
        raise ApiError(400, f"Could not load {symbol}: {e}")
    if len(df) < 2:
        raise ApiError(400, "Not enough data")
    return df


def _slice(df, start, end):
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    if len(df) < 10:
        raise ApiError(400, "Date range leaves fewer than 10 bars")
    return df


def _serialize_run(res: dict, df: pd.DataFrame) -> dict:
    times = _times(df)
    plots = [{**{k: v for k, v in p.items() if k != "data"}, "data": _series(times, p["data"])} for p in res["plots"]]
    markers = []
    for s in res.get("shapes", []):
        for i in np.where(s["where"])[0]:
            markers.append({"time": times[i], "position": s["position"], "shape": s["shape"],
                            "color": s["color"], "text": s["text"]})
    return {"kind": res["kind"], "title": res["title"], "overlay": res["overlay"], "inputs": res["inputs"],
            "plots": plots, "markers": markers, "hlines": res.get("hlines", []), "warnings": res.get("warnings", [])}


def _run(kind, file, df, params, code=None):
    try:
        return plugins.run_plugin(kind, file, df, params or {}, code)
    except USER_ERRORS as e:
        raise ApiError(422, str(e))


def _equity(bt):
    return [{"time": _iso_to_ts(e["time"]), "value": e["value"]} for e in bt["equity"]]


def _backtest_payload(bt: dict) -> dict:
    markers = []
    for t in bt["trades"]:
        long = t["direction"] == "long"
        markers.append({"time": _iso_to_ts(t["entry_time"]), "position": "belowBar" if long else "aboveBar",
                        "color": "#26a69a" if long else "#ef5350", "shape": "arrowUp" if long else "arrowDown",
                        "text": "L" if long else "S"})
        markers.append({"time": _iso_to_ts(t["exit_time"]), "position": "aboveBar" if long else "belowBar",
                        "color": "#B2B5BE", "shape": "circle", "text": f"{t['pnl_pct']:+.1f}%"})
    markers.sort(key=lambda m: m["time"])
    return {"metrics": bt["metrics"], "settings": bt["settings"], "trades": bt["trades"],
            "equity": _equity(bt), "trade_markers": markers}


def _need(body: dict, key: str):
    if key not in body or body[key] in (None, ""):
        raise ApiError(400, f"Missing field '{key}'")
    return body[key]


# ------------------------------------------------------------------ endpoints
def symbols(q, b):
    return {"symbols": datamod.list_sources(), "intervals": list(datamod.INTERVALS), "presets": datamod.PRESETS}


def refresh(q, b):
    sym = str(_need(b, "symbol")).upper()
    df = _load(sym, b.get("interval", "1d"), refresh=True)
    return {"symbol": sym, "bars": len(df), "last": df.index[-1].isoformat()}


def candles(q, b):
    symbol, interval = q.get("symbol", "BTC"), q.get("interval", "1d")
    df = _load(symbol, interval)
    rows = df[["open", "high", "low", "close", "volume"]].to_numpy()
    return {"symbol": symbol, "candles": [
        {"time": t, "open": round(float(o), 6), "high": round(float(h), 6), "low": round(float(l), 6),
         "close": round(float(c), 6), "volume": round(float(v), 2)}
        for t, (o, h, l, c, v) in zip(_times(df), rows)]}


def upload(filename: str, content: bytes) -> dict:
    try:
        return {"symbol": datamod.save_upload(filename or "upload.csv", content)}
    except Exception as e:
        raise ApiError(400, f"Invalid CSV: {e}")


def list_plugins(q, b):
    return {"plugins": plugins.list_plugins()}


def get_plugin(kind, file):
    try:
        return {"code": plugins.read_source(kind, file)}
    except plugins.PluginError as e:
        raise ApiError(404, str(e))


def save_plugin(kind, file, b):
    try:
        return {"file": plugins.save_source(kind, file, _need(b, "code"))}
    except plugins.PluginError as e:
        raise ApiError(400, str(e))


def delete_plugin(kind, file):
    try:
        plugins.delete_source(kind, file)
    except plugins.PluginError as e:
        raise ApiError(400, str(e))
    return {"ok": True}


def run(q, b):
    df = _load(b.get("symbol", "BTC"), b.get("interval", "1d"))
    return _serialize_run(_run(b.get("kind", "indicator"), _need(b, "file"), df, b.get("params"), b.get("code")), df)


def backtest(q, b):
    df = _slice(_load(b.get("symbol", "BTC"), b.get("interval", "1d")), b.get("start"), b.get("end"))
    res = _run("strategy", _need(b, "file"), df, b.get("params"), b.get("code"))
    try:
        bt = run_backtest(df, res["signals"], b.get("settings") or {})
    except (ValueError, TypeError) as e:
        raise ApiError(422, str(e))
    out = _serialize_run(res, df)
    out.update(_backtest_payload(bt))
    return out


def compare(q, b):
    df = _slice(_load(b.get("symbol", "BTC"), b.get("interval", "1d")), b.get("start"), b.get("end"))
    settings = b.get("settings") or {}
    results = []
    for item in _need(b, "strategies"):
        file = item.get("file")
        try:
            res = plugins.run_plugin("strategy", file, df, item.get("params") or {})
            bt = run_backtest(df, res["signals"], settings)
            results.append({"file": file, "title": res["title"], "metrics": bt["metrics"],
                            "params": {i["name"]: i["value"] for i in res["inputs"]}, "equity": _equity(bt)})
        except USER_ERRORS as e:
            results.append({"file": file, "title": file, "error": str(e)})
    close = df["close"].to_numpy()
    initial = float(settings.get("initial_capital") or 10_000)
    return {"results": results, "buy_hold": _series(_times(df), close / close[0] * initial)}


def optimize(q, b):
    df = _slice(_load(b.get("symbol", "BTC"), b.get("interval", "1d")), b.get("start"), b.get("end"))
    grid = _need(b, "grid")
    keys = list(grid)
    combos = list(itertools.product(*(grid[k] for k in keys)))
    if not combos:
        raise ApiError(400, "Empty parameter grid")
    if len(combos) > 500:
        raise ApiError(400, f"{len(combos)} combinations is too many (max 500)")
    rows = []
    for combo in combos:
        params = dict(zip(keys, combo))
        try:
            res = plugins.run_plugin("strategy", b["file"], df, params)
            rows.append({"params": params, "metrics": run_backtest(df, res["signals"], b.get("settings") or {})["metrics"]})
        except USER_ERRORS as e:
            rows.append({"params": params, "error": str(e)})
    sort_by = b.get("sort_by", "sharpe")

    # Higher is better for every sortable metric (drawdown is negative, so -5% beats -30%).
    def score(r):
        v = (r.get("metrics") or {}).get(sort_by)
        return v if v is not None else -1e18
    rows.sort(key=score, reverse=True)
    return {"rows": rows, "count": len(rows)}


# ------------------------------------------------------------------ router
_PLUGIN_RE = re.compile(r"^/api/plugins/(indicator|strategy)/([^/]+)$")
_ROUTES = {
    ("GET", "/api/symbols"): symbols,
    ("GET", "/api/candles"): candles,
    ("POST", "/api/refresh"): refresh,
    ("GET", "/api/plugins"): list_plugins,
    ("POST", "/api/run"): run,
    ("POST", "/api/backtest"): backtest,
    ("POST", "/api/compare"): compare,
    ("POST", "/api/optimize"): optimize,
}


def handle(method: str, path: str, query: dict | None = None, body: dict | None = None) -> dict:
    """Route an API call. Raises ApiError for user-facing errors."""
    method, query, body = method.upper(), query or {}, body or {}
    fn = _ROUTES.get((method, path))
    if fn:
        return fn(query, body)
    m = _PLUGIN_RE.match(path)
    if m:
        kind, file = m.group(1), m.group(2)
        if method == "GET":
            return get_plugin(kind, file)
        if method == "PUT":
            return save_plugin(kind, file, body)
        if method == "DELETE":
            return delete_plugin(kind, file)
    raise ApiError(404, f"No route {method} {path}")
