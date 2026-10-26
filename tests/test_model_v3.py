"""v3's models (docs/plan-v3.md, section 4): the lags of the noon issue time, the three terms computed from the table's
raw columns, v3's usable rows shared by every noon model, the bin edge of the intervals (section 7) and the term
definitions a frozen model stores; and v2's defaults, which reproduce v2's design exactly."""

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs
from valiwatt.forecast import model
from valiwatt.forecast.features import BASE_C, build
from valiwatt.forecast.guards import LookaheadError
from valiwatt.forecast.issue import MIDNIGHT, NOON, TERM_DEFINITIONS, TERMS
from valiwatt.forecast.model import (
    MODELS_V3,
    NOON_KEYS,
    V3_COMPARATORS,
    V3_INPUTS,
    GAMForecaster,
    ModelSpec,
    OLSForecaster,
    bin_edge,
    check_bin_edge,
    design,
    gam_columns,
    gam_design,
    knot_rule,
    ols_columns,
    require_term_definitions,
    term_columns,
    usable_rows,
    vif,
)
from valiwatt.quality.demand import build_demand

FIRST, LAST = pd.Timestamp("2021-01-01T06:00Z"), pd.Timestamp("2021-07-01T05:00Z")
TRAIN_LAST_DAY = pd.Timestamp("2021-04-30")
H = pd.Timedelta(hours=1)
LAGDAY_MISSING = pd.Timestamp("2021-02-17")  # a day whose lag day's mean is missing (fewer than 20 station hours)


