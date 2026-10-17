"""The explainable model recovers known structure, never imputes, and the baseline is the week-earlier value; the GAM
is the ruled scikit-learn pipeline, its knots the rule on its own training rows."""

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Ridge
from sklearn.preprocessing import SplineTransformer, StandardScaler

from conftest import synth_demand, synth_obs
from valiwatt.forecast import model
from valiwatt.forecast.features import BASE_C, build
from valiwatt.forecast.guards import LookaheadError
from valiwatt.forecast.model import (
    GAMForecaster,
    OLSForecaster,
    baseline,
    check_knots,
    design,
    gam_basis,
    gam_design,
    gam_predict,
    knot_rule,
    usable_rows,
)
from valiwatt.quality.demand import build_demand

FIRST, LAST = pd.Timestamp("2021-01-01T06:00Z"), pd.Timestamp("2021-07-01T05:00Z")
TRAIN_LAST_DAY = pd.Timestamp("2021-04-30")
COLD_DAY = pd.Timestamp("2021-06-10")  # a test day far colder than any training hour


def frame():
    demand = build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame
    temp = synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"]
    return build(demand, temp)


def with_forecast(f: pd.DataFrame, seed: int = 5) -> pd.DataFrame:
    """A forecast temperature beside the observation, two days no run fed, and a demand whose heating response differs
    by hour of the day."""
    f = f.copy()
    rng = np.random.default_rng(seed)
    f["temp_forecast"] = f["temp_observed"] + rng.normal(0, 1.5, len(f))
    f.loc[f["day"].isin(pd.to_datetime(["2021-02-10", "2021-03-03"])), "temp_forecast"] = np.nan
    f.loc[f["day"] == COLD_DAY, "temp_forecast"] = -35.0
    f["hdh_forecast"] = np.maximum(BASE_C - f["temp_forecast"], 0.0)
    f["cdh_forecast"] = np.maximum(f["temp_forecast"] - BASE_C, 0.0)
    f["available_forecast"] = f["origin"].where(f["temp_forecast"].notna())  # fed days: known at the origin
    slope = np.where(f["hour"] < 6, 450.0, np.where(f["hour"].between(12, 17), 0.0, 200.0))
    f["target"] = 15_000 + slope * f["hdh_forecast"] + 0.1 * f["lag24"] + rng.normal(0, 60, len(f))
    return f


def train_test(f):
    return f[f["day"] <= TRAIN_LAST_DAY], f[f["day"] > TRAIN_LAST_DAY]


class SpikeGam:
    """The spike's `Gam`, as the owner's ruling measured it (scratch compare2.py), written out again: the ruled
    scikit-learn pipeline, its settings literal."""

    def fit(self, d, s):
        self.s = s
        self.sp = SplineTransformer(n_knots=10, degree=3, knots="quantile", extrapolation="linear",
                                    include_bias=False).fit(d[[f"temp_{s}"]].to_numpy())
        X = self._X(d)
        self.sc = StandardScaler().fit(X)
        self.r = Ridge(alpha=1.0).fit(self.sc.transform(X), d["target"].to_numpy())
        return self

    def _X(self, d):
        cells = {f"cell:{dt}:{h:02d}": ((d["day_type"] == dt) & (d["hour"] == h)).to_numpy(float)
                 for dt in ("weekday", "saturday", "sunday_holiday") for h in range(24)}
        B = self.sp.transform(d[[f"temp_{self.s}"]].to_numpy())
        Hh = np.column_stack([(d["hour"] == h).to_numpy(float) for h in range(24)])
        tens = (Hh[:, :, None] * B[:, None, :]).reshape(len(d), -1)
        return np.column_stack([np.column_stack(list(cells.values())), tens,
                                d["lag24"].to_numpy(), d["lag168"].to_numpy()])

    def predict(self, d):
        return self.r.predict(self.sc.transform(self._X(d)))


def usable(d, s, target=True):
    return usable_rows(design(d, s), d["target"] if target else None)


# ------------------------------------------------------------------------------------------------ least squares


