"""Validation end to end on synthetic design years: it refuses 2024 rows and logs per-season thresholds."""

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs
from valiwatt.forecast import validate as vd
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import build
from valiwatt.forecast.guards import DESIGN_LAST_DAY, LookaheadError
from valiwatt.monitor.replay import day_verdicts
from valiwatt.quality.demand import build_demand

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


def test_each_fold_trains_on_the_table_seen_at_its_fit_clock(tables):
    """run_validation hands each fold's fit clock to the as-of table providers, and trains on what they return."""
    fp, fpanel = tables
    asked = {"primary": [], "panel": []}

    def at(name, f):
        def table(clock):
            asked[name].append(clock)
            return f[f.index <= clock]
        return table

    v = vd.run_validation(fp, fpanel, n_boot=10, first_fold="2022-01", last_fold="2023-02",
                          train_primary=at("primary", fp), train_panel=at("panel", fpanel))
    clocks = [fold.fit_clock for fold in wf.folds("2022-01", "2023-02")]
    assert asked["primary"] == clocks and asked["panel"] == clocks
    static = vd.run_validation(fp, fpanel, n_boot=10, first_fold="2022-01", last_fold="2023-02")
    pd.testing.assert_frame_equal(v["out"], static["out"])  # no repair in this world: the same rows either way


def test_raw_demand_other_than_the_checked_file_is_refused(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from valiwatt.sources import hydroquebec
    from valiwatt.sources.http import sha256_file

    csv = tmp_path / "hq.csv"
    csv.write_text(",".join(hydroquebec.HEADER) + "\n2021-01-01T06:00:00Z,20000\n", encoding="utf-8")
    monkeypatch.setattr(hydroquebec, "CSV_PATH", csv)
    monkeypatch.setattr(hydroquebec.load_raw, "__defaults__", (csv,))
    dq = SimpleNamespace(info=SimpleNamespace(run_id="dq"),
                         data=SimpleNamespace(params={"hq_sha256": sha256_file(csv)}))
    assert len(vd.load_raw_demand(dq)) == 1
    csv.write_text(",".join(hydroquebec.HEADER) + "\n2021-01-01T06:00:00Z,20001\n", encoding="utf-8")
    with pytest.raises(vd.LineageError):
        vd.load_raw_demand(dq)


def test_mondrian_intervals_calibrate_within_each_group():
    from valiwatt.forecast.evaluate import relative_conformal, season_of

    idx = pd.date_range("2021-01-01T06:00Z", periods=24 * 720, freq="h")
    day = (idx - pd.Timedelta(hours=6)).tz_convert(None).normalize()
    rng = np.random.default_rng(0)
    winter = pd.Series(day).dt.month.isin([12, 1, 2]).to_numpy()
    pred = np.full(len(idx), 20_000.0)
    target = pred * (1 + rng.normal(0, np.where(winter, 0.08, 0.02)))
    out = pd.DataFrame({"target": target, "pred": pred, "day": day,
                        "fold": pd.Series(day).dt.to_period("M").astype(str).to_numpy()}, index=idx)
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
