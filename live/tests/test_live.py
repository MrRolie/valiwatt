"""Tests of the live freeze's pre-declared rule and of the issue log's order guard (docs/plan-live.md).
Run: uv run pytest live/tests (outside tests/, so v2's code hash is unchanged)."""
import numpy as np
import pandas as pd
import pytest

from live.freeze import LABELS, MIN_N, bin_of, experimental_rule, groups_of
from live.issue import append_log
from valiwatt.forecast.freeze import FrozenModelError


def test_merge_rule_coldest_first_into_warmer_neighbour():
    m = groups_of({"<-20": 30, "-20..-11": 600, "-11..0": 1900, "0..10": 2000, ">10": 4000})
    assert m["<-20"] == m["-20..-11"] == "<-20+-20..-11"
    assert m["-11..0"] == "-11..0" and m[">10"] == ">10"


def test_merge_rule_warm_leftover_joins_previous_group():
    m = groups_of({"<-20": 0, "-20..-11": 0, "-11..0": 500, "0..10": 500, ">10": MIN_N - 1})
    assert m[">10"] == m["0..10"] == "0..10+>10"
    assert set(m) == set(LABELS)


def test_merge_rule_refuses_when_nothing_reaches_min_n():
    with pytest.raises(FrozenModelError):
        groups_of({lab: 1 for lab in LABELS})


def test_bins_are_left_closed_on_hourly_temperature():
    assert list(bin_of([-20.0, -11.0, 0.0, 10.0, -11.01])) == ["-20..-11", "-11..0", "0..10", ">10", "-20..-11"]


def test_rule_quantiles_and_median_per_group():
    rng = np.random.default_rng(0)
    n = 4000
    temp = np.where(np.arange(n) < 2000, -15.0, 15.0)
    e = np.where(temp < 0, rng.normal(0.03, 0.03, n), rng.normal(0.01, 0.02, n))
    pred = np.full(n, 30000.0)
    cal = pd.DataFrame({"target": pred * (1 + e), "pred": pred, "temp": temp})
    rule = experimental_rule(cal, "x" * 64)
    cold = rule["groups"][rule["bin_to_group"]["-20..-11"]]
    assert abs(cold["median"] - 0.03) < 0.005 and cold["lo"] < cold["median"] < cold["hi"]
    assert cold["hi"] - cold["median"] > 0.04  # ~1.645 sd


def _body(first_end):
    return {"day": "2026-12-01", "hours": [{"hour_ending_utc": (first_end + pd.Timedelta(hours=h)).isoformat()}
                                           for h in range(24)]}


def test_log_refuses_after_first_hour_ends(tmp_path):
    first_end = pd.Timestamp("2026-12-01T06:00Z")
    with pytest.raises(RuntimeError):
        append_log(_body(first_end), first_end, log=tmp_path / "l.jsonl")
    append_log(_body(first_end), first_end - pd.Timedelta(minutes=40), log=tmp_path / "l.jsonl")
    assert (tmp_path / "l.jsonl").read_text().count("\n") == 1
