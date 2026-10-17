"""Day-ahead features. One row per target hour, with only what is known when the forecast is issued.

The forecast for local-standard-time day D is issued at its origin: the end of day D-1, when every hour of D-1 has
been observed. Under hour-ending labels, that is the label of D-1's last hour (D 00:00 EST = D 05:00 UTC). Every
input of a row is either known at that origin or is the target-hour temperature, which stands in for a weather
forecast (the "observed" scenario) and is labelled as such wherever it is used.

Design code (validation folds, thresholds, frozen models) sees each decision time as the replay sees its clock
(`AsOf`): the demand series is rebuilt from the raw records stamped at or before that time, never read from the
data-quality layer's full-window series, in which a repair can fill an hour with a record that arrives much later.
Each row carries when its day is scored (`scored_at`), so an interval half-width fixed at a fit clock calibrates only
on the days already scored then (evaluate.calibration_rows).

v2's weather input is NOAA's NBM forecast (docs/plan-v2.md, section 3): `forecast_temperature` computes, once and
purely, which run feeds each day and its hourly values; `build` adds them as the "forecast" scenario (and the
six-hour-margin sensitivity as "forecast_late"), each row carrying the feeding run's availability so the origin guard
can refuse a run that was not yet in view. Every temperature a row reads, station or forecast, passes the one bounds
filter of the data-quality layer (quality.weather.usable_temperature), as the replay's does.
"""

from __future__ import annotations

from dataclasses import dataclass

import holidays
import numpy as np
import pandas as pd

from valiwatt.config import DEMAND_FIRST, DESIGN_LAST, LOCAL_TZ, LST_OFFSET
from valiwatt.forecast.guards import LookaheadError, require_forecast_known
from valiwatt.quality import weather
from valiwatt.quality.demand import USABLE, build_demand
from valiwatt.sources.nbm import AVAILABILITY_LAG, MAX_RUNS_BACK, SENSITIVITY_LAG

BASE_C = 18.0
# observed: the target hour's station observation (stands in for a perfect forecast, always labelled); persistence:
# the station 24 hours earlier (v1's production input); forecast: the NBM forecast available two hours after its run
# time; forecast_late: the same with six hours (the availability sensitivity, never chooses).
SCENARIOS = ("observed", "persistence", "forecast", "forecast_late")
FORECAST_LAGS = {"forecast": AVAILABILITY_LAG, "forecast_late": SENSITIVITY_LAG}
REASONS = ("no_run_available", "no_covering_run")
H = pd.Timedelta(hours=1)
LOOKBACK = 200 * H  # the demand a day's rows read behind their origin: more than the 168-hour lag needs
# The nine valid times a covering run must hold for day D, from its origin: D 06:00 to D+1 06:00 UTC every 3 hours,
# so every hour-ending label of D (D 06:00 to D+1 05:00 UTC) lies between two points of that one run.
BRACKET_HOURS = np.arange(1, 26, 3)
AVAILABLE_DTYPE = "datetime64[ns, UTC]"


@dataclass(frozen=True)
class IssuedForecast:
    """The forecast issued for each requested day.

    hourly: °C on the hour-ending UTC labels of the requested days, name "temp_forecast"; NaN where no run fed the day.
    record: one row per local-standard-time day: origin, runtime and available_utc of the feeding run, its rank (0 for
    the freshest available run), lead_h (hours from its run time to the origin), and reason ("" when fed, else one of
    REASONS); runtime, available_utc, rank and lead_h are empty when no run fed the day."""

    hourly: pd.Series
    record: pd.DataFrame


def _covers(points: dict, need: list) -> np.ndarray | None:
    """The run's values (°F) at the nine bracket points, or None when the run does not cover the day.

    points: the run's own points, valid time (UTC, ns since the epoch) -> °F; need: the nine valid times of the day.
    - The owner's ruling (AGENTS.md, v2's weather input): the run's own points include all nine valid times.
    - A data-quality refinement that is NOT part of the ruling (docs/plan-v2.md, sections 1, 3.2 and 16): each of the
      nine holds a finite value, and its Celsius conversion lies inside the bounds of the one filter."""
    if not all(t in points for t in need):
        return None
    values = np.array([points[t] for t in need], dtype=float)
    if not np.isfinite(values).all():
        return None
    celsius = pd.Series((values - 32.0) * 5.0 / 9.0)
    if (weather.usable_temperature(celsius).isna() & celsius.notna()).any():
        return None
    return values


def _ns(values) -> np.ndarray:
    """UTC instants as int64 nanoseconds since the epoch."""
    return pd.DatetimeIndex(values).tz_convert("UTC").as_unit("ns").asi8


