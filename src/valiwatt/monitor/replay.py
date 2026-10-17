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

v2 (docs/plan-v2.md, sections 3.2, 5, 9 and 11), when the NBM runs are given:
- A third feed holds the runs, each point stamped at its run's availability. At each origin the issue takes, from the
  runs the feed shows (through the clock), the MAX_RUNS_BACK freshest by run identity whatever their age, and their
  points only, through the run rule (features.forecast_temperature): the rule validation applies to the same runs.
- A day no compliant covering run fed is not predicted, each hour says why, and the day fails `forecast_missing`.
- The temperature drift watches the model's own input, the issued forecast temperature, against the issued forecasts
  of the same calendar weeks of earlier years, rebuilt at the start from the runs in view then.
- The comparison models are scored after the loop on the inputs the replay recorded at each issue; they issue nothing
  and touch no monitor. All three models' hashes are checked before and after.
- Reading 2025 needs the started 2025 run the command opened in the run store (monitor/run.py, check_2025_run).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from valiwatt.config import DEMAND_FIRST, DESIGN_LAST, LST_OFFSET, data_last
from valiwatt.forecast.features import IssuedForecast, build, forecast_temperature, origin_of  # noqa: F401
from valiwatt.forecast.freeze import FrozenModel
from valiwatt.forecast.guards import LookaheadError, require_known_at_origin
from valiwatt.monitor.clock import Clock, RawFeed
from valiwatt.monitor.drift import normed_wasserstein, seasonal_reference
from valiwatt.monitor.rules import MIN_WEEK_HOURS, day_problems, decide, week_record
from valiwatt.quality.demand import (
    SPIKE_Z,
    STATUS_MASKED_DST,
    USABLE,
    build_demand,
)
from valiwatt.quality.weather import station_series, usable_temperature
from valiwatt.sources.nbm import AVAILABILITY_LAG, MAX_RUNS_BACK

H = pd.Timedelta(hours=1)
STATUS_NOT_YET = "not_yet_observed"
LOOKBACK = 200 * H  # the feature window behind an origin: more than the 168-hour lag needs
# The last hour a replay may read before 2025: the end of 2024 (docs/plan-v2.md, section 11.3).
LAST_BEFORE_2025 = data_last(2024)
RUN_POINTS = ("runtime", "ftime", "tmp_f")
# v1's columns of an issued day; v2 adds every comparator input recorded at issue and the day's forecast record.
V1_ISSUED = ["day", "hour", "day_type", "lag24", "lag168", "temp_persistence"]
V2_INPUTS = ["hdh_persistence", "cdh_persistence", "temp_forecast", "available_forecast", "hdh_forecast",
             "cdh_forecast"]
RECORD_COLUMNS = {"runtime": "forecast_runtime", "rank": "forecast_rank", "lead_h": "forecast_lead_h",
                  "reason": "forecast_reason"}


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
    # v2: the issued forecast's record of each replayed day (features.IssuedForecast.record's shape), the
    # temperature-drift reference the replay started from, and each comparison model's hash.
    forecast_record: pd.DataFrame | None = None
    temperature_reference: pd.Series | None = None
    comparators_sha256: dict = field(default_factory=dict)


def forecast_feed_records(runs: pd.DataFrame, data_end: pd.Timestamp) -> pd.DataFrame:
    """The NBM runs as the forecast feed holds them: each point stamped (`ts_utc`) at its run's availability, the
    column `available_utc` as quality.run.load_runs gives it (runtime + AVAILABILITY_LAG when absent), the points
    available after `data_end` left out. A point without its run time, valid time or availability is refused: its run's
    availability cannot be established."""
    available = runs["available_utc"] if "available_utc" in runs else runs["runtime"] + AVAILABILITY_LAG
    out = runs[list(RUN_POINTS)].assign(ts_utc=pd.to_datetime(available, utc=True))
    undated = (out["runtime"].isna() | out["ftime"].isna() | out["ts_utc"].isna()).to_numpy()
    if undated.any():
        raise LookaheadError(f"{int(undated.sum())} forecast points have no run time, valid time or availability: "
                             "the forecast feed reads none of them")
    return out[out["ts_utc"] <= pd.Timestamp(data_end)].reset_index(drop=True)