def test_model_recovers_a_planted_heating_effect():
    f = frame()
    rng = np.random.default_rng(3)
    # Plant demand = cell mean + 400 MW per heating degree at night + noise; lags carry no signal.
    f["target"] = 18_000 + 400 * f["hdh_observed"] * f["hour"].isin(range(0, 6)) + rng.normal(0, 50, len(f))
    m = OLSForecaster("observed").fit(f.iloc[200:])
    assert abs(m.coefficients()["heating:night"] - 400) < 15


def test_rows_with_a_missing_input_are_not_predicted():
    f = frame()
    m = OLSForecaster("observed").fit(f.iloc[200:])
    f.loc[f.index[300], "lag24"] = np.nan
    pred = m.predict(f)
    assert np.isnan(pred.iloc[300]) and np.isfinite(pred.iloc[301])
    # The GAM: a missing lag, or a day no run fed, is not predicted either; nothing is imputed.
    g = with_forecast(frame())
    gam = GAMForecaster("forecast").fit(g.iloc[200:])
    g.loc[g.index[3000], "lag24"] = np.nan
    pred = gam.predict(g)
    no_run = g["temp_forecast"].isna()
    assert no_run.sum() == 48 and pred[no_run].isna().all()
    assert np.isnan(pred.iloc[3000]) and np.isfinite(pred.iloc[3001])
    assert (pred.notna() == usable(g, "forecast", target=False)).all()


def test_baseline_is_the_week_earlier_value():
    f = frame()
    t = f.index[400]
    assert baseline(f)[t] == f.loc[t - pd.Timedelta(hours=168), "target"]


def test_design_has_one_named_column_per_coefficient():
    X = design(frame(), "persistence")
    assert X.columns.is_unique and "lag24" in X.columns and any(c.startswith("heating:") for c in X.columns)
    # Each row falls in exactly one hour-by-day-type cell.
    cells = X[[c for c in X.columns if c.startswith("cell:")]]
    assert (cells.sum(axis=1) == 1).all()


# ------------------------------------------------------------------------------------------------------- the GAM


def test_the_gam_predicts_what_the_ruled_pipeline_predicts():
    """The production model is the spike's pipeline exactly: the same design, the same scaler and ridge, the same
    forecasts, extrapolation below and above the training range included."""
    f = with_forecast(frame())
    train, test = train_test(f)
    tr = train[usable(train, "forecast")]
    te = test[usable(test, "forecast", target=False)]
    spike = SpikeGam().fit(tr, "forecast")
    gam = GAMForecaster("forecast").fit(train)
    assert te["temp_forecast"].min() < tr["temp_forecast"].min() and te["temp_forecast"].max() > tr["temp_forecast"].max()

    X = gam_design(tr, "forecast", gam.knots)
    assert X.shape == (len(tr), 338) and len(gam.columns) == 338 and len(set(gam.columns)) == 338
    np.testing.assert_array_equal(X, spike._X(tr))
    np.testing.assert_allclose(gam.center, spike.sc.mean_, rtol=1e-12)
    np.testing.assert_allclose(gam.scale, spike.sc.scale_, rtol=1e-12)
    np.testing.assert_allclose(gam.coef, spike.r.coef_, rtol=1e-9, atol=1e-9)
    assert gam.intercept == pytest.approx(spike.r.intercept_, rel=1e-12)
    assert gam.n_train == len(tr)

    pred = gam.predict(test)
    np.testing.assert_allclose(pred[te.index].to_numpy(), spike.predict(te), rtol=1e-9, atol=0)
    assert pred.drop(te.index).isna().all()


def test_the_knot_rule_is_the_spline_transformer_s_quantile_placement():
    f = with_forecast(frame())
    train, _ = train_test(f)
    temps = train.loc[usable(train, "forecast"), "temp_forecast"].to_numpy()
    sp = SplineTransformer(n_knots=10, degree=3, knots="quantile", extrapolation="linear",
                           include_bias=False).fit(temps.reshape(-1, 1))
    base = sp.bsplines_[0].t[3:-3]
    assert base.shape == (10,)
    np.testing.assert_array_equal(knot_rule(temps), base)
    np.testing.assert_array_equal(GAMForecaster("forecast").fit(train).knots, base)
    # The rule reads the finite values only.
    np.testing.assert_array_equal(knot_rule(np.r_[temps, np.nan]), base)