def midnight_table(seed: int = 5) -> pd.DataFrame:
    """v2's table: the station, a forecast beside it known at its origin, a day no run fed."""
    demand = build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame
    f = build(demand, synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"])
    rng = np.random.default_rng(seed)
    f["temp_forecast"] = f["temp_observed"] + rng.normal(0, 1.5, len(f))
    f.loc[f["day"] == pd.Timestamp("2021-03-03"), "temp_forecast"] = np.nan
    f["hdh_forecast"] = np.maximum(BASE_C - f["temp_forecast"], 0.0)
    f["cdh_forecast"] = np.maximum(f["temp_forecast"] - BASE_C, 0.0)
    f["available_forecast"] = f["origin"].where(f["temp_forecast"].notna())
    f["target"] = 15_000 + 300 * f["hdh_forecast"] + 0.1 * f["lag24"] + rng.normal(0, 60, len(f))
    return f


def noon_table(seed: int = 5) -> pd.DataFrame:
    """A table in issue.py's noon column contract, written out here on its own: origin the eve at 17:00 UTC, lag48 and
    lag168 (no lag24, no persistence), the forecast's wind (km/h) and day's mean, the station's lag day's mean (D-2)
    and day's mean; a target that answers the hour's cold, the day's mean cold, the lag day's mean cold and wind with
    cold."""
    f = midnight_table(seed).drop(columns=["lag24", "temp_persistence", "hdh_persistence", "cdh_persistence"])
    rng = np.random.default_rng(seed + 1)
    f["origin"] = f["origin"] - 12 * H
    f["available_forecast"] = f["origin"].where(f["temp_forecast"].notna())
    mw = f["target"]
    f["lag48"] = mw.reindex(f.index - 48 * H).to_numpy()
    f["wind_forecast"] = pd.Series(np.round(rng.uniform(0, 20, len(f))) * 1.852, index=f.index).where(
        f["temp_forecast"].notna())
    f["daymean_forecast"] = f.groupby("day")["temp_forecast"].transform("mean")
    station = f.groupby("day")["temp_observed"].mean()
    f["daymean_observed"] = f["day"].map(station)
    f["lagday_observed"] = (f["day"] - pd.Timedelta(days=2)).map(station)
    hdh = np.maximum(18.0 - f["temp_forecast"], 0.0)
    f["target"] = (15_000 + 250 * hdh + 0.1 * f["lag48"] + 80 * np.maximum(18.0 - f["daymean_forecast"], 0.0)
                   - 40 * np.maximum(18.0 - f["lagday_observed"].fillna(0.0), 0.0) + 3 * f["wind_forecast"] * hdh
                   + rng.normal(0, 60, len(f)))
    f.loc[f["day"] == LAGDAY_MISSING, "lagday_observed"] = np.nan  # its demand observed, its lag day's mean not
    return f


def train_test(f):
    return f[f["day"] <= TRAIN_LAST_DAY], f[f["day"] > TRAIN_LAST_DAY]


# --------------------------------------------------------------------------------------------- v2's defaults


def test_v2_defaults_reproduce_the_design_columns_exactly():
    """Given no lag, term or required input, every design function is v2's: least squares' named columns and the GAM's
    338, in v2's order, ending lag24, lag168, their values array-equal to the ones given v2's lags explicitly; a v2
    forecaster and v3's registry entry for v2's form at midnight fit the same coefficients."""
    f = midnight_table()
    train, test = train_test(f)
    cells = [f"cell:{dt}:{h:02d}" for dt in model.DAY_TYPES for h in range(24)]
    spline = [f"spline:{h:02d}:{k:02d}" for h in range(24) for k in range(11)]
    assert gam_columns() == cells + spline + ["lag24", "lag168"] == gam_columns(lags=model.LAGS, terms=())
    assert ols_columns()[-2:] == ["lag24", "lag168"] and ols_columns() == ols_columns(lags=("lag24", "lag168"))
    pd.testing.assert_frame_equal(design(f, "forecast"), design(f, "forecast", lags=model.LAGS))
    assert list(design(f, "persistence").columns) == ols_columns()
    knots = knot_rule(train.loc[usable_rows(design(train, "forecast"), train["target"]), "temp_forecast"])
    np.testing.assert_array_equal(gam_design(test, "forecast", knots),
                                  gam_design(test, "forecast", knots, lags=("lag24", "lag168"), terms=()))
    v2 = GAMForecaster("forecast").fit(train)
    spec = model.forecaster("v2_midnight", MODELS_V3).fit(train)
    for a in ("knots", "center", "scale", "coef"):
        np.testing.assert_array_equal(getattr(v2, a), getattr(spec, a), err_msg=a)
    assert v2.columns == spec.columns == gam_columns() and v2.intercept == spec.intercept
    assert (v2.bin_edge, v2.term_definitions, spec.bin_edge, spec.term_definitions) == (None, None, None, None)
    np.testing.assert_array_equal(v2.predict(test).to_numpy(), spec.predict(test).to_numpy())
    assert v2.lag_coefficients() == model.gam_lag_coefficients(v2.coef, v2.scale)  # by name and by position agree
    ols = OLSForecaster("forecast").fit(train)
    np.testing.assert_array_equal(ols.beta, model.forecaster("forecast").fit(train).beta)
    assert ols.bin_edge is None and ols.columns == ols_columns()


def test_v2_s_registry_is_unchanged():
    assert model.PRODUCTION == "gam_forecast" and model.COMPARATORS == {"family": "forecast", "shipped": "persistence"}
    assert all(isinstance(v, tuple) and len(v) == 2 for v in model.MODELS.values())
    assert MODELS_V3["v2_midnight"] == ModelSpec("gam", "forecast") and MODELS_V3["v2_midnight"].lags == model.LAGS


# --------------------------------------------------------------------------------------------- v3's design


def test_v3_s_design_is_v2_s_with_lag48_and_the_three_terms_before_the_lags():
    """341 columns: the cells and each hour's curve where v2 has them, then the three terms in issue.TERMS' order, then
    lag48 and lag168; each term is its formula on the table's raw columns with base 18 C, NaN where an input is."""
    f = noon_table()
    train, test = train_test(f)
    names = gam_columns(lags=NOON.lag_columns, terms=TERMS)
    assert len(names) == 341 and names[:-5] == gam_columns()[:-2]
    assert names[-5:] == ["term:day_mean_cold", "term:lag_day_mean_cold", "term:wind_cold", "lag48", "lag168"]
    m = model.forecaster("v3", MODELS_V3).fit(train)
    assert m.columns == names and m.lags == ("lag48", "lag168") and m.terms == TERMS
    X = gam_design(test, "forecast", m.knots, lags=NOON.lag_columns, terms=TERMS, wind="forecast")
    base = TERM_DEFINITIONS["base_c"]
    assert base == 18.0
    expected = {"term:day_mean_cold": np.maximum(base - test["daymean_forecast"], 0.0),
                "term:lag_day_mean_cold": np.maximum(base - test["lagday_observed"], 0.0),
                "term:wind_cold": test["wind_forecast"] * np.maximum(base - test["temp_forecast"], 0.0),
                "lag48": test["lag48"], "lag168": test["lag168"]}
    for name, values in expected.items():
        np.testing.assert_array_equal(X[:, names.index(name)], values.to_numpy(dtype=float), err_msg=name)
    np.testing.assert_array_equal(X[:, :-5], gam_design(test, "forecast", m.knots, lags=()))  # v2's cells and curves
    lagday = gam_design(f, "forecast", m.knots, lags=NOON.lag_columns, terms=TERMS)[:, -4]
    assert names[-4] == "term:lag_day_mean_cold"
    assert np.isnan(lagday[(f["day"] == LAGDAY_MISSING).to_numpy()]).all()
    assert np.isfinite(lagday[(f["day"] == LAGDAY_MISSING + pd.Timedelta(days=1)).to_numpy()]).all()
    # The curves sit where v2's do: the partial effects read columns 72 to 335, which are the spline columns.
    first = len(model.DAY_TYPES) * 24
    assert all(c.startswith("spline:") for c in names[first:first + 24 * 11])


def test_the_terms_read_the_wind_scenario_given():
    """The observed-weather bound keeps the NBM wind (section 4.3): its wind with cold reads wind_forecast times the
    station's cold, its day's mean cold the station's day's mean."""
    f = noon_table()
    t = term_columns(f, TERMS, "observed", wind="forecast")
    np.testing.assert_array_equal(t["wind_cold"], f["wind_forecast"] * np.maximum(18.0 - f["temp_observed"], 0.0))
    np.testing.assert_array_equal(t["day_mean_cold"], np.maximum(18.0 - f["daymean_observed"], 0.0))
    assert MODELS_V3["v3_observed"].inputs == ("temp_observed", "daymean_observed", "lagday_observed",
                                               "wind_forecast", "lag48", "lag168")
    with pytest.raises(ValueError):
        term_columns(f, ("wind_chill",), "forecast")


def test_every_noon_model_fits_and_forecasts_v3_s_usable_rows():
    """A day whose lag day's mean is missing is dropped from training and not forecast by any noon model, v2's and
    v1's forms included, which read no term; v3's three noon models train on the same rows."""
    f = noon_table()
    f.loc[f["day"] == TRAIN_LAST_DAY + pd.Timedelta(days=20), "lagday_observed"] = np.nan  # a test day too
    train, test = train_test(f)
    fitted = {k: model.forecaster(k, MODELS_V3).fit(train) for k in (*NOON_KEYS, "v3_no_lag_day")}
    usable = model.required_known(train, V3_INPUTS) & np.isfinite(train["target"].to_numpy())
    assert len({m.n_train for m in fitted.values()}) == 1 and fitted["v3"].n_train == int(usable.sum())
    assert train.loc[train["day"] == LAGDAY_MISSING, "lagday_observed"].isna().all()
    for key, m in fitted.items():
        pred = m.predict(f)
        assert pred[f["day"].isin([LAGDAY_MISSING, TRAIN_LAST_DAY + pd.Timedelta(days=20)])].isna().all(), key
        assert (pred.notna().to_numpy() == model.required_known(f, V3_INPUTS)).all(), key
    # Without the required inputs, v2's form at noon would have trained on the day (control).
    loose = GAMForecaster("forecast", lags=NOON.lag_columns).fit(train)
    assert loose.n_train > fitted["v2_noon"].n_train


def test_no_lag_of_24_hours_enters_a_noon_model():
    f = noon_table()
    train, _ = train_test(f)
    assert "lag24" not in f
    for key, spec in MODELS_V3.items():
        assert spec.lags == (("lag24", "lag168") if spec.issue == MIDNIGHT else ("lag48", "lag168")), key
        if spec.issue == NOON:
            assert "lag24" not in spec.inputs and "lag24" not in (spec.required_inputs or ()), key
    m = model.forecaster("v1_noon", MODELS_V3).fit(train)
    assert m.columns[-2:] == ["lag48", "lag168"] and "lag24" not in m.columns
    with pytest.raises(KeyError):
        model.forecaster("v2_midnight", MODELS_V3).fit(train)  # v2's form at midnight reads lag24: not on this table


def test_a_specs_inputs_hold_its_required_inputs_so_a_comparator_masks_the_same_rows():
    """ModelSpec.inputs is what the model reads, then its required inputs (docs/plan-v3.md, sections 4.2 and 5): v2's
    and v1's forms at noon and each term left out list v3's wind, day's mean and lag day's mean though they read none
    of them, so a frozen comparator built from them keeps the fit's row mask; v2's form at midnight is unchanged."""
    v3_only = ("wind_forecast", "daymean_forecast", "lagday_observed")
    assert MODELS_V3["v2_noon"].inputs == ("temp_forecast", "lag48", "lag168", *v3_only)
    assert MODELS_V3["v1_noon"].inputs == ("temp_forecast", "lag48", "lag168", *v3_only)
    for key in ("v3", "v3_no_day_mean", "v3_no_lag_day", "v3_no_wind"):
        assert set(MODELS_V3[key].inputs) == set(V3_INPUTS) and len(MODELS_V3[key].inputs) == len(V3_INPUTS), key
    assert MODELS_V3["v3_no_wind"].inputs[:4] == ("temp_forecast", "daymean_forecast", "lagday_observed", "lag48")
    assert MODELS_V3["v2_midnight"].inputs == ("temp_forecast", "lag24", "lag168")
    f = noon_table()
    train, _ = train_test(f)
    for key in (*NOON_KEYS, "v3_no_day_mean", "v3_no_lag_day", "v3_no_wind"):
        rows = model.required_known(train, MODELS_V3[key].inputs) & train["target"].notna().to_numpy()
        assert model.forecaster(key, MODELS_V3).fit(train).n_train == int(rows.sum()), key


def test_lag_coefficients_by_position_read_only_v2_s_columns():
    """Without the columns, the last two coefficients are read as lag24 and lag168 only on v2's 338 columns; v3's 341
    are refused, and given its columns it gets lag48 and lag168 by name."""
    f = noon_table()
    train, _ = train_test(f)
    m = model.forecaster("v3", MODELS_V3).fit(train)
    assert len(m.coef) == 341 and len(gam_columns()) == 338
    with pytest.raises(ValueError, match="give its columns"):
        model.gam_lag_coefficients(m.coef, m.scale)
    by_name = model.gam_lag_coefficients(m.coef, m.scale, m.columns)
    assert list(by_name) == ["lag48", "lag168"] == m.columns[-2:]
    assert by_name["lag48"] == float(m.coef[-2] / m.scale[-2]) and by_name == m.lag_coefficients()
    v2 = GAMForecaster("forecast").fit(midnight_table()[lambda t: t["day"] <= TRAIN_LAST_DAY])
    assert model.gam_lag_coefficients(v2.coef, v2.scale) == model.gam_lag_coefficients(v2.coef, v2.scale, v2.columns)


def test_the_registry_holds_v3_its_comparators_and_its_sensitivities():
    assert NOON_KEYS == ("v3", "v2_noon", "v1_noon") and model.V3_PRODUCTION == "v3"
    assert V3_COMPARATORS == {"terms": "v2_noon", "family": "v1_noon", "horizon": "v2_midnight"}
    assert set(MODELS_V3) == {"v3", "v2_noon", "v1_noon", "v2_midnight", "v3_no_day_mean", "v3_no_lag_day",
                              "v3_no_wind", "v3_late", "v3_observed"}
    v3 = MODELS_V3["v3"]
    assert (v3.family, v3.scenario, v3.wind, v3.terms, v3.interval, v3.issue) == ("gam", "forecast", "forecast", TERMS,
                                                                                  "binned", NOON)
    assert V3_INPUTS == ("temp_forecast", "wind_forecast", "daymean_forecast", "lagday_observed", "lag48", "lag168")
    for key in ("v2_noon", "v1_noon"):
        assert MODELS_V3[key].terms == () and MODELS_V3[key].required_inputs == V3_INPUTS
        assert MODELS_V3[key].interval == "binned"
    assert MODELS_V3["v1_noon"].family == "ols"
    for key, left_out in (("v3_no_day_mean", "day_mean_cold"), ("v3_no_lag_day", "lag_day_mean_cold"),
                          ("v3_no_wind", "wind_cold")):
        spec = MODELS_V3[key]
        assert spec.terms == tuple(t for t in TERMS if t != left_out) and spec.required_inputs == V3_INPUTS, key
    assert MODELS_V3["v3_late"].scenario == MODELS_V3["v3_late"].wind == "forecast_late"
    assert (MODELS_V3["v3_observed"].scenario, MODELS_V3["v3_observed"].wind) == ("observed", "forecast")
    for key, spec in MODELS_V3.items():
        m = model.forecaster(key, MODELS_V3)
        assert isinstance(m, GAMForecaster if spec.family == "gam" else OLSForecaster) and m.scenario == spec.scenario
        assert m.binned == (spec.interval == "binned") and tuple(m.lags) == spec.lags
    with pytest.raises(ValueError):
        model.forecaster("v3")  # not one of v2's keys
    for bad in (dict(terms=("wind_cold", "day_mean_cold"), wind="forecast"), dict(family="ols", terms=TERMS),
                dict(terms=("day_mean_cold",), wind="forecast"), dict(terms=TERMS), dict(interval="by_season"),
                dict(family="quantile")):
        with pytest.raises(ValueError):
            ModelSpec(**({"family": "gam", "scenario": "forecast"} | bad))


# --------------------------------------------------------------------------------------- coefficients and VIF


def test_term_and_lag_coefficients_are_unscaled_and_named():
    """Each term's coefficient is MW per unit of the term: one more degree of the lag day's cold moves the forecast by
    it; each lag's is MW per MW, read by name (lag48 is not where v2's lag24 was)."""
    f = noon_table()
    train, test = train_test(f)
    m = model.forecaster("v3", MODELS_V3).fit(train)
    terms, lags = m.term_coefficients(), m.lag_coefficients()
    assert list(terms) == list(TERMS) and list(lags) == ["lag48", "lag168"]
    i = m.columns.index("lag48")
    assert lags["lag48"] == m.coef[i] / m.scale[i] and i == len(m.columns) - 2
    rows = test[model.required_known(test, V3_INPUTS) & (test["lagday_observed"] < 10)].head(5)
    colder = rows.assign(lagday_observed=rows["lagday_observed"] - 1.0)
    np.testing.assert_allclose((m.predict(colder) - m.predict(rows)).to_numpy(), terms["lag_day_mean_cold"], rtol=1e-6)
    later = rows.assign(lag48=rows["lag48"] + 1.0)
    np.testing.assert_allclose((m.predict(later) - m.predict(rows)).to_numpy(), lags["lag48"], rtol=1e-6)
    assert model.gam_term_coefficients(m.coef, m.scale, gam_columns()) == {}


def test_vif_is_one_for_an_unrelated_column_and_large_for_a_planted_collinearity():
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=5000), rng.normal(size=5000)
    c = a + 0.1 * rng.normal(size=5000)  # nearly a
    v = vif(np.column_stack([a, b, c]), ["a", "b", "c"], ["a", "b", "c"])
    assert v["b"] == pytest.approx(1.0, abs=0.01) and v["a"] > 50 and v["c"] > 50
    assert v["a"] == pytest.approx(1 + 1 / 0.01, rel=0.1)
    exact = vif(np.column_stack([a, b, a + b]), ["a", "b", "s"], ["s"])
    assert exact["s"] > 1e10 or exact["s"] == float("inf")
    assert np.isnan(vif(np.column_stack([a, np.ones(5000)]), ["a", "k"], ["k"])["k"])
    with pytest.raises(ValueError):
        vif(np.column_stack([a, np.r_[np.nan, b[1:]]]), ["a", "b"], ["a"])


