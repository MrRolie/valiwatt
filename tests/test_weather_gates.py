"""Every weather gate, with the break that must make it fire."""

import numpy as np
import pandas as pd

from conftest import synth_obs
from valiwatt.quality.weather import (
    MIN_PANEL_STATIONS,
    hourly_gap_injection,
    panel_checks,
    panel_temperature,
    station_series,
    weather_checks,
)

FIRST, LAST = pd.Timestamp("2021-01-01T00:00Z"), pd.Timestamp("2021-03-01T00:00Z")


def checks(station, obs):
    frame = station_series(obs, FIRST, LAST)
    return {c.name.split("[")[0]: c for c in weather_checks(station, frame)}, frame


def test_clean_station_passes(station):
    c, _ = checks(station, synth_obs(FIRST, LAST))
    assert all(x.passed for x in c.values() if x.kind == "gate")


def test_duplicate_rows_fail_the_index_gate(station):
    obs = synth_obs(FIRST, LAST)
    obs = pd.concat([obs, obs.iloc[[10]]], ignore_index=True)
    c, _ = checks(station, obs)
    assert not c["weather_index"].passed


def test_missing_rows_fail_the_index_gate(station):
    obs = synth_obs(FIRST, LAST).drop(index=[20, 21])
    c, _ = checks(station, obs)
    assert not c["weather_index"].passed


def test_impossible_temperature_fails_the_bounds_gate(station):
    obs = synth_obs(FIRST, LAST)
    obs.loc[30, "temp_c"] = 99.0
    c, _ = checks(station, obs)
    assert not c["weather_bounds"].passed


def test_sensor_outage_fails_the_coverage_gate(station):
    c, frame = checks(station, synth_obs(FIRST, LAST))
    broken = hourly_gap_injection(frame, share=0.10)
    after = {x.name.split("[")[0]: x for x in weather_checks(station, broken)}
    assert c["weather_coverage"].passed and not after["weather_coverage"].passed
    assert after["weather_gaps"].metrics["longest_run_hours"] >= int(len(frame) * 0.10)


def test_shared_outage_fails_the_panel_gate():
    frames = {f"s{i}": station_series(synth_obs(FIRST, LAST, seed=i), FIRST, LAST) for i in range(6)}
    ok = {c.name: c for c in panel_checks(frames, panel_temperature(frames, MIN_PANEL_STATIONS))}
    assert ok["weather_panel_coverage"].passed
    block = slice(100, 100 + int(len(frames["s0"]) * 0.05))
    for k in ("s0", "s1", "s2"):
        frames[k].iloc[block, frames[k].columns.get_loc("temp_c")] = np.nan
    broken = {c.name: c for c in panel_checks(frames, panel_temperature(frames, MIN_PANEL_STATIONS))}
    assert not broken["weather_panel_coverage"].passed
    assert broken["weather_shared_gaps"].metrics["longest_run_hours"] >= block.stop - block.start


def test_temperature_bounds_are_pinned_at_both_edges(station):
    from valiwatt.quality.weather import BOUNDS_C

    low, high = BOUNDS_C
    for v, ok in ((low - 0.1, False), (low + 0.1, True), (high - 0.1, True), (high + 0.1, False)):
        obs = synth_obs(FIRST, LAST)
        obs.loc[30, "temp_c"] = v
        c, _ = checks(station, obs)
        assert c["weather_bounds"].passed is ok, v
