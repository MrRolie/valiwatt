"""v3's feature table, issued at noon the day before (docs/plan-v3.md, sections 2, 3.2, 3.3 and 12): the three clocks,
the noon feed with its wind clause, the day's mean, the lag day's mean, and a noon table's invariants. v2's midnight
path is pinned by tests/test_features.py and tests/test_v2_goldens.py."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs, synth_runs
from valiwatt.forecast.features import (
    FORECAST_LAGS,
    AsOf,
    IssuedForecast,
    build,
    forecast_temperature,
    known_at_origin,
    observed_day_mean,
)
from valiwatt.forecast.guards import origin_violations, require_known_at_origin
from valiwatt.forecast.issue import (
    KNOTS_TO_KMH,
    LAG_DAY_MIN_HOURS,
    MIDNIGHT,
    NOON,
    day_edge,
    day_labels,
    day_origin,
    origin_of,
    score_clock,
)
from valiwatt.quality.demand import USABLE, build_demand
from valiwatt.sources.nbm import AVAILABILITY_LAG

H = pd.Timedelta(hours=1)
FIRST, LAST = pd.Timestamp("2021-02-01T06:00Z"), pd.Timestamp("2021-03-01T05:00Z")
DAY = pd.Timestamp("2021-02-10")
E = day_edge(DAY)  # 2021-02-10 05:00 UTC
O = day_origin(DAY, issue=NOON)  # 2021-02-09 17:00 UTC
BRACKET = [E + h * H for h in range(1, 26, 3)]  # D 06:00 .. D+1 06:00 UTC, counted from the edge
NO_WIND = replace(NOON, wind=False)


def run(runtime, base_f=20.0, slope_f=0.0, wind_kt=10.0, wind_slope=0.0, lead_h=71, drop=(), set_=None, set_wind=None):
    """One run's points every 3 hours of the UTC day after its run time, out to lead_h hours: base_f (°F) and wind_kt
    (knots), plus their slopes per hour since DAY's edge. drop: valid times left out; set_ / set_wind: {valid time:
    value}."""
    runtime = pd.Timestamp(runtime)
    ftimes = pd.date_range((runtime + 3 * H).floor("3h"), runtime + lead_h * H, freq="3h")
    if drop:
        ftimes = ftimes[~ftimes.isin(pd.DatetimeIndex(list(drop)))]
    hours = np.asarray((ftimes - E) / H, dtype=float)
    out = pd.DataFrame({"runtime": runtime, "ftime": ftimes, "tmp_f": base_f + slope_f * hours,
                        "wsp_kt": wind_kt + wind_slope * hours})
    for column, values in (("tmp_f", set_), ("wsp_kt", set_wind)):
        for t, v in (values or {}).items():
            out.loc[out["ftime"] == pd.Timestamp(t), column] = v
    return out


def runs_of(*parts):
    return pd.concat(parts, ignore_index=True).sort_values(["runtime", "ftime"]).reset_index(drop=True)


def day_values(series, day=DAY):
    return series[series.index.isin(day_labels(day))]


FRESH, OLDER = O - 4 * H, O - 10 * H  # D-1 13:00 UTC, the midday run (available 15:00); D-1 07:00 UTC


# ---------------------------------------------------------------------------------------------------------- clocks

def test_the_three_clocks_at_noon():
    assert O == pd.Timestamp("2021-02-09T17:00Z") and E == pd.Timestamp("2021-02-10T05:00Z")
    assert day_origin(DAY) == day_origin(DAY, issue=MIDNIGHT) == E  # v2's origin is the edge
    labels = day_labels(DAY)
    assert len(labels) == 24 and labels[0] == pd.Timestamp("2021-02-10T06:00Z")
    assert labels[-1] == pd.Timestamp("2021-02-11T05:00Z")
    assert (origin_of(labels, issue=NOON) == O).all() and (origin_of(labels) == E).all()
    lead = (labels - O) / H
    assert lead.min() == 13 and lead.max() == 36  # plan-v3 section 2: 13 to 36 hours after the origin
    far = pd.Timestamp("2030-01-01T00:00Z")
    assert score_clock(DAY, far) == pd.Timestamp("2021-02-11T06:00Z")  # whatever the issue time
    assert score_clock(DAY, pd.Timestamp("2021-02-11T03:00Z")) == pd.Timestamp("2021-02-11T03:00Z")  # data end


def noon_table(forecasts=True, temp=None):
    demand = build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame
    temp = synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"] if temp is None else temp
    issued = None
    if forecasts:
        runs = synth_runs(FIRST - 72 * H, LAST, wind=True)
        days = pd.date_range("2021-02-02", "2021-02-28", freq="D")
        issued = {s: forecast_temperature(runs, days, lag, issue=NOON) for s, lag in FORECAST_LAGS.items()}
    return build(demand, temp, issued, issue=NOON), issued, demand, temp


def test_a_noon_day_carries_exactly_its_24_labels_and_is_scored_the_next_morning():
    f, _, _, _ = noon_table()
    for day, rows in f.groupby("day"):
        if day == f["day"].max():
            continue  # the table ends with this day's last label: scored at the end of the data, as in v2
        assert list(rows.index) == list(day_labels(day)), day
        assert (rows["origin"] == day_origin(day, issue=NOON)).all(), day
        assert (rows["scored_at"] == day_edge(day + pd.Timedelta(days=1)) + H).all(), day
    assert f["target"].notna().all()  # no target blank because of the clock


def test_a_noon_table_holds_only_what_is_known_at_noon():
    """The noon table's invariants (plan-v3, section 2): origin 12 hours before the edge on every row; lags of 48 and
    168 hours, no 24-hour lag and no persistence temperature; every input at or before the origin; v3's columns."""
    f, issued, _, _ = noon_table()
    edges = pd.DatetimeIndex([day_edge(d) for d in f["day"]])
    assert (pd.DatetimeIndex(f["origin"]) == edges - 12 * H).all()
    assert "lag24" not in f.columns and "temp_persistence" not in f.columns
    assert "hdh_persistence" not in f.columns
    assert list(f.columns[:11]) == ["target", "origin", "day", "scored_at", "hour", "day_type", "lag48", "lag168",
                                    "temp_observed", "hdh_observed", "cdh_observed"]
    for s in FORECAST_LAGS:
        assert {f"temp_{s}", f"available_{s}", f"hdh_{s}", f"cdh_{s}", f"wind_{s}", f"daymean_{s}"} <= set(f.columns)
    assert {"lagday_observed", "lagday_last_utc", "daymean_observed"} <= set(f.columns)
    assert ((f.index - 48 * H) <= f["origin"]).all() and known_at_origin(f).all()
    assert (f["lagday_last_utc"].dropna() <= f.loc[f["lagday_last_utc"].notna(), "origin"]).all()
    fired = [k for k, m in origin_violations(f).items() if m.any()]
    assert fired == [] and "input_48h_after_origin" in origin_violations(f)
    require_known_at_origin(f)
    mw = build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame
    mw = mw["mw"].where(mw["status"].isin(USABLE))
    t = f.index[500]
    assert f.loc[t, "lag48"] == mw[t - 48 * H] and f.loc[t, "lag168"] == mw[t - 168 * H]
    fed = f[f["day"].between(pd.Timestamp("2021-02-02"), pd.Timestamp("2021-02-28"))]
    for s in FORECAST_LAGS:
        assert fed[f"wind_{s}"].notna().all() and fed[f"daymean_{s}"].notna().all()
        assert np.allclose(fed[f"wind_{s}"], issued[s].wind.reindex(fed.index))
        assert np.allclose(fed[f"daymean_{s}"], fed["day"].map(issued[s].record["day_mean_c"]))
        assert (fed[f"available_{s}"] <= fed["origin"]).all()
    # The six-hour margin takes away the midday run: the morning's 07:00 UTC run feeds the day (plan-v3, 3.4).
    assert (pd.DatetimeIndex(fed["available_forecast"]).hour == 15).all()
    assert (pd.DatetimeIndex(fed["available_forecast_late"]).hour == 13).all()
    unfed = f[f["day"] == pd.Timestamp("2021-02-01")]  # a day not requested
    assert unfed["wind_forecast"].isna().all() and unfed["daymean_forecast"].isna().all()


