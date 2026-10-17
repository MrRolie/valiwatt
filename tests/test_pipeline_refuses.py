"""End to end on synthetic data: a failing gate stops the chain, and a clean run renders a report; a real-data run
needs the `v2-first-run` tag; the window's end is a parameter, and the 2025 run lists the revised 2019-2024 records."""

import io
import subprocess

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs, synth_runs
from valiwatt import tracking
from valiwatt.config import DEMAND_FIRST, DEMAND_LAST, FIRST_RUN_TAG, data_last, stations
from valiwatt.quality import run as dq
from valiwatt.report import render as rr
from valiwatt.sources.nbm import REUSE_LEAD_H


def nbm_store(runs: pd.DataFrame):
    """`sources.nbm.load_runs` on a synthetic archive: the runs of the listed years' requests."""
    def load(years):
        years = list(years)
        lo, hi = pd.Timestamp(f"{min(years)}-01-01T00:00Z"), pd.Timestamp(f"{max(years) + 1}-01-01T01:00Z")
        out = runs[(runs["runtime"] >= lo) & (runs["runtime"] <= hi)].reset_index(drop=True)
        out.attrs["identical_duplicates"] = 0
        return out
    return load


def nbm_manifest(through=2024):
    return {"years": {str(y): {"retrieved_utc": "2026-01-01T00:00:00Z", "sha256": f"{y}" * 16, "rows": 1, "runs": 1,
                               "first_runtime": f"{y}-01-01 01:00:00", "last_runtime": f"{y + 1}-01-01 01:00:00",
                               "blank_tmp": 0}
                      for y in range(2019, through + 1)}}


MANIFESTS = (
    {"dataset": "synthetic", "export_url": "https://example.invalid", "retrieved_utc": "2026-01-01T00:00:00Z",
     "sha256": "0" * 64, "rows": 0, "dataset_modified": "2026-01-01", "license": "test", "license_url": "x",
     "publisher": "test"},
    [{"months": {"2019-01": {"retrieved_utc": "2026-01-01T00:00:00Z", "rows_without_observation": 0}}}
     for _ in stations()],
)


