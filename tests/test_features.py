"""Day-ahead features: every input except the labelled target-hour temperature is known at the origin; and the
forecast feed (docs/plan-v2.md, section 3.2): which run feeds a day, and its hourly values."""

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs, synth_runs
from valiwatt.forecast.features import (
    FORECAST_LAGS,
    SCENARIOS,
    build,
    day_origin,
    forecast_temperature,
    known_at_origin,
    origin_of,
)
from valiwatt.quality.demand import USABLE, build_demand
from valiwatt.sources.nbm import AVAILABILITY_LAG, MAX_RUNS_BACK

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


DAY = pd.Timestamp("2021-02-10")
O = day_origin(DAY)  # 2021-02-10 05:00 UTC
H = pd.Timedelta(hours=1)
BRACKET = [O + h * H for h in range(1, 26, 3)]  # D 06:00 .. D+1 06:00 UTC


def run(runtime, base_f=20.0, slope_f=0.0, lead_h=71, drop=(), set_=None):
    """One run's points every 3 hours of the UTC day after its run time, out to lead_h hours: base_f, plus slope_f per
    hour since DAY's origin. drop: valid times left out; set_: {valid time: °F}."""
    runtime = pd.Timestamp(runtime)
    ftimes = pd.date_range((runtime + 3 * H).floor("3h"), runtime + lead_h * H, freq="3h")
    if drop:
        ftimes = ftimes[~ftimes.isin(pd.DatetimeIndex(list(drop)))]
    vals = base_f + slope_f * np.asarray((ftimes - O) / H, dtype=float)
    out = pd.DataFrame({"runtime": runtime, "ftime": ftimes, "tmp_f": vals})
    for t, v in (set_ or {}).items():
        out.loc[out["ftime"] == pd.Timestamp(t), "tmp_f"] = v
    return out


def runs_of(*parts):
    return pd.concat(parts, ignore_index=True).sort_values(["runtime", "ftime"]).reset_index(drop=True)


def day_values(issued, day=DAY):
    o = day_origin(day)
    return issued.hourly[(issued.hourly.index > o) & (issued.hourly.index <= o + 24 * H)]


def test_the_bracket_and_the_lags_are_the_ruled_ones():
    assert BRACKET[0] == pd.Timestamp("2021-02-10T06:00Z") and BRACKET[-1] == pd.Timestamp("2021-02-11T06:00Z")
    assert AVAILABILITY_LAG == 2 * H and MAX_RUNS_BACK == 12
    assert SCENARIOS == ("observed", "persistence", "forecast", "forecast_late")
    assert FORECAST_LAGS == {"forecast": 2 * H, "forecast_late": 6 * H}


def test_the_freshest_run_available_by_the_origin_feeds_the_day():
    fresh, older = O - 4 * H, O - 10 * H  # the day's own 01 UTC run, available at 03 UTC; the evening's 19 UTC run
    issued = forecast_temperature(runs_of(run(older, 50.0), run(fresh, 20.0)), [DAY])
    r = issued.record.loc[DAY]
    assert r["runtime"] == fresh and r["available_utc"] == fresh + AVAILABILITY_LAG and r["rank"] == 0
    assert r["lead_h"] == 4 and r["reason"] == "" and r["origin"] == O
    assert np.allclose(day_values(issued), (20.0 - 32) * 5 / 9)
    at = O - AVAILABILITY_LAG  # available exactly at the origin: compliant, and fresher still
    assert forecast_temperature(runs_of(run(fresh), run(at, 30.0)), [DAY]).record.loc[DAY, "runtime"] == at


def test_a_run_available_after_the_origin_is_never_used():
    late = O - 1 * H  # stamped one hour before the origin: available one hour after it
    clean = run(O - 10 * H, 30.0)
    absurd = runs_of(clean, run(late, 104.0))  # 40 °C in February: absurd, yet inside the bounds
    a, b = forecast_temperature(clean, [DAY]), forecast_temperature(absurd, [DAY])
    assert b.record.loc[DAY, "runtime"] == O - 10 * H
    pd.testing.assert_series_equal(a.hourly, b.hourly)
    pd.testing.assert_frame_equal(a.record, b.record)


def test_a_run_missing_one_bracket_point_gives_way_to_the_next():
    fresh, older = O - 4 * H, O - 10 * H
    for missing in (BRACKET[0], BRACKET[4], BRACKET[-1]):
        issued = forecast_temperature(runs_of(run(older, 30.0), run(fresh, 20.0, drop=[missing])), [DAY])
        r = issued.record.loc[DAY]
        assert r["runtime"] == older and r["rank"] == 1 and r["lead_h"] == 10, missing
        assert np.allclose(day_values(issued), (30.0 - 32) * 5 / 9)
    short = run(fresh, 20.0, lead_h=24)  # its last point is D+1 03:00: D's last hour is not bracketed
    assert forecast_temperature(runs_of(run(older, 30.0), short), [DAY]).record.loc[DAY, "runtime"] == older


