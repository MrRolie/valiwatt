"""The frozen models (docs/plan-v2.md, sections 4.4 and 4.5): the GAM and its two comparison models as JSON files of
numbers, each refused when it no longer matches its hash or holds a spline setting other than the ruled ones; the
frozen GAM's forecast read from its file alone, whatever the module's settings say after the freeze, and checked
against the ruled scikit-learn pipeline built on its own; the freeze's own knot check; the echo premise; the eve of
the replayed year left out of the half-width; the partial effects drawn over the training range.

A synthetic world: two design years of demand that respond to the cold, the station's observations, and NBM-shaped
forecast runs; out-of-sample forecasts of 2023 for each model frozen, the eve of 2024 far off.
"""

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from conftest import synth_demand, synth_obs, synth_runs
from test_model import SpikeGam
from valiwatt.config import DESIGN_LAST
from valiwatt.forecast import freeze as fz
from valiwatt.forecast import model
from valiwatt.forecast import validate as vd
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.evaluate import conformal_quantile
from valiwatt.forecast.features import build, scored_at
from valiwatt.forecast.guards import LookaheadError
from valiwatt.quality.demand import build_demand

FIRST, LAST = pd.Timestamp("2022-01-01T06:00Z"), DESIGN_LAST
YEAR = 2024
EVE = pd.Timestamp("2023-12-31")
KEYS = ("gam_forecast", "forecast", "persistence")


@pytest.fixture(scope="module")
def world():
    raw = synth_demand(FIRST, LAST)
    obs = synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"]
    raw["mw"] = raw["mw"] + 300 * np.maximum(18 - obs.reindex(raw["ts_utc"]).to_numpy(), 0)
    runs = synth_runs(FIRST - pd.Timedelta(days=3), LAST)
    runs = runs[runs["runtime"] + pd.Timedelta(hours=2) <= LAST].reset_index(drop=True)
    days = pd.date_range("2022-01-01", "2023-12-31")
    forecasts = vd.forecast_inputs(runs, days)
    f = build(build_demand(raw, FIRST, LAST).frame, obs, forecasts)
    return f


@pytest.fixture(scope="module")
def oos(world):
    """Out-of-sample rows of 2023 as validation logs them: each model's forecast a few percent off, and far off on
    2023-12-31, the eve of the replayed year, so whether that day calibrates moves the half-width."""
    f = world
    rows = f[(f["day"] >= pd.Timestamp("2023-01-01")) & (f["day"] <= EVE)]
    out = rows[["target", "day"]].copy()
    out["fold"] = out["day"].dt.to_period("M").astype(str)
    out["scored_at"] = scored_at(out["day"], LAST)
    rng = np.random.default_rng(5)
    for i, key in enumerate(KEYS):
        out[f"pred_{key}"] = out["target"] * (1 + rng.normal(0, 0.02 + 0.01 * i, len(out)))
        out.loc[out["day"] == EVE, f"pred_{key}"] = out.loc[out["day"] == EVE, "target"] / 2
    return out


@pytest.fixture(scope="module")
def frozen_gam(world, oos):
    return fz.fit_frozen(world, oos, YEAR, key="gam_forecast")


def train_rows(f, key="gam_forecast"):
    """The usable training rows of the frozen model of `key`, taken from the table as the fit takes them."""
    _, scenario = model.MODELS[key]
    train, _ = wf.split(f, wf.folds(f"{YEAR}-01", f"{YEAR}-01")[0])
    return train[model.usable_rows(model.design(train, scenario), train["target"])]


def test_a_frozen_gam_round_trips_through_json_and_predicts_as_fitted(world, frozen_gam):
    frozen, _ = frozen_gam
    assert (frozen.family, frozen.scenario, frozen.replay_year) == ("gam", "forecast", YEAR)
    assert list(frozen.columns) == model.gam_columns() and len(frozen.knots) == model.N_KNOTS
    fitted = model.GAMForecaster("forecast").fit(wf.split(world, wf.folds("2024-01", "2024-01")[0])[0])
    rows = world[world["day"] >= pd.Timestamp("2023-11-01")].copy()
    rows.loc[rows.index[5], "temp_forecast"] = np.nan  # a row without its input is not predicted
    # Two hours past the training range, below and above it: each curve continues as a straight line there.
    rows.loc[rows.index[6], "temp_forecast"] = frozen.knots[0] - 10.0
    rows.loc[rows.index[7], "temp_forecast"] = frozen.knots[-1] + 10.0
    expected = fitted.predict(rows)
    again = fz.FrozenModel.from_json(frozen.to_json())
    assert again.sha256() == frozen.sha256() and again.knots == frozen.knots
    for m in (frozen, again):
        p = m.predict(rows)
        assert p["forecast"].isna().equals(expected.isna()) and np.isnan(p["forecast"].iloc[5])
        ok = expected.notna()
        np.testing.assert_allclose(p.loc[ok, "forecast"], expected[ok], rtol=1e-9, atol=0)
        np.testing.assert_allclose(p["lower"], p["forecast"] * (1 - m.q))
        np.testing.assert_allclose(p["upper"], p["forecast"] * (1 + m.q))
    # The frozen GAM predicts what the ruled scikit-learn pipeline, fitted on the same rows, predicts: the spike's
    # pipeline built on its own (test_model.SpikeGam: its design, SplineTransformer, StandardScaler and Ridge, the
    # settings literal, none of model's functions), so a fault in the prediction path the fitted and the frozen GAM
    # share (model.gam_design, model.gam_predict) cannot pass.
    spike = SpikeGam().fit(train_rows(world), "forecast")
    np.testing.assert_allclose(spike.sp.bsplines_[0].t[3:-3], frozen.knots, rtol=0, atol=1e-12)
    ok = expected.notna()
    assert ok.sum() > 1000 and ok.iloc[6] and ok.iloc[7]
    for m in (frozen, again):
        np.testing.assert_allclose(m.predict(rows.loc[ok])["forecast"], spike.predict(rows.loc[ok]), rtol=1e-9, atol=0)


def past_the_range(world, frozen):
    """Usable rows of late 2023, one day of them forecast 10 C below the frozen GAM's training range and one 10 C
    above it, where its extrapolation setting bites; and temperatures for its curves over the same span."""
    rows = world[world["day"] >= pd.Timestamp("2023-11-01")]
    rows = rows[model.usable_rows(model.design(rows, "forecast"))].copy()
    rows.loc[rows.index[:24], "temp_forecast"] = frozen.knots[0] - 10.0
    rows.loc[rows.index[24:48], "temp_forecast"] = frozen.knots[-1] + 10.0
    return rows, np.linspace(frozen.knots[0] - 10.0, frozen.knots[-1] + 10.0, 41)


