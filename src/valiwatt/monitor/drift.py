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


def normed_wasserstein(current: np.ndarray, reference: np.ndarray, min_current: int = 1) -> float:
    """NaN when the current sample has fewer than `min_current` values or the reference fewer than MIN_REFERENCE."""
    cur, ref = current[np.isfinite(current)], reference[np.isfinite(reference)]
    if len(cur) < max(min_current, 1) or len(ref) < MIN_REFERENCE:
        return float("nan")
    sd = np.std(ref)
    return float(wasserstein_distance(ref, cur) / sd) if sd > 0 else float("nan")


def calendar_distance(days: pd.Series, centre: pd.Timestamp) -> pd.Series:
    """Days between each date and the nearest same-calendar-date as `centre` (in its own year or a neighbouring one).
    Feb 29 falls back to Feb 28 in other years, so every year contributes the same window."""
    days = pd.to_datetime(days)
    out = pd.Series(np.inf, index=days.index)
    for year in days.dt.year.unique():
        rows = days.dt.year == year
        for y in (year - 1, year, year + 1):
            last = pd.Timestamp(year=y, month=centre.month, day=1).days_in_month
            anchor = pd.Timestamp(year=y, month=centre.month, day=min(centre.day, last))
            out[rows] = np.minimum(out[rows], (days[rows] - anchor).abs().dt.days)
    return out


def seasonal_reference(values: pd.Series, days: pd.Series, week_start: pd.Timestamp,
                       half_window_weeks: int = REFERENCE_HALF_WINDOW_WEEKS) -> np.ndarray:
    """Values from the same calendar weeks (± half window) of years strictly before the week's year: dates within
    7 * half_window_weeks + 3 days of the week's Thursday, taken on the calendar."""
    centre = week_start + pd.Timedelta(days=3)
    earlier = pd.to_datetime(days).dt.year < week_start.year
    near = calendar_distance(days, centre) <= 7 * half_window_weeks + 3
    return values[(earlier & near).to_numpy()].to_numpy()


def weekly_drift(values: pd.Series, days: pd.Series, weeks: list[pd.Timestamp], min_current: int = 1) -> pd.Series:
    """The drift statistic for each week start in `weeks`, each against its own earlier-years reference."""
    days = pd.to_datetime(days)
    out = {}
    for w in weeks:
        cur = values[((days >= w) & (days < w + pd.Timedelta(days=7))).to_numpy()].to_numpy()
        out[w] = normed_wasserstein(cur, seasonal_reference(values, days, w), min_current)
    return pd.Series(out, dtype=float)


def evidently_snapshot(current: np.ndarray, reference: np.ndarray, threshold: float, column: str = "x"):
    """The same statistic through Evidently itself: its value, and the snapshot the replay saves as an HTML report."""
    from evidently import DataDefinition, Dataset, Report
    from evidently.metrics import ValueDrift

    cur, ref = current[np.isfinite(current)], reference[np.isfinite(reference)]
    dd = DataDefinition(numerical_columns=[column])
    rep = Report([ValueDrift(column=column, method="wasserstein", threshold=threshold)])
    snap = rep.run(Dataset.from_pandas(pd.DataFrame({column: cur}), data_definition=dd),
                   Dataset.from_pandas(pd.DataFrame({column: ref}), data_definition=dd))
    return float(snap.dict()["metrics"][0]["value"]), snap


def evidently_value_drift(current: np.ndarray, reference: np.ndarray, threshold: float) -> float:
    return evidently_snapshot(current, reference, threshold)[0]