def test_a_midnight_table_holds_none_of_the_noon_columns():
    demand = build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame
    temp = synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"]
    runs = synth_runs(FIRST - 72 * H, LAST, wind=True)  # the loader's runs carry the wind; v2 never reads it
    days = pd.date_range("2021-02-02", "2021-02-28", freq="D")
    f = build(demand, temp, {"forecast": forecast_temperature(runs, days)})
    plain = build(demand, temp, {"forecast": forecast_temperature(runs.drop(columns="wsp_kt"), days)})
    pd.testing.assert_frame_equal(f, plain)
    assert not [c for c in f.columns if c.startswith(("wind_", "daymean_", "lagday_", "lag48"))]
    assert "lag24" in f.columns and "temp_persistence" in f.columns


# ------------------------------------------------------------------------------------------------- the noon origin

def test_the_freshest_run_available_by_the_noon_origin_feeds_the_day():
    issued = forecast_temperature(runs_of(run(OLDER, 50.0), run(FRESH, 20.0)), [DAY], issue=NOON)
    r = issued.record.loc[DAY]
    assert r["origin"] == O and r["runtime"] == FRESH and r["available_utc"] == FRESH + AVAILABILITY_LAG
    assert r["rank"] == 0 and r["lead_h"] == 4 and r["reason"] == ""
    assert np.allclose(day_values(issued.hourly), (20.0 - 32) * 5 / 9)
    at = O - AVAILABILITY_LAG  # available exactly at the origin: compliant, and fresher still
    assert forecast_temperature(runs_of(run(FRESH), run(at, 30.0)), [DAY], issue=NOON).record.loc[DAY, "runtime"] == at