def issued_forecast(feed: RawFeed, day: pd.Timestamp) -> IssuedForecast:
    """Day D's issued forecast from the runs the feed shows at the clock (D's origin, when the replay issues it): the
    MAX_RUNS_BACK freshest runs by run identity (`runtime`) whatever their age, and their points only, passed to the run
    rule (features.forecast_temperature) for that one day, as validation applies it to the same runs (plan section
    3.2: no age window, so the two choose among the same runs on any archive)."""
    points = feed.freshest("runtime", MAX_RUNS_BACK)
    return forecast_temperature(points[list(RUN_POINTS)].reset_index(drop=True), [pd.Timestamp(day)])


def issued_reference(feed: RawFeed, first: pd.Timestamp) -> pd.Series:
    """The temperature-drift reference at a replay's start (plan section 9.3): the forecast issued, by the run rule,
    for every day before the first replayed one, from the runs in view at the start (their own availability decides
    each day's), through the one bounds filter. Indexed by hour-ending UTC label."""
    days = pd.date_range(lst_day(pd.DatetimeIndex([DEMAND_FIRST]))[0], pd.Timestamp(first) - pd.Timedelta(days=1))
    if len(days) == 0:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC", name="ts_utc"), name="temp_forecast")
    visible = feed.visible()
    issued = forecast_temperature(visible[list(RUN_POINTS)].reset_index(drop=True), days)
    return usable_temperature(issued.hourly).dropna()


class _State:
    """What the replay rebuilds from the raw cut at the current clock.

    scale_last: the last hour whose spike statistic enters the robust spike scale (the end of the year before the
    replayed one), so the scale never moves with the replayed year's own hours. forecast: v2's feed of NBM runs, each
    point stamped at its run's availability (None: v1)."""

    def __init__(self, demand: RawFeed, temperature: RawFeed, clock: Clock, scale_last: pd.Timestamp,
                 forecast: RawFeed | None = None):
        self.demand, self.temperature, self.clock, self.scale_last = demand, temperature, clock, scale_last
        self.forecast = forecast

    def canonical(self) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.DataFrame]:
        """Demand frame (out-of-bounds values unusable), temperature (duplicates and out-of-bounds values blanked),
        and the raw demand and temperature records."""
        raw, raw_t = self.demand.visible(), self.temperature.visible()
        frame, temp = canonical(raw, raw_t, self.clock.now, self.scale_last)
        return frame, temp, raw, raw_t


def canonical(raw: pd.DataFrame, raw_t: pd.DataFrame, now: pd.Timestamp,
              scale_last: pd.Timestamp) -> tuple[pd.DataFrame, pd.Series]:
    """The canonical demand frame and temperature built from raw records up to `now`. Pure. The temperature passes
    the one bounds filter of the data-quality layer (quality.weather.usable_temperature), as design code's does."""
    frame = build_demand(raw, DEMAND_FIRST, now, scale_last=scale_last).frame
    temp_raw = station_series(raw_t, DEMAND_FIRST, now)["temp_c"]
    return frame, usable_temperature(temp_raw)


def _record_dict(record: pd.DataFrame, day: pd.Timestamp) -> dict:
    row = record.loc[pd.Timestamp(day)]
    return {"runtime": row["runtime"], "available_utc": row["available_utc"], "rank": float(row["rank"]),
            "lead_h": float(row["lead_h"]), "reason": str(row["reason"])}


