"""Donchian channel breakout (Turtle-style) — example trend-following strategy."""
import numpy as np

from app.core import ta

NAME = "Donchian Breakout (Python)"
PARAMS = {"entry_length": 55, "exit_length": 20, "atr_stop_mult": 0.0}


def generate_signals(df, entry_length=55, exit_length=20, atr_stop_mult=0.0):
    # Use the PREVIOUS bar's channel so today's high can break it (no look-ahead).
    upper = ta.shift(ta.highest(df["high"], entry_length), 1)
    exit_low = ta.shift(ta.lowest(df["low"], exit_length), 1)
    close = df["close"].to_numpy()
    out = {
        "long_entry": close > upper,
        "long_exit": close < exit_low,
        "plots": {"Breakout": upper, "Exit": exit_low},
    }
    if atr_stop_mult > 0:
        atr = ta.atr(df["high"], df["low"], df["close"], 20)
        out["stop_loss_pct"] = float(np.nanmedian(atr / close) * 100 * atr_stop_mult)
    return out
