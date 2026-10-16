"""ECCC station observations: index, bounds, coverage and spikes per station, over the demand window; and the NBM
forecast's gates and findings (docs/plan-v2.md, section 3.4).

`usable_temperature` is the one bounds filter: the station's gate, the feature tables (design and replay), the
forecast's covering rule and its gate all read temperatures through it."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from valiwatt.config import DEMAND_FIRST, DEMAND_LAST, DESIGN_LAST, LST_OFFSET, Station
from valiwatt.quality.checks import CheckResult, robust_z, runs_of_true, spike_statistic

if TYPE_CHECKING:  # features imports this module
    from valiwatt.forecast.features import IssuedForecast

# Loose physical bounds for an hourly air temperature in southern Québec.
BOUNDS_C = (-60.0, 45.0)
# Share of demand-window hours with a temperature observation.
MIN_COVERAGE = 0.95
SPIKE_Z = 10.0
# Share of the window's days the forecast must feed (set after the archive's day counts for 2019-2024 were seen,
# docs/plan-v2.md, section 3.4).
NBM_MIN_DAY_COVERAGE = 0.98


def usable_temperature(series: pd.Series) -> pd.Series:
    """The temperatures (°C) a model may read: values outside BOUNDS_C blanked, blanks kept blank. The one bounds
    filter, for the station and for the forecast, in design code and in the replay."""
    return series.where((series >= BOUNDS_C[0]) & (series <= BOUNDS_C[1]))


def station_series(obs: pd.DataFrame, first: pd.Timestamp = DEMAND_FIRST, last: pd.Timestamp = DEMAND_LAST) -> pd.DataFrame:
    """Observations on the demand window's hourly UTC grid. Duplicate timestamps are kept out and counted."""
    dup = obs["ts_utc"].duplicated(keep=False)
    clean = obs[~dup].set_index("ts_utc").sort_index()
    idx = pd.date_range(first, last, freq="h")
    frame = clean.reindex(idx)
    frame.index.name = "ts_utc"
    frame.attrs["duplicate_rows"] = int(dup.sum())
    frame.attrs["missing_rows"] = int(len(idx.difference(clean.index)))
    return frame


def weather_checks(station: Station, frame: pd.DataFrame) -> list[CheckResult]:
    k = station.key
    temp = frame["temp_c"]
    checks = [
        CheckResult(
            f"weather_index[{k}]",
            "gate",
            passed=frame.attrs["duplicate_rows"] == 0 and frame.attrs["missing_rows"] == 0,
            metrics={"duplicate_rows": frame.attrs["duplicate_rows"], "missing_rows": frame.attrs["missing_rows"]},
        )
    ]
    observed = temp.dropna()
    out = observed[usable_temperature(observed).isna()]
    checks.append(
        CheckResult(
            f"weather_bounds[{k}]",
            "gate",
            passed=out.empty,
            metrics={"low_c": BOUNDS_C[0], "high_c": BOUNDS_C[1], "violations": len(out),
                     "min_c": observed.min(), "max_c": observed.max()},
            evidence=[{"utc": t, "temp_c": v} for t, v in out.head(20).items()],
        )
    )
    coverage = float(temp.notna().mean())
    checks.append(
        CheckResult(
            f"weather_coverage[{k}]",
            "gate",
            passed=coverage >= MIN_COVERAGE,
            metrics={"share": coverage, "threshold": MIN_COVERAGE},
        )
    )
    gaps = runs_of_true(temp.isna())
    flags = frame["temp_flag"].fillna("").replace("nan", "")
    stat = spike_statistic(temp)
    _, med, mad = robust_z(spike_statistic(temp[temp.index <= DESIGN_LAST]).dropna())
    z = (stat - med) / mad
    flagged = z[z.abs() > SPIKE_Z]
    checks.append(
        CheckResult(
            f"weather_gaps[{k}]",
            "finding",
            passed=True,
            metrics={
                "missing_hours": int(temp.isna().sum()),
                "runs": len(gaps),
                "longest_run_hours": max((g["hours"] for g in gaps), default=0),
                "runs_over_24h": sum(g["hours"] > 24 for g in gaps),
                "flag_counts": {f: int(n) for f, n in flags[flags != ""].value_counts().items()},
                "spikes_flagged": len(flagged),
                "spike_threshold_z": SPIKE_Z,
            },
            evidence=sorted(gaps, key=lambda g: -g["hours"])[:5]
            + [{"spike_utc": t, "temp_c": temp[t], "z": v} for t, v in flagged.head(10).items()],
        )
    )
    return checks


