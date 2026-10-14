"""`valiwatt dq`: build the canonical series, run every data-quality check, and track it all as one MLflow run.

When a gate fails, the run is still logged (with the failing evidence) but no canonical data is written and the
command exits non-zero: downstream steps never see data the gates refused.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd

from valiwatt import __version__
from valiwatt.config import COLD_PEAK_MONTH, DESIGN_LAST, RESIDUAL_FIT_YEAR, SHIFT_2020_MONTHS, stations
from valiwatt.paths import INTERIM, rel
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
from valiwatt.quality.weather import MIN_PANEL_STATIONS, panel_checks, panel_temperature, station_series, weather_checks
from valiwatt.sources import eccc, hydroquebec
from valiwatt.sources.http import FetchRefused, sha256_file
from valiwatt.tracking import metric_key, start_run

EXPERIMENT = "valiwatt-data-quality"
LANGS = ("fr", "en")


def _verify_raw() -> tuple[dict, list[dict]]:
    """Raw files must still match the manifests written at fetch time."""
    if not hydroquebec.MANIFEST_PATH.exists():
        raise FetchRefused("no Hydro-Québec manifest: run `valiwatt fetch` first")
    hq = json.loads(hydroquebec.MANIFEST_PATH.read_text(encoding="utf-8"))
    if sha256_file(hydroquebec.CSV_PATH) != hq["sha256"]:
        raise FetchRefused("Hydro-Québec CSV no longer matches its manifest; re-fetch")
    stn = []
    for st in stations():
        path = eccc.RAW_ECCC / st.climate_id / "manifest.json"
        if not path.exists():
            raise FetchRefused(f"no manifest for {st.name}: run `valiwatt fetch` first")
        m = json.loads(path.read_text(encoding="utf-8"))
        for key, month in m["months"].items():
            y, mo = map(int, key.split("-"))
            if sha256_file(eccc.month_path(st, y, mo)) != month["sha256"]:
                raise FetchRefused(f"{st.name} {key} no longer matches its manifest; re-fetch")
        stn.append(m)
    return hq, stn


def eccc_manifests_sha256(manifests: list[dict]) -> str:
    """One digest of the stations' fetch manifests as `_verify_raw` returns them (each lists its months' SHA-256), so
    a later step can tell that the ECCC files are the ones this run checked."""
    import hashlib

    ordered = sorted(manifests, key=lambda m: str(m.get("climate_id", "")))
    return hashlib.sha256(json.dumps(ordered, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _numeric_metrics(check) -> dict:
    out = {}
    for k, v in check.metrics.items():
        if isinstance(v, bool):
            v = float(v)
        if isinstance(v, (int, float)) and v == v:
            out[metric_key(f"{check.name}.{k}")] = float(v)
    out[metric_key(f"{check.name}.passed")] = float(check.passed)
    return out


def run_dq() -> int:
    import mlflow

    hq_manifest, eccc_manifests = _verify_raw()
    panel_stations = stations()
    names = {s.key: s.centre for s in panel_stations}
    primary = next(s for s in panel_stations if s.primary)

    build = build_demand(hydroquebec.load_raw())
    checks = demand_checks(build)
    frames = {}
    for st in panel_stations:
        frames[st.key] = station_series(eccc.load_station(st))
        checks += weather_checks(st, frames[st.key])
    panel = panel_temperature(frames, MIN_PANEL_STATIONS)
    checks += panel_checks(frames, panel)
    failed = [c for c in checks if c.kind == "gate" and not c.passed]

    results = {
        "valiwatt_version": __version__,
        "design_last_utc": DESIGN_LAST,
        "sources": {
            "hydroquebec": {k: hq_manifest[k] for k in ("dataset", "export_url", "retrieved_utc", "sha256", "rows",
                                                         "dataset_modified", "license", "license_url", "publisher")},
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
        },
        "checks": {c.name: c.to_dict() for c in checks},
        "gates_passed": not failed,
    }

    from valiwatt.monitor.rules import rules_sha256

    with start_run(EXPERIMENT, "data-quality", tags={"kind": "dq", "valiwatt_version": __version__,
                                                     "rules_sha256": rules_sha256()}) as run:
        mlflow.log_params({
            "hq_sha256": hq_manifest["sha256"], "hq_rows": hq_manifest["rows"],
            "eccc_manifests_sha256": eccc_manifests_sha256(eccc_manifests),
            "hq_retrieved_utc": hq_manifest["retrieved_utc"], "hq_dataset_modified": hq_manifest["dataset_modified"],
            "stations": ",".join(s.climate_id for s in panel_stations), "primary_station": primary.climate_id,
            "design_last_utc": DESIGN_LAST.isoformat(), "min_panel_stations": MIN_PANEL_STATIONS,
        })
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
                mlflow.log_artifacts(str(tmp / "figures"), artifact_path="figures")

            INTERIM.mkdir(parents=True, exist_ok=True)
            demand_path = INTERIM / "demand.parquet"
            build.frame.to_parquet(demand_path)
            weather = pd.concat({k: f[["temp_c", "dew_point_c", "rel_hum_pct", "wind_kmh"]] for k, f in frames.items()},
                                axis=1)
            weather.columns = [f"{a}.{b}" for a, b in weather.columns]
            weather_path = INTERIM / "weather.parquet"
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