# The module's GAM settings, each changed after the freeze: the module's attribute, its new value, and what the
# frozen model's file, read again, is refused by (None: the file still loads).
AMBIENT = {
    "extrapolation": ("EXTRAPOLATION", "constant", "extrapolation"),
    "degree": ("DEGREE", 2, "degree"),
    "include_bias": ("INCLUDE_BIAS", True, "include_bias"),
    "n_knots": ("N_KNOTS", 11, "increasing knots"),
    "day_types": ("DAY_TYPES", tuple(reversed(model.DAY_TYPES)), None),
    "day_types_added": ("DAY_TYPES", (*model.DAY_TYPES, "extra"), None),  # another count: the cells' span moves
}


@pytest.mark.parametrize("change", list(AMBIENT))
def test_a_frozen_gam_forecasts_from_its_file_whatever_the_module_now_says(change, world, frozen_gam, monkeypatch):
    """Plan section 4.4: the frozen GAM rebuilds its design from its stored knots, settings and column order. With
    one of the module's GAM settings changed after the freeze, the frozen GAM forecasts and draws its curves exactly
    as before, past its training range included; its file, read again, is refused when the setting is one the file
    is checked against (the three spline settings, the number of knots), and otherwise loads under the same hash and
    forecasts exactly as before: never a different forecast under an unchanged hash. The control: the change is live,
    the module's own design of the same rows, or its column names, are no longer the frozen GAM's."""
    frozen, _ = frozen_gam
    rows, temps = past_the_range(world, frozen)
    text, knots = frozen.to_json(), np.asarray(frozen.knots)
    before, curves = frozen.predict(rows).to_numpy(), frozen.partial_effects(temps).to_numpy()
    design_before = model.gam_design(rows, "forecast", knots)
    name, value, refused_by = AMBIENT[change]
    monkeypatch.setattr(model, name, value)
    design_now = model.gam_design(rows, "forecast", knots)
    assert model.gam_columns() != list(frozen.columns) or design_now.shape != design_before.shape \
        or not np.array_equal(design_now, design_before), change  # the control: the change is live
    np.testing.assert_array_equal(frozen.predict(rows).to_numpy(), before)
    np.testing.assert_array_equal(frozen.partial_effects(temps).to_numpy(), curves)
    if refused_by:
        with pytest.raises(fz.FrozenModelError, match=refused_by):
            fz.FrozenModel.from_json(text)
    else:
        again = fz.FrozenModel.from_json(text)
        assert again.sha256() == frozen.sha256()
        np.testing.assert_array_equal(again.predict(rows).to_numpy(), before)
        np.testing.assert_array_equal(again.partial_effects(temps).to_numpy(), curves)


def _reordered(frozen, order):
    """The frozen GAM with its columns, means, scales and coefficients all taken in `order`: the same numbers, its
    columns written in another order."""
    def take(v):
        return [v[i] for i in order]
    return dataclasses.replace(frozen, columns=take(frozen.columns), beta=take(list(frozen.beta)),
                               center=take(frozen.center), scale=take(frozen.scale))


def test_a_frozen_gam_reads_its_cells_in_their_stored_order_and_refuses_columns_it_cannot_rebuild(world, frozen_gam):
    """The day types are read in the order of the stored cell columns: a file written with them in another order (as
    a fit under another order of model.DAY_TYPES writes it, its means, scales and coefficients in that order, its hash
    its own) forecasts and draws its curves as the frozen GAM does. A file whose columns are not those its stored
    knots, settings and day types generate, name for name and in order, matches its own hash and loads, and its
    forecast and its curves are refused (FrozenModelError), never computed on columns read in another order: the
    cells written hour major, a spline column renamed, the two lags swapped, a lag dropped. A file whose cells do not
    cover a row's day type (a day type renamed, or one dropped) forecasts no such row: refused, never with all-zero
    cells; its curves, which read no cell, are the frozen GAM's."""
    frozen, _ = frozen_gam
    rows, temps = past_the_range(world, frozen)
    before, curves = frozen.predict(rows), frozen.partial_effects(temps)
    cols = list(frozen.columns)
    n_cells = 24 * len(model.DAY_TYPES)
    by_type = [cols.index(f"cell:{dt}:{h:02d}") for dt in reversed(model.DAY_TYPES) for h in range(24)]
    other = _reordered(frozen, by_type + list(range(n_cells, len(cols))))
    assert other.columns[0] == "cell:sunday_holiday:00" and other.sha256() != frozen.sha256()
    again = fz.FrozenModel.from_json(other.to_json())  # the control: another order of the day types forecasts the same
    for m in (other, again):
        np.testing.assert_allclose(m.predict(rows).to_numpy(), before.to_numpy(), rtol=1e-12, atol=0)
        np.testing.assert_allclose(m.partial_effects(temps).to_numpy(), curves.to_numpy(), rtol=1e-12, atol=1e-9)

    hour_major = [cols.index(f"cell:{dt}:{h:02d}") for h in range(24) for dt in model.DAY_TYPES]
    renamed = dataclasses.replace(frozen, columns=cols[:100] + ["spline:01:99"] + cols[101:])
    dropped = dataclasses.replace(frozen, columns=cols[:-1], beta=frozen.beta[:-1], center=frozen.center[:-1],
                                  scale=frozen.scale[:-1])
    bad = {"hour_major": _reordered(frozen, hour_major + list(range(n_cells, len(cols)))),
           "renamed": renamed,
           "lags_swapped": _reordered(frozen, list(range(len(cols) - 2)) + [len(cols) - 1, len(cols) - 2]),
           "lag_dropped": dropped}
    for name, m in bad.items():
        loaded = fz.FrozenModel.from_json(m.to_json())
        assert loaded.sha256() == m.sha256() != frozen.sha256(), name
        for each in (m, loaded):
            with pytest.raises(fz.FrozenModelError, match="stored columns are not"):
                each.predict(rows)
            with pytest.raises(fz.FrozenModelError, match="stored columns are not"):
                each.partial_effects(temps)
    weekday = [i for i, c in enumerate(cols) if c.startswith("cell:weekday:")]
    keep = [i for i in range(len(cols)) if i not in weekday]
    uncovered = {"renamed_type": dataclasses.replace(frozen, columns=[c.replace("cell:weekday:", "cell:workday:")
                                                                      for c in cols]),
                 "type_dropped": dataclasses.replace(frozen, columns=[cols[i] for i in keep],
                                                     beta=[frozen.beta[i] for i in keep],
                                                     center=[frozen.center[i] for i in keep],
                                                     scale=[frozen.scale[i] for i in keep])}
    assert (rows["day_type"] == "weekday").any()
    for name, m in uncovered.items():  # rows of a day type its cells do not cover would get all-zero cells
        loaded = fz.FrozenModel.from_json(m.to_json())
        assert loaded.sha256() == m.sha256() != frozen.sha256(), name
        for each in (m, loaded):
            with pytest.raises(fz.FrozenModelError, match=r"day type the frozen GAM's stored cells do not cover: "
                                                          r"\['weekday'\]"):
                each.predict(rows)
            np.testing.assert_allclose(each.partial_effects(temps).to_numpy(), curves.to_numpy(), rtol=1e-12,
                                       atol=1e-9)  # the curves read no cell


