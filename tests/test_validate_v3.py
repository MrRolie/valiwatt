"""v3's validation (docs/plan-v3.md, sections 6 to 8) end to end on a synthetic world: every model of section 4 in every
fold at its own clock, v2's form at midnight exactly v2's validation, each fold's bin edge from its own fit's rows, the
intervals binned by the edge of the fit being calibrated, the thresholds of each replayed year from their own
validation years, cold_c from the noon feed, the contrasts, verdicts and ranking labelled, the bounds and
sensitivities beside, and the run of record tagged v3 and refused before it logs when the reproduction check fails.

The world is test_validate.py's v2 world (2019 to mid-2021, the same seeds, the day no run fed, the station gap, the
colder winter before the first fold) with the NBM wind added (conftest.synth_runs(wind=True): every other column the
same to the bit)."""

import inspect
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs, synth_runs
from test_validate import GAP, UNFED, V2_FIRST, V2_FOLDS, V2_LAST, v2, without_runs_for  # noqa: F401 (v2: fixture)
from valiwatt.config import V3_THRESHOLD_YEARS
from valiwatt.forecast import evaluate as ev
from valiwatt.forecast import model
from valiwatt.forecast import validate as vd
from valiwatt.forecast import validate_v3 as v3
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import build
from valiwatt.forecast.guards import LookaheadError
from valiwatt.forecast.issue import LAG_DAY_REASON, MIDNIGHT, NOON, TERMS
from valiwatt.forecast.model import MODELS_V3, NOON_KEYS
from valiwatt.quality.demand import build_demand

H = pd.Timedelta(hours=1)
LAST_DAY = pd.Timestamp("2021-06-30")
THRESHOLD_YEARS = {2022: (2021, 2021)}  # intervals start with the 13th fold, 2021-01
LAG_GAP_DAY = pd.Timestamp("2020-05-12")  # GAP leaves 2020-05-10 with 18 station hours: its D+2 has no lag day's mean


def world_tables():
    raw = synth_demand(V2_FIRST, V2_LAST, seed=2)
    obs = synth_obs(V2_FIRST, V2_LAST, seed=2).set_index("ts_utc")["temp_c"]
    raw["mw"] = raw["mw"] + 300 * np.maximum(18 - obs.reindex(raw["ts_utc"]).to_numpy(), 0)
    obs.loc[GAP] = np.nan
    runs = synth_runs(V2_FIRST - pd.Timedelta(days=3), V2_LAST, seed=2, wind=True)
    runs = without_runs_for(runs[runs["runtime"] + pd.Timedelta(hours=2) <= V2_LAST], UNFED)
    runs.loc[runs["ftime"] < pd.Timestamp("2019-03-01T00:00Z"), "tmp_f"] -= 9.0
    days = pd.date_range("2019-01-01", "2021-06-30")
    noon = vd.forecast_inputs(runs, days, last=V2_LAST, issue=NOON)
    midnight = vd.forecast_inputs(runs, days, last=V2_LAST)
    demand = build_demand(raw, V2_FIRST, V2_LAST).frame
    record = {"noon": {k: x.record for k, x in noon.items()}, "midnight": {k: x.record for k, x in midnight.items()}}
    return build(demand, obs, noon, issue=NOON), build(demand, obs, midnight), record


@pytest.fixture(scope="module")
def world():
    f_noon, f_midnight, record = world_tables()
    verdicts = {d: [] for d in pd.date_range("2019-01-01", "2021-12-31")}
    v = v3.run_validation_v3(f_noon, f_midnight, day_verdicts=verdicts, forecast_record=record, n_boot=50,
                             first_fold=V2_FOLDS[0], last_fold=V2_FOLDS[1], last_day=LAST_DAY,
                             threshold_years=THRESHOLD_YEARS)
    return SimpleNamespace(f_noon=f_noon, f_midnight=f_midnight, record=record, verdicts=verdicts, v=v,
                           r=v["results"], out=v["out"], fits=v["fits"])


# ------------------------------------------------------------------------------------- the models and their clocks


