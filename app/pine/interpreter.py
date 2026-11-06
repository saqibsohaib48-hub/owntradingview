"""Vectorized Pine Script evaluator.

Every series value is a numpy array covering ALL bars, so `ta.sma(close, 20)` is a
single numpy call. `if` blocks become boolean masks: statements inside them only
take effect on bars where the condition is true.

Supported: indicator()/strategy(), input.*, ta.*, math.*, plot/plotshape/hline,
strategy.entry/close/close_all/exit(stop%/profit%), if/else, ternaries, history
references x[n], user functions, tuple destructuring.

Not supported (needs bar-by-bar state): self-referencing recursion such as
`x := nz(x[1]) + 1`, strategy.position_size, loops over history, drawings.
"""
from __future__ import annotations

import math as _math

import numpy as np
import pandas as pd

from ..core import ta
from ..core.backtest import Signals
from .lexer import PineSyntaxError
from .parser import (Assign, Binary, Call, ExprStmt, For, FuncDef, If, Index, Name, Num, Str,
                     Ternary, TupleLit, Unary, parse)


class PineRuntimeError(Exception):
    pass


COLORS = {
    "aqua": "#00BCD4", "black": "#363A45", "blue": "#2962FF", "fuchsia": "#E040FB", "gray": "#787B86",
    "green": "#4CAF50", "lime": "#00E676", "maroon": "#880E4F", "navy": "#311B92", "olive": "#808000",
    "orange": "#FF9800", "purple": "#9C27B0", "red": "#FF5252", "silver": "#B2B5BE", "teal": "#00897B",
    "white": "#FFFFFF", "yellow": "#FFEB3B",
}
PALETTE = ["#2962FF", "#FF9800", "#9C27B0", "#00897B", "#E040FB", "#FFEB3B", "#00BCD4"]


def _arr(x, n):
    if isinstance(x, np.ndarray):
        return x.astype(float) if x.dtype != bool else x
    if x is None:
        return np.full(n, np.nan)
    if isinstance(x, (bool, np.bool_)):
        return np.full(n, bool(x))
    return np.full(n, float(x))


def _bool(x, n):
    a = np.asarray(_arr(x, n))
    if a.dtype == bool:
        return a
    return np.where(np.isnan(a), False, a != 0)


def _scalar(x):
    """Pine 'simple' arguments (lengths, multipliers) — take the last valid value."""
    if isinstance(x, np.ndarray):
        v = x[~pd.isna(x)]
        return float(v[-1]) if len(v) else float("nan")
    return x


def _int(x):
    return int(round(float(_scalar(x))))


class Scope:
    def __init__(self, parent: "Scope | None" = None):
        self.vars: dict = {}
        self.parent = parent

    def get(self, name):
        s = self
        while s is not None:
            if name in s.vars:
                return s.vars[name]
            s = s.parent
        raise KeyError(name)

    def owner(self, name):
        s = self
        while s is not None:
            if name in s.vars:
                return s
            s = s.parent
        return None


