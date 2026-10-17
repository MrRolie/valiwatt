"""Validation end to end on synthetic design years: it refuses 2024 rows and logs per-season thresholds; v2 fits every
model, the production model sets the intervals, thresholds and drift levels, and the forecast input is read from its
availability, from the design years' run files only."""

import inspect
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs, synth_runs
from valiwatt.config import DESIGN_LAST, IN_SAMPLE_LABEL, stations
from valiwatt.forecast import evaluate as ev
from valiwatt.forecast import validate as vd
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import build, day_origin
from valiwatt.forecast.guards import DESIGN_LAST_DAY, LookaheadError
from valiwatt.forecast.model import MODELS
from valiwatt.monitor.replay import day_verdicts
from valiwatt.quality.checks import jsonable
from valiwatt.quality.demand import build_demand
from valiwatt.quality.weather import BOUNDS_C

FIRST, LAST = pd.Timestamp("2021-01-01T06:00Z"), pd.Timestamp("2024-01-01T05:00Z")


@pytest.fixture(scope="module")
def raw_world():
    raw = synth_demand(FIRST, LAST)
    obs_raw = synth_obs(FIRST, LAST)
    obs = obs_raw.set_index("ts_utc")["temp_c"]
    # Demand responds to cold, so the model has something to learn.
    raw["mw"] = raw["mw"] + 300 * np.maximum(18 - obs.reindex(raw["ts_utc"]).to_numpy(), 0)
    return raw, obs_raw


@pytest.fixture(scope="module")
def tables(raw_world):
    raw, obs_raw = raw_world
    obs = obs_raw.set_index("ts_utc")["temp_c"]
    demand = build_demand(raw, FIRST, LAST).frame
    return build(demand, obs), build(demand, obs + 0.5)


@pytest.fixture(scope="module")
def verdicts(raw_world):
    raw, obs_raw = raw_world
    return day_verdicts(raw, obs_raw, pd.Timestamp("2021-01-01"), DESIGN_LAST_DAY)


def test_validation_runs_and_logs_thresholds_for_every_season(tables, verdicts):
    fp, fpanel = tables
    failed = verdicts | {pd.Timestamp("2023-03-15"): [{"kind": "missing_hour",
                                                       "utc": pd.Timestamp("2023-03-15T15:00Z")}]}
    v = vd.run_validation(fp, fpanel, n_boot=50, first_fold="2022-01", last_fold="2023-12", day_verdicts=failed)
    r = v["results"]
    # The week holding the failed day, and the one-day first week of 2023, are set aside, with why.
    assert {w["week"]: w["why"] for w in r["thresholds"]["2024"]["excluded_weeks"]} == {
        "2022-12-26/2023-01-01": ["partial_week", "too_few_hours"], "2023-03-13/2023-03-19": ["data_quality"]}
    assert r["folds"]["count"] == 24 and r["conformal"]["first_fold"] == "2023-01"
    assert set(r["thresholds"]) == {"2024"}  # intervals start in 2023 here, so only the 2024 replay has thresholds
    assert set(r["thresholds"]["2024"]["performance"]) == {"winter", "spring", "summer", "fall"}
    assert r["compare"]["persistence_vs_baseline"]["difference"] < 0
    assert 0.8 < r["coverage"]["relative"]["overall"]["mean"] < 0.97
    assert r["cases"]["shift_2020"]["evaluable"] is False  # no 2020 in these folds: recorded, never a silent blank


def test_validation_refuses_a_2024_row(tables, verdicts):
    fp, fpanel = tables
    leaky = pd.concat([fp, fp.iloc[[-1]].assign(day=pd.Timestamp("2024-01-05"))])
    with pytest.raises(LookaheadError):
        vd.run_validation(leaky, fpanel, n_boot=10, first_fold="2022-01", last_fold="2023-12", day_verdicts=verdicts)


def test_each_fold_trains_on_the_table_seen_at_its_fit_clock(tables, verdicts):
    """run_validation hands each fold's fit clock to the as-of table providers, and trains on what they return."""
    fp, fpanel = tables
    asked = {"primary": [], "panel": []}

    def at(name, f):
        def table(clock):
            asked[name].append(clock)
            return f[f.index <= clock]
        return table

    v = vd.run_validation(fp, fpanel, n_boot=10, first_fold="2022-01", last_fold="2023-02",
                          train_primary=at("primary", fp), train_panel=at("panel", fpanel), day_verdicts=verdicts)
    clocks = [fold.fit_clock for fold in wf.folds("2022-01", "2023-02")]
    assert asked["primary"] == clocks and asked["panel"] == clocks
    static = vd.run_validation(fp, fpanel, n_boot=10, first_fold="2022-01", last_fold="2023-02",
                               day_verdicts=verdicts)
    pd.testing.assert_frame_equal(v["out"], static["out"])  # no repair in this world: the same rows either way


class Reached(Exception):
    pass


def reached(name):
    def f(*a, **k):
        raise Reached(name)
    return f


def fake_dq(**params):
    from types import SimpleNamespace

    return SimpleNamespace(info=SimpleNamespace(run_id="dq"), data=SimpleNamespace(tags={}, params=params))


def test_raw_demand_other_than_the_checked_file_is_refused(monkeypatch):
    """Validation and the frozen-model fit read the raw records through the replay's loader (monitor/run.py
    load_raw), which refuses a Hydro-Québec file other than the one the data-quality run checked."""
    from valiwatt.forecast import freeze as fz
    from valiwatt.quality import run as qrun
    from valiwatt.sources import hydroquebec

    manifests = [{"climate_id": "1", "months": {"2023-01": {"sha256": "m" * 64}}}]
    monkeypatch.setattr(qrun, "_verify_raw", lambda: ({"sha256": "h" * 64}, manifests))
    monkeypatch.setattr(hydroquebec, "load_raw", reached("hydroquebec.load_raw"))
    monkeypatch.setattr(vd, "load_validation", lambda dq: (None, None))
    params = {"hq_sha256": "h" * 64, "eccc_manifests_sha256": qrun.eccc_manifests_sha256(manifests)}
    entries = {"validate": lambda: vd.run_validate(n_boot=1), "freeze": lambda: fz.run_freeze(2023)}
    # The control: the checked file is read. Another file is refused before it is read.
    for hq, refused in (("h" * 64, Reached), ("x" * 64, vd.LineageError)):
        dq = fake_dq(**(params | {"hq_sha256": hq}))
        monkeypatch.setattr(vd, "load_inputs", lambda dq=dq: (dq, None, None))
        for name, entry in entries.items():
            with pytest.raises(refused, match="Hydro-Québec|hydroquebec"):
                entry()