def test_a_fit_whose_knots_saw_a_test_row_is_refused(monkeypatch):
    """Knots placed on the training rows and a test row are not the rule on the fit's own rows: the fit refuses, even
    for one test hour that moves no knot by a hundredth of a degree. Knots 1.1e-12 C off the rule are refused."""
    f = with_forecast(frame())
    train, test = train_test(f)
    seen = test.loc[usable(test, "forecast", target=False), "temp_forecast"].to_numpy()
    real = model._place_knots
    monkeypatch.setattr(model, "_place_knots", lambda temps: real(np.r_[temps, seen]))
    with pytest.raises(LookaheadError, match="quantile rule"):
        GAMForecaster("forecast").fit(train)
    # One test row is enough, below the training range or inside it. A test hour at the training median, or the test
    # hour nearest it, moves no knot by a hundredth of a degree, and the fit still refuses: the check is to 1e-12.
    own = train.loc[usable(train, "forecast"), "temp_forecast"].to_numpy()
    median = np.median(own)
    for leaked in (seen.min(), median, seen[np.argmin(np.abs(seen - median))]):
        shift = np.abs(real(np.r_[own, leaked]) - knot_rule(own)).max()
        assert shift > model.KNOT_TOLERANCE and (leaked < own.min() or shift < 0.01), leaked
        monkeypatch.setattr(model, "_place_knots", lambda temps, x=leaked: real(np.r_[temps, x]))
        with pytest.raises(LookaheadError, match="quantile rule"):
            GAMForecaster("forecast").fit(train)
    # The tolerance is 1e-12 C, absolute: knots off the rule by 1.1e-12 either way are refused.
    assert model.KNOT_TOLERANCE == 1e-12
    for sign in (1, -1):
        with pytest.raises(LookaheadError, match="quantile rule"):
            check_knots(knot_rule(own) + sign * 1.1e-12, own)
    # Every knot is checked, the two end knots (the training minimum and maximum) included: one of them alone off the
    # rule by 1.1e-12, every other knot exact, is refused.
    for i in (0, -1):
        for sign in (1, -1):
            k = knot_rule(own).copy()
            k[i] += sign * 1.1e-12
            assert np.count_nonzero(k != knot_rule(own)) == 1, (i, sign)
            with pytest.raises(LookaheadError, match="quantile rule"):
                check_knots(k, own)


def test_a_fit_on_its_own_rows_passes_the_knot_check():
    """The control: the fit's own usable rows, from a table that also holds rows it cannot use (no target, no run, no
    lag), pass the check, to its 1e-12 C tolerance; the rows without a target are still forecast."""
    f = with_forecast(frame())
    train, _ = train_test(f)
    train = train.copy()
    train.loc[train.index[-30:], "target"] = np.nan
    gam = GAMForecaster("forecast").fit(train)
    own = train.loc[usable(train, "forecast"), "temp_forecast"].to_numpy()
    assert gam.n_train == len(own) < int(train["temp_forecast"].notna().sum())
    check_knots(gam.knots, own)
    np.testing.assert_array_equal(gam.knots, knot_rule(own))
    with pytest.raises(LookaheadError):
        check_knots(gam.knots, train["temp_forecast"].to_numpy())  # more rows than its own
    # Knots within the 1e-12 C tolerance of the rule (a recomputation's rounding) pass, either way.
    for sign in (1, -1):
        check_knots(gam.knots + sign * 0.9e-12, own)
    # The rows it could not train on (no target) are still forecast: a forecast needs its inputs, not its outcome.
    pred = gam.predict(train.iloc[-30:])
    assert train["target"].iloc[-30:].isna().all() and np.isfinite(pred.to_numpy()).all()


