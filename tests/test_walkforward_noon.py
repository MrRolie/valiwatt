"""Walk-forward folds at noon (docs/plan-v3.md, sections 2 and 6): a noon fold is fitted at its first test day's origin,
17:00 UTC on the eve, with v2's embargo; v2's form at midnight keeps v2's clocks. The shared `run` is generic: it
trains at the fold's clock and guards the noon table's own terms."""

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import build
from valiwatt.forecast.guards import LookaheadError
from valiwatt.forecast.issue import MIDNIGHT, NOON
from valiwatt.quality.demand import build_demand

H = pd.Timedelta(hours=1)


def test_a_noon_folds_fit_clock_is_the_eve_at_17_utc():
    noon, midnight = wf.month_folds("2024-01", "2024-12", issue=NOON), wf.month_folds("2024-01", "2024-12")
    assert noon[0].fit_clock == pd.Timestamp("2023-12-31T17:00Z")
    assert midnight[0].fit_clock == pd.Timestamp("2024-01-01T05:00Z")
    assert wf.folds("2024-01", "2024-01", issue=NOON)[0].fit_clock == pd.Timestamp("2023-12-31T17:00Z")
    for n, m in zip(noon, midnight, strict=True):
        assert n.issue == NOON and m.issue == MIDNIGHT
        assert n.fit_clock == m.fit_clock - 12 * H
        assert (n.test_month, n.train_last_day) == (m.test_month, m.train_last_day)  # the embargo does not move
        assert n.test_first_day - n.train_last_day == pd.Timedelta(days=wf.EMBARGO_DAYS + 1)
    assert wf.month_folds("2024-01", "2024-12") == wf.folds("2024-01", "2024-12")
    assert wf.month_folds("2024-01", "2024-12", issue=NOON) == wf.folds("2024-01", "2024-12", issue=NOON)
    assert wf.Fold(pd.Period("2024-01", "M"), pd.Timestamp("2023-12-24")) == midnight[0]  # v2's positional form


class Stub:
    """Stands in for a forecaster: records what it trains on, forecasts zero."""
    fits: list = []

    def __init__(self, key):
        self.key = key

    def fit(self, train):
        Stub.fits.append(train)
        self.n_train = len(train)
        return self

    def predict(self, test):
        return pd.Series(0.0, index=test.index)


def noon_table():
    first, last = pd.Timestamp("2021-01-01T06:00Z"), pd.Timestamp("2021-05-01T05:00Z")
    return build(build_demand(synth_demand(first, last), first, last).frame,
                 synth_obs(first, last).set_index("ts_utc")["temp_c"], issue=NOON)


def test_a_noon_run_trains_at_the_noon_clock_and_guards_the_noon_terms(monkeypatch):
    f = noon_table()
    monkeypatch.setattr(wf, "OLSForecaster", Stub)
    Stub.fits = []
    clocks = []

    def train_at(clock):
        clocks.append(clock)
        return f[f.index <= clock]

    fl = wf.month_folds("2021-03", "2021-04", issue=NOON)
    out = wf.run(f, fl, scenarios=("observed",), train_at=train_at)
    assert clocks == [pd.Timestamp("2021-02-28T17:00Z"), pd.Timestamp("2021-03-31T17:00Z")]
    assert set(out["fold"]) == {"2021-03", "2021-04"} and len(Stub.fits) == 2
    assert all(t["day"].max() <= fold.train_last_day for t, fold in zip(Stub.fits, fl, strict=True))

    def fail(*args, **kwargs):
        raise AssertionError("a fit ran before the guard")

    monkeypatch.setattr(wf, "OLSForecaster", fail)
    t = f.index[(f["day"] == pd.Timestamp("2021-02-01")).to_numpy()][10]  # a training row of both folds
    late = f.copy()
    late.loc[t, "lagday_last_utc"] = late.loc[t, "origin"] + H
    with pytest.raises(LookaheadError, match=r"fold 2021-03, training rows: 1 rows .*\(lag_day_after_origin; first"):
        wf.run(f, fl, scenarios=("observed",), train_at=lambda clock: late)
    with_lag24 = f.assign(lag24=np.where(f.index >= pd.Timestamp("2021-04-01T06:00Z"), 1.0, np.nan))
    with pytest.raises(LookaheadError, match=r"fold 2021-04, test rows: 360 rows .*\(input_24h_after_origin; first"):
        wf.run(with_lag24, fl[1:], scenarios=("observed",), train_at=lambda clock: f)


def test_a_fold_whose_clock_is_not_the_tables_origin_is_refused(monkeypatch):
    """A MIDNIGHT fold given a NOON table would train at 05:00 UTC, 12 hours after the noon clock its table's rows were
    issued at: refused before any table is asked for or any fit runs; the NOON fold on the same table runs (control).
    A table lacking the fold's first test day has no origin to check against and is refused too."""
    f = noon_table()
    monkeypatch.setattr(wf, "OLSForecaster", Stub)
    asked = []

    def train_at(clock):
        asked.append(clock)
        return f[f.index <= clock]

    midnight = wf.month_folds("2021-03", "2021-03")
    with pytest.raises(LookaheadError, match=r"fold 2021-03 is fitted at 2021-03-01 05:00:00\+00:00 \(midnight\), "
                                             r"but the table's origin .* is 2021-02-28 17:00:00\+00:00"):
        wf.run(f, midnight, scenarios=("observed",), train_at=train_at)
    assert asked == []
    noon = wf.month_folds("2021-03", "2021-03", issue=NOON)
    assert set(wf.run(f, noon, scenarios=("observed",), train_at=train_at)["fold"]) == {"2021-03"}
    assert asked == [noon[0].fit_clock]
    with pytest.raises(LookaheadError, match="first test day 2021-03-01 is none"):
        wf.run(f[f["day"] != pd.Timestamp("2021-03-01")], noon, scenarios=("observed",), train_at=train_at)