def test_validate_and_freeze_run_on_the_as_of_tables(monkeypatch):
    """`valiwatt validate` runs run_validation on exactly what validation_inputs builds from the checked raw records,
    as-of training tables included; `valiwatt freeze` fits on frozen_table's."""
    from valiwatt.forecast import freeze as fz
    from valiwatt.monitor import run as mrun

    from valiwatt.quality import run as qrun

    weather, raw, raw_t, oos, runs = object(), object(), object(), object(), object()
    monkeypatch.setattr(vd, "load_inputs", lambda: (fake_dq(), None, weather))
    monkeypatch.setattr(vd, "load_validation", lambda dq: (None, oos))
    monkeypatch.setattr(mrun, "load_raw", lambda dq: (raw, raw_t))
    monkeypatch.setattr(qrun, "load_runs", lambda dq, years=None: runs if years == vd.DESIGN_YEARS else None)
    built = {"f_primary": object(), "f_panel": object(), "train_primary": object(), "train_panel": object(),
             "day_verdicts": object(), "forecast_record": object()}
    calls = {}

    def inputs(*a, **k):
        calls["inputs"] = a, k
        return dict(built)

    def validation(*a, **k):
        calls["validation"] = a, k
        raise Reached("run_validation")

    monkeypatch.setattr(vd, "validation_inputs", inputs)
    monkeypatch.setattr(vd, "run_validation", validation)
    with pytest.raises(Reached):
        vd.run_validate(n_boot=7)
    assert calls["inputs"] == ((raw, raw_t, weather), {"runs": runs})
    a, k = calls["validation"]
    assert a == () and k == built | {"n_boot": 7, "models": tuple(MODELS)}

    table = object()

    def frozen_table(*a, **k):
        calls["frozen_table"] = a, k
        return table

    def fit(*a, **k):
        calls["fit"] = a, k
        raise Reached("fit_frozen")

    monkeypatch.setattr(fz, "frozen_table", frozen_table)
    monkeypatch.setattr(fz, "fit_frozen", fit)
    with pytest.raises(Reached):
        fz.run_freeze(2023)
    assert calls["frozen_table"] == ((raw, weather, 2023), {"runs": runs})
    assert calls["fit"] == ((table, oos, 2023), {"key": "gam_forecast"})  # the production model first


def test_mondrian_intervals_calibrate_within_each_group():
    from valiwatt.forecast.evaluate import relative_conformal, season_of
    from valiwatt.forecast.features import scored_at

    idx = pd.date_range("2021-01-01T06:00Z", periods=24 * 720, freq="h")
    day = (idx - pd.Timedelta(hours=6)).tz_convert(None).normalize()
    rng = np.random.default_rng(0)
    winter = pd.Series(day).dt.month.isin([12, 1, 2]).to_numpy()
    pred = np.full(len(idx), 20_000.0)
    target = pred * (1 + rng.normal(0, np.where(winter, 0.08, 0.02)))
    out = pd.DataFrame({"target": target, "pred": pred, "day": day,
                        "fold": pd.Series(day).dt.to_period("M").astype(str).to_numpy()}, index=idx)
    out["scored_at"] = scored_at(out["day"], idx.max())
    pooled = relative_conformal(out, "pred")
    by_season = relative_conformal(out, "pred", groups=season_of(out["day"]))
    w = out.loc[pooled.index, "day"].dt.month.isin([12, 1, 2])
    assert pooled.loc[w.to_numpy(), "covered"].mean() < 0.8  # one pooled quantile under-covers the noisy season
    assert 0.85 < by_season.loc[w.to_numpy(), "covered"].mean() < 0.95


def test_pseudo_replay_thresholds_read_no_week_of_the_year_they_judge(tables, verdicts):
    """Thresholds for replaying year Y come from validation weeks before Y only."""
    fp, fpanel = tables
    v = vd.run_validation(fp, fpanel, n_boot=10, first_fold="2021-06", last_fold="2023-12", day_verdicts=verdicts)
    th = v["results"]["thresholds"]
    assert set(th) == {"2023", "2024"}
    weeks = pd.DataFrame(th["2023"]["weekly_validation"])
    assert pd.to_datetime(weeks["first_day"]).max() < pd.Timestamp("2023-01-01")
    assert th["2023"]["last_year"] == 2022 and th["2024"]["last_year"] == 2023


def design_errors():
    """Hourly errors, coverage, temperatures and forecasts on synthetic design years 2019-2023, and day verdicts that
    pass every day."""
    days = pd.Series(pd.date_range("2019-01-01", "2023-12-31", freq="D").repeat(24))
    idx = pd.date_range("2019-01-01T06:00Z", periods=len(days), freq="h")
    days.index = idx
    rng = np.random.default_rng(3)
    doy = days.dt.dayofyear.to_numpy()
    temp = pd.Series(5 - 15 * np.cos(2 * np.pi * doy / 365.25) + rng.normal(0, 3, len(idx)), index=idx)
    fc = pd.Series(20_000 + 300 * np.maximum(18 - temp, 0) + rng.normal(0, 200, len(idx)), index=idx)
    err = pd.DataFrame({"day": days, "ape": rng.uniform(0, 0.08, len(idx)), "rel_bias": rng.normal(0, 0.03, len(idx)),
                        "year": days.dt.year, "week": days.dt.to_period("W-SUN").astype(str)}, index=idx)
    covered = pd.Series(rng.uniform(0, 1, len(idx)) < 0.9, index=idx).astype(float)
    clean = {d: [] for d in pd.date_range("2019-01-01", "2023-12-31")}
    return err, covered, temp, days, fc, clean


