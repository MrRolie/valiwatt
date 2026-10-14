"""Every demand gate, with the break that must make it fire."""

import json

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand
from replay_world import FIRST as WORLD_FIRST
from replay_world import LAST as WORLD_LAST
from replay_world import frozen_and_reference, world
from valiwatt.monitor import replay as rp
from valiwatt.monitor.clock import Clock, RawFeed
from valiwatt.quality.demand import (
    BOUNDS_MW,
    STATUS_MASKED_DST,
    STATUS_MISSING_VALUE,
    STATUS_OUT_OF_BOUNDS,
    STATUS_REPAIRED,
    USABLE,
    build_demand,
    demand_checks,
    same_wall_clock,
)

H = pd.Timedelta(hours=1)


def run(raw, first, last):
    build = build_demand(raw, pd.Timestamp(first), pd.Timestamp(last))
    return build, {c.name: c for c in demand_checks(build)}


def add_record(raw, ts, mw):
    extra = pd.DataFrame({"ts_utc": [pd.Timestamp(ts)], "mw": [mw], "source_line": [raw["source_line"].max() + 1]})
    return pd.concat([raw, extra], ignore_index=True)


def drop_record(raw, ts):
    return raw[raw["ts_utc"] != pd.Timestamp(ts)].reset_index(drop=True)


def value(raw, ts):
    return float(raw.loc[raw["ts_utc"] == pd.Timestamp(ts), "mw"].iloc[0])


FIRST, LAST = "2021-10-25T00:00Z", "2021-11-20T00:00Z"


def test_clean_series_passes_every_gate():
    _, c = run(synth_demand(FIRST, LAST), FIRST, LAST)
    assert all(x.passed for x in c.values() if x.kind == "gate")
    assert c["demand_dst"].metrics["events"] == 0 and c["demand_misplaced"].metrics["events"] == 0


def test_fall_back_collapse_is_masked_not_guessed():
    raw = synth_demand(FIRST, LAST)
    s, t = pd.Timestamp("2021-11-07T05:00Z"), pd.Timestamp("2021-11-07T06:00Z")
    assert same_wall_clock(s, t)
    raw = add_record(drop_record(raw, s), t, value(raw, s))
    build, c = run(raw, FIRST, LAST)
    assert c["demand_duplicates_resolved"].passed
    assert c["demand_dst"].metrics["events"] == 1
    assert (build.frame.loc[[s, t], "status"] == STATUS_MASKED_DST).all()
    assert build.frame.loc[[s, t], "mw"].isna().all()


def test_spring_forward_hours_are_not_the_same_wall_clock():
    assert not same_wall_clock(pd.Timestamp("2021-03-14T06:00Z"), pd.Timestamp("2021-03-14T07:00Z"))


def test_duplicate_without_known_shape_fails_the_gate():
    raw = synth_demand(FIRST, LAST)
    raw = add_record(raw, "2021-11-10T12:00Z", value(raw, "2021-11-10T12:00Z") + 500)
    _, c = run(raw, FIRST, LAST)
    assert not c["demand_duplicates_resolved"].passed
    assert c["demand_duplicates_resolved"].metrics["unresolved"] == 1


def test_misplaced_record_is_moved_with_evidence():
    first, last = "2021-12-28T00:00Z", "2022-01-10T00:00Z"
    raw = synth_demand(first, last, step_at="2022-01-04T00:00Z", step_mw=3_000)
    s, t = pd.Timestamp("2022-01-01T05:00Z"), pd.Timestamp("2022-01-06T05:00Z")
    original = value(raw, s)
    raw = add_record(drop_record(raw, s), t, original)
    build, c = run(raw, first, last)
    assert c["demand_duplicates_resolved"].passed
    assert build.frame.loc[s, "status"] == STATUS_REPAIRED
    assert build.frame.loc[s, "mw"] == original
    ev = c["demand_misplaced"].evidence[0]
    assert ev["cost_ratio"] >= 2.0 and ev["moved_to_utc"] == s


