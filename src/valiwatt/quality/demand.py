"""Hydro-Québec demand: from raw records to a canonical hourly series, with every repair recorded.

Three known shapes of index anomaly, each handled by an explicit rule:

- Daylight-saving fall-back collapse. On the November night when clocks go back, the two hours that share the local
  label 01:00 both arrive stamped with the later UTC hour, and the earlier UTC hour is missing. The order of the two
  values is not recorded, so both hours are MASKED (no value), never guessed. The continuity margin of each event is
  reported to show why a guess would not be reliable.
- Misplaced record. A timestamp carries two values while another hour has none. The candidate destinations are the
  hours with no record whose two neighbours are known. A value is moved only when the data say both which value and
  where: (1) at the chosen destination, the assignment fits at least MISPLACED_MIN_RATIO times better than the swapped
  one, and (2) the destination is unambiguous: the next-best destination's best cost is at least MISPLACED_MIN_RATIO
  times the chosen one's, or there is no other candidate. Both values must be inside BOUNDS_MW: a pair holding a value
  outside them is never repaired. The repair is logged with its evidence (both ratios, and the next-best destination).
  Otherwise nothing is moved, the hour is an unresolved duplicate whose evidence names both candidate destinations, and
  the gate fails.
- Missing record or blank value: left missing, reported as runs. Never interpolated in the canonical series.

Any other duplicate fails the gate: when two values claim one hour and the data cannot say which is right, the
pipeline refuses rather than picks. A final pass marks any usable hour whose value is outside BOUNDS_MW out of bounds,
so no path through this function (the full-window data-quality layer or the replay's clock cuts) returns a usable
out-of-bounds value.
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
STATUS_OUT_OF_BOUNDS = "out_of_bounds"
USABLE = (STATUS_OK, STATUS_REPAIRED)


@dataclass
class DemandBuild:
    frame: pd.DataFrame  # index: hour-ending UTC; columns: mw, status, spike_z, spike_flag
    dst_events: list = field(default_factory=list)
    misplaced_events: list = field(default_factory=list)
    unresolved_duplicates: list = field(default_factory=list)
    outside_window: list = field(default_factory=list)  # outside the window, or off its hourly grid
    out_of_bounds: list = field(default_factory=list)  # raw records whose value is outside BOUNDS_MW


def same_wall_clock(a: pd.Timestamp, b: pd.Timestamp, tz: str = LOCAL_TZ) -> bool:
    """True when two UTC instants read the same on a local wall clock (the repeated hour at fall-back)."""
    return a.tz_convert(tz).tz_localize(None) == b.tz_convert(tz).tz_localize(None)


def _fit(value: float, slot: pd.Timestamp, values: pd.Series) -> float:
    """How far a value sits from the two hours around a slot (sum of absolute steps). NaN if a neighbour is unknown."""
    h = pd.Timedelta(hours=1)
    before, after = values.get(slot - h, np.nan), values.get(slot + h, np.nan)
    return abs(value - before) + abs(after - value)


def _eligible(value: float) -> bool:
    """A duplicate's value may take part in a repair only when it is a number inside BOUNDS_MW."""
    return bool(np.isfinite(value)) and BOUNDS_MW[0] <= value <= BOUNDS_MW[1]


def _mark_out_of_bounds(frame: pd.DataFrame) -> pd.DataFrame:
    """The last line: a usable hour whose value is outside BOUNDS_MW becomes out_of_bounds (value kept as evidence)."""
    bad = frame["status"].isin(USABLE) & ((frame["mw"] < BOUNDS_MW[0]) | (frame["mw"] > BOUNDS_MW[1]))
    frame.loc[bad, "status"] = STATUS_OUT_OF_BOUNDS
    return frame


def _destinations(vals: list, t: pd.Timestamp, missing: set, values: pd.Series) -> list[dict]:
    """For each empty hour with both neighbours known, its best assignment of the pair at t, cheapest first (ties keep
    time order). cost_mw: moved value at the slot plus kept value at t; swapped_cost_mw: the two values the other way."""
    out = []
    for s in sorted(missing):
        best = None
        for a, b in ((vals[0], vals[1]), (vals[1], vals[0])):
            cost = _fit(a, s, values) + _fit(b, t, values)
            alt = _fit(b, s, values) + _fit(a, t, values)
            if np.isnan(cost) or np.isnan(alt):
                continue
            if best is None or cost < best["cost_mw"]:
                best = {"slot": s, "moved": a, "kept": b, "cost_mw": cost, "swapped_cost_mw": alt}
        if best is not None:
            out.append(best)
    return sorted(out, key=lambda d: d["cost_mw"])


