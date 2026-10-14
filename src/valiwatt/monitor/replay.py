"""A year replayed as production: the frozen model forecasts each day at its origin, each hour is scored after it
arrives, and the monitors recommend (never act) at the end of each week.

The clock advances hour by hour. At every step the only data in view are the raw records stamped at or before the
clock (`RawFeed`); the canonical series, spike scores, lags and drift references are rebuilt from that cut, never
read from the full-window outputs of the data-quality layer.

- Day D's forecast is issued at its origin, the end of day D-1 (D 05:00 UTC).
- Day D is scored, and its data quality judged, one hour after its last hour arrives: the next record is what shows
  whether the last hour was a spike, so nothing is judged before it can be. The one exception is the last day, when
  the data end with it: it is judged at the end of the data, its last hour's spike stays unscored, and its record says
  so (`judged_at_data_end`).
- A week (Monday to Sunday, local standard time) is judged right after its Sunday is scored.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from valiwatt.config import DEMAND_FIRST, LST_OFFSET
from valiwatt.forecast.features import build, origin_of
from valiwatt.forecast.freeze import FrozenModel
from valiwatt.forecast.guards import LookaheadError, require_known_at_origin
from valiwatt.monitor.clock import Clock, RawFeed
from valiwatt.monitor.drift import normed_wasserstein, seasonal_reference
from valiwatt.monitor.rules import MAX_SILENT_TEMPERATURE_HOURS, MIN_WEEK_HOURS, decide, week_record
from valiwatt.quality.demand import (
    BOUNDS_MW,
    SPIKE_Z,
    STATUS_MASKED_DST,
    USABLE,
    build_demand,
)
from valiwatt.quality.weather import BOUNDS_C, station_series

H = pd.Timedelta(hours=1)
STATUS_NOT_YET = "not_yet_observed"
LOOKBACK = 200 * H  # the feature window behind an origin: more than the 168-hour lag needs


class MonitorActedError(RuntimeError):
    """The frozen model or its interval half-width changed during the replay: the monitor must never act."""


def origin(day: pd.Timestamp) -> pd.Timestamp:
    """Issue time of day D's forecast: the hour-ending label of D-1's last hour (D 00:00 EST = D 05:00 UTC)."""
    return (pd.Timestamp(day) - LST_OFFSET).tz_localize("UTC")


def lst_day(ts: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return (ts - H + LST_OFFSET).tz_convert(None).normalize()


@dataclass
class ReplayResult:
    hourly: pd.DataFrame
    days: list = field(default_factory=list)
    weeks: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    drift_samples: dict = field(default_factory=dict)
    model_sha256: str = ""
    clock_end: pd.Timestamp | None = None


class _State:
    """What the replay rebuilds from the raw cut at the current clock.

    scale_last: the last hour whose spike statistic enters the robust spike scale (the end of the year before the
    replayed one), so the scale never moves with the replayed year's own hours."""

    def __init__(self, demand: RawFeed, temperature: RawFeed, clock: Clock, scale_last: pd.Timestamp):
        self.demand, self.temperature, self.clock, self.scale_last = demand, temperature, clock, scale_last

    def canonical(self) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.DataFrame]:
        """Demand frame (out-of-bounds values unusable), temperature (duplicates and out-of-bounds values blanked),
        and the raw demand and temperature records."""
        raw = self.demand.visible()
        frame = build_demand(raw, DEMAND_FIRST, self.clock.now, scale_last=self.scale_last).frame
        raw_t = self.temperature.visible()
        temp_raw = station_series(raw_t, DEMAND_FIRST, self.clock.now)["temp_c"]
        temp = temp_raw.where((temp_raw >= BOUNDS_C[0]) & (temp_raw <= BOUNDS_C[1]))
        return frame, temp, raw, raw_t


def issue(state: _State, model: FrozenModel, day: pd.Timestamp) -> pd.DataFrame:
    """Day D's 24 forecasts, from what is known at its origin (the clock must be exactly there)."""
    o = origin(day)
    if state.clock.now != o:
        raise LookaheadError(f"day {day.date()} must be issued at {o}, not at {state.clock.now}")
    frame, temp, _, _ = state.canonical()
    target_hours = pd.date_range(o + H, o + 24 * H, freq="h")
    window = pd.date_range(o - LOOKBACK, o + 24 * H, freq="h")
    dem = frame.reindex(window)
    dem.loc[target_hours, ["mw", "status"]] = [np.nan, STATUS_NOT_YET]
    f = build(dem, temp.reindex(window)).loc[target_hours]
    require_known_at_origin(f)
    if not (f["origin"] == o).all() or not (f["day"] == day).all():
        raise LookaheadError(f"day {day.date()}: rows with another origin or day")
    pred = model.predict(f)
    out = pd.concat([f[["day", "hour", "day_type", "lag24", "lag168", "temp_persistence"]], pred], axis=1)
    out["origin"] = o
    reasons = []
    for _, r in out.iterrows():
        missing = [k for k in ("lag24", "lag168", "temp_persistence") if not np.isfinite(r[k])]
        reasons.append(", ".join(missing) if np.isnan(r["forecast"]) else "")
    out["not_predicted_because"] = reasons
    return out