def test_every_model_of_section_4_is_fitted_in_every_fold_at_its_own_clock(world):
    out, fits = world.out, world.fits
    folds = [str(p) for p in pd.period_range(*V2_FOLDS, freq="M")]
    assert sorted(fits) == folds and sorted(out["fold"].unique()) == folds
    for k in MODELS_V3:
        assert f"pred_{k}" in out and f"n_train_{k}" in out, k
        assert out[f"pred_{k}"].notna().groupby(out["fold"]).any().all(), k  # a forecast in every fold
    for fold, r in fits.items():
        assert r["fit_clock"] == wf.month_folds(fold, fold, issue=NOON)[0].fit_clock
        assert r["fit_clock"] == pd.Period(fold).start_time.tz_localize("UTC") - pd.Timedelta(hours=7)  # eve 17:00
        assert sorted(r["edges"]) == sorted(v3.BINNED) == sorted(NOON_KEYS)
    assert set(world.r["performance"]) == set(MODELS_V3) | {"baseline"}
    assert set(world.r["coverage"]) == set(NOON_KEYS) and set(world.r["coverage"]["v3"]) == {"binned", "single"}
    assert "lag24" not in out and "temp_persistence" not in out  # nothing of the midnight table leaks into the frame
    assert set(v3.NOON_INPUTS) <= set(out.columns)  # the noon inputs, hour by hour, for the rehearsal (section 11.1)


def test_v2_s_form_at_midnight_is_v2_s_validation_exactly(world, v2):  # noqa: F811
    """Section 4.2: v2's form at midnight runs on v2's own table, clocks and loop. On v2's world and folds, its
    forecasts are v2's validation's production forecasts to the bit, hour for hour, with the same hours missing:
    the reproduction check (`check_reproduction`) on synthetic data."""
    mine, theirs = world.out["pred_v2_midnight"], v2.out["pred_gam_forecast"]
    assert mine.index.equals(theirs.index)
    np.testing.assert_array_equal(mine.to_numpy(), theirs.to_numpy())
    recorded = pd.DataFrame({"ts": theirs.index, "forecast": theirs.to_numpy()})
    assert v3.check_reproduction(world.out, recorded, days=("2020-01-01", "2021-06-30"))["max_abs_mw"] == 0.0
    assert (world.out["n_train_v2_midnight"] == v2.out["n_train_gam_forecast"]).all()


@pytest.fixture(scope="module")
def why(world):
    out = world.out
    frame_why = {k: v3.why_noon(world.f_noon, out, k, world.record["noon"]) for k in NOON_KEYS}
    return out, frame_why


def test_the_lag_day_and_the_unfed_day_are_dropped_with_their_reasons(world, why):
    """Section 5: a day whose lag day's mean is missing has no forecast from any noon model, with the reason
    lag_day_temperature_missing; v2's form at midnight still forecasts it; a day no run fed has no forecast from any
    model, with the record's reason."""
    out, reasons = why
    lagless, unfed = out["day"] == LAG_GAP_DAY, out["day"] == UNFED
    for k in NOON_KEYS:
        assert out.loc[lagless | unfed, f"pred_{k}"].isna().all(), k
        assert set(reasons[k][lagless]) == {LAG_DAY_REASON} and set(reasons[k][unfed]) == {"no_covering_run"}, k
        assert (reasons[k][out[f"pred_{k}"].notna()] == "").all()
        assert out[f"why_{k}"].equals(reasons[k]), k  # the frame logged says the same, hour by hour
    assert out.loc[lagless, "pred_v2_midnight"].notna().all() and out.loc[unfed, "pred_v2_midnight"].isna().all()
    lacking = world.r["common_hours"]
    assert lacking["v3"] == {LAG_DAY_REASON: 24, "no_covering_run": 24}
    assert lacking["horizon"]["v2_midnight"] == {"no_covering_run": 24}
    assert "2020-05-12" in world.r["forecast_input"]["noon"]["lag_day_missing_days"]


# --------------------------------------------------------------------------------------------- the bin edges


def edges_of(table: pd.DataFrame, fold_list) -> dict:
    """Each fold's bin edge as v3's fit stores it, the fold loop handed `table` whole at every clock."""
    _, fits = v3.run_noon(table, fold_list, train_at=lambda clock: table, keys=("v3",))
    return {fold: r["edges"]["v3"] for fold, r in fits.items()}