def test_a_run_available_at_17_01_is_never_used_for_the_day_and_the_next_freshest_is():
    """Plan-v3 section 12, the noon origin: a run available at D-1 17:01 UTC is never used for D, even with absurd
    values, and the next-freshest is; nor is v2's own run of D 01:00 UTC."""
    clean = run(FRESH, 30.0)
    late = run(O - AVAILABILITY_LAG + pd.Timedelta(minutes=1), 104.0, wind_kt=99.0)  # 40 °C in February, in bounds
    v2s = run(E - 4 * H, 104.0, wind_kt=99.0)
    a = forecast_temperature(clean, [DAY], issue=NOON)
    b = forecast_temperature(runs_of(clean, late, v2s), [DAY], issue=NOON)
    assert b.record.loc[DAY, "available_utc"] == pd.Timestamp("2021-02-09T15:00Z")
    pd.testing.assert_series_equal(a.hourly, b.hourly)
    pd.testing.assert_series_equal(a.wind, b.wind)
    pd.testing.assert_frame_equal(a.record, b.record)
    # The control: at midnight the 17:01 run is in view, fresher, and feeds the day.
    assert forecast_temperature(runs_of(clean, late), [DAY]).record.loc[DAY, "runtime"] == late["runtime"].iloc[0]


def test_the_bracket_is_counted_from_the_day_edge_whatever_the_origin():
    """D's nine points are D 06:00 to D+1 06:00 UTC at either clock: a run missing D+1 06:00 does not cover D at noon,
    and one missing a point between the noon origin and the edge (D-1 18:00, D 03:00) still does."""
    for missing in (BRACKET[0], BRACKET[-1]):
        r = forecast_temperature(runs_of(run(OLDER, 30.0), run(FRESH, drop=[missing])), [DAY], issue=NOON).record
        assert r.loc[DAY, "runtime"] == OLDER, missing
    between = [O + H, E - 2 * H]
    assert all(t in set(run(FRESH)["ftime"]) for t in between)
    r = forecast_temperature(runs_of(run(OLDER, 30.0), run(FRESH, drop=between)), [DAY], issue=NOON).record
    assert r.loc[DAY, "runtime"] == FRESH


# ------------------------------------------------------------------------------------------------------------ wind