class PineInterpreter:
    def __init__(self, source: str, df: pd.DataFrame, params: dict | None = None):
        self.source = source
        self.df = df
        self.n = len(df)
        self.params = params or {}
        self.meta = {"kind": "indicator", "title": "Pine Script", "overlay": False}
        self.inputs: list[dict] = []
        self.plots: list[dict] = []
        self.shapes: list[dict] = []
        self.hlines: list[dict] = []
        self.warnings: list[str] = []
        self.funcs: dict[str, FuncDef] = {}
        self.entries: dict[str, str] = {}  # entry id -> "long"/"short"
        n = self.n
        self.sig = {k: np.zeros(n, dtype=bool) for k in ("long_entry", "long_exit", "short_entry", "short_exit")}
        self.stop_loss_pct = None
        self.take_profit_pct = None
        self.globals = Scope()
        o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
        g = self.globals.vars
        g.update(open=o, high=h, low=l, close=c, volume=v, hl2=(h + l) / 2, hlc3=(h + l + c) / 3,
                 ohlc4=(o + h + l + c) / 4, hlcc4=(h + l + 2 * c) / 4,
                 bar_index=np.arange(n, dtype=float), last_bar_index=float(n - 1),
                 time=np.array([t.timestamp() * 1000 for t in df.index]), na=np.nan, true=True, false=False)
        self.constants = {
            "strategy.long": "long", "strategy.short": "short",
            "strategy.percent_of_equity": "percent_of_equity", "strategy.cash": "cash", "strategy.fixed": "fixed",
            "ta.tr": ta.tr(h, l, c), "ta.vwap": ta.vwap(h, l, c, v),
            "ta.obv": np.cumsum(np.sign(np.nan_to_num(ta.change(c))) * v),
            "math.pi": _math.pi, "math.e": _math.e,
            "barstate.islast": np.arange(n) == n - 1, "barstate.isfirst": np.arange(n) == 0,
            "barstate.isconfirmed": np.ones(n, dtype=bool),
            "dayofweek": df.index.dayofweek.to_numpy(float) + 1, "month": df.index.month.to_numpy(float),
            "year": df.index.year.to_numpy(float), "hour": df.index.hour.to_numpy(float),
        }
        for name, hexv in COLORS.items():
            self.constants[f"color.{name}"] = hexv
        for k in ("location", "shape", "size", "plot", "display", "hline", "position", "extend", "xloc", "yloc",
                  "format", "scale", "currency", "text", "font", "line", "label", "alert", "barmerge", "order"):
            self.constants[k] = k  # namespaces used only as enum prefixes

    # ------------------------------------------------------------------ run
    def run(self) -> dict:
        try:
            program = parse(self.source)
        except PineSyntaxError:
            raise
        mask = np.ones(self.n, dtype=bool)
        self.exec_block(program, self.globals, mask)
        return self.result()

    def result(self) -> dict:
        out = {**self.meta, "inputs": self.inputs, "plots": self.plots, "shapes": self.shapes,
               "hlines": self.hlines, "warnings": self.warnings}
        if self.meta["kind"] == "strategy":
            out["signals"] = Signals(self.sig["long_entry"], self.sig["long_exit"], self.sig["short_entry"],
                                     self.sig["short_exit"], self.stop_loss_pct, self.take_profit_pct)
        return out

    # ------------------------------------------------------------ statements
    def exec_block(self, stmts, scope: Scope, mask):
        last = None
        for st in stmts:
            last = self.exec_stmt(st, scope, mask)
        return last

    def exec_stmt(self, st, scope: Scope, mask):
        try:
            if isinstance(st, ExprStmt):
                return self.eval(st.expr, scope, mask)
            if isinstance(st, Assign):
                return self.exec_assign(st, scope, mask)
            if isinstance(st, If):
                return self.exec_if(st, scope, mask)
            if isinstance(st, FuncDef):
                self.funcs[st.name] = st
                return None
            if isinstance(st, For):
                start, end = _int(self.eval(st.start, scope, mask)), _int(self.eval(st.end, scope, mask))
                step = 1 if end >= start else -1
                for k in range(start, end + step, step):
                    scope.vars[st.var] = float(k)
                    self.exec_block(st.body, scope, mask)
                return None
        except (PineRuntimeError, PineSyntaxError):
            raise
        except Exception as e:  # attach line numbers to unexpected failures
            line = getattr(st, "line", None)
            raise PineRuntimeError(f"Line {line}: {e}" if line else str(e)) from e
        raise PineRuntimeError(f"Unknown statement {st!r}")

    def exec_assign(self, st: Assign, scope: Scope, mask):
        val = self.eval(st.value, scope, mask)
        if len(st.targets) > 1:
            if not isinstance(val, (tuple, list)) or len(val) != len(st.targets):
                raise PineRuntimeError(f"Line {st.line}: cannot unpack into {len(st.targets)} variables")
            for name, v in zip(st.targets, val):
                scope.vars[name] = v
            return val
        name = st.targets[0]
        if st.op in ("+=", "-=", "*=", "/="):
            val = self.binop(st.op[0], self.lookup(name, scope), val)
        if st.reassign:
            owner = scope.owner(name) or scope
            old = owner.vars.get(name, np.nan)
            if mask.all():
                owner.vars[name] = val
            else:
                owner.vars[name] = self._where(mask, val, old)
        else:
            if mask.all():
                scope.vars[name] = val
            else:
                scope.vars[name] = self._where(mask, val, np.nan)
        return val

    def _where(self, mask, a, b):
        if isinstance(a, str) or isinstance(b, str):
            return a
        a_arr, b_arr = _arr(a, self.n), _arr(b, self.n)
        if a_arr.dtype == bool and b_arr.dtype != bool:
            a_arr = a_arr.astype(float)
        if b_arr.dtype == bool and a_arr.dtype != bool:
            b_arr = b_arr.astype(float)
        return np.where(mask, a_arr, b_arr)

    def exec_if(self, st: If, scope: Scope, mask):
        cond = _bool(self.eval(st.cond, scope, mask), self.n)
        self.exec_block(st.body, Scope(scope), mask & cond)
        if st.orelse:
            self.exec_block(st.orelse, Scope(scope), mask & ~cond)
        return None

    # ----------------------------------------------------------- expressions
    def lookup(self, name, scope: Scope):
        try:
            return scope.get(name)
        except KeyError:
            pass
        if name in self.constants:
            return self.constants[name]
        if "." in name and name.split(".")[0] in self.constants:
            return name  # enum-like constant e.g. shape.triangleup
        raise PineRuntimeError(f"Undeclared identifier '{name}'")

    def eval(self, node, scope: Scope, mask):
        if isinstance(node, Num):
            return node.value
        if isinstance(node, Str):
            return node.value
        if isinstance(node, Name):
            return self.lookup(node.name, scope)
        if isinstance(node, TupleLit):
            return tuple(self.eval(x, scope, mask) for x in node.items)
        if isinstance(node, Index):
            target = self.eval(node.target, scope, mask)
            off = _int(self.eval(node.offset, scope, mask))
            if isinstance(target, (int, float)):
                return target
            return ta.shift(_arr(target, self.n), off)
        if isinstance(node, Unary):
            v = self.eval(node.operand, scope, mask)
            if node.op == "not":
                return ~_bool(v, self.n) if isinstance(v, np.ndarray) else not v
            if node.op == "-":
                return -_arr(v, self.n).astype(float) if isinstance(v, np.ndarray) else -v
            return v
        if isinstance(node, Binary):
            if node.op in ("and", "or"):
                a = self.eval(node.left, scope, mask)
                b = self.eval(node.right, scope, mask)
                if not isinstance(a, np.ndarray) and not isinstance(b, np.ndarray):
                    return (bool(a) and bool(b)) if node.op == "and" else (bool(a) or bool(b))
                a, b = _bool(a, self.n), _bool(b, self.n)
                return a & b if node.op == "and" else a | b
            return self.binop(node.op, self.eval(node.left, scope, mask), self.eval(node.right, scope, mask))
        if isinstance(node, Ternary):
            c = self.eval(node.cond, scope, mask)
            a = self.eval(node.a, scope, mask)
            b = self.eval(node.b, scope, mask)
            if not isinstance(c, np.ndarray):
                return a if c else b
            if isinstance(a, str) or isinstance(b, str):
                return a  # colors: pick first (charts use a single color per plot)
            cb = _bool(c, self.n)
            return self._where(cb, a, b)
        if isinstance(node, Call):
            return self.call(node, scope, mask)
        raise PineRuntimeError(f"Cannot evaluate {node!r}")

    def binop(self, op, a, b):
        if isinstance(a, str) or isinstance(b, str):
            if op == "+":
                return str(a) + str(b)
            if op in ("==", "!="):
                return (a == b) if op == "==" else (a != b)
            raise PineRuntimeError(f"Operator {op} not valid for strings")
        vec = isinstance(a, np.ndarray) or isinstance(b, np.ndarray)
        if vec:
            a = _arr(a, self.n).astype(float)
            b = _arr(b, self.n).astype(float)
        with np.errstate(all="ignore"):
            if op == "+": return a + b
            if op == "-": return a - b
            if op == "*": return a * b
            if op == "/":
                if vec:
                    return np.where(b == 0, np.nan, a / np.where(b == 0, 1, b))
                return a / b if b else float("nan")
            if op == "%": return np.mod(a, b) if vec else (a % b if b else float("nan"))
            if op == ">": return a > b
            if op == "<": return a < b
            if op == ">=": return a >= b
            if op == "<=": return a <= b
            if op == "==": return a == b
            if op == "!=": return a != b
        raise PineRuntimeError(f"Unknown operator {op}")

    # ------------------------------------------------------------ functions
    def call(self, node: Call, scope: Scope, mask):
        name = node.func
        if name in self.funcs:
            fd = self.funcs[name]
            local = Scope(self.globals)
            args = [self.eval(a, scope, mask) for a in node.args]
            for p, v in zip(fd.params, args):
                local.vars[p] = v
            for k, vnode in node.kwargs.items():
                local.vars[k] = self.eval(vnode, scope, mask)
            return self.exec_block(fd.body, local, np.ones(self.n, dtype=bool))

        args = [self.eval(a, scope, mask) for a in node.args]
        kwargs = {k: self.eval(v, scope, mask) for k, v in node.kwargs.items()}
        fn = BUILTINS.get(name)
        if fn is None:
            raise PineRuntimeError(f"Line {node.line}: function '{name}' is not supported")
        try:
            return fn(self, mask, *args, **kwargs)
        except TypeError as e:
            raise PineRuntimeError(f"Line {node.line}: bad arguments for {name}(): {e}") from e

    # helpers for builtins
    def series(self, x):
        return _arr(x, self.n).astype(float)