def test_a_temperature_issued_after_a_fold_s_clock_leaves_its_bin_edge_unchanged(world):
    """Section 12: changing a temperature issued after a fold's clock leaves that fold's bin edge unchanged. Every
    noon-feed temperature issued after March 2020's clock (2020-02-29 17:00 UTC) is set to -45 C in the table the loop
    is handed whole: March's edge does not move (April's, which trains on March, does, so the change was seen). The
    control: March's own usable training rows moved below its edge (one in ten of those above it: the noon feed's
    whole degrees Fahrenheit tie, so one row alone may leave the quantile where it is) move it. Each edge is the rule
    on its fit's own usable rows."""
    f = world.f_noon
    fl = wf.month_folds("2020-03", "2020-04", issue=NOON)
    base = edges_of(f, fl)
    train, _ = wf.split(f, fl[0])

    late = f.copy()
    after = (late["origin"] > fl[0].fit_clock).to_numpy()
    assert after.any() and not (late.loc[after, "day"] <= fl[0].train_last_day).any()
    late.loc[after, "temp_forecast"] = -45.0
    moved = edges_of(late, fl)
    assert moved["2020-03"] == base["2020-03"]
    assert moved["2020-04"] != base["2020-04"]
    assert base["2020-03"] == model.bin_edge(train.loc[v3.own_rows("v3", train), "temp_forecast"].to_numpy())

    usable = f.copy()
    rows = train.index[v3.own_rows("v3", train).to_numpy() & (train["temp_forecast"] > base["2020-03"]).to_numpy()]
    usable.loc[rows[::10], "temp_forecast"] = base["2020-03"] - 20.0
    assert edges_of(usable, fl)["2020-03"] != base["2020-03"]


def test_each_fold_bins_its_calibration_by_the_edge_of_the_fit_being_calibrated(world):
    """Section 12, in the validation loop: each fold's intervals carry its own fit's edge, and its calibration window
    (the previous 12 folds scored by its noon clock) is binned by that edge, never by the edges of the folds that
    scored the hours. 2021-01 and 2021-02 have different edges and share eleven months of calibration hours; the
    hours between the two edges are cold for one fold and rest for the other."""
    out, fits, ivs = world.out, world.fits, world.v["intervals_by_model"]
    for k, iv in ivs.items():
        by = iv.groupby("fold").first()
        assert by["edge_c"].to_dict() == {fold: fits[fold]["edges"][k] for fold in by.index}, k
    a, b = "2021-01", "2021-02"
    ea, eb = fits[a]["edges"]["v3"], fits[b]["edges"]["v3"]
    assert ea != eb
    windows = {}
    for fold in (a, b):
        months = [str(p) for p in pd.period_range(pd.Period(fold) - 12, pd.Period(fold) - 1, freq="M")]
        w = ev.calibration_rows(out[out["fold"].isin(months)], ev.fit_clock_of(fold, NOON))
        score = (w["target"] - w["pred_v3"]).abs() / w["pred_v3"]
        expected = ev.binned_half_widths(score, w["temp_forecast"], w["day"], fits[fold]["edges"]["v3"])
        got = ivs["v3"][ivs["v3"]["fold"] == fold].iloc[0]
        assert {k: got[k] for k in ("q_cold", "q_rest", "q_pooled", "cold_n", "cold_weeks", "fallback")} == \
            {k: expected[k] for k in ("q_cold", "q_rest", "q_pooled", "cold_n", "cold_weeks", "fallback")}, fold
        windows[fold] = w
    shared = windows[a].index.intersection(windows[b].index)
    t = windows[a].loc[shared, "temp_forecast"]
    between = shared[((t >= min(ea, eb)) & (t < max(ea, eb))).to_numpy()]
    assert len(between) > 0  # the same scores, binned one way for one fold and the other way for the other
    assert ivs["v3"].groupby("fold")["cold_n"].first()[a] != ivs["v3"].groupby("fold")["cold_n"].first()[b]


def test_every_interval_says_whether_its_cold_bin_fell_back_and_the_single_half_width_is_beside(world):
    bins = world.v["bins"]
    assert set(bins["model"]) == set(v3.BINNED) and len(bins) == len(v3.BINNED) * 6  # 2021-01 to 2021-06
    for row in bins.itertuples():
        assert row.fallback == (row.cold_n < ev.MIN_COLD_HOURS or row.cold_weeks < ev.MIN_COLD_WEEKS)
        assert (row.q_cold == row.q_pooled) or not row.fallback
    iv = world.v["intervals"]
    assert {"bin", "edge_c", "fallback", "q_cold", "q_rest", "q_pooled"} <= set(iv.columns)
    single = world.v["intervals_single"]
    assert "bin" not in single and single.index.equals(iv.index)
    pd.testing.assert_frame_equal(single, ev.relative_conformal(world.out, "pred_v3", issue=NOON))
    cov = world.r["coverage"]["v3"]
    assert {r["key"] for r in cov["binned"]["bin"]} <= {"cold", "rest"} and "overall" in cov["single"]


