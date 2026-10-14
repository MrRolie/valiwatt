"""Weekly drift statistic: Evidently's normed Wasserstein distance against the same calendar weeks of earlier years.

The statistic is computed here with the same formula Evidently uses (Wasserstein distance divided by the reference's
standard deviation); `tests/test_drift.py` checks the two agree. The reference is seasonal on purpose: comparing a
January week with a whole year would flag every winter as drift.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import wasserstein_distance

REFERENCE_HALF_WINDOW_WEEKS = 2
MIN_REFERENCE = 200


def normed_wasserstein(current: np.ndarray, reference: np.ndarray) -> float:
    cur, ref = current[np.isfinite(current)], reference[np.isfinite(reference)]
    if len(cur) == 0 or len(ref) < MIN_REFERENCE:
        return float("nan")
    sd = np.std(ref)
    return float(wasserstein_distance(ref, cur) / sd) if sd > 0 else float("nan")


def seasonal_reference(values: pd.Series, days: pd.Series, week_start: pd.Timestamp,
                       half_window_weeks: int = REFERENCE_HALF_WINDOW_WEEKS) -> np.ndarray:
    """Values from the same calendar weeks (± half window) of years strictly before the week's year."""
    doy = pd.to_datetime(days).dt.dayofyear
    centre = (week_start + pd.Timedelta(days=3)).dayofyear
    dist = np.minimum((doy - centre).abs(), 366 - (doy - centre).abs())
    earlier = pd.to_datetime(days).dt.year < week_start.year
    return values[earlier & (dist <= 7 * half_window_weeks + 3)].to_numpy()


def weekly_drift(values: pd.Series, days: pd.Series, weeks: list[pd.Timestamp]) -> pd.Series:
    """The drift statistic for each week start in `weeks`, each against its own earlier-years reference."""
    days = pd.to_datetime(days)
    out = {}
    for w in weeks:
        cur = values[(days >= w) & (days < w + pd.Timedelta(days=7))].to_numpy()
        out[w] = normed_wasserstein(cur, seasonal_reference(values, days, w))
    return pd.Series(out, dtype=float)


def evidently_value_drift(current: np.ndarray, reference: np.ndarray, threshold: float) -> float:
    """The same statistic through Evidently itself (used by the tests and by the replay's saved reports)."""
    from evidently import DataDefinition, Dataset, Report
    from evidently.metrics import ValueDrift

    dd = DataDefinition(numerical_columns=["x"])
    rep = Report([ValueDrift(column="x", method="wasserstein", threshold=threshold)])
    snap = rep.run(Dataset.from_pandas(pd.DataFrame({"x": current}), data_definition=dd),
                   Dataset.from_pandas(pd.DataFrame({"x": reference}), data_definition=dd))
    return float(snap.dict()["metrics"][0]["value"])
