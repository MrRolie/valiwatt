"""Day-ahead features: every input except the labelled target-hour temperature is known at the origin."""

import numpy as np
import pandas as pd

from conftest import synth_demand, synth_obs
from valiwatt.forecast.features import build, known_at_origin, origin_of
from valiwatt.quality.demand import build_demand

FIRST, LAST = pd.Timestamp("2021-02-01T06:00Z"), pd.Timestamp("2021-04-01T05:00Z")


def frame():
    demand = build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame
    temp = synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"]
    return build(demand, temp)


def test_origin_is_the_end_of_the_previous_local_standard_day():
    ts = pd.DatetimeIndex(["2021-02-02T06:00Z", "2021-02-03T05:00Z", "2021-02-03T06:00Z"])
    assert list(origin_of(ts)) == [pd.Timestamp("2021-02-02T05:00Z")] * 2 + [pd.Timestamp("2021-02-03T05:00Z")]


def test_every_target_hour_is_within_24_hours_after_its_origin():
    f = frame()
    lead = (f.index - f["origin"]) / pd.Timedelta(hours=1)
    assert lead.min() == 1 and lead.max() == 24


def test_lag_and_persistence_inputs_are_known_at_the_origin():
    f = frame()
    assert known_at_origin(f).all()


def test_a_lag_reaching_past_the_origin_is_caught():
    f = frame()
    f.loc[f.index[200], "origin"] = f.index[200] - pd.Timedelta(hours=30)
    assert not known_at_origin(f).iloc[200]


def test_lag_values_come_from_the_right_hours():
    f = frame()
    t = f.index[500]
    assert f.loc[t, "lag24"] == f.loc[t - pd.Timedelta(hours=24), "target"]
    assert f.loc[t, "lag168"] == f.loc[t - pd.Timedelta(hours=168), "target"]
    assert f.loc[t, "temp_persistence"] == f.loc[t - pd.Timedelta(hours=24), "temp_observed"]


def test_hour_of_day_follows_the_local_clock_across_the_spring_change():
    f = frame()
    # 2021-03-14: clocks jump from 02:00 EST to 03:00 EDT at 07:00Z. The hour ending 07:00Z starts at 01:00 EST;
    # the next one starts at 03:00 EDT, so local hour 2 never occurs that day.
    assert f.loc[pd.Timestamp("2021-03-14T07:00Z"), "hour"] == 1
    assert f.loc[pd.Timestamp("2021-03-14T08:00Z"), "hour"] == 3
    assert f.loc[pd.Timestamp("2021-03-13T08:00Z"), "hour"] == 2


def test_holidays_and_sundays_share_a_day_type():
    demand = build_demand(synth_demand("2023-06-20T06:00Z", "2023-06-28T05:00Z"),
                          pd.Timestamp("2023-06-20T06:00Z"), pd.Timestamp("2023-06-28T05:00Z")).frame
    temp = synth_obs("2023-06-20T06:00Z", "2023-06-28T05:00Z").set_index("ts_utc")["temp_c"]
    f = build(demand, temp)
    by_day = f.groupby("day")["day_type"].first()
    assert by_day[pd.Timestamp("2023-06-24")] == "sunday_holiday"  # Fête nationale, a Saturday in 2023
    assert by_day[pd.Timestamp("2023-06-25")] == "sunday_holiday"
    assert by_day[pd.Timestamp("2023-06-26")] == "weekday"


def test_masked_or_missing_hours_give_missing_targets_and_lags():
    first, last = FIRST, LAST
    raw = synth_demand(first, last)
    raw.loc[300, "mw"] = np.nan
    demand = build_demand(raw, first, last).frame
    temp = synth_obs(first, last).set_index("ts_utc")["temp_c"]
    f = build(demand, temp)
    t = raw.loc[300, "ts_utc"]
    assert np.isnan(f.loc[t, "target"]) and np.isnan(f.loc[t + pd.Timedelta(hours=24), "lag24"])