def isolate(tmp_path, monkeypatch, through: int):
    """The data-quality run in a scratch store, on synthetic sources ending with `through`."""
    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    monkeypatch.setattr(dq, "INTERIM", tmp_path / "interim")
    monkeypatch.setattr(rr, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(dq, "rel", lambda p: p.name)
    monkeypatch.setattr(dq, "_verify_raw", lambda through=2024: MANIFESTS)
    monkeypatch.setattr(dq, "_verify_hq", lambda through=2024: MANIFESTS[0])
    monkeypatch.setattr(dq, "_verify_nbm", lambda through=2024: nbm_manifest(through))
    first, last = DEMAND_FIRST.isoformat(), data_last(through).isoformat()
    obs = {s.climate_id: synth_obs(first, last, seed=i) for i, s in enumerate(stations())}
    monkeypatch.setattr(dq.eccc, "load_station", lambda st, through=2024: obs[st.climate_id])
    monkeypatch.setattr(dq.nbm, "load_runs", nbm_store(synth_runs(first, last)))
    return synth_demand(first, last)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    return tmp_path, isolate(tmp_path, monkeypatch, 2024)


def serve(monkeypatch, raw):
    """The Hydro-Québec export on disk holds `raw` (load_window's own cut still runs)."""
    monkeypatch.setattr(dq.hydroquebec, "load_raw", lambda path=None: raw)


def test_failing_gate_stops_the_chain(sandbox, monkeypatch):
    tmp, raw = sandbox
    t = raw.loc[1000, "ts_utc"]
    broken = pd.concat([raw, pd.DataFrame({"ts_utc": [t], "mw": [raw.loc[1000, "mw"] + 900], "source_line": [0]})])
    serve(monkeypatch, broken)
    assert dq.run_dq() == 1
    assert not (tmp / "interim" / "demand.parquet").exists()
    run = tracking.latest_run(dq.EXPERIMENT)
    assert run.data.tags["gates"] == "failed"
    with pytest.raises(rr.ReportRefused):
        rr.render(run.info.run_id)


def test_clean_run_writes_data_and_renders(sandbox, monkeypatch):
    tmp, raw = sandbox
    serve(monkeypatch, raw)
    assert dq.run_dq() == 0
    assert (tmp / "interim" / "demand.parquet").exists()
    written = rr.render()
    text = written[0].read_text(encoding="utf-8")
    assert "Qualité et représentativité" in text
    from valiwatt import attribution

    for path, lang in zip(written, ("fr", "en")):
        body = path.read_text(encoding="utf-8")
        for line in attribution.lines(lang, retrieved="2026-01-01"):
            assert line in body, (lang, line)
    run = tracking.latest_run(dq.EXPERIMENT, "tags.gates = 'passed'")
    assert run.info.status == "FINISHED"
    assert np.isfinite(run.data.metrics["repr.r2.panel"])
    assert run.data.params["through"] == "2024" and run.data.params["window_last_utc"] == DEMAND_LAST.isoformat()
    assert run.data.params["nbm_manifest_sha256"] == dq.nbm.manifest_sha256(nbm_manifest())
    assert run.data.metrics["nbm_bounds.passed"] == 1 and run.data.metrics["nbm_coverage.passed"] == 1
    assert "hq_revised_records" not in run.data.params  # the revisions are listed by the 2025 run only
    for lang in ("fr", "en"):
        assert (tmp / "reports" / "figures" / lang / dq.figures.FORECAST_FEEDING).exists()


def test_failing_weather_gate_stops_the_chain(sandbox, monkeypatch):
    tmp, raw = sandbox
    serve(monkeypatch, raw)
    original = dq.eccc.load_station

    def outage(st, through=2024):
        obs = original(st).copy()
        if st.primary:
            obs.loc[1000 : 1000 + int(len(obs) * 0.10), "temp_c"] = np.nan
        return obs

    monkeypatch.setattr(dq.eccc, "load_station", outage)
    assert dq.run_dq() == 1
    assert not (tmp / "interim" / "demand.parquet").exists()
    assert tracking.latest_run(dq.EXPERIMENT).data.tags["gates"] == "failed"


def test_a_failing_nbm_gate_stops_the_chain(sandbox, monkeypatch):
    tmp, raw = sandbox
    serve(monkeypatch, raw)
    runs = synth_runs(DEMAND_FIRST.isoformat(), DEMAND_LAST.isoformat())
    runs.loc[20_000, "tmp_f"] = 200.0  # 93 °C
    monkeypatch.setattr(dq.nbm, "load_runs", nbm_store(runs))
    assert dq.run_dq() == 1
    assert not (tmp / "interim" / "demand.parquet").exists()
    run = tracking.latest_run(dq.EXPERIMENT)
    assert run.data.tags["gates"] == "failed" and run.data.metrics["nbm_bounds.passed"] == 0
    assert run.data.metrics["nbm_bounds.violations"] == 1 and run.data.metrics["demand_window.passed"] == 1


def test_hydroquebec_records_after_the_window_are_set_aside_and_counted(sandbox, monkeypatch):
    tmp, raw = sandbox
    later = pd.DataFrame({"ts_utc": pd.to_datetime(["2025-01-01T06:00Z", "2025-03-01T12:00Z"]), "mw": [21_000.0] * 2,
                          "source_line": [0, 0]})
    serve(monkeypatch, pd.concat([raw, later], ignore_index=True))
    assert dq.run_dq() == 0  # the window gate stays as v1 wrote it: the cut at load keeps 2025 away from it
    run = tracking.latest_run(dq.EXPERIMENT)
    assert run.data.params["hq_records_after_window"] == "2" and run.data.metrics["demand_window.passed"] == 1
    assert pd.read_parquet(tmp / "interim" / "demand.parquet").index.max() == DEMAND_LAST


def test_raw_files_that_no_longer_match_their_manifest_are_refused(tmp_path, monkeypatch):
    from valiwatt.sources import hydroquebec
    from valiwatt.sources.http import FetchRefused, sha256_file

    monkeypatch.setattr(dq, "require_first_run", lambda: None)  # the real repository has no v2-first-run tag yet
    csv, manifest = tmp_path / "hq.csv", tmp_path / "manifest.json"
    monkeypatch.setattr(hydroquebec, "CSV_PATH", csv)
    monkeypatch.setattr(hydroquebec, "MANIFEST_PATH", manifest)
    with pytest.raises(FetchRefused, match="no Hydro-Québec manifest"):
        dq._verify_raw()
    csv.write_text("date,moyenne_mw\n")
    manifest.write_text('{"sha256": "%s"}' % sha256_file(csv))
    monkeypatch.setattr(dq, "stations", lambda: [])
    dq._verify_raw()
    csv.write_text("date,moyenne_mw\nX\n")
    with pytest.raises(FetchRefused, match="no longer matches"):
        dq._verify_raw()
    with pytest.raises(FetchRefused, match="no Hydro-Québec manifest through 2025"):
        dq._verify_raw(2025)  # the 2025 vintage has a file and a manifest of its own


def test_eccc_month_that_no_longer_matches_its_manifest_is_refused(tmp_path, monkeypatch):
    import json

    from valiwatt.sources import eccc, hydroquebec
    from valiwatt.sources.http import FetchRefused, sha256_file

    monkeypatch.setattr(dq, "require_first_run", lambda: None)  # the real repository has no v2-first-run tag yet
    csv, manifest = tmp_path / "hq.csv", tmp_path / "manifest.json"
    csv.write_text("date,moyenne_mw\n")
    manifest.write_text(json.dumps({"sha256": sha256_file(csv)}))
    monkeypatch.setattr(hydroquebec, "CSV_PATH", csv)
    monkeypatch.setattr(hydroquebec, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(eccc, "RAW_ECCC", tmp_path / "eccc")
    st = stations()[0]
    monkeypatch.setattr(dq, "stations", lambda: [st])
    with pytest.raises(FetchRefused, match="no manifest"):
        dq._verify_raw()
    month = eccc.month_path(st, 2019, 1)
    month.parent.mkdir(parents=True)
    month.write_text("a\n")
    later = eccc.month_path(st, 2025, 6)  # a month of a later window: not this window's, never checked by it
    later.write_text("c\n")
    (month.parent / "manifest.json").write_text(json.dumps({"months": {"2019-01": {"sha256": sha256_file(month)},
                                                                       "2025-06": {"sha256": "0" * 64}}}))
    _, stn = dq._verify_raw()
    assert list(stn[0]["months"]) == ["2019-01"]
    month.write_text("b\n")
    with pytest.raises(FetchRefused, match="no longer matches"):
        dq._verify_raw()


def test_failing_panel_gate_stops_the_chain(sandbox, monkeypatch):
    tmp, raw = sandbox
    serve(monkeypatch, raw)
    original = dq.eccc.load_station
    silent = {s.climate_id for s in stations()[:3]}

    def shared_outage(st, through=2024):
        obs = original(st).copy()
        if st.climate_id in silent:
            obs.loc[5000 : 5000 + int(len(obs) * 0.03), "temp_c"] = np.nan
        return obs

    monkeypatch.setattr(dq.eccc, "load_station", shared_outage)
    assert dq.run_dq() == 1
    run = tracking.latest_run(dq.EXPERIMENT)
    assert run.data.tags["gates"] == "failed" and run.data.metrics["weather_panel_coverage.passed"] == 0.0


def test_report_refuses_when_the_code_changed_since_the_run(sandbox, monkeypatch):
    tmp, raw = sandbox
    serve(monkeypatch, raw)
    assert dq.run_dq() == 0
    monkeypatch.setattr(rr, "code_sha256", lambda: "0" * 64)
    with pytest.raises(rr.ReportRefused, match="code changed"):
        rr.render()


# The v2-first-run tag (docs/plan-v2.md, section 10.4), in a scratch repository.

def git(repo, *args):
    return subprocess.run(["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def history(tmp_path, monkeypatch):
    """Commits a and b on the main line, c on a side branch from a; HEAD on b."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    commits = {}
    for name in ("a", "b"):
        (repo / f"{name}.txt").write_text(name)
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", name)
        commits[name] = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "side", commits["a"])
    (repo / "c.txt").write_text("c")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "c")
    commits["c"] = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    monkeypatch.setattr(dq, "REPO_ROOT", repo)
    return repo, commits


def tag(repo, commit):
    git(repo, "tag", "-f", "-a", "-m", "first run", FIRST_RUN_TAG, commit)


def test_a_real_data_run_without_the_first_run_tag_is_refused(history, tmp_path, monkeypatch):
    repo, c = history
    from valiwatt.sources import hydroquebec

    monkeypatch.setattr(hydroquebec, "MANIFEST_PATH", tmp_path / "absent" / "manifest.json")
    with pytest.raises(dq.FirstRunRefused, match="no `v2-first-run` tag"):
        dq.require_first_run()
    with pytest.raises(dq.FirstRunRefused):  # before any file is read: the missing manifest is never reached
        dq._verify_raw()
    tag(repo, c["c"])  # on a side branch: not an ancestor of HEAD
    with pytest.raises(dq.FirstRunRefused, match="does not descend"):
        dq.require_first_run()
    tag(repo, c["b"])
    git(repo, "checkout", "-q", c["a"])  # HEAD before the tag
    with pytest.raises(dq.FirstRunRefused, match="does not descend"):
        dq.require_first_run()


def test_a_real_data_run_from_a_descendant_of_the_first_run_tag_passes(history, tmp_path, monkeypatch):
    repo, c = history
    from valiwatt.sources import hydroquebec
    from valiwatt.sources.http import FetchRefused

    tag(repo, c["a"])  # HEAD (b) descends from it
    dq.require_first_run()
    tag(repo, c["b"])  # HEAD itself
    dq.require_first_run()
    monkeypatch.setattr(hydroquebec, "MANIFEST_PATH", tmp_path / "absent" / "manifest.json")
    with pytest.raises(FetchRefused, match="no Hydro-Québec manifest"):  # past the tag, on to the files
        dq._verify_raw()


# The NBM runs read against the data-quality run's own manifest hash.

def test_dq_runs_load_runs_against_its_own_manifest_hash(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from valiwatt.forecast.validate import LineageError
    from valiwatt.sources import nbm
    from valiwatt.sources.http import FetchRefused

    import test_nbm_source as T

    raw = tmp_path / "nbm"
    monkeypatch.setattr(nbm, "RAW_NBM", raw)
    monkeypatch.setattr(nbm, "MANIFEST_PATH", raw / "manifest.json")
    monkeypatch.setattr(nbm, "rel", lambda p: p.name)
    texts = {2022: T.iem_text(T.short_runs(2022)), 2023: T.iem_text(T.short_runs(2023))}
    monkeypatch.setattr(nbm, "get", lambda url, params=None, **kw: SimpleNamespace(
        content=texts[int(params["sts"][:4])].encode(), url=url))
    monkeypatch.setattr(nbm, "FIRST_YEAR", 2022)
    nbm.fetch(2023)
    checked = nbm.manifest_sha256(nbm.verify_files([2022, 2023]))
    run = SimpleNamespace(data=SimpleNamespace(params={"through": "2023", "nbm_manifest_sha256": checked}),
                          info=SimpleNamespace(run_id="dq1"))
    runs = dq.load_runs(run, years=[2022])  # control: the files the run checked
    assert (runs["available_utc"] == runs["runtime"] + nbm.AVAILABILITY_LAG).all()
    assert runs["runtime"].max() == pd.Timestamp("2023-01-01T01:00Z")  # the boundary run is kept
    nbm.year_path(2023).write_text("garbage\n")  # an unlisted year is never opened
    dq.load_runs(run, years=[2022])
    with pytest.raises(FetchRefused, match="no longer matches"):
        dq.load_runs(run)  # the 2023 file changed since the run checked it
    texts[2023] = T.iem_text(T.short_runs(2023, days=2))
    nbm.fetch_year(2023)  # fetched again: file and manifest agree, but they are not what the run checked
    with pytest.raises(LineageError, match="differ"):
        dq.load_runs(run, years=[2022])
    with pytest.raises(LineageError, match="did not check"):
        dq.load_runs(run, years=[2024])
    v1 = SimpleNamespace(data=SimpleNamespace(params={}), info=SimpleNamespace(run_id="v1"))
    with pytest.raises(LineageError, match="checked no NBM"):
        dq.load_runs(v1)


def test_data_quality_checks_the_rows_the_loader_returns(sandbox, monkeypatch):
    """One reader (`quality.run.window_runs`): the runs the data-quality run checks are exactly the rows `load_runs`
    returns for that run, blank points kept, and a run available after the window's last hour left out of both. A
    point without its run time is never dropped silently on the way: it refuses the data-quality run (the run rule),
    so data quality never passes on runs the loader would hand on."""
    from valiwatt.forecast.guards import LookaheadError

    tmp, raw = sandbox
    serve(monkeypatch, raw)
    first, last = DEMAND_FIRST.isoformat(), data_last(2024).isoformat()
    archive = synth_runs(first, last)
    archive.loc[archive.index[[40, 41, 5000]], "tmp_f"] = np.nan  # blank points: kept by both
    after = pd.DataFrame({"runtime": pd.Timestamp("2025-01-01T04:00Z"),  # available at 06:00, after the window
                          "ftime": pd.date_range("2025-01-01T06:00Z", periods=8, freq="3h"), "tmp_f": 20.0})
    assert (after["runtime"] + dq.nbm.AVAILABILITY_LAG > data_last(2024)).all()
    store = nbm_store(archive)

    def load(years, extra=after):
        out = pd.concat([store(years), extra], ignore_index=True)  # past the store's own cut: only the reader's left
        out.attrs["identical_duplicates"] = 0
        return out

    monkeypatch.setattr(dq.nbm, "load_runs", load)
    monkeypatch.setattr(dq.nbm, "manifest", lambda: nbm_manifest(2024))
    monkeypatch.setattr(dq.nbm, "verify_files", lambda years: nbm_manifest(2024))
    checked = {}
    real_checks = dq.forecast_checks

    def spy(runs, issued, observed, *args, **kw):
        checked["runs"] = runs.copy()
        return real_checks(runs, issued, observed, *args, **kw)

    monkeypatch.setattr(dq, "forecast_checks", spy)
    assert dq.run_dq() == 0
    run = tracking.latest_run(dq.EXPERIMENT)
    loaded = dq.load_runs(run)
    pd.testing.assert_frame_equal(checked["runs"], loaded, check_exact=True)
    assert len(loaded) == run.data.metrics["nbm_bounds.points"] == len(store(range(2019, 2025)))
    assert loaded["runtime"].nunique() == run.data.metrics["nbm_runs.runs"]
    assert int(loaded["tmp_f"].isna().sum()) == run.data.metrics["nbm_bounds.blank_points"] == 3
    assert not loaded["runtime"].isin(after["runtime"]).any()

    undated = archive.head(24).copy()
    undated["runtime"] = pd.Series(pd.NaT, index=undated.index, dtype=archive["runtime"].dtype)
    monkeypatch.setattr(dq.nbm, "load_runs", lambda years: load(years, extra=undated))
    finished = len(tracking.client().search_runs([tracking.experiment_id(dq.EXPERIMENT)]))
    with pytest.raises(LookaheadError, match="no run time or no valid time"):
        dq.run_dq()
    assert len(tracking.client().search_runs([tracking.experiment_id(dq.EXPERIMENT)])) == finished  # none logged
    assert dq.load_runs(run)["runtime"].isna().sum() == len(undated)  # what the run rule refuses downstream too


def test_a_lead_at_or_above_75_hours_is_reported_against_the_two_day_argument():
    from valiwatt.forecast.features import forecast_temperature
    from valiwatt.quality.weather import forecast_checks

    assert REUSE_LEAD_H == 75
    runs = synth_runs("2021-12-29T00:00Z", "2024-01-01T00:00Z")
    days = pd.date_range("2022-01-01", "2023-12-31", freq="D")

    def longest(leads):
        extra = []
        for year, lead in leads.items():
            rt = pd.Timestamp(f"{year}-06-01T01:00Z")
            extra.append({"runtime": rt, "ftime": rt + pd.Timedelta(hours=lead), "tmp_f": 60.0})
        r = pd.concat([runs, pd.DataFrame(extra)], ignore_index=True).sort_values(["runtime", "ftime"])
        r = r.drop_duplicates(["runtime", "ftime"], keep="last").reset_index(drop=True)
        issued = forecast_temperature(r, days)
        obs = pd.Series(np.nan, index=issued.hourly.index)
        return {c.name: c for c in forecast_checks(r, issued, obs)}["nbm_runs"].metrics

    m = longest({2022: 75, 2023: 74})
    assert m["longest_lead_h"][2022] == 75 and m["longest_lead_h"][2023] == 74
    assert m["leads_at_or_above_reuse_bound"] == [2022]
    assert longest({2022: 74, 2023: 72})["leads_at_or_above_reuse_bound"] == []
    assert longest({2022: 76})["leads_at_or_above_reuse_bound"] == [2022]


# The 2025 window (docs/plan-v2.md, section 11.3.2): the revised 2019-2024 records, a finding nothing decides on.

A, B, C, D = (pd.Timestamp(t) for t in ("2021-03-01T12:00Z", "2023-07-07T07:00Z", "2020-02-02T02:00Z",
                                        "2022-05-05T05:00Z"))


def exports(raw2025: pd.DataFrame):
    """The 2024 export and a 2025 export that revises it: hour A added (the 2024 export lacked it), hour B removed,
    hour C's value changed, and hour D's duplicate record dropped (two identical records in 2024, one in 2025: the
    repaired series would not tell them apart)."""
    old = raw2025[raw2025["ts_utc"] <= DEMAND_LAST]
    old = pd.concat([old[old["ts_utc"] != A], old[old["ts_utc"] == D]], ignore_index=True)
    new = raw2025[raw2025["ts_utc"] != B].copy()
    new.loc[new["ts_utc"] == C, "mw"] += 100.0
    return old.reset_index(drop=True), new.reset_index(drop=True)


@pytest.fixture
def sandbox2025(tmp_path, monkeypatch):
    raw = isolate(tmp_path, monkeypatch, 2025)

    def serve2025(old, new):
        paths = {str(dq.hydroquebec.csv_path(2024)): old, str(dq.hydroquebec.csv_path(2025)): new}
        monkeypatch.setattr(dq.hydroquebec, "load_raw", lambda path=None: paths[str(path)])
    return tmp_path, raw, serve2025


def logged_revisions(run) -> pd.DataFrame:
    import mlflow

    tracking.client()
    text = mlflow.artifacts.load_text(f"runs:/{run.info.run_id}/{dq.REVISIONS_ARTIFACT}")
    return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)


def test_revised_2019_2024_records_are_listed_by_the_2025_data_quality_run(sandbox2025):
    tmp, raw, serve2025 = sandbox2025
    old, new = exports(raw)
    listed = dq.revised_records(old, new)
    assert sorted(zip(listed["utc"], listed["kind"])) == sorted(
        [(A.isoformat(), "added"), (B.isoformat(), "removed"), (C.isoformat(), "changed"), (D.isoformat(), "changed")])
    d = listed.set_index("utc").loc[D.isoformat()]
    assert d["old_mw"].count(";") == 1 and d["new_mw"].count(";") == 0  # two records, then one
    assert dq.revised_records(old, pd.concat([old, raw[raw["ts_utc"] > DEMAND_LAST]])).empty  # nothing revised
    assert dq.revised_records(old, old.sample(frac=1.0, random_state=0)).empty  # the export's order is no revision
    serve2025(old, new)
    assert dq.run_dq(2025) == 0
    run = tracking.latest_run(dq.EXPERIMENT)
    assert run.data.params["through"] == "2025" and run.data.params["window_last_utc"] == data_last(2025).isoformat()
    assert run.data.params["hq_revised_records"] == "4"
    logged = logged_revisions(run)
    assert sorted(zip(logged["utc"], logged["kind"])) == sorted(zip(listed["utc"], listed["kind"]))
    assert (tmp / "interim" / "demand-through-2025.parquet").exists()
    assert not (tmp / "interim" / "demand.parquet").exists()  # the 2024 window's file is never overwritten


def test_the_2025_dq_run_logs_its_revision_artifact(sandbox2025):
    tmp, raw, serve2025 = sandbox2025
    old, new = exports(raw)
    serve2025(old, new)
    assert dq.run_dq(2025) == 0
    run = tracking.latest_run(dq.EXPERIMENT)
    pd.testing.assert_frame_equal(logged_revisions(run), dq.revised_records(old, new).astype(str))
    assert run.data.params["hq_revised_records"] == str(len(dq.revised_records(old, new)))
    unchanged = raw[raw["ts_utc"] <= DEMAND_LAST].reset_index(drop=True)
    serve2025(unchanged, raw)
    assert dq.run_dq(2025) == 0
    run = tracking.latest_run(dq.EXPERIMENT)
    assert run.data.params["hq_revised_records"] == "0"
    logged = logged_revisions(run)
    assert logged.empty and list(logged.columns) == dq.REVISION_COLUMNS
