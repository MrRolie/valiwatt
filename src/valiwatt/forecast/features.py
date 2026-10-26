"""Day-ahead features. One row per target hour, with only what is known when the forecast is issued.

The forecast for local-standard-time day D is issued at its origin (forecast/issue.py, the one home of the clocks). For
v2 (MIDNIGHT) that is the end of day D-1, when every hour of D-1 has been observed: under hour-ending labels, the label
of D-1's last hour (D 00:00 EST = D 05:00 UTC), D's edge. For v3 (NOON, docs/plan-v3.md, section 2) it is 12 hours
earlier, D-1 17:00 UTC. D's labels (D 06:00 to D+1 05:00 UTC) and its scoring (D+1 06:00 UTC) never move with the
origin. Every input of a row is either known at that origin or is the target-hour temperature, which stands in for a
weather forecast (the "observed" scenario) and is labelled as such wherever it is used.

Design code (validation folds, thresholds, frozen models) sees each decision time as the replay sees its clock
(`AsOf`): the demand series is rebuilt from the raw records stamped at or before that time, never read from the
data-quality layer's full-window series, in which a repair can fill an hour with a record that arrives much later.
Each row carries when its day is scored (`scored_at`), so an interval half-width fixed at a fit clock calibrates only
on the days already scored then (evaluate.calibration_rows).

v2's weather input is NOAA's NBM forecast (docs/plan-v2.md, section 3): `forecast_temperature` computes, once and
purely, which run feeds each day and its hourly values; `build` adds them as the "forecast" scenario (and the
six-hour-margin sensitivity as "forecast_late"), each row carrying the feeding run's availability so the origin guard
can refuse a run that was not yet in view. Every temperature a row reads, station or forecast, passes the one bounds
filter of the data-quality layer (quality.weather.usable_temperature), as the replay's does. v3 (an IssueSpec with
`wind`) also reads the same run's wind through the one wind filter (quality.weather.usable_wind) and its day's mean
temperature; with `lag_day` the station's mean over D-2 (`observed_day_mean`, plan-v3 section 3.3).
"""

from __future__ import annotations

from dataclasses import dataclass

import holidays
import numpy as np
import pandas as pd

from valiwatt.config import DEMAND_FIRST, DESIGN_LAST, LOCAL_TZ
from valiwatt.forecast.guards import LookaheadError, min_lag_hours, require_forecast_known
from valiwatt.forecast.issue import (  # noqa: F401 (day_origin, origin_of, score_clock: re-exported for v2's imports)
    H,
    KNOTS_TO_KMH,
    LAG_DAY_MIN_HOURS,
    MIDNIGHT,
    IssueSpec,
    day_edge,
    day_labels,
    day_origin,
    lst_day,
    origin_of,
    score_clock,
)
from valiwatt.quality import weather
from valiwatt.quality.demand import USABLE, build_demand
from valiwatt.sources.nbm import AVAILABILITY_LAG, MAX_RUNS_BACK, SENSITIVITY_LAG

BASE_C = 18.0
# observed: the target hour's station observation (stands in for a perfect forecast, always labelled); persistence:
# the station 24 hours earlier (v1's production input; MIDNIGHT only, IssueSpec.station_scenarios); forecast: the NBM
# forecast available two hours after its run time; forecast_late: the same with six hours (the availability
# sensitivity, never chooses). Both clocks keep both margins (plan-v3 sections 3.2 and 3.4).
SCENARIOS = ("observed", "persistence", "forecast", "forecast_late")
FORECAST_LAGS = {"forecast": AVAILABILITY_LAG, "forecast_late": SENSITIVITY_LAG}
REASONS = ("no_run_available", "no_covering_run")
LOOKBACK = 200 * H  # the demand a day's rows read behind their origin: more than the 168-hour lag needs at either clock
# The nine valid times a covering run must hold for day D, counted from D's edge (D 05:00 UTC, whatever the origin):
# D 06:00 to D+1 06:00 UTC every 3 hours, so every hour-ending label of D (D 06:00 to D+1 05:00 UTC) lies between two
# points of that one run.
BRACKET_HOURS = np.arange(1, 26, 3)
AVAILABLE_DTYPE = "datetime64[ns, UTC]"
DAY = pd.Timedelta(days=1)


