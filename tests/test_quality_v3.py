"""v3's data quality (docs/plan-v3.md, sections 3.1, 3.2, 3.3, 5, 9 and 12): the byte-copy check of the raw files
against v2's first 2024 replay, the data-quality run at noon (its wind gate, the feeding run's hour by year, the days
the wind clause changes, the lag day's mean, the midnight feed reported only), and a noon day's verdict read on its own
hours. Synthetic data and scratch directories only."""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs, synth_runs
from test_pipeline_refuses import MANIFESTS, nbm_manifest
from valiwatt import firstrun, tracking, v2store
from valiwatt.config import DEMAND_FIRST, DESIGN_LAST, V3_DESIGN_LAST, data_last, fetch_months, stations
from valiwatt.forecast.features import forecast_temperature, observed_day_mean
from valiwatt.forecast.issue import LAG_DAY_REASON, NOON, day_edge, day_labels, day_origin
from valiwatt.quality import byte_copies as bc
from valiwatt.quality import days as qdays
from valiwatt.quality import run as qrun
from valiwatt.quality import run_v3
from valiwatt.quality.checks import jsonable
from valiwatt.quality.demand import build_demand, demand_checks
from valiwatt.quality.weather import lag_day_check, station_series, weather_checks, wind_clause_check
from valiwatt.sources import eccc, hydroquebec, nbm
from valiwatt.sources.http import sha256_file

H = pd.Timedelta(hours=1)
YEARS = list(range(2019, 2025))

# ------------------------------------------------------------------------------------------------ the byte copies


@pytest.fixture
def raw_tree(tmp_path, monkeypatch):
    """A scratch data/raw holding v3's copies of the 2024 window's files (one station), and the anchor the read of
    v2's store would have logged for them: the hashes v2's first 2024 replay's data-quality run recorded."""
    root = tmp_path / "raw"
    monkeypatch.setattr(hydroquebec, "CSV_PATH", root / "hydroquebec" / "export.csv")
    monkeypatch.setattr(hydroquebec, "MANIFEST_PATH", root / "hydroquebec" / "manifest.json")
    monkeypatch.setattr(eccc, "RAW_ECCC", root / "eccc")
    monkeypatch.setattr(nbm, "RAW_NBM", root / "nbm")
    monkeypatch.setattr(nbm, "MANIFEST_PATH", root / "nbm" / "manifest.json")
    monkeypatch.setattr(nbm, "year_path", lambda year: root / "nbm" / f"CYUL-NBS-{int(year)}.csv")
    st = stations()[0]
    monkeypatch.setattr(bc, "stations", lambda: [st])

    csv = hydroquebec.CSV_PATH
    csv.parent.mkdir(parents=True)
    csv.write_text("date,moyenne_mw\n2024-01-01T06:00:00+00:00,21000\n")
    hydroquebec.MANIFEST_PATH.write_text(json.dumps({"sha256": sha256_file(csv)}))
    months = {}
    for y, m in fetch_months(2024):
        path = eccc.month_path(st, y, m)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{y}-{m:02d}\n")
        months[f"{y:04d}-{m:02d}"] = {"sha256": sha256_file(path), "retrieved_utc": "2026-10-13T00:00:00Z"}
    eccc_manifest = eccc.RAW_ECCC / st.climate_id / "manifest.json"
    # The manifest also lists a month past the window, which v2's run never hashed (quality.run._verify_manifests keeps
    # the window's months only); the anchor is v2's digest of the window's months, listed here by hand.
    beyond = {"2025-02": {"sha256": "5" * 64, "retrieved_utc": "2026-10-13T00:00:00Z"}}
    eccc_manifest.write_text(json.dumps({"climate_id": st.climate_id, "months": months | beyond}))
    years = {}
    for y in YEARS:
        path = nbm.year_path(y)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"runtime,ftime,model,tmp,wsp,station\n{y}\n")
        years[str(y)] = {"sha256": sha256_file(path), "rows": 1}
    nbm.MANIFEST_PATH.write_text(json.dumps({"station": "CYUL", "years": years}))
    anchor = {"run_id": "r" * 32, "v2_dq_run_id": "d" * 32, "hq_sha256": sha256_file(csv),
              "eccc_manifests_sha256": qrun.eccc_manifests_sha256([{"climate_id": st.climate_id, "months": months}]),
              "nbm_manifest_sha256": nbm.manifest_sha256(nbm.for_years(nbm.manifest(), YEARS))}
    return {"root": root, "anchor": anchor, "station": st, "eccc_manifest": eccc_manifest, "tmp": tmp_path}


