"""The no-lookahead rail as code: each guard refuses, and each has a test that breaks it."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from valiwatt.config import DESIGN_LAST, LST_OFFSET

# Last local-standard-time day of v2's design window (2019-2023). v3's ends 2024-12-31 (docs/plan-v3.md, section 3.1):
# its design code passes that day to require_design_window.
DESIGN_LAST_DAY = (DESIGN_LAST - pd.Timedelta(hours=1) + LST_OFFSET).tz_convert(None).normalize()
LAG_COLUMN = re.compile(r"lag(\d+)")


class LookaheadError(RuntimeError):
    """Information unavailable at the decision time reached a fit, a choice or a score."""


def require_design_window(f: pd.DataFrame, what: str, last_day: pd.Timestamp = DESIGN_LAST_DAY) -> None:
    """Design work (fits, choices, thresholds) may only see the design window: up to `last_day`, an LST day (v2's
    2023-12-31 by default; v3's design code gives 2024-12-31, docs/plan-v3.md section 3.1)."""
    late = f["day"] > pd.Timestamp(last_day)
    if late.any():
        raise LookaheadError(f"{what}: {int(late.sum())} rows after the design window (first {f.index[late][0]})")


def _origins(f: pd.DataFrame) -> pd.DatetimeIndex:
    """Each row's origin; NaT on every row of a table without an origin column."""
    if "origin" in f.columns:
        return pd.DatetimeIndex(f["origin"])
    return pd.DatetimeIndex([pd.NaT] * len(f), tz="UTC")


def min_lag_hours(f: pd.DataFrame) -> int:
    """The shortest look back, in hours, of the inputs a table reads from before its origin: its lag<N> columns, and
    24 for v1's persistence temperature (temp_persistence). 24 for a MIDNIGHT table (lag24), 48 for a NOON one (lag48,
    no temp_persistence: docs/plan-v3.md, section 2); 24 for a table holding neither. A table whose shortest look back
    would be the weekly lag (lag168 with no shorter lag and no persistence temperature) is refused (LookaheadError):
    every issue time reads a shorter lag (issue.py), so such a table lost its own, and judging it at 168 hours would
    loosen the term its issue time sets."""
    hours = [int(m.group(1)) for c in f.columns if (m := LAG_COLUMN.fullmatch(str(c)))]
    if "temp_persistence" in f.columns:
        hours.append(24)
    if hours and min(hours) >= 168:
        raise LookaheadError(f"the table holds {sorted(c for c in f.columns if LAG_COLUMN.fullmatch(str(c)))} but no "
                             f"shorter lag and no persistence temperature: its look back cannot be judged at "
                             f"{min(hours)} hours")
    return min(hours, default=24)


def forecast_violations(f: pd.DataFrame, scenarios=None) -> dict[str, pd.Series]:
    """The ways a row can read a weather forecast not known to be available at its origin, on the forecast scenarios
    given (default every one, features.FORECAST_LAGS; a scenario without availability, observed or persistence, is
    skipped), each as a mask of the rows that do. A row holds an input of scenario s when its temp_<s>, hdh_<s> or
    cdh_<s> is finite, or, in a table issued with wind (docs/plan-v3.md, section 3.2), its wind_<s> or daymean_<s>.
    - forecast_available_after_origin: its available_<s> is after its origin (NaT compares False);
    - forecast_availability_missing: it holds an input of s, and its available_<s> is NaT or the column absent;
    - origin_missing: it holds an input of s, and its origin is NaT or the table has no origin: no availability, however
      early, is established before an unknown decision time.
    A row no run fed holds no input, so its NaT availability never violates. features.build, the fits on a forecast
    input (model.py) and, through origin_violations, every consumer of a feature table read this one predicate."""
    from valiwatt.forecast.features import FORECAST_LAGS  # features imports this module

    chosen = [s for s in (FORECAST_LAGS if scenarios is None else scenarios) if s in FORECAST_LAGS]
    late, undated, unplaced = (np.zeros(len(f), dtype=bool) for _ in range(3))
    if chosen:
        origin = _origins(f)
        no_origin = np.asarray(origin.isna(), dtype=bool)
        for s in chosen:
            column = f"available_{s}"
            available = pd.DatetimeIndex(f[column]) if column in f.columns else None
            if available is not None:
                late |= np.asarray(available > origin, dtype=bool)  # NaT compares False
            inputs = [c for c in (f"temp_{s}", f"hdh_{s}", f"cdh_{s}", f"wind_{s}", f"daymean_{s}") if c in f.columns]
            if not inputs:
                continue
            holds = np.isfinite(f[inputs].to_numpy(dtype=float)).any(axis=1)
            known = (np.asarray(available.notna(), dtype=bool) if available is not None
                     else np.zeros(len(f), dtype=bool))
            undated |= holds & ~known
            unplaced |= holds & no_origin
    return {"forecast_available_after_origin": pd.Series(late, index=f.index),
            "forecast_availability_missing": pd.Series(undated, index=f.index),
            "origin_missing": pd.Series(unplaced, index=f.index)}


def origin_violations(f: pd.DataFrame) -> dict[str, pd.Series]:
    """Each way a row can read past its origin, as a mask of the rows that do.

    `input_<n>h_after_origin`, n the table's shortest look back (`min_lag_hours`): the hour n hours before the target
    is after the origin. 24 at MIDNIGHT (the 24-hour lag and the persistence temperature), 48 at NOON; a 24-hour input
    in a NOON table makes it the 24-hour term, which fires on the hours ending after D 17:00 UTC. There is no term for
    the 168-hour lag: an hour 168 hours back is earlier than the shorter lag's hour, so the lag can read past the
    origin only when the shorter inputs already do, and a term of its own could never fire alone.

    `lag_day_after_origin`: the station's lag day's mean (docs/plan-v3.md, section 3.3) read a label after the origin
    (lagday_last_utc > origin; NaT compares False). `lag_day_last_missing`: a row holds a finite lag day's mean
    (lagday_observed) and does not say the last label it read (lagday_last_utc NaT, or the column absent), so its mean
    is unjudged, as a forecast input without its availability is. A missing mean (NaN, NaT) reads nothing. Both are
    always present, quiet on a table without lagday_observed (v2's).

    A weather forecast is read from its availability only (docs/plan-v2.md, sections 2 and 3.2): every
    `available_<s>` column present (the feeding run's availability, features.build) must be at or before the row's
    origin (`forecast_available_after_origin`; NaT compares False there); and a row holding a finite input of a
    forecast scenario must say when its run became available (`forecast_availability_missing`, the predicate of
    `forecast_violations`).

    `origin_missing`: every row carries its origin, whatever it reads. Every other term compares with the origin, and
    a comparison with NaT is False, so a row without one would pass them all, its lags and its forecast unjudged. No
    legitimate row lacks one: features.build computes each row's origin from its own label (origin_of)."""
    h, origin, n = pd.Timedelta(hours=1), _origins(f), min_lag_hours(f)
    late = np.zeros(len(f), dtype=bool)
    for c in (c for c in f.columns if str(c).startswith("available_")):
        late |= np.asarray(pd.DatetimeIndex(f[c]) > origin, dtype=bool)  # NaT compares False
    last = pd.DatetimeIndex(f["lagday_last_utc"]) if "lagday_last_utc" in f.columns else None
    lag_day = (np.asarray(last > origin, dtype=bool)  # NaT compares False
               if last is not None else np.zeros(len(f), dtype=bool))
    held = (np.isfinite(f["lagday_observed"].to_numpy(dtype=float)) if "lagday_observed" in f.columns
            else np.zeros(len(f), dtype=bool))
    unsaid = held & (np.asarray(last.isna(), dtype=bool) if last is not None else np.ones(len(f), dtype=bool))
    return {f"input_{n}h_after_origin": pd.Series((f.index - n * h) > origin, index=f.index),
            "target_not_after_origin": pd.Series(f.index <= origin, index=f.index),
            "forecast_available_after_origin": pd.Series(late, index=f.index),
            "forecast_availability_missing": forecast_violations(f)["forecast_availability_missing"],
            "origin_missing": pd.Series(np.asarray(origin.isna(), dtype=bool), index=f.index),
            "lag_day_after_origin": pd.Series(lag_day, index=f.index),
            "lag_day_last_missing": pd.Series(unsaid, index=f.index)}


def _refuse(f: pd.DataFrame, terms: dict[str, pd.Series], what: str) -> None:
    bad = pd.concat(terms.values(), axis=1).any(axis=1)
    if bad.any():
        fired = [k for k, m in terms.items() if m.any()]
        raise LookaheadError(f"{what + ': ' if what else ''}{int(bad.sum())} rows read an input not known at their "
                             f"origin ({', '.join(fired)}; first {f.index[bad.to_numpy()][0]})")


def require_known_at_origin(f: pd.DataFrame, what: str = "") -> None:
    """Every row carries its origin; every lag and persistence input comes from at or before it, the target hour
    after it, every weather forecast a row reads from a run whose availability is recorded and at or before it, and
    the lag day's mean from labels at or before it, the last of them recorded.
    what: the boundary that checks, named in the refusal."""
    _refuse(f, origin_violations(f), what)


def require_forecast_known(f: pd.DataFrame, scenarios, what: str) -> None:
    """The forecast terms alone (`forecast_violations`), on the scenarios a consumer reads: features.build on the
    forecasts it is given, a fit on its own input. A scenario without availability (observed, persistence) checks
    nothing. what: the boundary that checks, named in the refusal."""
    _refuse(f, forecast_violations(f, scenarios), what)


def require_embargo(train: pd.DataFrame, test_first_day: pd.Timestamp, embargo_days: int) -> None:
    limit = test_first_day - pd.Timedelta(days=embargo_days + 1)
    if not train.empty and train["day"].max() > limit:
        raise LookaheadError(f"training reaches {train['day'].max().date()}, past the embargo limit {limit.date()}")


def require_before_clock(index: pd.DatetimeIndex, clock: pd.Timestamp, what: str) -> None:
    """Production reads (the replay) may only see records stamped at or before the clock."""
    late = index > clock
    if late.any():
        raise LookaheadError(f"{what}: {int(late.sum())} records after the clock {clock} (first {index[late][0]})")