def day_verdicts(raw_demand: pd.DataFrame, raw_temperature: pd.DataFrame, first_day: pd.Timestamp,
                 last_day: pd.Timestamp, runs: pd.DataFrame | None = None) -> dict[pd.Timestamp, list[dict]]:
    """The replay's day-level data-quality verdict (rules.day_problems) on every local standard day in
    [first_day, last_day]: day -> its problems (an empty list: the day passes). Raw records after the last day's last
    hour are never read.

    The replay judges a day on the series rebuilt from the raw cut one hour after the day's last hour; this builds the
    series once, from the cut at the last day's last hour. The verdict reads, on the day's own hours, the raw records
    stamped in the day, the demand status (a fall-back pair is masked by the two records of that pair alone) and the
    temperature (a record and its duplicates share one timestamp); none of these moves when records stamped after the
    day are added, so each day's verdict is the replay's. The last day is judged as at the end of the data, as a replay
    judges its last day when the data end with it (a fall-back pair that straddles the end, which never happens on
    31 December, is the one case where the two could differ). The explicit cut at the last day's last hour is kept,
    though build_demand and station_series already drop later records as off their window (`last` = that hour):
    removing it changes nothing, an equivalent mutant; reading a later day into a day's slice is what the lookahead
    break on day_verdicts catches.

    runs (v2): the NBM runs. Each day's forecast record is then computed as the replay computes it, from the runs the
    forecast feed shows at the day's origin (`issued_forecast`), and a day no run fed fails `forecast_missing`, so the
    weeks validation calibrates on are the ones the replay would judge."""
    days = pd.date_range(pd.Timestamp(first_day), pd.Timestamp(last_day), freq="D")
    end = origin(days[-1]) + 24 * H
    raw, raw_t = (r[r["ts_utc"] <= end].sort_values("ts_utc", kind="stable").reset_index(drop=True)
                  for r in (raw_demand, raw_temperature))
    frame, temp = canonical(raw, raw_t, end, end)
    clock = Clock(origin(days[0]))
    feed = None if runs is None else RawFeed(forecast_feed_records(runs, end), clock, "forecast")
    out = {}
    for d in days:
        o = origin(d)
        hours = pd.date_range(o + H, o + 24 * H, freq="h")
        cut = [int(r["ts_utc"].searchsorted(x, side="right")) for r in (raw, raw_t) for x in (o, hours[-1])]
        record = None
        if feed is not None:
            clock.advance(o)
            record = _record_dict(issued_forecast(feed, d).record, d)
        out[d] = day_problems(hours, raw.iloc[cut[0]:cut[1]], raw_t.iloc[cut[2]:cut[3]], frame["status"], temp,
                              forecast_record=record)
    return out


def _not_predicted(out: pd.DataFrame, inputs: tuple, temp_col: str | None, reason: str) -> list[str]:
    """Why each hour has no forecast ("" where it has one): the production model's missing inputs, its temperature
    input with the day's forecast-record reason when no run fed the day."""
    reasons = []
    for _, r in out.iterrows():
        if not np.isnan(r["forecast"]):
            reasons.append("")
            continue
        missing = []
        for k in inputs:
            if not np.isfinite(r[k]):
                missing.append(f"{k} ({reason})" if k == temp_col and reason else k)
        reasons.append(", ".join(missing))
    return reasons


def issue(state: _State, model: FrozenModel, day: pd.Timestamp) -> pd.DataFrame:
    """Day D's 24 forecasts, from what is known at its origin (the clock must be exactly there).

    v2 (a forecast feed in the state): the forecast scenario is the one the run rule issues from the runs in view
    (`issued_forecast`); a row whose forecast became available after the origin is refused (require_known_at_origin).
    The frame records every comparator input (persistence and forecast temperatures and degree-hours) and the day's
    forecast record (runtime, rank, lead, reason), so the comparison models are scored on them later."""
    o = origin(day)
    if state.clock.now != o:
        raise LookaheadError(f"day {day.date()} must be issued at {o}, not at {state.clock.now}")
    frame, temp, _, _ = state.canonical()
    target_hours = pd.date_range(o + H, o + 24 * H, freq="h")
    window = pd.date_range(o - LOOKBACK, o + 24 * H, freq="h")
    dem = frame.reindex(window)
    dem.loc[target_hours, ["mw", "status"]] = [np.nan, STATUS_NOT_YET]
    record = None
    if state.forecast is None:
        f = build(dem, temp.reindex(window)).loc[target_hours]
    else:
        issued = issued_forecast(state.forecast, day)
        record = _record_dict(issued.record, day)
        f = build(dem, temp.reindex(window), {"forecast": issued}).loc[target_hours]
    require_known_at_origin(f)
    if not (f["origin"] == o).all() or not (f["day"] == day).all():
        raise LookaheadError(f"day {day.date()}: rows with another origin or day")
    pred = model.predict(f)
    cols = V1_ISSUED if record is None else V1_ISSUED + V2_INPUTS
    out = pd.concat([f[cols], pred], axis=1)
    out["origin"] = o
    if record is None:
        out["not_predicted_because"] = _not_predicted(out, ("lag24", "lag168", "temp_persistence"), None, "")
        return out
    for k, col in RECORD_COLUMNS.items():
        out[col] = record[k]
    # A day no run fed has no runtime (NaT): the column stays a UTC time column all the same.
    out["forecast_runtime"] = pd.to_datetime(pd.Series(record["runtime"], index=out.index), utc=True).astype(
        "datetime64[ns, UTC]")
    temp_col = f"temp_{model.scenario}"
    out["not_predicted_because"] = _not_predicted(out, ("lag24", "lag168", temp_col), "temp_forecast",
                                                  record["reason"])
    return out


