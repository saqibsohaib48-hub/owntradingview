"""Keltner Channels — example overlay indicator using pandas directly."""
from app.core import ta

NAME = "Keltner Channels (Python)"
OVERLAY = True
PARAMS = {"length": 20, "atr_length": 10, "mult": 2.0}


def compute(df, length=20, atr_length=10, mult=2.0):
    mid = df["close"].ewm(span=length, adjust=False).mean()
    band = mult * ta.atr(df["high"], df["low"], df["close"], atr_length)
    return {"Upper": mid + band, "Middle": mid, "Lower": mid - band}
