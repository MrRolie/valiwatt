"""Tests of the live freeze's pre-declared rule and of the issue log's order guard (docs/plan-live.md).
Run: uv run pytest live/tests (outside tests/, so v2's code hash is unchanged)."""
from pathlib import Path

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


def _body(origin=pd.Timestamp("2026-12-01T05:00Z"), issued_at=None):
    import hashlib
    import json
    body = {"day": "2026-12-01", "issued_at": (issued_at or origin).isoformat(), "origin": origin.isoformat(),
            "official_sha256": "a", "experimental_sha256": "b",
            "hours": [{"hour_ending_utc": (origin + pd.Timedelta(hours=h)).isoformat(), "official": 30000.0}
                      for h in range(1, 25)]}
    body["sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    return body


ORIGIN = pd.Timestamp("2026-12-01T05:00Z")


def test_log_refuses_at_or_after_first_hour_end(tmp_path):
    with pytest.raises(RuntimeError):
        append_log(_body(), ORIGIN + pd.Timedelta(hours=1), log=tmp_path / "l.jsonl")
    assert append_log(_body(), ORIGIN + pd.Timedelta(minutes=20), log=tmp_path / "l.jsonl")
    assert (tmp_path / "l.jsonl").read_text().count("\n") == 1


def test_log_keeps_the_first_issue_of_a_day(tmp_path):
    log = tmp_path / "l.jsonl"
    assert append_log(_body(), ORIGIN, log=log)
    assert not append_log(_body(issued_at=ORIGIN + pd.Timedelta(minutes=5)), ORIGIN + pd.Timedelta(minutes=10), log=log)
    assert log.read_text().count("\n") == 1


def test_log_refuses_tampered_incomplete_or_future_issues(tmp_path):
    log = tmp_path / "l.jsonl"
    b = _body()
    b["hours"][3]["official"] = 1.0
    with pytest.raises(RuntimeError):
        append_log(b, ORIGIN, log=log)
    b = _body()
    b["hours"] = b["hours"][:23]
    with pytest.raises(RuntimeError):
        append_log(b, ORIGIN, log=log)
    with pytest.raises(RuntimeError):
        append_log(_body(issued_at=ORIGIN + pd.Timedelta(minutes=30)), ORIGIN, log=log)
    assert not log.exists()


def test_apply_rule_uses_stored_edges_and_official_forecast():
    from live.issue import apply_rule
    rule = {"edges_c": [None, 0.0, None], "labels": ["cold", "warm"], "bin_to_group": {"cold": "cold", "warm": "warm"},
            "groups": {"cold": {"lo": -0.03, "hi": 0.1, "median": 0.03}, "warm": {"lo": -0.02, "hi": 0.05, "median": 0.01}}}
    out = apply_rule([30000.0, 20000.0, 25000.0], [-5.0, 12.0, np.nan], rule)
    assert out["experimental"].round(6).tolist()[:2] == [30900.0, 20200.0]
    assert out["experimental_lower"].round(6).tolist()[:2] == [29100.0, 19600.0]
    assert out["experimental_upper"].round(6).tolist()[:2] == [33000.0, 21000.0]
    assert np.isnan(out["experimental"].iloc[2])


def test_rule_refuses_nonfinite_calibration():
    cal = pd.DataFrame({"target": [1.0] * 300, "pred": [1.0] * 299 + [0.0], "temp": [5.0] * 300})
    with pytest.raises(FrozenModelError):
        experimental_rule(cal, "x" * 64)


def test_log_refuses_an_origin_other_than_the_days():
    b = _body()
    b["origin"] = (ORIGIN + pd.Timedelta(hours=12)).isoformat()
    with pytest.raises(RuntimeError):
        append_log(b, ORIGIN, log=Path("/nonexistent/never-written.jsonl"))


def test_live_log_refuses_a_supplied_clock():
    from live.issue import LOG
    with pytest.raises(RuntimeError):
        append_log(_body(), ORIGIN, log=LOG)


def test_load_frozen_refuses_files_not_in_the_record(tmp_path, monkeypatch):
    import json as _json
    import live.issue as iss
    monkeypatch.setattr(iss, "tagged_record", lambda: {"official": {"sha256": "f" * 64}, "experimental": {"sha256": "e" * 64}})
    with pytest.raises(FrozenModelError):
        iss.load_frozen(tagged=True)


@pytest.mark.parametrize("col", ["target", "temp"])
def test_rule_refuses_nonfinite_target_or_temperature(col):
    cal = pd.DataFrame({"target": [1.0] * 300, "pred": [1.0] * 300, "temp": [5.0] * 300})
    cal.loc[7, col] = np.nan
    with pytest.raises(FrozenModelError):
        experimental_rule(cal, "x" * 64)


def test_flat_runs_are_fills_and_short_hours_are_missing():
    from live.daily import hourly
    ts = pd.date_range("2026-11-13T14:15Z", periods=16, freq="15min")
    mw = [100.0, 101, 102, 103] + [24034.0] * 7 + [200.0, 201, 202, 203, 204]
    h = hourly(pd.DataFrame({"ts_utc": ts, "mw": mw}))
    assert h["ts_utc"].tolist() == [pd.Timestamp("2026-11-13T15:00Z"), pd.Timestamp("2026-11-13T18:00Z")]
    assert h["mw"].tolist() == [101.5, 202.5]


def test_three_equal_readings_are_kept():
    from live.daily import hourly
    ts = pd.date_range("2026-11-13T14:15Z", periods=4, freq="15min")
    h = hourly(pd.DataFrame({"ts_utc": ts, "mw": [5.0, 5.0, 5.0, 6.0]}))
    assert h["mw"].tolist() == [5.25]