def test_ambiguous_misplaced_record_is_not_moved_and_fails_the_gate():
    first, last = "2021-12-28T00:00Z", "2022-01-10T00:00Z"
    raw = synth_demand(first, last)
    s, t = pd.Timestamp("2022-01-01T05:00Z"), pd.Timestamp("2022-01-06T05:00Z")
    raw = add_record(drop_record(raw, s), t, value(raw, t) + 1.0)
    build, c = run(raw, first, last)
    assert not c["demand_duplicates_resolved"].passed
    assert np.isnan(build.frame.loc[s, "mw"])


def test_record_outside_the_window_fails_the_gate():
    raw = synth_demand(FIRST, LAST)
    raw = add_record(raw, pd.Timestamp(FIRST) - H, 20_000.0)
    _, c = run(raw, FIRST, LAST)
    assert not c["demand_window"].passed


def test_out_of_bounds_value_fails_the_gate():
    for bad in (0.0, 600_000.0):
        raw = synth_demand(FIRST, LAST)
        raw.loc[100, "mw"] = bad
        _, c = run(raw, FIRST, LAST)
        assert not c["demand_bounds"].passed, bad


def test_coverage_below_threshold_fails_the_gate():
    raw = synth_demand(FIRST, LAST)
    n = int(len(raw) * 0.02) + 1
    raw.loc[200 : 200 + n, "mw"] = np.nan
    _, c = run(raw, FIRST, LAST)
    assert not c["demand_coverage"].passed


def test_blank_values_stay_blank():
    raw = synth_demand(FIRST, LAST)
    raw.loc[300:302, "mw"] = np.nan
    build, c = run(raw, FIRST, LAST)
    gap = build.frame.iloc[300:303]
    assert gap["mw"].isna().all() and (gap["status"] == STATUS_MISSING_VALUE).all()
    assert c["demand_missing"].metrics["longest_run_hours"] == 3


def test_injected_spike_is_flagged_and_kept():
    raw = synth_demand(FIRST, LAST)
    raw.loc[400, "mw"] += 5_000
    build, c = run(raw, FIRST, LAST)
    ts = raw.loc[400, "ts_utc"]
    assert build.frame.loc[ts, "spike_flag"]
    assert build.frame.loc[ts, "mw"] == raw.loc[400, "mw"]
    assert c["demand_spikes"].metrics["flagged"] >= 1


def test_clean_series_flags_no_spike():
    _, c = run(synth_demand(FIRST, LAST), FIRST, LAST)
    assert c["demand_spikes"].metrics["flagged"] == 0


def test_bounds_are_pinned_at_both_edges():
    # Literal edges: a change to the constant must break this test.
    for v, ok in ((4_999.0, False), (5_001.0, True), (59_999.0, True), (60_001.0, False)):
        raw = synth_demand(FIRST, LAST)
        raw.loc[100, "mw"] = v
        _, c = run(raw, FIRST, LAST)
        assert c["demand_bounds"].passed is ok, v


def test_record_after_the_window_fails_the_gate():
    raw = add_record(synth_demand(FIRST, LAST), pd.Timestamp(LAST) + H, 20_000.0)
    _, c = run(raw, FIRST, LAST)
    assert not c["demand_window"].passed


def test_coverage_threshold_is_pinned():
    for share, ok in ((0.012, False), (0.005, True)):
        raw = synth_demand(FIRST, LAST)
        n = int(len(raw) * share)
        raw.loc[200 : 200 + n - 1, "mw"] = np.nan
        _, c = run(raw, FIRST, LAST)
        assert c["demand_coverage"].passed is ok, share