def forecast_temperature(runs: pd.DataFrame, days, lag: pd.Timedelta = AVAILABILITY_LAG) -> IssuedForecast:
    """The run rule of docs/plan-v2.md, section 3.2, on each local-standard-time day of `days`. Pure: no file access.

    runs: columns runtime, ftime (UTC) and tmp_f (°F), as `sources.nbm.load_runs` gives them. For day D with origin
    `day_origin(D)`:
    - the candidates are the runs with runtime + lag <= origin (available at or before it), freshest first, at most
      MAX_RUNS_BACK of them, chosen by run identity whatever their age;
    - the first candidate that covers D feeds it (`_covers`: bracketing every hour is the owner's ruling; finite
      points inside the bounds are the data-quality refinement, not part of the ruling);
    - its nine points are interpolated linearly in time, in °F, onto D's 24 hour-ending labels (the label t takes the
      forecast valid at the instant t), then converted as (F - 32) * 5 / 9. Points of two runs are never mixed.
    A day no candidate covers has no forecast, and its record says why (REASONS).
    A point without its run time or its valid time is refused (LookaheadError): its run's availability cannot be
    established (sections 2 and 3.2), and a missing run time would otherwise sort as the oldest run, in view of every
    origin."""
    undated = (runs["runtime"].isna() | runs["ftime"].isna()).to_numpy()
    if undated.any():
        raise LookaheadError(f"{int(undated.sum())} forecast points have no run time or no valid time, so no "
                             f"availability: the run rule reads none of them (first at row {runs.index[undated][0]})")
    days = pd.DatetimeIndex(sorted(set(pd.to_datetime(list(days))))).normalize()
    rt_ns, ft_ns = _ns(runs["runtime"]), _ns(runs["ftime"])
    tmp = runs["tmp_f"].to_numpy(dtype=float)
    points: dict[int, dict[int, float]] = {}
    for r, t, v in zip(rt_ns.tolist(), ft_ns.tolist(), tmp.tolist()):
        points.setdefault(r, {})[t] = v
    runtimes = pd.DatetimeIndex(sorted(points), tz="UTC")
    available = runtimes + lag
    hour_ns = int(H.value)
    labels_h = np.arange(1, 25, dtype=float)

    hourly = np.full(24 * len(days), np.nan)
    rows = []
    origins = pd.DatetimeIndex([day_origin(d) for d in days])
    for i, o in enumerate(origins):
        need = [int(o.value) + int(h) * hour_ns for h in BRACKET_HOURS]
        k = int(available.searchsorted(o, side="right"))  # the runs with runtime + lag <= origin
        candidates = runtimes[max(0, k - MAX_RUNS_BACK):k][::-1]
        row = {"origin": o, "runtime": pd.NaT, "available_utc": pd.NaT, "rank": np.nan, "lead_h": np.nan,
               "reason": REASONS[0] if len(candidates) == 0 else REASONS[1]}
        for rank, rt in enumerate(candidates):
            values = _covers(points[int(rt.value)], need)
            if values is None:
                continue
            fahrenheit = np.interp(labels_h, BRACKET_HOURS.astype(float), values)
            hourly[24 * i: 24 * i + 24] = (fahrenheit - 32.0) * 5.0 / 9.0
            row.update(runtime=rt, available_utc=rt + lag, rank=float(rank), lead_h=(o - rt) / H, reason="")
            break
        rows.append(row)

    index = pd.DatetimeIndex((origins.repeat(24) + pd.to_timedelta(np.tile(np.arange(1, 25), len(days)), unit="h")),
                             name="ts_utc")
    record = pd.DataFrame(rows, index=pd.DatetimeIndex(days, name="day"),
                          columns=["origin", "runtime", "available_utc", "rank", "lead_h", "reason"])
    for c in ("origin", "runtime", "available_utc"):
        record[c] = pd.to_datetime(record[c], utc=True).astype(AVAILABLE_DTYPE)
    record["rank"] = record["rank"].astype(float)
    record["lead_h"] = record["lead_h"].astype(float)
    return IssuedForecast(hourly=pd.Series(hourly, index=index, name="temp_forecast"), record=record)