# ============================================================== builtins ====
BUILTINS: dict = {}


def builtin(*names):
    def deco(fn):
        for nm in names:
            BUILTINS[nm] = fn
        return fn
    return deco


def _ignore(*_a, **_k):
    return None


for _nm in ("bgcolor", "fill", "alertcondition", "alert", "barcolor", "plotchar", "plotcandle",
            "plotbar", "plotarrow", "label.new", "line.new", "box.new", "table.new", "table.cell",
            "runtime.error", "log.info", "log.warning", "label.delete", "line.delete"):
    BUILTINS[_nm] = _ignore


# --- declarations ---
@builtin("indicator", "study")
def _indicator(I, mask, title="Indicator", shorttitle=None, overlay=False, **kw):
    I.meta.update(kind="indicator", title=str(title), overlay=bool(overlay))


@builtin("strategy")
def _strategy(I, mask, title="Strategy", shorttitle=None, overlay=True, **kw):
    I.meta.update(kind="strategy", title=str(title), overlay=bool(overlay))
    I.meta["strategy_args"] = {k: (v if not isinstance(v, np.ndarray) else None) for k, v in kw.items()}


# --- inputs ---
def _make_input(kind):
    def fn(I, mask, defval=None, title=None, minval=None, maxval=None, step=None, options=None, **kw):
        key = title or f"input_{len(I.inputs)}"
        val = I.params.get(key, defval)
        if kind == "int":
            val = int(float(val))
        elif kind == "float":
            val = float(val)
        elif kind == "bool":
            val = val if isinstance(val, bool) else str(val).lower() in ("true", "1", "yes")
        if not any(i["name"] == key for i in I.inputs):
            I.inputs.append({"name": key, "type": kind, "default": defval if not isinstance(defval, np.ndarray) else "close",
                             "value": val if not isinstance(val, np.ndarray) else "close",
                             "min": minval, "max": maxval, "step": step,
                             "options": list(options) if isinstance(options, tuple) else None})
        if kind == "source" and isinstance(val, str):
            return I.globals.vars.get(val, I.globals.vars["close"])
        return val
    return fn