def test_byte_copies_of_v2s_files_pass_and_a_fresh_clone_is_not_run(raw_tree):
    a = raw_tree["anchor"]
    got = bc.check_byte_copies(a)
    assert got["status"] == "passed" and got["v2_dq_run_id"] == a["v2_dq_run_id"] and got["v2_read_run_id"] == a["run_id"]
    assert got["files"] == 2 + 1 + len(fetch_months(2024)) + 1 + len(YEARS)
    assert bc.check_byte_copies(None, fresh_clone=True) == {"status": "not_run"}  # told it is a fresh clone
    # Only the manifest's 2019-2024 entries are checked: v2's 2025 fetch adds its year, and nothing differs.
    m = json.loads(nbm.MANIFEST_PATH.read_text())
    m["years"]["2025"] = {"sha256": "5" * 64, "rows": 9}
    nbm.MANIFEST_PATH.write_text(json.dumps(m))
    assert bc.check_byte_copies(a)["status"] == "passed"
    # A plain ECCC fetch rewrites its manifest's bytes with the same content: digests, never bytes.
    raw_tree["eccc_manifest"].write_text(json.dumps(json.loads(raw_tree["eccc_manifest"].read_text()), indent=4))
    assert bc.check_byte_copies(a)["status"] == "passed"


def test_not_run_is_never_inferred_from_a_missing_read_of_v2s_store(raw_tree):
    """Section 3.1: the byte-copy check is not run only on a fresh clone the run is told of; without the word, no read
    of v2's store refuses, and with it a read in v3's store refuses (that machine is not a fresh clone)."""
    with pytest.raises(bc.ByteCopyRefused, match="no read of v2's store in v3's store"):
        bc.check_byte_copies(None)
    with pytest.raises(bc.ByteCopyRefused, match="not a fresh clone"):
        bc.check_byte_copies(raw_tree["anchor"], fresh_clone=True)


def _append(path, text="x"):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


@pytest.mark.parametrize("which", ["hq", "eccc_file", "eccc_manifest", "nbm_file", "nbm_manifest"])
def test_a_raw_file_whose_hash_differs_from_v2s_is_refused(raw_tree, which):
    a, st = raw_tree["anchor"], raw_tree["station"]
    if which == "hq":
        _append(hydroquebec.CSV_PATH)
        match = "Hydro-Québec export"
    elif which == "eccc_file":
        _append(eccc.month_path(st, 2022, 4))
        match = f"ECCC file {st.climate_id}/2022-04.csv"
    elif which == "eccc_manifest":  # a month fetched again: its manifest entry changes
        m = json.loads(raw_tree["eccc_manifest"].read_text())
        m["months"]["2023-02"]["retrieved_utc"] = "2026-10-27T00:00:00Z"
        raw_tree["eccc_manifest"].write_text(json.dumps(m))
        match = "ECCC manifests"
    elif which == "nbm_file":
        _append(nbm.year_path(2023))
        match = "NBS file CYUL-NBS-2023.csv"
    else:
        m = json.loads(nbm.MANIFEST_PATH.read_text())
        m["years"]["2021"]["rows"] = 2
        nbm.MANIFEST_PATH.write_text(json.dumps(m))
        match = "NBM manifest's 2019-2024 entries"
    with pytest.raises(bc.ByteCopyRefused, match=match):
        bc.check_byte_copies(a)


@pytest.mark.parametrize("how", ["file_symlink", "directory_symlink", "hard_link"])
def test_a_link_in_place_of_a_byte_copy_is_refused(raw_tree, how):
    """The same bytes, reached through a link: refused (section 3.1, "byte copies (never links)")."""
    a, st, tmp = raw_tree["anchor"], raw_tree["station"], raw_tree["tmp"]
    v2_side = tmp / "v2_checkout"
    v2_side.mkdir()
    if how == "file_symlink":
        target = eccc.month_path(st, 2021, 7)
        os.replace(target, v2_side / target.name)
        target.symlink_to(v2_side / target.name)
        match = "symbolic link"
    elif how == "directory_symlink":
        d = nbm.RAW_NBM
        os.replace(d, v2_side / "nbm")
        d.symlink_to(v2_side / "nbm", target_is_directory=True)
        match = "symbolic link"
    else:
        os.link(nbm.year_path(2020), v2_side / "CYUL-NBS-2020.csv")
        match = "hard links"
    with pytest.raises(bc.ByteCopyRefused, match=match):
        bc.check_byte_copies(a)