def _issued_record(issued: pd.DataFrame) -> dict | None:
    """The day's forecast record as the issue stored it in its rows (None: a v1 issue)."""
    if "forecast_reason" not in issued:
        return None
    first = issued.iloc[0]
    return {"runtime": first["forecast_runtime"], "available_utc": first["available_forecast"],
            "rank": float(first["forecast_rank"]), "lead_h": float(first["forecast_lead_h"]),
            "reason": str(first["forecast_reason"])}


def score_day(state: _State, day: pd.Timestamp, issued: pd.DataFrame, final: bool = False) -> tuple[pd.DataFrame, dict]:
    """Score day D's hours and judge its data quality, one hour after its last hour (`final`: at the end of the data,
    when no later record exists). v2: the day's forecast record, as issued, enters its verdict (`forecast_missing`)."""
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

    expected = f["status"] == STATUS_MASKED_DST
    forecast_record = _issued_record(issued)
    problems = day_problems(hours, raw[(raw["ts_utc"] > o) & (raw["ts_utc"] <= hours[-1])],
                            raw_t[(raw_t["ts_utc"] > o) & (raw_t["ts_utc"] <= hours[-1])], f["status"], temp,
                            forecast_record=forecast_record)
    record = {
        "day": day, "failed": bool(problems), "problems": problems,
        "expected": [{"kind": "fall_back", "utc": t} for t in hours[expected]],
        "spikes": [{"utc": t, "z": float(z)} for t, z in s["spike_z"].items() if np.isfinite(z) and abs(z) > SPIKE_Z],
        "spike_unscored": [t for t, z in s["spike_z"].items() if not np.isfinite(z) and usable[t]],
        "not_predicted": {t: r for t, r in s["not_predicted_because"].items() if r},
        "scored_hours": int(ok.sum()),
        "judged_at_data_end": final,
    }
    if forecast_record is not None:
        record["forecast_record"] = forecast_record
    return s, record


def week_drift(state: _State, week_start: pd.Timestamp, week_hours: pd.DataFrame,
               reference_forecasts: pd.DataFrame, temperature_reference: pd.Series | None = None) -> tuple[dict, dict]:
    """Temperature and forecast drift of one week against the same calendar weeks of earlier years.

    temperature_reference (v2): the issued forecast temperatures the replay holds (those of earlier years at its start
    and every day issued since); the week's current temperature is then its own issued forecast (`temp_forecast`),
    the model's input (plan section 9.3). None: v1's station observation."""
    if temperature_reference is None:
        _, temp, _, _ = state.canonical()
        temp_days = pd.Series(lst_day(temp.index), index=temp.index)
        in_week = (temp_days >= week_start) & (temp_days < week_start + pd.Timedelta(days=7))
        cur_t = temp[in_week].to_numpy()
        ref_t = seasonal_reference(temp, temp_days, week_start)
    else:
        cur_t = week_hours["temp_forecast"].to_numpy(dtype=float)
        ref_days = pd.Series(lst_day(pd.DatetimeIndex(temperature_reference.index)))
        ref_t = seasonal_reference(temperature_reference.reset_index(drop=True), ref_days, week_start)
    cur_f = week_hours["forecast"].to_numpy()
    ref_f = seasonal_reference(reference_forecasts["forecast"], reference_forecasts["day"], week_start)
    # A week with fewer than MIN_WEEK_HOURS current values (the partial first and last weeks) has no drift statistic.
    values = {"temperature": normed_wasserstein(cur_t, ref_t, MIN_WEEK_HOURS),
              "forecast": normed_wasserstein(cur_f, ref_f, MIN_WEEK_HOURS)}
    return values, {"temperature": (cur_t, ref_t), "forecast": (cur_f, ref_f)}