def panel_temperature(frames: dict[str, pd.DataFrame], min_stations: int) -> pd.Series:
    """Equal-weight mean of the panel's temperatures at each hour where at least min_stations report."""
    temps = pd.DataFrame({k: f["temp_c"] for k, f in frames.items()})
    n = temps.notna().sum(axis=1)
    return temps.mean(axis=1).where(n >= min_stations)


def hourly_gap_injection(frame: pd.DataFrame, share: float, seed: int = 0) -> pd.DataFrame:
    """Test helper: blank a contiguous block of temperatures (a sensor outage) covering `share` of the window."""
    out = frame.copy()
    n = int(len(out) * share)
    start = np.random.default_rng(seed).integers(0, len(out) - n)
    out.iloc[start : start + n, out.columns.get_loc("temp_c")] = np.nan
    out.attrs = dict(frame.attrs)
    return out


# Hours where at least this many of the panel's stations report count toward the panel mean.
MIN_PANEL_STATIONS = 4
MIN_PANEL_COVERAGE = 0.99


def panel_checks(frames: dict[str, pd.DataFrame], panel: pd.Series) -> list[CheckResult]:
    """Coverage of the panel mean, and gaps shared by several stations at once (a network-wide outage)."""
    temps = pd.DataFrame({k: f["temp_c"] for k, f in frames.items()})
    reporting = temps.notna().sum(axis=1)
    shared = runs_of_true(reporting <= len(frames) // 2)
    coverage = float(panel.notna().mean())
    return [
        CheckResult(
            "weather_panel_coverage",
            "gate",
            passed=coverage >= MIN_PANEL_COVERAGE,
            metrics={"share": coverage, "threshold": MIN_PANEL_COVERAGE, "min_stations": MIN_PANEL_STATIONS,
                     "stations": len(frames)},
        ),
        CheckResult(
            "weather_shared_gaps",
            "finding",
            passed=True,
            metrics={
                "hours_half_or_more_missing": int((reporting <= len(frames) // 2).sum()),
                "hours_all_missing": int((reporting == 0).sum()),
                "runs": len(shared),
                "longest_run_hours": max((g["hours"] for g in shared), default=0),
            },
            evidence=sorted(shared, key=lambda g: -g["hours"])[:5],
        ),
    ]


def _lst_day(ts: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return (ts - pd.Timedelta(hours=1) + LST_OFFSET).tz_convert(None).normalize()


def forecast_checks(runs: pd.DataFrame, issued: IssuedForecast, observed: pd.Series,
                    design_last: pd.Timestamp = DESIGN_LAST) -> list[CheckResult]:
    """The NBM forecast's gates and finding over a window (docs/plan-v2.md, section 3.4).

    runs: the window's runs (`sources.nbm.load_runs`); issued: `forecast_temperature` on every day of the window;
    observed: the primary station's temperature on the hourly grid (the forecast's error is read on days up to
    `design_last` only).
    - gate nbm_bounds: every point of the runs, converted to °C, inside the one filter's bounds;
    - gate nbm_coverage: at least NBM_MIN_DAY_COVERAGE of the window's days fed by a run;
    - finding nbm_runs, never gating: the identical duplicate points; the feeding run's UTC hour by year; the rank and
      lead (from the run time to the origin) counts; every day no run fed, with why; the longest lead of any run's
      points (valid time minus run time), by year of run time (`longest_lead_h`), and the years where it reaches
      sources.nbm.REUSE_LEAD_H (`leads_at_or_above_reuse_bound`: section 9.2 argues that a run feeds at most two
      consecutive days while every lead is below it, so a year listed is a finding against that argument and a trigger
      for a section 18 entry); the days fed by a run that also fed the day before; the forecast minus the observation
      by year and season on the design days."""
    from valiwatt.forecast.evaluate import SEASONS  # evaluate's imports reach the feature code, which imports this
    from valiwatt.sources.nbm import REUSE_LEAD_H

    celsius = (runs["tmp_f"].astype(float) - 32.0) * 5.0 / 9.0
    outside = celsius.notna() & usable_temperature(celsius).isna()
    bounds = CheckResult(
        "nbm_bounds", "gate", passed=not outside.any(),
        metrics={"low_c": BOUNDS_C[0], "high_c": BOUNDS_C[1], "violations": int(outside.sum()), "points": len(runs),
                 "blank_points": int(celsius.isna().sum()), "min_c": celsius.min(), "max_c": celsius.max()},
        evidence=[{"runtime": r.runtime, "ftime": r.ftime, "tmp_f": r.tmp_f, "temp_c": c}
                  for r, c in zip(runs[outside].head(20).itertuples(), celsius[outside].head(20))])

    record = issued.record
    fed = record["reason"] == ""
    days = len(record)
    share = float(fed.mean()) if days else 0.0
    coverage = CheckResult("nbm_coverage", "gate", passed=days > 0 and share >= NBM_MIN_DAY_COVERAGE,
                           metrics={"share": share, "threshold": NBM_MIN_DAY_COVERAGE, "days": days,
                                    "fed_days": int(fed.sum())})

    f = record[fed]
    day_year = pd.DatetimeIndex(f.index).year
    run_hour = pd.DatetimeIndex(f["runtime"]).hour
    hour_by_year = {int(y): {int(h): int(n) for h, n in g.value_counts().sort_index().items()}
                    for y, g in pd.Series(run_hour, index=day_year).groupby(level=0)}
    lead_point_h = (runs["ftime"] - runs["runtime"]) / pd.Timedelta(hours=1)
    longest = {int(y): float(v) for y, v in lead_point_h.groupby(runs["runtime"].dt.year).max().items()}
    at_or_above = [y for y, v in longest.items() if v >= REUSE_LEAD_H]
    previous = f["runtime"].reindex(pd.DatetimeIndex(f.index) - pd.Timedelta(days=1)).to_numpy()
    reused = f.index[f["runtime"].to_numpy() == previous]

    hourly = issued.hourly
    obs = usable_temperature(observed.reindex(hourly.index))
    design = hourly.index <= pd.Timestamp(design_last)
    err = (hourly - obs)[design].dropna()
    err_day = _lst_day(pd.DatetimeIndex(err.index))
    by_year_season = {}
    for (y, s), g in err.groupby([err_day.year, err_day.month.map(SEASONS)]):
        by_year_season.setdefault(int(y), {})[s] = {"mean_c": float(g.mean()), "mae_c": float(g.abs().mean()),
                                                    "hours": len(g)}
    not_fed = record[~fed]
    finding = CheckResult(
        "nbm_runs", "finding", passed=True,
        metrics={"identical_duplicates": int(runs.attrs.get("identical_duplicates", 0)), "runs": int(runs["runtime"].nunique()),
                 "points": len(runs), "run_hour_by_year": hour_by_year,
                 "rank_counts": {int(k): int(v) for k, v in f["rank"].value_counts().sort_index().items()},
                 "lead_counts": {float(k): int(v) for k, v in f["lead_h"].value_counts().sort_index().items()},
                 "days_not_fed": len(not_fed),
                 "reasons": {str(k): int(v) for k, v in not_fed["reason"].value_counts().items()},
                 "longest_lead_h": longest, "reuse_lead_h": REUSE_LEAD_H,
                 "leads_at_or_above_reuse_bound": at_or_above,
                 "days_fed_by_the_previous_days_run": len(reused),
                 "error_vs_observed": by_year_season, "error_last_utc": pd.Timestamp(design_last)},
        evidence=[{"day": d, "reason": r} for d, r in not_fed["reason"].items()]
        + [{"reused_day": d, "runtime": f.loc[d, "runtime"]} for d in reused])
    return [bounds, coverage, finding]