for _k in ("int", "float", "bool", "string", "source", "color", "timeframe", "symbol", "session", "price", "time"):
    BUILTINS[f"input.{_k}"] = _make_input(_k if _k in ("int", "float", "bool", "source") else "string")
BUILTINS["input"] = lambda I, mask, defval=None, title=None, **kw: _make_input(
    "bool" if isinstance(defval, bool) else "int" if isinstance(defval, int) else
    "float" if isinstance(defval, float) else "source" if isinstance(defval, np.ndarray) else "string")(I, mask, defval, title, **kw)


# --- drawing outputs ---
@builtin("plot")
def _plot(I, mask, series, title=None, color=None, linewidth=1, style=None, **kw):
    if isinstance(series, (int, float)) or series is None:
        series = I.series(series)
    I.plots.append({"title": str(title or f"Plot {len(I.plots) + 1}"),
                    "color": color if isinstance(color, str) else PALETTE[len(I.plots) % len(PALETTE)],
                    "linewidth": int(_scalar(linewidth) or 1),
                    "style": "histogram" if style and "histogram" in str(style) or style and "columns" in str(style) else "line",
                    "data": I.series(series)})
    return series


@builtin("plotshape")
def _plotshape(I, mask, series, title=None, style="shape.circle", location="location.abovebar",
               color=None, text="", **kw):
    cond = _bool(series, I.n) & mask
    I.shapes.append({"title": str(title or "Shape"), "where": cond,
                     "position": "aboveBar" if "above" in str(location) else "belowBar",
                     "shape": "arrowDown" if "down" in str(style) else "arrowUp" if "up" in str(style) else "circle",
                     "color": color if isinstance(color, str) else "#FF9800", "text": str(text or "")})