# ------------------------------------------------------------------------------- thresholds, cold_c, the decision


def test_the_thresholds_of_each_replayed_year_read_their_own_validation_years(world):
    """Section 6: the 2025 thresholds from the 2022-2024 weeks, the rehearsal's from 2021-2023: an explicit table
    (config.V3_THRESHOLD_YEARS), the default of run_validation_v3; here one year of the world, on v3's binned
    coverage and the noon feed's temperature."""
    assert V3_THRESHOLD_YEARS == {2024: (2021, 2023), 2025: (2022, 2024)}
    assert inspect.signature(v3.run_validation_v3).parameters["threshold_years"].default is V3_THRESHOLD_YEARS
    th = world.r["thresholds"]
    assert set(th) == {"2022"} and (th["2022"]["first_year"], th["2022"]["last_year"]) == (2021, 2021)
    expected = vd.thresholds(ev.errors(world.out, "pred_v3"), world.v["intervals"]["covered"],
                             world.f_noon["temp_forecast"], world.f_noon["day"], world.out["pred_v3"],
                             world.out["day"], 2021, 2021, world.verdicts)
    assert json.dumps(th["2022"], default=str, sort_keys=True) == json.dumps(expected, default=str, sort_keys=True)


def test_cold_c_comes_from_the_noon_feed_over_every_design_hour(world):
    """Section 6: the 5th percentile of the noon feed's temperatures over every design hour that has one (the hours
    before the first fold included; the winter of 2019, forecast colder, moves it), inputs only."""
    f = world.f_noon
    cold_c = world.r["config"]["cold_c"]
    assert cold_c == ev.cold_threshold(f.loc[f["day"] <= LAST_DAY, "temp_forecast"])
    assert cold_c != ev.cold_threshold(f.loc[f["day"] >= pd.Timestamp("2020-01-01"), "temp_forecast"])
    assert world.r["contrasts"]["cold_c"] == cold_c


def test_the_contrasts_verdicts_and_ranking_are_evaluate_s_and_labelled_in_sample(world):
    r, out = world.r, world.out
    assert r["contrasts"]["label"] == r["verdict"]["label"] == r["ranking"]["label"] == v3.IN_SAMPLE_LABEL_V3
    frame = out[["target", "day", "temp_forecast", *(f"pred_{k}" for k in (*NOON_KEYS, "v2_midnight"))]]
    c = ev.contrasts_v3(frame, r["config"]["cold_c"], n_boot=50)
    for name in ("terms", "family", "whole"):
        assert r["contrasts"][name]["all"]["difference"] == c[name]["all"]["difference"], name
    assert r["contrasts"]["horizon"]["contrast"]["all"]["difference"] == c["horizon"]["contrast"]["all"]["difference"]
    assert {k: r["verdict"][k] for k in ("terms", "family")} == ev.verdict_v3(c)
    assert sorted(r["ranking"]["years"]) == ["2020", "2021"]
    assert r["ranking"]["years"]["2021"] == ev.rank_windows(frame, "2021-01-01", "2021-12-31")


def test_each_bound_and_sensitivity_is_a_labelled_contrast_against_v3(world):
    """Section 4.3: each term left out, the six-hour margin, the observed-weather bound and the seasonal baseline,
    scored against v3 on the common set intersected with its hours, on each subset, the dropped hours by reason."""
    s = world.r["sensitivity"]
    assert set(s) == set(v3.SENSITIVITIES)
    for k, x in s.items():
        assert x["label"] == v3.SENSITIVITY_LABEL and x["versus"] == "v3" and set(x["contrast"]) == set(ev.SUBSETS)
        assert x["hours"] + sum(x["dropped"].values()) == world.r["contrasts"]["hours"], k
    # The station gap: the observed-weather bound lacks the station's hours and its day's mean on 2020-05-10.
    assert s["v3_observed"]["dropped"] == {"daymean_observed": 18, "temp_observed, daymean_observed": 6}
    common = ev.common_hours(world.out, [f"pred_{k}" for k in NOON_KEYS])
    d = ev.paired_difference(ev._ape(world.out, common, "v3_late"), ev._ape(world.out, common, "v3"), n_boot=50)
    assert s["v3_late"]["contrast"]["all"]["difference"] == d["difference"]


