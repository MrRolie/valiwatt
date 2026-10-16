"""`valiwatt dq`: build the canonical series, run every data-quality check, and track it all as one MLflow run.

When a gate fails, the run is still logged (with the failing evidence) but no canonical data is written and the
command exits non-zero: downstream steps never see data the gates refused.

The window runs from the first hour of 2019 to the last hour of `through` (2024 by default; 2025 only once fetched
behind the v2 freeze, docs/plan-v2.md, section 11.3). Hydro-Québec records labelled after it are set aside at load and
counted, never passed to the demand layer. The NBM forecast is checked over the same window. The run with 2025 also
lists every 2019-2024 record of the new export that differs from the 2024 export: a finding nothing decides on.

Every command that computes on real data reads its raw files through `_verify_raw`, which refuses unless the
`v2-first-run` tag exists and is an ancestor of the head (docs/plan-v2.md, section 10.4).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

from valiwatt import __version__
from valiwatt.config import (
    COLD_PEAK_MONTH,
    DEMAND_FIRST,
    DESIGN_LAST,
    FIRST_RUN_TAG,
    LST_OFFSET,
    RESIDUAL_FIT_YEAR,
    SHIFT_2020_MONTHS,
    THROUGH_DEFAULT,
    THROUGH_YEARS,
    data_last,
    fetch_months,
    stations,
)
from valiwatt.forecast.features import forecast_temperature
from valiwatt.paths import INTERIM, REPO_ROOT, rel
from valiwatt.quality import figures
from valiwatt.quality.checks import jsonable
from valiwatt.quality.demand import build_demand, demand_checks
from valiwatt.quality.representativeness import (
    design_window,
    r2_difference,
    representativeness,
    residual_by_month,
    top_hours,
)
from valiwatt.quality.weather import (
    MIN_PANEL_STATIONS,
    forecast_checks,
    panel_checks,
    panel_temperature,
    station_series,
    weather_checks,
)
from valiwatt.sources import eccc, hydroquebec, nbm
from valiwatt.sources.http import FetchRefused, sha256_file
from valiwatt.tracking import metric_key, start_run

EXPERIMENT = "valiwatt-data-quality"
LANGS = ("fr", "en")
REVISIONS_ARTIFACT = "revisions/hydroquebec.csv"
REVISION_COLUMNS = ["utc", "kind", "old_mw", "new_mw"]


class FirstRunRefused(RuntimeError):
    """A command that computes on real data ran from a commit that does not descend from the `v2-first-run` tag."""


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def require_first_run() -> None:
    """The `v2-first-run` tag exists and is an ancestor of the head (or the head itself), else refuse: the tag marks
    the first real-data run of v2, so every rule change after it is disclosed (docs/plan-v2.md, section 10.4)."""
    tag = _git("rev-parse", "-q", "--verify", f"refs/tags/{FIRST_RUN_TAG}^{{commit}}")
    commit = tag.stdout.strip()
    if tag.returncode != 0 or not commit:
        raise FirstRunRefused(f"no `{FIRST_RUN_TAG}` tag: a v2 command computes on real data only from a commit that "
                              "descends from it")
    if _git("merge-base", "--is-ancestor", commit, "HEAD").returncode != 0:
        raise FirstRunRefused(f"the head does not descend from `{FIRST_RUN_TAG}` ({commit[:12]}): no real-data run "
                              "from it")


def _verify_hq(through: int = THROUGH_DEFAULT) -> dict:
    """The Hydro-Québec export of the window through `through` must still match the manifest written at fetch time."""
    path = hydroquebec.manifest_path(through)
    vintage = "" if int(through) == THROUGH_DEFAULT else f" through {through}"
    if not path.exists():
        raise FetchRefused(f"no Hydro-Québec manifest{vintage}: run `valiwatt fetch` first")
    hq = json.loads(path.read_text(encoding="utf-8"))
    if sha256_file(hydroquebec.csv_path(through)) != hq["sha256"]:
        raise FetchRefused(f"Hydro-Québec CSV{vintage} no longer matches its manifest; re-fetch")
    return hq


def _verify_raw(through: int = THROUGH_DEFAULT) -> tuple[dict, list[dict]]:
    """Raw files must still match the manifests written at fetch time. Refused first unless the head descends from
    `v2-first-run`. Each station's manifest is returned with the months of the window only, so a month fetched for a
    later window never changes what an earlier window's run checked."""
    require_first_run()
    hq = _verify_hq(through)
    window = {f"{y:04d}-{m:02d}" for y, m in fetch_months(through)}
    stn = []
    for st in stations():
        path = eccc.RAW_ECCC / st.climate_id / "manifest.json"
        if not path.exists():
            raise FetchRefused(f"no manifest for {st.name}: run `valiwatt fetch` first")
        m = json.loads(path.read_text(encoding="utf-8"))
        months = {k: v for k, v in m["months"].items() if k in window}
        for key, month in months.items():
            y, mo = map(int, key.split("-"))
            if sha256_file(eccc.month_path(st, y, mo)) != month["sha256"]:
                raise FetchRefused(f"{st.name} {key} no longer matches its manifest; re-fetch")
        stn.append({**m, "months": months})
    return hq, stn