@dataclass(frozen=True)
class IssuedForecast:
    """The forecast issued for each requested day.

    hourly: °C on the hour-ending UTC labels of the requested days, name "temp_forecast"; NaN where no run fed the day.
    record: one row per local-standard-time day: origin, runtime and available_utc of the feeding run, its rank (0 for
    the freshest available run), lead_h (hours from its run time to the origin), and reason ("" when fed, else one of
    REASONS); runtime, available_utc, rank and lead_h are empty when no run fed the day. Issued with wind (plan-v3,
    section 3.2), it also holds day_mean_c: the mean of the day's 24 hourly °C values (NaN when unfed).
    wind: issued with wind only, km/h on the same labels, name "wind_forecast"; None otherwise."""

    hourly: pd.Series
    record: pd.DataFrame
    wind: pd.Series | None = None


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


def _wind_covers(points: dict, need: list) -> np.ndarray | None:
    """The run's wind (knots) at the nine bracket points, or None when one is missing, blank or outside the wind bounds
    of the one wind filter (quality.weather.usable_wind): the wind clause of plan-v3's covering rule (section 3.2).

    points: the run's own points, valid time (UTC, ns since the epoch) -> knots; need: the nine valid times of the
    day."""
    if not all(t in points for t in need):
        return None
    values = np.array([points[t] for t in need], dtype=float)
    if weather.usable_wind(pd.Series(values)).isna().any():  # blank, not finite or outside WIND_BOUNDS_KT
        return None
    return values


def _ns(values) -> np.ndarray:
    """UTC instants as int64 nanoseconds since the epoch."""
    return pd.DatetimeIndex(values).tz_convert("UTC").as_unit("ns").asi8


def _points(rt_ns: np.ndarray, ft_ns: np.ndarray, values: np.ndarray) -> dict[int, dict[int, float]]:
    """run time -> {valid time -> value}, all in ns since the epoch."""
    points: dict[int, dict[int, float]] = {}
    for r, t, v in zip(rt_ns.tolist(), ft_ns.tolist(), values.tolist()):
        points.setdefault(r, {})[t] = v
    return points


