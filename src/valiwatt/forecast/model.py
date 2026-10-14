"""A seasonal baseline and one explainable model (ordinary least squares on named features)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DAY_TYPES = ("weekday", "saturday", "sunday_holiday")
# Hours of the local day grouped where heating and cooling act differently: night, morning ramp, day, evening peak,
# late evening.
HOUR_BLOCKS = {"night": range(0, 6), "morning": range(6, 10), "day": range(10, 16), "evening": range(16, 21),
               "late": range(21, 24)}


def block_of(hour: pd.Series) -> pd.Series:
    out = pd.Series(index=hour.index, dtype=object)
    for name, hours in HOUR_BLOCKS.items():
        out[hour.isin(list(hours))] = name
    return out


def design(f: pd.DataFrame, scenario: str) -> pd.DataFrame:
    """Named columns, one per coefficient. Rows with any missing input come out with NaN and are never imputed."""
    cols = {}
    for dt in DAY_TYPES:
        for h in range(24):
            cols[f"cell:{dt}:{h:02d}"] = ((f["day_type"] == dt) & (f["hour"] == h)).astype(float)
    blk = block_of(f["hour"])
    for name in HOUR_BLOCKS:
        in_block = (blk == name).astype(float)
        cols[f"heating:{name}"] = f[f"hdh_{scenario}"] * in_block
        cols[f"cooling:{name}"] = f[f"cdh_{scenario}"] * in_block
    cols["lag24"] = f["lag24"]
    cols["lag168"] = f["lag168"]
    return pd.DataFrame(cols, index=f.index)


def usable_rows(X: pd.DataFrame, y: pd.Series | None = None) -> pd.Series:
    ok = np.isfinite(X.to_numpy()).all(axis=1)
    if y is not None:
        ok &= np.isfinite(y.to_numpy())
    return pd.Series(ok, index=X.index)


@dataclass
class OLSForecaster:
    scenario: str
    columns: list = field(default_factory=list)
    beta: np.ndarray | None = None
    n_train: int = 0

    def fit(self, f: pd.DataFrame) -> "OLSForecaster":
        X = design(f, self.scenario)
        ok = usable_rows(X, f["target"])
        Xa, y = X[ok].to_numpy(), f.loc[ok, "target"].to_numpy()
        self.beta, *_ = np.linalg.lstsq(Xa, y, rcond=None)
        self.columns = list(X.columns)
        self.n_train = int(ok.sum())
        return self

    def predict(self, f: pd.DataFrame) -> pd.Series:
        if self.beta is None:
            raise RuntimeError("model not fitted")
        X = design(f, self.scenario)[self.columns]
        ok = usable_rows(X)
        out = pd.Series(np.nan, index=f.index)
        out[ok] = X[ok].to_numpy() @ self.beta
        return out

    def coefficients(self) -> pd.Series:
        return pd.Series(self.beta, index=self.columns)


def baseline(f: pd.DataFrame) -> pd.Series:
    """Seasonal naive: the same hour one week earlier."""
    return f["lag168"].astype(float)
