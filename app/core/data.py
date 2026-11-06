"""Market data loading.

Sources:
  * CSV files in ./data (columns: time/date, open, high, low, close, volume)
  * Built-in synthetic sample data (works offline)
  * Yahoo Finance via `yfinance` (optional, needs internet)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

_COLUMN_ALIASES = {
    "date": "time", "datetime": "time", "timestamp": "time",
    "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
    "adj close": "adj_close",
}


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Return a clean OHLCV frame indexed by a DatetimeIndex named `time`."""
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):  # yfinance multi-ticker shape
        df.columns = df.columns.get_level_values(0)
    df.columns = [_COLUMN_ALIASES.get(str(c).strip().lower(), str(c).strip().lower()) for c in df.columns]
    if "time" in df.columns:
        t = df["time"]
        if pd.api.types.is_numeric_dtype(t):  # unix seconds or ms
            unit = "ms" if t.iloc[0] > 1e11 else "s"
            df["time"] = pd.to_datetime(t, unit=unit)
        else:
            df["time"] = pd.to_datetime(t)
        df = df.set_index("time")
    else:
        df.index = pd.to_datetime(df.index)
        df.index.name = "time"
    missing = {"open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise ValueError(f"Data is missing columns: {sorted(missing)}")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[~df.index.duplicated()].sort_index().dropna(subset=["open", "high", "low", "close"])
    return df


def sample_data(symbol: str = "SAMPLE", bars: int = 1500, seed: int | None = None) -> pd.DataFrame:
    """Synthetic daily OHLCV with trending + ranging regimes (deterministic per symbol)."""
    import zlib
    rng = np.random.default_rng(seed if seed is not None else zlib.crc32(symbol.encode()))
    drift = np.repeat(rng.normal(0.0004, 0.0015, bars // 100 + 1), 100)[:bars]
    vol = np.repeat(rng.uniform(0.008, 0.025, bars // 100 + 1), 100)[:bars]
    rets = rng.normal(drift, vol)
    close = 100 * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[100.0], close[:-1]]) * (1 + rng.normal(0, 0.002, bars))
    spread = np.abs(rng.normal(0, vol, bars)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.lognormal(13, 0.4, bars)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=bars, name="time")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


# Friendly names -> Yahoo Finance tickers. Add your own here (e.g. "ETH": "ETH-USD", "SILVER": "SI=F").
PRESETS = {
    "BTC": "BTC-USD",   # Bitcoin in US dollars, trades 7 days a week
    "GOLD": "GC=F",     # COMEX gold futures (continuous front month), close to spot XAU/USD
}

# Timeframes offered in the UI. Intraday comes from Yahoo hourly data (last ~730 days);
# 4h and weekly are built by resampling so they stay consistent with the base data.
INTERVALS = {
    "1h": ("1h", None),
    "4h": ("1h", "4h"),
    "1d": ("1d", None),
    "1wk": ("1d", "W-FRI"),
}


def _csv_path(symbol: str, base: str) -> Path:
    return DATA_DIR / (f"{symbol}.csv" if base == "1d" else f"{symbol}_{base}.csv")


def list_sources() -> list[str]:
    DATA_DIR.mkdir(exist_ok=True)
    files = sorted({p.stem.split("_")[0] for p in DATA_DIR.glob("*.csv")} - set(PRESETS))
    return list(PRESETS) + files + ["SAMPLE", "SAMPLE2", "SAMPLE3"]


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = df.resample(rule, label="left" if rule.endswith("h") else "right", closed="left" if rule.endswith("h") else "right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return out.dropna(subset=["open", "close"])


def download(symbol: str, base: str = "1d") -> pd.DataFrame:
    """Download from Yahoo Finance and cache to ./data. Needs internet + `pip install yfinance`."""
    try:
        import yfinance as yf  # optional dependency
    except ImportError as e:
        raise ValueError("yfinance is not installed. Run: pip install yfinance") from e
    ticker = PRESETS.get(symbol.upper(), symbol)
    period = "730d" if base == "1h" else "max"
    df = yf.download(ticker, interval=base, period=period, progress=False, auto_adjust=True)
    if df is None or df.empty:
        raise ValueError(f"No data returned from Yahoo for {ticker!r} — check the ticker and your internet connection")
    df = normalize(df.reset_index())
    if df.index.tz is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    DATA_DIR.mkdir(exist_ok=True)
    df.reset_index().to_csv(_csv_path(symbol.upper(), base), index=False)
    return df


def load(symbol: str, interval: str = "1d", refresh: bool = False) -> pd.DataFrame:
    """Load OHLCV for `symbol` at `interval` (1h, 4h, 1d, 1wk).

    Order: cached CSV in ./data -> built-in sample -> Yahoo download (then cached).
    `refresh=True` always re-downloads from Yahoo first.
    """
    if interval not in INTERVALS:
        raise ValueError(f"Unknown interval {interval!r}; use one of {list(INTERVALS)}")
    base, rule = INTERVALS[interval]
    symbol = symbol.upper()

    if symbol.startswith("SAMPLE"):
        if base != "1d":
            raise ValueError("Sample data is daily only — pick 1d or 1wk")
        df = sample_data(symbol)
    else:
        csv = _csv_path(symbol, base)
        if refresh or not csv.exists():
            df = download(symbol, base)
        else:
            df = normalize(pd.read_csv(csv))
    return resample(df, rule) if rule else df


def save_upload(name: str, content: bytes) -> str:
    """Validate and store an uploaded CSV in ./data. Returns the symbol name."""
    import io
    DATA_DIR.mkdir(exist_ok=True)
    symbol = "".join(ch for ch in Path(name).stem if ch.isalnum() or ch in "-.").upper() or "UPLOAD"
    df = normalize(pd.read_csv(io.BytesIO(content)))
    df.reset_index().to_csv(DATA_DIR / f"{symbol}.csv", index=False)
    return symbol
