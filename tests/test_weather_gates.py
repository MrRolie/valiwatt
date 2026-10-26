"""Every weather gate, with the break that must make it fire; the station and the forecast share one bounds filter."""

import numpy as np
import pandas as pd

from conftest import synth_demand, synth_obs, synth_runs
from valiwatt.forecast.features import build, forecast_temperature
from valiwatt.quality import weather
from valiwatt.quality.demand import build_demand
from valiwatt.quality.weather import (
    MIN_PANEL_STATIONS,
    NBM_MIN_DAY_COVERAGE,
    forecast_checks,
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
    # Literal edges: a change to the constant must break this test.
    for v, ok in ((-60.1, False), (-59.9, True), (44.9, True), (45.1, False)):
        obs = synth_obs(FIRST, LAST)
        obs.loc[30, "temp_c"] = v
        c, _ = checks(station, obs)
        assert c["weather_bounds"].passed is ok, v


def test_weather_spike_scale_ignores_hours_after_the_design_window(station):
    from valiwatt.config import DESIGN_LAST

    first, last = pd.Timestamp("2023-11-01T00:00Z"), pd.Timestamp("2024-02-01T00:00Z")
    obs = synth_obs(first, last)
    other = obs.copy()
    after = other["ts_utc"] > DESIGN_LAST
    other.loc[after, "temp_c"] = other.loc[after, "temp_c"] + np.random.default_rng(2).normal(0, 6, after.sum())
    ga = weather_checks(station, station_series(obs, first, last))[-1]
    gb = weather_checks(station, station_series(other, first, last))[-1]
    design_spikes = lambda g: [e for e in g.evidence if "spike_utc" in e and pd.Timestamp(e["spike_utc"]) < DESIGN_LAST]
    assert design_spikes(ga) == design_spikes(gb)


# The NBM forecast's gates (docs/plan-v2.md, section 3.4), on a synthetic winter.
NFIRST, NLAST = pd.Timestamp("2021-01-01T06:00Z"), pd.Timestamp("2021-04-01T05:00Z")
NDAYS = pd.date_range("2021-01-01", "2021-03-31", freq="D")
D2 = pd.Timedelta(days=2)


def nbm_checks(runs, days=NDAYS):
    issued = forecast_temperature(runs, days)
    observed = synth_obs(NFIRST, NLAST).set_index("ts_utc")["temp_c"]
    return {c.name: c for c in forecast_checks(runs, issued, observed)}, issued


def unfed(runs, *days):
    """The runs without every run that could feed each of `days`: no run with a run time from day D-2 06:00 to day D
    02:00 UTC is left, and the run of D-2 01:00 UTC ends before D+1 06:00 (synth_runs: 72 hours out)."""
    keep = pd.Series(True, index=runs.index)
    for d in days:
        d = pd.Timestamp(d, tz="UTC")
        keep &= ~runs["runtime"].between(d - D2 + pd.Timedelta(hours=6), d + pd.Timedelta(hours=2))
    return runs[keep].reset_index(drop=True)


def test_clean_forecast_passes_its_gates():
    c, issued = nbm_checks(synth_runs(NFIRST - D2, NLAST))
    assert c["nbm_bounds"].passed and c["nbm_coverage"].passed and c["nbm_coverage"].metrics["share"] == 1.0
    assert c["nbm_runs"].kind == "finding" and c["nbm_runs"].metrics["days_not_fed"] == 0
    assert c["nbm_runs"].metrics["run_hour_by_year"] == {2021: {1: len(NDAYS)}}
    err = c["nbm_runs"].metrics["error_vs_observed"][2021]
    assert set(err) == {"winter", "spring"} and abs(err["winter"]["mean_c"]) < 0.5


def test_an_impossible_forecast_temperature_fails_the_nbm_bounds_gate():
    runs = synth_runs(NFIRST - D2, NLAST)
    # Literal edges, in °F: 113 °F is 45 °C and -76 °F is -60 °C, both inside; one degree further is not.
    for v, ok in ((113.0, True), (114.0, False), (-76.0, True), (-77.0, False), (200.0, False)):
        broken = runs.copy()
        broken.loc[500, "tmp_f"] = v
        c, _ = nbm_checks(broken)
        assert c["nbm_bounds"].passed is ok, v
        assert c["nbm_bounds"].metrics["violations"] == (0 if ok else 1)
    assert c["nbm_bounds"].evidence[0]["ftime"] == runs.loc[500, "ftime"]
    blank = runs.copy()
    blank.loc[500, "tmp_f"] = np.nan  # a blank is no value: not a bounds violation
    c, _ = nbm_checks(blank)
    assert c["nbm_bounds"].passed and c["nbm_bounds"].metrics["blank_points"] == 1


def test_forecast_days_below_coverage_fail_the_nbm_coverage_gate():
    runs = synth_runs(NFIRST - D2, NLAST)
    gap = (runs["runtime"] >= pd.Timestamp("2021-02-10T00:00Z")) & (runs["runtime"] < pd.Timestamp("2021-02-20T00:00Z"))
    c, issued = nbm_checks(runs[~gap].reset_index(drop=True))
    assert not c["nbm_coverage"].passed and c["nbm_coverage"].metrics["share"] < NBM_MIN_DAY_COVERAGE
    unfed = [e["day"] for e in c["nbm_runs"].evidence if "reason" in e]
    assert len(unfed) == len(NDAYS) - c["nbm_coverage"].metrics["fed_days"] > 0
    # The last run before the gap still covers its first days, from further back: no age window (section 3.2).
    assert issued.record.loc[pd.Timestamp("2021-02-11"), "lead_h"] == 34
    assert issued.record.loc[pd.Timestamp("2021-02-15"), "reason"] == "no_covering_run"
    # That run feeds 10 and 11 February: the finding lists the second day as fed by the run of the day before.
    assert c["nbm_runs"].metrics["days_fed_by_the_previous_days_run"] == 1
    reused = [e for e in c["nbm_runs"].evidence if "reused_day" in e]
    assert [(e["reused_day"], e["runtime"]) for e in reused] == [
        (pd.Timestamp("2021-02-11"), pd.Timestamp("2021-02-09T19:00Z"))]
    assert nbm_checks(runs)[0]["nbm_runs"].metrics["days_fed_by_the_previous_days_run"] == 0
    # A day without runs costs no day by itself: the run of the evening before feeds 10 February.
    short = (runs["runtime"] >= pd.Timestamp("2021-02-10T00:00Z")) & (runs["runtime"] < pd.Timestamp("2021-02-11T00:00Z"))
    ok, issued = nbm_checks(runs[~short].reset_index(drop=True))
    assert ok["nbm_coverage"].passed and ok["nbm_coverage"].metrics["share"] == 1.0
    assert issued.record.loc[pd.Timestamp("2021-02-10"), "runtime"] == pd.Timestamp("2021-02-09T19:00Z")


def test_the_nbm_coverage_gate_passes_at_98_percent_of_days_fed_and_fails_below():
    # Literal edges (docs/plan-v2.md, section 3.4: fewer than 98 % of the window's days fed fails): 49 of 50 days is
    # exactly 98 % and passes; 48 of 50, and 48 of 49 (97.96 %), fail. A change to the constant, to the comparison or
    # to the share must break this test.
    runs = synth_runs(NFIRST - D2, NLAST)
    for days, missing, fed, ok in ((NDAYS[:50], ("2021-02-01",), 49, True),
                                   (NDAYS[:50], ("2021-02-01", "2021-02-10"), 48, False),
                                   (NDAYS[:49], ("2021-02-01",), 48, False)):
        c, issued = nbm_checks(unfed(runs, *missing), days)
        assert list(issued.record.index[issued.record["reason"] != ""]) == [pd.Timestamp(d) for d in missing]
        m = c["nbm_coverage"].metrics
        assert (m["days"], m["fed_days"], m["share"]) == (len(days), fed, fed / len(days)), (len(days), missing)
        assert c["nbm_coverage"].passed is ok, (len(days), missing)


def test_the_forecast_error_is_read_on_the_design_days_only():
    # A window across the design edge: the forecast's error against the station is a design-years finding (section
    # 3.4), so no hour after design_last enters it, whatever the window holds.
    from valiwatt.config import DESIGN_LAST

    first, last = pd.Timestamp("2023-12-01T06:00Z"), pd.Timestamp("2024-02-01T05:00Z")
    runs = synth_runs(first - D2, last)
    issued = forecast_temperature(runs, pd.date_range("2023-12-01", "2024-01-31", freq="D"))
    observed = synth_obs(first, last).set_index("ts_utc")["temp_c"]
    assert issued.hourly.notna().all() and issued.hourly.index.max() > DESIGN_LAST

    def error(observed, **kw):
        m = {c.name: c for c in forecast_checks(runs, issued, observed, **kw)}["nbm_runs"].metrics
        return m["error_vs_observed"], m["error_last_utc"]

    err, last_utc = error(observed)  # the default edge: the design window's
    design_hours = int((issued.hourly.index <= DESIGN_LAST).sum())
    assert last_utc == DESIGN_LAST and design_hours == 31 * 24
    assert set(err) == {2023} and set(err[2023]) == {"winter"} and err[2023]["winter"]["hours"] == design_hours
    err, last_utc = error(observed, design_last=pd.Timestamp("2023-12-16T05:00Z"))  # the edge is the parameter's
    assert last_utc == pd.Timestamp("2023-12-16T05:00Z") and set(err) == {2023} and set(err[2023]) == {"winter"}
    assert err[2023]["winter"]["hours"] == 15 * 24
    # An observation outside the bounds is no observation: its hour leaves the error.
    broken = observed.copy()
    broken[pd.Timestamp("2023-12-10T12:00Z")] = 99.0
    err, _ = error(broken)
    assert err[2023]["winter"]["hours"] == design_hours - 1 and abs(err[2023]["winter"]["mean_c"]) < 0.5


def test_station_and_forecast_share_one_bounds_filter(monkeypatch, station):
    demand = build_demand(synth_demand(NFIRST, NLAST), NFIRST, NLAST).frame
    temp = synth_obs(NFIRST, NLAST).set_index("ts_utc")["temp_c"]
    runs = synth_runs(NFIRST - D2, NLAST)
    wide = forecast_temperature(runs, NDAYS)
    assert wide.hourly.notna().all()  # the ruled bounds: every day fed
    narrow = (-5.0, 5.0)
    monkeypatch.setattr(weather, "BOUNDS_C", narrow)  # every path that reads the one filter now reads these bounds

    f = build(demand, temp)  # the station, as design code and the replay read it
    inside = temp.reindex(f.index).between(*narrow).to_numpy()
    assert 0 < inside.sum() < len(inside)
    assert np.array_equal(f["temp_observed"].notna().to_numpy(), inside)

    issued = forecast_temperature(runs, NDAYS)  # the covering rule
    fed = issued.hourly.dropna()
    assert 0 < len(fed) < len(issued.hourly) and fed.between(*narrow).all()
    g = build(demand, temp, {"forecast": issued})
    assert g["temp_forecast"].dropna().between(*narrow).all()
    h = build(demand, temp, {"forecast": wide})  # a forecast issued under other bounds is filtered when read
    expected = wide.hourly.reindex(h.index).between(*narrow).to_numpy()
    assert 0 < expected.sum() and np.array_equal(h["temp_forecast"].notna().to_numpy(), expected)

    celsius = (runs["tmp_f"] - 32) * 5 / 9
    c = {x.name: x for x in forecast_checks(runs, issued, temp)}  # the forecast's gate
    assert c["nbm_bounds"].metrics["violations"] == int((~celsius.between(*narrow)).sum())
    s = {x.name.split("[")[0]: x for x in weather_checks(station, station_series(synth_obs(NFIRST, NLAST), NFIRST,
                                                                                   NLAST))}  # the station's gate
    obs = synth_obs(NFIRST, NLAST)["temp_c"]
    assert s["weather_bounds"].metrics["violations"] == int((~obs.between(*narrow)).sum())


# The forecast wind's gate (docs/plan-v3.md, sections 3.1 and 12, "Wind"), and the noon feed's findings.

def test_the_wind_bounds_gate_is_pinned_at_both_edges():
    from valiwatt.quality.weather import WIND_BOUNDS_KT, usable_wind, wind_bounds_check

    assert WIND_BOUNDS_KT == (0.0, 100.0)
    runs = synth_runs(NFIRST - D2, NLAST, wind=True)
    clean = wind_bounds_check(runs)
    assert clean.name == "nbm_wind_bounds" and clean.kind == "gate" and clean.passed
    assert clean.metrics["violations"] == 0 and clean.metrics["points"] == len(runs)
    # Literal edges, in knots, both included: a change to the constant or to a comparison must break this test.
    for v, ok in ((100.0, True), (101.0, False), (0.0, True), (-1.0, False), (250.0, False)):
        broken = runs.copy()
        broken.loc[500, "wsp_kt"] = v
        c = wind_bounds_check(broken)
        assert c.passed is ok and c.metrics["violations"] == (0 if ok else 1), v
        assert bool(usable_wind(broken["wsp_kt"]).notna().all()) is ok, v
    assert c.evidence == [{"runtime": runs.loc[500, "runtime"], "ftime": runs.loc[500, "ftime"], "wsp_kt": 250.0}]
    blank = runs.copy()
    blank.loc[[500, 501], "wsp_kt"] = np.nan  # a blank is no value: not a bounds violation
    c = wind_bounds_check(blank)
    assert c.passed and c.metrics["blank_points"] == 2


def test_the_wind_gate_refuses_runs_without_wind():
    import pytest

    from valiwatt.quality.weather import wind_bounds_check

    with pytest.raises(ValueError, match="no wind"):
        wind_bounds_check(synth_runs(NFIRST - D2, NLAST))


def test_the_noon_feed_passes_its_gates_and_reports_its_reuse_bound():
    """The noon feed on a synthetic winter: the midday run (13:00 UTC) feeds every day, 4 hours before the origin; the
    run finding reads the reuse bound of the issue time it is given (sources.nbm.reuse_lead_h: 87 at noon, plan-v3
    section 9.2), and refuses a forecast issued at another one."""
    import pytest

    from valiwatt.forecast.issue import NOON
    from valiwatt.sources.nbm import REUSE_LEAD_H, reuse_lead_h

    runs = synth_runs(NFIRST - D2, NLAST, wind=True)
    issued = forecast_temperature(runs, NDAYS, issue=NOON)
    observed = synth_obs(NFIRST, NLAST).set_index("ts_utc")["temp_c"]
    c = {x.name: x for x in forecast_checks(runs, issued, observed, issue=NOON)}
    assert c["nbm_bounds"].passed and c["nbm_coverage"].passed and c["nbm_coverage"].metrics["share"] == 1.0
    m = c["nbm_runs"].metrics
    assert m["run_hour_by_year"] == {2021: {13: len(NDAYS)}} and m["lead_counts"] == {4.0: len(NDAYS)}
    assert m["reuse_lead_h"] == 87 and m["leads_at_or_above_reuse_bound"] == []
    v2 = {x.name: x for x in forecast_checks(runs, forecast_temperature(runs, NDAYS), observed)}
    assert v2["nbm_runs"].metrics["reuse_lead_h"] == REUSE_LEAD_H == 75  # v2's default, unchanged
    # A run reaching 75 hours is a finding against v2's two-day argument and not against v3's.
    long = pd.DataFrame({"runtime": [pd.Timestamp("2021-02-01T01:00Z")], "ftime": [pd.Timestamp("2021-02-04T04:00Z")],
                         "tmp_f": [20.0], "wsp_kt": [10.0]})
    r = pd.concat([runs, long], ignore_index=True).sort_values(["runtime", "ftime"]).reset_index(drop=True)
    noon = {x.name: x for x in forecast_checks(r, issued, observed, issue=NOON)}
    mid = {x.name: x for x in forecast_checks(r, forecast_temperature(r, NDAYS), observed)}
    assert noon["nbm_runs"].metrics["leads_at_or_above_reuse_bound"] == []
    assert mid["nbm_runs"].metrics["leads_at_or_above_reuse_bound"] == [2021]
    assert noon["nbm_runs"].metrics["reuse_lead_h"] == reuse_lead_h(NOON) == 87
    # The bound follows the forecast's own clock: a noon forecast checked at midnight, or v2's at noon, is refused.
    with pytest.raises(ValueError, match="issued at another origin"):
        forecast_checks(runs, issued, observed)
    with pytest.raises(ValueError, match="issued at another origin"):
        forecast_checks(runs, forecast_temperature(runs, NDAYS), observed, issue=NOON)
