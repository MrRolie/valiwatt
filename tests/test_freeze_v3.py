"""v3's frozen models (docs/plan-v3.md, sections 4.4, 7, 11.1, 11.2 and 12): v3 and its three comparison models, each
at its own clock; each noon model's bin edge, half-widths and fallback stored and hashed, v3's term definitions too;
v2's form at midnight frozen at v2's clocks with no interval, its fit v2's; and the freeze's refusals, each seen to
fire with its control: a lag coefficient at or below zero, terms or definitions other than the ruled ones, and a bin
edge that is not the rule on the model's own training rows.

A synthetic world: the design years 2019-10 to 2020, demand that answers the cold and carries a persistent daily level
(so both lags read it), the station, NBM-shaped runs with their wind; out-of-sample rows of 2020 for each model as
v3's validation logs them, far off on 2020-12-31, the eve of the replayed year."""

import dataclasses
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs, synth_runs
from valiwatt import records_v3
from valiwatt.forecast import evaluate as ev
from valiwatt.forecast import freeze as fz
from valiwatt.forecast import freeze_v3 as fv
from valiwatt.forecast import model
from valiwatt.forecast import validate as vd
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import build, scored_at
from valiwatt.forecast.guards import LookaheadError
from valiwatt.forecast.issue import NOON, TERM_DEFINITIONS, TERMS
from valiwatt.forecast.validate_v3 import own_rows
from valiwatt.quality.demand import build_demand

FIRST, LAST = pd.Timestamp("2019-10-01T06:00Z"), pd.Timestamp("2021-01-01T05:00Z")
YEAR, LAST_DAY, EVE = 2021, pd.Timestamp("2020-12-31"), pd.Timestamp("2020-12-31")
H = pd.Timedelta(hours=1)


@pytest.fixture(scope="module")
def world():
    raw = synth_demand(FIRST, LAST, seed=4)
    obs = synth_obs(FIRST, LAST, seed=4).set_index("ts_utc")["temp_c"]
    days = pd.DatetimeIndex(raw["ts_utc"]).tz_convert(None).normalize()
    level = pd.Series(np.cumsum(np.random.default_rng(4).normal(0, 150, len(days.unique()))), index=days.unique())
    raw["mw"] = (raw["mw"] + 300 * np.maximum(18 - obs.reindex(raw["ts_utc"]).to_numpy(), 0)
                 + level.reindex(days).to_numpy())
    runs = synth_runs(FIRST - pd.Timedelta(days=3), LAST, seed=4, wind=True)
    runs = runs[runs["runtime"] + pd.Timedelta(hours=2) <= LAST].reset_index(drop=True)
    design_days = pd.date_range("2019-10-01", "2020-12-31")
    demand = build_demand(raw, FIRST, LAST).frame
    tables = {"noon": build(demand, obs, vd.forecast_inputs(runs, design_days, last=LAST, issue=NOON), issue=NOON),
              "midnight": build(demand, obs, vd.forecast_inputs(runs, design_days, last=LAST))}
    return tables


@pytest.fixture(scope="module")
def oos(world):
    """Out-of-sample rows of 2020 as v3's validation logs them: each model a few percent off, and far off on the eve
    of the replayed year, so whether that day calibrates moves the half-widths."""
    f = world["noon"]
    rows = f[(f["day"] >= pd.Timestamp("2020-01-01")) & (f["day"] <= EVE)]
    out = rows[["target", "day", "temp_forecast"]].copy()
    out["fold"] = out["day"].dt.to_period("M").astype(str)
    out["scored_at"] = scored_at(out["day"], LAST)
    rng = np.random.default_rng(5)
    for i, key in enumerate(fv.FROZEN_KEYS):
        out[f"pred_{key}"] = out["target"] * (1 + rng.normal(0, 0.02 + 0.01 * i, len(out)))
        out.loc[out["day"] == EVE, f"pred_{key}"] = out.loc[out["day"] == EVE, "target"] / 2
    return out


def freeze(world, oos, key, **kwargs):
    return fv.fit_frozen_v3(fv.table_of(key, world), oos, YEAR, key=key, last_day=LAST_DAY, **kwargs)