def score_day(state: _State, day: pd.Timestamp, issued: pd.DataFrame, final: bool = False) -> tuple[pd.DataFrame, dict]:
    """Score day D's hours and judge its data quality, one hour after its last hour (`final`: at the end of the data,
    when no later record exists)."""
    o = origin(day)
    hours = pd.date_range(o + H, o + 24 * H, freq="h")
    if state.clock.now < hours[-1] + (0 * H if final else H):
        raise LookaheadError(f"day {day.date()} scored at {state.clock.now}, before the hour after its last hour")
    frame, temp, raw, raw_t = state.canonical()
    f = frame.reindex(hours)
    usable = f["status"].isin(USABLE)
    s = issued.reindex(hours).copy()
    s["target"] = f["mw"].where(usable)
    s["status"] = f["status"]
    s["spike_z"] = f["spike_z"]
    s["temp_observed"] = temp.reindex(hours)
    ok = s["target"].notna() & s["forecast"].notna()
    s["ape"] = ((s["target"] - s["forecast"]).abs() / s["target"]).where(ok)
    s["rel_bias"] = ((s["target"] - s["forecast"]) / s["target"]).where(ok)
    s["covered"] = ((s["target"] >= s["lower"]) & (s["target"] <= s["upper"])).astype(float).where(ok)

    day_raw = raw[(raw["ts_utc"] > o) & (raw["ts_utc"] <= hours[-1])]
    counts = day_raw[day_raw["ts_utc"].isin(hours)].groupby("ts_utc").size().reindex(hours, fill_value=0)
    expected = f["status"] == STATUS_MASKED_DST
    problems = []
    for t in hours[(counts == 0) & ~expected]:
        problems.append({"kind": "missing_hour", "utc": t})
    for t in day_raw.loc[day_raw["mw"].isna(), "ts_utc"]:
        problems.append({"kind": "blank_value", "utc": t})
    for t in hours[(counts > 1) & ~expected]:
        problems.append({"kind": "duplicate", "utc": t, "records": int(counts[t])})
    for t in day_raw.loc[~day_raw["ts_utc"].isin(hours), "ts_utc"]:
        problems.append({"kind": "off_grid_record", "utc": t})
    # Bounds are judged on every raw record, so a value inside a masked fall-back pair is still seen.
    bad = day_raw["mw"].notna() & ((day_raw["mw"] < BOUNDS_MW[0]) | (day_raw["mw"] > BOUNDS_MW[1]))
    for _, rec in day_raw[bad].iterrows():
        problems.append({"kind": "demand_out_of_bounds", "utc": rec["ts_utc"], "mw": float(rec["mw"])})
    day_t = raw_t[(raw_t["ts_utc"] > o) & (raw_t["ts_utc"] <= hours[-1])]
    for t, n in day_t.groupby("ts_utc").size().items():
        if n > 1:
            problems.append({"kind": "temperature_duplicate", "utc": t, "records": int(n)})
    bad_t = day_t["temp_c"].notna() & ((day_t["temp_c"] < BOUNDS_C[0]) | (day_t["temp_c"] > BOUNDS_C[1]))
    for _, rec in day_t[bad_t].iterrows():
        problems.append({"kind": "temperature_out_of_bounds", "utc": rec["ts_utc"], "temp_c": float(rec["temp_c"])})
    silent = int(s["temp_observed"].isna().sum())
    if silent > MAX_SILENT_TEMPERATURE_HOURS:
        problems.append({"kind": "temperature_silent", "hours": silent, "limit": MAX_SILENT_TEMPERATURE_HOURS})
    record = {
        "day": day, "failed": bool(problems), "problems": problems,
        "expected": [{"kind": "fall_back", "utc": t} for t in hours[expected]],
        "spikes": [{"utc": t, "z": float(z)} for t, z in s["spike_z"].items() if np.isfinite(z) and abs(z) > SPIKE_Z],
        "spike_unscored": [t for t, z in s["spike_z"].items() if not np.isfinite(z) and usable[t]],
        "not_predicted": {t: r for t, r in s["not_predicted_because"].items() if r},
        "scored_hours": int(ok.sum()),
        "judged_at_data_end": final,
    }
    return s, record


