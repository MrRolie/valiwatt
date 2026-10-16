"""The lookahead guards refuse their breaks and pass clean data."""

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
    at.loc[at.index[:30], "available_forecast_late"] = pd.NaT  # a day no run fed
    assert fired(at) == []
    require_known_at_origin(at)
    v1_shaped = f.drop(columns=[c for c in f.columns if c.startswith("available_")])
    assert fired(v1_shaped) == [] and "forecast_available_after_origin" in origin_violations(v1_shaped)