def _pair_path(first: float, second: float, s: pd.Timestamp, t: pd.Timestamp, values: pd.Series) -> float:
    """Steps along before(s) -> first -> second -> after(t) for two adjacent hours s, t = s + 1h."""
    h = pd.Timedelta(hours=1)
    return abs(first - values.get(s - h, np.nan)) + abs(second - first) + abs(values.get(t + h, np.nan) - second)


def build_demand(raw: pd.DataFrame, first: pd.Timestamp = DEMAND_FIRST, last: pd.Timestamp = DEMAND_LAST,
                 scale_last: pd.Timestamp = DESIGN_LAST) -> DemandBuild:
    """scale_last: the last hour whose spike statistic enters the robust scale (the design window by default)."""
    idx = pd.date_range(first, last, freq="h")
    on_grid = raw["ts_utc"].isin(idx)
    counts = raw[on_grid].groupby("ts_utc").size()
    outside = sorted(raw.loc[~on_grid, "ts_utc"].unique())
    raw = raw[on_grid]
    out_of_range = raw["mw"].notna() & ((raw["mw"] < BOUNDS_MW[0]) | (raw["mw"] > BOUNDS_MW[1]))
    bad_bounds = raw.loc[out_of_range, ["ts_utc", "mw", "source_line"]].to_dict("records")
    dup_slots = [t for t in counts[counts > 1].index]
    missing = set(idx.difference(counts.index))

    values = pd.Series(np.nan, index=idx, dtype=float)
    status = pd.Series(STATUS_OK, index=idx, dtype=object)
    single = raw[~raw["ts_utc"].isin(dup_slots) & raw["ts_utc"].between(first, last)].set_index("ts_utc")["mw"]
    values.loc[single.index] = single.to_numpy()
    # An out-of-bounds value is set aside before any repair or spike score uses it as a neighbour.
    oob = values.notna() & ((values < BOUNDS_MW[0]) | (values > BOUNDS_MW[1]))
    oob_values = values[oob]
    values[oob] = np.nan
    status.loc[single.index[single.isna().to_numpy()]] = STATUS_MISSING_VALUE
    status.loc[sorted(missing)] = STATUS_MISSING_RECORD

    build = DemandBuild(frame=pd.DataFrame(), outside_window=outside, out_of_bounds=bad_bounds)
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
        best = runner_up = None
        candidates = []
        if len(vals) != 2:
            reason = "not_a_pair"
        elif any(np.isnan(vals)):
            reason = "blank_value"
        elif not all(_eligible(v) for v in vals):
            reason = "value_out_of_bounds"
        else:
            candidates = _destinations(vals, t, missing, values)
            best = candidates[0] if candidates else None
            runner_up = candidates[1] if len(candidates) > 1 else None
            if best is None:
                reason = "no_destination"
            elif not (best["cost_mw"] > 0 and best["swapped_cost_mw"] / best["cost_mw"] >= MISPLACED_MIN_RATIO):
                reason = "swap_ratio"
            elif runner_up is not None and runner_up["cost_mw"] / best["cost_mw"] < MISPLACED_MIN_RATIO:
                reason = "ambiguous_destination"
            else:
                reason = None
        if reason is None:
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
                    "destinations": len(candidates),
                    "runner_up": runner_up,
                    "destination_ratio": None if runner_up is None else runner_up["cost_mw"] / best["cost_mw"],
                }
            )
        else:
            values[t] = np.nan
            status[t] = "unresolved_duplicate"
            build.unresolved_duplicates.append(
                {"utc": t, "values_mw": vals, "source_lines": recs["source_line"].tolist(), "reason": reason,
                 "best_candidate": best, "runner_up": runner_up}
            )

    frame = pd.DataFrame({"mw": values, "status": status})
    frame.index.name = "ts_utc"
    frame.loc[oob_values.index, "mw"] = oob_values  # kept as evidence, never used
    frame.loc[oob_values.index, "status"] = STATUS_OUT_OF_BOUNDS
    frame = _mark_out_of_bounds(frame)
    usable = frame["status"].isin(USABLE)
    series = frame["mw"].where(usable)
    stat = spike_statistic(series)
    # The scale comes from the design window alone: the statistic is recomputed on the cut series, so the last design
    # hour never borrows its 2024 neighbour.
    _, med, mad = robust_z(spike_statistic(series[series.index <= scale_last]).dropna())
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
    # Bounds are judged on the raw records, so a value hidden by a masked fall-back pair is still seen.
    observed = f["mw"].dropna()
    checks.append(
        CheckResult(
            "demand_bounds",
            "gate",
            passed=not build.out_of_bounds,
            metrics={"low_mw": BOUNDS_MW[0], "high_mw": BOUNDS_MW[1], "violations": len(build.out_of_bounds),
                     "min_mw": observed.min(), "max_mw": observed.max()},
            evidence=[{"utc": r["ts_utc"], "mw": r["mw"]} for r in build.out_of_bounds[:20]],
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