def test_a_week_holding_a_day_the_replay_would_fail_does_not_calibrate_the_thresholds():
    """The thresholds come from the weeks the replay would judge. Three winter weeks whose errors are extreme, each
    holding one day that fails the replay's day-level data quality, set the winter MAPE threshold when those days are
    ignored; with the days' verdicts the weeks are set aside, with why, and the threshold is what the other weeks
    give."""
    err, covered, temp, days, fc, clean = design_errors()
    mondays = [pd.Timestamp(d) for d in ("2022-01-10", "2022-01-24", "2022-02-07")]
    for m in mondays:
        err.loc[((days >= m) & (days < m + pd.Timedelta(days=7))).to_numpy(), "ape"] = 0.5
    args = (err, covered, temp, days, fc, days, 2021, 2022)
    ignored = vd.thresholds(*args, clean)
    failed = clean | {m + pd.Timedelta(days=2): [{"kind": "missing_hour", "utc": m + pd.Timedelta(days=2, hours=15)}]
                      for m in mondays}
    judged = vd.thresholds(*args, failed)
    assert ignored["performance"]["winter"]["mape_high"] > 0.3  # the control: the extreme weeks calibrate
    assert judged["performance"]["winter"]["weeks"] == ignored["performance"]["winter"]["weeks"] - 3
    assert judged["performance"]["winter"]["mape_high"] < 0.1
    out = [w for w in judged["excluded_weeks"] if w["week"] == "2022-01-10/2022-01-16"]
    assert out == [{"week": "2022-01-10/2022-01-16", "first_day": pd.Timestamp("2022-01-10"), "why": ["data_quality"],
                    "days": 7, "hours": 168,
                    "failed_days": [{"day": pd.Timestamp("2022-01-12"), "problems": ["missing_hour"]}]}]
    assert {w["week"] for w in judged["excluded_weeks"] if w["why"] == ["data_quality"]} == \
        {f"{m.date()}/{(m + pd.Timedelta(days=6)).date()}" for m in mondays}
    assert not {w["week"] for w in judged["weekly_validation"]} & {w["week"] for w in judged["excluded_weeks"]}


def test_weeks_cut_by_the_calibration_years_are_set_aside_and_a_day_without_a_verdict_is_refused():
    """A week straddling New Year has fewer than seven days in the years that calibrate, so the replay would not judge
    it whole: it is set aside even with enough hours. A day with no verdict leaves the population unknown: refused."""
    err, covered, temp, days, fc, clean = design_errors()
    th = vd.thresholds(err, covered, temp, days, fc, days, 2021, 2022, clean)
    why = {w["week"]: (w["why"], w["days"]) for w in th["excluded_weeks"]}
    assert why["2022-12-26/2023-01-01"] == (["partial_week"], 6)  # 144 scored hours: enough, but not whole
    assert why["2020-12-28/2021-01-03"] == (["partial_week", "too_few_hours"], 3)
    assert set(why) == {"2022-12-26/2023-01-01", "2020-12-28/2021-01-03"}
    starts = pd.to_datetime([w["first_day"] for w in th["weekly_validation"]])
    assert (starts.dayofweek == 0).all()  # every week that calibrates starts on its Monday, inside the years
    hole = {d: v for d, v in clean.items() if d != pd.Timestamp("2022-06-15")}
    with pytest.raises(vd.LineageError, match="2022-06-15"):
        vd.thresholds(err, covered, temp, days, fc, days, 2021, 2022, hole)


def test_drift_thresholds_for_a_replay_ignore_the_replayed_year():
    """Changing every temperature and forecast of 2023 moves the 2024 replay's drift alarm levels, never the 2023
    pseudo-replay's."""
    err, covered, temp, days, fc, clean = design_errors()
    late = (days >= pd.Timestamp("2023-01-01")).to_numpy()
    base = {y: vd.thresholds(err, covered, temp, days, fc, days, 2021, y, clean) for y in (2022, 2023)}
    moved = {y: vd.thresholds(err, covered, temp.where(~late, temp + 15), days, fc.where(~late, fc * 1.3), days, 2021,
                              y, clean) for y in (2022, 2023)}
    assert moved[2022]["drift"] == base[2022]["drift"]
    assert moved[2023]["drift"]["temperature_high"] != base[2023]["drift"]["temperature_high"]


# ------------------------------------------------------------------------------------------------------------- v2

# What BASE's v1 validation (commit f3b7f0a, before v2's validation existed) gives on the `tables` world, folds
# 2022-01 to 2023-12, 50 resamples: models=None must keep giving it.
V1_AT_BASE = {
    ("performance", "persistence", "overall", "mean"): 0.00373674165583444,
    ("performance", "persistence", "overall", "ci95_low"): 0.0036450433747429755,
    ("coverage", "relative", "overall", "mean"): 0.8988584474885845,
    ("coverage", "relative_by_season", "mean_half_width_pct"): 0.78130907534895,
    ("compare", "persistence_vs_baseline", "difference"): -0.010072050744608593,
    ("compare", "panel_vs_primary", "difference"): 8.923622794854033e-06,
    ("thresholds", "2024", "performance", "winter", "mape_high"): 0.0036378399556773145,
    ("thresholds", "2024", "drift", "temperature_high"): 0.20459005651505976,
    ("thresholds", "2024", "drift", "forecast_high"): 0.14150030092839277,
}
# The panel-vs-primary difference is of two MAPEs near 3.7e-3: a few units in their last place (CI's runner) are 2e-8
# of it, so it is pinned to 1e-9 of the MAPE it is taken from, not of itself.
V1_TOLERANCE = {("compare", "panel_vs_primary", "difference"):
                {"rel": 0, "abs": 1e-9 * V1_AT_BASE[("performance", "persistence", "overall", "mean")]}}