def test_misplaced_ratio_below_two_is_not_moved(monkeypatch):
    import valiwatt.quality.demand as dm

    first, last = "2021-12-28T00:00Z", "2022-01-10T00:00Z"
    s, t = pd.Timestamp("2022-01-01T05:00Z"), pd.Timestamp("2022-01-06T05:00Z")

    def case(step):
        raw = synth_demand(first, last, step_at="2022-01-04T00:00Z", step_mw=step)
        return add_record(drop_record(raw, s), t, value(raw, s))

    assert dm.MISPLACED_MIN_RATIO == 2.0
    # Measure the ratio each step size gives, then pick a case between 1 and the rule's 2.
    monkeypatch.setattr(dm, "MISPLACED_MIN_RATIO", 1.0)
    ratios = {step: dm.build_demand(case(step), pd.Timestamp(first), pd.Timestamp(last)).misplaced_events[0]["cost_ratio"]
              for step in (100, 200, 300, 400, 600, 800)}
    step = next(k for k, r in ratios.items() if 1.2 < r < 1.9)
    monkeypatch.setattr(dm, "MISPLACED_MIN_RATIO", 2.0)
    build, c = run(case(step), first, last)
    assert not c["demand_duplicates_resolved"].passed and not build.misplaced_events


def test_spike_scale_ignores_hours_after_the_design_window():
    from valiwatt.config import DESIGN_LAST

    first, last = "2023-12-01T00:00Z", "2024-01-15T00:00Z"
    raw = synth_demand(first, last)
    other = raw.copy()
    after = other["ts_utc"] > DESIGN_LAST
    other.loc[after, "mw"] = other.loc[after, "mw"] * 1.5 + np.random.default_rng(1).normal(0, 800, after.sum())
    a = build_demand(raw, pd.Timestamp(first), pd.Timestamp(last)).frame
    b = build_demand(other, pd.Timestamp(first), pd.Timestamp(last)).frame
    design = a.index < DESIGN_LAST
    assert np.allclose(a.loc[design, "spike_z"], b.loc[design, "spike_z"], equal_nan=True)
    qa = {c.name: c for c in demand_checks(build_demand(raw, pd.Timestamp(first), pd.Timestamp(last)))}
    qb = {c.name: c for c in demand_checks(build_demand(other, pd.Timestamp(first), pd.Timestamp(last)))}
    key = "design_quantile_abs_z"
    assert qa["demand_spikes"].metrics[key] == pytest.approx(qb["demand_spikes"].metrics[key])


def test_a_record_off_the_hourly_grid_fails_the_window_gate_with_its_timestamp():
    raw = synth_demand(FIRST, LAST)
    raw.loc[150, "ts_utc"] = raw.loc[150, "ts_utc"] + pd.Timedelta(minutes=30)
    build, c = run(raw, FIRST, LAST)
    assert not c["demand_window"].passed
    assert raw.loc[150, "ts_utc"] in build.outside_window


def test_an_out_of_bounds_value_inside_a_fall_back_pair_still_fails_the_bounds_gate():
    fall_back = pd.Timestamp("2021-11-07T06:00Z")  # 01:00 EST, the second 01:00 of the night
    raw = drop_record(synth_demand(FIRST, LAST), fall_back - H)
    raw = add_record(raw, fall_back, 0.0)
    build, c = run(raw, FIRST, LAST)
    assert (build.frame.loc[[fall_back - H, fall_back], "status"] == STATUS_MASKED_DST).all()
    assert not c["demand_bounds"].passed


def test_an_out_of_bounds_neighbour_does_not_make_a_spike():
    raw = synth_demand(FIRST, LAST)
    raw.loc[500, "mw"] = 9.9e6
    build, _ = run(raw, FIRST, LAST)
    t = raw.loc[500, "ts_utc"]
    assert build.frame.loc[t, "status"] == "out_of_bounds"
    assert not build.frame.loc[[t - H, t + H], "spike_flag"].any()


def test_an_out_of_bounds_value_is_set_aside_before_a_repair_reads_it():
    """A misplaced record is judged by how it fits its neighbours; an out-of-bounds neighbour must not enter that fit:
    with it set aside the fit cannot be computed, so nothing is moved and no cost is built on the garbage value."""
    first, last = "2021-12-28T00:00Z", "2022-01-10T00:00Z"
    raw = synth_demand(first, last, step_at="2022-01-04T00:00Z", step_mw=3_000)
    s, t = pd.Timestamp("2022-01-01T05:00Z"), pd.Timestamp("2022-01-06T05:00Z")
    raw = add_record(drop_record(raw, s), t, value(raw, s))
    clean_build, _ = run(raw, first, last)
    assert clean_build.misplaced_events[0]["moved_to_utc"] == s  # the clean case repairs
    raw.loc[raw["ts_utc"] == s - H, "mw"] = 9.9e6
    build, c = run(raw, first, last)
    assert not build.misplaced_events and not c["demand_bounds"].passed
    best = build.unresolved_duplicates[0]["best_candidate"]
    assert best is None or best["cost_mw"] < 1e5


