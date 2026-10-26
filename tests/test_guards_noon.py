"""The lookahead guards on a noon table (docs/plan-v3.md, sections 2, 3.3 and 12): the shortest lag's term, no 24-hour
input in a noon model, the lag day's mean read at or before the origin, the wind and the day's mean read only with
their run's availability, and v3's design window. v2's terms keep their names on v2's tables (tests/test_guards.py)."""

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs, synth_runs
from valiwatt.forecast.features import FORECAST_LAGS, build, forecast_temperature
from valiwatt.forecast.guards import (
    DESIGN_LAST_DAY,
    LookaheadError,
    min_lag_hours,
    origin_violations,
    require_design_window,
    require_forecast_known,
    require_known_at_origin,
)
from valiwatt.forecast.issue import NOON, day_edge
from valiwatt.quality.demand import build_demand

H = pd.Timedelta(hours=1)
FIRST, LAST = pd.Timestamp("2021-02-01T06:00Z"), pd.Timestamp("2021-03-01T05:00Z")


def noon_frame(forecasts=True):
    if not forecasts:
        return build(build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame,
                     synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"], issue=NOON)
    days = pd.date_range("2021-02-01", "2021-02-28", freq="D")
    runs = synth_runs(FIRST - 72 * H, LAST, wind=True)
    issued = {s: forecast_temperature(runs, days, lag, issue=NOON) for s, lag in FORECAST_LAGS.items()}
    return build(build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame,
                 synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"], issued, issue=NOON)


def fired(f):
    return [k for k, m in origin_violations(f).items() if m.any()]


def test_a_noon_table_passes_its_guards_under_the_48_hour_term():
    f = noon_frame()
    assert min_lag_hours(f) == 48 and f["lagday_last_utc"].notna().any()
    terms = origin_violations(f)
    assert "input_48h_after_origin" in terms and "input_24h_after_origin" not in terms
    assert fired(f) == []
    require_known_at_origin(f)
    v2 = build(build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame,
               synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"])
    assert min_lag_hours(v2) == 24 and "input_24h_after_origin" in origin_violations(v2)
    assert min_lag_hours(v2.drop(columns=["lag24", "lag168", "temp_persistence"])) == 24  # nothing read back


def test_an_origin_moved_earlier_than_the_48_hour_lag_allows_is_refused():
    # No forecast held and no lag day's mean: the moved origin judges the lags alone.
    f = noon_frame(forecasts=False).drop(columns=["lagday_observed", "lagday_last_utc", "daymean_observed"])
    broken = f.copy()
    t = broken.index[200]
    broken.loc[t, "origin"] = t - 48 * H  # at the lag's own hour: still known
    assert fired(broken) == []
    broken.loc[t, "origin"] = t - 49 * H  # one hour before it
    assert fired(broken) == ["input_48h_after_origin"]  # this term alone
    with pytest.raises(LookaheadError, match="input_48h_after_origin"):
        require_known_at_origin(broken)


@pytest.mark.parametrize("column", ["lag24", "temp_persistence"])
def test_no_24_hour_input_enters_a_noon_table(column):
    """Plan-v3 section 12: no lag of 24 h enters a noon model. Injected, it reads past the noon origin on exactly D's
    hours ending after D 17:00 UTC (12 a day), and only the 24-hour term fires."""
    f = noon_frame()
    broken = f.copy()
    broken[column] = 1.0
    assert min_lag_hours(broken) == 24
    assert fired(broken) == ["input_24h_after_origin"]
    mask = origin_violations(broken)["input_24h_after_origin"]
    after = pd.Series(f.index > pd.DatetimeIndex([day_edge(d) for d in f["day"]]) + 12 * H, index=f.index)
    pd.testing.assert_series_equal(mask, after, check_names=False)
    assert mask.groupby(f["day"]).sum().eq(12).all()
    with pytest.raises(LookaheadError, match="input_24h_after_origin"):
        require_known_at_origin(broken)


def test_a_lag_days_mean_read_after_the_origin_is_refused():
    f = noon_frame()
    t = f.index[300]
    at = f.copy()
    at.loc[t, "lagday_last_utc"] = at.loc[t, "origin"]  # read up to the origin itself: known
    assert fired(at) == []
    late = f.copy()
    late.loc[t, "lagday_last_utc"] = late.loc[t, "origin"] + H
    assert fired(late) == ["lag_day_after_origin"]  # this term alone
    assert origin_violations(late)["lag_day_after_origin"].sum() == 1
    with pytest.raises(LookaheadError, match="lag_day_after_origin"):
        require_known_at_origin(late)
    missing = f.copy()
    missing.loc[t, ["lagday_observed", "lagday_last_utc"]] = [np.nan, pd.NaT]  # a missing mean: nothing read
    assert fired(missing) == []
    # Always present, quiet on a table without the lag day (v2's).
    v2_like = origin_violations(f.drop(columns=["lagday_observed", "lagday_last_utc"]))
    assert not v2_like["lag_day_after_origin"].any() and not v2_like["lag_day_last_missing"].any()


def test_a_finite_lag_days_mean_without_its_last_label_is_refused():
    """A finite lag day's mean that does not say the last label it read cannot be judged against the origin: its NaT
    lagday_last_utc, or the column dropped, fires lag_day_last_missing alone, on exactly those rows."""
    f = noon_frame()
    t = f.index[300]
    assert np.isfinite(f.loc[t, "lagday_observed"])
    undated = f.copy()
    undated.loc[t, "lagday_last_utc"] = pd.NaT
    assert fired(undated) == ["lag_day_last_missing"]
    assert origin_violations(undated)["lag_day_last_missing"].sum() == 1
    with pytest.raises(LookaheadError, match="lag_day_last_missing"):
        require_known_at_origin(undated)
    dropped = f.drop(columns=["lagday_last_utc"])
    assert fired(dropped) == ["lag_day_last_missing"]
    pd.testing.assert_series_equal(origin_violations(dropped)["lag_day_last_missing"],
                                   pd.Series(np.isfinite(f["lagday_observed"].to_numpy()), index=f.index))
    undated.loc[t, "lagday_observed"] = np.nan  # the control: a missing mean reads nothing
    assert fired(undated) == []
    v2 = build(build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame,
               synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"])
    assert not origin_violations(v2)["lag_day_last_missing"].any()  # v2's table: always present, all False


def test_a_table_left_with_the_weekly_lag_alone_is_refused():
    """The guard's look back comes from the table's shortest lag; a table holding lag168 but no shorter lag and no
    persistence temperature is refused rather than judged at 168 hours."""
    f = noon_frame(forecasts=False)
    stripped = f.drop(columns=["lag48"])
    for call in (min_lag_hours, origin_violations, require_known_at_origin):
        with pytest.raises(LookaheadError, match="no shorter lag"):
            call(stripped)
    assert min_lag_hours(stripped.assign(lag48=f["lag48"])) == 48  # the control: lag48 back, judged at 48 hours
    assert min_lag_hours(stripped.assign(temp_persistence=1.0)) == 24


@pytest.mark.parametrize("only", ["wind", "daymean"])
def test_a_finite_wind_or_day_mean_without_its_availability_is_refused(only):
    f = noon_frame()
    t = f.index[100]
    for s in FORECAST_LAGS:
        broken = f.copy()
        broken.loc[t, f"available_{s}"] = pd.NaT
        others = [c for c in (f"temp_{s}", f"hdh_{s}", f"cdh_{s}", f"wind_{s}", f"daymean_{s}")
                  if c != f"{only}_{s}"]
        broken.loc[t, others] = np.nan
        assert np.isfinite(broken.loc[t, f"{only}_{s}"])
        assert fired(broken) == ["forecast_availability_missing"], s
        with pytest.raises(LookaheadError, match="forecast_availability_missing"):
            require_forecast_known(broken, (s,), "a noon fit")
        broken.loc[t, f"{only}_{s}"] = np.nan  # the control: no input held, nothing to date
        assert fired(broken) == [], s


def test_v3s_design_window_ends_on_2024_12_31_and_v2s_stays():
    first, last = pd.Timestamp("2024-12-20T06:00Z"), pd.Timestamp("2025-01-03T05:00Z")
    f = build(build_demand(synth_demand(first, last), first, last, scale_last=last).frame,
              synth_obs(first, last).set_index("ts_utc")["temp_c"], issue=NOON)
    v3_last = pd.Timestamp("2024-12-31")
    require_design_window(f[f["day"] <= v3_last], "clean", v3_last)
    with pytest.raises(LookaheadError, match="after the design window"):
        require_design_window(f, "break", v3_last)
    with pytest.raises(LookaheadError, match="after the design window"):
        require_design_window(f[f["day"] <= v3_last], "v2's window")  # v2's default: 2024 is not design
    assert DESIGN_LAST_DAY == pd.Timestamp("2023-12-31")