V1_KEYS = {
    (): ["cases", "compare", "config", "conformal", "coverage", "folds", "performance", "thresholds"],
    ("performance",): ["baseline", "observed", "panel_persistence", "persistence"],
    ("coverage",): ["absolute", "observed_relative", "relative", "relative_by_season"],
    ("cases",): ["peak", "shift_2020", "shift_2020_observed"],
    ("config",): ["base_c", "hour_blocks", "n_boot", "temperature_source"],
}
V1_OOS = ["target", "fold", "day", "scored_at", "hour", "day_type", "baseline", "pred_observed", "n_train_observed",
          "pred_persistence", "n_train_persistence"]


def at(d, path):
    for k in path:
        d = d[k]
    return d


def test_models_none_is_v1_validation_exactly(tables, verdicts):
    """The control: without models, validation is v1's, key for key and number for number as BASE gave it: v1's least
    squares, persistence the production model, the drift monitor on the station."""
    fp, fpanel = tables
    v = vd.run_validation(fp, fpanel, n_boot=50, first_fold="2022-01", last_fold="2023-12", day_verdicts=verdicts)
    r = jsonable(v["results"])
    assert list(v) == ["results", "out", "intervals"]
    for path, keys in V1_KEYS.items():
        assert sorted(at(r, path)) == keys, path
    assert list(v["out"].columns) == V1_OOS
    pd.testing.assert_frame_equal(v["intervals"], ev.relative_conformal(v["out"], "pred_persistence"))
    for path, value in V1_AT_BASE.items():
        assert at(r, path) == pytest.approx(value, **V1_TOLERANCE.get(path, {"rel": 1e-9, "abs": 0})), path
    expected = vd.thresholds(ev.errors(v["out"], "pred_persistence"), v["intervals"]["covered"], fp["temp_observed"],
                             fp["day"], v["out"]["pred_persistence"], v["out"]["day"], 2023, 2023, verdicts)
    assert r["thresholds"]["2024"] == jsonable(expected)


V2_FIRST, V2_LAST = pd.Timestamp("2019-01-01T06:00Z"), pd.Timestamp("2021-07-01T05:00Z")
V2_FOLDS = ("2020-01", "2021-06")
UNFED = pd.Timestamp("2020-02-12")  # a day no run fed
GAP = pd.date_range("2020-05-10T12:00Z", periods=6, freq="h")  # station hours missing: v1's input lacks them a day later


