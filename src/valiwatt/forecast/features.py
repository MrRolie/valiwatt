"""Day-ahead features. One row per target hour, with only what is known when the forecast is issued.

The forecast for local-standard-time day D is issued at its origin: the end of day D-1, when every hour of D-1 has
been observed. Under hour-ending labels, that is the label of D-1's last hour (D 00:00 EST = D 05:00 UTC). Every
input of a row is either known at that origin or is the target-hour temperature, which stands in for a weather
forecast (the "observed" scenario) and is labelled as such wherever it is used.
"""

from __future__ import annotations

import holidays
import numpy as np
import pandas as pd

from valiwatt.config import LOCAL_TZ, LST_OFFSET
from valiwatt.quality.demand import USABLE

BASE_C = 18.0
SCENARIOS = ("observed", "persistence")
H = pd.Timedelta(hours=1)


def origin_of(ts_hour_ending: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The issue time of the forecast covering each target hour: the hour-ending label of the previous LST day's
    last hour."""
    start_lst = (ts_hour_ending - H + LST_OFFSET).tz_convert(None)
    day = start_lst.normalize()
    return pd.DatetimeIndex(day - LST_OFFSET).tz_localize("UTC")


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
    """For each row: are the timestamps its lag and persistence inputs come from at or before the origin?"""
    return ((f.index - 24 * H) <= f["origin"]) & ((f.index - 168 * H) <= f["origin"])
