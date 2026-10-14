"""The no-lookahead rail as code: each guard refuses, and each has a test that breaks it."""

from __future__ import annotations

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


def require_known_at_origin(f: pd.DataFrame) -> None:
    """Every lag and persistence input must come from at or before the row's origin."""
    h = pd.Timedelta(hours=1)
    bad = ((f.index - 24 * h) > f["origin"]) | ((f.index - 168 * h) > f["origin"]) | (f.index <= f["origin"])
    if bad.any():
        raise LookaheadError(f"{int(bad.sum())} rows read an input after their origin (first {f.index[bad][0]})")


def require_embargo(train: pd.DataFrame, test_first_day: pd.Timestamp, embargo_days: int) -> None:
    limit = test_first_day - pd.Timedelta(days=embargo_days + 1)
    if not train.empty and train["day"].max() > limit:
        raise LookaheadError(f"training reaches {train['day'].max().date()}, past the embargo limit {limit.date()}")