@pytest.fixture(scope="module")
def frozen(world, oos):
    return {k: freeze(world, oos, k) for k in fv.FROZEN_KEYS}


def own_temps(world, key):
    table = fv.table_of(key, world)
    train, _ = wf.split(table, fv.fold_of(key, YEAR))
    return train.loc[own_rows(key, train), "temp_forecast"].to_numpy(dtype=float)


# ------------------------------------------------------------------------------------------ what each model stores


def test_each_model_is_frozen_at_its_own_clock_on_its_own_table(frozen):
    """Sections 4.2, 11.1 and 11.2: the noon models' fit clock is the eve at 17:00 UTC, v2's form at midnight's the
    first day's 05:00 UTC; both keep v2's embargo (training ends 2020-12-24). The bounds and sensitivities of section
    4.3 are not frozen."""
    assert fv.FROZEN_KEYS == ("v3", "v2_noon", "v1_noon", "v2_midnight")
    assert fv.fold_of("v3", 2025).fit_clock == pd.Timestamp("2024-12-31T17:00Z")
    assert fv.fold_of("v3", 2024).fit_clock == pd.Timestamp("2023-12-31T17:00Z")
    assert fv.fold_of("v2_midnight", 2025).fit_clock == pd.Timestamp("2025-01-01T05:00Z")
    assert fv.fold_of("v2_midnight", 2024).fit_clock == pd.Timestamp("2024-01-01T05:00Z")
    for k, (m, _) in frozen.items():
        assert m.train_last_day == "2020-12-24" and m.replay_year == YEAR, k
    v3m = frozen["v3"][0]
    assert v3m.lags == ("lag48", "lag168") and v3m.terms == TERMS and v3m.term_definitions == TERM_DEFINITIONS
    assert v3m.wind == "forecast" and v3m.required_inputs == model.V3_INPUTS
    assert frozen["v1_noon"][0].family == "ols" and frozen["v1_noon"][0].lags == ("lag48", "lag168")
    with pytest.raises(ValueError, match="validation only"):
        fv.fit_frozen_v3(pd.DataFrame(), pd.DataFrame(), YEAR, key="v3_late")


def test_v2_s_form_at_midnight_is_v2_s_fit_at_v2_s_clock_with_no_interval(world, oos, frozen):
    """Section 4.2 and the ruling that only the noon models get intervals: v2's form at midnight is v2's frozen fit
    (freeze.fit_frozen of v2's production key on the same table), number for number, and stores no interval (q NaN,
    no layer), its file a v2-shaped file otherwise; NaN round-trips the file and the hash."""
    m, _ = frozen["v2_midnight"]
    v2m, _ = fz.fit_frozen(world["midnight"], oos.rename(columns={"pred_v2_midnight": "pred_gam_forecast"}), YEAR,
                           key="gam_forecast")
    for name in ("beta", "knots", "center", "scale", "columns"):
        np.testing.assert_array_equal(np.asarray(getattr(m, name)), np.asarray(getattr(v2m, name)), err_msg=name)
    assert m.intercept == v2m.intercept and m.lags is None and m.terms == () and m.interval is None
    assert np.isnan(m.q) and m.calibration_folds == ()
    text = m.to_json()

    def strict(token):
        raise ValueError(f"{token} is not JSON")

    assert json.loads(text, parse_constant=strict)["q"] is None  # strict JSON: no interval is null, never NaN
    again = fz.FrozenModel.from_json(text)
    assert again.sha256() == m.sha256() and np.isnan(again.q) and again.to_json() == text
    assert set(json.loads(m.to_json())) == set(json.loads(v2m.to_json()))  # no v3 field in its file
    p = m.predict(world["midnight"].iloc[-48:])
    assert list(p.columns) == ["forecast", "lower", "upper"] and p["lower"].isna().all()
    assert p["forecast"].notna().any()


