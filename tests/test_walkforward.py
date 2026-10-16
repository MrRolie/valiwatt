"""Walk-forward folds: training always ends an embargo before the test month; never a random split. Each model fits
on its own usable training rows in each fold."""

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import BASE_C, build
from valiwatt.forecast.model import MODELS, OLSForecaster, design, forecaster, usable_rows
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


def v2_table():
    """A v1 synthetic table with the forecast inputs added: the forecast, and the late forecast, each missing on days
    of its own; one observation missing."""
    first, last = pd.Timestamp("2021-01-01T06:00Z"), pd.Timestamp("2021-06-01T05:00Z")
    f = build(build_demand(synth_demand(first, last), first, last).frame,
              synth_obs(first, last).set_index("ts_utc")["temp_c"])
    rng = np.random.default_rng(2)
    for s, gaps in (("forecast", ["2021-02-10", "2021-04-03"]), ("forecast_late", ["2021-03-15", "2021-04-20"])):
        f[f"temp_{s}"] = f["temp_observed"] + rng.normal(0, 1.5, len(f))
        f.loc[f["day"].isin(pd.to_datetime(gaps)), f"temp_{s}"] = np.nan
        f[f"hdh_{s}"] = np.maximum(BASE_C - f[f"temp_{s}"], 0.0)
        f[f"cdh_{s}"] = np.maximum(f[f"temp_{s}"] - BASE_C, 0.0)
    gap = f.index[(f["day"] == pd.Timestamp("2021-03-20")).to_numpy()][:5]
    f.loc[gap, ["temp_observed", "hdh_observed", "cdh_observed"]] = np.nan
    f["target"] = 15_000 + 300 * f["hdh_observed"].fillna(10.0) + rng.normal(0, 60, len(f))
    return f


def test_each_model_key_trains_on_its_own_usable_rows_in_each_fold():
    f = v2_table()
    fl = wf.folds("2021-03", "2021-05")
    out = wf.run(f, fl, models=tuple(MODELS))
    for key, (_, scenario) in MODELS.items():
        assert {f"pred_{key}", f"n_train_{key}"} <= set(out.columns)
    for fold in fl:
        train, test = wf.split(f, fold)
        rows = out[out["fold"] == str(fold.test_month)]
        assert rows.index.equals(test.index)
        for key, (_, scenario) in MODELS.items():
            own = usable_rows(design(train, scenario), train["target"])
            assert (rows[f"n_train_{key}"] == int(own.sum())).all(), (fold.test_month, key)
            expected = forecaster(key).fit(train[own]).predict(test)
            pd.testing.assert_series_equal(rows[f"pred_{key}"], expected, check_names=False, rtol=1e-9)
            assert (rows[f"pred_{key}"].notna() == usable_rows(design(test, scenario))).all(), (fold.test_month, key)
    # Each input's own gaps: the forecast's days are lost to the forecast models only, the late forecast's to the late
    # model only.
    n = out.groupby("fold").first()
    assert (n["n_train_gam_forecast"] == n["n_train_forecast"]).all()
    assert (n["n_train_gam_forecast"] != n["n_train_gam_forecast_late"]).any()
    assert (n["n_train_gam_observed"] == n["n_train_observed"]).all()
    assert (n["n_train_gam_observed"] != n["n_train_gam_forecast"]).all()
    # The least squares keys are v1's scenario names: pred_persistence is v1 as shipped.
    v1 = wf.run(f, fl, scenarios=("persistence",))
    pd.testing.assert_series_equal(out["pred_persistence"], v1["pred_persistence"])


def test_models_none_is_v1_exactly():
    """The control: without models, each fold fits v1's least squares on each scenario, and nothing else."""
    f = v2_table()
    fl = wf.folds("2021-03", "2021-04")
    for kwargs, scenarios in (({}, ("observed", "persistence")), ({"scenarios": ("persistence",)}, ("persistence",)),
                              ({"models": None}, ("observed", "persistence"))):
        out = wf.run(f, fl, **kwargs)
        assert list(out.columns) == ["target", "fold", "day", "scored_at", "hour", "day_type", "baseline"] + \
            [c for s in scenarios for c in (f"pred_{s}", f"n_train_{s}")], kwargs
        for fold in fl:
            train, test = wf.split(f, fold)
            rows = out[out["fold"] == str(fold.test_month)]
            for s in scenarios:
                m = OLSForecaster(s).fit(train)
                pd.testing.assert_series_equal(rows[f"pred_{s}"], m.predict(test), check_names=False)
                assert (rows[f"n_train_{s}"] == m.n_train).all()
    pd.testing.assert_frame_equal(wf.run(f, fl), wf.run(f, fl, models=("observed", "persistence")))