def test_a_fit_on_a_forecast_without_its_availability_is_refused(monkeypatch):
    """A fit checks the one input it reads, whoever supplied the rows: a training row holding a finite forecast whose
    availability is NaT or absent, after its origin, or without its origin is refused, before the knots are placed,
    by the GAM and by the least squares on the forecast; fits on the observed or persistence input read no forecast
    and fit as before (control)."""
    f = with_forecast(frame())
    train, _ = train_test(f)
    gam = GAMForecaster("forecast").fit(train)  # the control: every row known at its origin
    t = train.index[500]
    assert usable(train, "forecast")[t]
    lost = train.copy()
    lost.loc[t, "available_forecast"] = pd.NaT
    late = train.copy()
    late.loc[t, "available_forecast"] = late.loc[t, "origin"] + pd.Timedelta(hours=1)
    unplaced = train.copy()
    unplaced.loc[t, "origin"] = pd.NaT
    cases = ((lost, "forecast_availability_missing"), (train.drop(columns=["available_forecast"]),
                                                       "forecast_availability_missing"),
             (late, "forecast_available_after_origin"), (unplaced, "origin_missing"))

    def no_knots(temps):
        raise AssertionError("knots placed before the check")
    with monkeypatch.context() as m:
        m.setattr(model, "_place_knots", no_knots)
        for broken, term in cases:
            with pytest.raises(LookaheadError, match=rf"the GAM fit on forecast: \d+ rows .*\({term}; first"):
                GAMForecaster("forecast").fit(broken)
            with pytest.raises(LookaheadError, match=rf"the least squares fit on forecast: \d+ rows .*\({term};"):
                OLSForecaster("forecast").fit(broken)
    assert gam.n_train == GAMForecaster("forecast").fit(train).n_train
    for broken, _ in cases:
        np.testing.assert_array_equal(GAMForecaster("observed").fit(broken).coef,
                                      GAMForecaster("observed").fit(train).coef)
        np.testing.assert_array_equal(OLSForecaster("persistence").fit(broken).beta,
                                      OLSForecaster("persistence").fit(train).beta)


def test_a_basis_rebuilt_from_the_stored_knots_equals_the_fitted_one():
    f = with_forecast(frame())
    train, test = train_test(f)
    temps = train.loc[usable(train, "forecast"), "temp_forecast"].to_numpy()
    fitted = SplineTransformer(n_knots=10, degree=3, knots="quantile", extrapolation="linear",
                               include_bias=False).fit(temps.reshape(-1, 1))
    knots = knot_rule(temps)
    grid = np.r_[np.linspace(temps.min() - 25, temps.min(), 30), temps, np.linspace(temps.max(), temps.max() + 20, 30)]
    rebuilt = gam_basis(grid, knots)
    assert rebuilt.shape == (len(grid), 11)
    np.testing.assert_array_equal(rebuilt, fitted.transform(grid.reshape(-1, 1)))
    # Past the training range each basis column continues as a straight line, below and above.
    for side in (np.linspace(temps.min() - 25, temps.min() - 5, 5), np.linspace(temps.max() + 5, temps.max() + 20, 5)):
        b = gam_basis(side, knots)
        np.testing.assert_allclose(np.diff(b, n=2, axis=0), 0, atol=1e-9)
        assert np.abs(np.diff(b, axis=0)).max() > 0
    # A missing temperature gives a row of NaN, never a value.
    assert np.isnan(gam_basis(np.array([np.nan, 0.0]), knots)[0]).all()


def test_each_hour_has_its_own_temperature_curve():
    """Demand planted at 450 MW per heating degree at night, none mid-day, 200 otherwise: each hour's curve follows
    its own slope."""
    f = with_forecast(frame())
    train, _ = train_test(f)
    gam = GAMForecaster("forecast").fit(train)
    # From 5 C down to -5 C, inside every hour's own training range: 10 more heating degrees.
    by_hour = train.loc[usable(train, "forecast")].groupby("hour")["temp_forecast"]
    assert (by_hour.min() < -5).all() and (by_hour.max() > 5).all()
    pe = gam.partial_effects(np.array([-5.0]), ref_c=5.0)
    effect = pe.set_index("hour")["effect_mw"]
    assert len(pe) == 24 and (pe["temp_c"] == -5.0).all()
    for h in range(6):
        assert effect[h] == pytest.approx(450 * 10, rel=0.05), h
    for h in range(12, 18):
        assert abs(effect[h]) < 0.05 * 450 * 10, h
    for h in (*range(6, 12), *range(18, 24)):
        assert effect[h] == pytest.approx(200 * 10, rel=0.05), h


