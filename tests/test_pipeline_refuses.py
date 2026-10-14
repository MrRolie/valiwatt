"""End to end on synthetic data: a failing gate stops the chain, and a clean run renders a report."""

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs
from valiwatt import tracking
from valiwatt.config import DEMAND_FIRST, DEMAND_LAST, stations
from valiwatt.quality import run as dq
from valiwatt.report import render as rr


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    monkeypatch.setattr(dq, "INTERIM", tmp_path / "interim")
    monkeypatch.setattr(rr, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(dq, "rel", lambda p: p.name)
    manifests = (
        {"dataset": "synthetic", "export_url": "https://example.invalid", "retrieved_utc": "2026-01-01T00:00:00Z",
         "sha256": "0" * 64, "rows": 0, "dataset_modified": "2026-01-01", "license": "test", "license_url": "x",
         "publisher": "test"},
        [{"months": {"2019-01": {"retrieved_utc": "2026-01-01T00:00:00Z", "rows_without_observation": 0}}}
         for _ in stations()],
    )
    monkeypatch.setattr(dq, "_verify_raw", lambda: manifests)
    first, last = DEMAND_FIRST.isoformat(), DEMAND_LAST.isoformat()
    obs = {s.climate_id: synth_obs(first, last, seed=i) for i, s in enumerate(stations())}
    monkeypatch.setattr(dq.eccc, "load_station", lambda st: obs[st.climate_id])
    return tmp_path, synth_demand(first, last)


def test_failing_gate_stops_the_chain(sandbox, monkeypatch):
    tmp, raw = sandbox
    t = raw.loc[1000, "ts_utc"]
    broken = pd.concat([raw, pd.DataFrame({"ts_utc": [t], "mw": [raw.loc[1000, "mw"] + 900], "source_line": [0]})])
    monkeypatch.setattr(dq.hydroquebec, "load_raw", lambda: broken)
    assert dq.run_dq() == 1
    assert not (tmp / "interim" / "demand.parquet").exists()
    run = tracking.latest_run(dq.EXPERIMENT)
    assert run.data.tags["gates"] == "failed"
    with pytest.raises(rr.ReportRefused):
        rr.render(run.info.run_id)


def test_clean_run_writes_data_and_renders(sandbox, monkeypatch):
    tmp, raw = sandbox
    monkeypatch.setattr(dq.hydroquebec, "load_raw", lambda: raw)
    assert dq.run_dq() == 0
    assert (tmp / "interim" / "demand.parquet").exists()
    written = rr.render()
    text = written[0].read_text(encoding="utf-8")
    assert "Qualité et représentativité" in text
    run = tracking.latest_run(dq.EXPERIMENT, "tags.gates = 'passed'")
    assert run.info.status == "FINISHED"
    assert np.isfinite(run.data.metrics["repr.r2.panel"])


def test_failing_weather_gate_stops_the_chain(sandbox, monkeypatch):
    tmp, raw = sandbox
    monkeypatch.setattr(dq.hydroquebec, "load_raw", lambda: raw)
    original = dq.eccc.load_station

    def outage(st):
        obs = original(st).copy()
        if st.primary:
            obs.loc[1000 : 1000 + int(len(obs) * 0.10), "temp_c"] = np.nan
        return obs

    monkeypatch.setattr(dq.eccc, "load_station", outage)
    assert dq.run_dq() == 1
    assert not (tmp / "interim" / "demand.parquet").exists()
    assert tracking.latest_run(dq.EXPERIMENT).data.tags["gates"] == "failed"


def test_raw_files_that_no_longer_match_their_manifest_are_refused(tmp_path, monkeypatch):
    from valiwatt.sources import hydroquebec
    from valiwatt.sources.http import FetchRefused, sha256_file

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


def test_eccc_month_that_no_longer_matches_its_manifest_is_refused(tmp_path, monkeypatch):
    import json

    from valiwatt.sources import eccc, hydroquebec
    from valiwatt.sources.http import FetchRefused, sha256_file

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
    (month.parent / "manifest.json").write_text(json.dumps({"months": {"2019-01": {"sha256": sha256_file(month)}}}))
    dq._verify_raw()
    month.write_text("b\n")
    with pytest.raises(FetchRefused, match="no longer matches"):
        dq._verify_raw()