def test_the_forecast_hours_outside_each_fold_s_training_range_of_each_new_term_are_listed(world):
    """Section 6's added finding: per fold, the range of the noon temperature and of each term over v3's own usable
    training rows, and the hours v3 forecasts outside it, with their errors."""
    ext = world.r["extrapolation"]
    assert set(ext) == {"temp_forecast", *TERMS}
    f, out = world.f_noon, world.out
    fold = "2020-06"
    train, _ = wf.split(f, wf.month_folds(fold, fold, issue=NOON)[0])
    rows = train[v3.own_rows("v3", train)]
    lag_cold = np.maximum(18.0 - rows["lagday_observed"], 0.0)
    entry = next(e for e in ext["lag_day_mean_cold"] if e["fold"] == fold)
    assert (entry["train_min"], entry["train_max"]) == (lag_cold.min(), lag_cold.max())
    test = out[(out["fold"] == fold) & out["pred_v3"].notna()]
    value = np.maximum(18.0 - f.loc[test.index, "lagday_observed"], 0.0)
    outside = test.index[((value < lag_cold.min()) | (value > lag_cold.max())).to_numpy()]
    assert [o["utc"] for o in entry["outside"]] == list(outside) and entry["hours"] == len(test)
    assert sum(e["hours_below"] + e["hours_above"] for e in ext["lag_day_mean_cold"]) > 0  # June is milder than May
    for o in entry["outside"]:
        assert o["ape"] == pytest.approx(abs(out.at[o["utc"], "target"] - out.at[o["utc"], "pred_v3"])
                                         / out.at[o["utc"], "target"])


# ---------------------------------------------------------------------------------------------------- the gates


def test_the_noon_and_midnight_folds_must_score_the_same_hours_and_targets(world):
    """join_midnight refuses frames whose hours, days or targets differ; the control (the world's) passes."""
    f = world.f_noon
    fl = wf.month_folds("2020-03", "2020-03", issue=NOON)
    out_n, _ = v3.run_noon(f, fl, keys=("v1_noon",))
    out_m = wf.run(world.f_midnight, wf.month_folds("2020-03", "2020-03"), models=("gam_forecast",))
    joined = v3.join_midnight(out_n, out_m)
    assert joined["pred_v2_midnight"].equals(out_m["pred_gam_forecast"].reindex(out_n.index))
    for bad in (out_m.iloc[1:], out_m.assign(target=out_m["target"] + np.r_[1.0, np.zeros(len(out_m) - 1)]),
                out_m.assign(target=out_m["target"].where(np.arange(len(out_m)) != 5))):
        with pytest.raises(ValueError, match="different hours|another target"):
            v3.join_midnight(out_n, bad)


def test_a_midnight_model_is_never_fitted_on_noon_folds(world):
    fl = wf.month_folds("2020-03", "2020-03", issue=NOON)
    with pytest.raises(ValueError, match="not issued at the folds' clock"):
        v3.run_noon(world.f_noon, fl, keys=("v3", "v2_midnight"))
    with pytest.raises(LookaheadError):  # a midnight fold on the noon table: its clock is not the table's origin
        v3.run_noon(world.f_noon, wf.month_folds("2020-03", "2020-03"), keys=())


def test_validation_refuses_a_row_after_the_design_window(world):
    leaky = pd.concat([world.f_noon, world.f_noon.iloc[[-1]].assign(day=LAST_DAY + pd.Timedelta(days=3))])
    with pytest.raises(LookaheadError, match="after the design window"):
        v3.run_validation_v3(leaky, world.f_midnight, day_verdicts=world.verdicts, forecast_record=world.record,
                             n_boot=10, first_fold="2020-03", last_fold="2020-03", last_day=LAST_DAY,
                             threshold_years={})


# ------------------------------------------------------------------------------------------- the run of record


def fake_dq(tags: dict | None = None, **params):
    """v3's data-quality run of the 2019-2024 window, its byte copies checked against the read of v2's store read2;
    tags and params laid over."""
    params = {"through": "2024", "hq_sha256": "h" * 64, "eccc_manifests_sha256": "e" * 64,
              "nbm_manifest_sha256": "n" * 64, "v2_read_run_id": "read2"} | params
    tags = {"byte_copies": "passed", "fresh_clone": "false"} | (tags or {})
    return SimpleNamespace(info=SimpleNamespace(run_id="dq3"), data=SimpleNamespace(tags=tags, params=params))


