"""FastAPI server: serves the web UI and the charting / backtesting API.

Run:  python run.py        (then open http://127.0.0.1:8000)
"""
from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .core import data as datamod
from .core import plugins
from .core.backtest import run_backtest
from .pine.interpreter import PineRuntimeError
from .pine.lexer import PineSyntaxError

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"

app = FastAPI(title="OpenTrade", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

USER_ERRORS = (plugins.PluginError, PineSyntaxError, PineRuntimeError, ValueError)


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
    except Exception as e:
        raise HTTPException(400, f"Could not load {symbol}: {e}")
    if len(df) < 2:
        raise HTTPException(400, "Not enough data")
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
        return plugins.run_plugin(kind, file, df, params, code)
    except USER_ERRORS as e:
        raise HTTPException(422, str(e))


def _backtest_payload(res: dict, bt: dict) -> dict:
    trades = bt["trades"]
    markers = []
    for t in trades:
        long = t["direction"] == "long"
        markers.append({"time": _iso_to_ts(t["entry_time"]), "position": "belowBar" if long else "aboveBar",
                        "color": "#26a69a" if long else "#ef5350", "shape": "arrowUp" if long else "arrowDown",
                        "text": "L" if long else "S"})
        markers.append({"time": _iso_to_ts(t["exit_time"]), "position": "aboveBar" if long else "belowBar",
                        "color": "#B2B5BE", "shape": "circle", "text": f"{t['pnl_pct']:+.1f}%"})
    markers.sort(key=lambda m: m["time"])
    return {"metrics": bt["metrics"], "settings": bt["settings"], "trades": trades,
            "equity": [{"time": _iso_to_ts(e["time"]), "value": e["value"]} for e in bt["equity"]],
            "trade_markers": markers}


# ------------------------------------------------------------------ models
class RunReq(BaseModel):
    symbol: str = "BTC"
    interval: str = "1d"
    kind: str = "indicator"
    file: str
    params: dict = Field(default_factory=dict)
    code: str | None = None


class BacktestReq(BaseModel):
    symbol: str = "BTC"
    interval: str = "1d"
    file: str
    params: dict = Field(default_factory=dict)
    settings: dict = Field(default_factory=dict)
    code: str | None = None
    start: str | None = None
    end: str | None = None


class CompareItem(BaseModel):
    file: str
    params: dict = Field(default_factory=dict)


class CompareReq(BaseModel):
    symbol: str = "BTC"
    interval: str = "1d"
    strategies: list[CompareItem]
    settings: dict = Field(default_factory=dict)
    start: str | None = None
    end: str | None = None


class OptimizeReq(BaseModel):
    symbol: str = "BTC"
    interval: str = "1d"
    file: str
    grid: dict[str, list]          # {"fast": [10, 20], "slow": [50, 100]}
    settings: dict = Field(default_factory=dict)
    sort_by: str = "sharpe"
    start: str | None = None
    end: str | None = None


class SaveReq(BaseModel):
    code: str


def _slice(df, start, end):
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    if len(df) < 10:
        raise HTTPException(400, "Date range leaves fewer than 10 bars")
    return df


# ------------------------------------------------------------------ routes
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/symbols")
def symbols():
    return {"symbols": datamod.list_sources(), "intervals": list(datamod.INTERVALS), "presets": datamod.PRESETS}


class RefreshReq(BaseModel):
    symbol: str
    interval: str = "1d"


@app.post("/api/refresh")
def refresh(req: RefreshReq):
    """Re-download the latest prices from Yahoo Finance (also used to add a new ticker)."""
    df = _load(req.symbol, req.interval, refresh=True)
    return {"symbol": req.symbol.upper(), "bars": len(df), "last": df.index[-1].isoformat()}


@app.get("/api/candles")
def candles(symbol: str = "BTC", interval: str = "1d"):
    df = _load(symbol, interval)
    times = _times(df)
    rows = df[["open", "high", "low", "close", "volume"]].to_numpy()
    return {"symbol": symbol, "candles": [
        {"time": t, "open": round(o, 6), "high": round(h, 6), "low": round(l, 6), "close": round(c, 6), "volume": round(v, 2)}
        for t, (o, h, l, c, v) in zip(times, rows)]}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    try:
        symbol = datamod.save_upload(file.filename or "upload.csv", await file.read())
    except Exception as e:
        raise HTTPException(400, f"Invalid CSV: {e}")
    return {"symbol": symbol}


@app.get("/api/plugins")
def list_plugins():
    return {"plugins": plugins.list_plugins()}


@app.get("/api/plugins/{kind}/{file}")
def get_plugin(kind: str, file: str):
    try:
        return {"code": plugins.read_source(kind, file)}
    except plugins.PluginError as e:
        raise HTTPException(404, str(e))


@app.put("/api/plugins/{kind}/{file}")
def save_plugin(kind: str, file: str, req: SaveReq):
    try:
        return {"file": plugins.save_source(kind, file, req.code)}
    except plugins.PluginError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/plugins/{kind}/{file}")
def delete_plugin(kind: str, file: str):
    try:
        plugins.delete_source(kind, file)
    except plugins.PluginError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/run")
def run(req: RunReq):
    df = _load(req.symbol, req.interval)
    return _serialize_run(_run(req.kind, req.file, df, req.params, req.code), df)


@app.post("/api/backtest")
def backtest(req: BacktestReq):
    df = _slice(_load(req.symbol, req.interval), req.start, req.end)
    res = _run("strategy", req.file, df, req.params, req.code)
    try:
        bt = run_backtest(df, res["signals"], req.settings)
    except ValueError as e:
        raise HTTPException(422, str(e))
    out = _serialize_run(res, df)
    out.update(_backtest_payload(res, bt))
    return out


@app.post("/api/compare")
def compare(req: CompareReq):
    df = _slice(_load(req.symbol, req.interval), req.start, req.end)
    results = []
    for item in req.strategies:
        try:
            res = plugins.run_plugin("strategy", item.file, df, item.params)
            bt = run_backtest(df, res["signals"], req.settings)
            results.append({"file": item.file, "title": res["title"], "metrics": bt["metrics"],
                            "params": {i["name"]: i["value"] for i in res["inputs"]},
                            "equity": [{"time": _iso_to_ts(e["time"]), "value": e["value"]} for e in bt["equity"]]})
        except USER_ERRORS as e:
            results.append({"file": item.file, "title": item.file, "error": str(e)})
    close = df["close"].to_numpy()
    initial = float(req.settings.get("initial_capital") or 10_000)
    bh = _series(_times(df), close / close[0] * initial)
    return {"results": results, "buy_hold": bh}


@app.post("/api/optimize")
def optimize(req: OptimizeReq):
    df = _slice(_load(req.symbol, req.interval), req.start, req.end)
    keys = list(req.grid)
    combos = list(itertools.product(*(req.grid[k] for k in keys)))
    if not combos:
        raise HTTPException(400, "Empty parameter grid")
    if len(combos) > 500:
        raise HTTPException(400, f"{len(combos)} combinations is too many (max 500)")
    rows = []
    for combo in combos:
        params = dict(zip(keys, combo))
        try:
            res = plugins.run_plugin("strategy", req.file, df, params)
            m = run_backtest(df, res["signals"], req.settings)["metrics"]
            rows.append({"params": params, "metrics": m})
        except USER_ERRORS as e:
            rows.append({"params": params, "error": str(e)})
    # Higher is better for every sortable metric (drawdown is negative, so -5% beats -30%).
    def score(r):
        v = (r.get("metrics") or {}).get(req.sort_by)
        return v if v is not None else -1e18
    rows.sort(key=score, reverse=True)
    return {"rows": rows, "count": len(rows)}