def test_a_frozen_gam_with_an_altered_knot_scale_or_coefficient_is_refused(frozen_gam):
    """A knot, a scale or a coefficient edited in the file is refused by the hash; so is every other field the hash
    covers (the three spline settings have their own test): a mean, the intercept, the half-width, a column name, the
    input and the family, each a value the model itself accepts, so only the hash can refuse it."""
    frozen, _ = frozen_gam
    text = frozen.to_json()
    assert fz.FrozenModel.from_json(text).sha256() == frozen.sha256()  # the control: the file as written loads
    for field, i, delta in (("knots", 4, 1e-6), ("scale", 100, 1e-6), ("beta", 200, 1e-6)):
        d = json.loads(text)
        d[field][i] += delta * max(1.0, abs(d[field][i]))
        with pytest.raises(fz.FrozenModelError, match="does not match its own hash"):
            fz.FrozenModel.from_json(json.dumps(d))

    def nudged(x):
        return x + 1e-6 * max(1.0, abs(x))

    center, columns = list(frozen.center), list(frozen.columns)
    center[100], columns[0] = nudged(center[100]), columns[0] + "_x"
    for field, other in (("center", center), ("intercept", nudged(frozen.intercept)), ("q", nudged(frozen.q)),
                         ("columns", columns), ("scenario", "observed"), ("family", "ols")):
        assert dataclasses.replace(frozen, **{field: other}).sha256() != frozen.sha256(), field
        with pytest.raises(fz.FrozenModelError, match="does not match its own hash"):
            fz.FrozenModel.from_json(json.dumps(json.loads(text) | {field: other}))


def _hash_of(d: dict) -> str:
    return fz.frozen_sha256(d["family"], d["scenario"], d["columns"], d["knots"], d["degree"], d["extrapolation"],
                            d["include_bias"], d["center"], d["scale"], d["beta"], d["intercept"], d["q"])


def test_a_frozen_gam_with_another_degree_extrapolation_or_bias_is_refused(frozen_gam):
    """Each setting other than the owner's ruling (degree 3, linear extrapolation, no bias column) is refused when
    the model is built and when its file is read, a file whose hash was recomputed with that setting included; and
    each changes the hash. A file without the settings reads the ruled ones, and its hash must still match."""
    frozen, _ = frozen_gam
    ruled = json.loads(frozen.to_json())
    assert _hash_of(ruled) == ruled["sha256"] == frozen.sha256()
    for name, other in (("degree", 2), ("extrapolation", "constant"), ("include_bias", True)):
        with pytest.raises(fz.FrozenModelError, match=name):
            dataclasses.replace(frozen, **{name: other})
        d = dict(ruled) | {name: other}
        d["sha256"] = _hash_of(d)
        assert d["sha256"] != frozen.sha256(), name  # the setting is in the hash
        with pytest.raises(fz.FrozenModelError, match=name):
            fz.FrozenModel.from_json(json.dumps(d))
        # The setting dropped from a file hashed with it: the ruled default is read, and the hash refuses it.
        del d[name]
        with pytest.raises(fz.FrozenModelError, match="does not match its own hash"):
            fz.FrozenModel.from_json(json.dumps(d))
    bare = {k: v for k, v in ruled.items() if k not in ("degree", "extrapolation", "include_bias")}
    assert fz.FrozenModel.from_json(json.dumps(bare)).sha256() == frozen.sha256()
    # Least squares carries the same settings and the same refusal.
    ols = fz.FrozenModel(columns=("lag24", "lag168"), beta=[0.5, 0.3], q=0.1, replay_year=YEAR,
                         train_last_day="2023-12-24", n_train=1)
    with pytest.raises(fz.FrozenModelError, match="extrapolation"):
        dataclasses.replace(ols, extrapolation="constant")


def test_a_frozen_gam_whose_knots_are_not_ten_finite_increasing_values_is_refused(frozen_gam):
    """The GAM's basis is rebuilt from its stored knots: they must be model.N_KNOTS finite, strictly increasing values.
    Each other set is refused when the model is built and when its file is read, a file whose hash was recomputed
    with those knots, so the refusal is the knots' and not the hash's."""
    frozen, _ = frozen_gam
    k = list(frozen.knots)
    assert dataclasses.replace(frozen, knots=k).sha256() == frozen.sha256()  # the control: the fitted knots
    ruled = json.loads(frozen.to_json())
    bad = {"reversed": k[::-1], "nan": k[:4] + [float("nan")] + k[5:], "infinite": k[:-1] + [float("inf")],
           "repeated": k[:5] + [k[4]] + k[6:], "nine": k[:-1]}
    for name, knots in bad.items():
        with pytest.raises(fz.FrozenModelError, match="increasing knots"):
            dataclasses.replace(frozen, knots=knots)
        d = ruled | {"knots": knots}
        d["sha256"] = _hash_of(d)
        with pytest.raises(fz.FrozenModelError, match="increasing knots"):
            fz.FrozenModel.from_json(json.dumps(d))


