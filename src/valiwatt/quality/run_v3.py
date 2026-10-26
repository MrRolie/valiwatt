"""`valiwatt dq-v3`: v3's data-quality run (docs/plan-v3.md, sections 3.1, 3.2, 3.3 and 9.2), beside v2's `run_dq`.

In order, before any record is parsed: v3's first-run guard (section 10.2); the byte-copy check of the raw files
against the hashes of v2's first 2024 replay's data-quality run, as the read of v2's store logged them
(quality/byte_copies.py; on a fresh clone, and only when the run is told so with `--fresh-clone`, it is not run, and
the run says so); every raw file against its manifest, as v2 checks them (quality.run._verify_manifests, _verify_nbm). Then v2's demand and station gates on the window, with the
design window ending at V3_DESIGN_LAST (the last label of 2024), and the forecast as v3 reads it:

- the noon feed (`forecast_temperature` at NOON, the covering rule with its wind clause) on every day of the window,
  from the NBS files of 2019 to the window's last year opened by an explicit list (quality.run.window_runs): gates
  nbm_bounds and nbm_coverage, finding nbm_runs (the feeding run's hour by year, the leads against the noon reuse
  bound of section 9.2), as v2's `forecast_checks` computes them at NOON;
- gate nbm_wind_bounds: every wind point inside 0 to 100 knots, both included (section 3.1);
- finding nbm_wind_clause: every day whose feeding run changes because of the wind clause (section 3.2);
- finding lag_day_temperature: the days whose lag day's mean is missing (sections 3.3 and 5);
- finding nbm_runs[midnight]: the feed at v2's origin (v2's form at midnight, section 4.2), reported only, never
  gating: its coverage, its reasons, its run hours and its leads against v2's bound.

v3 has no station panel and no design analysis of the stations: neither the panel gate nor the representativeness
block of v2's run is computed. The raw-file hashes are logged as v2's run logs them (`hq_sha256`,
`eccc_manifests_sha256`, `nbm_manifest_sha256`), with the byte-copy check's status (`byte_copies`: passed or not_run).
The run is tagged kind `dq_v3` in v2's data-quality experiment, so no v2 step ever takes it for one of v2's, and
FRESH_CLONE (true or false); its canonical series go to their own files (CANONICAL), beside v2's, never over them.

v3's validation and freezes read one data-quality run only through `require_design_run`: the window ending with 2024
(sections 3.1 and 11.2: a revised 2019-2024 record in a later vintage never reaches a design quantity), its byte copies
checked, or its run labelled a fresh clone.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
from pathlib import Path

import pandas as pd

from valiwatt import __version__
from valiwatt.config import DEMAND_FIRST, THROUGH_DEFAULT, THROUGH_YEARS, V3_DESIGN_LAST, data_last, stations
from valiwatt.forecast.features import forecast_temperature, observed_day_mean
from valiwatt.forecast.issue import MIDNIGHT, NOON
from valiwatt.paths import INTERIM, rel
from valiwatt.quality import run as qrun
from valiwatt.quality.byte_copies import check_byte_copies
from valiwatt.quality.checks import CheckResult, jsonable
from valiwatt.quality.demand import build_demand, demand_checks
from valiwatt.quality.weather import (
    forecast_checks,
    lag_day_check,
    station_series,
    weather_checks,
    wind_bounds_check,
    wind_clause_check,
)
from valiwatt.sources import eccc, hydroquebec, nbm
from valiwatt.sources.http import sha256_file

EXPERIMENT = qrun.EXPERIMENT
KIND = "dq_v3"
PASSED = f"tags.kind = '{KIND}' and tags.gates = 'passed' and attributes.status = 'FINISHED'"
RECORD_ARTIFACT = "frames/forecast_record_noon.parquet"
FRESH_CLONE = "fresh_clone"  # the tag that says the run was told it is on a fresh clone (section 3.1)
# The noon feed's covering rule without its wind clause: only to list the days the clause changes (section 3.2).
NOON_WITHOUT_WIND = dataclasses.replace(NOON, name="noon_without_wind", wind=False)


def canonical_paths(through: int) -> dict[str, Path]:
    """v3's canonical series of the window through `through`: files of their own, beside v2's."""
    suffix = "" if int(through) == THROUGH_DEFAULT else f"-through-{int(through)}"
    return {"demand": INTERIM / f"demand-v3{suffix}.parquet", "weather": INTERIM / f"weather-v3{suffix}.parquet"}


def midnight_finding(runs: pd.DataFrame, midnight, observed: pd.Series) -> CheckResult:
    """Finding nbm_runs[midnight]: v2's forecast checks on the feed at v2's origin (forecast_checks at MIDNIGHT),
    folded into one finding that never gates (its coverage share and fed days beside the nbm_runs metrics)."""
    _, coverage, runs_finding = forecast_checks(runs, midnight, observed, V3_DESIGN_LAST, issue=MIDNIGHT)
    return CheckResult("nbm_runs[midnight]", "finding", passed=True,
                       metrics={**runs_finding.metrics, "coverage": coverage.metrics,
                                "coverage_at_threshold": coverage.passed},
                       evidence=runs_finding.evidence)


def forecast_checks_v3(runs: pd.DataFrame, days, observed: pd.Series) -> tuple[list[CheckResult], dict]:
    """The forecast's gates and findings as v3 reads it, on the window's runs (quality.run.window_runs, with wsp_kt),
    its days and the primary station's temperature on the hourly grid. Returns the checks and the three feeds
    (noon, noon without the wind clause, midnight)."""
    noon = forecast_temperature(runs, days, issue=NOON)
    without = forecast_temperature(runs, days, issue=NOON_WITHOUT_WIND)
    midnight = forecast_temperature(runs, days, issue=MIDNIGHT)
    checks = forecast_checks(runs, noon, observed, V3_DESIGN_LAST, issue=NOON)
    checks += [wind_bounds_check(runs), wind_clause_check(noon, without),
               lag_day_check(observed_day_mean(observed), days), midnight_finding(runs, midnight, observed)]
    return checks, {"noon": noon, "noon_without_wind": without, "midnight": midnight}


def latest_passed():
    """The latest finished v3 data-quality run whose gates passed (for v3's later steps)."""
    from valiwatt.tracking import latest_run

    return latest_run(EXPERIMENT, PASSED)


def require_design_run(dq) -> dict:
    """The data-quality run v3's validation and freezes may read (docs/plan-v3.md, sections 3.1 and 11.2), checked
    before any file is read: the window ending with 2024 (its `through` parameter), and its byte copies checked
    (`byte_copies` passed) unless the run is labelled a fresh clone (FRESH_CLONE true). A run labelled a fresh clone is
    refused once v3's store holds a read of v2's store (v2store.recorded), as dq-v3 refuses `--fresh-clone` when a read
    exists: on a machine that has the read, the label cannot stand in for the byte-copy check. Refused (LineageError)
    otherwise, an absent parameter or tag included. Returns {"byte_copies": its status, "fresh_clone": bool}."""
    from valiwatt.forecast.validate import LineageError  # validate imports quality.run
    from valiwatt.v2store import recorded

    run_id, through = dq.info.run_id, dq.data.params.get("through")
    if through != str(THROUGH_DEFAULT):
        raise LineageError(f"data-quality run {run_id} reads the window through {through}: v3's validation and freezes "
                           f"read the 2019-{THROUGH_DEFAULT} files only (docs/plan-v3.md, sections 3.1 and 11.2)")
    fresh = dq.data.tags.get(FRESH_CLONE) == "true"
    status = dq.data.tags.get("byte_copies")
    if status != "passed" and not fresh:
        raise LineageError(f"data-quality run {run_id}: its byte-copy check is {status!r}, not passed, and the run is "
                           "not labelled a fresh clone; run `valiwatt read-v2 PATH`, then `valiwatt dq-v3` again")
    if fresh and (read := recorded()) is not None:
        raise LineageError(f"data-quality run {run_id} is labelled a fresh clone, but v3's store holds the read of v2's "
                           f"store {read['run_id']}: the byte copies can be checked here, so run `valiwatt dq-v3` "
                           "again without --fresh-clone")
    return {"byte_copies": status, "fresh_clone": fresh}


def run_dq_v3(through: int = THROUGH_DEFAULT, fresh_clone: bool = False) -> int:
    """`valiwatt dq-v3`. fresh_clone: the run is told it is on a fresh clone, holding neither v2's files nor its store
    (`--fresh-clone`): the byte-copy check is then not run, and the run says so (tag FRESH_CLONE)."""
    import mlflow

    from valiwatt.firstrun import require_first_run
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.records_v3 import current_freeze
    from valiwatt.tracking import start_run
    from valiwatt.v2store import recorded, require_own_store

    through = int(through)
    if through not in THROUGH_YEARS:
        raise ValueError(f"no data window ends with {through}; the windows end with {list(THROUGH_YEARS)}")
    require_first_run()
    require_own_store()
    freeze = current_freeze()  # v3-pre-freeze before the tag; under the tag, refused on other rules (section 10.3)
    copies = check_byte_copies(recorded(), fresh_clone=bool(fresh_clone))
    hq_manifest, eccc_manifests = qrun._verify_manifests(through)
    nbm_manifest = qrun._verify_nbm(through)
    window_last = data_last(through)
    panel_stations = stations()
    primary = next(s for s in panel_stations if s.primary)

    raw = hydroquebec.load_window(through)
    after_window = int(raw.attrs.get("after_window", 0))
    build = build_demand(raw, DEMAND_FIRST, window_last, scale_last=V3_DESIGN_LAST)
    checks = demand_checks(build, V3_DESIGN_LAST)
    frames = {}
    for st in panel_stations:
        frames[st.key] = station_series(eccc.load_station(st, through), DEMAND_FIRST, window_last)
        checks += weather_checks(st, frames[st.key], V3_DESIGN_LAST)

    years = list(range(nbm.FIRST_YEAR, through + 1))  # an explicit list, never a pattern
    runs = qrun.window_runs(years, through)
    days = qrun.window_days(through)
    forecast, feeds = forecast_checks_v3(runs, days, frames[primary.key]["temp_c"])
    checks += forecast

    revisions = baseline = None
    if through > THROUGH_DEFAULT:
        baseline = qrun._verify_hq(THROUGH_DEFAULT)
        revisions = qrun.revised_records(hydroquebec.load_window(THROUGH_DEFAULT), raw)
    failed = [c for c in checks if c.kind == "gate" and not c.passed]

    results = {
        "valiwatt_version": __version__,
        "issue": NOON.name,
        "design_last_utc": V3_DESIGN_LAST,
        "window": {"through": through, "first_utc": DEMAND_FIRST, "last_utc": window_last,
                   "hq_records_after_window": after_window, "nbm_years": years},
        "byte_copies": copies,
        "fresh_clone": bool(fresh_clone),
        "sources": {
            "hydroquebec": {k: hq_manifest.get(k) for k in ("dataset", "export_url", "retrieved_utc", "sha256", "rows",
                                                            "dataset_modified", "license", "license_url", "publisher")},
            "nbm": {"url": nbm.URL, "station": nbm.STATION, "model": nbm.MODEL,
                    "availability_lag_h": nbm.AVAILABILITY_LAG / pd.Timedelta(hours=1),
                    "reuse_lead_h": nbm.reuse_lead_h(NOON), "knots_to_kmh": 1.852,
                    "years": {y: {k: e.get(k) for k in ("retrieved_utc", "sha256", "rows", "runs", "first_runtime",
                                                        "last_runtime", "blank_tmp", "blank_wsp")}
                              for y, e in nbm_manifest["years"].items()}},
        },
        "checks": {c.name: c.to_dict() for c in checks},
        "gates_passed": not failed,
    }
    if revisions is not None:
        results["revisions"] = {"hydroquebec": {"artifact": qrun.REVISIONS_ARTIFACT, "records": len(revisions)}}

    tags = {"kind": KIND, "issue": NOON.name, "valiwatt_version": __version__, "rules_sha256": rules_sha256(),
            "byte_copies": copies["status"], FRESH_CLONE: str(bool(fresh_clone)).lower(),
            "freeze_tag": freeze["freeze_tag"], "freeze_commit": freeze["freeze_commit"]}
    with start_run(EXPERIMENT, "data-quality-v3", tags=tags) as run:
        mlflow.log_params({
            "hq_sha256": hq_manifest["sha256"], "hq_rows": hq_manifest["rows"],
            "eccc_manifests_sha256": qrun.eccc_manifests_sha256(eccc_manifests),
            "nbm_manifest_sha256": nbm.manifest_sha256(nbm_manifest),
            "hq_retrieved_utc": hq_manifest["retrieved_utc"], "hq_dataset_modified": hq_manifest["dataset_modified"],
            "stations": ",".join(s.climate_id for s in panel_stations), "primary_station": primary.climate_id,
            "design_last_utc": V3_DESIGN_LAST.isoformat(), "through": through,
            "window_last_utc": window_last.isoformat(), "hq_records_after_window": after_window,
            "nbm_years": ",".join(str(y) for y in years), "issue": NOON.name, "byte_copies": copies["status"],
            "v2_dq_run_id": copies.get("v2_dq_run_id", ""), "v2_read_run_id": copies.get("v2_read_run_id", ""),
        })
        if revisions is not None:
            mlflow.log_params({"hq_revised_records": len(revisions),
                               "hq_revisions_baseline_sha256": baseline["sha256"]})
            mlflow.log_text(revisions.to_csv(index=False), qrun.REVISIONS_ARTIFACT)
        for c in checks:
            mlflow.log_metrics(qrun._numeric_metrics(c))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / Path(RECORD_ARTIFACT).name
            feeds["noon"].record.to_parquet(path)
            mlflow.log_artifact(str(path), artifact_path=str(Path(RECORD_ARTIFACT).parent))

        if not failed:
            paths = canonical_paths(through)
            INTERIM.mkdir(parents=True, exist_ok=True)
            build.frame.to_parquet(paths["demand"])
            weather = pd.concat({k: f[["temp_c", "dew_point_c", "rel_hum_pct", "wind_kmh"]] for k, f in frames.items()},
                                axis=1)
            weather.columns = [f"{a}.{b}" for a, b in weather.columns]
            weather.to_parquet(paths["weather"])
            results["canonical"] = {k: {"file": rel(p), "sha256": sha256_file(p)} for k, p in paths.items()}
            mlflow.log_params({f"{k}_parquet_sha256": v["sha256"] for k, v in results["canonical"].items()})

        results["run_id"] = run.info.run_id
        mlflow.log_text(json.dumps(jsonable(results), indent=2, ensure_ascii=False), "results/dq_results.json")
        mlflow.set_tag("gates", "passed" if not failed else "failed")  # last: a crashed run never says passed

    if failed:
        print("V3 DATA-QUALITY GATES FAILED (run logged, no canonical data written):")
        for c in failed:
            print(f"  {c.name}: {c.metrics}")
        return 1
    print(f"v3 data quality: all {sum(c.kind == 'gate' for c in checks)} gates passed; byte copies "
          f"{copies['status']}; run {run.info.run_id}")
    return 0