@pytest.fixture
def entry(tmp_path, monkeypatch, world):
    """`valiwatt validate-v3`'s inputs replaced by the world's validation, in a throwaway MLflow store; returns what
    each step was handed (seen["dq"] is the data-quality run latest_passed gives, seen["paths"] the canonical files
    load_inputs was handed, seen["reads"] the reads of v3's store)."""
    from valiwatt import firstrun, tracking, v2store
    from valiwatt.monitor import run as mrun
    from valiwatt.quality import run as qrun
    from valiwatt.quality import run_v3

    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    seen = {"first_run": 0}
    monkeypatch.setattr(firstrun, "require_first_run", lambda: seen.__setitem__("first_run", seen["first_run"] + 1))
    seen["dq"], seen["reads"] = fake_dq(), []
    monkeypatch.setattr(run_v3, "latest_passed", lambda: seen["dq"])
    monkeypatch.setattr(vd, "load_inputs", lambda dq, paths, command: seen.__setitem__("paths", paths) or (dq, None,
                                                                                                         "weather"))
    monkeypatch.setattr(mrun, "load_raw", lambda dq: ("raw", "raw_t"))
    monkeypatch.setattr(qrun, "load_runs", lambda dq, years=None: "runs" if years == v3.V3_DESIGN_YEARS else None)
    monkeypatch.setattr(v3, "validation_inputs_v3", lambda *a: seen.__setitem__("inputs", a) or {})
    monkeypatch.setattr(v3, "run_validation_v3", lambda **k: world.v)
    recorded = pd.DataFrame({"ts": world.out.index, "forecast": world.out["pred_v2_midnight"].to_numpy()})
    seen["recorded"] = recorded
    monkeypatch.setattr(v2store, "recorded", lambda: seen["reads"].append("recorded") or {"run_id": "read2"})
    monkeypatch.setattr(v2store, "recorded_forecasts", lambda: seen["reads"].append("forecasts") or seen["recorded"])
    return seen


def runs_in_store():
    from valiwatt import tracking

    c = tracking.client()
    exp = c.get_experiment_by_name(vd.EXPERIMENT)
    return [] if exp is None else c.search_runs([exp.experiment_id])


def test_the_run_of_record_is_tagged_v3_and_pre_freeze_and_logs_its_frames(entry, world):
    import mlflow

    from valiwatt.quality import run_v3

    assert v3.run_validate_v3(n_boot=50) == 0
    assert entry["first_run"] == 1 and entry["inputs"] == ("raw", "raw_t", "weather", "runs")
    assert entry["paths"] == run_v3.canonical_paths(2024)
    (run,) = runs_in_store()
    t = run.data.tags
    assert (t["kind"], t["version"], t["freeze_tag"], t["status"]) == ("validation_v3", "v3", "v3-pre-freeze",
                                                                      "complete")
    assert (t["reproduction"], t["v2_read_run_id"], t["dq_run_id"]) == ("passed", "read2", "dq3")
    assert (t["byte_copies"], t["fresh_clone"]) == ("passed", "false")
    assert t["kind"] != "validation"  # v2's validation readers never take a v3 run
    res = json.loads(mlflow.artifacts.load_text(f"runs:/{run.info.run_id}/results/validation.json"))
    assert res["reproduction"]["status"] == "passed" and res["version"] == "v3"
    frames = {a.path for a in tracking_client().list_artifacts(run.info.run_id, "frames")}
    assert {"frames/oos.parquet", "frames/bins.parquet", "frames/intervals_v3.parquet",
            "frames/intervals_single.parquet", "frames/forecast_record_noon.parquet",
            "frames/forecast_record_noon_late.parquet", "frames/forecast_record_midnight.parquet"} <= frames
    assert float(run.data.params["cold_c"]) == pytest.approx(world.r["config"]["cold_c"])


def tracking_client():
    from valiwatt import tracking

    return tracking.client()


@pytest.mark.parametrize("change", ["value", "hour"])
def test_a_failed_reproduction_refuses_before_any_run_is_logged(entry, change):
    """Section 12: v2's form at midnight more than 1e-6 MW from v2's recorded validation on one hour, or on another
    set of hours, and the run refuses: nothing reaches the store. The control is the test above."""
    bad = entry["recorded"].copy()
    if change == "value":
        bad.loc[bad["forecast"].notna().idxmax(), "forecast"] += 2e-6
    else:
        bad = bad.drop(index=bad["forecast"].notna().idxmax())
    entry["recorded"] = bad
    with pytest.raises(v3.ReproductionError):
        v3.run_validate_v3(n_boot=50)
    assert runs_in_store() == []