def test_a_run_with_one_blank_wind_point_gives_way_to_the_next_though_its_temperatures_are_whole():
    for t in (BRACKET[0], BRACKET[4], BRACKET[-1]):
        runs = runs_of(run(OLDER, 30.0, wind_kt=5.0), run(FRESH, 20.0, set_wind={t: np.nan}))
        issued = forecast_temperature(runs, [DAY], issue=NOON)
        r = issued.record.loc[DAY]
        assert r["runtime"] == OLDER and r["rank"] == 1 and r["reason"] == "", t
        assert np.allclose(day_values(issued.hourly), (30.0 - 32) * 5 / 9)
        assert np.allclose(day_values(issued.wind), 5.0 * KNOTS_TO_KMH)  # the two runs are never mixed
        # The control: without the wind clause the fresh run covers, its temperatures being whole.
        assert forecast_temperature(runs, [DAY], issue=NO_WIND).record.loc[DAY, "runtime"] == FRESH, t
    alone = forecast_temperature(run(FRESH, set_wind={BRACKET[2]: np.nan}), [DAY], issue=NOON)
    r = alone.record.loc[DAY]
    assert r["reason"] == "no_covering_run" and np.isnan(r["day_mean_c"]) and day_values(alone.wind).isna().all()


def test_a_wind_point_outside_the_bounds_does_not_cover():
    """Wind bounds 0 to 100 knots, both included (plan-v3, section 3.1), through the one wind filter."""
    for v, covers in ((100.0, True), (101.0, False), (0.0, True), (-1.0, False), (np.inf, False)):
        runs = runs_of(run(OLDER, 30.0), run(FRESH, 20.0, set_wind={BRACKET[3]: v}))
        assert (forecast_temperature(runs, [DAY], issue=NOON).record.loc[DAY, "runtime"] == FRESH) is covers, v
    # Only the nine bracket points decide: an impossible wind later in the run does not stop it covering the day.
    later = runs_of(run(OLDER, 30.0), run(FRESH, 20.0, set_wind={E + 49 * H: 150.0}))
    assert forecast_temperature(later, [DAY], issue=NOON).record.loc[DAY, "runtime"] == FRESH


def test_wind_is_interpolated_in_knots_then_converted_and_the_days_mean_is_the_mean_of_its_24_celsius_values():
    issued = forecast_temperature(run(FRESH, 10.0, slope_f=0.5, wind_kt=4.0, wind_slope=0.25), [DAY], issue=NOON)
    temp, wind = day_values(issued.hourly), day_values(issued.wind)
    hours = np.asarray((wind.index - E) / H, dtype=float)
    assert wind.name == "wind_forecast" and list(wind.index) == list(day_labels(DAY))
    assert np.allclose(wind.to_numpy(), (4.0 + 0.25 * hours) * KNOTS_TO_KMH, rtol=0, atol=1e-12)
    assert np.allclose(temp.to_numpy(), (10.0 + 0.5 * hours - 32) * 5 / 9, rtol=0, atol=1e-12)
    assert issued.record.loc[DAY, "day_mean_c"] == temp.mean()
    bumpy = forecast_temperature(run(FRESH, set_wind={BRACKET[1]: 22.0}), [DAY], issue=NOON)  # 09:00 at 22, else 10
    assert day_values(bumpy.wind)[E + 2 * H] == pytest.approx((10 + 12 / 3) * KNOTS_TO_KMH)
    assert list(issued.record.columns) == ["origin", "runtime", "available_utc", "rank", "lead_h", "reason",
                                           "day_mean_c"]
    v2 = forecast_temperature(run(E - 4 * H), [DAY])  # v2's record and feed, unchanged
    assert v2.wind is None and "day_mean_c" not in v2.record.columns


def test_the_noon_feed_refuses_runs_without_wind():
    with pytest.raises(ValueError, match="no wsp_kt"):
        forecast_temperature(run(FRESH).drop(columns="wsp_kt"), [DAY], issue=NOON)
    forecast_temperature(run(FRESH).drop(columns="wsp_kt"), [DAY], issue=NO_WIND)  # the spec without the clause


