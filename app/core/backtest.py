"""Event-driven (bar-by-bar) backtesting engine.

Contract: a strategy produces a `Signals` object of boolean arrays aligned with the
price data. A signal on bar i is filled at the OPEN of bar i+1 (no look-ahead).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd


@dataclass
class Signals:
    long_entry: np.ndarray
    long_exit: np.ndarray | None = None
    short_entry: np.ndarray | None = None
    short_exit: np.ndarray | None = None
    # Optional per-strategy overrides (percent, e.g. 2.0 = 2%)
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None

    def normalized(self, n: int) -> "Signals":
        def b(x):
            if x is None:
                return np.zeros(n, dtype=bool)
            arr = np.asarray(x)
            arr = np.where(pd.isna(arr), False, arr).astype(bool)
            if len(arr) != n:
                raise ValueError(f"Signal length {len(arr)} != data length {n}")
            return arr
        return Signals(b(self.long_entry), b(self.long_exit), b(self.short_entry), b(self.short_exit),
                       self.stop_loss_pct, self.take_profit_pct)


@dataclass
class Settings:
    initial_capital: float = 10_000.0
    size_type: str = "percent_equity"   # percent_equity | fixed_cash | fixed_qty | risk_percent
    size_value: float = 100.0           # % of equity, cash amount, quantity, or % equity risked
    commission_pct: float = 0.05        # per side, percent of trade value
    slippage_pct: float = 0.02          # adverse price move per fill, percent
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    allow_short: bool = True

    @classmethod
    def from_dict(cls, d: dict | None) -> "Settings":
        d = d or {}
        fields = cls.__dataclass_fields__
        clean = {}
        for k, v in d.items():
            if k in fields and v not in ("", None):
                clean[k] = v
        s = cls(**clean)
        for k in ("initial_capital", "size_value", "commission_pct", "slippage_pct"):
            setattr(s, k, float(getattr(s, k)))
        for k in ("stop_loss_pct", "take_profit_pct"):
            v = getattr(s, k)
            setattr(s, k, float(v) if v not in (None, "", 0, "0") else None)
        s.allow_short = str(s.allow_short).lower() not in ("false", "0", "no")
        return s


@dataclass
class Trade:
    direction: str
    entry_time: str
    entry_price: float
    exit_time: str = ""
    exit_price: float = 0.0
    qty: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    bars: int = 0
    exit_reason: str = ""
    _entry_idx: int = field(default=0, repr=False)


def position_size(settings: Settings, equity: float, price: float) -> float:
    """How many units to buy/sell for a new position.

    This is the money-management heart of the backtest — customise it freely.
    `risk_percent` sizes the trade so that hitting the stop loses `size_value`% of equity.
    """
    if price <= 0 or equity <= 0:
        return 0.0
    if settings.size_type == "fixed_qty":
        return settings.size_value
    if settings.size_type == "fixed_cash":
        return min(settings.size_value, equity) / price
    if settings.size_type == "risk_percent" and settings.stop_loss_pct:
        risk_cash = equity * settings.size_value / 100
        qty = risk_cash / (price * settings.stop_loss_pct / 100)
        return min(qty, equity / price)  # never use leverage
    return equity * min(settings.size_value, 100.0) / 100 / price


def run_backtest(df: pd.DataFrame, signals: Signals, settings: Settings | dict | None = None) -> dict:
    if not isinstance(settings, Settings):
        settings = Settings.from_dict(settings)
    n = len(df)
    sig = signals.normalized(n)
    sl_pct = sig.stop_loss_pct if sig.stop_loss_pct is not None else settings.stop_loss_pct
    tp_pct = sig.take_profit_pct if sig.take_profit_pct is not None else settings.take_profit_pct

    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    times = [t.isoformat() for t in df.index]
    comm, slip = settings.commission_pct / 100, settings.slippage_pct / 100

    cash = settings.initial_capital
    pos = 0          # +1 long, -1 short, 0 flat
    qty = 0.0
    entry_price = 0.0
    trade: Trade | None = None
    trades: list[Trade] = []
    equity = np.empty(n)
    pending: str | None = None   # action decided at previous close, executed at this open

    def close_position(i: int, price: float, reason: str):
        nonlocal cash, pos, qty, trade
        fill = price * (1 - slip) if pos > 0 else price * (1 + slip)
        value = qty * fill
        fee = value * comm
        if pos > 0:
            cash += value - fee
            pnl = (fill - entry_price) * qty
        else:
            cash -= value + fee
            pnl = (entry_price - fill) * qty
        pnl -= fee + trade.qty * entry_price * comm  # include both commissions
        trade.exit_time, trade.exit_price = times[i], round(fill, 6)
        trade.pnl = round(pnl, 4)
        trade.pnl_pct = round(pnl / (trade.qty * entry_price) * 100, 4)
        trade.bars = i - trade._entry_idx
        trade.exit_reason = reason
        trades.append(trade)
        pos, qty, trade = 0, 0.0, None

    def open_position(i: int, direction: int, price: float):
        nonlocal cash, pos, qty, entry_price, trade
        fill = price * (1 + slip) if direction > 0 else price * (1 - slip)
        eq = cash
        q = position_size(settings, eq / (1 + comm), fill)
        if q <= 0:
            return
        fee = q * fill * comm
        if direction > 0:
            cash -= q * fill + fee
        else:
            cash += q * fill - fee
        pos, qty, entry_price = direction, q, fill
        trade = Trade("long" if direction > 0 else "short", times[i], round(fill, 6), qty=q, _entry_idx=i)

    for i in range(n):
        # 1) Execute orders decided on the previous bar's close, at this bar's open.
        if pending:
            if pending in ("exit_long", "reverse_short") and pos > 0:
                close_position(i, o[i], "signal")
            if pending in ("exit_short", "reverse_long") and pos < 0:
                close_position(i, o[i], "signal")
            if pending in ("long", "reverse_long") and pos == 0:
                open_position(i, 1, o[i])
            if pending in ("short", "reverse_short") and pos == 0:
                open_position(i, -1, o[i])
            pending = None

        # 2) Intrabar stop loss / take profit (stop checked first = pessimistic).
        if pos != 0 and (sl_pct or tp_pct):
            if pos > 0:
                stop = entry_price * (1 - sl_pct / 100) if sl_pct else None
                target = entry_price * (1 + tp_pct / 100) if tp_pct else None
                if stop is not None and l[i] <= stop:
                    close_position(i, min(stop, o[i]), "stop_loss")
                elif target is not None and h[i] >= target:
                    close_position(i, max(target, o[i]), "take_profit")
            else:
                stop = entry_price * (1 + sl_pct / 100) if sl_pct else None
                target = entry_price * (1 - tp_pct / 100) if tp_pct else None
                if stop is not None and h[i] >= stop:
                    close_position(i, max(stop, o[i]), "stop_loss")
                elif target is not None and l[i] <= target:
                    close_position(i, min(target, o[i]), "take_profit")

        # 3) Mark to market.
        equity[i] = cash + (qty * c[i] if pos > 0 else -qty * c[i] if pos < 0 else 0.0)

        # 4) Decide orders for the next bar from this bar's signals.
        if i < n - 1:
            le, lx = sig.long_entry[i], sig.long_exit[i]
            se = sig.short_entry[i] and settings.allow_short
            sx = sig.short_exit[i]
            if pos == 0:
                pending = "long" if le else "short" if se else None
            elif pos > 0:
                pending = "reverse_short" if se else "exit_long" if lx else None
            else:
                pending = "reverse_long" if le else "exit_short" if sx else None

    if pos != 0:
        close_position(n - 1, c[-1], "end_of_data")
        equity[-1] = cash

    return {
        "settings": asdict(settings),
        "metrics": compute_metrics(df, equity, trades, settings.initial_capital),
        "equity": [{"time": t, "value": round(float(v), 2)} for t, v in zip(times, equity)],
        "trades": [{k: v for k, v in asdict(t).items() if not k.startswith("_")} for t in trades],
    }


def compute_metrics(df: pd.DataFrame, equity: np.ndarray, trades: list[Trade], initial: float) -> dict:
    eq = pd.Series(equity, index=df.index)
    rets = eq.pct_change().dropna()
    years = max((df.index[-1] - df.index[0]).days / 365.25, 1e-9)
    # Bars per year estimated from the data itself (works for any timeframe).
    bars_per_year = len(df) / years if years > 0 else 252
    total_ret = eq.iloc[-1] / initial - 1
    cagr = (eq.iloc[-1] / initial) ** (1 / years) - 1 if eq.iloc[-1] > 0 else -1.0
    sharpe = rets.mean() / rets.std() * np.sqrt(bars_per_year) if rets.std() > 0 else 0.0
    downside = rets[rets < 0].std()
    sortino = rets.mean() / downside * np.sqrt(bars_per_year) if downside and downside > 0 else 0.0
    peak = eq.cummax()
    dd = eq / peak - 1
    max_dd = dd.min()
    pnls = np.array([t.pnl for t in trades]) if trades else np.array([])
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    bh = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    in_market = sum(t.bars for t in trades) / len(df) if len(df) else 0

    def r(x, d=2):
        return round(float(x), d) if np.isfinite(x) else None

    return {
        "net_profit": r(eq.iloc[-1] - initial),
        "total_return_pct": r(total_ret * 100),
        "cagr_pct": r(cagr * 100),
        "buy_hold_return_pct": r(bh * 100),
        "max_drawdown_pct": r(max_dd * 100),
        "sharpe": r(sharpe),
        "sortino": r(sortino),
        "calmar": r(cagr / abs(max_dd)) if max_dd < 0 else None,
        "total_trades": len(trades),
        "win_rate_pct": r(len(wins) / len(trades) * 100) if trades else 0.0,
        "profit_factor": r(gross_win / gross_loss) if gross_loss > 0 else None,
        "avg_trade": r(pnls.mean()) if trades else 0.0,
        "avg_win": r(wins.mean()) if len(wins) else 0.0,
        "avg_loss": r(losses.mean()) if len(losses) else 0.0,
        "largest_win": r(pnls.max()) if trades else 0.0,
        "largest_loss": r(pnls.min()) if trades else 0.0,
        "avg_bars_in_trade": r(np.mean([t.bars for t in trades]), 1) if trades else 0.0,
        "exposure_pct": r(in_market * 100),
        "final_equity": r(eq.iloc[-1]),
    }
