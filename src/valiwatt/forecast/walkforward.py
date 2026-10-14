"""Walk-forward validation with an embargo: monthly test folds, each model refitted only on what came well before."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from valiwatt.forecast.features import day_origin
from valiwatt.forecast.guards import require_embargo
from valiwatt.forecast.model import OLSForecaster, baseline

EMBARGO_DAYS = 7


@dataclass(frozen=True)
class Fold:
    test_month: pd.Period
    train_last_day: pd.Timestamp  # last LST day whose hours may be used for fitting

    @property
    def test_first_day(self) -> pd.Timestamp:
        return self.test_month.start_time

    @property
    def test_last_day(self) -> pd.Timestamp:
        return self.test_month.end_time.normalize()

    @property
    def fit_clock(self) -> pd.Timestamp:
        """When the fold's model is fitted: the origin of its first test day, the latest moment it must exist."""
        return day_origin(self.test_first_day)


def folds(first_month: str, last_month: str, embargo_days: int = EMBARGO_DAYS) -> list[Fold]:
    out = []
    for m in pd.period_range(first_month, last_month, freq="M"):
        out.append(Fold(m, m.start_time - pd.Timedelta(days=embargo_days + 1)))
    return out


def split(f: pd.DataFrame, fold: Fold) -> tuple[pd.DataFrame, pd.DataFrame]:
    train = f[f["day"] <= fold.train_last_day]
    test = f[(f["day"] >= fold.test_first_day) & (f["day"] <= fold.test_last_day)]
    return train, test


def run(f: pd.DataFrame, fold_list: list[Fold], scenarios=("observed", "persistence"),
        train_at: Callable[[pd.Timestamp], pd.DataFrame] | None = None) -> pd.DataFrame:
    """Out-of-sample predictions for every fold: one row per test hour with the target, when it was scored, each
    scenario's forecast and the baseline.

    f: the test rows (in design code, each as the replay issues and scores it: `features.AsOf.issued`).
    train_at: the feature table as seen at a clock (`features.AsOf.table`); each fold trains on it at its
    `fit_clock`. Without it, training rows come from f."""
    parts = []
    for fold in fold_list:
        train, test = split(f, fold)
        if train_at is not None:
            train, _ = split(train_at(fold.fit_clock), fold)
        if train.empty or test.empty:
            raise ValueError(f"fold {fold.test_month} has no training or test rows")
        require_embargo(train, fold.test_first_day, EMBARGO_DAYS)
        out = pd.DataFrame({"target": test["target"], "fold": str(fold.test_month), "day": test["day"],
                            "scored_at": test["scored_at"], "hour": test["hour"], "day_type": test["day_type"],
                            "baseline": baseline(test)}, index=test.index)
        for s in scenarios:
            m = OLSForecaster(s).fit(train)
            out[f"pred_{s}"] = m.predict(test)
            out[f"n_train_{s}"] = m.n_train
        parts.append(out)
    return pd.concat(parts)