def test_a_noon_table_refuses_a_forecast_issued_without_wind():
    demand = build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame
    temp = synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"]
    runs = synth_runs(FIRST - 72 * H, LAST, wind=True)
    days = pd.date_range("2021-02-02", "2021-02-28", freq="D")
    with pytest.raises(ValueError, match="issued without them"):
        build(demand, temp, {"forecast": forecast_temperature(runs, days, issue=NO_WIND)}, issue=NOON)
    build(demand, temp, {"forecast": forecast_temperature(runs, days, issue=NOON)}, issue=NOON)


def test_a_finite_wind_without_its_availability_is_refused_by_build():
    demand = build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame
    temp = synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"]
    runs = synth_runs(FIRST - 72 * H, LAST, wind=True)
    issued = forecast_temperature(runs, pd.date_range("2021-02-02", "2021-02-28", freq="D"), issue=NOON)
    day = pd.Timestamp("2021-02-20")
    hourly = issued.hourly.copy()
    hourly[day_labels(day)] = np.nan  # the temperature gone: only the wind and the day's mean hold an input
    lost = issued.record.copy()
    lost.loc[day, "available_utc"] = pd.NaT
    from valiwatt.forecast.guards import LookaheadError

    with pytest.raises(LookaheadError, match=r"features\.build: 24 rows .*forecast_availability_missing"):
        build(demand, temp, {"forecast": IssuedForecast(hourly, lost, issued.wind)}, issue=NOON)


# ---------------------------------------------------------------------------------------------- the lag day's mean

def lag_labels(day=DAY):
    return day_labels(day - pd.Timedelta(days=2))  # D-2 06:00 .. D-1 05:00 UTC


def lag_day(temp, day=DAY):
    f, _, _, _ = noon_table(forecasts=False, temp=temp)
    rows = f[f["day"] == day]
    assert rows["lagday_observed"].nunique(dropna=False) == 1 and rows["daymean_observed"].nunique(dropna=False) == 1
    return rows["lagday_observed"].iloc[0], rows["lagday_last_utc"].iloc[0], rows["daymean_observed"].iloc[0]


def test_the_lag_days_mean_reads_d_minus_2s_24_labels_and_nothing_after():
    """Plan-v3 section 12: the observation at D-2 05:00 UTC is not in D-2's mean, the one at D-1 05:00 UTC is; an
    ECCC hour after D-1 05:00 UTC never reaches it."""
    temp = synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"]
    labels = lag_labels()
    assert labels[0] == pd.Timestamp("2021-02-08T06:00Z") and labels[-1] == pd.Timestamp("2021-02-09T05:00Z")
    mean, last, own = lag_day(temp)
    assert mean == pytest.approx(temp[labels].mean(), rel=1e-12) and last == labels[-1] and last < O
    assert own == pytest.approx(temp[day_labels(DAY)].mean(), rel=1e-12)

    before = temp.copy()
    before[labels[0] - H] = 40.0  # D-2 05:00 UTC: D-3's last hour
    assert lag_day(before)[0] == mean
    last_hour = temp.copy()
    last_hour[labels[-1]] = 40.0  # D-1 05:00 UTC: D-2's last hour
    assert lag_day(last_hour)[0] == pytest.approx(mean + (40.0 - temp[labels[-1]]) / 24)
    after = temp.copy()
    after[after.index > labels[-1]] = 40.0  # every ECCC hour after D-1 05:00 UTC, the origin's own included
    assert lag_day(after)[:2] == (mean, last)


def test_19_usable_hours_leave_the_lag_days_mean_missing_and_20_compute_it():
    """Plan-v3 sections 3.3, 5 and 12: at least LAG_DAY_MIN_HOURS usable observations, else missing (nothing imputed).
    An observation outside the bounds is no observation."""
    assert LAG_DAY_MIN_HOURS == 20
    temp = synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"]
    labels = lag_labels()
    twenty = temp.copy()
    twenty[labels[[1, 5, 9, 23]]] = np.nan  # 20 left, D-1 05:00 among the blanks
    mean, last, _ = lag_day(twenty)
    assert mean == pytest.approx(twenty[labels].mean()) and last == labels[22]
    blank = twenty.copy()
    blank[labels[12]] = np.nan
    hot = twenty.copy()
    hot[labels[12]] = 99.0  # outside the bounds: not usable
    for nineteen in (blank, hot):
        mean, last, _ = lag_day(nineteen)
        assert np.isnan(mean) and pd.isna(last)
    m = observed_day_mean(hot)
    d2 = DAY - pd.Timedelta(days=2)
    assert m.loc[d2, "n_usable"] == 19 and np.isnan(m.loc[d2, "mean_c"]) and pd.isna(m.loc[d2, "last_utc"])
    assert observed_day_mean(hot, min_hours=19).loc[d2, "mean_c"] == pytest.approx(blank[labels].mean())
    assert str(m["last_utc"].dtype) == "datetime64[ns, UTC]"