# --------------------------------------------------------------------------------------- the data-quality run at noon

def nbm_years_store(runs: pd.DataFrame, calls: list):
    """`sources.nbm.load_runs` on a synthetic archive, recording the years it is asked for."""
    def load(years):
        years = list(years)
        calls.append(years)
        lo, hi = pd.Timestamp(f"{min(years)}-01-01T00:00Z"), pd.Timestamp(f"{max(years) + 1}-01-01T01:00Z")
        out = runs[(runs["runtime"] >= lo) & (runs["runtime"] <= hi)].reset_index(drop=True)
        out.attrs["identical_duplicates"] = 0
        return out
    return load


def isolate_v3(tmp_path, monkeypatch, runs: pd.DataFrame, noisy_2024: bool = False,
               sources: dict | None = None) -> list:
    """v3's data-quality run in a scratch store on synthetic sources ending with 2024, the first-run guard passing and
    no read of v2's store logged (a fresh clone, which the run must be told of: the byte-copy check is not run).
    Returns the NBM years asked for. noisy_2024: every hour after 2024-01-01 05:00 UTC set off its neighbours, the
    demand by 280 MW and each station's temperature by 2.8 C, alternately up and down, so the spike statistics' robust
    scale moves with the hours it reads; sources: filled with the raw demand and the stations' observations."""
    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    monkeypatch.setattr(run_v3, "INTERIM", tmp_path / "interim")
    monkeypatch.setattr(run_v3, "rel", lambda p: p.name)
    monkeypatch.setattr(qrun, "INTERIM", tmp_path / "interim_v2")
    monkeypatch.setattr(firstrun, "require_first_run", lambda: "0" * 40)
    monkeypatch.setattr(v2store, "recorded", lambda client=None: None)
    monkeypatch.setattr(qrun, "_verify_manifests", lambda through=2024: MANIFESTS)
    monkeypatch.setattr(qrun, "_verify_hq", lambda through=2024: MANIFESTS[0])
    monkeypatch.setattr(qrun, "_verify_nbm", lambda through=2024: nbm_manifest(through))
    first, last = DEMAND_FIRST.isoformat(), data_last(2024).isoformat()
    obs = {s.climate_id: synth_obs(first, last, seed=i) for i, s in enumerate(stations())}
    raw = synth_demand(first, last)
    if noisy_2024:
        for frame, col, step in [(raw, "mw", 280.0)] + [(o, "temp_c", 2.8) for o in obs.values()]:
            late = (frame["ts_utc"] > DESIGN_LAST).to_numpy()
            frame.loc[late, col] += np.where(np.arange(late.sum()) % 2 == 0, step, -step)
    monkeypatch.setattr(eccc, "load_station", lambda st, through=2024: obs[st.climate_id])
    calls = []
    monkeypatch.setattr(nbm, "load_runs", nbm_years_store(runs, calls))
    monkeypatch.setattr(hydroquebec, "load_raw", lambda path=None: raw)
    if sources is not None:
        sources |= {"raw": raw, "obs": obs}
    return calls


@pytest.fixture(scope="module")
def runs_2019_2024():
    return synth_runs(DEMAND_FIRST.isoformat(), data_last(2024).isoformat(), wind=True)


def dq_results(run) -> dict:
    import mlflow

    return json.loads(mlflow.artifacts.load_text(f"runs:/{run.info.run_id}/results/dq_results.json"))


def spike_checks(sources: dict, design_last: pd.Timestamp) -> dict:
    """The demand's and each station's spike statistics on the 2019-2024 window, their robust scale cut at
    `design_last`, as a data-quality run's results file holds them."""
    window = hydroquebec.load_window(2024)
    build = build_demand(window, DEMAND_FIRST, data_last(2024), scale_last=design_last)
    checks = demand_checks(build, design_last)
    for st in stations():
        checks += weather_checks(st, station_series(sources["obs"][st.climate_id], DEMAND_FIRST, data_last(2024)),
                                 design_last)
    return {c.name: json.loads(json.dumps(jsonable(c.to_dict()))) for c in checks
            if c.name == "demand_spikes" or c.name.startswith("weather_gaps")}


