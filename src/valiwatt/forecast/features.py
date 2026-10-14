"""Day-ahead features. One row per target hour, with only what is known when the forecast is issued.

The forecast for local-standard-time day D is issued at its origin: the end of day D-1, when every hour of D-1 has
been observed. Under hour-ending labels, that is the label of D-1's last hour (D 00:00 EST = D 05:00 UTC). Every
input of a row is either known at that origin or is the target-hour temperature, which stands in for a weather
forecast (the "observed" scenario) and is labelled as such wherever it is used.

Design code (validation folds, thresholds, frozen models) sees each decision time as the replay sees its clock
(`AsOf`): the demand series is rebuilt from the raw records stamped at or before that time, never read from the
data-quality layer's full-window series, in which a repair can fill an hour with a record that arrives much later.
"""

from __future__ import annotations

import holidays
import numpy as np
import pandas as pd

from valiwatt.config import DEMAND_FIRST, DESIGN_LAST, LOCAL_TZ, LST_OFFSET
from valiwatt.forecast.guards import LookaheadError, require_before_clock
from valiwatt.quality.demand import USABLE, build_demand

BASE_C = 18.0
SCENARIOS = ("observed", "persistence")
H = pd.Timedelta(hours=1)
LOOKBACK = 200 * H  # the demand a day's rows read behind their origin: more than the 168-hour lag needs


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


def quebec_holidays(years) -> set:
    return set(holidays.Canada(subdiv="QC", years=list(years)).keys())


def day_type(dates: pd.DatetimeIndex, hol: set) -> np.ndarray:
    """weekday, saturday, or sunday_holiday (Sundays and Québec statutory holidays)."""
    dow = dates.dayofweek
    is_hol = np.array([d.date() in hol for d in dates])
    return np.where(is_hol | (dow == 6), "sunday_holiday", np.where(dow == 5, "saturday", "weekday"))


def build(demand: pd.DataFrame, temperature: pd.Series) -> pd.DataFrame:
    """Feature table indexed by target hour (UTC, hour-ending).

    demand: canonical frame (mw, status) on the hourly grid; temperature: the chosen source on the same grid."""
    idx = demand.index
    mw = demand["mw"].where(demand["status"].isin(USABLE))
    temp = temperature.reindex(idx)
    start_local = (idx - H).tz_convert(LOCAL_TZ)
    lst_day = (idx - H + LST_OFFSET).tz_convert(None).normalize()
    hol = quebec_holidays(range(lst_day.min().year, lst_day.max().year + 1))
    f = pd.DataFrame(index=idx)
    f["target"] = mw
    f["origin"] = origin_of(idx)
    f["day"] = lst_day
    f["hour"] = start_local.hour  # the local wall-clock hour the demand hour starts in
    f["day_type"] = day_type(pd.DatetimeIndex(start_local.tz_localize(None).normalize()), hol)
    f["lag24"] = mw.reindex(idx - 24 * H).to_numpy()
    f["lag168"] = mw.reindex(idx - 168 * H).to_numpy()
    f["temp_observed"] = temp.to_numpy()
    f["temp_persistence"] = temp.reindex(idx - 24 * H).to_numpy()
    for s in SCENARIOS:
        f[f"hdh_{s}"] = np.maximum(BASE_C - f[f"temp_{s}"], 0.0)
        f[f"cdh_{s}"] = np.maximum(f[f"temp_{s}"] - BASE_C, 0.0)
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
        cut = self._raw.iloc[: int(self._raw["ts_utc"].searchsorted(clock, side="right"))]
        require_before_clock(pd.DatetimeIndex(cut["ts_utc"]), clock, "demand")
        if clock < self.first:
            empty = pd.DatetimeIndex([], tz="UTC", name="ts_utc")
            return pd.DataFrame({"mw": pd.Series(dtype=float, index=empty),
                                 "status": pd.Series(dtype=object, index=empty)})
        # The spike scale does not enter the features; it is taken on every hour in view.
        return build_demand(cut, self.first, clock, scale_last=clock).frame

    def table(self, clock: pd.Timestamp, temperature: pd.Series) -> pd.DataFrame:
        """The feature table a fit at `clock` sees: every row up to the clock, its target and lags from the demand as
        of the clock."""
        return build(self.frame(clock), temperature)

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

    def issued(self, temperature: pd.Series) -> pd.DataFrame:
        """The feature table of test rows from `first` to `last`, each as the replay issues and scores it."""
        f = build(self.frame(self.last), temperature)
        d = self._issued_demand().reindex(f.index)
        for c in ("target", "lag24", "lag168"):
            f[c] = d[c].to_numpy()
        return f