def test_a_freeze_whose_knots_are_not_the_rule_on_its_training_rows_is_refused(world, oos, monkeypatch):
    """The fit's own knot check is bypassed and its knots are placed on the frozen table's training rows plus one
    later row (an hour of the embargo, after the training rows, forecast colder than any of them): the freeze's own
    check, on the training rows it takes again from the table, must refuse."""
    own = train_rows(world)["temp_forecast"].to_numpy()
    table = world.copy()
    hour = pd.Timestamp("2023-12-28T12:00Z")
    assert table.loc[hour, "day"] > pd.Timestamp(wf.folds("2024-01", "2024-01")[0].train_last_day)
    table.loc[hour, "temp_forecast"] = later = own.min() - 2.0
    assert not np.allclose(model.knot_rule(np.append(own, later)), model.knot_rule(own), rtol=0, atol=1e-12)
    real_fit = model.GAMForecaster.fit

    def leaky_fit(self, table):
        with monkeypatch.context() as m:
            m.setattr(model, "_place_knots", lambda temps: model.knot_rule(np.append(temps, later)))
            m.setattr(model, "check_knots", lambda knots, temps: None)
            return real_fit(self, table)

    monkeypatch.setattr(model.GAMForecaster, "fit", leaky_fit)
    with pytest.raises(fz.FrozenModelError, match="quantile rule") as e:
        fz.fit_frozen(table, oos, YEAR, key="gam_forecast")
    assert isinstance(e.value.__cause__, LookaheadError)
    monkeypatch.undo()
    frozen, _ = fz.fit_frozen(table, oos, YEAR, key="gam_forecast")  # the control: the ruled fit passes the check
    np.testing.assert_allclose(frozen.knots, model.knot_rule(own), rtol=0, atol=1e-12)


def test_a_production_model_with_a_lag_coefficient_not_positive_is_refused(frozen_gam, world, oos):
    frozen, _ = frozen_gam
    ols, _ = fz.fit_frozen(world, oos, YEAR, key="forecast")
    for m in (frozen, ols):
        lags = m.lag_coefficients()
        assert lags["lag24"] > 0 and lags["lag168"] > 0, lags
        fz.require_echo_premise(m)  # the control: both positive, accepted
        for lag in model.LAGS:
            i = m.columns.index(lag)
            for value in (0.0, -abs(m.beta[i])):
                beta = m.beta.copy()
                beta[i] = value
                bad = dataclasses.replace(m, beta=beta)
                assert bad.lag_coefficients()[lag] == pytest.approx(value / (m.scale[i] if m.family == "gam" else 1))
                with pytest.raises(fz.FrozenModelError, match="echo premise"):
                    fz.require_echo_premise(bad)


def test_a_frozen_ols_keeps_v1_s_forecast(world, oos):
    """The control: least squares on yesterday's temperature, frozen by the default key, has v1's coefficients,
    half-width rule and forecast: X @ beta on v1's named columns, the interval forecast x (1 -/+ q)."""
    frozen, coef = fz.fit_frozen(world, oos, YEAR)
    assert (frozen.family, frozen.scenario) == ("ols", "persistence")
    fold = wf.folds(f"{YEAR}-01", f"{YEAR}-01")[0]
    train, _ = wf.split(world, fold)
    v1 = model.OLSForecaster("persistence").fit(train)
    assert list(frozen.columns) == v1.columns
    np.testing.assert_array_equal(frozen.beta, v1.beta)
    assert list(coef.columns) == ["feature", "frozen", "no_lag"] and list(coef["feature"]) == v1.columns
    rows = world[world["day"] >= pd.Timestamp("2023-10-01")]
    X = model.design(rows, "persistence")[v1.columns]
    ok = model.usable_rows(X)
    forecast = pd.Series(np.nan, index=rows.index)
    forecast[ok] = X[ok].to_numpy() @ v1.beta
    expected = pd.DataFrame({"forecast": forecast, "lower": forecast * (1 - frozen.q),
                             "upper": forecast * (1 + frozen.q)}, index=rows.index)
    pd.testing.assert_frame_equal(frozen.predict(rows), expected)
    again = fz.FrozenModel.from_json(frozen.to_json())
    pd.testing.assert_frame_equal(again.predict(rows), expected)
    assert fz.INPUT_COLUMNS == ["day_type", "hour", "temp_persistence", "lag24", "lag168"]


# --- least squares from its file alone (plan section 4.4) -----------------------------------------------------------

# v1's hour blocks and base temperature, written out: what the least squares' forecast read from the module before
# the frozen model stored them.
V1_BLOCKS = {"night": range(0, 6), "morning": range(6, 10), "day": range(10, 16), "evening": range(16, 21),
             "late": range(21, 24)}
HOUR_6_AT_NIGHT = {"night": range(0, 7), "morning": range(7, 10), "day": range(10, 16), "evening": range(16, 21),
                   "late": range(21, 24)}


def ols_previous_formula(m, rows: pd.DataFrame) -> pd.Series:
    """A frozen least squares' forecast as the code before this freeze's fix computed it, written out on its own: the
    cells, the table's hdh_<s> and cdh_<s> times v1's blocks, the lags, then X @ beta on the rows with every input."""
    s = m.scenario
    cols = {f"cell:{dt}:{h:02d}": ((rows["day_type"] == dt) & (rows["hour"] == h)).astype(float)
            for dt in ("weekday", "saturday", "sunday_holiday") for h in range(24)}
    for name, hours in V1_BLOCKS.items():
        in_block = rows["hour"].isin(list(hours)).astype(float)
        cols[f"heating:{name}"] = rows[f"hdh_{s}"] * in_block
        cols[f"cooling:{name}"] = rows[f"cdh_{s}"] * in_block
    cols["lag24"], cols["lag168"] = rows["lag24"], rows["lag168"]
    X = pd.DataFrame(cols, index=rows.index)[list(m.columns)]
    ok = np.isfinite(X.to_numpy()).all(axis=1)
    out = pd.Series(np.nan, index=rows.index)
    out[ok] = X[ok].to_numpy() @ m.beta
    return out


@pytest.fixture(scope="module")
def frozen_ols(world, oos):
    """The two comparison models, least squares on the forecast and on yesterday's temperature, as the freeze fits
    them."""
    return {key: fz.fit_frozen(world, oos, YEAR, key=key)[0] for key in model.COMPARATORS.values()}