def test_the_v3_data_quality_run_at_noon_on_clean_data(tmp_path, monkeypatch, runs_2019_2024):
    """The clean run, on a fresh clone it is told of. Its 2024 is noisier than the years before it, so the spike
    statistics' robust scale differs with its cut (section 12, the spike scale): the run's demand and station spike
    statistics are those cut at 2025-01-01 05:00 UTC, the last label of 2024, never at v2's 2024-01-01 05:00 UTC."""
    sources = {}
    calls = isolate_v3(tmp_path, monkeypatch, runs_2019_2024, noisy_2024=True, sources=sources)
    assert run_v3.run_dq_v3(fresh_clone=True) == 0
    run = run_v3.latest_passed()
    t, p = run.data.tags, run.data.params
    assert t["kind"] == "dq_v3" and t["gates"] == "passed" and t["issue"] == "noon" and t["byte_copies"] == "not_run"
    assert t["fresh_clone"] == "true"
    assert p["design_last_utc"] == V3_DESIGN_LAST.isoformat() == "2025-01-01T05:00:00+00:00"
    assert p["nbm_years"] == "2019,2020,2021,2022,2023,2024" and calls == [YEARS]  # an explicit list
    assert {"hq_sha256", "eccc_manifests_sha256", "nbm_manifest_sha256"} <= set(p)
    with pytest.raises(LookupError):  # never one of v2's data-quality runs
        tracking.latest_run(qrun.EXPERIMENT, "tags.kind = 'dq'")
    c = dq_results(run)["checks"]
    assert c["nbm_wind_bounds"]["kind"] == "gate" and c["nbm_wind_bounds"]["passed"]
    m = c["nbm_runs"]["metrics"]
    # The noon feed: the midday run feeds every day but the first, whose origin (2018-12-31 17:00 UTC) precedes every run.
    days = {y: (pd.Timestamp(f"{y + 1}-01-01") - pd.Timestamp(f"{y}-01-01")).days for y in YEARS}
    assert m["run_hour_by_year"] == {str(y): {"13": days[y] - (y == 2019)} for y in YEARS}
    assert m["reasons"] == {"no_run_available": 1} and m["reuse_lead_h"] == 87
    assert m["error_last_utc"] == V3_DESIGN_LAST.isoformat() and "2024" in m["error_vs_observed"]
    assert c["nbm_wind_clause"]["kind"] == "finding" and c["nbm_wind_clause"]["metrics"]["days"] == 0
    lag = c["lag_day_temperature"]["metrics"]
    assert lag["missing"] == 2 and lag["by_year"] == {"2019": 2}  # the lag days of 1 and 2 January 2019 are unobserved
    mid = c["nbm_runs[midnight]"]
    assert mid["kind"] == "finding" and mid["metrics"]["reuse_lead_h"] == 75
    assert mid["metrics"]["run_hour_by_year"]["2021"] == {"1": days[2021]}
    assert (tmp_path / "interim" / "demand-v3.parquet").exists() and not (tmp_path / "interim_v2").exists()
    v3_cut, v2_cut = spike_checks(sources, V3_DESIGN_LAST), spike_checks(sources, DESIGN_LAST)
    assert len(v3_cut) == 1 + len(stations())
    for name, want in v3_cut.items():
        assert c[name] == want and c[name] != v2_cut[name], name


def test_a_wind_point_above_100_knots_fails_the_v3_run_and_its_day_takes_the_next_run(tmp_path, monkeypatch,
                                                                                     runs_2019_2024):
    """Section 3.1: a point outside the wind bounds fails the gate, as a temperature outside its bounds does; section
    3.2: the day whose feeding run held it is fed by the next run, and listed among the wind clause's days."""
    runs = runs_2019_2024.copy()
    rt, ft = pd.Timestamp("2022-06-14T13:00Z"), pd.Timestamp("2022-06-15T12:00Z")
    point = (runs["runtime"] == rt) & (runs["ftime"] == ft)
    assert point.sum() == 1
    runs.loc[point, "wsp_kt"] = 101.0
    isolate_v3(tmp_path, monkeypatch, runs)
    assert run_v3.run_dq_v3(fresh_clone=True) == 1
    run = tracking.latest_run(run_v3.EXPERIMENT, f"tags.kind = '{run_v3.KIND}'")
    assert run.data.tags["gates"] == "failed" and not (tmp_path / "interim" / "demand-v3.parquet").exists()
    c = dq_results(run)["checks"]
    assert not c["nbm_wind_bounds"]["passed"] and c["nbm_wind_bounds"]["metrics"]["violations"] == 1
    clause = c["nbm_wind_clause"]
    assert clause["metrics"]["days"] == 1 and clause["metrics"]["by_year"] == {"2022": 1}
    assert clause["evidence"] == [{"day": "2022-06-15T00:00:00", "runtime": "2022-06-14T07:00:00+00:00", "reason": "",
                                   "runtime_without_wind": rt.isoformat(), "reason_without_wind": ""}]