def test_a_point_outside_the_bounds_does_not_cover():
    fresh, older = O - 4 * H, O - 10 * H
    hot = runs_of(run(older, 30.0), run(fresh, 20.0, set_={BRACKET[3]: 114.0}))  # 45.6 °C
    assert forecast_temperature(hot, [DAY]).record.loc[DAY, "runtime"] == older
    cold = runs_of(run(older, 30.0), run(fresh, 20.0, set_={BRACKET[5]: -77.0}))  # -60.6 °C
    assert forecast_temperature(cold, [DAY]).record.loc[DAY, "runtime"] == older
    edge = runs_of(run(older, 30.0), run(fresh, 20.0, set_={BRACKET[3]: 113.0, BRACKET[5]: -76.0}))  # 45 and -60 °C
    assert forecast_temperature(edge, [DAY]).record.loc[DAY, "runtime"] == fresh
    # Only the nine bracket points decide: an impossible point later in the run does not stop it covering the day.
    later = runs_of(run(older, 30.0), run(fresh, 20.0, set_={O + 49 * H: 150.0}))
    assert forecast_temperature(later, [DAY]).record.loc[DAY, "runtime"] == fresh


def test_a_blank_point_does_not_cover():
    fresh, older = O - 4 * H, O - 10 * H
    for t in (BRACKET[0], BRACKET[6]):
        issued = forecast_temperature(runs_of(run(older, 30.0), run(fresh, 20.0, set_={t: np.nan})), [DAY])
        assert issued.record.loc[DAY, "runtime"] == older and day_values(issued).notna().all(), t


def test_no_covering_run_among_twelve_leaves_the_day_without_a_forecast_and_says_why():
    stamps = [O - 4 * H - k * 6 * H for k in range(MAX_RUNS_BACK + 1)]  # 13 compliant runs, 6 hours apart
    lacking = [run(t, 20.0, lead_h=150, drop=[BRACKET[2]]) for t in stamps[:MAX_RUNS_BACK]]
    thirteenth = run(stamps[-1], 30.0, lead_h=150)  # it would cover, but it is the thirteenth freshest
    issued = forecast_temperature(runs_of(*lacking, thirteenth), [DAY])
    r = issued.record.loc[DAY]
    assert r["reason"] == "no_covering_run" and pd.isna(r["runtime"]) and pd.isna(r["available_utc"])
    assert np.isnan(r["rank"]) and np.isnan(r["lead_h"])
    assert len(day_values(issued)) == 24 and day_values(issued).isna().all()
    # The twelfth freshest still feeds the day when it covers.
    twelfth = runs_of(*lacking[:-1], run(stamps[MAX_RUNS_BACK - 1], 30.0, lead_h=150), thirteenth)
    fed = forecast_temperature(twelfth, [DAY]).record.loc[DAY]
    assert fed["rank"] == MAX_RUNS_BACK - 1 and fed["runtime"] == stamps[MAX_RUNS_BACK - 1]
    none = forecast_temperature(run(O + 2 * H), [DAY]).record.loc[DAY]  # no run available before the origin
    assert none["reason"] == "no_run_available"


def test_hourly_values_are_linear_in_time_inside_one_run_and_in_celsius():
    issued = forecast_temperature(run(O - 4 * H, 10.0, slope_f=0.5), [DAY])
    v = day_values(issued)
    expected = (10.0 + 0.5 * np.asarray((v.index - O) / H, dtype=float) - 32) * 5 / 9
    assert np.allclose(v.to_numpy(), expected, rtol=0, atol=1e-12)
    assert list(v.index) == list(pd.date_range(O + H, O + 24 * H, freq="h")) and v.name == "temp_forecast"
    bumpy = forecast_temperature(run(O - 4 * H, 20.0, set_={BRACKET[1]: 41.0}), [DAY])  # 09:00 at 41 °F, else 20
    w = day_values(bumpy)
    assert w[O + 2 * H] == pytest.approx(((20 + (41 - 20) / 3) - 32) * 5 / 9)  # 07:00, a third of the way up
    assert w[O + 4 * H] == pytest.approx((41 - 32) * 5 / 9)
    assert w[O + 6 * H] == pytest.approx(((41 - 2 * (41 - 20) / 3) - 32) * 5 / 9)