def test_a_duplicated_label_is_refused_rather_than_counted_twice():
    """19 usable hours and one of them stamped twice would count 20 and compute the mean: the duplicate is refused,
    whatever its value, never counted."""
    temp = synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"]
    labels = lag_labels()
    nineteen = temp.copy()
    nineteen[labels[[1, 5, 9, 12, 23]]] = np.nan
    assert np.isnan(observed_day_mean(nineteen).loc[DAY - pd.Timedelta(days=2), "mean_c"])  # the control
    for value in (nineteen[labels[2]], np.nan):
        twice = pd.concat([nineteen, pd.Series([value], index=labels[[2]])])
        with pytest.raises(ValueError, match="duplicated labels"):
            observed_day_mean(twice)


def test_the_noon_lags_are_read_as_of_the_noon_origin():
    """AsOf at NOON (plan-v3, section 2): each day's lags from the demand as of D-1 17:00 UTC; its target as of D+1
    06:00 UTC. A record stamped after the noon origin never reaches D's lags, though build_demand's misplaced-record
    repair could move it into a missing hour that a lag reads: only the AsOf clock keeps it out (the input_48h guard
    compares labels and cannot see it)."""
    first, last = pd.Timestamp("2021-02-01T06:00Z"), pd.Timestamp("2021-02-22T05:00Z")
    raw = synth_demand(first, last)
    temp = synth_obs(first, last).set_index("ts_utc")["temp_c"]
    day = pd.Timestamp("2021-02-15")
    o = day_origin(day, issue=NOON)
    noon = AsOf(raw, first, last, issue=NOON).issued(temp)
    assert "lag24" not in noon.columns and {"lag48", "lag168"} <= set(noon.columns)
    rows = noon[noon["day"] == day]
    seen = AsOf(raw, first, last).frame(o)
    mw = seen["mw"].where(seen["status"].isin(USABLE))
    assert np.allclose(rows["lag48"], mw.reindex(rows.index - 48 * H)) and rows["lag48"].notna().all()
    assert np.allclose(rows["lag168"], mw.reindex(rows.index - 168 * H))
    scored = AsOf(raw, first, last).frame(score_clock(day, last))
    assert np.allclose(rows["target"], scored["mw"].reindex(rows.index)) and rows["target"].notna().all()

    # The misplaced record: the hour D 10:00 UTC's lag48 reads (D-2 10:00 UTC) loses its record, and its value comes
    # back as a duplicate of a later hour, which build_demand's repair moves into the gap once both are in view.
    target = day_labels(day)[4]
    hole = target - 48 * H
    hidden = raw.loc[raw["ts_utc"] == hole, "mw"].iloc[0]

    def misplaced(stamp):
        dup = raw[raw["ts_utc"] == hole].assign(ts_utc=stamp)
        moved = pd.concat([raw[raw["ts_utc"] != hole], dup], ignore_index=True)
        return AsOf(moved, first, last, issue=NOON).issued(temp)

    late = misplaced(o + H)  # stamped one hour after the noon origin
    assert np.isnan(late.loc[target, "lag48"])
    others = rows.index.drop(target)
    pd.testing.assert_series_equal(late.loc[others, "lag48"], rows.loc[others, "lag48"])
    # The control: stamped before the origin, the record is in view at it, and the repair fills lag48 with the hidden
    # value. (At the origin itself the stamped hour has no next hour in view, so the repair cannot place it.)
    assert misplaced(o - H).loc[target, "lag48"] == hidden