def probe_row(world, hour: int = 6) -> pd.DataFrame:
    """A weekday row at `hour`, 30 heating degree-hours (-12 C) and no cooling ones on both inputs, both lags 30,000
    MW, its forecast known at its origin."""
    row = world[(world["day_type"] == "weekday") & (world["hour"] == hour)].iloc[[0]].copy()
    for s in ("persistence", "forecast"):
        row[f"temp_{s}"], row[f"hdh_{s}"], row[f"cdh_{s}"] = -12.0, 30.0, 0.0
    row["lag24"] = row["lag168"] = 30_000.0
    return row


def test_a_frozen_least_squares_predicts_as_the_previous_formula_on_the_pipeline_s_rows(world, frozen_ols):
    """No number of the chain moves: on every row features.build produces (the design years, rows without an input
    among them), the frozen least squares, built from its stored blocks and base and its degree-hours computed from
    the temperature, forecasts exactly what the previous formula, reading the table's degree-hours, forecast: 0 MW
    apart, missing where it was missing; read back from its file too."""
    for key, m in frozen_ols.items():
        expected = ols_previous_formula(m, world)
        assert expected.notna().sum() > 10_000 and expected.isna().any(), key
        for each in (m, fz.FrozenModel.from_json(m.to_json())):
            got = each.predict(world)["forecast"]
            np.testing.assert_array_equal(got.to_numpy(), expected.to_numpy(), err_msg=key)


@pytest.mark.parametrize("change", ["hour_blocks", "base_c"])
def test_a_frozen_least_squares_forecasts_from_its_file_whatever_the_module_now_says(change, world, frozen_ols,
                                                                                    monkeypatch):
    """Plan section 4.4: what the forecast depends on is in the file and its hash. With the module's hour blocks
    changed after the freeze (hour 6 moved from the morning block to the night block), or its base temperature
    (18 C to 15 C, the table's degree-hours built with it as features.build builds them), each frozen least squares
    forecasts and draws its curves exactly as before, Astra's probe row included, and its file reads back under the
    same hash and forecasts the same. The control: the change is live, the module's own design of the same rows is
    no longer the frozen model's."""
    from valiwatt.forecast import features

    rows = pd.concat([world[world["day"] >= pd.Timestamp("2023-11-01")], probe_row(world)])
    temps = np.linspace(-30.0, 30.0, 13)
    before = {k: (m.predict(rows).to_numpy(), m.partial_effects(temps).to_numpy(), m.to_json())
              for k, m in frozen_ols.items()}
    designs = {k: model.design(rows, m.scenario).to_numpy() for k, m in frozen_ols.items()}
    if change == "hour_blocks":
        monkeypatch.setattr(model, "HOUR_BLOCKS", HOUR_6_AT_NIGHT)
        now = rows
    else:
        monkeypatch.setattr(features, "BASE_C", 15.0)
        now = rows.copy()
        for s in ("persistence", "forecast"):
            now[f"hdh_{s}"] = np.maximum(features.BASE_C - now[f"temp_{s}"], 0.0)
            now[f"cdh_{s}"] = np.maximum(now[f"temp_{s}"] - features.BASE_C, 0.0)
    for key, m in frozen_ols.items():
        live = model.design(now, m.scenario)
        assert list(live.columns) != list(m.columns) or not np.array_equal(live.to_numpy(), designs[key],
                                                                           equal_nan=True), key  # the control
        forecast, curves, text = before[key]
        np.testing.assert_array_equal(m.predict(now).to_numpy(), forecast, err_msg=key)
        np.testing.assert_array_equal(m.partial_effects(temps).to_numpy(), curves, err_msg=key)
        again = fz.FrozenModel.from_json(text)
        assert again.sha256() == m.sha256() and again.to_json() == text, key
        np.testing.assert_array_equal(again.predict(now).to_numpy(), forecast, err_msg=key)


def test_a_frozen_least_squares_stores_and_hashes_its_hour_blocks_and_base(world, frozen_ols, frozen_gam):
    """The file holds the hour blocks (each name and its hours, in column order) and the base temperature, and the
    hash covers both: Astra's probe row (weekday, hour 6, 30 heating degree-hours, both lags 30,000 MW) forecast
    another way with hour 6 at night or another base comes with another hash, never under the same one; a file edited
    in either is refused by its hash. A GAM's file holds neither and its hash is the one without them."""
    m = frozen_ols["forecast"]
    d = json.loads(m.to_json())
    assert d["hour_blocks"] == {k: list(v) for k, v in V1_BLOCKS.items()} and list(d["hour_blocks"]) == list(V1_BLOCKS)
    assert d["base_c"] == 18.0 and m.hour_blocks == tuple((k, tuple(v)) for k, v in V1_BLOCKS.items())
    row = probe_row(world)
    for other in (dataclasses.replace(m, hour_blocks=HOUR_6_AT_NIGHT), dataclasses.replace(m, base_c=15.0)):
        assert other.sha256() != m.sha256()
        assert other.predict(row)["forecast"].iloc[0] != m.predict(row)["forecast"].iloc[0]
    for field, edit in (("hour_blocks", {"night": list(range(7)), "morning": [7, 8, 9]}), ("base_c", 15.0)):
        e = json.loads(m.to_json())
        e[field] = (e[field] | edit) if field == "hour_blocks" else edit
        with pytest.raises(fz.FrozenModelError, match="does not match its own hash"):
            fz.FrozenModel.from_json(json.dumps(e))
    gam, _ = frozen_gam
    g = json.loads(gam.to_json())
    assert "hour_blocks" not in g and "base_c" not in g and gam.hour_blocks is None and gam.base_c is None
    assert _hash_of(g) == g["sha256"] == gam.sha256()  # the GAM's hash is the one without them, as before
    for field, value in (("hour_blocks", V1_BLOCKS), ("base_c", 18.0)):
        with pytest.raises(fz.FrozenModelError, match="a frozen GAM stores no hour blocks"):
            dataclasses.replace(gam, **{field: value})


