"""Walk-forward folds: training always ends an embargo before the test month; never a random split. Each model fits
on its own usable training rows in each fold."""

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import BASE_C, build
from valiwatt.forecast.guards import LookaheadError, require_known_at_origin
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
        f[f"available_{s}"] = f["origin"].where(f[f"temp_{s}"].notna())  # fed days: known at the origin
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


def _no_fit(monkeypatch):
    """Any fit or knot placement the walk-forward attempts fails the test: the guards must refuse first."""
    def fail(*args, **kwargs):
        raise AssertionError("a fit ran before the guard")
    monkeypatch.setattr(wf, "forecaster", fail)
    monkeypatch.setattr(wf, "OLSForecaster", fail)


BREAKS = (("available_forecast", pd.NaT, "forecast_availability_missing"),
          ("available_forecast", pd.Timedelta(hours=1), "forecast_available_after_origin"),  # one hour after the origin
          ("origin", pd.NaT, "origin_missing"))


def broken_at(f: pd.DataFrame, t: pd.Timestamp, column: str, value) -> pd.DataFrame:
    out = f.copy()
    out.loc[t, column] = out.loc[t, "origin"] + value if isinstance(value, pd.Timedelta) else value
    return out


def test_a_fold_trains_only_on_rows_whose_forecast_was_known_at_their_origin(monkeypatch):
    """The rows a fold fits on are guarded where they are consumed, whoever supplies them: a train_at table holding a
    training row whose forecast has no availability, one available after its origin, or a row without its origin is
    refused before any fit, though the test table passes the guard on its own (Astra's reproduction)."""
    f = v2_table()
    require_known_at_origin(f)
    fl = wf.folds("2021-03", "2021-04")
    t = f.index[(f["day"] == pd.Timestamp("2021-02-01")).to_numpy()][10]  # a training row of both folds
    assert np.isfinite(f.loc[t, "temp_forecast"])
    # The control: train_at's rows are what the folds fit on.
    pd.testing.assert_frame_equal(wf.run(f, fl, models=("gam_forecast", "persistence"), train_at=lambda clock: f),
                                  wf.run(f, fl, models=("gam_forecast", "persistence")))
    _no_fit(monkeypatch)
    for column, value, term in BREAKS:
        broken = broken_at(f, t, column, value)
        for kwargs in ({}, {"models": ("persistence",)}, {"models": tuple(MODELS)}):
            with pytest.raises(LookaheadError, match=rf"fold 2021-03, training rows: 1 rows .*\({term}; first"):
                wf.run(f, fl, train_at=lambda clock, b=broken: b, **kwargs)


def test_a_fold_forecasts_only_rows_whose_forecast_was_known_at_their_origin(monkeypatch):
    """A fold's test rows, whose forecasts go on to calibrate the intervals, are guarded before any fit: a test row
    whose forecast has no availability, one available after its origin, or a row without its origin is refused."""
    f = v2_table()
    fl = wf.folds("2021-03", "2021-04")
    t = f.index[(f["day"] == pd.Timestamp("2021-04-12")).to_numpy()][10]  # a test row of the second fold only
    assert np.isfinite(f.loc[t, "temp_forecast"])
    for column, value, term in BREAKS:
        broken = broken_at(f, t, column, value)
        for kwargs in ({}, {"models": ("persistence",)}, {"models": tuple(MODELS)}):
            wf.run(broken, fl[:1], **kwargs)  # the first fold neither trains on nor forecasts the row
            with monkeypatch.context() as m:
                _no_fit(m)
                with pytest.raises(LookaheadError, match=rf"fold 2021-04, test rows: 1 rows .*\({term}; first"):
                    wf.run(broken, fl[1:], train_at=lambda clock: f, **kwargs)
