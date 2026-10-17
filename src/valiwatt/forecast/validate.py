"""`valiwatt validate`: walk-forward validation on 2019-2023, conformal intervals, test cases, monitoring thresholds.

Everything here sees the design window only (the guards refuse anything later). The thresholds the 2024 replay uses are
computed and logged here, before any 2024 forecast exists. Each fit, forecast and score sees the demand as the replay
would at that moment (`features.AsOf`), rebuilt from the raw records.

v2 (`docs/plan-v2.md`, sections 3 to 8): every model of `model.MODELS` is fitted in every fold, each on its own usable
rows; the production model (`model.PRODUCTION`, the GAM on the NBM forecast) feeds the intervals, the load deciles, the
test cases, the thresholds and the drift alarm levels; the comparison models are scored beside it and set nothing. The
forecast input is read from its availability only: design code opens the run files of DESIGN_YEARS and refuses a run
available after the design window (`forecast_inputs`). The decision rule's contrasts and verdicts run on the 2020-2023
out-of-sample hours, labelled in sample for the choice of the family and the input (config.IN_SAMPLE_LABEL).
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from valiwatt.config import DEMAND_FIRST, DESIGN_LAST, IN_SAMPLE_LABEL, LST_OFFSET, stations
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.evaluate import (
    ALPHA,
    CALIBRATION_FOLDS,
    COLD_QUANTILE,
    MIN_COLD_HOURS,
    MIN_COLD_WEEKS,
    N_BOOT,
    PRINT_DECIMALS_PP,
    SEASONS,
    by_group,
    cold_threshold,
    contrasts,
    errors,
    mean_with_ci,
    paired_difference,
    relative_conformal,
    season_of,
    verdict,
    weekly_stats,
)
from valiwatt.forecast.features import BASE_C, FORECAST_LAGS, AsOf, IssuedForecast, forecast_temperature
from valiwatt.forecast.guards import DESIGN_LAST_DAY, LookaheadError, require_design_window, require_known_at_origin
from valiwatt.forecast.model import (
    COMPARATORS,
    DEGREE,
    EXTRAPOLATION,
    HOUR_BLOCKS,
    INCLUDE_BIAS,
    KNOTS,
    MODELS,
    N_KNOTS,
    PRODUCTION,
    RIDGE_ALPHA,
    design,
    usable_rows,
)
from valiwatt.monitor.drift import REFERENCE_HALF_WINDOW_WEEKS, weekly_drift
from valiwatt.monitor.replay import day_verdicts as replay_day_verdicts
from valiwatt.monitor.rules import MIN_WEEK_HOURS, QUANTILES, WEEK_DAYS, not_judged_because, rules_sha256
from valiwatt.paths import INTERIM
from valiwatt.quality.checks import jsonable
from valiwatt.quality.run import EXPERIMENT as DQ_EXPERIMENT
from valiwatt.quality.weather import MIN_PANEL_STATIONS, usable_temperature
from valiwatt.sources.http import FetchRefused, sha256_file
from valiwatt.sources.nbm import AVAILABILITY_LAG, MAX_RUNS_BACK, SENSITIVITY_LAG

EXPERIMENT = "valiwatt-validation"
FIRST_FOLD, LAST_FOLD = "2020-01", "2023-12"
SHIFT_MONTHS = (3, 4, 5, 6)
SHIFT_YEAR = 2020
PEAK_HOURS = 5
PEAK_MONTH = "2023-02"
# The NBM run files design code may open (plan section 3.3): an explicit list, never a pattern. The 2023 file ends
# with a run of 2024-01-01 01:00 UTC, available before the end of the design window; the 2024 file is never opened.
DESIGN_YEARS = tuple(range(2019, 2024))
# The interval forms each model's own scores calibrate (plan section 7): the production model and v1 as shipped carry
# v1's relative form and its two sensitivities; the observed-weather bounds the relative form.
INTERVAL_FORMS = {PRODUCTION: ("relative", "absolute", "relative_by_season"),
                  "persistence": ("relative", "absolute", "relative_by_season"),
                  "gam_observed": ("relative",), "observed": ("relative",)}
# The three forecasts the decision rule compares (plan section 8.1).
COMPARED = (PRODUCTION, COMPARATORS["family"], COMPARATORS["shipped"])
LATE = "gam_forecast_late"  # the production model with the six-hour availability margin (plan section 3.5)


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


def load_validation(dq):
    """The latest complete validation run built on this data-quality run and these rules, and its out-of-sample
    frame. Refused when either changed since: thresholds and calibration scores would no longer match."""
    import mlflow

    from valiwatt.tracking import client, latest_run

    if dq.data.tags.get("rules_sha256") != rules_sha256():
        raise LineageError(f"data-quality run {dq.info.run_id} was made under other rules; run `valiwatt dq` again")
    run = latest_run(EXPERIMENT, "tags.kind = 'validation' and tags.status = 'complete'")
    if run.data.tags.get("dq_run_id") != dq.info.run_id:
        raise LineageError(f"validation run {run.info.run_id} was built on another data-quality run; "
                           "run `valiwatt validate` again")
    if run.data.tags.get("rules_sha256") != rules_sha256():
        raise LineageError(f"the rule files changed since validation run {run.info.run_id}; run `valiwatt validate` again")
    client()
    oos = pd.read_parquet(mlflow.artifacts.download_artifacts(run_id=run.info.run_id, artifact_path="frames/oos.parquet"))
    return run, oos


def validation_results(run) -> dict:
    import mlflow

    return json.loads(mlflow.artifacts.load_text(f"runs:/{run.info.run_id}/results/validation.json"))


def temperature_sources(weather: pd.DataFrame) -> dict[str, pd.Series]:
    """The primary station's temperature and the panel mean, each through the one bounds filter the replay reads
    temperatures through (quality.weather.usable_temperature; plan section 6.3): a station value outside the bounds is
    blank, so it never enters the panel mean, and the panel counts only the stations that hold a usable value."""
    primary = next(s for s in stations() if s.primary)
    temps = weather[[c for c in weather.columns if c.endswith(".temp_c")]].apply(usable_temperature)
    panel = temps.mean(axis=1).where(temps.notna().sum(axis=1) >= MIN_PANEL_STATIONS)
    return {"primary": usable_temperature(temps[f"{primary.key}.temp_c"]), "panel": usable_temperature(panel)}


def design_days(last: pd.Timestamp = DESIGN_LAST) -> pd.DatetimeIndex:
    """The local-standard-time days from the first demand day to the last day whose hours end by `last`."""
    first = (DEMAND_FIRST - pd.Timedelta(hours=1) + LST_OFFSET).tz_convert(None).normalize()
    last_day = (pd.Timestamp(last) - pd.Timedelta(hours=1) + LST_OFFSET).tz_convert(None).normalize()
    return pd.date_range(first, last_day, freq="D")


def forecast_inputs(runs: pd.DataFrame, days, last: pd.Timestamp = DESIGN_LAST) -> dict[str, IssuedForecast]:
    """The forecast issued for each of `days` by the run rule (features.forecast_temperature), with the assumed
    availability ("forecast", sources.nbm.AVAILABILITY_LAG) and with the six-hour margin of the sensitivity
    ("forecast_late", SENSITIVITY_LAG).

    Information time, not valid time, decides what design code may read (plan section 3.3): a run is design
    information when it is available, runtime + AVAILABILITY_LAG, by `last`, even where its points are valid after it;
    a design feed holding a run available after `last` is refused (LookaheadError). The runtime's year never decides:
    the file of 2023 ends with a run of 2024-01-01 01:00 UTC, available at 03:00, before the end of the design window,
    and it is kept. The six-hour margin only delays when a run may feed a day; it never admits a run the cut refuses."""
    runtime = pd.to_datetime(runs["runtime"], utc=True)
    late = np.array(runtime + AVAILABILITY_LAG > pd.Timestamp(last), dtype=bool)
    if "available_utc" in runs:
        late |= np.array(pd.to_datetime(runs["available_utc"], utc=True) > pd.Timestamp(last), dtype=bool)
    if late.any():
        raise LookaheadError(f"{int(late.sum())} forecast points come from runs available after {last}, the end of "
                             f"the design window (first run {runtime[late].min()}): design code may not read them")
    return {s: forecast_temperature(runs, days, lag) for s, lag in FORECAST_LAGS.items()}


def design_features(asof: AsOf, temperature: pd.Series,
                    forecasts: dict[str, IssuedForecast] | None = None) -> pd.DataFrame:
    """The test rows of the design window, each as the replay issues and scores it. The guards run where the table is
    consumed (run_validation)."""
    f = asof.issued(temperature, forecasts)
    return f[f["day"] <= DESIGN_LAST_DAY]


def validation_inputs(raw_demand: pd.DataFrame, raw_temperature: pd.DataFrame, weather: pd.DataFrame,
                      last: pd.Timestamp = DESIGN_LAST, runs: pd.DataFrame | None = None) -> dict:
    """What `valiwatt validate` runs `run_validation` on, from the raw records (monitor/run.py load_raw) and the weather
    table: for each temperature source, the test rows as the replay issues and scores them and the training table as
    of a clock, and the replay's day-level verdict on each design day. last: the last record loaded.

    runs: the NBM runs of DESIGN_YEARS (quality.run.load_runs). Given, the primary tables carry the forecast issued for
    each design day (`forecast_inputs`, both margins) and one more key is returned, "forecast_record": each margin's
    record of which run fed each day, or why none did. The panel stays v1's six-station sensitivity on yesterday's
    temperature. Every key returned is a parameter of `run_validation`."""
    asof = AsOf(raw_demand, last=last)
    src = temperature_sources(weather)
    forecasts = None if runs is None else forecast_inputs(runs, design_days(last), last=last)
    f_primary = design_features(asof, src["primary"], forecasts)
    last_day = (pd.Timestamp(last) - pd.Timedelta(hours=1) + LST_OFFSET).tz_convert(None).normalize()
    out = {"f_primary": f_primary, "f_panel": design_features(asof, src["panel"]),
           "train_primary": lambda clock: asof.table(clock, src["primary"], forecasts),
           "train_panel": lambda clock: asof.table(clock, src["panel"]),
           "day_verdicts": replay_day_verdicts(raw_demand, raw_temperature, f_primary["day"].min(), last_day,
                                               runs=runs)}
    if forecasts is not None:
        out["forecast_record"] = {s: issued.record for s, issued in forecasts.items()}
    return out


def _quantiles(values: pd.Series) -> dict:
    return {k: float(values.quantile(q)) for k, q in QUANTILES.items() if not values.empty}


def thresholds(err_p: pd.DataFrame, covered: pd.Series, temp: pd.Series, temp_days: pd.Series,
               forecast: pd.Series, forecast_days: pd.Series, first_year: int, last_year: int,
               day_verdicts: dict) -> dict:
    """Per-season weekly thresholds and drift alarm levels, from the validation weeks of first_year..last_year.

    err_p, covered, forecast: the production model's errors, interval coverage and forecasts; temp: the temperature
    the drift monitor watches (v2: the issued forecast temperature, the model's own input; v1: the station).

    Weeks are labelled by the year of their first day; a week straddling New Year belongs to the earlier year and its
    days in the next year stay out (the cut is on days, so no hour after last_year is read).

    Only the weeks the replay would judge enter: the replay's predicate (rules.not_judged_because) is applied to each
    week, with its days inside first_year..last_year, its days that fail the replay's day-level data quality
    (`day_verdicts`: local standard day -> its problems, from monitor/replay.py day_verdicts) and its scored hours. A
    week that is set aside is listed with why under "excluded_weeks". A day of a week without a verdict is refused:
    without it the week's population cannot be checked.

    Unlike an interval's half-width, fixed at its model's fit clock (evaluate.calibration_rows), the thresholds for
    replaying Y are first read when the replay judges its first week, after every day of last_year is scored."""
    e = err_p[(err_p["year"] >= first_year) & (err_p["year"] <= last_year)]
    w = weekly_stats(e, covered)
    first_day, last_day = pd.Timestamp(f"{first_year}-01-01"), pd.Timestamp(f"{last_year}-12-31")
    verdicts = {pd.Timestamp(d): p for d, p in day_verdicts.items()}
    judged, excluded = [], []
    for label, hours in w["hours"].items():
        start = pd.Timestamp(label.split("/")[0])
        inside = pd.date_range(max(start, first_day), min(start + pd.Timedelta(days=WEEK_DAYS - 1), last_day))
        unknown = [d for d in inside if d not in verdicts]
        if unknown:
            raise LineageError(f"week {label}: no day-level data-quality verdict for {unknown[0].date()}; the weeks "
                               "that calibrate the thresholds cannot be checked")
        failed = [d for d in inside if verdicts[d]]
        why = not_judged_because(len(inside), len(failed), int(hours))
        judged.append(not why)
        if why:
            excluded.append({"week": label, "first_day": start, "why": why, "days": len(inside), "hours": int(hours),
                             "failed_days": [{"day": d, "problems": sorted({p["kind"] for p in verdicts[d]})}
                                             for d in failed]})
    w = w[judged]
    temp, temp_days = temp[pd.to_datetime(temp_days) <= last_day], temp_days[pd.to_datetime(temp_days) <= last_day]
    keep = pd.to_datetime(forecast_days) <= last_day
    forecast, forecast_days = forecast[keep], forecast_days[keep]
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
    drift_t = weekly_drift(temp, temp_days, starts, MIN_WEEK_HOURS).dropna()
    drift_f = weekly_drift(forecast, forecast_days, starts, MIN_WEEK_HOURS).dropna()
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
        "week_days": WEEK_DAYS,
        "excluded_weeks": excluded,
        "first_year": first_year,
        "last_year": last_year,
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


def peak_case(f: pd.DataFrame, out: pd.DataFrame, iv: pd.DataFrame, keys=("persistence", "observed"),
              forecast_temperature: bool = False) -> dict:
    """The busiest hours of the design years by target, with each model's forecast (`keys`) and the production model's
    interval (`iv`); v2 adds the forecast temperature the production model read (`forecast_temperature`)."""
    top = f["target"].dropna().nlargest(PEAK_HOURS).index
    rows = []
    for t in top:
        row = {"utc": t, "target": f.loc[t, "target"], "temp_c": f.loc[t, "temp_observed"]}
        if forecast_temperature:
            row["temp_forecast"] = f.loc[t, "temp_forecast"]
        row |= {f"pred_{k}": out[f"pred_{k}"].get(t) for k in keys}
        row |= {"lower": iv["lower"].get(t), "upper": iv["upper"].get(t), "covered": iv["covered"].get(t)}
        rows.append(row)
    covered = [r["covered"] for r in rows if r["covered"] == r["covered"] and r["covered"] is not None]
    return {"hours": rows, "covered": int(sum(covered)), "evaluated": len(covered),
            "busiest_in_named_month": str(pd.Timestamp(top[0]).tz_convert("America/Toronto").strftime("%Y-%m"))
            == PEAK_MONTH, "named_month": PEAK_MONTH}


def design_cold_c(f_primary: pd.DataFrame) -> float:
    """cold_c (plan section 6.2): evaluate.cold_threshold of the NBM forecast temperature of every design hour that
    has one, 2019-2023, test rows of every fold and the hours before the first fold alike. Inputs only: no target, no
    forecast of demand, no hour after the design window."""
    rows = f_primary[f_primary["day"] <= DESIGN_LAST_DAY]
    return cold_threshold(rows["temp_forecast"])


def _deciles(pred: pd.Series, exact_v1: bool) -> pd.Series:
    """Load deciles on the production forecast, cut points from the forecasts themselves (never the outcome). v1's
    form gives an hour without a forecast the top decile (it never mattered: its own errors drop those hours); v2's
    leaves it without a decile, since the comparison models forecast hours the production model does not."""
    cuts = np.quantile(pred.dropna(), np.linspace(0, 1, 11)[1:-1])
    decile = pd.Series(np.searchsorted(cuts, pred.to_numpy()) + 1, index=pred.index)
    if exact_v1:
        return decile
    return decile.astype("Int64").where(pred.notna())


def _coverage(out: pd.DataFrame, ivx: pd.DataFrame, pred_col: str, decile: pd.Series, hour_group: pd.Series,
              n_boot: int) -> dict:
    c = errors(out.loc[ivx.index], pred_col)
    c["covered"] = ivx["covered"].reindex(c.index)
    c = c.dropna(subset=["covered"]).assign(decile=decile.reindex(c.index), hour_group=hour_group.reindex(c.index))
    return {"overall": mean_with_ci(c, "covered", n_boot),
            **{g: by_group(c, g, values=("covered",), n_boot=n_boot) for g in ("season", "year", "hour_group", "decile")},
            "mean_half_width_pct": float(100 * (ivx["upper"] - ivx["lower"]).div(2).div(out.loc[ivx.index, pred_col]).mean())}


def _intervals(out: pd.DataFrame, key: str, form: str) -> pd.DataFrame:
    col = f"pred_{key}"
    if form == "relative":
        return relative_conformal(out, col)
    if form == "absolute":
        return relative_conformal(out, col, absolute=True)
    return relative_conformal(out, col, groups=season_of(out["day"]))


def _why(f: pd.DataFrame, out: pd.DataFrame, key: str, forecast_record: dict) -> pd.Series:
    """Why model `key` has no forecast for each out-of-sample hour ("" where it has one): the day's forecast-record
    reason when its forecast input is blank (no run fed the day), or the input that is missing (the temperature, a
    demand lag whose hour was unusable or absent), joined by ", "."""
    _, scenario = MODELS[key]
    rows = f.reindex(out.index)
    temp = f"temp_{scenario}"
    if scenario in FORECAST_LAGS:
        reason = rows["day"].map(forecast_record[scenario]["reason"]).fillna("")
        temp_reason = reason.where(reason != "", temp)
    else:
        temp_reason = pd.Series(temp, index=out.index)
    why = pd.Series("", index=out.index)
    for missing, label in ((rows[temp].isna(), temp_reason), (rows["lag24"].isna(), "lag24"),
                           (rows["lag168"].isna(), "lag168")):
        label = label if isinstance(label, pd.Series) else pd.Series(label, index=out.index)
        why = why.where(~missing, why.where(why == "", why + ", ") + label)
    return why.where(out[f"pred_{key}"].isna(), "")


def _lacking(frame: pd.DataFrame) -> dict:
    """The out-of-sample hours left out of the common set of hours, by reason (plan section 5): no usable target, and
    for each compared model, why it has no forecast."""
    out = {"oos_hours": int(len(frame)), "no_target": int(frame["target"].isna().sum())}
    for key in COMPARED:
        why = frame.loc[frame[f"pred_{key}"].isna(), f"why_{key}"]
        out[key] = {str(k): int(v) for k, v in why.value_counts().sort_index().items()}
    return out


def _training_ranges(train_at: Callable[[pd.Timestamp], pd.DataFrame] | None, f: pd.DataFrame, fl: list,
                     scenario: str, ranges: dict) -> Callable[[pd.Timestamp], pd.DataFrame]:
    """A train_at for walkforward.run that also records, per fold, the minimum and maximum temperature of the rows the
    production model trains on (its usable rows: model.usable_rows of its design and target, the rows its knots are
    placed on). Without train_at, the training rows come from f, as in walkforward.run."""
    by_clock = {fold.fit_clock: fold for fold in fl}

    def at(clock: pd.Timestamp) -> pd.DataFrame:
        table = f if train_at is None else train_at(clock)
        fold = by_clock[clock]
        train, _ = wf.split(table, fold)
        ok = usable_rows(design(train, scenario), train["target"])
        t = train.loc[ok, f"temp_{scenario}"]
        ranges[str(fold.test_month)] = (float(t.min()), float(t.max())) if len(t) else (float("nan"), float("nan"))
        return table
    return at


def _extrapolation(out: pd.DataFrame, f: pd.DataFrame, ranges: dict, key: str) -> list[dict]:
    """Per fold: the production model's training temperature range, and its forecast hours whose input lies outside
    it (plan section 4.6), each with its APE when the hour has a target."""
    _, scenario = MODELS[key]
    temp = f[f"temp_{scenario}"].reindex(out.index)
    pred = out[f"pred_{key}"]
    ape = ((out["target"] - pred).abs() / out["target"]).where(out["target"].notna() & pred.notna())
    rows = []
    for fold, part in out.groupby("fold", sort=True):
        lo, hi = ranges[fold]
        idx = part.index[pred.loc[part.index].notna().to_numpy()]
        t = temp.loc[idx]
        below, above = idx[(t < lo).to_numpy()], idx[(t > hi).to_numpy()]
        outside = below.append(above).sort_values()
        scored = ape.loc[outside].dropna()
        rows.append({"fold": fold, "train_min_c": lo, "train_max_c": hi, "hours": int(len(idx)),
                     "hours_below": int(len(below)), "hours_above": int(len(above)),
                     "mape_outside": float(scored.mean()) if len(scored) else None,
                     "outside": [{"utc": u, "temp_c": float(temp.loc[u]), "ape": ape.loc[u]} for u in outside]})
    return rows


def _forecast_input(record: pd.DataFrame, f: pd.DataFrame) -> dict:
    """What fed the production model's input on the design days (plan sections 3.2 and 3.4): the days no run fed, with
    why; the rank and lead counts; the feeding run's UTC hour by year; and the forecast minus the ECCC observation at
    the target hour, by year and season, on the design hours."""
    record = record[pd.DatetimeIndex(record.index) <= DESIGN_LAST_DAY]
    fed = record["reason"] == ""
    r = record[fed]
    hour_by_year = {int(y): {int(h): int(n) for h, n in g.value_counts().sort_index().items()}
                    for y, g in pd.Series(pd.DatetimeIndex(r["runtime"]).hour,
                                          index=pd.DatetimeIndex(r.index).year).groupby(level=0)}
    err = (f["temp_forecast"] - f["temp_observed"]).dropna()
    day = pd.to_datetime(f.loc[err.index, "day"])
    by_year_season: dict = {}
    for (y, s), g in err.groupby([day.dt.year.to_numpy(), day.dt.month.map(SEASONS).to_numpy()]):
        by_year_season.setdefault(int(y), {})[s] = {"mean_c": float(g.mean()), "mae_c": float(g.abs().mean()),
                                                    "hours": int(len(g))}
    return {"days": int(len(record)), "fed_days": int(fed.sum()),
            "days_not_fed": [{"day": d, "reason": why} for d, why in record.loc[~fed, "reason"].items()],
            "reasons": {str(k): int(v) for k, v in record.loc[~fed, "reason"].value_counts().items()},
            "rank_counts": {int(k): int(v) for k, v in r["rank"].value_counts().sort_index().items()},
            "lead_counts": {float(k): int(v) for k, v in r["lead_h"].value_counts().sort_index().items()},
            "run_hour_by_year": hour_by_year, "error_vs_observed": by_year_season,
            "availability_lag_h": AVAILABILITY_LAG / pd.Timedelta(hours=1)}


def run_validation(f_primary: pd.DataFrame, f_panel: pd.DataFrame, n_boot: int = N_BOOT,
                   first_fold: str = FIRST_FOLD, last_fold: str = LAST_FOLD,
                   train_primary: Callable[[pd.Timestamp], pd.DataFrame] | None = None,
                   train_panel: Callable[[pd.Timestamp], pd.DataFrame] | None = None, *, day_verdicts: dict,
                   models=None, forecast_record: dict | None = None) -> dict:
    """Pure: the whole validation on given feature tables. Returns results and the frames to log.

    train_primary, train_panel: the feature tables as seen at a clock, which each fold trains on at its fit clock
    (`walkforward.run`); without them, training rows come from the test tables.
    day_verdicts: the replay's day-level data-quality verdict on each design day (monitor/replay.py day_verdicts); the
    thresholds are calibrated only on the weeks the replay would judge.
    models: None is v1's validation exactly (v1's least squares on the observed and persistence inputs, persistence
    the production model). The keys of model.MODELS, all of them, are v2's: each fitted in every fold, the production
    model (model.PRODUCTION) setting the intervals that feed the monitor, the deciles, the cases, the thresholds and the
    drift alarm levels; it needs forecast_record (validation_inputs), each margin's record of which run fed each day."""
    v2 = models is not None
    if v2:
        models = tuple(models)
        if sorted(models) != sorted(MODELS):
            raise ValueError(f"v2's validation fits every model of model.MODELS, {sorted(MODELS)}; got {sorted(models)}")
        if forecast_record is None or set(forecast_record) != set(FORECAST_LAGS):
            raise ValueError("v2's validation needs the forecast record of each margin (validation_inputs with runs): "
                             "without it no hour can say why the production model has no forecast")
    prod = PRODUCTION if v2 else "persistence"
    for name, f in (("primary features", f_primary), ("panel features", f_panel)):
        require_design_window(f, name)
        require_known_at_origin(f)
    fl = wf.folds(first_fold, last_fold)
    ranges: dict = {}
    if v2:
        out = wf.run(f_primary, fl, train_at=_training_ranges(train_primary, f_primary, fl, MODELS[prod][1], ranges),
                     models=models)
    else:
        out = wf.run(f_primary, fl, train_at=train_primary)
    out_panel = wf.run(f_panel, fl, scenarios=("persistence",), train_at=train_panel)
    keys = models if v2 else ("persistence", "observed")
    err = {k: errors(out, f"pred_{k}") for k in keys}
    err |= {"baseline": errors(out, "baseline"), "panel_persistence": errors(out_panel, "pred_persistence")}

    if v2:
        ivs = {k: {form: _intervals(out, k, form) for form in forms} for k, forms in INTERVAL_FORMS.items()}
    else:
        ivs = {"persistence": {form: _intervals(out, "persistence", form)
                               for form in ("relative", "absolute", "relative_by_season")},
               "observed": {"relative": _intervals(out, "observed", "relative")}}
    iv = ivs[prod]["relative"]

    decile = _deciles(out[f"pred_{prod}"], exact_v1=not v2)
    hour_group = pd.cut(out["hour"], bins=[-1, 5, 9, 15, 20, 23], labels=list(HOUR_BLOCKS)).astype(str)

    perf = {}
    for name, e in err.items():
        e = e.assign(decile=decile.reindex(e.index), hour_group=hour_group.reindex(e.index))
        perf[name] = {"overall": mean_with_ci(e, "ape", n_boot) | {"bias": mean_with_ci(e, "rel_bias", n_boot)},
                      **{g: by_group(e, g, n_boot=n_boot) for g in ("season", "year", "hour", "decile")}}

    by_model = {k: {form: _coverage(out, ivx, f"pred_{k}", decile, hour_group, n_boot) for form, ivx in forms.items()}
                for k, forms in ivs.items()}
    # v1's flat keys: v1's model (persistence, v1's production model) and its observed-weather bound.
    cov = {**by_model["persistence"], "observed_relative": by_model["observed"]["relative"]}
    if v2:
        cov |= by_model

    compare = {
        "persistence_vs_baseline": paired_difference(err["persistence"], err["baseline"], n_boot=n_boot),
        "observed_vs_persistence": paired_difference(err["observed"], err["persistence"], n_boot=n_boot),
        "panel_vs_primary": paired_difference(err["panel_persistence"], err["persistence"], n_boot=n_boot),
    }
    first_interval_year = int(pd.Period(iv["fold"].min()).year)
    last_year = int(pd.Period(last_fold).year)
    # The drift monitor watches the model's own input: v2's issued forecast temperature, v1's station (section 9.3).
    temp_col = "temp_forecast" if v2 else "temp_observed"
    # One set of thresholds per replay: the 2024 replay reads weeks up to 2023; the 2023 pseudo-replay up to 2022.
    th = {str(y + 1): thresholds(err[prod], iv["covered"], f_primary[temp_col], f_primary["day"],
                                 out[f"pred_{prod}"], out["day"], first_interval_year, y, day_verdicts)
          for y in range(first_interval_year, last_year + 1)}
    cases = {"shift_2020": shift_case(err[prod], n_boot)}
    if v2:
        cases["shift_2020_persistence"] = shift_case(err["persistence"], n_boot)
    cases["shift_2020_observed"] = shift_case(err["observed"], n_boot)
    cases["peak"] = peak_case(f_primary, out, iv, keys=keys, forecast_temperature=v2)
    results = {
        "folds": {"first": first_fold, "last": last_fold, "count": len(fl), "embargo_days": wf.EMBARGO_DAYS,
                  "train_rows_last_fold": int(out[f"n_train_{prod}"].iloc[-1])},
        "conformal": {"alpha": ALPHA, "calibration_folds": CALIBRATION_FOLDS, "first_fold": iv["fold"].min()},
        "performance": perf,
        "coverage": cov,
        "compare": compare,
        "cases": cases,
        "thresholds": th,
        "config": {"base_c": BASE_C, "hour_blocks": {k: [min(v), max(v)] for k, v in HOUR_BLOCKS.items()},
                   "temperature_source": "primary station", "n_boot": n_boot},
    }
    if not v2:
        return {"results": results, "out": out, "intervals": iv}

    cold_c = design_cold_c(f_primary)
    frame = out[["target", "day"] + [f"pred_{k}" for k in COMPARED]].copy()
    frame["temp_forecast"] = f_primary["temp_forecast"].reindex(out.index)
    for k in COMPARED:
        frame[f"why_{k}"] = _why(f_primary, out, k, forecast_record)
    c = contrasts(frame, cold_c, n_boot=n_boot)
    results["production"] = prod
    results["contrasts"] = c | {"label": IN_SAMPLE_LABEL}
    results["verdict"] = verdict(c) | {"label": IN_SAMPLE_LABEL}
    results["common_hours"] = {"hours": c["hours"], **_lacking(frame)}
    results["forecast_input"] = _forecast_input(forecast_record["forecast"], f_primary)
    results["extrapolation"] = _extrapolation(out, f_primary, ranges, prod)
    results["sensitivity"] = {"availability_6h": paired_difference(err[LATE], err[prod], n_boot=n_boot)}
    results["config"] |= {"cold_c": cold_c, "n_knots": N_KNOTS, "degree": DEGREE, "ridge_alpha": RIDGE_ALPHA,
                          "availability_lag_h": AVAILABILITY_LAG / pd.Timedelta(hours=1),
                          "sensitivity_lag_h": SENSITIVITY_LAG / pd.Timedelta(hours=1),
                          "models": {k: list(MODELS[k]) for k in models}, "production": prod}
    temps = f_primary.reindex(out.index)[["temp_forecast", "available_forecast", "temp_observed", "temp_persistence"]]
    oos = pd.concat([out, temps], axis=1)
    return {"results": results, "out": oos, "intervals": iv,
            "intervals_persistence": ivs["persistence"]["relative"],
            "forecast_record": forecast_record["forecast"]}