@builtin("hline")
def _hline(I, mask, price, title=None, color=None, **kw):
    I.hlines.append({"title": str(title or "hline"), "price": float(_scalar(price)),
                     "color": color if isinstance(color, str) else "#787B86"})


@builtin("color.new", "color.rgb")
def _color_new(I, mask, c, *a, **kw):
    if isinstance(c, (int, float)) and a:  # color.rgb(r, g, b)
        return "#%02x%02x%02x" % (int(c), int(a[0]), int(a[1]))
    return c


# --- strategy orders ---
@builtin("strategy.entry")
def _entry(I, mask, id, direction=None, qty=None, limit=None, stop=None, when=None, comment=None, **kw):
    d = direction if direction in ("long", "short") else "long"
    cond = mask.copy()
    if when is not None:
        cond &= _bool(when, I.n)
    I.entries[str(id)] = d
    I.sig[f"{d}_entry"] |= cond


@builtin("strategy.order")
def _order(I, mask, id, direction=None, *a, **kw):
    return _entry(I, mask, id, direction, **{k: v for k, v in kw.items() if k == "when"})


@builtin("strategy.close")
def _close(I, mask, id, when=None, **kw):
    cond = mask.copy()
    if when is not None:
        cond &= _bool(when, I.n)
    d = I.entries.get(str(id))
    if d is None:
        for dd in ("long", "short"):
            I.sig[f"{dd}_exit"] |= cond
    else:
        I.sig[f"{d}_exit"] |= cond


@builtin("strategy.close_all")
def _close_all(I, mask, when=None, **kw):
    cond = mask.copy()
    if when is not None:
        cond &= _bool(when, I.n)
    I.sig["long_exit"] |= cond
    I.sig["short_exit"] |= cond


