"""`valiwatt validate`: walk-forward validation on 2019-2023, conformal intervals, test cases, monitoring thresholds.

Everything here sees the design window only (the guards refuse anything later). The thresholds the 2024 replay uses are
computed and logged here, before any 2024 forecast exists.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from valiwatt.config import stations
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.evaluate import (
    ALPHA,
    CALIBRATION_FOLDS,
    N_BOOT,
    by_group,
    errors,
    mean_with_ci,
    paired_difference,
    relative_conformal,
    season_of,
    weekly_stats,
)
from valiwatt.forecast.features import BASE_C, build
from valiwatt.forecast.guards import DESIGN_LAST_DAY, require_design_window, require_known_at_origin
from valiwatt.forecast.model import HOUR_BLOCKS
from valiwatt.monitor.drift import REFERENCE_HALF_WINDOW_WEEKS, weekly_drift
from valiwatt.paths import INTERIM
from valiwatt.quality.checks import jsonable
from valiwatt.quality.run import EXPERIMENT as DQ_EXPERIMENT
from valiwatt.quality.weather import MIN_PANEL_STATIONS
from valiwatt.sources.http import FetchRefused, sha256_file

EXPERIMENT = "valiwatt-validation"
FIRST_FOLD, LAST_FOLD = "2020-01", "2023-12"
# Weeks with fewer usable hours than this are left out of the weekly statistics.
MIN_WEEK_HOURS = 120
QUANTILES = {"mape_high": 0.95, "bias_low": 0.025, "bias_high": 0.975, "coverage_low": 0.05, "drift_high": 0.95}
SHIFT_MONTHS = (3, 4, 5, 6)
SHIFT_YEAR = 2020
PEAK_HOURS = 5
PEAK_MONTH = "2023-02"


class LineageError(RuntimeError):
    pass


def load_inputs():
    from valiwatt.tracking import latest_run

    dq = latest_run(DQ_EXPERIMENT, "tags.kind = 'dq' and tags.gates = 'passed' and attributes.status = 'FINISHED'")
    paths = {"demand": INTERIM / "demand.parquet", "weather": INTERIM / "weather.parquet"}
    for name, path in paths.items():
        if not path.exists():
            raise FetchRefused(f"{path.name} missing: run `valiwatt dq` first")
        if sha256_file(path) != dq.data.params.get(f"{name}_parquet_sha256"):
            raise LineageError(f"{path.name} does not match data-quality run {dq.info.run_id}; run `valiwatt dq` again")
    return dq, pd.read_parquet(paths["demand"]), pd.read_parquet(paths["weather"])


def temperature_sources(weather: pd.DataFrame) -> dict[str, pd.Series]:
    primary = next(s for s in stations() if s.primary)
    temps = weather[[c for c in weather.columns if c.endswith(".temp_c")]]
    panel = temps.mean(axis=1).where(temps.notna().sum(axis=1) >= MIN_PANEL_STATIONS)
    return {"primary": weather[f"{primary.key}.temp_c"], "panel": panel}


def design_features(demand: pd.DataFrame, temperature: pd.Series) -> pd.DataFrame:
    """The feature table cut to the design window. The guards run where the table is consumed (run_validation)."""
    f = build(demand, temperature)
    return f[f["day"] <= DESIGN_LAST_DAY]


def _quantiles(values: pd.Series) -> dict:
    return {k: float(values.quantile(q)) for k, q in QUANTILES.items() if not values.empty}


def thresholds(err_p: pd.DataFrame, covered: pd.Series, temp: pd.Series, temp_days: pd.Series,
               forecast: pd.Series, forecast_days: pd.Series, first_year: int) -> dict:
    """Per-season weekly thresholds and drift alarm levels, from validation weeks in `first_year` and after."""
    w = weekly_stats(err_p[err_p["year"] >= first_year], covered)
    w = w[w["hours"] >= MIN_WEEK_HOURS]
    perf = {}
    for season, part in w.groupby("season"):
        perf[season] = {
            "mape_high": float(part["mape"].quantile(QUANTILES["mape_high"])),
            "bias_low": float(part["rel_bias"].quantile(QUANTILES["bias_low"])),
            "bias_high": float(part["rel_bias"].quantile(QUANTILES["bias_high"])),
            "coverage_low": float(part["covered"].quantile(QUANTILES["coverage_low"])),
            "weeks": int(len(part)),
        }
    starts = sorted({pd.Timestamp(p.split("/")[0]) for p in w.index})
    drift_t = weekly_drift(temp, temp_days, starts).dropna()
    drift_f = weekly_drift(forecast, forecast_days, starts).dropna()
    return {
        "performance": perf,
        "drift": {
            "temperature_high": float(drift_t.quantile(QUANTILES["drift_high"])),
            "forecast_high": float(drift_f.quantile(QUANTILES["drift_high"])),
            "weeks_temperature": int(len(drift_t)), "weeks_forecast": int(len(drift_f)),
            "reference_half_window_weeks": REFERENCE_HALF_WINDOW_WEEKS,
        },
        "quantiles": QUANTILES,
        "min_week_hours": MIN_WEEK_HOURS,
        "first_year": first_year,
        "weekly_validation": w.reset_index(names="week").to_dict("records"),
    }


def shift_case(err: pd.DataFrame, n_boot: int) -> dict:
    """Spring 2020 against the same months of the other validation years, for one forecast."""
    months = pd.to_datetime(err["day"]).dt.month.isin(SHIFT_MONTHS)
    in_year = err["year"] == SHIFT_YEAR
    if not (months & in_year).any() or not (months & ~in_year).any():
        # The report refuses to render a case that was not evaluated.
        return {"months": list(SHIFT_MONTHS), "year": SHIFT_YEAR, "evaluable": False,
                "reason": f"the folds hold no spring {SHIFT_YEAR} hours or no other spring"}
    a = mean_with_ci(err[months & in_year], "rel_bias", n_boot)
    b = mean_with_ci(err[months & ~in_year], "rel_bias", n_boot)
    ma = mean_with_ci(err[months & in_year], "ape", n_boot)
    mb = mean_with_ci(err[months & ~in_year], "ape", n_boot)
    monthly = by_group(err[months].assign(month=pd.to_datetime(err.loc[months, "day"]).dt.to_period("M").astype(str)),
                       "month", n_boot=n_boot)
    return {"months": list(SHIFT_MONTHS), "year": SHIFT_YEAR, "evaluable": True, "bias_shift_year": a, "bias_other_years": b,
            "mape_shift_year": ma, "mape_other_years": mb,
            "separated": bool(a["ci95_high"] < b["ci95_low"] or a["ci95_low"] > b["ci95_high"]),
            "monthly": monthly}


def peak_case(f: pd.DataFrame, out: pd.DataFrame, iv: pd.DataFrame) -> dict:
    top = f["target"].dropna().nlargest(PEAK_HOURS).index
    rows = []
    for t in top:
        rows.append({
            "utc": t, "target": f.loc[t, "target"], "temp_c": f.loc[t, "temp_observed"],
            "pred_persistence": out["pred_persistence"].get(t), "pred_observed": out["pred_observed"].get(t),
            "lower": iv["lower"].get(t), "upper": iv["upper"].get(t), "covered": iv["covered"].get(t),
        })
    covered = [r["covered"] for r in rows if r["covered"] == r["covered"] and r["covered"] is not None]
    return {"hours": rows, "covered": int(sum(covered)), "evaluated": len(covered),
            "busiest_in_named_month": str(pd.Timestamp(top[0]).tz_convert("America/Toronto").strftime("%Y-%m"))
            == PEAK_MONTH, "named_month": PEAK_MONTH}


def run_validation(f_primary: pd.DataFrame, f_panel: pd.DataFrame, n_boot: int = N_BOOT,
                   first_fold: str = FIRST_FOLD, last_fold: str = LAST_FOLD) -> dict:
    """Pure: the whole validation on given feature tables. Returns results and the frames to log."""
    for name, f in (("primary features", f_primary), ("panel features", f_panel)):
        require_design_window(f, name)
        require_known_at_origin(f)
    fl = wf.folds(first_fold, last_fold)
    out = wf.run(f_primary, fl)
    out_panel = wf.run(f_panel, fl, scenarios=("persistence",))
    err = {
        "persistence": errors(out, "pred_persistence"),
        "observed": errors(out, "pred_observed"),
        "baseline": errors(out, "baseline"),
        "panel_persistence": errors(out_panel, "pred_persistence"),
    }
    iv = relative_conformal(out, "pred_persistence")
    iv_abs = relative_conformal(out, "pred_persistence", absolute=True)
    iv_obs = relative_conformal(out, "pred_observed")
    iv_season = relative_conformal(out, "pred_persistence", groups=season_of(out["day"]))

    # Load deciles on the forecast, cut points from the forecasts themselves (never the outcome).
    cuts = np.quantile(out["pred_persistence"].dropna(), np.linspace(0, 1, 11)[1:-1])
    decile = pd.Series(np.searchsorted(cuts, out["pred_persistence"].to_numpy()) + 1, index=out.index)
    hour_group = pd.cut(out["hour"], bins=[-1, 5, 9, 15, 20, 23], labels=list(HOUR_BLOCKS)).astype(str)

    perf = {}
    for name, e in err.items():
        e = e.assign(decile=decile.reindex(e.index), hour_group=hour_group.reindex(e.index))
        perf[name] = {"overall": mean_with_ci(e, "ape", n_boot) | {"bias": mean_with_ci(e, "rel_bias", n_boot)},
                      **{g: by_group(e, g, n_boot=n_boot) for g in ("season", "year", "hour", "decile")}}

    cov = {}
    for name, ivx in (("relative", iv), ("absolute", iv_abs), ("relative_by_season", iv_season),
                      ("observed_relative", iv_obs)):
        c = errors(out.loc[ivx.index], "pred_persistence" if name != "observed_relative" else "pred_observed")
        c["covered"] = ivx["covered"].reindex(c.index)
        c = c.dropna(subset=["covered"]).assign(decile=decile.reindex(c.index), hour_group=hour_group.reindex(c.index))
        cov[name] = {"overall": mean_with_ci(c, "covered", n_boot),
                     **{g: by_group(c, g, values=("covered",), n_boot=n_boot)
                        for g in ("season", "year", "hour_group", "decile")},
                     "mean_half_width_pct": float(100 * (ivx["upper"] - ivx["lower"]).div(2).div(
                         out.loc[ivx.index, "pred_persistence" if name != "observed_relative" else "pred_observed"]).mean())}

    compare = {
        "persistence_vs_baseline": paired_difference(err["persistence"], err["baseline"], n_boot=n_boot),
        "observed_vs_persistence": paired_difference(err["observed"], err["persistence"], n_boot=n_boot),
        "panel_vs_primary": paired_difference(err["panel_persistence"], err["persistence"], n_boot=n_boot),
    }
    first_interval_year = int(pd.Period(iv["fold"].min()).year)
    th = thresholds(err["persistence"], iv["covered"], f_primary["temp_observed"], f_primary["day"],
                    out["pred_persistence"], out["day"], first_interval_year)
    results = {
        "folds": {"first": first_fold, "last": last_fold, "count": len(fl), "embargo_days": wf.EMBARGO_DAYS,
                  "train_rows_last_fold": int(out["n_train_persistence"].iloc[-1])},
        "conformal": {"alpha": ALPHA, "calibration_folds": CALIBRATION_FOLDS, "first_fold": iv["fold"].min()},
        "performance": perf,
        "coverage": cov,
        "compare": compare,
        "cases": {"shift_2020": shift_case(err["persistence"], n_boot),
                  "shift_2020_observed": shift_case(err["observed"], n_boot),
                  "peak": peak_case(f_primary, out, iv)},
        "thresholds": th,
        "config": {"base_c": BASE_C, "hour_blocks": {k: [min(v), max(v)] for k, v in HOUR_BLOCKS.items()},
                   "temperature_source": "primary station", "n_boot": n_boot},
    }
    return {"results": results, "out": out, "intervals": iv}


def run_validate(n_boot: int = N_BOOT) -> int:
    import mlflow

    from valiwatt.tracking import start_run

    dq, demand, weather = load_inputs()
    src = temperature_sources(weather)
    f_primary = design_features(demand, src["primary"])
    f_panel = design_features(demand, src["panel"])
    v = run_validation(f_primary, f_panel, n_boot)
    res = v["results"]
    with start_run(EXPERIMENT, "validation", tags={"kind": "validation", "dq_run_id": dq.info.run_id,
                                                    "demand_sha256": dq.data.params["demand_parquet_sha256"],
                                                    "weather_sha256": dq.data.params["weather_parquet_sha256"]}) as run:
        mlflow.log_params({"first_fold": FIRST_FOLD, "last_fold": LAST_FOLD, "embargo_days": wf.EMBARGO_DAYS,
                           "alpha": ALPHA, "calibration_folds": CALIBRATION_FOLDS, "base_c": BASE_C,
                           "n_boot": n_boot, "min_week_hours": MIN_WEEK_HOURS,
                           **{f"q_{k}": q for k, q in QUANTILES.items()}})
        th = res["thresholds"]
        for season, t in th["performance"].items():
            mlflow.log_params({f"th_{season}_{k}": round(val, 6) for k, val in t.items()})
        mlflow.log_params({f"th_drift_{k}": v2 for k, v2 in th["drift"].items()})
        for name, p in res["performance"].items():
            mlflow.log_metrics({f"mape.{name}": p["overall"]["mean"], f"bias.{name}": p["overall"]["bias"]["mean"]})
        mlflow.log_metric("coverage.relative", res["coverage"]["relative"]["overall"]["mean"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            v["out"].to_parquet(tmp / "oos.parquet")
            v["intervals"].to_parquet(tmp / "intervals.parquet")
            mlflow.log_artifacts(str(tmp), artifact_path="frames")
        res["run_id"] = run.info.run_id
        mlflow.log_text(json.dumps(jsonable(res), indent=2, ensure_ascii=False), "results/validation.json")
        mlflow.set_tag("status", "complete")
    o = res["performance"]
    print(f"validation run {run.info.run_id}: MAPE persistence {100 * o['persistence']['overall']['mean']:.2f}%, "
          f"observed {100 * o['observed']['overall']['mean']:.2f}%, baseline {100 * o['baseline']['overall']['mean']:.2f}%; "
          f"coverage {100 * res['coverage']['relative']['overall']['mean']:.1f}%")
    return 0
