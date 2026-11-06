import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core import data, ta
from app.core.backtest import Settings, Signals, run_backtest
from app.core.plugins import list_plugins, run_plugin
from app.pine.interpreter import PineRuntimeError, run_pine
from app.pine.lexer import PineSyntaxError
from app.server import app


@pytest.fixture(scope="module")
def df():
    return data.sample_data("TEST", bars=600, seed=7)


def flat_df(prices):
    idx = pd.bdate_range("2024-01-01", periods=len(prices))
    p = np.asarray(prices, float)
    return pd.DataFrame({"open": p, "high": p, "low": p, "close": p, "volume": 1.0}, index=idx)


# --- indicators ---------------------------------------------------------------
def test_sma_ema_rsi_match_pandas(df):
    c = df["close"]
    assert np.allclose(ta.sma(c, 10)[9:], c.rolling(10).mean()[9:])
    assert np.isnan(ta.ema(c, 10)[8]) and not np.isnan(ta.ema(c, 10)[9])
    r = ta.rsi(c, 14)
    assert np.nanmin(r) >= 0 and np.nanmax(r) <= 100


def test_crossover():
    a = np.array([1, 2, 3, 2, 1], float)
    b = np.array([2, 2, 2, 2, 2], float)
    assert ta.crossover(a, b).tolist() == [False, False, True, False, False]
    assert ta.crossunder(a, b).tolist() == [False, False, False, False, True]


# --- backtester ---------------------------------------------------------------
def test_fill_next_open_and_pnl():
    d = flat_df([10, 10, 20, 20, 20])
    d["open"] = [10, 10, 20, 20, 20]
    entry = np.array([1, 0, 0, 0, 0], bool)      # signal bar 0 -> fills at bar 1 open (10)
    exit_ = np.array([0, 0, 1, 0, 0], bool)      # signal bar 2 -> fills at bar 3 open (20)
    res = run_backtest(d, Signals(entry, exit_), Settings(commission_pct=0, slippage_pct=0))
    t = res["trades"][0]
    assert t["entry_price"] == 10 and t["exit_price"] == 20
    assert res["metrics"]["total_return_pct"] == pytest.approx(100.0)


def test_stop_loss_triggers():
    d = flat_df([100, 100, 100, 100])
    d.loc[d.index[2], "low"] = 90
    res = run_backtest(d, Signals(np.array([1, 0, 0, 0], bool)),
                       Settings(commission_pct=0, slippage_pct=0, stop_loss_pct=5))
    assert res["trades"][0]["exit_reason"] == "stop_loss"
    assert res["trades"][0]["exit_price"] == pytest.approx(95)


def test_short_trade_profits_when_price_falls():
    d = flat_df([100, 100, 80, 80])
    res = run_backtest(d, Signals(None, None, np.array([1, 0, 0, 0], bool), np.array([0, 0, 1, 0], bool)),
                       Settings(commission_pct=0, slippage_pct=0))
    assert res["trades"][0]["direction"] == "short"
    assert res["trades"][0]["pnl"] > 0


# --- pine ---------------------------------------------------------------------
def test_pine_indicator(df):
    res = run_pine('indicator("T", overlay=true)\nl = input.int(5, "Len")\nplot(ta.sma(close, l), "S")', df)
    assert res["overlay"] and res["plots"][0]["title"] == "S"
    assert np.allclose(res["plots"][0]["data"][4:], ta.sma(df["close"], 5)[4:])


def test_pine_inputs_override(df):
    res = run_pine('indicator("T")\nl = input.int(5, "Len")\nplot(ta.sma(close, l))', df, {"Len": 10})
    assert np.isnan(res["plots"][0]["data"][8]) and not np.isnan(res["plots"][0]["data"][9])


def test_pine_strategy_if_else_and_history(df):
    src = """strategy("S")
f = ta.ema(close, 5)
if f > f[1]
    strategy.entry("L", strategy.long)
else
    strategy.close("L")
"""
    sig = run_pine(src, df)["signals"]
    assert sig.long_entry.any() and sig.long_exit.any()
    assert not (sig.long_entry & sig.long_exit).any()


def test_pine_user_function_and_tuple(df):
    src = """indicator("F")
double(x) => x * 2
[m, s, h] = ta.macd(close, 12, 26, 9)
plot(double(m))
"""
    res = run_pine(src, df)
    assert np.allclose(np.nan_to_num(res["plots"][0]["data"]), np.nan_to_num(2 * ta.macd(df["close"])[0]))


def test_pine_errors_have_line_numbers(df):
    with pytest.raises(PineRuntimeError, match="Line 2"):
        run_pine('indicator("x")\nplot(foo.bar(close))', df)
    with pytest.raises(PineSyntaxError):
        run_pine('indicator("x"\nplot(close', df)


# --- plugins & API -------------------------------------------------------------
def test_all_bundled_plugins_run(df):
    plugs = list_plugins()
    assert len(plugs) >= 8
    for p in plugs:
        res = run_plugin(p["kind"], p["file"], df)
        if p["kind"] == "strategy":
            assert run_backtest(df, res["signals"])["metrics"]["final_equity"] > 0


def test_api_endpoints():
    c = TestClient(app)
    assert c.get("/api/symbols").status_code == 200
    assert len(c.get("/api/candles?symbol=SAMPLE").json()["candles"]) > 100
    r = c.post("/api/backtest", json={"symbol": "SAMPLE", "file": "ema_cross.pine"})
    assert r.status_code == 200 and "metrics" in r.json()
    r = c.post("/api/compare", json={"symbol": "SAMPLE", "strategies": [{"file": "sma_cross.py"}, {"file": "ema_cross.pine"}]})
    assert len(r.json()["results"]) == 2
    r = c.post("/api/optimize", json={"symbol": "SAMPLE", "file": "sma_cross.py", "grid": {"fast": [10, 20], "slow": [50, 100]}})
    assert r.json()["count"] == 4
    assert c.post("/api/run", json={"kind": "indicator", "file": "rsi_bands.pine", "code": "bad("}).status_code == 422
    assert c.put("/api/plugins/indicator/..%2Fevil.py", json={"code": "x"}).status_code in (400, 404)


# --- bundled real data: BTC & gold -------------------------------------------
@pytest.mark.parametrize("sym", ["BTC", "GOLD"])
def test_bundled_btc_and_gold(sym):
    d = data.load(sym, "1d")
    assert len(d) >= 900 and (d["high"] >= d["low"]).all()
    w = data.load(sym, "1wk")
    assert len(w) < len(d) / 4 and w["high"].max() == pytest.approx(d["high"].max())
    assert sym in data.list_sources()


def test_api_btc_gold_and_intervals():
    c = TestClient(app)
    j = c.get("/api/symbols").json()
    assert j["symbols"][:2] == ["BTC", "GOLD"] and "1wk" in j["intervals"]
    for sym in ("BTC", "GOLD"):
        r = c.post("/api/backtest", json={"symbol": sym, "interval": "1wk", "file": "ema_cross.pine"})
        assert r.status_code == 200, r.text
    assert c.get("/api/candles?symbol=SAMPLE&interval=1h").status_code == 400
