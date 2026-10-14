"""ECCC station observations: index, bounds, coverage and spikes per station, over the demand window."""

from __future__ import annotations

import numpy as np
import pandas as pd

from valiwatt.config import DEMAND_FIRST, DEMAND_LAST, DESIGN_LAST, Station
from valiwatt.quality.checks import CheckResult, robust_z, runs_of_true, spike_statistic

# Loose physical bounds for an hourly air temperature in southern Québec.
BOUNDS_C = (-60.0, 45.0)
# Share of demand-window hours with a temperature observation.
MIN_COVERAGE = 0.95
SPIKE_Z = 10.0


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
    out = observed[(observed < BOUNDS_C[0]) | (observed > BOUNDS_C[1])]
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