def _verify_nbm(through: int = THROUGH_DEFAULT) -> dict:
    """The NBM manifest restricted to the window's years, every year's file verified against it."""
    return nbm.verify_files(range(nbm.FIRST_YEAR, int(through) + 1))


def eccc_manifests_sha256(manifests: list[dict]) -> str:
    """One digest of the stations' fetch manifests as `_verify_raw` returns them (each lists its months' SHA-256), so
    a later step can tell that the ECCC files are the ones this run checked."""
    import hashlib

    ordered = sorted(manifests, key=lambda m: str(m.get("climate_id", "")))
    return hashlib.sha256(json.dumps(ordered, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def window_days(through: int = THROUGH_DEFAULT) -> pd.DatetimeIndex:
    """The local-standard-time days of the window through `through`."""
    first, last = ((t - pd.Timedelta(hours=1) + LST_OFFSET).tz_convert(None).normalize()
                   for t in (DEMAND_FIRST, data_last(through)))
    return pd.date_range(first, last, freq="D")


def load_runs(dq, years=None) -> pd.DataFrame:
    """The NBM runs of `years` (None: every year the data-quality run `dq` checked), refused unless they are the files
    `dq` checked: the NBM manifest of its window must hash to its `nbm_manifest_sha256`, and each listed year's file
    must match the manifest. Only the listed years' files are opened (design code lists 2019-2023, so the 2024 file is
    never opened there). Columns of `sources.nbm.load_runs`, plus available_utc = runtime + AVAILABILITY_LAG.

    The runs of a boundary are kept (a year's file ends with the next year's 01:00 UTC run): availability, not the run
    time's year, decides who may read them."""
    from valiwatt.forecast.validate import LineageError  # validate imports this module

    expected = dq.data.params.get("nbm_manifest_sha256")
    if not expected:
        raise LineageError(f"data-quality run {dq.info.run_id} checked no NBM forecast; run `valiwatt dq` again")
    through = int(dq.data.params.get("through", THROUGH_DEFAULT))
    checked = range(nbm.FIRST_YEAR, through + 1)
    years = list(checked) if years is None else [int(y) for y in years]
    unchecked = [y for y in years if y not in checked]
    if unchecked:
        raise LineageError(f"data-quality run {dq.info.run_id} did not check the NBM files of {unchecked}")
    if nbm.manifest_sha256(nbm.for_years(nbm.manifest(), checked)) != expected:
        raise LineageError(f"the NBM files differ from the ones data-quality run {dq.info.run_id} checked")
    nbm.verify_files(years)
    runs = nbm.load_runs(years)
    runs["available_utc"] = runs["runtime"] + nbm.AVAILABILITY_LAG
    return runs


def _values(raw: pd.DataFrame, first: pd.Timestamp, last: pd.Timestamp) -> dict:
    """Each UTC label's raw values, sorted (a blank as None), on the labels from `first` to `last`."""
    r = raw[(raw["ts_utc"] >= first) & (raw["ts_utc"] <= last)]
    out: dict = {}
    for t, v in zip(r["ts_utc"], r["mw"]):
        out.setdefault(pd.Timestamp(t), []).append(None if pd.isna(v) else float(v))
    return {t: tuple(sorted(v, key=lambda x: (x is None, x or 0.0))) for t, v in out.items()}


def _shown(values) -> str:
    return "" if values is None else ";".join("" if v is None else repr(v) for v in values)


def revised_records(old: pd.DataFrame, new: pd.DataFrame, first: pd.Timestamp = DEMAND_FIRST,
                    last: pd.Timestamp | None = None) -> pd.DataFrame:
    """Every 2019-2024 record of the `new` export that differs from the `old` one, by UTC label, on the raw records as
    `hydroquebec.load_raw` parses them (label and value, before any repair, mask, bounds or spike score). A label is
    added (only in the new export), removed (only in the old one) or changed (the sorted list of its values differs).
    Columns utc, kind, old_mw, new_mw (a label's values joined by ";", a blank record shown empty). Pure."""
    last = data_last(THROUGH_DEFAULT) if last is None else last
    a, b = _values(old, first, last), _values(new, first, last)
    rows = []
    for t in sorted(set(a) | set(b)):
        va, vb = a.get(t), b.get(t)
        if va == vb:
            continue
        kind = "added" if va is None else "removed" if vb is None else "changed"
        rows.append({"utc": t.isoformat(), "kind": kind, "old_mw": _shown(va), "new_mw": _shown(vb)})
    return pd.DataFrame(rows, columns=REVISION_COLUMNS)


def _numeric_metrics(check) -> dict:
    out = {}
    for k, v in check.metrics.items():
        if isinstance(v, bool):
            v = float(v)
        if isinstance(v, (int, float)) and v == v:
            out[metric_key(f"{check.name}.{k}")] = float(v)
    out[metric_key(f"{check.name}.passed")] = float(check.passed)
    return out


def run_dq(through: int = THROUGH_DEFAULT) -> int:
    import mlflow

    through = int(through)
    if through not in THROUGH_YEARS:
        raise ValueError(f"no data window ends with {through}; the windows end with {list(THROUGH_YEARS)}")
    window_last = data_last(through)
    hq_manifest, eccc_manifests = _verify_raw(through)
    nbm_manifest = _verify_nbm(through)
    panel_stations = stations()
    names = {s.key: s.centre for s in panel_stations}
    primary = next(s for s in panel_stations if s.primary)

    raw = hydroquebec.load_window(through)
    after_window = int(raw.attrs.get("after_window", 0))
    build = build_demand(raw, DEMAND_FIRST, window_last)
    checks = demand_checks(build)
    frames = {}
    for st in panel_stations:
        frames[st.key] = station_series(eccc.load_station(st, through), DEMAND_FIRST, window_last)
        checks += weather_checks(st, frames[st.key])
    panel = panel_temperature(frames, MIN_PANEL_STATIONS)
    checks += panel_checks(frames, panel)

    # The forecast: every run available by the window's end, fed by the run rule to every day of the window.
    runs = nbm.load_runs(range(nbm.FIRST_YEAR, through + 1))
    duplicates = int(runs.attrs.get("identical_duplicates", 0))
    runs = runs[runs["runtime"] + nbm.AVAILABILITY_LAG <= window_last].reset_index(drop=True)
    runs.attrs["identical_duplicates"] = duplicates
    issued = forecast_temperature(runs, window_days(through))
    checks += forecast_checks(runs, issued, frames[primary.key]["temp_c"])

    revisions = baseline = None
    if through > THROUGH_DEFAULT:
        baseline = _verify_hq(THROUGH_DEFAULT)
        revisions = revised_records(hydroquebec.load_window(THROUGH_DEFAULT), raw)
    failed = [c for c in checks if c.kind == "gate" and not c.passed]

    results = {
        "valiwatt_version": __version__,
        "design_last_utc": DESIGN_LAST,
        "window": {"through": through, "first_utc": DEMAND_FIRST, "last_utc": window_last,
                   "hq_records_after_window": after_window},
        "sources": {
            "hydroquebec": {k: hq_manifest[k] for k in ("dataset", "export_url", "retrieved_utc", "sha256", "rows",
                                                         "dataset_modified", "license", "license_url", "publisher")}
            | {k: hq_manifest.get(k) for k in ("count_url", "where")},
            "eccc": [
                {
                    "key": st.key, "name": st.name, "centre": st.centre, "climate_id": st.climate_id,
                    "station_id": st.station_id, "primary": st.primary, "reason": st.reason, "reason_fr": st.reason_fr,
                    "months": len(m["months"]),
                    "first_retrieved_utc": min(x["retrieved_utc"] for x in m["months"].values()),
                    "last_retrieved_utc": max(x["retrieved_utc"] for x in m["months"].values()),
                    "rows_without_observation": sum(x.get("rows_without_observation", 0) for x in m["months"].values()),
                }
                for st, m in zip(panel_stations, eccc_manifests)
            ],
            "nbm": {"url": nbm.URL, "station": nbm.STATION, "model": nbm.MODEL,
                    "paired_climate_id": nbm.PAIRED_CLIMATE_ID,
                    "availability_lag_h": nbm.AVAILABILITY_LAG / pd.Timedelta(hours=1),
                    "years": {y: {k: e.get(k) for k in ("retrieved_utc", "sha256", "rows", "runs", "first_runtime",
                                                        "last_runtime", "blank_tmp")}
                              for y, e in nbm_manifest["years"].items()}},
        },
        "checks": {c.name: c.to_dict() for c in checks},
        "gates_passed": not failed,
    }
    if revisions is not None:
        results["revisions"] = {"hydroquebec": {
            "artifact": REVISIONS_ARTIFACT, "records": len(revisions),
            "by_kind": {k: int((revisions["kind"] == k).sum()) for k in ("added", "removed", "changed")},
            "baseline": {k: baseline.get(k) for k in ("retrieved_utc", "sha256", "rows", "dataset_modified")}}}

    from valiwatt.monitor.rules import rules_sha256

    with start_run(EXPERIMENT, "data-quality", tags={"kind": "dq", "valiwatt_version": __version__,
                                                     "rules_sha256": rules_sha256()}) as run:
        mlflow.log_params({
            "hq_sha256": hq_manifest["sha256"], "hq_rows": hq_manifest["rows"],
            "eccc_manifests_sha256": eccc_manifests_sha256(eccc_manifests),
            "hq_retrieved_utc": hq_manifest["retrieved_utc"], "hq_dataset_modified": hq_manifest["dataset_modified"],
            "stations": ",".join(s.climate_id for s in panel_stations), "primary_station": primary.climate_id,
            "design_last_utc": DESIGN_LAST.isoformat(), "min_panel_stations": MIN_PANEL_STATIONS,
            "through": through, "window_last_utc": window_last.isoformat(),
            "nbm_manifest_sha256": nbm.manifest_sha256(nbm_manifest), "hq_records_after_window": after_window,
        })
        if revisions is not None:
            mlflow.log_params({"hq_revised_records": len(revisions), "hq_revisions_baseline_sha256": baseline["sha256"]})
            mlflow.log_text(revisions.to_csv(index=False), REVISIONS_ARTIFACT)
        for c in checks:
            mlflow.log_metrics(_numeric_metrics(c))

        if not failed:
            demand_design = design_window(build.frame)
            temps = {k: design_window(f["temp_c"]) for k, f in frames.items()}
            temps["panel"] = design_window(panel)
            rep = representativeness(demand_design, temps)
            daily = rep.pop("daily")
            rep["r2_difference"] = [r2_difference(daily, primary.key, "panel", winter_only=w) for w in (False, True)]
            resid_source = "panel"
            resid = residual_by_month(daily, resid_source, RESIDUAL_FIT_YEAR)
            yearly = resid.groupby(resid.index.str[:4]).apply(
                lambda g: pd.Series({"mean_vs_fit_year_pp": (g["vs_fit_year_pp"] * g["days"]).sum() / g["days"].sum(),
                                     "months_above": int((g["vs_fit_year_pp"] > 0).sum()), "months": len(g)}))
            rep["residual_by_month"] = {"source": resid_source, "fit_year": RESIDUAL_FIT_YEAR,
                                        "shift_months": list(SHIFT_2020_MONTHS),
                                        "months": resid.reset_index(names="month").to_dict("records"),
                                        "years": yearly.reset_index(names="year").to_dict("records")}
            rep["top_hours"] = top_hours(build.frame, frames[primary.key]["temp_c"])
            rep["cold_peak_month"] = COLD_PEAK_MONTH
            rep["design_first_year"] = int(pd.Timestamp(rep["first_day"]).year)
            rep["design_last_year"] = int(pd.Timestamp(rep["last_day"]).year)
            for r in rep["r2_difference"]:
                tag = "winter" if r["winter_only"] else "year"
                mlflow.log_metrics({f"repr.r2_diff.{tag}": r["difference"], f"repr.r2_diff.{tag}.ci95_low": r["ci95_low"],
                                    f"repr.r2_diff.{tag}.ci95_high": r["ci95_high"]})
            results["representativeness"] = jsonable(rep)
            for r in rep["by_source"]:
                mlflow.log_metrics({f"repr.r2.{r['source']}": r["r2"], f"repr.r2_winter.{r['source']}": r["r2_winter"]})

            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                for lang in LANGS:
                    d = tmp / "figures" / lang
                    figures.daily_demand(daily, lang, d / "daily_demand.png")
                    figures.heating_curve(daily, primary.key, primary.centre, lang, d / "heating_curve.png")
                    figures.r2_by_source(rep["by_source"], names, lang, d / "r2_by_source.png")
                    figures.residual_by_month(resid, lang, d / "residual_by_month.png")
                    figures.forecast_feeding(issued.record, lang, d / figures.FORECAST_FEEDING)
                mlflow.log_artifacts(str(tmp / "figures"), artifact_path="figures")

            INTERIM.mkdir(parents=True, exist_ok=True)
            # The 2024 window keeps v1's files, which design code reads; a later window writes its own beside them.
            suffix = "" if through == THROUGH_DEFAULT else f"-through-{through}"
            demand_path = INTERIM / f"demand{suffix}.parquet"
            build.frame.to_parquet(demand_path)
            weather = pd.concat({k: f[["temp_c", "dew_point_c", "rel_hum_pct", "wind_kmh"]] for k, f in frames.items()},
                                axis=1)
            weather.columns = [f"{a}.{b}" for a, b in weather.columns]
            weather_path = INTERIM / f"weather{suffix}.parquet"
            weather.to_parquet(weather_path)
            results["canonical"] = {
                "demand": {"file": rel(demand_path), "sha256": sha256_file(demand_path)},
                "weather": {"file": rel(weather_path), "sha256": sha256_file(weather_path)},
            }
            mlflow.log_params({"demand_parquet_sha256": results["canonical"]["demand"]["sha256"],
                               "weather_parquet_sha256": results["canonical"]["weather"]["sha256"]})

        results["run_id"] = run.info.run_id
        mlflow.log_text(json.dumps(jsonable(results), indent=2, ensure_ascii=False), "results/dq_results.json")
        # Set last, so a run that crashed part-way never carries a "passed" tag.
        mlflow.set_tag("gates", "passed" if not failed else "failed")

    if failed:
        print("DATA-QUALITY GATES FAILED (run logged, no canonical data written):")
        for c in failed:
            print(f"  {c.name}: {c.metrics}")
        return 1
    print(f"data quality: all {sum(c.kind == 'gate' for c in checks)} gates passed; run {run.info.run_id}")
    return 0