def run_validate(n_boot: int = N_BOOT) -> int:
    import mlflow

    from valiwatt.monitor.run import load_raw  # monitor/run.py imports this module
    from valiwatt.quality import run as qrun  # the one NBM reader, the files the data-quality run checked
    from valiwatt.tracking import start_run

    dq, _, weather = load_inputs()
    raw_demand, raw_temp = load_raw(dq)
    runs = qrun.load_runs(dq, years=DESIGN_YEARS)
    v = run_validation(n_boot=n_boot, models=tuple(MODELS),
                       **validation_inputs(raw_demand, raw_temp, weather, runs=runs))
    res = v["results"]
    with start_run(EXPERIMENT, "validation", tags={"kind": "validation", "dq_run_id": dq.info.run_id,
                                                    "rules_sha256": rules_sha256(),
                                                    "demand_sha256": dq.data.params["demand_parquet_sha256"],
                                                    "weather_sha256": dq.data.params["weather_parquet_sha256"],
                                                    "nbm_manifest_sha256": dq.data.params["nbm_manifest_sha256"],
                                                    "production": res["production"]}) as run:
        mlflow.log_params({"first_fold": FIRST_FOLD, "last_fold": LAST_FOLD, "embargo_days": wf.EMBARGO_DAYS,
                           "alpha": ALPHA, "calibration_folds": CALIBRATION_FOLDS, "base_c": BASE_C,
                           "n_boot": n_boot, "min_week_hours": MIN_WEEK_HOURS,
                           **{f"q_{k}": q for k, q in QUANTILES.items()}})
        # The constants fixed before any v2 run (plan sections 3, 4, 6.2 and 8), beside the values they gave.
        mlflow.log_params({"cold_c": res["config"]["cold_c"], "cold_quantile": COLD_QUANTILE,
                           "min_cold_hours": MIN_COLD_HOURS, "min_cold_weeks": MIN_COLD_WEEKS,
                           "print_decimals_pp": PRINT_DECIMALS_PP, "n_knots": N_KNOTS, "knots": KNOTS,
                           "degree": DEGREE, "extrapolation": EXTRAPOLATION, "include_bias": INCLUDE_BIAS,
                           "ridge_alpha": RIDGE_ALPHA, "availability_lag_h": res["config"]["availability_lag_h"],
                           "sensitivity_lag_h": res["config"]["sensitivity_lag_h"], "max_runs_back": MAX_RUNS_BACK,
                           "models": ",".join(res["config"]["models"]), "production": res["production"],
                           "comparators": ",".join(f"{k}={v}" for k, v in COMPARATORS.items()),
                           "design_years": ",".join(str(y) for y in DESIGN_YEARS)})
        for replay_year, th in res["thresholds"].items():
            for season, t in th["performance"].items():
                mlflow.log_params({f"th{replay_year}_{season}_{k}": round(val, 6) for k, val in t.items()})
            mlflow.log_params({f"th{replay_year}_drift_{k}": v2 for k, v2 in th["drift"].items()})
            # The weeks set aside, with why, are in results/validation.json (thresholds -> excluded_weeks).
            mlflow.log_param(f"th{replay_year}_excluded_weeks", len(th["excluded_weeks"]))
        for name, p in res["performance"].items():
            mlflow.log_metrics({f"mape.{name}": p["overall"]["mean"], f"bias.{name}": p["overall"]["bias"]["mean"]})
        mlflow.log_metric("coverage.relative", res["coverage"]["relative"]["overall"]["mean"])
        for key in INTERVAL_FORMS:
            mlflow.log_metric(f"coverage.{key}.relative", res["coverage"][key]["relative"]["overall"]["mean"])
        for name in ("shipped", "family", "input"):
            mlflow.log_metric(f"contrast.{name}", res["contrasts"][name]["difference"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            v["out"].to_parquet(tmp / "oos.parquet")
            v["intervals"].to_parquet(tmp / "intervals.parquet")
            v["intervals_persistence"].to_parquet(tmp / "intervals_persistence.parquet")
            v["forecast_record"].to_parquet(tmp / "forecast_record.parquet")
            mlflow.log_artifacts(str(tmp), artifact_path="frames")
        res["run_id"] = run.info.run_id
        mlflow.log_text(json.dumps(jsonable(res), indent=2, ensure_ascii=False), "results/validation.json")
        mlflow.set_tag("status", "complete")
    o = res["performance"]
    print(f"validation run {run.info.run_id}: MAPE {res['production']} {100 * o[res['production']]['overall']['mean']:.2f}%, "
          f"persistence {100 * o['persistence']['overall']['mean']:.2f}%, "
          f"baseline {100 * o['baseline']['overall']['mean']:.2f}%; coverage "
          f"{100 * res['coverage'][res['production']]['relative']['overall']['mean']:.1f}% "
          f"(contrasts {IN_SAMPLE_LABEL})")
    return 0
