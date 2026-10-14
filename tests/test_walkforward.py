"""Walk-forward folds: training always ends an embargo before the test month; never a random split."""

import pandas as pd
import pytest

from conftest import synth_demand, synth_obs
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import build
from valiwatt.quality.demand import build_demand


def test_training_ends_an_embargo_before_each_test_month():
    for fold in wf.folds("2020-01", "2023-12"):
        gap = fold.test_first_day - fold.train_last_day
        assert gap == pd.Timedelta(days=wf.EMBARGO_DAYS + 1)


def test_folds_cover_each_month_once_in_order():
    fl = wf.folds("2020-01", "2023-12")
    assert [str(f.test_month) for f in fl] == [str(p) for p in pd.period_range("2020-01", "2023-12", freq="M")]


def test_split_never_lets_a_training_day_reach_the_embargo():
    first, last = pd.Timestamp("2021-01-01T06:00Z"), pd.Timestamp("2021-05-01T05:00Z")
    f = build(build_demand(synth_demand(first, last), first, last).frame,
              synth_obs(first, last).set_index("ts_utc")["temp_c"])
    for fold in wf.folds("2021-03", "2021-04"):
        train, test = wf.split(f, fold)
        assert train["day"].max() <= fold.test_first_day - pd.Timedelta(days=wf.EMBARGO_DAYS + 1)
        assert set(test["day"].dt.to_period("M").astype(str)) == {str(fold.test_month)}
    out = wf.run(f, wf.folds("2021-03", "2021-04"))
    assert out["pred_observed"].notna().mean() > 0.95 and set(out["fold"]) == {"2021-03", "2021-04"}


def test_a_fold_without_training_rows_is_refused():
    first, last = pd.Timestamp("2021-01-01T06:00Z"), pd.Timestamp("2021-02-01T05:00Z")
    f = build(build_demand(synth_demand(first, last), first, last).frame,
              synth_obs(first, last).set_index("ts_utc")["temp_c"])
    with pytest.raises(ValueError):
        wf.run(f, wf.folds("2021-01", "2021-01"))
