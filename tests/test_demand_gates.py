"""Every demand gate, with the break that must make it fire."""

import numpy as np
import pandas as pd

from conftest import synth_demand
from valiwatt.quality.demand import (
    STATUS_MASKED_DST,
    STATUS_MISSING_VALUE,
    STATUS_REPAIRED,
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
    from valiwatt.quality.demand import BOUNDS_MW

    low, high = BOUNDS_MW
    for v, ok in ((low - 1, False), (low + 1, True), (high - 1, True), (high + 1, False)):
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