def test_each_noon_model_stores_its_edge_and_its_half_widths_calibrated_at_its_noon_clock(world, oos, frozen):
    """Section 7: the edge is the 5th percentile of the noon feed's temperature over the model's own usable training
    rows; the layer is calibrated on the 12 folds of 2020, the rows scored by the noon clock (2020-12-31 17:00 UTC:
    the eve, scored at 2021-01-01 06:00 UTC, stays out, the day before it is in), every score binned by its own
    temperature against that edge."""
    clock = pd.Timestamp("2020-12-31T17:00Z")
    for k in ("v3", "v2_noon", "v1_noon"):
        m, _ = frozen[k]
        assert m.interval["bin_edge"] == model.bin_edge(own_temps(world, k)), k
        cal = ev.calibration_rows(oos[oos["fold"].isin(fz.calibration_folds(YEAR))], clock)
        assert not (cal["day"] == EVE).any() and (cal["day"] == EVE - pd.Timedelta(days=1)).sum() == 24
        score = (cal["target"] - cal[f"pred_{k}"]).abs() / cal[f"pred_{k}"]
        assert m.interval == ev.check_interval_layer(
            ev.binned_half_widths(score, cal["temp_forecast"], cal["day"], m.interval["bin_edge"])), k
        assert m.q == m.interval["q_pooled"] and m.calibration_scores == len(cal)
        assert m.calibration_folds == tuple(f"2020-{i:02d}" for i in range(1, 13))


def test_a_frozen_v3_model_round_trips_and_its_hash_covers_its_terms_and_interval(world, frozen):
    m, coef = frozen["v3"]
    text = m.to_json()
    again = fz.FrozenModel.from_json(text)
    assert again.sha256() == m.sha256() and again.interval == m.interval and again.terms == m.terms
    rows = world["noon"].iloc[-24 * 10:]
    pd.testing.assert_frame_equal(again.predict(rows), m.predict(rows))
    fitted = model.forecaster("v3", model.MODELS_V3).fit(wf.split(world["noon"], fv.fold_of("v3", YEAR))[0])
    np.testing.assert_allclose(m.forecast(rows), fitted.predict(rows), rtol=0, atol=1e-6)
    d = json.loads(text)
    assert {"lags", "terms", "wind", "term_definitions", "required_inputs", "interval"} <= set(d)
    for field, value in (("interval", m.interval | {"q_cold": m.interval["q_cold"] * 1.01}),
                         ("required_inputs", list(m.required_inputs[:-1]))):
        d2 = dict(d) | {field: value}
        with pytest.raises(fz.FrozenModelError):  # the file no longer matches its hash, or its layer its rule
            fz.FrozenModel.from_json(json.dumps(d2))
    assert set(coef.dropna(subset=["vif"])["feature"]) == {*(model.TERM_PREFIX + t for t in TERMS), "lag48",
                                                           "lag168"}
    assert fv.interval_params(m)["interval.bin_edge"] == m.interval["bin_edge"]
    assert set(k for k in fv.interval_params(m) if k.startswith("term.")) == {f"term.{t}_mw_per_unit" for t in TERMS}


def test_a_comparison_model_at_noon_forecasts_only_v3_s_usable_rows(world, frozen):
    """Section 4.2: v2's and v1's forms at noon never forecast a row v3 drops (a missing lag day's mean), though they
    read no term."""
    rows = world["noon"].iloc[-24 * 10:].copy()
    rows.loc[rows.index[:24], "lagday_observed"] = np.nan
    for k in ("v3", "v2_noon", "v1_noon"):
        p = frozen[k][0].predict(rows)
        assert p["forecast"].iloc[:24].isna().all() and p["forecast"].iloc[24:].notna().all(), k


# --------------------------------------------------------------------------------------------- the refusals


