"""SMA crossover — example Python strategy.

Return boolean arrays. A True on bar i is filled at the OPEN of bar i+1.
Keys: long_entry, long_exit, short_entry, short_exit (all optional except one entry),
plus optional `plots` to draw lines on the chart.
"""
from app.core import ta

NAME = "SMA Crossover (Python)"
PARAMS = {"fast": 20, "slow": 50, "allow_short": False}


def generate_signals(df, fast=20, slow=50, allow_short=False):
    f = ta.sma(df["close"], fast)
    s = ta.sma(df["close"], slow)
    up, down = ta.crossover(f, s), ta.crossunder(f, s)
    return {
        "long_entry": up,
        "long_exit": down,
        "short_entry": down if allow_short else None,
        "short_exit": up if allow_short else None,
        "plots": {f"SMA {fast}": f, f"SMA {slow}": s},
    }