@pytest.mark.parametrize("through", ["2025", None])
def test_validate_v3_reads_a_data_quality_run_of_the_2019_2024_window_only(entry, through):
    """Sections 3.1 and 11.2: a data-quality run of another window (the 2025 vintage re-serves 2019-2024 with its
    revisions), or one that names none, is refused before any file is read and before anything is logged; the run of
    the 2019-2024 window is read (the control)."""
    entry["dq"] = fake_dq(through=through)
    if through is None:
        del entry["dq"].data.params["through"]
    with pytest.raises(vd.LineageError, match="2019-2024 files only"):
        v3.run_validate_v3(n_boot=50)
    assert "paths" not in entry and entry["reads"] == [] and runs_in_store() == []
    entry["dq"] = fake_dq()
    assert v3.run_validate_v3(n_boot=50) == 0 and len(runs_in_store()) == 1


def test_a_data_quality_run_whose_byte_copies_were_not_checked_is_refused_unless_a_fresh_clone(entry):
    """Section 3.1: not run is never inferred from an absence. A data-quality run whose byte-copy check did not pass is
    refused unless it is labelled a fresh clone, before any file is read; on a fresh clone the reproduction check is
    not run, labelled so, and v3's store is not asked for a read of v2's."""
    entry["dq"] = fake_dq(tags={"byte_copies": "not_run"}, v2_read_run_id="")
    with pytest.raises(vd.LineageError, match="not passed, and the run is not labelled a fresh clone"):
        v3.run_validate_v3(n_boot=50)
    assert "paths" not in entry and runs_in_store() == []
    entry["dq"] = fake_dq(tags={"byte_copies": "not_run", "fresh_clone": "true"}, v2_read_run_id="")
    assert v3.run_validate_v3(n_boot=50) == 0
    (run,) = runs_in_store()
    t = run.data.tags
    assert (t["reproduction"], t["fresh_clone"], t["byte_copies"], t["v2_read_run_id"]) == ("not_run", "true",
                                                                                           "not_run", "")
    assert entry["reads"] == []


@pytest.mark.parametrize("read", [None, "read9"])
def test_without_the_read_its_data_quality_run_checked_the_reproduction_is_refused(entry, monkeypatch, read):
    """A data-quality run whose byte copies passed was checked against one read of v2's store: with no read in v3's
    store, or another, the reproduction check cannot be run, and validate-v3 refuses before any file is read."""
    from valiwatt import v2store

    monkeypatch.setattr(v2store, "recorded", lambda: None if read is None else {"run_id": read})
    with pytest.raises(vd.LineageError, match="no reproduction check without it"):
        v3.run_validate_v3(n_boot=50)
    assert "paths" not in entry and runs_in_store() == []


def test_a_fold_whose_own_rows_taken_again_differ_from_its_fit_s_is_refused(world, monkeypatch):
    """Each fold's term ranges are read on v3's own usable training rows taken again from the table: as many as the fit
    used, or the fold is refused (a table whose rows differ from the fit's). The control: the rows as taken."""
    folds = wf.month_folds("2020-03", "2020-03", issue=NOON)
    v3.run_noon(world.f_noon, folds, keys=("v3",))  # the control
    real = v3.own_rows

    def one_short(key, table):
        rows = real(key, table).copy()
        rows.iloc[int(np.flatnonzero(rows.to_numpy())[0])] = False
        return rows

    monkeypatch.setattr(v3, "own_rows", one_short)
    with pytest.raises(RuntimeError, match="own rows, not the"):
        v3.run_noon(world.f_noon, folds, keys=("v3",))


def test_the_freeze_tag_is_v3_s_freeze_once_its_tag_exists(monkeypatch):
    """Section 10.3: runs made before `v3-rules-frozen` exists carry the freeze tag v3-pre-freeze."""
    calls = []

    def git(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0 if exists else 1, stdout="abc123\n" if exists else "")

    monkeypatch.setattr(v3.subprocess, "run", git)
    for exists, expected in ((False, "v3-pre-freeze"), (True, "v3-rules-frozen")):
        assert v3.freeze_tag() == expected
    assert all("refs/tags/v3-rules-frozen^{commit}" in a for a in calls)


# ------------------------------------------------------------------------------------------ the real-data inputs