def test_partial_effects_are_zero_at_the_reference_and_follow_the_coefficients():
    f = with_forecast(frame())
    train, test = train_test(f)
    gam = GAMForecaster("forecast").fit(train)
    temps = np.array([-35.0, -12.0, 0.0, 18.0, 25.0])
    pe = gam.partial_effects(temps)
    assert list(pe.columns) == ["hour", "temp_c", "effect_mw"] and len(pe) == 24 * len(temps)
    at_ref = pe[pe["temp_c"] == 18.0]
    assert len(at_ref) == 24 and (at_ref["effect_mw"].abs() < 1e-9).all()
    # The effect is the change in the forecast when only the temperature moves from 18 C, at that hour.
    rows = test[usable(test, "forecast", target=False)].groupby("hour").head(1)
    for t in temps:
        moved, ref = rows.copy(), rows.copy()
        for d, v in ((moved, t), (ref, 18.0)):
            d["temp_forecast"] = v
            d["hdh_forecast"], d["cdh_forecast"] = max(BASE_C - v, 0.0), max(v - BASE_C, 0.0)
        delta = (gam.predict(moved) - gam.predict(ref)).groupby(rows["hour"]).first()
        expected = pe[pe["temp_c"] == t].set_index("hour")["effect_mw"]
        np.testing.assert_allclose(delta.sort_index().to_numpy(), expected.sort_index().to_numpy(), atol=1e-6)
    other = gam.partial_effects(np.array([-12.0]), ref_c=0.0).set_index("hour")["effect_mw"]
    by18 = pe.set_index(["temp_c", "hour"])["effect_mw"]
    np.testing.assert_allclose(other.to_numpy(), (by18[-12.0] - by18[0.0]).to_numpy(), atol=1e-6)


def test_lag_coefficients_are_in_mw_per_mw():
    f = with_forecast(frame())
    train, test = train_test(f)
    gam = GAMForecaster("forecast").fit(train)
    lags = gam.lag_coefficients()
    assert set(lags) == {"lag24", "lag168"}
    assert lags["lag24"] == pytest.approx(gam.coef[-2] / gam.scale[-2]) and lags["lag168"] == \
        pytest.approx(gam.coef[-1] / gam.scale[-1])
    rows = test[usable(test, "forecast", target=False)].head(5)
    for lag in ("lag24", "lag168"):
        up = rows.copy()
        up[lag] = up[lag] + 1.0
        np.testing.assert_allclose((gam.predict(up) - gam.predict(rows)).to_numpy(), lags[lag], rtol=1e-6)


def test_gam_predict_equals_the_scaler_and_ridge():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(400, 338)) * rng.uniform(0.5, 50, 338) + rng.uniform(-20, 20, 338)
    y = X @ rng.normal(size=338) + 1_000 + rng.normal(size=400)
    sc = StandardScaler().fit(X)
    r = Ridge(alpha=1.0).fit(sc.transform(X), y)
    Xt = rng.normal(size=(50, 338)) * 30
    np.testing.assert_allclose(gam_predict(Xt, sc.mean_, sc.scale_, r.coef_, r.intercept_),
                               r.predict(sc.transform(Xt)), rtol=1e-9)
    # The fitted model predicts through it on its stored arrays.
    f = with_forecast(frame())
    train, test = train_test(f)
    gam = GAMForecaster("forecast").fit(train)
    te = test[usable(test, "forecast", target=False)]
    np.testing.assert_array_equal(gam.predict(te).to_numpy(),
                                  gam_predict(gam_design(te, "forecast", gam.knots), gam.center, gam.scale, gam.coef,
                                              gam.intercept))


def test_each_model_key_has_its_family_and_input():
    assert model.PRODUCTION == "gam_forecast" and model.COMPARATORS == {"family": "forecast", "shipped": "persistence"}
    for key, (family, scenario) in model.MODELS.items():
        m = model.forecaster(key)
        assert isinstance(m, GAMForecaster if family == "gam" else OLSForecaster) and m.scenario == scenario
    with pytest.raises(ValueError):
        model.forecaster("gam_persistence")
    assert (model.N_KNOTS, model.DEGREE, model.RIDGE_ALPHA) == (10, 3, 1.0)
    assert (model.KNOTS, model.EXTRAPOLATION, model.INCLUDE_BIAS) == ("quantile", "linear", False)