def test_two_runs_are_never_mixed():
    fresh, older = O - 4 * H, O - 10 * H
    issued = forecast_temperature(runs_of(run(older, 50.0), run(fresh, 20.0, drop=[BRACKET[4]])), [DAY])
    alone = forecast_temperature(run(older, 50.0), [DAY])
    pd.testing.assert_series_equal(day_values(issued), day_values(alone))
    assert np.allclose(day_values(issued), (50.0 - 32) * 5 / 9)


def test_the_eve_of_the_design_edge_keeps_its_forecast():
    eve = pd.Timestamp("2023-12-31")
    o = day_origin(eve)  # 2023-12-31 05:00 UTC
    own = run(o - 4 * H, 20.0, set_={pd.Timestamp("2024-01-01T03:00Z"): 23.0, pd.Timestamp("2024-01-01T06:00Z"): 32.0})
    new_year = run(pd.Timestamp("2024-01-01T01:00Z"), 80.0)  # available 03:00 on 1 January: after the eve's origin
    issued = forecast_temperature(runs_of(own, new_year), [eve])
    r = issued.record.loc[eve]
    assert r["runtime"] == pd.Timestamp("2023-12-31T01:00Z") and r["reason"] == ""
    v = day_values(issued, eve)
    assert v.notna().sum() == 24 and v.index[-1] == pd.Timestamp("2024-01-01T05:00Z")
    # 05:00 lies two thirds of the way from the eve's own run's 03:00 point to its 06:00 point.
    assert v.iloc[-1] == pytest.approx(((23 + 2 * (32 - 23) / 3) - 32) * 5 / 9)


def test_build_without_forecasts_keeps_every_v1_column():
    demand = build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame
    temp = synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"]
    f = build(demand, temp)
    v1 = ["target", "origin", "day", "scored_at", "hour", "day_type", "lag24", "lag168", "temp_observed",
          "temp_persistence", "hdh_observed", "cdh_observed", "hdh_persistence", "cdh_persistence"]
    assert list(f.columns[: len(v1)]) == v1
    idx = demand.index
    t = temp.reindex(idx).to_numpy()
    t24 = temp.reindex(idx).reindex(idx - 24 * H).to_numpy()
    mw = demand["mw"].where(demand["status"].isin(USABLE)).to_numpy()

    def same(col, expected):
        assert np.array_equal(f[col].to_numpy(dtype=float), np.asarray(expected, dtype=float), equal_nan=True), col

    same("target", mw)
    same("temp_observed", t)
    same("temp_persistence", t24)
    same("hdh_observed", np.maximum(18.0 - t, 0.0))
    same("cdh_observed", np.maximum(t - 18.0, 0.0))
    same("hdh_persistence", np.maximum(18.0 - t24, 0.0))
    same("cdh_persistence", np.maximum(t24 - 18.0, 0.0))
    same("lag24", pd.Series(mw, index=idx).reindex(idx - 24 * H).to_numpy())
    same("lag168", pd.Series(mw, index=idx).reindex(idx - 168 * H).to_numpy())
    for s in ("forecast", "forecast_late"):
        assert f[f"temp_{s}"].isna().all() and f[f"hdh_{s}"].isna().all() and f[f"cdh_{s}"].isna().all()
        assert f[f"available_{s}"].isna().all() and str(f[f"available_{s}"].dtype) == "datetime64[ns, UTC]"


def test_build_carries_the_forecast_and_its_availability():
    demand = build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame
    temp = synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"]
    days = pd.date_range("2021-02-02", "2021-03-31", freq="D")
    runs = synth_runs(FIRST - 48 * H, LAST)
    issued = {s: forecast_temperature(runs, days, lag) for s, lag in FORECAST_LAGS.items()}
    f = build(demand, temp, issued)
    rows = f[f["day"].isin(days)]
    assert rows["temp_forecast"].notna().all() and rows["temp_forecast_late"].notna().all()
    assert np.allclose(rows["temp_forecast"], issued["forecast"].hourly.reindex(rows.index))
    assert (rows["available_forecast"] == rows["day"].map(issued["forecast"].record["available_utc"])).all()
    assert (rows["available_forecast"] <= rows["origin"]).all()
    assert (rows["available_forecast_late"] <= rows["origin"]).all()
    # The six-hour margin takes away the day's own early run: the previous evening's feeds the day.
    assert (rows["available_forecast_late"] < rows["available_forecast"]).all()
    assert np.allclose(rows["hdh_forecast"], np.maximum(18.0 - rows["temp_forecast"], 0.0))
    first = f[f["day"] == pd.Timestamp("2021-02-01")]  # a day not requested: no forecast
    assert first["temp_forecast"].isna().all() and first["available_forecast"].isna().all()
    with pytest.raises(ValueError, match="no forecast scenario"):
        build(demand, temp, {"observed": issued["forecast"]})
