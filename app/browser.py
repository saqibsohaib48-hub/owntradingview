"""Bridge used when OpenTrade runs entirely in the browser (GitHub Pages + Pyodide).

JavaScript calls `call(method, path, query_json, body_json)` and gets a JSON string back.
There is no yfinance in the browser, so when data is missing we raise NeedDownload;
JavaScript then fetches prices (Binance public API), calls `ingest_csv`, and retries.
"""
from __future__ import annotations

import io
import json
import math

import numpy as np
import pandas as pd

from . import api_core
from .core import data


def _need_download(symbol: str, base: str):
    raise data.NeedDownload(symbol, base)


data.DOWNLOADER = _need_download


def _clean(o):
    """Make results strict-JSON safe (no NaN/Infinity, no numpy scalar types)."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    return o


def _reply(**kw) -> str:
    return json.dumps(_clean(kw))


def call(method: str, path: str, query_json: str = "{}", body_json: str = "null") -> str:
    try:
        result = api_core.handle(method, path, json.loads(query_json or "{}"), json.loads(body_json or "null"))
        return _reply(ok=True, data=result)
    except data.NeedDownload as e:
        return _reply(ok=False, need_download={"symbol": e.symbol, "base": e.base})
    except api_core.ApiError as e:
        return _reply(ok=False, status=e.status, error=e.message)
    except Exception as e:  # unexpected bug: report it instead of crashing the page
        return _reply(ok=False, status=500, error=f"{type(e).__name__}: {e}")


def ingest_csv(symbol: str, base: str, csv_text: str) -> str:
    """Store downloaded prices for `symbol` at base interval ('1h' or '1d'). Returns the saved CSV path."""
    df = data.normalize(pd.read_csv(io.StringIO(csv_text)))
    path = data._csv_path(symbol.upper(), base)
    path.parent.mkdir(exist_ok=True)
    df.reset_index().to_csv(path, index=False)
    return _reply(ok=True, path=str(path.relative_to(data.DATA_DIR.parent)), bars=len(df),
                  last=df.index[-1].isoformat())


def upload(filename: str, csv_text: str) -> str:
    try:
        res = api_core.upload(filename, csv_text.encode("utf-8"))
        return _reply(ok=True, data={**res, "path": f"data/{res['symbol']}.csv"})
    except api_core.ApiError as e:
        return _reply(ok=False, status=e.status, error=e.message)
