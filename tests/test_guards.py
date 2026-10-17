"""The lookahead guards refuse their breaks and pass clean data."""

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs, synth_runs
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import FORECAST_LAGS, build, forecast_temperature
from valiwatt.forecast.guards import (
    DESIGN_LAST_DAY,
    LookaheadError,
    origin_violations,
    require_design_window,
    require_embargo,
    require_known_at_origin,
)
from valiwatt.quality.demand import build_demand


def frame(first, last):
    first, last = pd.Timestamp(first), pd.Timestamp(last)
    return build(build_demand(synth_demand(first, last), first, last).frame,
                 synth_obs(first, last).set_index("ts_utc")["temp_c"])


def test_design_window_ends_on_2023_12_31():
    assert DESIGN_LAST_DAY == pd.Timestamp("2023-12-31")


def test_a_2024_row_is_refused_by_design_code():
    f = frame("2023-12-20T06:00Z", "2024-01-03T05:00Z")
    require_design_window(f[f["day"] <= DESIGN_LAST_DAY], "clean")
    with pytest.raises(LookaheadError):
        require_design_window(f, "break")


def fired(f):
    return [k for k, m in origin_violations(f).items() if m.any()]


def test_an_input_after_the_origin_is_refused():
    f = frame("2021-02-01T06:00Z", "2021-03-01T05:00Z")
    require_known_at_origin(f)
    assert fired(f) == []
    broken = f.copy()
    broken.loc[broken.index[100], "origin"] = broken.index[100]  # the target hour itself as the origin
    assert fired(broken) == ["target_not_after_origin"]  # this term alone
    with pytest.raises(LookaheadError, match="target_not_after_origin"):
        require_known_at_origin(broken)


def test_an_origin_moved_earlier_than_the_lag_allows_is_refused():
    f = frame("2021-02-01T06:00Z", "2021-03-01T05:00Z")
    broken = f.copy()
    t = broken.index[100]
    # One hour before the 24-hour lag's hour: the lag would read demand not yet observed at that origin.
    broken.loc[t, "origin"] = t - pd.Timedelta(hours=25)
    assert fired(broken) == ["input_24h_after_origin"]  # this term alone
    with pytest.raises(LookaheadError, match="input_24h_after_origin"):
        require_known_at_origin(broken)


def test_a_training_row_inside_the_embargo_is_refused():
    f = frame("2021-01-01T06:00Z", "2021-04-01T05:00Z")
    fold = wf.folds("2021-03", "2021-03")[0]
    train, _ = wf.split(f, fold)
    require_embargo(train, fold.test_first_day, wf.EMBARGO_DAYS)
    leaky = f[f["day"] < fold.test_first_day]
    with pytest.raises(LookaheadError):
        require_embargo(leaky, fold.test_first_day, wf.EMBARGO_DAYS)


def test_walk_forward_refuses_a_fold_that_breaks_the_embargo(monkeypatch):
    f = frame("2021-01-01T06:00Z", "2021-04-01T05:00Z")
    fold = wf.Fold(pd.Period("2021-03", "M"), pd.Timestamp("2021-02-27"))  # 1 day before the month: no embargo
    with pytest.raises(LookaheadError):
        wf.run(f, [fold])


def forecast_frame():
    """A table with both forecast scenarios fed, as features.build gives it."""
    first, last = pd.Timestamp("2021-02-01T06:00Z"), pd.Timestamp("2021-03-01T05:00Z")
    days = pd.date_range("2021-02-01", "2021-02-28", freq="D")
    runs = synth_runs(first - pd.Timedelta(days=2), last)
    issued = {s: forecast_temperature(runs, days, lag) for s, lag in FORECAST_LAGS.items()}
    return build(build_demand(synth_demand(first, last), first, last).frame,
                 synth_obs(first, last).set_index("ts_utc")["temp_c"], issued)


def test_a_row_whose_forecast_became_available_after_its_origin_is_refused():
    f = forecast_frame()
    assert f["available_forecast"].notna().all()
    require_known_at_origin(f)
    for column in ("available_forecast", "available_forecast_late"):
        broken = f.copy()
        t = broken.index[100]
        broken.loc[t, column] = broken.loc[t, "origin"] + pd.Timedelta(hours=1)
        assert fired(broken) == ["forecast_available_after_origin"]  # this term alone
        with pytest.raises(LookaheadError, match="forecast_available_after_origin"):
            require_known_at_origin(broken)


def test_a_forecast_available_at_the_origin_passes():
    f = forecast_frame()
    at = f.copy()
    at["available_forecast"] = at["origin"]  # available exactly at the origin
    # A day no run fed: no availability and no forecast input, as features.build leaves it.
    at.loc[at.index[:30], ["available_forecast_late", "temp_forecast_late", "hdh_forecast_late",
                           "cdh_forecast_late"]] = [pd.NaT, np.nan, np.nan, np.nan]
    assert fired(at) == []
    require_known_at_origin(at)
    # v1's table holds no forecast scenario: neither an input nor an availability.
    v1_shaped = f.drop(columns=[c for c in f.columns
                                if c.startswith(("available_", "temp_forecast", "hdh_forecast", "cdh_forecast"))])
    assert fired(v1_shaped) == [] and "forecast_available_after_origin" in origin_violations(v1_shaped)
    assert "forecast_availability_missing" in origin_violations(v1_shaped)
    require_known_at_origin(v1_shaped)
    # Built without forecasts, every forecast input is NaN beside a NaT availability: nothing fires.
    first, last = pd.Timestamp("2021-02-01T06:00Z"), pd.Timestamp("2021-03-01T05:00Z")
    require_known_at_origin(frame(first, last))


def test_a_finite_forecast_without_its_availability_is_refused():
    """Availability is established before use (docs/plan-v2.md, sections 2 and 3.2): a row holding a finite input of a
    forecast scenario whose availability is NaT, or whose availability column is absent, is refused; each input of
    the scenario (temperature, heating or cooling degrees) is enough on its own."""
    f = forecast_frame()
    require_known_at_origin(f)
    t = f.index[100]
    for s in FORECAST_LAGS:
        broken = f.copy()
        broken.loc[t, f"available_{s}"] = pd.NaT  # the run that fed the day, its availability lost
        assert np.isfinite(broken.loc[t, f"temp_{s}"])
        assert fired(broken) == ["forecast_availability_missing"]  # this term alone
        assert origin_violations(broken)["forecast_availability_missing"].sum() == 1
        with pytest.raises(LookaheadError, match="forecast_availability_missing"):
            require_known_at_origin(broken)
        for only in (f"hdh_{s}", f"cdh_{s}"):
            one = broken.copy()
            others = [c for c in (f"temp_{s}", f"hdh_{s}", f"cdh_{s}") if c != only]
            one.loc[t, others] = np.nan
            one.loc[t, only] = 3.0
            assert fired(one) == ["forecast_availability_missing"], only
        absent = f.drop(columns=[f"available_{s}"])
        assert fired(absent) == ["forecast_availability_missing"], s
        with pytest.raises(LookaheadError, match="forecast_availability_missing"):
            require_known_at_origin(absent)
