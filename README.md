# ◆ OpenTrade

A self-hosted, TradingView-style charting and backtesting app. Write indicators and strategies in
**Pine Script** or **Python**, backtest them, compare several strategies side by side, and grid-search
their parameters. It runs as a **website on GitHub Pages** (nothing to install) or on your own computer.

**▶ Open the app: https://YOUR_USERNAME.github.io/opentrade/**

![tests](https://github.com/YOUR_USERNAME/opentrade/actions/workflows/tests.yml/badge.svg)

## Features

- **Candlestick charts** with volume, crosshair OHLC readout, and synced indicator panes
- **Pine Script v5 interpreter** — run `indicator()` and `strategy()` scripts locally
- **Python indicators & strategies** — drop a `.py` file into a folder and it appears in the app
- **In-browser script editor** — create, edit, save and apply scripts (Ctrl/Cmd + S)
- **Strategy Tester** — equity curve vs buy & hold, trade list, 16 performance metrics
- **Compare Strategies** — overlay equity curves and rank by Sharpe
- **Optimizer** — grid search over parameters, one click to apply the best set
- **Realistic fills** — signals execute at the next bar's open, with commission, slippage, stop loss / take profit
- **Bitcoin & Gold built in** — real daily history ships with the repo (BTC from 2021, gold from 2023), so it works offline
- **Timeframes** — 1 hour, 4 hours, 1 day, 1 week
- **Live updates** — ⟳ Refresh pulls the latest prices from Yahoo Finance; "＋ Add ticker…" adds any Yahoo symbol (ETH-USD, AAPL, SI=F, EURUSD=X…)
- **Your own data** — upload any CSV

## Use it as a website (GitHub Pages)

One-time setup, about 2 minutes:

1. In your repo on GitHub, go to **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
3. Go to the **Actions** tab, click **deploy-pages** → **Run workflow** (after that it redeploys on every push).
4. When it turns green, open **https://YOUR_USERNAME.github.io/opentrade/**.

The first visit downloads the Python engine (~25 MB, about 10 seconds); after that the browser caches it.

**How it works:** the page runs the same Python code as the server, inside your browser via
[Pyodide](https://pyodide.org) (Python compiled to WebAssembly). The badge in the top right shows
**● running in your browser**.

**Differences from running it on your computer:**

| | Website (GitHub Pages) | Your computer (`python run.py`) |
|---|---|---|
| Live prices (⟳ Refresh, 1h/4h) | Binance: BTC = BTC/USDT, GOLD = PAXG/USDT (a token backed by 1 oz of gold) | Yahoo Finance: BTC-USD, GC=F gold futures |
| Add ticker | Crypto from Binance (ETH, SOL, …) | Any Yahoo ticker (stocks, forex, metals) |
| Scripts you save in the editor | Kept in *this browser* — click **Download** and add the file to your repo to keep it for good | Saved straight into `indicators/` / `strategies/` |
| Daily BTC/GOLD data | Updated automatically every day by the **update-prices** workflow | ⟳ Refresh |

To add a script for everyone: put the `.pine` or `.py` file in `indicators/` or `strategies/` and push.
The site picks it up on the next deploy.

## Run it on your computer

```bash
git clone https://github.com/YOUR_USERNAME/opentrade.git
cd opentrade
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Your browser opens at <http://127.0.0.1:8000> showing **BTC**. Switch to **GOLD** from the symbol menu.

## Market data

| Symbol | What it is | Source when you press ⟳ Refresh |
|---|---|---|
| `BTC` | Bitcoin / US dollar | Yahoo `BTC-USD` |
| `GOLD` | Gold / US dollar | Yahoo `GC=F` (COMEX gold futures, tracks spot XAU/USD closely) |

- `data/BTC.csv` and `data/GOLD.csv` are refreshed daily from Yahoo by the **update-prices** GitHub workflow (you can also run it by hand from the Actions tab).
- **1h / 4h** download the last ~2 years of hourly data from Yahoo the first time you pick them, then cache it in `data/`.
- **1wk** is built from the daily data.
- **Add more:** pick "＋ Add ticker…" in the symbol menu, or add a short name in `PRESETS` in `app/core/data.py`
  (e.g. `"ETH": "ETH-USD"`, `"SILVER": "SI=F"`).
- **Upload CSV** works too (columns: `time, open, high, low, close, volume`).

## Writing a Pine Script

Put `.pine` files in `indicators/` or `strategies/`, or use the **Script Editor** tab.

```pine
//@version=5
strategy("EMA Crossover", overlay=true)
fast = ta.ema(close, input.int(12, "Fast EMA"))
slow = ta.ema(close, input.int(26, "Slow EMA"))
plot(fast, "Fast", color=color.lime)
plot(slow, "Slow", color=color.red)

if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")
strategy.exit("SL", "Long", loss=3)   // 3% stop (percent, not ticks)
```

**Supported:** `indicator`, `strategy`, `input.*`, `plot`, `plotshape`, `hline`, `if / else if / else`,
ternaries, history refs `x[1]`, `:=`, tuples `[a, b] = ...`, user functions `f(x) => ...`, `for` loops,
`ta.*` (sma, ema, rma, wma, hma, vwma, rsi, macd, bb, atr, tr, stoch, cci, stdev, highest, lowest, change,
mom, roc, crossover, crossunder, cross, supertrend, barssince, valuewhen, pivothigh, pivotlow, vwap, obv,
cum, rising, falling), `math.*`, `na`, `nz`, `strategy.entry/close/close_all/exit`.

**Not supported (yet):** self-referencing recursion like `x := nz(x[1]) + 1`, `strategy.position_size`,
`request.security`, drawings (`line.new`, `label.new` are ignored), `switch`, `while`, libraries.
The engine evaluates whole series at once (vectorized), which is why stateful recursion is out of scope.
For that kind of logic, write a Python strategy.

> `strategy.exit` takes `loss=` / `profit=` as **percent** of entry price here (TradingView uses ticks).

## Writing a Python indicator

```python
# indicators/my_sma.py
from app.core import ta

NAME = "My SMA"
OVERLAY = True                  # False = separate pane below the chart
PARAMS = {"length": 20}         # editable in the UI ⚙
HLINES = []                     # optional horizontal lines, e.g. [30, 70]

def compute(df, length=20):     # df: open, high, low, close, volume
    return {"SMA": ta.sma(df["close"], length)}
```

## Writing a Python strategy

```python
# strategies/my_strategy.py
from app.core import ta

NAME = "My Strategy"
PARAMS = {"fast": 9, "slow": 21}

def generate_signals(df, fast=9, slow=21):
    f, s = ta.ema(df["close"], fast), ta.ema(df["close"], slow)
    return {
        "long_entry":  ta.crossover(f, s),
        "long_exit":   ta.crossunder(f, s),
        "short_entry": None,               # optional
        "short_exit":  None,               # optional
        "stop_loss_pct": None,             # optional, overrides UI setting
        "plots": {"Fast": f, "Slow": s},   # optional lines on the chart
    }
```

A `True` on bar *i* fills at the **open of bar *i+1***, so you can't accidentally trade on information you
wouldn't have had. An entry signal while in the opposite position reverses it.

## Backtest settings

| Setting | Meaning |
|---|---|
| Position size | `% of equity`, `fixed cash`, `fixed quantity`, or `% equity risked` (sizes the trade so hitting the stop loses that % — needs a stop loss) |
| Commission | Percent of trade value, charged on entry and exit |
| Slippage | Adverse price move applied to every fill |
| Stop loss / Take profit | Percent from entry, checked intrabar on high/low (if both hit in one bar, the stop is assumed first) |
| From / To | Restrict the test to a date range — use this for out-of-sample checks after optimizing |

## Project layout

```
index.html            the app (works on GitHub Pages and with run.py)
static/engine.js      picks server mode or in-browser Python mode
app/
  api_core.py         all API logic, shared by both modes
  browser.py          bridge for in-browser Python (Pyodide)
  core/data.py        data loading: CSV, sample, Yahoo
  core/ta.py          indicator math shared by Pine and Python
  core/backtest.py    bar-by-bar engine + metrics
  core/plugins.py     discovers/runs .py and .pine scripts
  pine/               Pine Script lexer, parser, interpreter
  server.py           FastAPI server for `python run.py`
tools/build_manifest.py  file list the website loads (built automatically on deploy)
.github/workflows/    tests, Pages deploy, daily price update
indicators/           your indicators (.pine / .py)
strategies/           your strategies (.pine / .py)
data/                 CSV market data
static/               web UI
tests/                pytest suite (runs in GitHub Actions)
```

## Running tests

```bash
pip install pytest httpx
pytest -q
```

## Security note

The Script Editor saves and runs Python code on the machine hosting the server. `run.py` binds to
`127.0.0.1` by default so only you can reach it. Don't expose it on a public network.

## Disclaimer

For research and education. Backtest results are not a guarantee of future performance.

## License

MIT. Charts by [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) (Apache 2.0, bundled in `static/vendor`).