def test_a_frozen_least_squares_with_blocks_or_a_base_it_cannot_use_is_refused(world, frozen_ols):
    """Hour blocks that do not partition the hours 0 to 23 (an hour in two blocks, an hour in none, an empty block,
    an hour that is not a whole hour, a name twice) or a base temperature that is not a finite number are refused
    when the model is built and when its file is read, a file whose hash was recomputed with them, so the refusal is
    theirs and not the hash's. A file whose columns are not those its stored day types and blocks generate (a block
    renamed in the blocks but not in the columns) loads no forecast: refused when read, and its forecast refused."""
    m = frozen_ols["persistence"]
    ruled = json.loads(m.to_json())

    def rehashed(**changes):
        e = dict(ruled) | changes
        e.pop("sha256")
        e["sha256"] = fz.frozen_sha256(e["family"], e["scenario"], e["columns"], e["knots"], e["degree"],
                                       e["extrapolation"], e["include_bias"], e["center"], e["scale"], e["beta"],
                                       e["intercept"], e["q"], hour_blocks=e["hour_blocks"].items(),
                                       base_c=e["base_c"])
        return json.dumps(e)

    assert fz.FrozenModel.from_json(rehashed()).sha256() == m.sha256()  # the control: the recomputed hash is the hash
    blocks = {k: list(v) for k, v in V1_BLOCKS.items()}
    bad_blocks = {"hour twice": blocks | {"night": list(range(7))},
                  "hour missing": blocks | {"late": [21, 22]},
                  "empty block": blocks | {"extra": []},
                  "not a whole hour": blocks | {"late": [21, 22, 23.0]},
                  "outside the day": blocks | {"late": [21, 22, 23, 24]}}
    for name, value in bad_blocks.items():
        with pytest.raises(fz.FrozenModelError, match="hour block"):
            dataclasses.replace(m, hour_blocks=value)
        with pytest.raises(fz.FrozenModelError, match="hour block"):
            fz.FrozenModel.from_json(rehashed(hour_blocks=value))
    with pytest.raises(fz.FrozenModelError, match="do not partition"):
        dataclasses.replace(m, hour_blocks=[("night", range(0, 6)), ("night", range(6, 24))])
    for value in (float("nan"), float("inf"), "18", True):
        with pytest.raises(fz.FrozenModelError, match="base temperature"):
            dataclasses.replace(m, base_c=value)
    with pytest.raises(fz.FrozenModelError, match="base temperature"):
        fz.FrozenModel.from_json(rehashed(base_c="18"))
    renamed = {("nuit" if k == "night" else k): v for k, v in blocks.items()}
    with pytest.raises(fz.FrozenModelError, match="stored columns are not"):
        fz.FrozenModel.from_json(rehashed(hour_blocks=renamed))
    loose = dataclasses.replace(m, hour_blocks=renamed)
    for draw in (lambda: loose.predict(probe_row(world)), lambda: loose.partial_effects(np.array([0.0]))):
        with pytest.raises(fz.FrozenModelError, match="stored columns are not"):
            draw()


def test_a_row_whose_day_type_or_hour_is_not_known_is_never_forecast(world, frozen_ols, frozen_gam):
    """For both families, fitted or frozen: a row with a missing day type, or an hour outside 0 to 23 (24, -1, 6.5,
    missing), is a row with a missing input, NaN, never forecast with all-zero cells; every other row is forecast as
    before. A day type the stored cells do not cover is refused, never forecast with all-zero cells: least squares
    whose weekday cells were dropped, as the GAM, read back from its file too, and a row of an unknown day type."""
    late = world[world["day"] >= pd.Timestamp("2023-11-01")]
    rows = late[model.usable_rows(model.design(late, "forecast"))
                & model.usable_rows(model.design(late, "persistence"))].copy()
    bad = rows.index[[3, 4, 5, 6, 7]]
    others = ~rows.index.isin(bad)
    odd = rows.astype({"hour": float})
    odd.loc[bad[0], "day_type"] = np.nan
    for t, hour in zip(bad[1:], (24.0, -1.0, 6.5, np.nan)):
        odd.loc[t, "hour"] = hour
    frozen = {**frozen_ols, "gam_forecast": frozen_gam[0]}
    train, _ = wf.split(world, wf.folds(f"{YEAR}-01", f"{YEAR}-01")[0])
    fitted = {"ols": model.OLSForecaster("forecast").fit(train), "gam": model.GAMForecaster("forecast").fit(train)}
    for key, m in frozen.items():
        before, got = m.predict(rows)["forecast"], m.predict(odd)["forecast"]
        assert before[bad].notna().all() and got[bad].isna().all(), key
        # the same rows, one product on fewer of them: equal to the last place or so
        np.testing.assert_allclose(got[others].to_numpy(), before[others].to_numpy(), rtol=1e-12, atol=0, err_msg=key)
    for key, m in fitted.items():
        before, got = m.predict(rows), m.predict(odd)
        assert before[bad].notna().all() and got[bad].isna().all(), key
        np.testing.assert_allclose(got[others].to_numpy(), before[others].to_numpy(), rtol=1e-12, atol=0, err_msg=key)

    m = frozen_ols["forecast"]
    cols = list(m.columns)
    keep = [i for i, c in enumerate(cols) if not c.startswith("cell:weekday:")]
    dropped = dataclasses.replace(m, columns=[cols[i] for i in keep], beta=[m.beta[i] for i in keep])
    assert (rows["day_type"] == "weekday").any()
    for each in (dropped, fz.FrozenModel.from_json(dropped.to_json())):
        with pytest.raises(fz.FrozenModelError, match=r"day type the frozen least squares' stored cells do not "
                                                      r"cover: \['weekday'\]"):
            each.predict(rows)
    unknown = rows.copy()
    unknown.loc[bad[0], "day_type"] = "holiday_eve"
    for key, each in frozen.items():
        with pytest.raises(fz.FrozenModelError, match=r"stored cells do not cover: \['holiday_eve'\]"):
            each.predict(unknown)


def test_an_hour_given_as_text_is_forecast_as_its_number(world, frozen_ols, frozen_gam):
    """The hour that decides whether a row is known (`model.calendar_known`) is the hour its cells, blocks and curves
    are built from (`model.local_hour`): an hour given as text ("6") is hour 6 everywhere, for both families, fitted or
    frozen, forecast exactly as the integer hour; never known yet forecast from its lags alone with all-zero cells.
    Text that is not a whole hour from 0 to 23 ("6.5", "night") is a missing input, NaN."""
    late = world[world["day"] >= pd.Timestamp("2023-11-01")]
    rows = late[model.usable_rows(model.design(late, "forecast"))
                & model.usable_rows(model.design(late, "persistence"))].copy()
    text = rows.astype({"hour": str})
    assert text["hour"].map(type).eq(str).all() and set(text["hour"]) >= {"0", "6", "23"}
    odd = text.copy()
    bad = rows.index[[3, 4]]
    odd.loc[bad[0], "hour"], odd.loc[bad[1], "hour"] = "6.5", "night"
    train, _ = wf.split(world, wf.folds(f"{YEAR}-01", f"{YEAR}-01")[0])
    fitted = {"ols": model.OLSForecaster("forecast").fit(train), "gam": model.GAMForecaster("forecast").fit(train)}
    for key, m in {**frozen_ols, "gam_forecast": frozen_gam[0], **fitted}.items():
        predict = (lambda r, m=m: m.predict(r)["forecast"]) if isinstance(m, fz.FrozenModel) else m.predict
        before = predict(rows)
        assert before.notna().all(), key
        pd.testing.assert_series_equal(predict(text), before, check_exact=True, obj=key)
        got = predict(odd)
        assert got[bad].isna().all() and got.drop(bad).notna().all(), key


