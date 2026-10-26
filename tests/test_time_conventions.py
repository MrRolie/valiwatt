"""Time alignment and the no-lookahead rail for design-window analysis; the data windows and freezes of v2."""

from pathlib import Path

import pandas as pd
import pytest

from conftest import synth_demand, synth_obs
from valiwatt.config import DEMAND_FIRST, DESIGN_LAST, fetch_months
from valiwatt.quality.demand import build_demand
from valiwatt.quality.representativeness import (
    LookaheadError,
    daily_demand,
    daily_temperature,
    design_window,
    lst_day,
)


def test_hour_belongs_to_the_local_standard_day_it_starts_in():
    ts = pd.DatetimeIndex(["2019-01-01T06:00Z", "2019-01-02T05:00Z", "2019-01-02T06:00Z"])
    days = lst_day(ts)
    # 00:00-01:00 EST Jan 1; 23:00-24:00 EST Jan 1; 00:00-01:00 EST Jan 2.
    assert [d.strftime("%Y-%m-%d") for d in days] == ["2019-01-01", "2019-01-01", "2019-01-02"]


def test_demand_window_matches_six_calendar_years_of_hour_ending_labels():
    assert DEMAND_FIRST == pd.Timestamp("2019-01-01T06:00Z")
    assert len(pd.date_range(DEMAND_FIRST, pd.Timestamp("2025-01-01T05:00Z"), freq="h")) == 52_608
    assert fetch_months()[0] == (2019, 1) and fetch_months()[-1] == (2025, 1)


def test_the_window_ends_with_the_last_hour_of_its_last_year():
    from valiwatt.config import DEMAND_LAST, THROUGH_DEFAULT, THROUGH_YEARS, data_last

    assert data_last(2024) == DEMAND_LAST == pd.Timestamp("2025-01-01T05:00Z")
    assert data_last(2025) == pd.Timestamp("2026-01-01T05:00Z")
    assert data_last(2023) == DESIGN_LAST  # the design window ends with 2023, the same way
    assert lst_day(pd.DatetimeIndex([data_last(2025)]))[0].strftime("%Y-%m-%d") == "2025-12-31"
    assert THROUGH_DEFAULT == 2024 and THROUGH_YEARS == (2024, 2025)
    assert fetch_months(2024) == fetch_months()
    assert fetch_months(2025)[0] == (2019, 1) and fetch_months(2025)[-1] == (2026, 1)
    assert len(fetch_months(2025)) == len(fetch_months()) + 12


def test_the_freezes_and_the_labels_are_configured():
    from valiwatt import config

    assert config.FREEZE_TAG == "rules-frozen-v2" and config.FIRST_RUN_TAG == "v2-first-run"
    assert set(config.FREEZES) == {"rules-frozen", "rules-frozen-v2", "v3-rules-frozen"}
    assert config.FREEZES["rules-frozen"]["years"] == (2024,) and config.FREEZES["rules-frozen"]["end"] == "v1"
    # v2's range is closed at the fork on the v3 branch (docs/plan-v3.md, section 10.1); v3's is open.
    assert config.FREEZES["rules-frozen-v2"]["years"] == (2024, 2025)
    assert config.FREEZES["rules-frozen-v2"]["end"] == "v3-fork" == config.V3_FORK_TAG
    assert config.FREEZES["v3-rules-frozen"]["years"] == (2024, 2025) and config.FREEZES["v3-rules-frozen"]["end"] is None
    assert config.FREEZES["v3-rules-frozen"]["heading"] == "## Departures from the v3 frozen rules"
    # v3's tags and records (sections 10.2, 10.3): no v3 tag matches v2's `rules-frozen*` pattern.
    assert (config.V3_FREEZE_TAG, config.V3_PRE_FREEZE_TAG, config.V3_FORK_TAG, config.V3_PLAN_TAG,
            config.V3_FIRST_RUN_TAG) == ("v3-rules-frozen", "v3-pre-freeze", "v3-fork", "plan-v3", "v3-first-run")
    assert not any(t.startswith("rules-frozen") for t in (config.V3_FREEZE_TAG, config.V3_PRE_FREEZE_TAG,
                                                          config.V3_FORK_TAG, config.V3_PLAN_TAG,
                                                          config.V3_FIRST_RUN_TAG))
    assert (config.V3_LEDGER, config.V3_FREEZE_RECORD) == ("docs/replay-ledger-v3.json", "docs/freeze-v3.json")
    assert config.EVALUATION == {2023: "rehearsal", 2024: "seen_by_developers", 2025: "held_out"}
    assert config.HELD_OUT_YEAR == 2025 and config.IN_SAMPLE_LABEL == "in_sample_for_choice"
    agents = (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text(encoding="utf-8")
    for freeze in config.FREEZES.values():
        assert freeze["heading"] + "\n" in agents  # each freeze's departures section exists under that heading


def test_design_window_ends_with_2023_local_time():
    assert lst_day(pd.DatetimeIndex([DESIGN_LAST]))[0].strftime("%Y-%m-%d") == "2023-12-31"


def test_design_analysis_refuses_hours_after_the_design_window():
    first, last = "2023-12-20T00:00Z", "2024-01-10T00:00Z"
    frame = build_demand(synth_demand(first, last), pd.Timestamp(first), pd.Timestamp(last)).frame
    with pytest.raises(LookaheadError):
        daily_demand(frame)
    temp = synth_obs(first, last).set_index("ts_utc")["temp_c"]
    with pytest.raises(LookaheadError):
        daily_temperature(temp)
    assert daily_demand(design_window(frame)).index.max() <= pd.Timestamp("2023-12-31")


def test_a_day_needs_all_24_demand_hours():
    first, last = "2021-02-01T06:00Z", "2021-02-04T05:00Z"
    frame = build_demand(synth_demand(first, last), pd.Timestamp(first), pd.Timestamp(last)).frame
    assert len(daily_demand(frame)) == 3
    frame.loc[pd.Timestamp("2021-02-02T12:00Z"), "status"] = "missing_value"
    assert len(daily_demand(frame)) == 2


def test_r2_difference_separates_only_when_the_interval_excludes_zero():
    import numpy as np

    from valiwatt.quality.representativeness import r2_difference

    days = pd.date_range("2021-01-01", "2022-12-31", freq="D")
    rng = np.random.default_rng(0)
    temp = 5 - 15 * np.cos(2 * np.pi * np.arange(len(days)) / 365.25) + rng.normal(0, 3, len(days))
    demand = 25_000 + 500 * np.maximum(18 - temp, 0) + rng.normal(0, 300, len(days))
    daily = pd.DataFrame({"demand_mw": demand, "good": temp, "same": temp,
                          "noisy": temp + rng.normal(0, 6, len(days))}, index=days)
    tie = r2_difference(daily, "good", "same", n_boot=200)
    assert tie["difference"] == pytest.approx(0.0) and not tie["separated"]
    worse = r2_difference(daily, "good", "noisy", n_boot=200)
    assert worse["separated"] and worse["difference"] < 0 and worse["ci95_high"] < 0
