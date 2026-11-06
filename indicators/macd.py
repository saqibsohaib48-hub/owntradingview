"""MACD — example Python indicator.

Any file in this folder that defines `compute(df, **params)` shows up in the app.
`df` has columns: open, high, low, close, volume (DatetimeIndex).
Return a dict of {line name: series}. Use OVERLAY = True to draw on the price chart.
"""
from app.core import ta

NAME = "MACD (Python)"
OVERLAY = False
PARAMS = {"fast": 12, "slow": 26, "signal": 9}
HLINES = [0]


def compute(df, fast=12, slow=26, signal=9):
    line, sig, hist = ta.macd(df["close"], fast, slow, signal)
    return {"MACD": line, "Signal": sig, "Histogram": hist}