def test_the_2024_freeze_half_width_leaves_the_eve_out(world, oos):
    """The 2024 freeze is fitted at the end of the design data, so 2023-12-31 is scored there by the design rows; the
    replay scores it an hour later. Its far-off scores must not calibrate any of the three frozen half-widths."""
    assert (oos.loc[oos["day"] == EVE, "scored_at"] == fz.fit_clock(YEAR)).all()
    for key in KEYS:
        rel = ((oos["target"] - oos[f"pred_{key}"]).abs() / oos[f"pred_{key}"]).to_numpy()
        before_eve = (oos["day"] < EVE).to_numpy()
        expected = conformal_quantile(rel[before_eve])
        assert expected != conformal_quantile(rel), key  # the control: the eve would move it
        m, _ = fz.fit_frozen(world, oos, YEAR, key=key)
        assert m.q == expected and m.calibration_scores == int(before_eve.sum()), key


def test_partial_effects_are_drawn_over_the_training_range_and_marked_outside(world, frozen_gam):
    frozen, pe = frozen_gam
    train = train_rows(world)
    lo, hi = train["temp_forecast"].min(), train["temp_forecast"].max()
    assert (frozen.knots[0], frozen.knots[-1]) == (lo, hi)
    assert list(pe.columns) == ["hour", "temp_c", "effect_mw", "effect_mw_nolag", "in_training_range"]
    temps = np.sort(pe["temp_c"].unique())
    assert temps[0] == np.floor(lo) - fz.PARTIAL_EFFECTS_MARGIN_C and temps[-1] == np.ceil(hi) + fz.PARTIAL_EFFECTS_MARGIN_C
    np.testing.assert_allclose(np.diff(temps), fz.PARTIAL_EFFECTS_STEP_C)
    assert sorted(pe["hour"].unique()) == list(range(24)) and len(pe) == 24 * len(temps)
    pd.testing.assert_series_equal(pe["in_training_range"], (pe["temp_c"] >= lo) & (pe["temp_c"] <= hi),
                                   check_names=False)
    assert pe["in_training_range"].any() and (~pe["in_training_range"]).any()
    at_ref = pe[pe["temp_c"] == 18.0]
    assert len(at_ref) == 24 and np.allclose(at_ref[["effect_mw", "effect_mw_nolag"]], 0, atol=1e-6)
    np.testing.assert_allclose(pe["effect_mw"], frozen.partial_effects(temps)["effect_mw"])
    # The curves without the lags: the ruled pipeline without its two lag columns, on the same rows and knots.
    X = model.gam_design(train, "forecast", np.asarray(frozen.knots))[:, :-2]
    scaler = StandardScaler().fit(X)
    ridge = Ridge(alpha=1.0).fit(scaler.transform(X), train["target"].to_numpy())
    nolag = model.gam_partial_effects(np.asarray(frozen.knots), ridge.coef_, scaler.scale_, temps)
    np.testing.assert_allclose(pe["effect_mw_nolag"], nolag["effect_mw"], rtol=1e-9, atol=1e-6)
    assert not np.allclose(pe["effect_mw"], pe["effect_mw_nolag"])  # the lags carry part of the curve


def log_world(tmp_path, monkeypatch, world, oos, frozen_gam):
    """The world's production model and its two comparators, logged and registered in a throwaway MLflow store."""
    from valiwatt import tracking

    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    frozen, effects = frozen_gam
    comparators = {k: fz.fit_frozen(world, oos, YEAR, key=k)[0] for k in model.COMPARATORS.values()}
    cols = fz.input_columns(frozen.family, frozen.scenario)
    example = world[cols].dropna().head(24).astype({"hour": float})
    run, _ = fz.log_frozen(frozen, fz.coefficient_table(frozen), example, {}, comparators=comparators,
                           extras={"partial_effects.csv": effects})
    return frozen, comparators, run


