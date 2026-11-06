"""Bollinger Band mean reversion — buy below the lower band, sell at the middle."""
from app.core import ta

NAME = "Bollinger Reversion (Python)"
PARAMS = {"length": 20, "mult": 2.0, "stop_loss_pct": 6.0}


def generate_signals(df, length=20, mult=2.0, stop_loss_pct=6.0):
    basis, upper, lower = ta.bb(df["close"], length, mult)
    close = df["close"].to_numpy()
    return {
        "long_entry": ta.crossover(close, lower),   # closes back inside after a flush
        "long_exit": close > basis,
        "stop_loss_pct": stop_loss_pct or None,
        "plots": {"Basis": basis, "Upper": upper, "Lower": lower},
    }