def forecast_temperature(runs: pd.DataFrame, days, lag: pd.Timedelta = AVAILABILITY_LAG, *,
                         issue: IssueSpec = MIDNIGHT) -> IssuedForecast:
    """The run rule of docs/plan-v2.md, section 3.2 (plan-v3 section 3.2 at NOON), on each local-standard-time day of
    `days`. Pure: no file access.

    runs: columns runtime, ftime (UTC) and tmp_f (°F), as `sources.nbm.load_runs` gives them; with `issue.wind`, also
    wsp_kt (knots), else refused. For day D with origin `day_origin(D, issue=issue)`:
    - the candidates are the runs with runtime + lag <= origin (available at or before it), freshest first, at most
      MAX_RUNS_BACK of them, chosen by run identity whatever their age;
    - the first candidate that covers D feeds it (`_covers`: bracketing every hour of D, its nine points counted from
      D's edge whatever the origin, is the owner's ruling; finite points inside the bounds are the data-quality
      refinement, not part of the ruling; with `issue.wind`, `_wind_covers` too: nine finite winds inside the wind
      bounds);
    - its nine points are interpolated linearly in time, in °F, onto D's 24 hour-ending labels (the label t takes the
      forecast valid at the instant t), then converted as (F - 32) * 5 / 9. Points of two runs are never mixed. With
      `issue.wind`, its winds likewise in knots, then times KNOTS_TO_KMH; the record's day_mean_c is the mean of the
      24 °C values.
    A day no candidate covers has no forecast, and its record says why (REASONS).
    A point without its run time or its valid time is refused (LookaheadError): its run's availability cannot be
    established (sections 2 and 3.2), and a missing run time would otherwise sort as the oldest run, in view of every
    origin."""
    undated = (runs["runtime"].isna() | runs["ftime"].isna()).to_numpy()
    if undated.any():
        raise LookaheadError(f"{int(undated.sum())} forecast points have no run time or no valid time, so no "
                             f"availability: the run rule reads none of them (first at row {runs.index[undated][0]})")
    if issue.wind and "wsp_kt" not in runs.columns:
        raise ValueError(f"the {issue.name} feed reads the wind (plan-v3, section 3.2): the runs carry no wsp_kt")
    days = pd.DatetimeIndex(sorted(set(pd.to_datetime(list(days))))).normalize()
    rt_ns, ft_ns = _ns(runs["runtime"]), _ns(runs["ftime"])
    points = _points(rt_ns, ft_ns, runs["tmp_f"].to_numpy(dtype=float))
    winds = _points(rt_ns, ft_ns, runs["wsp_kt"].to_numpy(dtype=float)) if issue.wind else {}
    runtimes = pd.DatetimeIndex(sorted(points), tz="UTC")
    available = runtimes + lag
    hour_ns = int(H.value)
    labels_h = np.arange(1, 25, dtype=float)

    hourly = np.full(24 * len(days), np.nan)
    wind = np.full(24 * len(days), np.nan)
    rows = []
    edges = pd.DatetimeIndex([day_edge(d) for d in days])
    origins = pd.DatetimeIndex([day_origin(d, issue=issue) for d in days])
    for i, (e, o) in enumerate(zip(edges, origins)):
        need = [int(e.value) + int(h) * hour_ns for h in BRACKET_HOURS]
        k = int(available.searchsorted(o, side="right"))  # the runs with runtime + lag <= origin
        candidates = runtimes[max(0, k - MAX_RUNS_BACK):k][::-1]
        row = {"origin": o, "runtime": pd.NaT, "available_utc": pd.NaT, "rank": np.nan, "lead_h": np.nan,
               "reason": REASONS[0] if len(candidates) == 0 else REASONS[1]}
        if issue.wind:
            row["day_mean_c"] = np.nan
        for rank, rt in enumerate(candidates):
            values = _covers(points[int(rt.value)], need)
            knots = _wind_covers(winds[int(rt.value)], need) if issue.wind else None
            if values is None or (issue.wind and knots is None):
                continue
            fahrenheit = np.interp(labels_h, BRACKET_HOURS.astype(float), values)
            hourly[24 * i: 24 * i + 24] = (fahrenheit - 32.0) * 5.0 / 9.0
            row.update(runtime=rt, available_utc=rt + lag, rank=float(rank), lead_h=(o - rt) / H, reason="")
            if issue.wind:
                wind[24 * i: 24 * i + 24] = np.interp(labels_h, BRACKET_HOURS.astype(float), knots) * KNOTS_TO_KMH
                row["day_mean_c"] = float(hourly[24 * i: 24 * i + 24].mean())
            break
        rows.append(row)

    index = pd.DatetimeIndex((edges.repeat(24) + pd.to_timedelta(np.tile(np.arange(1, 25), len(days)), unit="h")),
                             name="ts_utc")
    columns = ["origin", "runtime", "available_utc", "rank", "lead_h", "reason"]
    columns += ["day_mean_c"] if issue.wind else []
    record = pd.DataFrame(rows, index=pd.DatetimeIndex(days, name="day"), columns=columns)
    for c in ("origin", "runtime", "available_utc"):
        record[c] = pd.to_datetime(record[c], utc=True).astype(AVAILABLE_DTYPE)
    record["rank"] = record["rank"].astype(float)
    record["lead_h"] = record["lead_h"].astype(float)
    if issue.wind:
        record["day_mean_c"] = record["day_mean_c"].astype(float)
    return IssuedForecast(hourly=pd.Series(hourly, index=index, name="temp_forecast"), record=record,
                          wind=pd.Series(wind, index=index, name="wind_forecast") if issue.wind else None)


def scored_at(days: pd.Series, last: pd.Timestamp) -> pd.Series:
    """`score_clock` of each row's day, the data ending at `last`."""
    clocks = {d: score_clock(d, last) for d in pd.unique(days)}
    return days.map(clocks)


def observed_day_mean(temperature: pd.Series, *, min_hours: int = LAG_DAY_MIN_HOURS) -> pd.DataFrame:
    """The station's mean over each local-standard-time day's 24 labels (plan-v3, section 3.3). Pure.

    temperature: the station on hour-ending UTC labels, read through the one bounds filter
    (quality.weather.usable_temperature), each observation at its own label. One row per LST day holding an
    observation (index `day`): n_usable, its usable observations; mean_c, their mean when n_usable >= min_hours, else
    NaN (nothing is imputed, plan-v3 section 5); last_utc, the last label it read (NaT when mean_c is missing).
    `build` reads it twice: day D-2's row as D's lag day's mean, D's own as the observed-weather bound's day mean.
    Design code and the replay call this one function on their own station series. A series with a duplicated label
    is refused (ValueError): its hour would count twice toward min_hours."""
    dup = pd.Index(temperature.index).duplicated()
    if dup.any():
        raise ValueError(f"observed_day_mean: {int(dup.sum())} duplicated labels in the station series (first "
                         f"{temperature.index[dup][0]}): each hour counts once toward the lag day's mean")
    usable = weather.usable_temperature(temperature).dropna()
    labels = pd.DatetimeIndex(usable.index)
    by = pd.DataFrame({"temp": usable.to_numpy(dtype=float), "ts": labels}, index=lst_day(labels)).groupby(level=0)
    n = by["temp"].count()
    ok = n >= int(min_hours)
    out = pd.DataFrame({"mean_c": by["temp"].mean().where(ok), "n_usable": n.astype(int),
                        "last_utc": pd.Series(by["ts"].max(), index=n.index).where(ok)})
    out["last_utc"] = pd.to_datetime(out["last_utc"], utc=True).astype(AVAILABLE_DTYPE)
    out.index = pd.DatetimeIndex(out.index, name="day")
    return out