def reads_held_out(last_day: str, data_end: pd.Timestamp) -> bool:
    """Whether a replay of days up to `last_day`, with raw records up to `data_end`, reads the held-out year."""
    return pd.Timestamp(data_end) > DESIGN_LAST or origin(pd.Timestamp(last_day)) + 24 * H > DESIGN_LAST


def reads_2025(last_day: str, data_end: pd.Timestamp) -> bool:
    """Whether a replay of days up to `last_day`, with raw records up to `data_end`, reads past the end of 2024."""
    return pd.Timestamp(data_end) > LAST_BEFORE_2025 or origin(pd.Timestamp(last_day)) + 24 * H > LAST_BEFORE_2025


def score_comparators(hourly: pd.DataFrame, comparators: dict) -> pd.DataFrame:
    """Each comparison model's forecast and interval (pred_<key>, lower_<key>, upper_<key>) on every replayed hour,
    predicted from the inputs the replay recorded when it issued the hour (the hourly frame's columns), never from
    the series as known later. They issue nothing during the replay and touch no monitor (plan sections 4.2, 16)."""
    out = {}
    for key, m in comparators.items():
        p = m.predict(hourly)
        out[f"pred_{key}"], out[f"lower_{key}"], out[f"upper_{key}"] = p["forecast"], p["lower"], p["upper"]
    return pd.DataFrame(out, index=hourly.index)


