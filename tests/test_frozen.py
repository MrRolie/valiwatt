"""The frozen models (docs/plan-v2.md, sections 4.4 and 4.5): the GAM and its two comparison models as JSON files of
numbers, each refused when it no longer matches its hash or holds a spline setting other than the ruled ones; the
freeze's own knot check; the echo premise; the eve of the replayed year left out of the half-width; the partial
effects drawn over the training range.

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
    # The frozen GAM predicts what the ruled scikit-learn pipeline, fitted on the same rows, predicts.
    train = train_rows(world)
    temps = train["temp_forecast"].to_numpy().reshape(-1, 1)
    from sklearn.preprocessing import SplineTransformer

    spline = SplineTransformer(n_knots=10, degree=3, knots="quantile", extrapolation="linear",
                               include_bias=False).fit(temps)
    np.testing.assert_allclose(spline.bsplines_[0].t[3:-3], frozen.knots, rtol=0, atol=1e-12)
    ok = expected.notna()
    np.testing.assert_allclose(again.predict(rows.loc[ok])["forecast"], fitted.predict(rows.loc[ok]), rtol=1e-9)


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
    assert fz.INPUT_COLUMNS == ["day_type", "hour", "hdh_persistence", "cdh_persistence", "lag24", "lag168"]


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