def quebec_holidays(years) -> set:
    return set(holidays.Canada(subdiv="QC", years=list(years)).keys())


def day_type(dates: pd.DatetimeIndex, hol: set) -> np.ndarray:
    """weekday, saturday, or sunday_holiday (Sundays and Québec statutory holidays)."""
    dow = dates.dayofweek
    is_hol = np.array([d.date() in hol for d in dates])
    return np.where(is_hol | (dow == 6), "sunday_holiday", np.where(dow == 5, "saturday", "weekday"))


def build(demand: pd.DataFrame, temperature: pd.Series,
          forecasts: dict[str, IssuedForecast] | None = None, *, issue: IssueSpec = MIDNIGHT) -> pd.DataFrame:
    """Feature table indexed by target hour (UTC, hour-ending), issued at `issue`'s origin.

    demand: canonical frame (mw, status) on the hourly grid; temperature: the chosen station source on the same grid;
    forecasts: the issued forecast of each forecast scenario given ("forecast", "forecast_late"; `forecast_temperature`
    with the same `issue`). scored_at: when the row's day is scored, the data ending with the table (`score_clock`).

    Every temperature passes the one bounds filter (quality.weather.usable_temperature). The table holds the lags of
    `issue.lags_h` (lag<h>), temp_<s>, hdh_<s> and cdh_<s> of every station scenario of the spec
    (`issue.station_scenarios`) and of both forecast scenarios (NaN for one not given) and, for each forecast scenario,
    available_<s>: when the run that fed the row's day became available (NaT when none did). At MIDNIGHT, v1's columns
    come first, in v1's order, with v1's values, and v2's table is unchanged. With `issue.wind`: wind_<s> (km/h) and
    daymean_<s> (°C) of the same run, refused when a forecast given was issued without wind. With `issue.lag_day`
    (plan-v3 section 3.3, `observed_day_mean` on `temperature`): lagday_observed, D-2's mean (NaN under
    LAG_DAY_MIN_HOURS usable hours), lagday_last_utc, the last label it read (NaT when missing), and daymean_observed,
    D's own (the observed-weather bound only).

    A forecast value is taken only with its availability (docs/plan-v2.md, sections 2 and 3.2): a row holding a finite
    input of a scenario given whose availability is missing (its day absent from the record, or the record's
    available_utc NaT) or after its origin is refused (LookaheadError, guards.require_forecast_known), before any
    consumer reads the table."""
    idx = demand.index
    mw = demand["mw"].where(demand["status"].isin(USABLE))
    temp = weather.usable_temperature(temperature.reindex(idx))
    start_local = (idx - H).tz_convert(LOCAL_TZ)
    days = lst_day(idx)
    hol = quebec_holidays(range(days.min().year, days.max().year + 1))
    f = pd.DataFrame(index=idx)
    f["target"] = mw
    f["origin"] = origin_of(idx, issue=issue)
    f["day"] = days
    f["scored_at"] = scored_at(f["day"], idx.max())
    f["hour"] = start_local.hour  # the local wall-clock hour the demand hour starts in
    f["day_type"] = day_type(pd.DatetimeIndex(start_local.tz_localize(None).normalize()), hol)
    for h in issue.lags_h:
        f[f"lag{h}"] = mw.reindex(idx - h * H).to_numpy()
    f["temp_observed"] = temp.to_numpy()
    if issue.persistence:
        f["temp_persistence"] = temp.reindex(idx - 24 * H).to_numpy()
    for s in issue.station_scenarios:
        f[f"hdh_{s}"] = np.maximum(BASE_C - f[f"temp_{s}"], 0.0)
        f[f"cdh_{s}"] = np.maximum(f[f"temp_{s}"] - BASE_C, 0.0)
    given = forecasts or {}
    unknown = sorted(set(given) - set(FORECAST_LAGS))
    if unknown:
        raise ValueError(f"no forecast scenario {unknown}; the forecast scenarios are {list(FORECAST_LAGS)}")
    for s in FORECAST_LAGS:
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
        if issue.wind:
            if issued is None:
                f[f"wind_{s}"], f[f"daymean_{s}"] = np.nan, np.nan
            elif issued.wind is None or "day_mean_c" not in issued.record.columns:
                raise ValueError(f"the {issue.name} table reads the wind and the day's mean (plan-v3, section 3.2): "
                                 f"the {s} forecast given was issued without them")
            else:
                f[f"wind_{s}"] = issued.wind.reindex(idx).to_numpy(dtype=float)
                f[f"daymean_{s}"] = f["day"].map(issued.record["day_mean_c"]).to_numpy(dtype=float)
    if issue.lag_day:
        means = observed_day_mean(temperature)
        lag = means.reindex(pd.DatetimeIndex(f["day"]) - 2 * DAY)
        f["lagday_observed"] = lag["mean_c"].to_numpy(dtype=float)
        f["lagday_last_utc"] = pd.Series(lag["last_utc"].array, index=idx).astype(AVAILABLE_DTYPE)
        f["daymean_observed"] = means["mean_c"].reindex(pd.DatetimeIndex(f["day"])).to_numpy(dtype=float)
    require_forecast_known(f, tuple(given), "features.build")
    return f