def test_a_fitted_v3_reports_the_vif_of_its_terms_and_lags_on_its_own_rows():
    f = noon_table()
    train, _ = train_test(f)
    m = model.forecaster("v3", MODELS_V3).fit(train)
    v = m.vif(train)
    assert list(v) == [*TERMS, "lag48", "lag168"] and all(np.isfinite(x) and x >= 1.0 for x in v.values())
    with pytest.raises(ValueError, match="fitted on"):
        m.vif(f)  # more rows than the fit's own


# -------------------------------------------------------------------------------------------- the bin edge


def test_a_binned_fit_stores_the_rule_on_its_own_usable_rows():
    """The bin edge is the 5th percentile of temp_forecast over the fit's own usable training rows, as the knots are
    their rule: a row without a lag day's mean is not among them, and changing its temperature leaves the edge as it
    was."""
    f = noon_table()
    train, test = train_test(f)
    for key in NOON_KEYS:
        m = model.forecaster(key, MODELS_V3).fit(train)
        own = train.loc[model.required_known(train, V3_INPUTS) & train["target"].notna(), "temp_forecast"]
        assert m.bin_edge == bin_edge(own) == float(np.quantile(own, 0.05)), key
        check_bin_edge(m.bin_edge, own.to_numpy())
    edge = model.forecaster("v3", MODELS_V3).fit(train).bin_edge
    moved = f.copy()
    moved.loc[moved["day"] == LAGDAY_MISSING, "temp_forecast"] = -60.0  # never usable: no lag day's mean
    assert LAGDAY_MISSING <= TRAIN_LAST_DAY and (train_test(moved)[0]["temp_forecast"] == -60.0).sum() == 24
    assert model.forecaster("v3", MODELS_V3).fit(train_test(moved)[0]).bin_edge == edge
    usable = moved.copy()  # the control: a usable training row's temperature moves the edge
    usable.loc[usable["day"] == LAGDAY_MISSING - pd.Timedelta(days=5), "temp_forecast"] = -60.0
    assert model.forecaster("v3", MODELS_V3).fit(train_test(usable)[0]).bin_edge != edge
    assert GAMForecaster("forecast").fit(midnight_table()).bin_edge is None  # single: v2's


