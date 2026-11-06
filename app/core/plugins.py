"""Discovers and runs indicator / strategy plugins written in Python or Pine Script.

Python indicator contract (indicators/*.py):
    NAME = "My Indicator"          # optional
    OVERLAY = True                 # draw on the price chart (False = separate pane)
    PARAMS = {"length": 20}        # defaults, editable in the UI
    def compute(df, **params) -> dict[str, array-like]   # one entry per line plotted

Python strategy contract (strategies/*.py):
    NAME, PARAMS as above
    def generate_signals(df, **params) -> dict with keys
        long_entry, long_exit, short_entry, short_exit   (bool arrays; any may be omitted)
        plots (optional dict[str, array] drawn on the chart)
        stop_loss_pct / take_profit_pct (optional floats)
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

from ..pine.interpreter import PALETTE, run_pine
from .backtest import Signals

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # lets plugins `from app.core import ta`
    sys.path.insert(0, str(ROOT))
FOLDERS = {"indicator": ROOT / "indicators", "strategy": ROOT / "strategies"}
EXTS = (".py", ".pine")


class PluginError(Exception):
    pass


def _safe_name(name: str) -> str:
    stem = Path(name).name
    if not stem.endswith(EXTS):
        raise PluginError("File must end with .py or .pine")
    if not all(ch.isalnum() or ch in "-_." for ch in stem) or stem.startswith("."):
        raise PluginError("Use only letters, numbers, '-', '_' in file names")
    return stem


def path_for(kind: str, filename: str) -> Path:
    if kind not in FOLDERS:
        raise PluginError(f"Unknown plugin kind {kind!r}")
    return FOLDERS[kind] / _safe_name(filename)


def list_plugins() -> list[dict]:
    out = []
    for kind, folder in FOLDERS.items():
        folder.mkdir(exist_ok=True)
        for p in sorted(folder.iterdir()):
            if p.suffix in EXTS and not p.name.startswith("_"):
                out.append({"kind": kind, "file": p.name, "lang": "pine" if p.suffix == ".pine" else "python",
                            "name": _display_name(p)})
    return out


def _display_name(p: Path) -> str:
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return p.stem
    import re
    if p.suffix == ".pine":
        m = re.search(r'(?:indicator|strategy|study)\(\s*(?:title\s*=\s*)?["\']([^"\']+)', text)
    else:
        m = re.search(r'^NAME\s*=\s*["\']([^"\']+)', text, re.M)
    return m.group(1) if m else p.stem.replace("_", " ").title()


def read_source(kind: str, filename: str) -> str:
    p = path_for(kind, filename)
    if not p.exists():
        raise PluginError(f"{filename} not found")
    return p.read_text(encoding="utf-8")


def save_source(kind: str, filename: str, code: str) -> str:
    p = path_for(kind, filename)
    p.parent.mkdir(exist_ok=True)
    p.write_text(code, encoding="utf-8")
    return p.name


def delete_source(kind: str, filename: str):
    p = path_for(kind, filename)
    if p.exists():
        p.unlink()


def _load_module(path: Path):
    mod_name = f"_plugin_{path.parent.name}_{path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)  # fresh import every time -> edits apply instantly
    except Exception as e:
        raise PluginError(_format_exc(e, path)) from e
    return mod


def _format_exc(e: Exception, path: Path) -> str:
    tb = traceback.extract_tb(e.__traceback__)
    frames = [f for f in tb if Path(f.filename).name == path.name]
    where = f" (line {frames[-1].lineno})" if frames else ""
    return f"{type(e).__name__}{where}: {e}"


def _coerce_params(defaults: dict, given: dict | None) -> dict:
    out = dict(defaults)
    for k, v in (given or {}).items():
        if k not in defaults:
            continue
        d = defaults[k]
        try:
            if isinstance(d, bool):
                out[k] = v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes")
            elif isinstance(d, int):
                out[k] = int(float(v))
            elif isinstance(d, float):
                out[k] = float(v)
            else:
                out[k] = v
        except (TypeError, ValueError):
            pass
    return out


def _param_inputs(defaults: dict, values: dict) -> list[dict]:
    kinds = {bool: "bool", int: "int", float: "float"}
    return [{"name": k, "type": kinds.get(type(v), "string"), "default": v, "value": values.get(k, v)}
            for k, v in defaults.items()]


def _arr(x, n):
    a = np.asarray(x.to_numpy() if isinstance(x, pd.Series) else x, dtype=float)
    if a.shape != (n,):
        raise PluginError(f"Output has length {a.shape} but data has {n} bars")
    return a


def run_plugin(kind: str, filename: str, df: pd.DataFrame, params: dict | None = None, code: str | None = None) -> dict:
    """Run a plugin (from disk, or from unsaved `code`). Returns a uniform result dict:
    {kind, title, overlay, inputs, plots, shapes, hlines, warnings, [signals]}"""
    path = path_for(kind, filename)
    is_pine = path.suffix == ".pine"
    if code is None:
        code = read_source(kind, filename)

    if is_pine:
        res = run_pine(code, df, params)
        if kind == "strategy" and res["kind"] != "strategy":
            raise PluginError("This Pine script is an indicator(); use strategy() to backtest it")
        return res

    # Python: write unsaved code to a temp sibling so imports/tracebacks work
    tmp = path if code == (path.read_text(encoding="utf-8") if path.exists() else None) else path.with_name(f"_tmp_{path.name}")
    if tmp != path:
        tmp.write_text(code, encoding="utf-8")
    try:
        mod = _load_module(tmp)
    finally:
        if tmp != path and tmp.exists():
            tmp.unlink()

    defaults = dict(getattr(mod, "PARAMS", {}))
    values = _coerce_params(defaults, params)
    title = getattr(mod, "NAME", path.stem)
    n = len(df)
    data = df.copy()

    def to_plots(d: dict) -> list[dict]:
        plots = []
        for i, (name, series) in enumerate((d or {}).items()):
            plots.append({"title": str(name), "color": PALETTE[i % len(PALETTE)], "linewidth": 2,
                          "style": "line", "data": _arr(series, n)})
        return plots

    try:
        if kind == "indicator":
            fn = getattr(mod, "compute", None)
            if fn is None:
                raise PluginError("Indicator must define compute(df, **params)")
            out = fn(data, **values)
            if isinstance(out, (pd.Series, np.ndarray)):
                out = {title: out}
            if isinstance(out, pd.DataFrame):
                out = {c: out[c] for c in out.columns}
            return {"kind": "indicator", "title": title, "overlay": bool(getattr(mod, "OVERLAY", False)),
                    "inputs": _param_inputs(defaults, values), "plots": to_plots(out), "shapes": [],
                    "hlines": [{"title": str(v), "price": float(v), "color": "#787B86"} for v in getattr(mod, "HLINES", [])],
                    "warnings": []}

        fn = getattr(mod, "generate_signals", None)
        if fn is None:
            raise PluginError("Strategy must define generate_signals(df, **params)")
        out = fn(data, **values)
        if isinstance(out, Signals):
            sig, plots = out, {}
        else:
            if isinstance(out, pd.DataFrame):
                out = {c: out[c] for c in out.columns}
            plots = out.get("plots", {})
            sig = Signals(*(np.asarray(out.get(k), dtype=float) if out.get(k) is not None else None
                            for k in ("long_entry", "long_exit", "short_entry", "short_exit")),
                          out.get("stop_loss_pct"), out.get("take_profit_pct"))
        return {"kind": "strategy", "title": title, "overlay": True, "inputs": _param_inputs(defaults, values),
                "plots": to_plots(plots), "shapes": [], "hlines": [], "warnings": [], "signals": sig}
    except PluginError:
        raise
    except Exception as e:
        raise PluginError(_format_exc(e, tmp)) from e