def known_at_origin(f: pd.DataFrame) -> pd.Series:
    """For each row: are the timestamps its lag and persistence inputs come from at or before the origin? The
    shortest lag the table holds is the latest (guards.min_lag_hours: 24 hours at MIDNIGHT, 48 at NOON); the 168-hour
    lag is earlier still, so it needs no term of its own."""
    return (f.index - min_lag_hours(f) * H) <= f["origin"]


class AsOf:
    """The demand series as the replay sees it at a clock: `build_demand` on the raw records stamped at or before the
    clock, over the grid from `first` to the clock (monitor/replay.py, `_State.canonical`). Records after `last` are
    never loaded; asking for a later clock is refused.

    The decision times design code uses, at `issue`'s clocks (forecast/issue.py):
    - a fit (a validation fold, a frozen model) at the origin of its first test day, the latest moment the model must
      exist (`walkforward.Fold.fit_clock`); the embargo still decides which rows it fits on;
    - a test row's lags at its day's origin, as the replay issues the forecast;
    - a test row's target when its day is scored (`score_clock`, D+1 06:00 UTC at either clock), as the replay scores
      it.
    A test row's target is fixed at its day's scoring; the same hour in a later fit's training rows is as of that fit.
    """

    def __init__(self, raw: pd.DataFrame, first: pd.Timestamp = DEMAND_FIRST, last: pd.Timestamp = DESIGN_LAST, *,
                 issue: IssueSpec = MIDNIGHT):
        self.first, self.last, self.issue = pd.Timestamp(first), pd.Timestamp(last), issue
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
        return build(self.frame(clock), temperature, forecasts, issue=self.issue)

    def _issued_demand(self) -> pd.DataFrame:
        """target and the lags of `issue` of every hour from `first` to `last`, each as the replay has it: the lags as
        of the hour's origin, the target as of its day's scoring. Rebuilt once per day, cached. The window reaches D's
        last label: at NOON the hours from the origin to D's edge are simply absent from the frame as of the origin."""
        if self._issued is None:
            parts = []
            lags = list(self.issue.lag_columns)
            first_day, last_day = lst_day(pd.DatetimeIndex([self.first, self.last]))
            for day in pd.date_range(first_day, last_day, freq="D"):
                o = day_origin(day, issue=self.issue)
                hours = day_labels(day)
                window = pd.date_range(o - LOOKBACK, hours[-1], freq="h")
                dem = self.frame(o).reindex(window)
                dem.loc[hours, ["mw", "status"]] = [np.nan, None]  # not yet observed at the origin
                rows = build(dem, pd.Series(np.nan, index=window), issue=self.issue).loc[hours, lags]
                scored = self.frame(score_clock(day, self.last)).reindex(hours)
                rows["target"] = build(scored, pd.Series(np.nan, index=hours), issue=self.issue)["target"]
                parts.append(rows)
            out = pd.concat(parts)
            self._issued = out[(out.index >= self.first) & (out.index <= self.last)]
        return self._issued

    def issued(self, temperature: pd.Series, forecasts: dict[str, IssuedForecast] | None = None) -> pd.DataFrame:
        """The feature table of test rows from `first` to `last`, each as the replay issues and scores it."""
        f = build(self.frame(self.last), temperature, forecasts, issue=self.issue)
        d = self._issued_demand().reindex(f.index)
        for c in ("target", *self.issue.lag_columns):
            f[c] = d[c].to_numpy()
        return f