WFIRST, WLAST = pd.Timestamp("2021-01-04T06:00Z"), pd.Timestamp("2021-02-01T05:00Z")
WDAYS = pd.date_range("2021-01-06", "2021-01-31", freq="D")


def test_the_wind_clause_lists_the_days_whose_run_changes_and_no_other():
    runs = synth_runs(WFIRST - 48 * H, WLAST, wind=True)
    without = dict(issue=run_v3.NOON_WITHOUT_WIND)
    clean = wind_clause_check(forecast_temperature(runs, WDAYS, issue=NOON), forecast_temperature(runs, WDAYS, **without))
    assert clean.passed and clean.metrics["days"] == 0 and clean.evidence == []
    day = pd.Timestamp("2021-01-20")
    fresh = day_origin(day, issue=NOON) - 4 * H  # D-1 13:00 UTC, the midday run
    blank = runs.copy()
    blank.loc[(blank["runtime"] == fresh) & (blank["ftime"] == day_edge(day) + 4 * H), "wsp_kt"] = np.nan
    c = wind_clause_check(forecast_temperature(blank, WDAYS, issue=NOON), forecast_temperature(blank, WDAYS, **without))
    assert c.kind == "finding" and c.passed and c.metrics["days"] == 1 and c.metrics["by_year"] == {2021: 1}
    assert c.evidence == [{"day": day, "runtime": fresh - 6 * H, "reason": "", "runtime_without_wind": fresh,
                           "reason_without_wind": ""}]
    with pytest.raises(ValueError, match="other days or other origins"):
        wind_clause_check(forecast_temperature(blank, WDAYS, issue=NOON), forecast_temperature(blank, WDAYS))


def test_the_midnight_feed_is_reported_and_never_gates():
    """The same runs leave days unfed at both clocks: the noon feed's coverage gate fails, the midnight feed's
    finding stays a finding (plan-v3: the midnight feed is a data-quality finding only)."""
    runs = synth_runs(WFIRST - 48 * H, WLAST, wind=True)
    gone = (runs["runtime"] >= pd.Timestamp("2021-01-10T00:00Z")) & (runs["runtime"] < pd.Timestamp("2021-01-15T00:00Z"))
    observed = synth_obs(WFIRST.isoformat(), WLAST.isoformat()).set_index("ts_utc")["temp_c"]
    checks, feeds = run_v3.forecast_checks_v3(runs[~gone].reset_index(drop=True), WDAYS, observed)
    c = {x.name: x for x in checks}
    assert c["nbm_coverage"].kind == "gate" and not c["nbm_coverage"].passed
    mid = c["nbm_runs[midnight]"]
    assert mid.kind == "finding" and mid.passed and not mid.metrics["coverage_at_threshold"]
    assert mid.metrics["days_not_fed"] > 0 and mid.metrics["coverage"]["share"] < 0.98
    assert (feeds["midnight"].record["origin"] == [day_edge(d) for d in WDAYS]).all()
    assert [x.name for x in checks if x.kind == "gate"] == ["nbm_bounds", "nbm_coverage", "nbm_wind_bounds"]


def test_the_lag_day_finding_lists_the_days_whose_lag_day_holds_19_usable_hours_and_not_20():
    obs = synth_obs(WFIRST.isoformat(), WLAST.isoformat()).set_index("ts_utc")["temp_c"]
    day = pd.Timestamp("2021-01-20")
    lag_labels = day_labels(day - pd.Timedelta(days=2))
    for blanked, missing in ((5, True), (4, False)):
        o = obs.copy()
        o.loc[lag_labels[:blanked]] = np.nan
        c = lag_day_check(observed_day_mean(o), WDAYS)
        assert c.kind == "finding" and c.passed
        assert (day in [e["day"] for e in c.evidence]) is missing, blanked
        assert c.metrics["missing"] == int(missing)
    assert c.metrics["days"] == len(WDAYS)