@builtin("strategy.exit")
def _exit(I, mask, id=None, from_entry=None, qty=None, qty_percent=None, profit=None, limit=None,
          loss=None, stop=None, trail_points=None, trail_offset=None, when=None, **kw):
    """Supported form: percentage brackets via `profit`/`loss` given as PERCENT (e.g. loss=2 -> 2%).
    Absolute price `stop`/`limit` vary per bar and are approximated as a % distance from close."""
    c = I.globals.vars["close"]
    if loss is not None:
        I.stop_loss_pct = float(_scalar(loss))
    if profit is not None:
        I.take_profit_pct = float(_scalar(profit))
    if stop is not None and loss is None:
        dist = np.nanmedian(np.abs(c - I.series(stop)) / c * 100)
        if np.isfinite(dist):
            I.stop_loss_pct = float(dist)
            I.warnings.append("strategy.exit(stop=price) approximated as a fixed % stop of %.2f%%" % dist)
    if limit is not None and profit is None:
        dist = np.nanmedian(np.abs(I.series(limit) - c) / c * 100)
        if np.isfinite(dist):
            I.take_profit_pct = float(dist)
            I.warnings.append("strategy.exit(limit=price) approximated as a fixed % target of %.2f%%" % dist)


@builtin("strategy.cancel", "strategy.cancel_all", "strategy.risk.allow_entry_in")
def _noop(I, mask, *a, **kw):
    return None


# --- ta.* ---
def _src_len(fn):
    return lambda I, mask, source, length, *a, **kw: fn(I.series(source), _int(length))


for _nm, _fn in {"sma": ta.sma, "ema": ta.ema, "rma": ta.rma, "wma": ta.wma, "hma": ta.hma, "rsi": ta.rsi,
                 "stdev": ta.stdev, "mom": ta.mom, "roc": ta.roc, "cci": ta.cci}.items():
    BUILTINS[f"ta.{_nm}"] = _src_len(_fn)
    BUILTINS[_nm] = BUILTINS[f"ta.{_nm}"]  # Pine v4 names


def _hl(kind):
    def fn(I, mask, a, b=None, **kw):
        if b is None:  # ta.highest(length)
            src = I.globals.vars["high" if kind == "highest" else "low"]
            return getattr(ta, kind)(src, _int(a))
        return getattr(ta, kind)(I.series(a), _int(b))
    return fn


BUILTINS["ta.highest"] = BUILTINS["highest"] = _hl("highest")
BUILTINS["ta.lowest"] = BUILTINS["lowest"] = _hl("lowest")

BUILTINS["ta.vwma"] = BUILTINS["vwma"] = lambda I, m, source, length, **k: ta.vwma(I.series(source), I.globals.vars["volume"], _int(length))
BUILTINS["ta.change"] = BUILTINS["change"] = lambda I, m, source, length=1, **k: ta.change(I.series(source), _int(length))
BUILTINS["ta.atr"] = BUILTINS["atr"] = lambda I, m, length, **k: ta.atr(I.globals.vars["high"], I.globals.vars["low"], I.globals.vars["close"], _int(length))
BUILTINS["ta.tr"] = lambda I, m, *a, **k: I.constants["ta.tr"]
BUILTINS["ta.crossover"] = BUILTINS["crossover"] = lambda I, m, a, b, **k: ta.crossover(I.series(a), I.series(b))
BUILTINS["ta.crossunder"] = BUILTINS["crossunder"] = lambda I, m, a, b, **k: ta.crossunder(I.series(a), I.series(b))
BUILTINS["ta.cross"] = BUILTINS["cross"] = lambda I, m, a, b, **k: ta.cross(I.series(a), I.series(b))
BUILTINS["ta.macd"] = lambda I, m, source, fastlen=12, slowlen=26, siglen=9, **k: ta.macd(I.series(source), _int(fastlen), _int(slowlen), _int(siglen))
BUILTINS["ta.bb"] = lambda I, m, series, length, mult, **k: ta.bb(I.series(series), _int(length), float(_scalar(mult)))
BUILTINS["ta.stoch"] = BUILTINS["stoch"] = lambda I, m, source, high, low, length, **k: ta.stoch(I.series(source), I.series(high), I.series(low), _int(length))
BUILTINS["ta.supertrend"] = lambda I, m, factor, atrPeriod, **k: ta.supertrend(I.globals.vars["high"], I.globals.vars["low"], I.globals.vars["close"], float(_scalar(factor)), _int(atrPeriod))
BUILTINS["ta.barssince"] = BUILTINS["barssince"] = lambda I, m, condition, **k: ta.barssince(_bool(condition, I.n))
BUILTINS["ta.valuewhen"] = BUILTINS["valuewhen"] = lambda I, m, condition, source, occurrence=0, **k: ta.valuewhen(_bool(condition, I.n), I.series(source), _int(occurrence))
BUILTINS["ta.cum"] = BUILTINS["cum"] = lambda I, m, source, **k: np.nancumsum(I.series(source))
BUILTINS["ta.vwap"] = lambda I, m, *a, **k: I.constants["ta.vwap"]
BUILTINS["ta.rising"] = BUILTINS["rising"] = lambda I, m, source, length, **k: I.series(source) > ta.highest(ta.shift(I.series(source), 1), _int(length))
BUILTINS["ta.falling"] = BUILTINS["falling"] = lambda I, m, source, length, **k: I.series(source) < ta.lowest(ta.shift(I.series(source), 1), _int(length))


