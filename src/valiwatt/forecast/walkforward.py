"""Walk-forward validation with an embargo: monthly test folds, each model refitted only on what came well before."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

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


def folds(first_month: str, last_month: str, embargo_days: int = EMBARGO_DAYS) -> list[Fold]:
    out = []
    for m in pd.period_range(first_month, last_month, freq="M"):
        out.append(Fold(m, m.start_time - pd.Timedelta(days=embargo_days + 1)))
    return out


def split(f: pd.DataFrame, fold: Fold) -> tuple[pd.DataFrame, pd.DataFrame]:
    train = f[f["day"] <= fold.train_last_day]
    test = f[(f["day"] >= fold.test_first_day) & (f["day"] <= fold.test_last_day)]
    return train, test


def run(f: pd.DataFrame, fold_list: list[Fold], scenarios=("observed", "persistence")) -> pd.DataFrame:
    """Out-of-sample predictions for every fold: one row per test hour with the target, each scenario's forecast
    and the baseline."""
    parts = []
    for fold in fold_list:
        train, test = split(f, fold)
        if train.empty or test.empty:
            raise ValueError(f"fold {fold.test_month} has no training or test rows")
        if train["day"].max() > fold.train_last_day:
            raise AssertionError(f"fold {fold.test_month}: training reaches into the embargo")
        out = pd.DataFrame({"target": test["target"], "fold": str(fold.test_month), "day": test["day"],
                            "hour": test["hour"], "day_type": test["day_type"], "baseline": baseline(test)},
                           index=test.index)
        for s in scenarios:
            m = OLSForecaster(s).fit(train)
            out[f"pred_{s}"] = m.predict(test)
            out[f"n_train_{s}"] = m.n_train
        parts.append(out)
    return pd.concat(parts)
