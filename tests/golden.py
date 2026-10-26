"""Canonical digests and samples for the v2 golden tests (tests/test_v2_goldens.py).

A frame's digest is the sha256 of a canonical text of it: its index and columns by position, each column's dtype, and
every cell as repr (a float as repr(float(x)), the shortest text that round-trips it, NaN as 'nan'). A list or dict of
records (the replay's days, weeks and decisions, validation's results) is digested as JSON with sorted keys, floats
by repr, NaN and NaT spelled out. Two digests match only when every cell is the same to the last bit.
"""

from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd


def _cell(x) -> str:
    if isinstance(x, (float, np.floating)):
        return repr(float(x))
    if isinstance(x, (np.integer,)):
        return repr(int(x))
    if x is pd.NaT:
        return "NaT"
    if isinstance(x, pd.Timestamp):
        return x.isoformat() + ("" if x.tzinfo is None else f"[{x.tzinfo}]")
    return repr(x)


def frame_text(df: pd.DataFrame) -> str:
    d = df.reset_index()
    cols = [d.iloc[:, i] for i in range(d.shape[1])]
    lines = ["|".join(str(c) for c in d.columns), "|".join(str(c.dtype) for c in cols)]
    cells = [[_cell(x) for x in c.tolist()] if not pd.api.types.is_float_dtype(c)
             else [repr(float(x)) for x in c.to_numpy(dtype=float)] for c in cols]
    lines += ["|".join(row) for row in zip(*cells)]
    return "\n".join(lines) + "\n"


def frame_digest(df: pd.DataFrame) -> str:
    return hashlib.sha256(frame_text(df).encode()).hexdigest()


def _plain(obj):
    if isinstance(obj, dict):
        return {_key(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, pd.DataFrame):
        return {"frame": frame_text(obj)}
    if isinstance(obj, pd.Series):
        return {"series": frame_text(obj.to_frame())}
    if isinstance(obj, np.ndarray):
        return [_plain(v) for v in obj.tolist()]
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        return {"float": repr(float(obj))}  # repr, NaN included, never JSON's own float text
    if obj is None or isinstance(obj, str):
        return obj
    return {"repr": _cell(obj)}


def _key(k) -> str:
    return _cell(k) if not isinstance(k, str) else k


def records_digest(obj) -> str:
    return hashlib.sha256(json.dumps(_plain(obj), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def column_stats(s: pd.Series) -> dict:
    v = s.to_numpy(dtype=float)
    ok = v[np.isfinite(v)]
    return {"hours": int(len(v)), "nan": int(np.isnan(v).sum()), "sum": float(ok.sum()),
            "sumsq": float((ok ** 2).sum())}


def sample(s: pd.Series, n: int = 200) -> list:
    """n (label, value) pairs at evenly spaced positions, labels as text, NaN as None."""
    pos = np.unique(np.linspace(0, len(s) - 1, n).round().astype(int))
    return [(_cell(s.index[i]), None if not math.isfinite(float(s.iloc[i])) else float(s.iloc[i])) for i in pos]