def test_the_bin_edge_check_refuses_an_edge_that_is_not_the_rule_on_its_rows():
    """The freeze's check (section 12): an edge off the rule by 1.1e-12 C either way, or placed on one row more than
    the fit's own (a temperature issued after its clock), is refused; the rule itself, or 0.9e-12 C off it, passes."""
    small = np.array([10.0, -3.0, 4.0, 0.0, 7.0, np.nan])  # by hand: sorted -3, 0, 4, 7, 10; 0.05 * 4 = 0.2 of the
    check_bin_edge(-2.4, small)  # way from -3 to 0, so -3 + 0.2 * 3 = -2.4
    for wrong in (-3.0, -2.4 + 1e-9, 0.0):
        with pytest.raises(LookaheadError, match="bin edge"):
            check_bin_edge(wrong, small)
    rng = np.random.default_rng(1)
    own = rng.normal(-2, 9, 3000)
    edge = bin_edge(own)
    assert edge == float(np.quantile(own, model.COLD_QUANTILE)) and model.COLD_QUANTILE == 0.05
    check_bin_edge(edge, own)
    check_bin_edge(edge, np.r_[own, np.nan])  # the rule reads the finite values only
    for sign in (1, -1):
        check_bin_edge(edge + sign * 0.9e-12, own)
        with pytest.raises(LookaheadError, match="bin edge"):
            check_bin_edge(edge + sign * 1.1e-12, own)
    with pytest.raises(LookaheadError, match="bin edge"):
        check_bin_edge(bin_edge(np.r_[own, -40.0]), own)
    with pytest.raises(LookaheadError):
        check_bin_edge(float("nan"), own)
    with pytest.raises(ValueError):
        bin_edge(np.array([np.nan]))


