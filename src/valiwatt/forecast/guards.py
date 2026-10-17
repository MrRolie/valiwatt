"""The no-lookahead rail as code: each guard refuses, and each has a test that breaks it."""

from __future__ import annotations

import numpy as np
import pandas as pd

from valiwatt.config import DESIGN_LAST, LST_OFFSET

# Last local-standard-time day of the design window (2019-2023).
DESIGN_LAST_DAY = (DESIGN_LAST - pd.Timedelta(hours=1) + LST_OFFSET).tz_convert(None).normalize()


class LookaheadError(RuntimeError):
    """Information unavailable at the decision time reached a fit, a choice or a score."""


def require_design_window(f: pd.DataFrame, what: str) -> None:
    """Design work (fits, choices, thresholds) may only see 2019-2023."""
    late = f["day"] > DESIGN_LAST_DAY
    if late.any():
        raise LookaheadError(f"{what}: {int(late.sum())} rows after the design window (first {f.index[late][0]})")


def origin_violations(f: pd.DataFrame) -> dict[str, pd.Series]:
    """Each way a row can read past its origin, as a mask of the rows that do.

    There is no term for the 168-hour lag: an hour 168 hours back is earlier than the hour 24 hours back, so the lag
    can read past the origin only when the 24-hour inputs (the 24-hour lag and the persistence temperature) already
    do, and a term of its own could never fire alone.

    A weather forecast is read from its availability only (docs/plan-v2.md, sections 2 and 3.2): every
    `available_<s>` column present (the feeding run's availability, features.build) must be at or before the row's
    origin (`forecast_available_after_origin`; NaT compares False there); and a row holding a finite input of a
    forecast scenario (temp_<s>, hdh_<s> or cdh_<s>, s in features.FORECAST_LAGS) must say when its run became
    available: its `available_<s>` missing, NaT or the column absent, it violates (`forecast_availability_missing`).
    A row no run fed has no finite forecast input, so its NaT never violates."""
    from valiwatt.forecast.features import FORECAST_LAGS  # features imports this module

    h, origin = pd.Timedelta(hours=1), pd.DatetimeIndex(f["origin"])
    late = np.zeros(len(f), dtype=bool)
    for c in (c for c in f.columns if str(c).startswith("available_")):
        late |= np.asarray(pd.DatetimeIndex(f[c]) > origin, dtype=bool)  # NaT compares False
    undated = np.zeros(len(f), dtype=bool)
    for s in FORECAST_LAGS:
        inputs = [c for c in (f"temp_{s}", f"hdh_{s}", f"cdh_{s}") if c in f.columns]
        if not inputs:
            continue
        finite = np.isfinite(f[inputs].to_numpy(dtype=float)).any(axis=1)
        known = (pd.DatetimeIndex(f[f"available_{s}"]).notna() if f"available_{s}" in f.columns
                 else np.zeros(len(f), dtype=bool))
        undated |= finite & ~np.asarray(known, dtype=bool)
    return {"input_24h_after_origin": pd.Series((f.index - 24 * h) > origin, index=f.index),
            "target_not_after_origin": pd.Series(f.index <= origin, index=f.index),
            "forecast_available_after_origin": pd.Series(late, index=f.index),
            "forecast_availability_missing": pd.Series(undated, index=f.index)}


def require_known_at_origin(f: pd.DataFrame) -> None:
    """Every lag and persistence input must come from at or before the row's origin, the target hour after it, and
    every weather forecast a row reads from a run whose availability is recorded and at or before it."""
    terms = origin_violations(f)
    bad = pd.concat(terms.values(), axis=1).any(axis=1)
    if bad.any():
        fired = [k for k, m in terms.items() if m.any()]
        raise LookaheadError(f"{int(bad.sum())} rows read an input after their origin ({', '.join(fired)}; "
                             f"first {f.index[bad.to_numpy()][0]})")


def require_embargo(train: pd.DataFrame, test_first_day: pd.Timestamp, embargo_days: int) -> None:
    limit = test_first_day - pd.Timedelta(days=embargo_days + 1)
    if not train.empty and train["day"].max() > limit:
        raise LookaheadError(f"training reaches {train['day'].max().date()}, past the embargo limit {limit.date()}")


def require_before_clock(index: pd.DatetimeIndex, clock: pd.Timestamp, what: str) -> None:
    """Production reads (the replay) may only see records stamped at or before the clock."""
    late = index > clock
    if late.any():
        raise LookaheadError(f"{what}: {int(late.sum())} records after the clock {clock} (first {index[late][0]})")