def replay(raw_demand: pd.DataFrame, raw_temperature: pd.DataFrame, model: FrozenModel, thresholds: dict,
           reference_forecasts: pd.DataFrame, first_day: str, last_day: str, data_end: pd.Timestamp,
           departure: bool = False, runs: pd.DataFrame | None = None, comparators: dict | None = None,
           forecast_reference: pd.Series | None = None, run_id: str | None = None) -> ReplayResult:
    """Replay [first_day, last_day] (local standard days). Raw records after `data_end` are never loaded.

    thresholds: the validation thresholds for this replay; refused unless they come from years before first_day's.
    reference_forecasts: out-of-sample forecasts of earlier years (columns day, forecast), the forecast-drift reference.
    A replay that reads past the design window up to the end of 2024 passes the freeze gate first, here, whoever calls
    it (the command, or the measurements of monitor/sensitivity.py): held_out_gate(2024, departure). A replay that
    reads 2025 is refused unless the run store holds `run_id` as the started 2025 run of this code (check_2025_run):
    the 2025 gate itself ran once, in the command, before that run opened.

    runs None: v1's replay exactly. runs given (v2): the NBM runs (quality.run.load_runs), fed behind the clock at their
    availability; comparators: {model key: FrozenModel}, scored after the loop on the recorded inputs;
    forecast_reference: the temperature-drift reference precomputed (the sensitivity replays pass the main replay's),
    else rebuilt at the start from the runs in view."""
    data_end = pd.Timestamp(data_end)
    if reads_2025(last_day, data_end):
        from valiwatt.monitor.run import check_2025_run  # run.py imports this module

        check_2025_run(run_id)
    elif reads_held_out(last_day, data_end):
        from valiwatt.monitor.run import REPLAY_YEAR, held_out_gate  # run.py imports this module

        held_out_gate(REPLAY_YEAR, departure)
    sha_before = model.sha256()
    comparators = dict(comparators or {})
    if comparators and runs is None:
        raise ValueError("the comparison models are scored on the inputs a v2 replay records: give the runs")
    comparators_before = {k: m.sha256() for k, m in comparators.items()}
    first = pd.Timestamp(first_day)
    last_year = thresholds.get("last_year")
    if last_year is None or int(last_year) >= first.year:
        raise LookaheadError(f"thresholds from weeks up to {last_year} cannot judge a replay starting {first.date()}")
    if "weekly_validation" not in thresholds:
        raise LookaheadError("thresholds carry no list of the weeks they came from: their period cannot be checked")
    late = [w for w in thresholds["weekly_validation"] if pd.Timestamp(w["first_day"]) >= first]
    if late:
        raise LookaheadError(f"thresholds were computed on {len(late)} weeks of the replayed period")
    if not reference_forecasts["day"].lt(first).all():
        raise LookaheadError("forecast-drift reference holds days of the replayed period")
    if forecast_reference is not None and runs is not None and len(forecast_reference) \
            and not (lst_day(pd.DatetimeIndex(forecast_reference.index)) < first).all():
        raise LookaheadError("temperature-drift reference holds days of the replayed period")
    clock = Clock(origin(first) - H)
    feed = None if runs is None else RawFeed(forecast_feed_records(runs, data_end), clock, "forecast")
    state = _State(RawFeed(raw_demand[raw_demand["ts_utc"] <= data_end], clock, "demand"),
                   RawFeed(raw_temperature[raw_temperature["ts_utc"] <= data_end], clock, "temperature"), clock,
                   scale_last=origin(pd.Timestamp(f"{first.year}-01-01")), forecast=feed)
    temperature_reference = None
    if feed is not None:
        temperature_reference = forecast_reference if forecast_reference is not None else issued_reference(feed, first)
    issued_temperatures = []  # each replayed day's issued forecast temperature, appended as it is issued
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
            if feed is not None:
                issued_temperatures.append(usable_temperature(issued[issue_at[t]]["temp_forecast"]))
        for d, final in score_at.get(t, []):
            s, rec = score_day(state, d, issued.pop(d), final=final)
            scored.append(s)
            day_records[d] = rec
            if d.dayofweek == 6 or d == days[-1]:
                week_start = d - pd.Timedelta(days=d.dayofweek)
                wk = pd.concat([x for x in scored if x["day"].iloc[0] >= week_start])
                wdays = [day_records[x] for x in sorted(day_records) if x >= week_start]
                if len(wdays) == 7:
                    reference = None if feed is None else pd.concat([temperature_reference, *issued_temperatures])
                    drift, sample = week_drift(state, week_start, wk, reference_forecasts, reference)
                else:  # a partial first or last week: the weekly monitors stay quiet
                    drift, sample = {"temperature": float("nan"), "forecast": float("nan")}, {}
                rec_w = week_record(week_start, wk.dropna(subset=["ape", "covered"]), wdays, drift, thresholds)
                decision = decide(rec_w, weeks[-1] if weeks else None, weeks[-2] if len(weeks) > 1 else None)
                weeks.append(rec_w)
                decisions.append(decision)
                if any(rec_w["drift_flags"].values()):
                    samples[week_start] = sample
    if model.sha256() != sha_before:
        raise MonitorActedError("the frozen model or its half-width changed during the replay")
    hourly = pd.concat(scored)
    hourly.index.name = "ts_utc"
    record = None
    if feed is not None:
        record = pd.DataFrame([{"origin": origin(d), **{k: day_records[d]["forecast_record"][k] for k in
                                                         ("runtime", "available_utc", "rank", "lead_h", "reason")}}
                               for d in days], index=pd.DatetimeIndex(days, name="day"))
        for c in ("origin", "runtime", "available_utc"):
            record[c] = pd.to_datetime(record[c], utc=True).astype("datetime64[ns, UTC]")
    if comparators:
        changed = [k for k, m in comparators.items() if m.sha256() != comparators_before[k]]
        if changed:
            raise MonitorActedError(f"comparison models {changed} changed during the replay")
        hourly = pd.concat([hourly, score_comparators(hourly, comparators)], axis=1)
        changed = [k for k, m in comparators.items() if m.sha256() != comparators_before[k]]
        if changed:
            raise MonitorActedError(f"comparison models {changed} changed while they were scored")
    return ReplayResult(hourly=hourly, days=[day_records[d] for d in days], weeks=weeks, decisions=decisions,
                        drift_samples=samples, model_sha256=sha_before, clock_end=clock_end, forecast_record=record,
                        temperature_reference=temperature_reference, comparators_sha256=comparators_before)