# ---------------------------------------------------------------------------------------- term definitions


def test_term_definitions_other_than_the_ruled_ones_are_refused():
    """What a term means (base 18 C, D-2's labels, the 20-hour rule, the knot conversion) is stored with the model and
    checked: any other value, a missing or extra key, or a boolean for a number is refused."""
    assert require_term_definitions(dict(TERM_DEFINITIONS)) == TERM_DEFINITIONS
    assert TERM_DEFINITIONS == {"base_c": 18.0, "lag_day_first_h": -47, "lag_day_last_h": -24, "lag_day_min_hours": 20,
                                "knots_to_kmh": 1.852}
    for k, v in (("base_c", 17.5), ("lag_day_first_h", -48), ("lag_day_last_h", -23), ("lag_day_min_hours", 19),
                 ("knots_to_kmh", 1.85), ("base_c", True), ("base_c", "18"), ("base_c", float("nan"))):
        with pytest.raises(ValueError, match="term definition"):
            require_term_definitions(TERM_DEFINITIONS | {k: v})
    for bad in ({k: v for k, v in TERM_DEFINITIONS.items() if k != "base_c"}, TERM_DEFINITIONS | {"extra": 1}, None):
        with pytest.raises(ValueError, match="term definitions"):
            require_term_definitions(bad)
    m = model.forecaster("v3", MODELS_V3).fit(train_test(noon_table())[0])
    assert m.term_definitions == TERM_DEFINITIONS
    assert model.forecaster("v2_noon", MODELS_V3).term_definitions is None


def test_a_wind_input_not_known_at_its_origin_is_refused():
    """The observed-weather bound reads the station's temperature (no availability) and the NBM wind: a row whose run
    became available after its origin is refused before anything is fitted."""
    f = noon_table()
    train, _ = train_test(f)
    model.forecaster("v3_observed", MODELS_V3).fit(train)  # the control
    late = train.copy()
    late.loc[late.index[500], "available_forecast"] = late.loc[late.index[500], "origin"] + H
    with pytest.raises(LookaheadError, match="wind on forecast"):
        model.forecaster("v3_observed", MODELS_V3).fit(late)
    with pytest.raises(LookaheadError, match="the GAM fit on forecast"):
        model.forecaster("v3", MODELS_V3).fit(late)
