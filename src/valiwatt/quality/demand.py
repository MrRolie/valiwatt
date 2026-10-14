"""Hydro-Québec demand: from raw records to a canonical hourly series, with every repair recorded.

Three known shapes of index anomaly, each handled by an explicit rule:

- Daylight-saving fall-back collapse. On the November night when clocks go back, the two hours that share the local
  label 01:00 both arrive stamped with the later UTC hour, and the earlier UTC hour is missing. The order of the two
  values is not recorded, so both hours are MASKED (no value), never guessed. The continuity margin of each event is
  reported to show why a guess would not be reliable.
- Misplaced record. A timestamp carries two values while another hour has none, and one value fits the empty hour's
  neighbours far better than its own (cost ratio at least MISPLACED_MIN_RATIO). The value is moved to the empty hour
  and the repair is logged with its evidence. Below the ratio, nothing is moved and the gate fails.
- Missing record or blank value: left missing, reported as runs. Never interpolated in the canonical series.

Any other duplicate fails the gate: when two values claim one hour and the data cannot say which is right, the
pipeline refuses rather than picks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from valiwatt.config import DEMAND_FIRST, DEMAND_LAST, DESIGN_LAST, LOCAL_TZ
from valiwatt.quality.checks import MAD_SCALE, CheckResult, robust_z, runs_of_true, spike_statistic

# A misplaced value must fit its new hour at least twice as well as the swapped assignment.
MISPLACED_MIN_RATIO = 2.0
# Loose physical bounds for Québec's hourly demand. A value outside is a unit or parsing error, not weather.
BOUNDS_MW = (5_000.0, 60_000.0)
# Usable hours (not missing, not masked) over the whole window.
MIN_COVERAGE = 0.99
# |robust z| of the spike statistic above which an hour is flagged for review. Flagged hours keep their value. The
# robust scale is estimated on the design window (2019-2023) and applied unchanged to every year.
SPIKE_Z = 10.0
# The quantile of |z| reported to show where the threshold sits relative to ordinary hours.
DESIGN_QUANTILE = 0.999

STATUS_OK = "ok"
STATUS_MISSING_RECORD = "missing_record"
STATUS_MISSING_VALUE = "missing_value"
STATUS_MASKED_DST = "masked_dst"
STATUS_REPAIRED = "repaired_misplaced"
USABLE = (STATUS_OK, STATUS_REPAIRED)


@dataclass
class DemandBuild:
    frame: pd.DataFrame  # index: hour-ending UTC; columns: mw, status, spike_z, spike_flag
    dst_events: list = field(default_factory=list)
    misplaced_events: list = field(default_factory=list)
    unresolved_duplicates: list = field(default_factory=list)
    outside_window: list = field(default_factory=list)


def same_wall_clock(a: pd.Timestamp, b: pd.Timestamp, tz: str = LOCAL_TZ) -> bool:
    """True when two UTC instants read the same on a local wall clock (the repeated hour at fall-back)."""
    return a.tz_convert(tz).tz_localize(None) == b.tz_convert(tz).tz_localize(None)


def _fit(value: float, slot: pd.Timestamp, values: pd.Series) -> float:
    """How far a value sits from the two hours around a slot (sum of absolute steps). NaN if a neighbour is unknown."""
    h = pd.Timedelta(hours=1)
    before, after = values.get(slot - h, np.nan), values.get(slot + h, np.nan)
    return abs(value - before) + abs(after - value)


def _pair_path(first: float, second: float, s: pd.Timestamp, t: pd.Timestamp, values: pd.Series) -> float:
    """Steps along before(s) -> first -> second -> after(t) for two adjacent hours s, t = s + 1h."""
    h = pd.Timedelta(hours=1)
    return abs(first - values.get(s - h, np.nan)) + abs(second - first) + abs(values.get(t + h, np.nan) - second)


def build_demand(raw: pd.DataFrame, first: pd.Timestamp = DEMAND_FIRST, last: pd.Timestamp = DEMAND_LAST) -> DemandBuild:
    idx = pd.date_range(first, last, freq="h")
    counts = raw.groupby("ts_utc").size()
    outside = [t for t in counts.index if t < first or t > last]
    dup_slots = [t for t in counts[counts > 1].index if first <= t <= last]
    missing = set(idx.difference(counts.index))

    values = pd.Series(np.nan, index=idx, dtype=float)
    status = pd.Series(STATUS_OK, index=idx, dtype=object)
    single = raw[~raw["ts_utc"].isin(dup_slots) & raw["ts_utc"].between(first, last)].set_index("ts_utc")["mw"]
    values.loc[single.index] = single.to_numpy()
    status.loc[single.index[single.isna().to_numpy()]] = STATUS_MISSING_VALUE
    status.loc[sorted(missing)] = STATUS_MISSING_RECORD

    build = DemandBuild(frame=pd.DataFrame(), outside_window=outside)
    h = pd.Timedelta(hours=1)
    remaining = []
    for t in dup_slots:
        recs = raw.loc[raw["ts_utc"] == t, ["mw", "source_line"]]
        vals = recs["mw"].tolist()
        s = t - h
        if len(vals) == 2 and s in missing and same_wall_clock(s, t) and not any(np.isnan(vals)):
            a, b = vals
            in_order = _pair_path(a, b, s, t, values)
            swapped = _pair_path(b, a, s, t, values)
            values[[s, t]] = np.nan
            status[[s, t]] = STATUS_MASKED_DST
            missing.discard(s)
            build.dst_events.append(
                {
                    "missing_utc": s,
                    "duplicated_utc": t,
                    "local_wall_clock": str(t.tz_convert(LOCAL_TZ).tz_localize(None)),
                    "values_mw": vals,
                    "source_lines": recs["source_line"].tolist(),
                    "continuity_cost_mw": sorted([in_order, swapped]),
                    "continuity_margin_mw": abs(in_order - swapped),
                }
            )
        else:
            remaining.append(t)

    for t in remaining:
        recs = raw.loc[raw["ts_utc"] == t, ["mw", "source_line"]]
        vals = recs["mw"].tolist()
        best = None
        if len(vals) == 2 and not any(np.isnan(vals)):
            for s in sorted(missing):
                for a, b in ((vals[0], vals[1]), (vals[1], vals[0])):
                    cost = _fit(a, s, values) + _fit(b, t, values)
                    alt = _fit(b, s, values) + _fit(a, t, values)
                    if np.isnan(cost) or np.isnan(alt):
                        continue
                    if best is None or cost < best["cost_mw"]:
                        best = {"slot": s, "moved": a, "kept": b, "cost_mw": cost, "swapped_cost_mw": alt}
        if best and best["cost_mw"] > 0 and best["swapped_cost_mw"] / best["cost_mw"] >= MISPLACED_MIN_RATIO:
            s = best["slot"]
            values[s], values[t] = best["moved"], best["kept"]
            status[s], status[t] = STATUS_REPAIRED, STATUS_OK
            missing.discard(s)
            build.misplaced_events.append(
                {
                    "duplicated_utc": t,
                    "moved_to_utc": s,
                    "offset_hours": int((t - s) / h),
                    "moved_mw": best["moved"],
                    "kept_mw": best["kept"],
                    "source_lines": recs["source_line"].tolist(),
                    "cost_mw": best["cost_mw"],
                    "swapped_cost_mw": best["swapped_cost_mw"],
                    "cost_ratio": best["swapped_cost_mw"] / best["cost_mw"],
                }
            )
        else:
            values[t] = np.nan
            status[t] = "unresolved_duplicate"
            build.unresolved_duplicates.append(
                {"utc": t, "values_mw": vals, "source_lines": recs["source_line"].tolist(), "best_candidate": best}
            )

    frame = pd.DataFrame({"mw": values, "status": status})
    frame.index.name = "ts_utc"
    usable = frame["status"].isin(USABLE)
    series = frame["mw"].where(usable)
    stat = spike_statistic(series)
    # The scale comes from the design window alone: the statistic is recomputed on the cut series, so the last design
    # hour never borrows its 2024 neighbour.
    _, med, mad = robust_z(spike_statistic(series[series.index <= DESIGN_LAST]).dropna())
    frame["spike_z"] = (stat - med) / mad
    frame["spike_flag"] = frame["spike_z"].abs() > SPIKE_Z
    build.frame = frame
    return build


def demand_checks(build: DemandBuild) -> list[CheckResult]:
    f = build.frame
    usable = f["status"].isin(USABLE)
    checks = []

    checks.append(
        CheckResult(
            "demand_window",
            "gate",
            passed=not build.outside_window,
            metrics={"first_utc": f.index[0], "last_utc": f.index[-1], "hours": len(f), "outside": len(build.outside_window)},
            evidence=build.outside_window[:20],
        )
    )
    checks.append(
        CheckResult(
            "demand_duplicates_resolved",
            "gate",
            passed=not build.unresolved_duplicates,
            metrics={
                "dst_events": len(build.dst_events),
                "misplaced_repaired": len(build.misplaced_events),
                "unresolved": len(build.unresolved_duplicates),
            },
            evidence=build.unresolved_duplicates[:20],
        )
    )
    observed = f["mw"].dropna()
    out = observed[(observed < BOUNDS_MW[0]) | (observed > BOUNDS_MW[1])]
    checks.append(
        CheckResult(
            "demand_bounds",
            "gate",
            passed=out.empty,
            metrics={"low_mw": BOUNDS_MW[0], "high_mw": BOUNDS_MW[1], "violations": len(out),
                     "min_mw": observed.min(), "max_mw": observed.max()},
            evidence=[{"utc": t, "mw": v} for t, v in out.head(20).items()],
        )
    )
    coverage = float(usable.mean())
    checks.append(
        CheckResult(
            "demand_coverage",
            "gate",
            passed=coverage >= MIN_COVERAGE,
            metrics={"usable_share": coverage, "threshold": MIN_COVERAGE, "usable_hours": int(usable.sum()),
                     "hours": len(f)},
        )
    )

    by_status = f["status"].value_counts().to_dict()
    gaps = runs_of_true(f["status"].isin([STATUS_MISSING_RECORD, STATUS_MISSING_VALUE]))
    checks.append(
        CheckResult(
            "demand_missing",
            "finding",
            passed=True,
            metrics={
                "missing_value_hours": by_status.get(STATUS_MISSING_VALUE, 0),
                "missing_record_hours": by_status.get(STATUS_MISSING_RECORD, 0),
                "masked_dst_hours": by_status.get(STATUS_MASKED_DST, 0),
                "repaired_hours": by_status.get(STATUS_REPAIRED, 0),
                "runs": len(gaps),
                "longest_run_hours": max((g["hours"] for g in gaps), default=0),
            },
            evidence=gaps,
        )
    )
    checks.append(
        CheckResult(
            "demand_dst",
            "finding",
            passed=True,
            metrics={
                "events": len(build.dst_events),
                "min_margin_mw": min((e["continuity_margin_mw"] for e in build.dst_events), default=None),
                "max_margin_mw": max((e["continuity_margin_mw"] for e in build.dst_events), default=None),
            },
            evidence=build.dst_events,
        )
    )
    checks.append(
        CheckResult(
            "demand_misplaced",
            "finding",
            passed=True,
            metrics={"events": len(build.misplaced_events), "min_ratio": MISPLACED_MIN_RATIO},
            evidence=build.misplaced_events,
        )
    )
    flagged = f[f["spike_flag"]]
    checks.append(
        CheckResult(
            "demand_spikes",
            "finding",
            passed=True,
            metrics={"threshold_z": SPIKE_Z, "flagged": len(flagged), "mad_scale": MAD_SCALE,
                     "design_quantile": DESIGN_QUANTILE,
                     "design_quantile_abs_z": float(f.loc[f.index < DESIGN_LAST, "spike_z"].abs().quantile(DESIGN_QUANTILE)),
                     "consecutive_pairs": int((f["spike_flag"] & f["spike_flag"].shift(1, fill_value=False)).sum())},
            evidence=[
                {"utc": t, "mw": r.mw, "z": r.spike_z,
                 "before_mw": f["mw"].get(t - pd.Timedelta(hours=1)), "after_mw": f["mw"].get(t + pd.Timedelta(hours=1))}
                for t, r in flagged.iterrows()
            ],
        )
    )
    return checks
