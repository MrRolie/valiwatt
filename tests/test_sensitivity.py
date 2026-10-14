"""The pseudo-replay's measurements of the rules (monitor/sensitivity.py): each finds what it exists to find, and
nothing where there is nothing."""

import pandas as pd
import pytest

from replay_world import LAST, frozen_and_reference, thresholds, world
from test_replay import END, START, week
from valiwatt.monitor import replay as rp
from valiwatt.monitor import sensitivity as S
from valiwatt.monitor.rules import RETRAIN


@pytest.fixture(scope="module")
def clean():
    raw, obs = world()
    frozen, ref = frozen_and_reference()
    return rp.replay(raw, obs, frozen, thresholds(), ref, START, END, LAST)


def args(clean):
    raw, obs = world()
    frozen, ref = frozen_and_reference()
    return raw, obs, frozen, thresholds(), ref, clean, LAST


def test_a_level_shift_in_demand_is_measured_and_no_shift_finds_nothing(clean):
    out = S.level_shifts(*args(clean), factors=(1.0, 1.12), starts=["2022-01-10"], workers=1)
    weeks = {round(x["shift"], 2): x["weeks"] for x in out}
    assert weeks[0.0] is None
    assert weeks[0.12] is not None and weeks[0.12] <= 3


def test_one_day_episodes_bring_no_alarm_and_every_start_is_counted(clean):
    out = S.episodes(*args(clean), days=(1,), factors=(1.35, 0.65), starts=["2022-01-15", "2022-01-16"], workers=1)
    assert [(r["days"], r["factor"], r["starts"]) for r in out["rows"]] == [(1, 0.65, 2), (1, 1.35, 2)]
    assert all(r["retrain"] == 0 and r["recalibrate"] == 0 for r in out["rows"])


def test_a_stretch_across_a_monday_is_measured_as_a_false_alarm_and_the_echo_exclusion_keeps_others(clean):
    """The measure fires: five low days from a Thursday spoil two calendar weeks and yield a clean retrain; five high
    days are an episode and its echo, which the exclusion keeps from retrain."""
    out = S.episodes(*args(clean), days=(5,), factors=(0.6, 1.4), starts=["2022-01-05", "2022-01-06"], workers=1)
    rows = {r["factor"]: r for r in out["rows"]}
    assert rows[0.6]["retrain"] == rows[0.6]["retrain_clean"] == 1
    job = next(j for j in out["jobs_with_alarms"] if j["alarms"])
    assert (job["start"], job["factor"]) == (pd.Timestamp("2022-01-06"), 0.6)
    assert [(a["week_start"], a["recommendation"]) for a in job["alarms"]] == [(pd.Timestamp("2022-01-10"), RETRAIN)]
    assert rows[1.4]["retrain"] == 0 and rows[1.4]["kept_from_retrain_by_echo_exclusion"] == 2


def test_the_episode_grid_starts_on_the_first_judged_week_and_leaves_room_for_the_echo(clean):
    out = S.episodes(*args(clean), days=(7,), factors=(1.0,), workers=1)
    row = out["rows"][0]
    first, last = S._judged_span(clean)
    assert out["first_start"] == first == pd.Timestamp("2022-01-03")
    assert row["starts"] == (last - first).days - (7 - 1 + S.ECHO_MARGIN_DAYS) + 1
    assert row["retrain"] == 0 and not out["jobs_with_alarms"]  # factor 1 changes nothing


def test_an_alarm_resting_on_a_week_already_out_of_band_is_assisted_and_old_alarms_are_not_counted():
    a, b, c = week("2024-01-01"), week("2024-01-08"), week("2024-01-15")
    base = {w["week_start"]: {"recommendation": None, "flagged": False} for w in (a, b, c)}
    ev = lambda *ws: [{"week_start": w["week_start"]} for w in ws]  # noqa: E731
    decisions = [{"week_start": a["week_start"], "recommendation": None, "fired": []},
                 {"week_start": b["week_start"], "recommendation": RETRAIN,
                  "fired": [{"rule": 2, "why": ["mape_high"], "evidence": ev(b, a)}]},
                 {"week_start": c["week_start"], "recommendation": None, "fired": []}]
    assert S.alarms([a, b, c], decisions, base)["alarms"][0]["assisted"] is False
    base[a["week_start"]]["flagged"] = True
    assert S.alarms([a, b, c], decisions, base)["alarms"][0]["assisted"] is True
    base[b["week_start"]]["recommendation"] = RETRAIN  # the year as it was already said retrain there
    assert S.alarms([a, b, c], decisions, base)["alarms"] == []


def test_the_measures_run_in_fresh_worker_processes_and_find_what_one_process_finds(clean):
    """With several workers, each starts from a fresh process (never a fork holding the parent's MLflow connection),
    receives the inputs and finds the same alarms as one process."""
    assert S.POOL_CONTEXT == "forkserver"
    kw = dict(days=(5,), factors=(0.6, 1.4), starts=["2022-01-05", "2022-01-06"])
    one = S.episodes(*args(clean), **kw, workers=1)
    two = S.episodes(*args(clean), **kw, workers=2)
    assert two["rows"] == one["rows"] and two["jobs_with_alarms"] == one["jobs_with_alarms"]
    assert any(r["retrain"] for r in two["rows"])


def test_the_echo_exclusion_only_ever_removes_or_delays_retrain(clean):
    out = S.echo_exclusion_cost(clean, thresholds(), sds=(0.0, 0.3), seeds=3, horizon=4, starts=["2022-01-10"])
    rows = {r["sd"]: r for r in out["rows"]}
    assert rows[0.0]["retrain_with"] == rows[0.0]["retrain_without"] == 0  # no error, no alarm
    assert rows[0.3]["retrain_without"] > 0  # the break bites
    for x in out["runs"]:
        if x["weeks_with"] is not None:
            assert x["weeks_without"] is not None and x["weeks_without"] <= x["weeks_with"]