def week_drift(state: _State, week_start: pd.Timestamp, week_hours: pd.DataFrame,
               reference_forecasts: pd.DataFrame) -> tuple[dict, dict]:
    """Temperature and forecast drift of one week against the same calendar weeks of earlier years."""
    _, temp, _, _ = state.canonical()
    temp_days = pd.Series(lst_day(temp.index), index=temp.index)
    in_week = (temp_days >= week_start) & (temp_days < week_start + pd.Timedelta(days=7))
    cur_t = temp[in_week].to_numpy()
    ref_t = seasonal_reference(temp, temp_days, week_start)
    cur_f = week_hours["forecast"].to_numpy()
    ref_f = seasonal_reference(reference_forecasts["forecast"], reference_forecasts["day"], week_start)
    # A week with fewer than MIN_WEEK_HOURS current values (the partial first and last weeks) has no drift statistic.
    values = {"temperature": normed_wasserstein(cur_t, ref_t, MIN_WEEK_HOURS),
              "forecast": normed_wasserstein(cur_f, ref_f, MIN_WEEK_HOURS)}
    return values, {"temperature": (cur_t, ref_t), "forecast": (cur_f, ref_f)}


def replay(raw_demand: pd.DataFrame, raw_temperature: pd.DataFrame, model: FrozenModel, thresholds: dict,
           reference_forecasts: pd.DataFrame, first_day: str, last_day: str, data_end: pd.Timestamp) -> ReplayResult:
    """Replay [first_day, last_day] (local standard days). Raw records after `data_end` are never loaded.

    thresholds: the validation thresholds for this replay; refused unless they come from years before first_day's.
    reference_forecasts: out-of-sample forecasts of earlier years (columns day, forecast), the forecast-drift reference.
    """
    sha_before = model.sha256()
    first = pd.Timestamp(first_day)
    last_year = thresholds.get("last_year")
    if last_year is None or int(last_year) >= first.year:
        raise LookaheadError(f"thresholds from weeks up to {last_year} cannot judge a replay starting {first.date()}")
    late = [w for w in thresholds.get("weekly_validation", []) if pd.Timestamp(w["first_day"]) >= first]
    if late:
        raise LookaheadError(f"thresholds were computed on {len(late)} weeks of the replayed period")
    if not reference_forecasts["day"].lt(first).all():
        raise LookaheadError("forecast-drift reference holds days of the replayed period")
    clock = Clock(origin(first) - H)
    state = _State(RawFeed(raw_demand[raw_demand["ts_utc"] <= data_end], clock, "demand"),
                   RawFeed(raw_temperature[raw_temperature["ts_utc"] <= data_end], clock, "temperature"), clock,
                   scale_last=origin(pd.Timestamp(f"{first.year}-01-01")))
    days = pd.date_range(first_day, last_day, freq="D")
    clock_end = min(origin(days[-1] + pd.Timedelta(days=1)) + H, data_end)
    issue_at = {origin(d): d for d in days}
    score_at = {}
    for d in days:
        at = origin(d + pd.Timedelta(days=1)) + H
        score_at.setdefault(min(at, clock_end), []).append((d, at > clock_end))

    issued, scored, day_records, weeks, decisions, samples = {}, [], {}, [], [], {}
    for t in pd.date_range(origin(days[0]), clock_end, freq="h"):
        clock.advance(t)
        if t in issue_at:
            issued[issue_at[t]] = issue(state, model, issue_at[t])
        for d, final in score_at.get(t, []):
            s, rec = score_day(state, d, issued.pop(d), final=final)
            scored.append(s)
            day_records[d] = rec
            if d.dayofweek == 6 or d == days[-1]:
                week_start = d - pd.Timedelta(days=d.dayofweek)
                wk = pd.concat([x for x in scored if x["day"].iloc[0] >= week_start])
                wdays = [day_records[x] for x in sorted(day_records) if x >= week_start]
                if len(wdays) == 7:
                    drift, sample = week_drift(state, week_start, wk, reference_forecasts)
                else:  # a partial first or last week: the weekly monitors stay quiet
                    drift, sample = {"temperature": float("nan"), "forecast": float("nan")}, {}
                rec_w = week_record(week_start, wk.dropna(subset=["ape", "covered"]), wdays, drift, thresholds)
                decision = decide(rec_w, weeks[-1] if weeks else None)
                weeks.append(rec_w)
                decisions.append(decision)
                if any(rec_w["drift_flags"].values()):
                    samples[week_start] = sample
    if model.sha256() != sha_before:
        raise MonitorActedError("the frozen model or its half-width changed during the replay")
    hourly = pd.concat(scored)
    hourly.index.name = "ts_utc"
    return ReplayResult(hourly=hourly, days=[day_records[d] for d in days], weeks=weeks, decisions=decisions,
                        drift_samples=samples, model_sha256=sha_before, clock_end=clock_end)