def set_mw(raw, hours, mw):
    raw = raw.copy()
    raw.loc[raw["ts_utc"].isin(pd.DatetimeIndex(hours)), "mw"] = mw
    return raw


def test_a_misplaced_record_that_fits_two_empty_hours_is_not_moved_and_fails_the_gate():
    """Two empty hours whose neighbours read 10,000 and 10,020 MW, and a 10,010 MW record misplaced elsewhere: it fits
    both hours equally well, so the data cannot say where it belongs. The swap test alone would pass."""
    first, last = "2021-12-28T00:00Z", "2022-01-10T00:00Z"
    s1, s2, t = (pd.Timestamp(x) for x in ("2022-01-02T10:00Z", "2022-01-04T10:00Z", "2022-01-07T10:00Z"))
    raw = set_mw(synth_demand(first, last), [s1 - H, s1 + H], 10_000.0)
    raw = set_mw(set_mw(raw, [s2 - H, s2 + H], 10_020.0), [t - H, t, t + H], 20_000.0)
    one_empty = add_record(drop_record(raw, s1), t, 10_010.0)

    build, c = run(one_empty, first, last)  # control: with one empty hour the record is moved
    assert c["demand_duplicates_resolved"].passed and build.frame.loc[s1, "status"] == STATUS_REPAIRED
    ev = build.misplaced_events[0]
    assert ev["destinations"] == 1 and ev["runner_up"] is None and ev["destination_ratio"] is None

    far = drop_record(set_mw(one_empty, [s2 - H, s2 + H], 14_000.0), s2)  # control: the other empty hour fits worse
    build, c = run(far, first, last)
    ev = build.misplaced_events[0]
    assert c["demand_duplicates_resolved"].passed and ev["moved_to_utc"] == s1
    assert ev["destinations"] == 2 and ev["runner_up"]["slot"] == s2 and ev["destination_ratio"] >= 2.0
    json.dumps(c["demand_misplaced"].to_dict())

    build, c = run(drop_record(one_empty, s2), first, last)
    assert not c["demand_duplicates_resolved"].passed and not build.misplaced_events
    assert build.frame.loc[[s1, s2, t], "mw"].isna().all()
    assert (build.frame.loc[[s1, s2], "status"] == "missing_record").all()
    assert build.frame.loc[t, "status"] == "unresolved_duplicate"
    ev = build.unresolved_duplicates[0]
    assert ev["reason"] == "ambiguous_destination"
    assert {ev["best_candidate"]["slot"], ev["runner_up"]["slot"]} == {s1, s2}
    best = ev["best_candidate"]
    assert best["swapped_cost_mw"] / best["cost_mw"] >= 2.0  # the swap test alone would move it
    json.dumps(c["demand_duplicates_resolved"].to_dict())



def test_a_destination_whose_runner_up_costs_exactly_the_ratio_is_repaired():
    """The boundary of the destination test: the next-best empty hour costs exactly MISPLACED_MIN_RATIO times the
    chosen one (40 MW against 20 MW, exact in floating point). The rule refuses only below the ratio, so it moves."""
    from valiwatt.quality.demand import MISPLACED_MIN_RATIO

    first, last = "2021-12-28T00:00Z", "2022-01-10T00:00Z"
    s1, s2, t = (pd.Timestamp(x) for x in ("2022-01-02T10:00Z", "2022-01-04T10:00Z", "2022-01-07T10:00Z"))
    raw = set_mw(synth_demand(first, last), [s1 - H, s1 + H], 10_000.0)
    raw = set_mw(set_mw(raw, [s2 - H, s2 + H], 9_990.0), [t - H, t, t + H], 20_000.0)
    raw = drop_record(drop_record(add_record(raw, t, 10_010.0), s1), s2)
    build, c = run(raw, first, last)
    ev = c["demand_misplaced"].evidence[0] if build.misplaced_events else build.unresolved_duplicates[0]
    assert ev.get("reason") is None, ev.get("reason")
    assert MISPLACED_MIN_RATIO == 2.0 and (ev["cost_mw"], ev["runner_up"]["cost_mw"]) == (20.0, 40.0)
    assert ev["destination_ratio"] == MISPLACED_MIN_RATIO and ev["runner_up"]["slot"] == s2
    assert c["demand_duplicates_resolved"].passed and build.frame.loc[s1, "status"] == STATUS_REPAIRED
    assert build.frame.loc[s1, "mw"] == 10_010.0 and build.frame.loc[s2, "status"] == "missing_record"