def test_comparators_are_logged_and_reloaded_against_their_hashes(tmp_path, monkeypatch, world, oos, frozen_gam):
    from valiwatt import tracking

    frozen, comparators, run = log_world(tmp_path, monkeypatch, world, oos, frozen_gam)
    loaded, frozen_run = fz.load_frozen(YEAR)
    assert loaded.sha256() == frozen.sha256() and loaded.family == "gam"
    tags = frozen_run.data.tags
    assert {k: tags[f"comparator_sha256.{k}"] for k in comparators} == {k: m.sha256() for k, m in comparators.items()}
    assert set(comparators) == {"forecast", "persistence"}
    back = fz.load_comparators(frozen_run)
    assert {k: m.sha256() for k, m in back.items()} == {k: m.sha256() for k, m in comparators.items()}
    assert frozen_run.data.params["include_bias"] == "False" and float(frozen_run.data.params["lag24_mw_per_mw"]) > 0
    names = {a.path for a in tracking.client().list_artifacts(run.info.run_id)}
    assert {"coefficients.csv", "partial_effects.csv", "comparators"} <= names

    path = Path(urlparse(frozen_run.info.artifact_uri).path) / "comparators" / "forecast.json"
    written = path.read_text(encoding="utf-8")
    # An edited comparator that still matches its own hash (the file rewritten whole) differs from its run's record.
    edited = dataclasses.replace(comparators["forecast"], beta=comparators["forecast"].beta * 1.001)
    path.write_text(edited.to_json(), encoding="utf-8")
    with pytest.raises(fz.FrozenModelError, match="hash its run recorded"):
        fz.load_comparators(frozen_run)
    # One edited in place no longer matches its own hash.
    d = json.loads(written)
    d["beta"][0] += 1.0
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(fz.FrozenModelError, match="its own hash"):
        fz.load_comparators(frozen_run)
    path.write_text(written, encoding="utf-8")
    assert set(fz.load_comparators(frozen_run)) == set(comparators)  # restored, it loads again
    # A run that records no comparator is refused, not read as having none.
    bare = SimpleNamespace(info=frozen_run.info, data=SimpleNamespace(
        tags={k: v for k, v in tags.items() if not k.startswith("comparator_sha256.")}))
    with pytest.raises(fz.FrozenModelError, match="comparison models are"):
        fz.load_comparators(bare)
    # A comparator frozen for another year: the year is not hashed, so the file matches its own hash and its run's,
    # and only the year refuses it.
    moved = dataclasses.replace(comparators["forecast"], replay_year=YEAR - 1)
    assert moved.sha256() == tags["comparator_sha256.forecast"]
    path.write_text(moved.to_json(), encoding="utf-8")
    with pytest.raises(fz.FrozenModelError, match=f"was frozen for {YEAR - 1}"):
        fz.load_comparators(frozen_run)
    # The other comparison model in forecast.json, its run's tag rewritten to that model's hash: not the model its
    # key names.
    c, run_id, other = tracking.client(), frozen_run.info.run_id, comparators["persistence"]
    path.write_text(other.to_json(), encoding="utf-8")
    c.set_tag(run_id, "comparator_sha256.forecast", other.sha256())
    with pytest.raises(fz.FrozenModelError, match="holds a ols model on persistence"):
        fz.load_comparators(c.get_run(run_id))
    c.set_tag(run_id, "comparator_sha256.forecast", tags["comparator_sha256.forecast"])
    path.write_text(written, encoding="utf-8")
    assert set(fz.load_comparators(c.get_run(run_id))) == set(comparators)  # restored, it loads again
    # A comparator file gone while its run still records its hash.
    (path.parent / "persistence.json").unlink()
    with pytest.raises(fz.FrozenModelError, match="holds files"):
        fz.load_comparators(frozen_run)


def freeze_inputs(tmp_path, monkeypatch, world, oos):
    """`valiwatt freeze`'s inputs replaced by the world's, in a throwaway MLflow store: the frozen table is the world
    only when the runs were loaded for DESIGN_YEARS."""
    from valiwatt import tracking
    from valiwatt.monitor import run as mrun
    from valiwatt.quality import run as qrun

    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    dq = SimpleNamespace(info=SimpleNamespace(run_id="dq"), data=SimpleNamespace(tags={}, params={}))
    val = SimpleNamespace(info=SimpleNamespace(run_id="val"))
    monkeypatch.setattr(vd, "load_inputs", lambda: (dq, None, None))
    monkeypatch.setattr(vd, "load_validation", lambda dq: (val, oos))
    monkeypatch.setattr(mrun, "load_raw", lambda dq: (None, None))
    monkeypatch.setattr(qrun, "load_runs", lambda dq, years=None: "runs" if years == vd.DESIGN_YEARS else None)
    monkeypatch.setattr(fz, "frozen_table", lambda raw, weather, year, runs=None: world if runs == "runs" else None)


def test_the_freeze_refuses_a_production_model_whose_lag_coefficient_is_not_positive(tmp_path, monkeypatch, world,
                                                                                      oos, frozen_gam):
    """`valiwatt freeze` applies the echo premise itself (plan section 4.4): a production GAM fitted with a lag
    coefficient of zero, either lag, is refused before anything is logged. The control: the GAM as fitted reaches the
    logging."""
    frozen, effects = frozen_gam
    freeze_inputs(tmp_path, monkeypatch, world, oos)
    real_fit, production, logged = fz.fit_frozen, {}, []

    def fit(f, oos, replay_year, key="persistence"):
        return (production["model"], effects) if key == fz.PRODUCTION else real_fit(f, oos, replay_year, key=key)

    class Logged(Exception):
        pass

    def log(m, *args, **kwargs):
        logged.append(m.sha256())
        raise Logged

    monkeypatch.setattr(fz, "fit_frozen", fit)
    monkeypatch.setattr(fz, "log_frozen", log)
    production["model"] = frozen
    with pytest.raises(Logged):
        fz.run_freeze(YEAR)
    assert logged == [frozen.sha256()]
    for lag in model.LAGS:
        beta = frozen.beta.copy()
        beta[frozen.columns.index(lag)] = 0.0
        production["model"] = dataclasses.replace(frozen, beta=beta)
        logged.clear()
        with pytest.raises(fz.FrozenModelError, match="echo premise"):
            fz.run_freeze(YEAR)
        assert logged == [], lag


def test_run_freeze_logs_the_production_gam_and_its_two_comparators(tmp_path, monkeypatch, world, oos):
    """`valiwatt freeze` on the world: the production GAM registered with its alias, v1's form on the forecast and v1
    as shipped beside it as hashed files, the partial effects and the coefficients, scikit-learn among the model's
    requirements."""
    import mlflow

    freeze_inputs(tmp_path, monkeypatch, world, oos)
    assert fz.run_freeze(YEAR) == 0
    frozen, run = fz.load_frozen(YEAR)
    assert (frozen.family, frozen.scenario) == ("gam", "forecast")
    assert run.data.tags["validation_run_id"] == "val" and run.data.tags["coefficient_sha256"] == frozen.sha256()
    comparators = fz.load_comparators(run)
    assert {k: (m.family, m.scenario) for k, m in comparators.items()} == {"forecast": ("ols", "forecast"),
                                                                           "persistence": ("ols", "persistence")}
    expected = {k: fz.fit_frozen(world, oos, YEAR, key=k)[0].sha256() for k in comparators}
    assert {k: m.sha256() for k, m in comparators.items()} == expected
    effects = pd.read_csv(mlflow.artifacts.download_artifacts(run_id=run.info.run_id,
                                                              artifact_path="partial_effects.csv"))
    assert list(effects.columns) == ["hour", "temp_c", "effect_mw", "effect_mw_nolag", "in_training_range"]
    coef = pd.read_csv(mlflow.artifacts.download_artifacts(run_id=run.info.run_id, artifact_path="coefficients.csv"))
    assert list(coef["feature"]) == model.gam_columns()
    model_uri = f"models:/{fz.MODEL_NAME}@replay-{YEAR}"
    reqs = Path(mlflow.pyfunc.get_model_dependencies(model_uri)).read_text(encoding="utf-8")
    import sklearn

    assert f"scikit-learn=={sklearn.__version__}" in reqs