def test_the_v3_run_refuses_a_file_that_is_not_v2s_before_it_reads_any_file(raw_tree, monkeypatch):
    seen = []
    monkeypatch.setattr(tracking, "MLRUNS", raw_tree["tmp"] / "mlruns")
    monkeypatch.setattr(firstrun, "require_first_run", lambda: "0" * 40)
    monkeypatch.setattr(v2store, "recorded", lambda client=None: raw_tree["anchor"])
    monkeypatch.setattr(qrun, "_verify_manifests", lambda through=2024: seen.append(through))
    _append(nbm.year_path(2019))
    with pytest.raises(bc.ByteCopyRefused, match="CYUL-NBS-2019.csv"):
        run_v3.run_dq_v3()
    assert seen == []


def test_without_a_read_of_v2s_store_dq_v3_refuses_unless_told_it_is_a_fresh_clone(raw_tree, tmp_path, monkeypatch):
    """Section 3.1: not run is never inferred from an absence. Without a read of v2's store, dq-v3 refuses unless
    run with --fresh-clone; with --fresh-clone, a read in v3's store refuses; each before any raw file is read and
    before anything is logged. The control is the clean run above (a fresh clone it is told of)."""
    seen = []
    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    monkeypatch.setattr(firstrun, "require_first_run", lambda: "0" * 40)
    monkeypatch.setattr(qrun, "_verify_manifests", lambda through=2024: seen.append(through))
    for read, fresh, match in ((None, False, "no read of v2's store"), (raw_tree["anchor"], True, "not a fresh clone")):
        monkeypatch.setattr(v2store, "recorded", lambda client=None, read=read: read)
        with pytest.raises(bc.ByteCopyRefused, match=match):
            run_v3.run_dq_v3(fresh_clone=fresh)
    with pytest.raises(LookupError):
        tracking.latest_run(run_v3.EXPERIMENT)
    assert seen == []


# ------------------------------------------------------------------------------ a noon day's verdict, on its own hours

VFIRST, VLAST = "2019-01-01T06:00Z", "2019-01-12T05:00Z"
D = pd.Timestamp("2019-01-07")


def verdict_inputs():
    raw = synth_demand(VFIRST, VLAST)
    raw_t = synth_obs(VFIRST, VLAST)
    return raw, raw_t


def judged(raw, raw_t, **kw):
    return qdays.day_verdicts(raw, raw_t, D - pd.Timedelta(days=2), D + pd.Timedelta(days=2), **kw)


@pytest.mark.parametrize("stamp,owner", [
    ("2019-01-06T18:00Z", -1),  # after D's noon origin (D-1 17:00 UTC), in D-1's hours
    ("2019-01-07T05:00Z", -1),  # D's edge: D-1's last hour
    ("2019-01-07T06:00Z", 0),  # D's first hour
    ("2019-01-08T05:00Z", 0),  # D's last hour
    ("2019-01-08T06:00Z", 1),  # D+1's first hour, the hour D is scored at
])
def test_a_noon_days_verdict_reads_only_the_raw_records_stamped_in_its_own_hours(stamp, owner):
    raw, raw_t = verdict_inputs()
    assert not any(judged(raw, raw_t).values())  # the control: every day passes
    t = pd.Timestamp(stamp)
    assert day_origin(D, issue=NOON) == pd.Timestamp("2019-01-06T17:00Z")
    bad = pd.concat([raw, pd.DataFrame({"ts_utc": [t], "mw": [99_000.0], "source_line": [0]})], ignore_index=True)
    bad_t = pd.concat([raw_t, raw_t[raw_t["ts_utc"] == t].assign(temp_c=80.0)], ignore_index=True)
    for kind, v in (("demand_out_of_bounds", judged(bad, raw_t)), ("temperature_out_of_bounds", judged(raw, bad_t))):
        failed = {d for d, p in v.items() if any(x["kind"] == kind for x in p)}
        assert failed == {D + pd.Timedelta(days=owner)}, (kind, stamp)


def test_a_noon_day_without_its_feed_or_its_lag_days_mean_fails_its_verdict():
    raw, raw_t = verdict_inputs()
    days = pd.date_range(D - pd.Timedelta(days=2), D + pd.Timedelta(days=2))
    record = pd.DataFrame({"runtime": pd.NaT, "reason": ""}, index=days)
    record.loc[D, "reason"] = "no_covering_run"
    lag = pd.Series(1.0, index=days)
    lag[D + pd.Timedelta(days=1)] = np.nan
    v = judged(raw, raw_t, record=record, lag_day=lag)
    assert v[D] == [{"kind": "forecast_missing", "reason": "no_covering_run"}]
    assert v[D + pd.Timedelta(days=1)] == [{"kind": LAG_DAY_REASON}]
    assert not any(p for d, p in v.items() if d not in (D, D + pd.Timedelta(days=1)))