def low_load_world():
    """The replay world with an empty low-load hour s (neighbours 5,500 MW) and, at an earlier high-load hour t, a
    second record of 4,900 MW: below the bounds, yet it fits s far better than t."""
    raw, obs = world()
    day = pd.Timestamp("2022-01-12")
    s, t = rp.origin(day) - 10 * H, rp.origin(day) - 100 * H
    raw = add_record(drop_record(set_mw(raw, [s - H, s + H], 5_500.0), s), t, 4_900.0)
    return raw, obs, day, s, t


def replay_issue(raw, obs, day):
    """The replay's own path at day's origin: the canonical frame from the raw cut, and the day's forecasts."""
    frozen, _ = frozen_and_reference()
    clock = Clock(rp.origin(day))
    state = rp._State(RawFeed(raw, clock, "demand"), RawFeed(obs, clock, "temperature"), clock,
                      scale_last=pd.Timestamp("2022-01-01T05:00Z"))
    return state.canonical()[0], rp.issue(state, frozen, day)


def assert_no_usable_out_of_bounds(frame, s):
    usable = frame["status"].isin(USABLE)
    assert not usable[s]
    assert not (usable & ((frame["mw"] < BOUNDS_MW[0]) | (frame["mw"] > BOUNDS_MW[1]))).any()


def assert_replay_never_reads(raw, obs, day, s):
    frame, issued = replay_issue(raw, obs, day)
    assert_no_usable_out_of_bounds(frame, s)
    row = issued.loc[s + 24 * H]
    assert np.isnan(row["lag24"]) and np.isnan(row["forecast"]) and "lag24" in row["not_predicted_because"]


def test_an_out_of_bounds_duplicate_is_never_moved_into_a_usable_hour():
    raw, obs, day, s, t = low_load_world()
    build, c = run(raw, WORLD_FIRST, WORLD_LAST)
    assert build.frame.loc[s, "status"] == "missing_record" and np.isnan(build.frame.loc[s, "mw"])
    assert build.frame.loc[t, "status"] == "unresolved_duplicate" and not build.misplaced_events
    assert build.unresolved_duplicates[0]["reason"] == "value_out_of_bounds"
    assert not c["demand_duplicates_resolved"].passed and not c["demand_bounds"].passed
    assert_no_usable_out_of_bounds(build.frame, s)
    assert_replay_never_reads(raw, obs, day, s)


def test_an_out_of_bounds_value_that_reaches_a_usable_hour_is_marked_out_of_bounds(monkeypatch):
    """The last line: with the repair's bounds rule switched off, the 4,900 MW record is moved into s (the fault), and
    the final pass still marks s out of bounds, in the data-quality layer and on the replay's issue path."""
    import valiwatt.quality.demand as dm

    monkeypatch.setattr(dm, "_eligible", lambda v: not np.isnan(v))
    raw, obs, day, s, t = low_load_world()
    build, _ = run(raw, WORLD_FIRST, WORLD_LAST)
    assert build.misplaced_events and build.misplaced_events[0]["moved_to_utc"] == s  # the fault is injected
    assert build.frame.loc[s, "status"] == STATUS_OUT_OF_BOUNDS and build.frame.loc[s, "mw"] == 4_900.0
    assert_no_usable_out_of_bounds(build.frame, s)
    assert_replay_never_reads(raw, obs, day, s)