def origin_of(ts_hour_ending: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The issue time of the forecast covering each target hour: the hour-ending label of the previous LST day's
    last hour."""
    start_lst = (ts_hour_ending - H + LST_OFFSET).tz_convert(None)
    day = start_lst.normalize()
    return pd.DatetimeIndex(day - LST_OFFSET).tz_localize("UTC")


def day_origin(day) -> pd.Timestamp:
    """The origin of local-standard-time day D's forecast: D 00:00 EST, the label of D-1's last hour (D 05:00 UTC)."""
    return (pd.Timestamp(day) - LST_OFFSET).tz_localize("UTC")


def score_clock(day, last: pd.Timestamp) -> pd.Timestamp:
    """When day D is scored: one hour after its last hour, since the next record shows whether that hour was a spike;
    at `last` when the data end before then (the replay's `judged_at_data_end`)."""
    return min(day_origin(pd.Timestamp(day) + pd.Timedelta(days=1)) + H, pd.Timestamp(last))


def scored_at(days: pd.Series, last: pd.Timestamp) -> pd.Series:
    """`score_clock` of each row's day, the data ending at `last`."""
    clocks = {d: score_clock(d, last) for d in pd.unique(days)}
    return days.map(clocks)


def quebec_holidays(years) -> set:
    return set(holidays.Canada(subdiv="QC", years=list(years)).keys())


def day_type(dates: pd.DatetimeIndex, hol: set) -> np.ndarray:
    """weekday, saturday, or sunday_holiday (Sundays and Québec statutory holidays)."""
    dow = dates.dayofweek
    is_hol = np.array([d.date() in hol for d in dates])
    return np.where(is_hol | (dow == 6), "sunday_holiday", np.where(dow == 5, "saturday", "weekday"))


def build(demand: pd.DataFrame, temperature: pd.Series,
          forecasts: dict[str, IssuedForecast] | None = None) -> pd.DataFrame:
    """Feature table indexed by target hour (UTC, hour-ending).

    demand: canonical frame (mw, status) on the hourly grid; temperature: the chosen station source on the same grid;
    forecasts: the issued forecast of each forecast scenario given ("forecast", "forecast_late"; `forecast_temperature`).
    scored_at: when the row's day is scored, the data ending with the table (`score_clock`).

    Every temperature passes the one bounds filter (quality.weather.usable_temperature). The table always holds
    temp_<s>, hdh_<s> and cdh_<s> of every scenario (NaN for a forecast scenario not given) and, for each forecast
    scenario, available_<s>: when the run that fed the row's day became available (NaT when none did). v1's columns
    come first, in v1's order, with v1's values.

    A forecast value is taken only with its availability (docs/plan-v2.md, sections 2 and 3.2): a row holding a finite
    input of a scenario given whose availability is missing (its day absent from the record, or the record's
    available_utc NaT) or after its origin is refused (LookaheadError, guards.require_forecast_known), before any
    consumer reads the table."""
    idx = demand.index
    mw = demand["mw"].where(demand["status"].isin(USABLE))
    temp = weather.usable_temperature(temperature.reindex(idx))
    start_local = (idx - H).tz_convert(LOCAL_TZ)
    lst_day = (idx - H + LST_OFFSET).tz_convert(None).normalize()
    hol = quebec_holidays(range(lst_day.min().year, lst_day.max().year + 1))
    f = pd.DataFrame(index=idx)
    f["target"] = mw
    f["origin"] = origin_of(idx)
    f["day"] = lst_day
    f["scored_at"] = scored_at(f["day"], idx.max())
    f["hour"] = start_local.hour  # the local wall-clock hour the demand hour starts in
    f["day_type"] = day_type(pd.DatetimeIndex(start_local.tz_localize(None).normalize()), hol)
    f["lag24"] = mw.reindex(idx - 24 * H).to_numpy()
    f["lag168"] = mw.reindex(idx - 168 * H).to_numpy()
    f["temp_observed"] = temp.to_numpy()
    f["temp_persistence"] = temp.reindex(idx - 24 * H).to_numpy()
    station, forecast = SCENARIOS[:2], SCENARIOS[2:]
    for s in station:
        f[f"hdh_{s}"] = np.maximum(BASE_C - f[f"temp_{s}"], 0.0)
        f[f"cdh_{s}"] = np.maximum(f[f"temp_{s}"] - BASE_C, 0.0)
    given = forecasts or {}
    unknown = sorted(set(given) - set(FORECAST_LAGS))
    if unknown:
        raise ValueError(f"no forecast scenario {unknown}; the forecast scenarios are {list(FORECAST_LAGS)}")
    for s in forecast:
        issued = given.get(s)
        if issued is None:
            f[f"temp_{s}"] = np.nan
            f[f"available_{s}"] = pd.Series(pd.NaT, index=idx, dtype=AVAILABLE_DTYPE)
        else:
            f[f"temp_{s}"] = weather.usable_temperature(issued.hourly.reindex(idx)).to_numpy()
            fed = issued.record["available_utc"].astype(AVAILABLE_DTYPE)
            f[f"available_{s}"] = pd.Series(f["day"].map(fed), index=idx).astype(AVAILABLE_DTYPE)
        f[f"hdh_{s}"] = np.maximum(BASE_C - f[f"temp_{s}"], 0.0)
        f[f"cdh_{s}"] = np.maximum(f[f"temp_{s}"] - BASE_C, 0.0)
    require_forecast_known(f, tuple(given), "features.build")
    return f


def known_at_origin(f: pd.DataFrame) -> pd.Series:
    """For each row: are the timestamps its lag and persistence inputs come from at or before the origin? The
    inputs 24 hours back are the latest; the 168-hour lag is earlier still, so it needs no term of its own."""
    return (f.index - 24 * H) <= f["origin"]


class AsOf:
    """The demand series as the replay sees it at a clock: `build_demand` on the raw records stamped at or before the
    clock, over the grid from `first` to the clock (monitor/replay.py, `_State.canonical`). Records after `last` are
    never loaded; asking for a later clock is refused.

    The decision times design code uses:
    - a fit (a validation fold, a frozen model) at the origin of its first test day, the latest moment the model must
      exist (`walkforward.Fold.fit_clock`); the embargo still decides which rows it fits on;
    - a test row's lags at its day's origin, as the replay issues the forecast;
    - a test row's target when its day is scored (`score_clock`), as the replay scores it.
    A test row's target is fixed at its day's scoring; the same hour in a later fit's training rows is as of that fit.
    """

    def __init__(self, raw: pd.DataFrame, first: pd.Timestamp = DEMAND_FIRST, last: pd.Timestamp = DESIGN_LAST):
        self.first, self.last = pd.Timestamp(first), pd.Timestamp(last)
        raw = raw[raw["ts_utc"] <= self.last]
        self._raw = raw.sort_values("ts_utc", kind="stable").reset_index(drop=True)
        self._issued: pd.DataFrame | None = None

    def frame(self, clock: pd.Timestamp) -> pd.DataFrame:
        """The canonical demand frame (mw, status, ...) on the hourly grid from `first` to the clock."""
        clock = pd.Timestamp(clock)
        if clock > self.last:
            raise LookaheadError(f"demand asked as of {clock}, after the last record loaded ({self.last})")
        # The records stamped at or before the clock, as the replay's feed gives them (build_demand would drop the
        # later ones from its grid anyway, so no guard here could fire).
        cut = self._raw.iloc[: int(self._raw["ts_utc"].searchsorted(clock, side="right"))]
        if clock < self.first:
            empty = pd.DatetimeIndex([], tz="UTC", name="ts_utc")
            return pd.DataFrame({"mw": pd.Series(dtype=float, index=empty),
                                 "status": pd.Series(dtype=object, index=empty)})
        # The spike scale does not enter the features; it is taken on every hour in view.
        return build_demand(cut, self.first, clock, scale_last=clock).frame

    def table(self, clock: pd.Timestamp, temperature: pd.Series,
              forecasts: dict[str, IssuedForecast] | None = None) -> pd.DataFrame:
        """The feature table a fit at `clock` sees: every row up to the clock, its target and lags from the demand as
        of the clock. A row's forecast is the one issued at its own origin, at or before the clock."""
        return build(self.frame(clock), temperature, forecasts)

    def _issued_demand(self) -> pd.DataFrame:
        """target, lag24 and lag168 of every hour from `first` to `last`, each as the replay has it: the lags as of the
        hour's origin, the target as of its day's scoring. Rebuilt once per day, cached."""
        if self._issued is None:
            parts = []
            first_day = (self.first - H + LST_OFFSET).tz_convert(None).normalize()
            last_day = (self.last - H + LST_OFFSET).tz_convert(None).normalize()
            for day in pd.date_range(first_day, last_day, freq="D"):
                o = day_origin(day)
                hours = pd.date_range(o + H, o + 24 * H, freq="h")
                window = pd.date_range(o - LOOKBACK, o + 24 * H, freq="h")
                dem = self.frame(o).reindex(window)
                dem.loc[hours, ["mw", "status"]] = [np.nan, None]  # not yet observed at the origin
                rows = build(dem, pd.Series(np.nan, index=window)).loc[hours, ["lag24", "lag168"]]
                scored = self.frame(score_clock(day, self.last)).reindex(hours)
                rows["target"] = build(scored, pd.Series(np.nan, index=hours))["target"]
                parts.append(rows)
            out = pd.concat(parts)
            self._issued = out[(out.index >= self.first) & (out.index <= self.last)]
        return self._issued

    def issued(self, temperature: pd.Series, forecasts: dict[str, IssuedForecast] | None = None) -> pd.DataFrame:
        """The feature table of test rows from `first` to `last`, each as the replay issues and scores it."""
        f = build(self.frame(self.last), temperature, forecasts)
        d = self._issued_demand().reindex(f.index)
        for c in ("target", "lag24", "lag168"):
            f[c] = d[c].to_numpy()
        return f
