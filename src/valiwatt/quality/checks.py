"""The result every check returns: structured numbers and evidence, no prose (the report templates add the words)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

import numpy as np
import pandas as pd


@dataclass
class CheckResult:
    name: str
    kind: Literal["gate", "finding"]
    passed: bool
    metrics: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return jsonable(asdict(self))


class GateFailed(RuntimeError):
    def __init__(self, failed: list[CheckResult]):
        self.failed = failed
        super().__init__("data-quality gates failed: " + ", ".join(c.name for c in failed))


def jsonable(obj):
    """Convert numpy/pandas scalars and timestamps to plain JSON types."""
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def runs_of_true(mask: pd.Series) -> list[dict]:
    """Contiguous runs where mask is True: start, end (inclusive) and length in hours."""
    out = []
    if not mask.any():
        return out
    m = mask.to_numpy()
    idx = mask.index
    starts = np.flatnonzero(m & ~np.r_[False, m[:-1]])
    ends = np.flatnonzero(m & ~np.r_[m[1:], False])
    for s, e in zip(starts, ends):
        out.append({"start": idx[s], "end": idx[e], "hours": int(e - s + 1)})
    return out


# Makes the median absolute deviation estimate the standard deviation when the data are normal.
MAD_SCALE = 1.4826


def robust_z(x: pd.Series) -> tuple[pd.Series, float, float]:
    """(x - median) / (MAD_SCALE * MAD): a z-score that a few extreme values cannot inflate."""
    med = float(x.median())
    mad = float((x - med).abs().median()) * MAD_SCALE
    if not mad > 0:
        raise ValueError("MAD is zero; robust z undefined")
    return (x - med) / mad, med, mad


def spike_statistic(v: pd.Series) -> pd.Series:
    """Distance of each hour from the midpoint of its two neighbours."""
    return v - (v.shift(1) + v.shift(-1)) / 2
