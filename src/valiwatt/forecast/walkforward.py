"""Walk-forward validation with an embargo: monthly test folds, each model refitted only on what came well before."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from valiwatt.forecast.guards import LookaheadError, require_embargo, require_known_at_origin
from valiwatt.forecast.issue import MIDNIGHT, IssueSpec, day_origin
from valiwatt.forecast.model import OLSForecaster, baseline, forecaster

EMBARGO_DAYS = 7


@dataclass(frozen=True)
class Fold:
    test_month: pd.Period
    train_last_day: pd.Timestamp  # last LST day whose hours may be used for fitting
    issue: IssueSpec = MIDNIGHT  # whose clock the fold is fitted at (docs/plan-v3.md, sections 2 and 6)

    @property
    def test_first_day(self) -> pd.Timestamp:
        return self.test_month.start_time

    @property
    def test_last_day(self) -> pd.Timestamp:
        return self.test_month.end_time.normalize()

    @property
    def fit_clock(self) -> pd.Timestamp:
        """When the fold's model is fitted: the origin of its first test day at the fold's issue time, the latest moment
        it must exist (the first test day's 05:00 UTC at MIDNIGHT, the eve's 17:00 UTC at NOON)."""
        return day_origin(self.test_first_day, issue=self.issue)


def folds(first_month: str, last_month: str, embargo_days: int = EMBARGO_DAYS, *,
          issue: IssueSpec = MIDNIGHT) -> list[Fold]:
    """One fold per month from `first_month` to `last_month`, each training on the days up to the embargo before it
    and fitted at `issue`'s clock. The embargo does not move with the clock (docs/plan-v3.md, section 6)."""
    out = []
    for m in pd.period_range(first_month, last_month, freq="M"):
        out.append(Fold(m, m.start_time - pd.Timedelta(days=embargo_days + 1), issue))
    return out


def month_folds(first_month: str, last_month: str, *, issue: IssueSpec = MIDNIGHT) -> list[Fold]:
    """`folds` with v2's embargo, the clock named: the monthly folds of validation, a freeze and its calibration."""
    return folds(first_month, last_month, issue=issue)


def split(f: pd.DataFrame, fold: Fold) -> tuple[pd.DataFrame, pd.DataFrame]:
    train = f[f["day"] <= fold.train_last_day]
    test = f[(f["day"] >= fold.test_first_day) & (f["day"] <= fold.test_last_day)]
    return train, test


def require_fold_clock(test: pd.DataFrame, fold: Fold) -> None:
    """The fold is fitted at the origin its table gives its first test day: every row of that day carries origin
    fold.fit_clock (docs/plan-v3.md, sections 2 and 6). Refused (LookaheadError) otherwise, or when the day is
    absent."""
    first = test.loc[test["day"] == fold.test_first_day, "origin"] if "origin" in test.columns else pd.Series()
    if first.empty or not first.eq(fold.fit_clock).all():
        seen = sorted({str(o) for o in first}) or ["none"]
        raise LookaheadError(f"fold {fold.test_month} is fitted at {fold.fit_clock} ({fold.issue.name}), but the "
                             f"table's origin of its first test day {fold.test_first_day.date()} is "
                             f"{', '.join(seen)}")


def run(f: pd.DataFrame, fold_list: list[Fold], scenarios=("observed", "persistence"),
        train_at: Callable[[pd.Timestamp], pd.DataFrame] | None = None, models=None) -> pd.DataFrame:
    """Out-of-sample predictions for every fold: one row per test hour with the target, when it was scored, each
    forecast and the baseline.

    f: the test rows (in design code, each as the replay issues and scores it: `features.AsOf.issued`).
    train_at: the feature table as seen at a clock (`features.AsOf.table`); each fold trains on it at its
    `fit_clock`. Without it, training rows come from f.
    scenarios: v1's least squares on each temperature input (columns `pred_<scenario>`, `n_train_<scenario>`).
    models: keys of `model.MODELS` (v2): one forecaster per key per fold, each on its own usable training rows
    (columns `pred_<key>`, `n_train_<key>`); given, it alone sets the forecasts and `scenarios` is unused. The least
    squares keys are v1's scenario names, so `pred_persistence` keeps its meaning.
    Each fold's training rows (from train_at when given) and test rows pass the origin guard
    (`guards.require_known_at_origin`) before anything is fitted or forecast; a refusal names the fold and the rows.
    Before that, a fold whose clock is not the table's origin of its first test day is refused (LookaheadError): a
    MIDNIGHT fold on a NOON table would train 12 hours after the noon clock, and a fold whose first test day the table
    lacks has no origin to be checked against."""
    keys = list(scenarios) if models is None else list(models)
    make = OLSForecaster if models is None else forecaster
    parts = []
    for fold in fold_list:
        train, test = split(f, fold)
        require_fold_clock(test, fold)
        if train_at is not None:
            train, _ = split(train_at(fold.fit_clock), fold)
        if train.empty or test.empty:
            raise ValueError(f"fold {fold.test_month} has no training or test rows")
        require_embargo(train, fold.test_first_day, EMBARGO_DAYS)
        # The rows this fold actually fits on and forecasts, whoever supplied them, before any fit or knot placement.
        require_known_at_origin(train, f"fold {fold.test_month}, training rows")
        require_known_at_origin(test, f"fold {fold.test_month}, test rows")
        out = pd.DataFrame({"target": test["target"], "fold": str(fold.test_month), "day": test["day"],
                            "scored_at": test["scored_at"], "hour": test["hour"], "day_type": test["day_type"],
                            "baseline": baseline(test)}, index=test.index)
        for k in keys:
            m = make(k).fit(train)
            out[f"pred_{k}"] = m.predict(test)
            out[f"n_train_{k}"] = m.n_train
        parts.append(out)
    return pd.concat(parts)