def without_runs_for(runs: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    """The runs that could feed `day` removed: those available by its origin whose points reach its last hour."""
    o = day_origin(day)
    gone = (runs["runtime"] >= o + pd.Timedelta(hours=25 - 72)) & (runs["runtime"] <= o - pd.Timedelta(hours=2))
    return runs[~gone].reset_index(drop=True)


@pytest.fixture(scope="module")
def v2():
    """v2's validation on a synthetic world of 2019 to mid-2021, every model in every fold of 2020-01..2021-06; one day
    no run fed, and six station hours missing (so v1 as shipped lacks them a day later, while the forecast models
    do not)."""
    raw = synth_demand(V2_FIRST, V2_LAST, seed=2)
    obs = synth_obs(V2_FIRST, V2_LAST, seed=2).set_index("ts_utc")["temp_c"]
    raw["mw"] = raw["mw"] + 300 * np.maximum(18 - obs.reindex(raw["ts_utc"]).to_numpy(), 0)
    obs.loc[GAP] = np.nan
    runs = synth_runs(V2_FIRST - pd.Timedelta(days=3), V2_LAST, seed=2)
    runs = without_runs_for(runs[runs["runtime"] + pd.Timedelta(hours=2) <= V2_LAST], UNFED)
    # The winter before the first fold forecast colder than the later ones: the hours before the folds weigh on cold_c.
    runs.loc[runs["ftime"] < pd.Timestamp("2019-03-01T00:00Z"), "tmp_f"] -= 9.0
    forecasts = vd.forecast_inputs(runs, pd.date_range("2019-01-01", "2021-06-30"), last=V2_LAST)
    demand = build_demand(raw, V2_FIRST, V2_LAST).frame
    fp, fpanel = build(demand, obs, forecasts), build(demand, obs + 0.5)
    verdicts = {d: [] for d in pd.date_range("2019-01-01", "2021-12-31")}
    record = {k: x.record for k, x in forecasts.items()}
    v = vd.run_validation(fp, fpanel, n_boot=50, first_fold=V2_FOLDS[0], last_fold=V2_FOLDS[1], day_verdicts=verdicts,
                          models=tuple(MODELS), forecast_record=record)
    return SimpleNamespace(fp=fp, fpanel=fpanel, verdicts=verdicts, record=record, v=v, r=v["results"], out=v["out"])


def production_thresholds(v2, key="gam_forecast", temp="temp_forecast", covered=None):
    out = v2.out
    covered = v2.v["intervals"]["covered"] if covered is None else covered
    return vd.thresholds(ev.errors(out, f"pred_{key}"), covered, v2.fp[temp], v2.fp["day"], out[f"pred_{key}"],
                         out["day"], 2021, 2021, v2.verdicts)


def test_validation_fits_every_model_and_thresholds_read_the_production_model(v2):
    out, r = v2.out, v2.r
    assert r["production"] == "gam_forecast" and r["config"]["production"] == "gam_forecast"
    assert all(f"pred_{k}" in out and f"n_train_{k}" in out for k in MODELS)
    assert {"temp_forecast", "available_forecast", "temp_observed", "temp_persistence"} <= set(out.columns)
    assert set(r["performance"]) == set(MODELS) | {"baseline", "panel_persistence"}
    assert set(r["coverage"]) == {"relative", "absolute", "relative_by_season", "observed_relative",
                                  "gam_forecast", "persistence", "gam_observed", "observed"}
    for k, forms in vd.INTERVAL_FORMS.items():
        assert set(r["coverage"][k]) == set(forms), k
    assert r["coverage"]["relative"] is r["coverage"]["persistence"]["relative"]  # v1's flat keys: v1's model
    iv = ev.relative_conformal(out, "pred_gam_forecast")
    pd.testing.assert_frame_equal(v2.v["intervals"], iv)
    pd.testing.assert_frame_equal(v2.v["intervals_persistence"], ev.relative_conformal(out, "pred_persistence"))
    assert r["coverage"]["gam_forecast"]["relative"]["overall"]["mean"] == pytest.approx(iv["covered"].mean())
    expected = production_thresholds(v2)
    assert set(r["thresholds"]) == {"2022"} and jsonable(r["thresholds"]["2022"]) == jsonable(expected)
    v1s = production_thresholds(v2, "persistence", covered=v2.v["intervals_persistence"]["covered"])
    assert jsonable(v1s["performance"]) != jsonable(expected["performance"])  # the control: the model matters
    for case, key in (("shift_2020", "gam_forecast"), ("shift_2020_persistence", "persistence"),
                      ("shift_2020_observed", "observed")):
        assert jsonable(r["cases"][case]) == jsonable(vd.shift_case(ev.errors(out, f"pred_{key}"), 50)), case
    assert r["cases"]["shift_2020"]["evaluable"] is True
    assert r["folds"]["train_rows_last_fold"] == out["n_train_gam_forecast"].iloc[-1]
    for row in r["cases"]["peak"]["hours"]:
        assert {f"pred_{k}" for k in MODELS} | {"temp_forecast", "temp_c"} <= set(row)
        t = row["utc"]
        assert jsonable([row["lower"], row["upper"]]) == jsonable([iv["lower"].get(t), iv["upper"].get(t)])
    # v2 fits every model, and needs its forecast record to say why the production model lacks an hour.
    args = dict(n_boot=10, first_fold="2020-01", last_fold="2021-06", day_verdicts=v2.verdicts)
    with pytest.raises(ValueError, match="forecast record"):
        vd.run_validation(v2.fp, v2.fpanel, models=tuple(MODELS), **args)
    with pytest.raises(ValueError, match="every model"):
        vd.run_validation(v2.fp, v2.fpanel, models=("gam_forecast", "persistence"), forecast_record=v2.record, **args)


def test_drift_temperature_thresholds_read_the_forecast_input(v2):
    """The temperature drift monitor watches the model's own input, the issued NBM forecast; the forecast drift, the
    production model's forecasts. Alarm levels computed on the station's observations, or on v1's forecasts, differ."""
    drift = v2.r["thresholds"]["2022"]["drift"]
    on_forecast = production_thresholds(v2)["drift"]
    on_station = production_thresholds(v2, temp="temp_observed")["drift"]
    assert drift["temperature_high"] == on_forecast["temperature_high"]
    assert drift["temperature_high"] != on_station["temperature_high"]
    assert drift["forecast_high"] == on_forecast["forecast_high"]
    v1s = production_thresholds(v2, "persistence", covered=v2.v["intervals_persistence"]["covered"])["drift"]
    assert drift["forecast_high"] != v1s["forecast_high"]


def test_a_design_temperature_outside_the_bounds_is_blank_as_in_the_replay():
    """Design code reads the station through the replay's bounds filter: an impossible primary temperature is blank in
    the design table as in the replay's, and one at another station never enters the panel mean."""
    from valiwatt.monitor import replay as rp

    first, last = pd.Timestamp("2021-01-01T06:00Z"), pd.Timestamp("2021-01-21T05:00Z")
    obs = synth_obs(first, last)
    idx = pd.DatetimeIndex(obs["ts_utc"])
    weather = pd.DataFrame({f"{s.key}.temp_c": obs["temp_c"].to_numpy() + 0.3 * i for i, s in enumerate(stations())},
                           index=idx)
    primary = next(s for s in stations() if s.primary)
    other = next(s for s in stations() if not s.primary)
    t1, t2 = idx[100], idx[200]
    weather.loc[t1, f"{primary.key}.temp_c"] = BOUNDS_C[1] + 54
    weather.loc[t2, f"{other.key}.temp_c"] = BOUNDS_C[0] - 10
    src = vd.temperature_sources(weather)
    assert np.isnan(src["primary"][t1]) and src["primary"].drop(t1).notna().all()
    in_bounds = weather.loc[t2].drop(f"{other.key}.temp_c")
    assert weather.loc[t2].mean() != pytest.approx(in_bounds.mean())  # the control: the value would move the mean
    assert src["panel"][t2] == pytest.approx(in_bounds.mean())
    raw = synth_demand(first, last)
    f = build(build_demand(raw, first, last).frame, src["primary"])
    raw_t = obs.assign(temp_c=weather[f"{primary.key}.temp_c"].to_numpy())
    _, replayed = rp.canonical(raw, raw_t, last, last)
    pd.testing.assert_series_equal(f["temp_observed"], replayed.reindex(f.index), check_names=False)
    assert np.isnan(f.loc[t1, "temp_observed"]) and np.isnan(f.loc[t1 + pd.Timedelta(hours=24), "temp_persistence"])
    panel = build(build_demand(raw, first, last).frame, src["panel"])
    assert panel.loc[t2, "temp_observed"] == pytest.approx(in_bounds.mean())


def edge_runs() -> pd.DataFrame:
    """Runs at 01, 07, 13 and 19 UTC from 2023-12-25 to 2024-01-01 19:00 UTC."""
    return synth_runs("2023-12-25", "2024-01-01")


EDGE_DAYS = pd.date_range("2023-12-28", "2023-12-31")


def test_a_forecast_run_available_after_the_design_window_is_refused():
    runs = edge_runs()
    with pytest.raises(LookaheadError, match="available after"):
        vd.forecast_inputs(runs, EDGE_DAYS)
    in_file = runs[runs["runtime"] <= pd.Timestamp("2024-01-01T01:00Z")]
    last_run = in_file[in_file["runtime"] == in_file["runtime"].max()]
    for runtime, refused in (("2024-01-01T03:00:01Z", True), ("2024-01-01T03:00Z", False)):
        extra = pd.concat([in_file, last_run.assign(runtime=pd.Timestamp(runtime))], ignore_index=True)
        if refused:
            with pytest.raises(LookaheadError, match="available after"):
                vd.forecast_inputs(extra, EDGE_DAYS)
        else:  # available at 05:00 UTC, the window's last hour: design information
            vd.forecast_inputs(extra, EDGE_DAYS)
    # An availability recorded later than the run time implies is read as recorded.
    late = in_file.assign(available_utc=in_file["runtime"] + pd.Timedelta(hours=2))
    late.loc[late.index[-1], "available_utc"] = DESIGN_LAST + pd.Timedelta(minutes=1)
    with pytest.raises(LookaheadError, match="available after"):
        vd.forecast_inputs(late, EDGE_DAYS)


def test_the_2024_boundary_run_in_the_2023_file_is_kept_when_available_in_time():
    """The control: the file of 2023 ends with the 2024-01-01 01:00 UTC run, available at 03:00, before the end of the
    design window; it is kept, and 2023-12-31 is forecast from its own 01:00 UTC run, which reads its 2024-01-01
    06:00 UTC point (information time, not valid time)."""
    in_file = edge_runs()
    in_file = in_file[in_file["runtime"] <= pd.Timestamp("2024-01-01T01:00Z")]
    assert in_file["runtime"].max() == pd.Timestamp("2024-01-01T01:00Z")
    fc = vd.forecast_inputs(in_file, EDGE_DAYS)
    eve = fc["forecast"].record.loc[pd.Timestamp("2023-12-31")]
    assert (eve["runtime"], eve["rank"], eve["reason"]) == (pd.Timestamp("2023-12-31T01:00Z"), 0.0, "")
    assert eve["available_utc"] == pd.Timestamp("2023-12-31T03:00Z")
    hours = pd.date_range("2023-12-31T06:00Z", DESIGN_LAST, freq="h")
    assert fc["forecast"].hourly.reindex(hours).notna().all()
    assert (fc["forecast_late"].record["reason"] == "").all()
    assert not (fc["forecast"].record["runtime"] == pd.Timestamp("2024-01-01T01:00Z")).any()


def test_design_code_reads_only_the_design_years_run_files(monkeypatch):
    """`valiwatt validate` and `valiwatt freeze` read the NBM runs through the data-quality run's loader for the
    design years only: the years asked of the file layer are recorded, and asking for 2024 fails."""
    from valiwatt.forecast import freeze as fz
    from valiwatt.monitor import run as mrun
    from valiwatt.quality import run as qrun
    from valiwatt.sources import nbm

    manifest = {"years": {str(y): {"sha256": f"{y}"} for y in range(2019, 2025)}}
    opened = []

    def files(what):
        def f(years):
            years = [int(y) for y in years]
            opened.append((what, years))
            if 2024 in years:
                raise AssertionError(f"the 2024 NBM file was asked for ({what})")
            return nbm.for_years(manifest, years) if what == "verify" else synth_runs("2019-01-01", "2019-01-03")
        return f

    monkeypatch.setattr(nbm, "manifest", lambda: manifest)
    monkeypatch.setattr(nbm, "verify_files", files("verify"))
    monkeypatch.setattr(nbm, "load_runs", files("load"))
    dq = fake_dq(nbm_manifest_sha256=nbm.manifest_sha256(manifest), through="2024")
    monkeypatch.setattr(vd, "load_inputs", lambda: (dq, None, None))
    monkeypatch.setattr(vd, "load_validation", lambda dq: (None, None))
    monkeypatch.setattr(mrun, "load_raw", lambda dq: (None, None))
    monkeypatch.setattr(vd, "validation_inputs", reached("validation_inputs"))
    monkeypatch.setattr(fz, "frozen_table", reached("frozen_table"))
    for name, entry in (("validation_inputs", lambda: vd.run_validate(n_boot=1)),
                        ("frozen_table", lambda: fz.run_freeze(2024))):
        with pytest.raises(Reached, match=name):
            entry()
    assert opened == [("verify", list(vd.DESIGN_YEARS)), ("load", list(vd.DESIGN_YEARS))] * 2
    with pytest.raises(AssertionError, match="2024"):  # the control: every year the run checked includes 2024
        qrun.load_runs(dq)


def test_a_day_no_run_fed_has_no_production_forecast_and_is_listed(v2):
    out, r = v2.out, v2.r
    hours = out.index[(out["day"] == UNFED).to_numpy()]
    assert len(hours) == 24
    assert out.loc[hours, ["pred_gam_forecast", "pred_forecast", "pred_gam_forecast_late"]].isna().all().all()
    assert out.loc[hours, "pred_persistence"].notna().all()  # v1 as shipped reads no forecast
    assert out.loc[hours, ["temp_forecast", "available_forecast"]].isna().all().all()
    assert r["forecast_input"]["days_not_fed"] == [{"day": UNFED, "reason": "no_covering_run"}]
    assert r["forecast_input"]["fed_days"] == r["forecast_input"]["days"] - 1
    assert v2.v["forecast_record"].loc[UNFED, "reason"] == "no_covering_run"
    after = GAP + pd.Timedelta(hours=24)
    lack = r["common_hours"]
    assert lack["gam_forecast"] == lack["forecast"] == {"no_covering_run": 24}
    assert lack["persistence"] == {"temp_persistence": len(GAP)} and lack["no_target"] == 0
    assert lack["hours"] == r["contrasts"]["hours"] == len(out) - 24 - len(after)
    # Every contrast is scored on the common hours, never on its own pair's.
    for name in ("shipped", "family", "input"):
        assert r["contrasts"][name]["hours"] == lack["hours"], name
    pairwise = ev.paired_difference(ev.errors(out, "pred_gam_forecast"), ev.errors(out, "pred_forecast"), n_boot=10)
    assert pairwise["hours"] == lack["hours"] + len(after)  # the control: the family's own pair has more hours


def test_cold_c_comes_from_design_forecast_temperatures_only(v2):
    """cold_c is the 5th percentile of the forecast temperature of every design hour that has one, the hours before
    the first fold included: changing every target leaves it unchanged."""
    fp, r = v2.fp, v2.r
    design = fp.loc[fp["day"] <= DESIGN_LAST_DAY, "temp_forecast"]
    expected = float(np.quantile(design.dropna(), ev.COLD_QUANTILE))
    assert r["config"]["cold_c"] == expected and r["contrasts"]["cold"]["threshold_c"] == expected
    assert vd.design_cold_c(fp.assign(target=fp["target"] * 3 + 1000)) == expected
    # The control: the population matters, so a threshold on the out-of-sample hours or on 2020 onward differs.
    assert ev.cold_threshold(design.reindex(v2.out.index)) != expected
    assert ev.cold_threshold(design[(fp["day"] >= pd.Timestamp("2020-01-01")).to_numpy()]) != expected


def test_the_2020_2023_contrasts_are_labelled_in_sample_for_the_choice(v2):
    out, r = v2.out, v2.r
    assert IN_SAMPLE_LABEL == "in_sample_for_choice"
    assert r["contrasts"]["label"] == r["verdict"]["label"] == IN_SAMPLE_LABEL
    frame = out[["target", "day", "pred_gam_forecast", "pred_forecast", "pred_persistence"]].assign(
        temp_forecast=v2.fp["temp_forecast"].reindex(out.index))
    c = ev.contrasts(frame, r["config"]["cold_c"], n_boot=50)
    for k in ("hours", "weeks", "shipped", "family", "input", "cold", "additive_check"):
        assert jsonable(r["contrasts"][k]) == jsonable(c[k]), k
    assert {k: x for k, x in r["verdict"].items() if k != "label"} == ev.verdict(c)
    assert all(pd.Timestamp(p["utc"]) in out.index for p in r["contrasts"]["peak"])  # out-of-sample hours only
    assert r["sensitivity"]["availability_6h"] == ev.paired_difference(
        ev.errors(out, "pred_gam_forecast_late"), ev.errors(out, "pred_gam_forecast"), n_boot=50)


def test_the_forecast_hours_outside_each_fold_s_training_range_are_counted(v2):
    from valiwatt.forecast.model import design, usable_rows

    out, r = v2.out, v2.r
    rows = r["extrapolation"]
    assert [x["fold"] for x in rows] == [str(m) for m in pd.period_range(*V2_FOLDS, freq="M")]
    for x, fold in zip(rows, wf.folds(*V2_FOLDS), strict=True):
        train, _ = wf.split(v2.fp, fold)
        t = train.loc[usable_rows(design(train, "forecast"), train["target"]), "temp_forecast"]
        assert (x["train_min_c"], x["train_max_c"]) == (t.min(), t.max())
        test = out[(out["fold"] == x["fold"]) & out["pred_gam_forecast"].notna()]
        temp = v2.fp["temp_forecast"].reindex(test.index)
        assert x["hours"] == len(test)
        assert (x["hours_below"], x["hours_above"]) == (int((temp < t.min()).sum()), int((temp > t.max()).sum()))
        assert len(x["outside"]) == x["hours_below"] + x["hours_above"]
    # The range is that of the rows the production model trains on: an hour without a target is none of them.
    fold = wf.folds(*V2_FOLDS)[0]
    train, _ = wf.split(v2.fp, fold)
    coldest = train.index[(train["temp_forecast"] == train["temp_forecast"].min()).to_numpy()]
    table = v2.fp.copy()
    table.loc[coldest, "target"] = np.nan
    ranges = {}
    vd._training_ranges(None, table, [fold], "forecast", ranges)(fold.fit_clock)
    rest = train["temp_forecast"].drop(coldest)
    assert ranges[str(fold.test_month)] == (rest.min(), rest.max()) and rest.min() > train["temp_forecast"].min()


def test_run_validate_binds_every_key_validation_inputs_returns(monkeypatch):
    """`valiwatt validate` runs the real validation_inputs on the design runs and hands run_validation everything it
    returns: every key must be one of run_validation's parameters (a key without one cannot bind), and the runs come
    from the loader for DESIGN_YEARS only."""
    from valiwatt.config import DEMAND_FIRST
    from valiwatt.monitor import run as mrun
    from valiwatt.quality import run as qrun

    last = pd.Timestamp("2019-02-01T05:00Z")
    raw, obs = synth_demand(DEMAND_FIRST, last), synth_obs(DEMAND_FIRST, last)
    weather = pd.DataFrame({f"{s.key}.temp_c": obs["temp_c"].to_numpy() for s in stations()},
                           index=pd.DatetimeIndex(obs["ts_utc"]))
    runs = synth_runs(DEMAND_FIRST - pd.Timedelta(days=3), last)
    runs = runs[runs["runtime"] + pd.Timedelta(hours=2) <= last].reset_index(drop=True)
    asked = []
    monkeypatch.setattr(qrun, "load_runs", lambda dq, years=None: asked.append(years) or runs)
    monkeypatch.setattr(vd, "load_inputs", lambda: (fake_dq(), None, weather))
    monkeypatch.setattr(mrun, "load_raw", lambda dq: (raw, obs))
    monkeypatch.setattr(vd.validation_inputs, "__defaults__", (last, None))  # the world's last record
    real = vd.run_validation
    bound = {}

    def spy(*a, **k):
        bound["args"] = inspect.signature(real).bind(*a, **k).arguments
        raise Reached("run_validation")

    monkeypatch.setattr(vd, "run_validation", spy)
    with pytest.raises(Reached):
        vd.run_validate(n_boot=3)
    assert asked == [vd.DESIGN_YEARS]
    args = bound["args"]
    assert set(args) == {"n_boot", "models", "f_primary", "f_panel", "train_primary", "train_panel", "day_verdicts",
                         "forecast_record"}
    assert args["models"] == tuple(MODELS) and set(args["forecast_record"]) == {"forecast", "forecast_late"}
    assert args["f_primary"]["temp_forecast"].notna().any() and args["f_panel"]["temp_forecast"].isna().all()
    with pytest.raises(TypeError):  # the control: a key that is no parameter cannot bind
        inspect.signature(real).bind(**args, forecasts=None)


def test_a_day_no_run_fed_fails_forecast_missing_in_the_verdicts_validation_calibrates_on(monkeypatch):
    """`valiwatt validate` hands run_validation the replay's day-level verdicts computed with the design runs: a day no
    run fed fails forecast_missing there as in the replay, so no threshold calibrates on its week. On every design day
    the verdict agrees with validation's own forecast record; without the runs (v1's verdicts), the day passes."""
    from valiwatt.config import DEMAND_FIRST
    from valiwatt.forecast.features import REASONS
    from valiwatt.monitor import run as mrun
    from valiwatt.quality import run as qrun

    last, unfed = pd.Timestamp("2019-02-01T05:00Z"), pd.Timestamp("2019-01-16")
    raw, obs = synth_demand(DEMAND_FIRST, last), synth_obs(DEMAND_FIRST, last)
    weather = pd.DataFrame({f"{s.key}.temp_c": obs["temp_c"].to_numpy() for s in stations()},
                           index=pd.DatetimeIndex(obs["ts_utc"]))
    runs = synth_runs(DEMAND_FIRST - pd.Timedelta(days=3), last)
    runs = without_runs_for(runs[runs["runtime"] + pd.Timedelta(hours=2) <= last].reset_index(drop=True), unfed)
    monkeypatch.setattr(qrun, "load_runs", lambda dq, years=None: runs)
    monkeypatch.setattr(vd, "load_inputs", lambda: (fake_dq(), None, weather))
    monkeypatch.setattr(mrun, "load_raw", lambda dq: (raw, obs))
    monkeypatch.setattr(vd.validation_inputs, "__defaults__", (last, None))  # the world's last record
    bound = {}

    def spy(**k):
        bound.update(k)
        raise Reached("run_validation")

    monkeypatch.setattr(vd, "run_validation", spy)
    with pytest.raises(Reached):
        vd.run_validate(n_boot=3)
    verdicts, record = bound["day_verdicts"], bound["forecast_record"]["forecast"]
    missing = [{"kind": "forecast_missing", "reason": REASONS[1]}]
    assert record.loc[unfed, "reason"] == REASONS[1] and verdicts[unfed] == missing
    assert set(verdicts) <= set(record.index)
    for day, problems in verdicts.items():
        reason = record.loc[day, "reason"]
        assert [p for p in problems if p["kind"] == "forecast_missing"] == (
            [{"kind": "forecast_missing", "reason": reason}] if reason else []), day
    # The control: v1's verdicts, without the runs, let the unfed day pass.
    assert vd.validation_inputs(raw, obs, weather, last=last)["day_verdicts"][unfed] == []


def test_the_forecast_record_is_logged_per_design_day(v2, tmp_path, monkeypatch):
    """The control: `valiwatt validate` logs the forecast record of every design day, the frames v2 adds and a
    results file that holds v2's keys and reads back as JSON."""
    import mlflow

    from valiwatt import tracking
    from valiwatt.monitor import run as mrun
    from valiwatt.quality import run as qrun

    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    dq = fake_dq(demand_parquet_sha256="d", weather_parquet_sha256="w", nbm_manifest_sha256="n")
    monkeypatch.setattr(vd, "load_inputs", lambda: (dq, None, None))
    monkeypatch.setattr(mrun, "load_raw", lambda dq: (None, None))
    monkeypatch.setattr(qrun, "load_runs", lambda dq, years=None: None)
    monkeypatch.setattr(vd, "validation_inputs", lambda *a, **k: {})
    monkeypatch.setattr(vd, "run_validation", lambda **k: {**v2.v, "results": dict(v2.r)})
    assert vd.run_validate(n_boot=50) == 0
    run = tracking.latest_run(vd.EXPERIMENT)

    def frame(name):
        return pd.read_parquet(mlflow.artifacts.download_artifacts(run_id=run.info.run_id,
                                                                    artifact_path=f"frames/{name}.parquet"))

    record = frame("forecast_record")
    pd.testing.assert_frame_equal(record, v2.record["forecast"], check_freq=False)
    assert list(record.index) == list(pd.date_range("2019-01-01", "2021-06-30"))
    assert list(record.columns) == ["origin", "runtime", "available_utc", "rank", "lead_h", "reason"]
    pd.testing.assert_frame_equal(frame("intervals"), v2.v["intervals"], check_freq=False)
    pd.testing.assert_frame_equal(frame("intervals_persistence"), v2.v["intervals_persistence"], check_freq=False)
    assert list(frame("oos").columns) == list(v2.out.columns)
    res = json.loads(mlflow.artifacts.load_text(f"runs:/{run.info.run_id}/results/validation.json"))
    assert set(V1_KEYS[()]) | {"production", "contrasts", "verdict", "common_hours", "forecast_input",
                               "extrapolation", "sensitivity", "run_id"} == set(res)
    assert float(run.data.params["cold_c"]) == pytest.approx(res["config"]["cold_c"])
    assert run.data.params["production"] == "gam_forecast" and run.data.tags["production"] == "gam_forecast"