@pytest.fixture(scope="module")
def raw_inputs():
    """Raw records of 2019-01 to 2019-04 (demand, the station, the weather table, NBM runs with their wind), and the
    tables validation_inputs_v3 builds from them, the day-level verdicts read by the noon data-quality layer
    (quality.days.day_verdicts) replaced by a recorder."""
    from valiwatt.config import stations
    from valiwatt.quality import days as qdays

    first, last = pd.Timestamp("2019-01-01T06:00Z"), pd.Timestamp("2019-05-01T05:00Z")
    raw, obs = synth_demand(first, last, seed=3), synth_obs(first, last, seed=3)
    weather = pd.DataFrame({f"{s.key}.temp_c": obs.set_index("ts_utc")["temp_c"] for s in stations()})
    runs = synth_runs(first - pd.Timedelta(days=3), last, seed=3, wind=True)
    runs = runs[runs["runtime"] + 2 * H <= last].reset_index(drop=True)
    seen = {}

    def verdicts(raw_demand, raw_temperature, first_day, last_day, *, record, lag_day):
        seen.update(first_day=first_day, last_day=last_day, record=record, lag_day=lag_day)
        return {"called": True}

    real, qdays.day_verdicts = qdays.day_verdicts, verdicts
    try:
        inputs = v3.validation_inputs_v3(raw, obs, weather, runs, last=last)
    finally:
        qdays.day_verdicts = real
    return SimpleNamespace(raw=raw, obs=obs, weather=weather, runs=runs, last=last, inputs=inputs, seen=seen)


def test_validation_inputs_v3_give_v2_s_tables_at_midnight_and_the_noon_tables_beside(raw_inputs):
    """The midnight table and its as-of training tables are v2's validation_inputs' (the same raw records, the same
    cut); the noon table is issued at the eve's 17:00 UTC with lag48, the noon feed's wind, day's mean and the lag
    day's mean; the day-level verdicts are the noon data-quality layer's, on the noon record and the lag day's mean."""
    x, w = raw_inputs.inputs, raw_inputs
    theirs = vd.validation_inputs(w.raw, w.obs, w.weather, last=w.last, runs=w.runs)
    pd.testing.assert_frame_equal(x["f_midnight"], theirs["f_primary"])
    clock = wf.month_folds("2019-04", "2019-04")[0].fit_clock
    pd.testing.assert_frame_equal(x["train_midnight"](clock), theirs["train_primary"](clock))
    for s in ("forecast", "forecast_late"):
        pd.testing.assert_frame_equal(x["forecast_record"]["midnight"][s], theirs["forecast_record"][s])
    f = x["f_noon"]
    assert (f["origin"] == f["day"].dt.tz_localize("UTC") - pd.Timedelta(hours=7)).all()
    assert {"lag48", "lag168", "wind_forecast", "daymean_forecast", "lagday_observed"} <= set(f.columns)
    assert "lag24" not in f and (f["available_forecast"].dropna() <= f.loc[f["available_forecast"].notna(),
                                                                          "origin"]).all()
    assert x["day_verdicts"] == {"called": True}
    seen = raw_inputs.seen
    assert seen["record"] is x["forecast_record"]["noon"]["forecast"]
    assert (seen["first_day"], seen["last_day"]) == (f["day"].min(), pd.Timestamp("2019-04-30"))
    pd.testing.assert_series_equal(seen["lag_day"], f.groupby("day")["lagday_observed"].first())


def test_the_frozen_tables_are_validation_s_training_tables_at_each_fit_clock(raw_inputs, monkeypatch):
    """The freeze fits on the table validation's folds train on at the same clock: the noon table at the noon clock,
    v2's at v2's (one run rule, one cut). The world holds no new year, so the frozen fold is April 2019's."""
    from valiwatt.forecast import freeze_v3 as fv

    assert fv.fold_of("v3", 2025).fit_clock == pd.Timestamp("2024-12-31T17:00Z")
    assert fv.fold_of("v2_midnight", 2025).fit_clock == pd.Timestamp("2025-01-01T05:00Z")
    w, x = raw_inputs, raw_inputs.inputs
    monkeypatch.setattr(fv, "fold_of", lambda key, year: wf.month_folds("2019-04", "2019-04",
                                                                        issue=MODELS_V3[key].issue)[0])
    tables = fv.frozen_tables_v3(w.raw, w.weather, 2019, w.runs, last=w.last)
    noon, midnight = (wf.month_folds("2019-04", "2019-04", issue=i)[0].fit_clock for i in (NOON, MIDNIGHT))
    pd.testing.assert_frame_equal(tables["noon"], x["train_noon"](noon))
    pd.testing.assert_frame_equal(tables["midnight"], x["train_midnight"](midnight))
    assert tables["noon"].index.max() == noon and tables["midnight"].index.max() == midnight