@pytest.mark.parametrize(("cold", "weeks", "fallback"), [(119, 6, True), (120, 3, True), (120, 4, False)])
def test_a_cold_bin_of_119_scores_or_of_120_in_3_weeks_takes_the_pooled_half_width_and_says_so(world, oos, cold,
                                                                                                weeks, fallback):
    """Section 12: a frozen model whose cold bin holds 119 calibration scores, or 120 from 3 Monday-to-Sunday weeks,
    takes the pooled half-width, records it, and every interval it issues says so; 120 from 4 weeks keeps its own.
    v1's form at noon, its edge from its own rows; the calibration rows' noon temperatures set 10 C above it, but
    `cold` of them 10 C below it, those far off."""
    key = "v1_noon"
    edge = model.bin_edge(own_temps(world, key))
    cal = oos.copy()
    cal["temp_forecast"] = edge + 10.0
    mondays = pd.date_range("2020-03-02", periods=weeks, freq="7D")
    chosen = []
    for i in range(cold):
        day = mondays[i % weeks] + pd.Timedelta(days=(i // weeks) % 7)
        chosen.append(cal.index[(cal["day"] == day).to_numpy()][(i // (weeks * 7)) % 24])
    assert len(set(chosen)) == cold and ev.week_of(cal.loc[chosen, "day"]).nunique() == weeks
    cal.loc[chosen, "temp_forecast"] = edge - 10.0
    cal.loc[chosen, f"pred_{key}"] = cal.loc[chosen, "target"] * 0.8
    m, _ = freeze(world, cal, key)
    layer = m.interval
    assert (layer["cold_n"], layer["cold_weeks"], layer["fallback"]) == (cold, weeks, fallback)
    assert (layer["q_cold"] == layer["q_pooled"]) == fallback
    rows = world["noon"].iloc[-48:].copy()
    rows.loc[rows.index[:24], "temp_forecast"] = edge - 5.0
    rows.loc[rows.index[24:], "temp_forecast"] = edge + 5.0
    p = m.predict(rows)
    assert list(p["bin"]) == ["cold"] * 24 + ["rest"] * 24 and (p["fallback"] == fallback).all()
    half = (p["upper"] - p["forecast"]) / p["forecast"]
    np.testing.assert_allclose(half.iloc[:24], layer["q_cold"], rtol=1e-12)
    np.testing.assert_allclose(half.iloc[24:], layer["q_rest"], rtol=1e-12)


@pytest.mark.parametrize("key", ["v3", "v1_noon"])
def test_a_freeze_whose_bin_edge_is_not_the_rule_on_its_own_rows_is_refused(world, oos, monkeypatch, key):
    """Section 12: the fit's edge is placed on its training rows plus the hours of a later month (the replayed one),
    colder than any of them (the noon feed's whole degrees Fahrenheit tie, so a few hours may leave the quantile
    where it is); the freeze's own check, on the rows it takes again from the table, refuses. The control: the ruled
    fit passes it."""
    own = own_temps(world, key)
    later = np.full(24 * 31, own.min() - 5.0)
    assert model.bin_edge(np.append(own, later)) != model.bin_edge(own)
    family = model.GAMForecaster if model.MODELS_V3[key].family == "gam" else model.OLSForecaster
    real_fit, real_edge = family.fit, model.bin_edge

    def leaky_fit(self, table):
        with monkeypatch.context() as m:
            m.setattr(model, "bin_edge", lambda temps: real_edge(np.append(temps, later)))
            return real_fit(self, table)

    monkeypatch.setattr(family, "fit", leaky_fit)
    with pytest.raises(fz.FrozenModelError, match="bin edge is not the 5th-percentile rule") as e:
        freeze(world, oos, key)
    assert isinstance(e.value.__cause__, LookaheadError)
    monkeypatch.undo()
    m, _ = freeze(world, oos, key)  # the control
    assert m.interval["bin_edge"] == model.bin_edge(own)


def test_a_production_model_with_a_lag_coefficient_at_or_below_zero_is_refused(frozen):
    """Section 4.4 (the echo premise, section 9.2): lag48 or lag168 at zero or below, in MW per MW, is refused; both
    positive is accepted."""
    m, _ = frozen["v3"]
    good = m
    for lag in ("lag48", "lag168"):
        i = m.columns.index(lag)
        beta = m.beta.copy()
        beta[i] = abs(beta[i]) or 1.0
        good = dataclasses.replace(good, beta=beta)
    assert set(good.lag_coefficients()) == {"lag48", "lag168"} and all(v > 0 for v in good.lag_coefficients().values())
    fz.require_echo_premise(good)  # the control
    for lag in ("lag48", "lag168"):
        i = good.columns.index(lag)
        for value in (0.0, -abs(good.beta[i])):
            beta = good.beta.copy()
            beta[i] = value
            with pytest.raises(fz.FrozenModelError, match="echo premise"):
                fz.require_echo_premise(dataclasses.replace(good, beta=beta))


def _rehash(d: dict) -> dict:
    """A file's fields with the hash recomputed from them, so a refusal is the field's and not the hash's."""
    m = {k: v for k, v in d.items() if k != "sha256"}
    sha = fz.frozen_sha256(m["family"], m["scenario"], m["columns"], m["knots"], m["degree"], m["extrapolation"],
                           m["include_bias"], m["center"], m["scale"], m["beta"], m["intercept"], m["q"],
                           lags=m.get("lags"), terms=m.get("terms", ()), wind=m.get("wind"),
                           term_definitions=m.get("term_definitions"), required_inputs=m.get("required_inputs"),
                           interval=m.get("interval"))
    return m | {"sha256": sha}


@pytest.mark.parametrize(("name", "value"), [("base_c", 17.0), ("lag_day_min_hours", 19), ("knots_to_kmh", 1.85),
                                             ("lag_day_first_h", -48), ("base_c", True)])
def test_a_term_definition_other_than_the_ruled_one_is_refused(frozen, name, value):
    """Section 4.4 and 12: a frozen model whose stored term definition is not the ruled one is refused, built or read
    from its file under a hash recomputed to match; the ruled ones load (the control)."""
    m, _ = frozen["v3"]
    d = json.loads(m.to_json())
    assert fz.FrozenModel.from_json(json.dumps(_rehash(d))).sha256() == m.sha256()  # the control
    bad = d | {"term_definitions": d["term_definitions"] | {name: value}}
    with pytest.raises(fz.FrozenModelError, match="term definition|term definitions"):
        fz.FrozenModel.from_json(json.dumps(_rehash(bad)))
    with pytest.raises(fz.FrozenModelError, match="term definition|term definitions"):
        dataclasses.replace(m, term_definitions=m.term_definitions | {name: value})
    with pytest.raises(fz.FrozenModelError, match="exactly when it has a term"):
        dataclasses.replace(m, term_definitions=None)


def test_a_production_model_whose_terms_are_not_the_ruled_ones_is_refused(world, frozen):
    """Section 12: a production model missing a term, or holding none, is refused; v3 as fitted passes (the
    control)."""
    fv.require_ruled_terms(frozen["v3"][0])
    train, _ = wf.split(world["noon"], fv.fold_of("v3", YEAR))
    fitted = model.forecaster("v3_no_lag_day", model.MODELS_V3).fit(train)
    two = fz.FrozenModel(columns=fitted.columns, beta=fitted.coef, knots=fitted.knots, center=fitted.center,
                         scale=fitted.scale, intercept=fitted.intercept, q=0.01, replay_year=YEAR,
                         train_last_day="2020-12-24", n_train=fitted.n_train, family="gam", scenario="forecast",
                         lags=fitted.lags, terms=fitted.terms, wind="forecast",
                         term_definitions=fitted.term_definitions)
    for bad in (two, frozen["v2_noon"][0]):
        with pytest.raises(fz.FrozenModelError, match="not the ruled ones"):
            fv.require_ruled_terms(bad)


# ------------------------------------------------------------------------------------------ `valiwatt freeze-v3`


def fake_dq(tags: dict | None = None, **params):
    """v3's data-quality run of the 2019-2024 window, its byte copies checked; tags and params laid over."""
    return SimpleNamespace(info=SimpleNamespace(run_id="dq3"), data=SimpleNamespace(
        tags={"byte_copies": "passed", "fresh_clone": "false"} | (tags or {}), params={"through": "2024"} | params))


def fake_validation(reproduction: str = "passed"):
    return SimpleNamespace(info=SimpleNamespace(run_id="val3"), data=SimpleNamespace(tags={"reproduction":
                                                                                           reproduction}))


LINEAGE: dict = {}


@pytest.fixture
def freeze_inputs(tmp_path, monkeypatch, world, oos):
    """`valiwatt freeze-v3`'s inputs replaced by the world's, in a throwaway MLflow store; v3's validation run is
    `oos`; the replayed year is the world's. The data-quality run latest_passed gives is LINEAGE["dq"], the validation
    run LINEAGE["validation"]; LINEAGE["paths"] the canonical files load_inputs was handed."""
    from valiwatt import firstrun, tracking
    from valiwatt.monitor import run as mrun
    from valiwatt.quality import run as qrun
    from valiwatt.quality import run_v3

    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    monkeypatch.setattr(firstrun, "require_first_run", lambda: None)
    LINEAGE.clear()
    LINEAGE.update(dq=fake_dq(), validation=fake_validation())
    monkeypatch.setattr(run_v3, "latest_passed", lambda: LINEAGE["dq"])
    monkeypatch.setattr(vd, "load_inputs", lambda dq, paths, command: LINEAGE.__setitem__("paths", paths) or (
        dq, None, "weather"))
    monkeypatch.setattr(fv, "load_validation_v3", lambda dq: (LINEAGE["validation"], oos))
    monkeypatch.setattr(mrun, "load_raw", lambda dq: ("raw", None))
    monkeypatch.setattr(qrun, "load_runs", lambda dq, years=None, wind=False: "runs" if years == fv.V3_DESIGN_YEARS
                        and wind else None)  # v3 reads the wind (plan-v3, section 3.1)
    monkeypatch.setattr(fv, "frozen_tables_v3", lambda raw, weather, year, runs: world if runs == "runs" else None)
    monkeypatch.setattr(fv, "REPLAY_YEARS", (YEAR,))
    real = fv.fit_frozen_v3
    monkeypatch.setattr(fv, "fit_frozen_v3", lambda f, oos, year, key: real(f, oos, year, key=key, last_day=LAST_DAY))
    return real


def with_positive_lags(m):
    beta = m.beta.copy()
    for lag in ("lag48", "lag168"):
        beta[m.columns.index(lag)] = abs(beta[m.columns.index(lag)]) or 1.0
    return dataclasses.replace(m, beta=beta)


def test_freeze_v3_registers_v3_under_its_own_name_with_its_comparators(freeze_inputs, monkeypatch, frozen):
    """`valiwatt freeze-v3`: v3 registered as valiwatt-demand-v3 with the alias replay-<year> (v2's registered model
    untouched), its three comparison models beside it, the run tagged v3 and v3-pre-freeze, the interval layer, the
    terms and the VIFs logged."""
    import mlflow

    from valiwatt.quality import run_v3

    real = freeze_inputs
    production = with_positive_lags(frozen["v3"][0])
    monkeypatch.setattr(fv, "fit_frozen_v3", lambda f, oos, year, key: (
        (production, frozen["v3"][1]) if key == "v3" else real(f, oos, year, key=key, last_day=LAST_DAY)))
    assert fv.run_freeze_v3(YEAR) == 0
    got, run = fz.load_frozen(YEAR, model_name=fv.MODEL_NAME_V3)
    assert got.sha256() == production.sha256() and got.interval == production.interval
    t = run.data.tags
    in_force = records_v3.current_freeze()["freeze_tag"]  # v3-pre-freeze before v3's tag (section 10.3)
    assert (t["version"], t["freeze_tag"], t["validation_run_id"], t["dq_run_id"]) == ("v3", in_force, "val3", "dq3")
    assert (t["byte_copies"], t["reproduction"], t["fresh_clone"]) == ("passed", "passed", "false")
    assert LINEAGE["paths"] == run_v3.canonical_paths(2024)
    assert {k.removeprefix("comparator_sha256.") for k in t if k.startswith("comparator_sha256.")} == \
        {"v2_noon", "v1_noon", "v2_midnight"}
    for k in ("v2_noon", "v1_noon", "v2_midnight"):
        text = mlflow.artifacts.load_text(f"runs:/{run.info.run_id}/comparators/{k}.json")
        assert fz.FrozenModel.from_json(text).sha256() == t[f"comparator_sha256.{k}"] == frozen[k][0].sha256()
    p = run.data.params
    assert float(p["interval.bin_edge"]) == pytest.approx(production.interval["bin_edge"])
    assert {"vif.lag48", "vif.lag168", *(f"vif.{t}" for t in TERMS), "lag48_mw_per_mw"} <= set(p)
    assert mlflow.MlflowClient().get_registered_model(fv.MODEL_NAME_V3).name == fv.MODEL_NAME_V3
    with pytest.raises(mlflow.exceptions.MlflowException):
        mlflow.MlflowClient().get_registered_model(fz.MODEL_NAME)  # v2's never registered by v3's freeze


@pytest.mark.parametrize("fault", ["lag48", "lag168", "terms"])
def test_freeze_v3_refuses_before_logging_a_production_model_the_rules_refuse(freeze_inputs, monkeypatch, frozen,
                                                                              fault, world):
    """Section 12: `valiwatt freeze-v3` refuses a production model whose lag coefficient is at zero, or whose terms
    are not the ruled ones, before anything is logged (the control is the test above)."""
    good = with_positive_lags(frozen["v3"][0])
    if fault == "terms":
        bad = dataclasses.replace(good, terms=TERMS[:2], wind=None)
    else:
        beta = good.beta.copy()
        beta[good.columns.index(fault)] = 0.0
        bad = dataclasses.replace(good, beta=beta)
    monkeypatch.setattr(fv, "fit_frozen_v3", lambda f, oos, year, key: (bad, frozen["v3"][1]))
    logged = []
    monkeypatch.setattr(fv, "log_frozen", lambda *a, **k: logged.append(a))
    with pytest.raises(fz.FrozenModelError, match="echo premise|not the ruled ones"):
        fv.run_freeze_v3(YEAR)
    assert logged == []


class Reached(Exception):
    """The freeze went past the step under test."""


@pytest.mark.parametrize("through", ["2025", None])
def test_freeze_v3_reads_a_data_quality_run_of_the_2019_2024_window_only(freeze_inputs, monkeypatch, through):
    """Section 11.2, "a revised 2019-2024 record in the 2025 vintage never changes these models": a data-quality run
    of another window, or one that names none, is refused before any file is read. The control: the run of the
    2019-2024 window reaches the reading of its files."""
    LINEAGE["dq"] = fake_dq(through=through)
    if through is None:
        del LINEAGE["dq"].data.params["through"]
    with pytest.raises(vd.LineageError, match="2019-2024 files only"):
        fv.run_freeze_v3(YEAR)
    assert "paths" not in LINEAGE
    LINEAGE["dq"] = fake_dq()
    monkeypatch.setattr(vd, "load_inputs", lambda dq, paths, command: (_ for _ in ()).throw(Reached(paths)))
    with pytest.raises(Reached):
        fv.run_freeze_v3(YEAR)


def test_freeze_v3_refuses_a_lineage_whose_checks_did_not_run_unless_it_is_a_fresh_clone(freeze_inputs, monkeypatch,
                                                                                         frozen):
    """Section 3.1: a data-quality run whose byte copies were not checked, or a validation run whose reproduction check
    did not pass, is refused before any model is fitted, unless the data-quality run is labelled a fresh clone; the
    freeze run then carries both statuses and the label (the control, with both checks passed, is the registration
    test above)."""
    fitted = []
    monkeypatch.setattr(fv, "fit_frozen_v3", lambda *a, **k: fitted.append(a))
    LINEAGE["dq"] = fake_dq(tags={"byte_copies": "not_run"})
    with pytest.raises(vd.LineageError, match="not labelled a fresh clone"):
        fv.run_freeze_v3(YEAR)
    assert "paths" not in LINEAGE
    LINEAGE.update(dq=fake_dq(), validation=fake_validation("not_run"))
    with pytest.raises(vd.LineageError, match="reproduction check is 'not_run'"):
        fv.run_freeze_v3(YEAR)
    assert fitted == []
    production = with_positive_lags(frozen["v3"][0])
    monkeypatch.setattr(fv, "fit_frozen_v3", lambda f, oos, year, key: (
        (production, frozen["v3"][1]) if key == "v3" else (frozen[key][0], None)))
    LINEAGE["dq"] = fake_dq(tags={"byte_copies": "not_run", "fresh_clone": "true"})
    assert fv.run_freeze_v3(YEAR) == 0
    _, run = fz.load_frozen(YEAR, model_name=fv.MODEL_NAME_V3)
    t = run.data.tags
    assert (t["byte_copies"], t["reproduction"], t["fresh_clone"]) == ("not_run", "not_run", "true")


def test_freeze_v3_refuses_a_fresh_clone_data_quality_run_once_a_read_of_v2_s_store_is_logged(freeze_inputs,
                                                                                               monkeypatch):
    """D11: a read of v2's store in v3's store refuses a freeze on a data-quality run labelled a fresh clone, before any
    file is read or model fitted (the fresh-clone control is the test above)."""
    from valiwatt import v2store

    fitted = []
    monkeypatch.setattr(fv, "fit_frozen_v3", lambda *a, **k: fitted.append(a))
    monkeypatch.setattr(v2store, "recorded", lambda client=None: {"run_id": "read2"})
    LINEAGE["dq"] = fake_dq(tags={"byte_copies": "not_run", "fresh_clone": "true"})
    with pytest.raises(vd.LineageError, match="labelled a fresh clone, but v3's store holds the read of v2's store"):
        fv.run_freeze_v3(YEAR)
    assert "paths" not in LINEAGE and fitted == []


def test_a_freeze_whose_own_rows_taken_again_differ_from_its_fit_s_is_refused(world, oos, monkeypatch):
    """The freeze checks its knots and bin edge on the frozen table's own usable rows taken again: as many as the fit
    used, or refused (the control: the rows as taken, in the tests above)."""
    real = fv.own_rows

    def one_short(key, table):
        rows = real(key, table).copy()
        rows.iloc[int(np.flatnonzero(rows.to_numpy())[0])] = False
        return rows

    monkeypatch.setattr(fv, "own_rows", one_short)
    for key in ("v3", "v2_midnight"):
        with pytest.raises(fz.FrozenModelError, match="own usable rows taken again"):
            freeze(world, oos, key)


@pytest.mark.parametrize("key", ["v3", "v2_midnight"])
def test_a_freeze_whose_knots_are_not_the_rule_on_its_own_rows_is_refused(world, oos, monkeypatch, key):
    """Section 4.4: the fit's own knot check is bypassed and its knots placed on its training rows plus the hours of a
    later month, colder than any of them; the freeze's own check, on the rows it takes again from the table, refuses.
    The control: the ruled fit passes it."""
    own = own_temps(world, key)
    later = np.full(24 * 31, own.min() - 5.0)
    assert not np.allclose(model.knot_rule(np.append(own, later)), model.knot_rule(own), rtol=0, atol=1e-12)
    real_fit = model.GAMForecaster.fit

    def leaky_fit(self, table):
        with monkeypatch.context() as m:
            m.setattr(model, "_place_knots", lambda temps: model.knot_rule(np.append(temps, later)))
            m.setattr(model, "check_knots", lambda knots, temps: None)
            return real_fit(self, table)

    monkeypatch.setattr(model.GAMForecaster, "fit", leaky_fit)
    with pytest.raises(fz.FrozenModelError, match="knots are not the quantile rule") as e:
        freeze(world, oos, key)
    assert isinstance(e.value.__cause__, LookaheadError)
    monkeypatch.undo()
    m, _ = freeze(world, oos, key)  # the control
    np.testing.assert_allclose(m.knots, model.knot_rule(own), rtol=0, atol=1e-12)


@pytest.mark.parametrize("fault", ["lags", "ols_with_terms", "wind", "fallback", "q"])
def test_a_frozen_model_built_with_a_v3_field_its_rules_refuse_is_refused(frozen, fault):
    """Section 4.4: a frozen model is refused at construction when its lags are not demand lags, least squares holds a
    term, its wind is not given exactly when it reads wind with cold, its interval layer does not follow its rule (a
    fallback flag its cold bin's count and weeks contradict), or its half-width q is not the layer's pooled one; each
    with its own message. The control: the model as frozen is rebuilt unchanged."""
    m, _ = frozen["v3"]
    assert dataclasses.replace(m).sha256() == m.sha256()  # the control
    change, match = {
        "lags": ({"lags": ("lag48", "temp_forecast")}, "are not demand-lag columns"),
        "ols_with_terms": ({"family": "ols"}, "least squares reads no term"),
        "wind": ({"wind": None}, "is given exactly when it reads wind with cold"),
        "fallback": ({"interval": m.interval | {"fallback": not m.interval["fallback"]}},
                     "the frozen model's interval layer"),
        "q": ({"q": m.interval["q_pooled"] + 1e-3}, "is not its interval layer's pooled one"),
    }[fault]
    with pytest.raises(fz.FrozenModelError, match=match):
        dataclasses.replace(m, **change)