def _pivot(kind):
    def fn(I, mask, a, b, c=None, **kw):
        if c is None:
            src = I.globals.vars["high" if kind == "pivothigh" else "low"]
            return getattr(ta, kind)(src, _int(a), _int(b))
        return getattr(ta, kind)(I.series(a), _int(b), _int(c))
    return fn


BUILTINS["ta.pivothigh"] = BUILTINS["pivothigh"] = _pivot("pivothigh")
BUILTINS["ta.pivotlow"] = BUILTINS["pivotlow"] = _pivot("pivotlow")


# --- na / math ---
def _vec(fn):
    def wrap(I, mask, *args, **kw):
        if any(isinstance(a, np.ndarray) for a in args):
            args = [I.series(a) if isinstance(a, np.ndarray) else a for a in args]
        with np.errstate(all="ignore"):
            return fn(*args)
    return wrap


BUILTINS["na"] = lambda I, m, x, **k: np.isnan(I.series(x)) if isinstance(x, np.ndarray) else (x is None or (isinstance(x, float) and np.isnan(x)))
BUILTINS["nz"] = lambda I, m, x, replacement=0, **k: (np.where(np.isnan(I.series(x)), replacement, I.series(x)) if isinstance(x, np.ndarray) else (replacement if x is None or (isinstance(x, float) and np.isnan(x)) else x))
BUILTINS["fixnan"] = lambda I, m, x, **k: pd.Series(I.series(x)).ffill().to_numpy()
for _nm, _f in {"abs": np.abs, "sqrt": np.sqrt, "log": np.log, "log10": np.log10, "exp": np.exp,
                "sign": np.sign, "floor": np.floor, "ceil": np.ceil, "sin": np.sin, "cos": np.cos,
                "tan": np.tan, "pow": np.power, "round": np.round}.items():
    BUILTINS[f"math.{_nm}"] = BUILTINS[_nm] = _vec(_f)
BUILTINS["math.max"] = BUILTINS["max"] = _vec(lambda *a: np.maximum.reduce([np.asarray(x, float) for x in a]))
BUILTINS["math.min"] = BUILTINS["min"] = _vec(lambda *a: np.minimum.reduce([np.asarray(x, float) for x in a]))
BUILTINS["math.avg"] = BUILTINS["avg"] = _vec(lambda *a: np.mean([np.asarray(x, float) for x in a], axis=0))
BUILTINS["str.tostring"] = BUILTINS["tostring"] = lambda I, m, x, *a, **k: str(_scalar(x))
BUILTINS["int"] = _vec(lambda x: np.trunc(x))
BUILTINS["float"] = _vec(lambda x: x)
BUILTINS["bool"] = lambda I, m, x, **k: _bool(x, I.n)


def run_pine(source: str, df: pd.DataFrame, params: dict | None = None) -> dict:
    """Execute a Pine script against OHLCV data."""
    return PineInterpreter(source, df, params).run()
