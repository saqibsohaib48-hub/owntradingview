"""Technical analysis library.

Every function takes and returns numpy float arrays (NaN = not enough data yet).
Semantics follow TradingView's Pine `ta.*` functions as closely as practical, so
the same math backs both Pine scripts and Python indicators.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _a(x) -> np.ndarray:
    return np.asarray(x, dtype=float)


def _s(x) -> pd.Series:
    return pd.Series(_a(x))


def sma(src, length: int):
    return _s(src).rolling(int(length), min_periods=int(length)).mean().to_numpy()


def ema(src, length: int):
    length = int(length)
    x = _a(src)
    out = np.full_like(x, np.nan)
    alpha = 2.0 / (length + 1)
    valid = np.where(~np.isnan(x))[0]
    if len(valid) < length:
        return out
    start = valid[0] + length - 1
    out[start] = np.nanmean(x[valid[0]:start + 1])  # seed with SMA like Pine
    for i in range(start + 1, len(x)):
        v = x[i] if not np.isnan(x[i]) else out[i - 1]
        out[i] = alpha * v + (1 - alpha) * out[i - 1]
    return out


def rma(src, length: int):
    """Wilder's moving average (used by RSI / ATR)."""
    length = int(length)
    x = _a(src)
    out = np.full_like(x, np.nan)
    alpha = 1.0 / length
    valid = np.where(~np.isnan(x))[0]
    if len(valid) < length:
        return out
    start = valid[0] + length - 1
    out[start] = np.nanmean(x[valid[0]:start + 1])
    for i in range(start + 1, len(x)):
        v = x[i] if not np.isnan(x[i]) else 0.0
        out[i] = alpha * v + (1 - alpha) * out[i - 1]
    return out


def wma(src, length: int):
    length = int(length)
    w = np.arange(1, length + 1, dtype=float)
    return _s(src).rolling(length).apply(lambda v: np.dot(v, w) / w.sum(), raw=True).to_numpy()


def hma(src, length: int):
    length = int(length)
    return wma(2 * wma(src, length // 2) - wma(src, length), int(np.sqrt(length)))


def vwma(src, volume, length: int):
    return sma(_a(src) * _a(volume), length) / sma(volume, length)


def stdev(src, length: int):
    return _s(src).rolling(int(length)).std(ddof=0).to_numpy()


def highest(src, length: int):
    return _s(src).rolling(int(length)).max().to_numpy()


def lowest(src, length: int):
    return _s(src).rolling(int(length)).min().to_numpy()


def change(src, length: int = 1):
    x = _a(src)
    out = np.full_like(x, np.nan)
    out[int(length):] = x[int(length):] - x[:-int(length)]
    return out


def mom(src, length: int):
    return change(src, length)


def roc(src, length: int):
    x = _a(src)
    prev = shift(x, int(length))
    return 100 * (x - prev) / prev


def shift(src, n: int = 1):
    x = _a(src)
    n = int(n)
    out = np.full_like(x, np.nan)
    if n == 0:
        return x.copy()
    if n < len(x):
        out[n:] = x[:-n]
    return out


def rsi(src, length: int):
    d = change(src, 1)
    up = rma(np.where(np.isnan(d), np.nan, np.maximum(d, 0)), length)
    down = rma(np.where(np.isnan(d), np.nan, np.maximum(-d, 0)), length)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = up / down
        out = 100 - 100 / (1 + rs)
    out = np.where(down == 0, 100.0, out)
    out = np.where(up == 0, np.where(down == 0, 50.0, 0.0), out)
    return np.where(np.isnan(up) | np.isnan(down), np.nan, out)


def tr(high, low, close):
    h, l, c = _a(high), _a(low), _a(close)
    pc = shift(c, 1)
    out = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    out[0] = h[0] - l[0]
    return out


def atr(high, low, close, length: int):
    return rma(tr(high, low, close), length)


def macd(src, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(src, fast) - ema(src, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bb(src, length: int = 20, mult: float = 2.0):
    basis = sma(src, length)
    dev = mult * stdev(src, length)
    return basis, basis + dev, basis - dev


def stoch(close, high, low, length: int):
    hh, ll = highest(high, length), lowest(low, length)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100 * (_a(close) - ll) / (hh - ll)


def cci(src, length: int):
    m = sma(src, length)
    md = _s(src).rolling(int(length)).apply(lambda v: np.mean(np.abs(v - v.mean())), raw=True).to_numpy()
    return (_a(src) - m) / (0.015 * md)


def vwap(high, low, close, volume):
    tp = (_a(high) + _a(low) + _a(close)) / 3
    v = _a(volume)
    cv = np.cumsum(v)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(cv > 0, np.cumsum(tp * v) / cv, np.nan)  # NaN when data has no volume


def supertrend(high, low, close, factor: float = 3.0, length: int = 10):
    """Returns (supertrend_line, direction) where direction = -1 uptrend, 1 downtrend (Pine convention)."""
    h, l, c = _a(high), _a(low), _a(close)
    a = atr(h, l, c, length)
    hl2 = (h + l) / 2
    up, dn = hl2 - factor * a, hl2 + factor * a
    st = np.full_like(c, np.nan)
    direction = np.full_like(c, np.nan)
    for i in range(1, len(c)):
        if np.isnan(a[i]):
            continue
        if not np.isnan(up[i - 1]) and c[i - 1] > up[i - 1]:
            up[i] = max(up[i], up[i - 1])
        if not np.isnan(dn[i - 1]) and c[i - 1] < dn[i - 1]:
            dn[i] = min(dn[i], dn[i - 1])
        prev_dir = direction[i - 1] if not np.isnan(direction[i - 1]) else 1
        if prev_dir == 1:
            direction[i] = -1 if c[i] > dn[i] else 1
        else:
            direction[i] = 1 if c[i] < up[i] else -1
        st[i] = up[i] if direction[i] == -1 else dn[i]
    return st, direction


def crossover(a, b):
    a, b = _a(a), _a(b)
    pa, pb = shift(a, 1), shift(b, 1)
    return (a > b) & (pa <= pb)


def crossunder(a, b):
    a, b = _a(a), _a(b)
    pa, pb = shift(a, 1), shift(b, 1)
    return (a < b) & (pa >= pb)


def cross(a, b):
    return crossover(a, b) | crossunder(a, b)


def barssince(cond):
    c = _a(cond).astype(bool)
    out = np.full(len(c), np.nan)
    last = -1
    for i, v in enumerate(c):
        if v:
            last = i
        if last >= 0:
            out[i] = i - last
    return out


def valuewhen(cond, src, occurrence: int = 0):
    c, s = _a(cond).astype(bool), _a(src)
    out = np.full(len(c), np.nan)
    hits: list[float] = []
    for i in range(len(c)):
        if c[i]:
            hits.append(s[i])
        if len(hits) > occurrence:
            out[i] = hits[-1 - int(occurrence)]
    return out


def pivothigh(src, left: int, right: int):
    s = _a(src)
    out = np.full_like(s, np.nan)
    for i in range(left, len(s) - right):
        w = s[i - left:i + right + 1]
        if s[i] == np.nanmax(w):
            out[i + right] = s[i]  # confirmed `right` bars later, like Pine
    return out


def pivotlow(src, left: int, right: int):
    s = _a(src)
    out = np.full_like(s, np.nan)
    for i in range(left, len(s) - right):
        w = s[i - left:i + right + 1]
        if s[i] == np.nanmin(w):
            out[i + right] = s[i]
    return out
