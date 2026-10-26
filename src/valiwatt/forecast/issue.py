"""When a forecast is issued, and what it reads (docs/plan-v3.md, section 2).

Three clocks, kept apart. Local-standard-time day D owns the 24 hour-ending labels D 06:00 to D+1 05:00 UTC; its
**edge** is D 05:00 UTC, the label of D-1's last hour. D is **scored** one hour after its last hour, D+1 06:00 UTC,
whatever the issue time. D's forecast is **issued** at its origin: the edge for v2 (MIDNIGHT, D 00:00 EST) and 12 hours
before it for v3 (NOON, D-1 12:00 EST). v2's clocks are MIDNIGHT exactly, so every v2 path that takes an IssueSpec and
is given MIDNIGHT computes what it computed before v3.

The column contract of a feature table built with NOON (forecast/features.py), read by v3's models (forecast/model.py):

- `lag48`, `lag168`: demand 48 h and 168 h before the target hour (MIDNIGHT: `lag24`, `lag168`);
- per forecast scenario s in ("forecast", "forecast_late"): `temp_s` (°C), `available_s`, `hdh_s`, `cdh_s`, and with
  wind: `wind_s` (km/h, the run's knots interpolated in time, then times KNOTS_TO_KMH) and `daymean_s` (°C, the mean of
  the 24 hourly °C values of D from the same run);
- `temp_observed`, `hdh_observed`, `cdh_observed`: the station at the target hour (the observed-weather bound only);
- with the lag day: `lagday_observed` (°C, the station's mean over D-2's 24 labels, D-2 06:00 to D-1 05:00 UTC, when at
  least LAG_DAY_MIN_HOURS are usable; NaN otherwise), `lagday_last_utc` (the last label it read; NaT when missing) and
  `daymean_observed` (the same rule over D's own labels, for the observed-weather bound);
- no `lag24` and no `temp_persistence`: neither is known at noon for half of D.

The three terms (docs/plan-v3.md, section 4.1) are computed by the model from those raw columns with TERM_DEFINITIONS,
which a frozen model stores and checks:

- day's mean cold: max(0, base_c - daymean_<temperature scenario>);
- lag day's mean cold: max(0, base_c - lagday_observed);
- wind with cold: wind_<wind scenario> * max(0, base_c - temp_<temperature scenario>).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from valiwatt.config import LST_OFFSET

H = pd.Timedelta(hours=1)
KNOTS_TO_KMH = 1.852
LAG_DAY_MIN_HOURS = 20
LAG_DAY_REASON = "lag_day_temperature_missing"
TERMS = ("day_mean_cold", "lag_day_mean_cold", "wind_cold")
# What a v3 term means, stored in and checked by every frozen model that reads a term. The lag day's labels are given
# in hours after D's edge: D-2 06:00 UTC is 47 hours before it, D-1 05:00 UTC 24 hours before.
TERM_DEFINITIONS = {"base_c": 18.0, "lag_day_first_h": -47, "lag_day_last_h": -24,
                    "lag_day_min_hours": LAG_DAY_MIN_HOURS, "knots_to_kmh": KNOTS_TO_KMH}


@dataclass(frozen=True)
class IssueSpec:
    """An issue time and what it reads. origin_offset: the origin relative to the day's edge; lags_h: the demand lags,
    in hours; persistence: build the station 24 hours earlier (v1's input); wind: read the NBM wind (the covering
    clause, `wind_s`, `daymean_s`); lag_day: read the station's lag day's mean (`lagday_observed` and the rest)."""

    name: str
    origin_offset: pd.Timedelta
    lags_h: tuple[int, ...]
    persistence: bool
    wind: bool
    lag_day: bool

    @property
    def lag_columns(self) -> tuple[str, ...]:
        return tuple(f"lag{h}" for h in self.lags_h)

    @property
    def station_scenarios(self) -> tuple[str, ...]:
        return ("observed", "persistence") if self.persistence else ("observed",)


MIDNIGHT = IssueSpec("midnight", pd.Timedelta(0), (24, 168), persistence=True, wind=False, lag_day=False)
NOON = IssueSpec("noon", pd.Timedelta(hours=-12), (48, 168), persistence=False, wind=True, lag_day=True)


def lst_day(ts_hour_ending) -> pd.DatetimeIndex:
    """The local-standard-time day (naive midnight) each hour-ending UTC label belongs to."""
    return pd.DatetimeIndex((pd.DatetimeIndex(ts_hour_ending) - H + LST_OFFSET).tz_convert(None)).normalize()


def day_edge(day) -> pd.Timestamp:
    """D 05:00 UTC: the label of D-1's last hour, which v2 issues at."""
    return (pd.Timestamp(day) - LST_OFFSET).tz_localize("UTC")


def day_labels(day) -> pd.DatetimeIndex:
    """D's 24 hour-ending UTC labels, D 06:00 to D+1 05:00 UTC, whatever the issue time."""
    e = day_edge(day)
    return pd.date_range(e + H, e + 24 * H, freq="h")


def _offset(issue: IssueSpec) -> pd.Timedelta:
    """The origin's offset in whole seconds, so adding it keeps the edge's time unit: a nanosecond Timedelta would turn
    v2's microsecond clocks into nanosecond ones, and every grid built on a fit clock with them."""
    return issue.origin_offset.as_unit("s")


def day_origin(day, *, issue: IssueSpec = MIDNIGHT) -> pd.Timestamp:
    """When day D's forecast is issued."""
    return day_edge(day) + _offset(issue)


def origin_of(ts_hour_ending, *, issue: IssueSpec = MIDNIGHT) -> pd.DatetimeIndex:
    """The origin of the forecast covering each hour-ending label."""
    return pd.DatetimeIndex(lst_day(ts_hour_ending) - LST_OFFSET).tz_localize("UTC") + _offset(issue)


def score_clock(day, last: pd.Timestamp) -> pd.Timestamp:
    """When day D is scored: one hour after its last hour (D+1 06:00 UTC), whatever the issue time; at `last` when the
    data end before then (the replay's `judged_at_data_end`)."""
    return min(day_edge(pd.Timestamp(day) + pd.Timedelta(days=1)) + H, pd.Timestamp(last))
