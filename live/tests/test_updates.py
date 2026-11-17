"""Tests of line 3, the updates (docs/plan-live.md, "Line 3, the updates"): the rule never reads an hour after k, the
log's deadline and duplicate guards, the due-update choice, the frozen files' hash checks.
Run: uv run pytest live/tests."""
import json

import numpy as np
import pandas as pd
import pytest

from live import updates as up
from valiwatt.forecast.features import day_origin
from valiwatt.forecast.freeze import FrozenModelError

DAY = "2025-01-21"
ORIGIN = day_origin(pd.Timestamp(DAY))
H = pd.Timedelta(hours=1)


def params(m=1, rho=0.93):
    rng = {str(l): {"lo": -0.02, "hi": 0.02, "n": 9999, "group": [l]} for l in range(1, 24)}
    p = {"m": m, "rho": rho, "alpha": 0.1, "range_by_lead": rng, "n_paths": 200, "window_days": 30,
         "lib_sha256": "x", "base": "test"}
    p["sha256"] = up._sha(p)
    return p


def lib(n=60, seed=0):
    r = np.random.default_rng(seed)
    return r.normal(0, 0.03, (n, 24)), np.arange(1, n + 1) % 366 + 1


def issue_body():
    F = 30000 + 1000 * np.sin(np.arange(24) / 3)
    return {"day": DAY, "origin": ORIGIN.isoformat(), "sha256": "issue",
            "hours": [{"hour_ending_utc": (ORIGIN + (i + 1) * H).isoformat(), "official": float(F[i])}
                      for i in range(24)]}, F


def test_ebar_uses_only_the_last_m_finite_hours_up_to_k():
    e = np.array([[0.1, np.nan, 0.3, 0.4, 9.0] + [9.0] * 19])
    assert up.ebar(e, 4, 1)[0] == pytest.approx(0.4)
    assert up.ebar(e, 4, 2)[0] == pytest.approx(0.35)
    assert up.ebar(e, 2, 3)[0] == pytest.approx(0.1)  # NaN skipped, nothing before the origin
    assert up.ebar(np.full((1, 24), np.nan), 5, 1)[0] == 0.0  # no complete hour: U = F


def test_update_formula_decays_by_lead():
    F = np.full((1, 24), 100.0)
    U, lead = up.updated(F, np.array([0.1]), 22, 0.5)
    assert list(lead) == [1, 2] and U[0] == pytest.approx([105.0, 102.5])


def test_an_update_never_reads_an_hour_after_k():
    body, F = issue_body()
    e_lib, doy = lib()
    A = F * 1.05
    at = ORIGIN + 6 * H + up.WAIT
    a = up.make(DAY, 6, at, body, A, params(), e_lib, doy)
    A2 = A.copy(); A2[6:] = 1.0  # change every hour after k
    b = up.make(DAY, 6, at, body, A2, params(), e_lib, doy)
    assert a == b
    A3 = A.copy(); A3[5] *= 1.1  # change hour k itself: it must matter
    assert up.make(DAY, 6, at, body, A3, params(), e_lib, doy)["sha256"] != a["sha256"]


def test_peak_paths_are_reproducible_and_bounded_below_by_the_measured_peak():
    body, F = issue_body()
    e_lib, doy = lib()
    A = F.copy(); A[2] = 50000.0
    p1 = up.make(DAY, 5, ORIGIN, body, A, params(), e_lib, doy)["peak"]
    p2 = up.make(DAY, 5, ORIGIN, body, A, params(), e_lib, doy)["peak"]
    assert p1 == p2 and p1["p10"] >= 50000.0 and p1["hour_ending"] == 3


def test_append_refuses_after_the_deadline_and_twice(tmp_path):
    body, F = issue_body()
    e_lib, doy = lib()
    log = tmp_path / "u.jsonl"
    at = ORIGIN + 6 * H + up.WAIT
    b = up.make(DAY, 6, at, body, F, params(), e_lib, doy)
    with pytest.raises(RuntimeError, match="deadline"):
        up.append(b, now=ORIGIN + 7 * H, log=log)
    assert up.append(b, now=at, log=log) is True
    assert up.append(b, now=at, log=log) is False
    assert len(log.read_text().splitlines()) == 1


def test_append_refuses_a_tampered_update(tmp_path):
    body, F = issue_body()
    e_lib, doy = lib()
    at = ORIGIN + 3 * H + up.WAIT
    b = up.make(DAY, 3, at, body, F, params(), e_lib, doy)
    b["hours"][0]["update"] += 1
    with pytest.raises(RuntimeError, match="hash"):
        up.append(b, now=at, log=tmp_path / "u.jsonl")


def test_the_live_log_takes_the_machine_clock_only():
    body, F = issue_body()
    e_lib, doy = lib()
    b = up.make(DAY, 1, ORIGIN + H + up.WAIT, body, F, params(), e_lib, doy)
    with pytest.raises(RuntimeError, match="machine"):
        up.append(b, now=ORIGIN + H + up.WAIT, log=up.LOG)


def test_due_is_the_newest_complete_hour_inside_its_window():
    complete = np.zeros(24, dtype=bool); complete[:8] = True
    now = ORIGIN + 8 * H + up.WAIT + pd.Timedelta(minutes=5)
    assert up.due(ORIGIN, complete, set(), now) == 8
    assert up.due(ORIGIN, complete, {8}, now) is None
    assert up.due(ORIGIN, complete, set(), ORIGIN + 8 * H + pd.Timedelta(minutes=10)) is None  # before +25 min
    complete[7] = False  # hour 8 incomplete: nothing is due at k = 8, and k = 7's window is over
    assert up.due(ORIGIN, complete, set(), now) is None
    assert up.due(ORIGIN, np.zeros(24, dtype=bool), set(), ORIGIN + up.WAIT) == 0


def test_load_refuses_files_that_differ_from_the_record(tmp_path, monkeypatch):
    e_lib, doy = lib()
    import hashlib
    p = params()
    p.pop("sha256")
    p["lib_sha256"] = hashlib.sha256(np.ascontiguousarray(e_lib).tobytes() + np.ascontiguousarray(doy).tobytes()).hexdigest()
    p["sha256"] = up._sha({k: v for k, v in p.items()})
    (tmp_path / "updates.json").write_text(json.dumps(p))
    np.savez(tmp_path / "updates_lib.npz", e=e_lib, doy=doy)
    rec = tmp_path / "REC.json"
    rec.write_text(json.dumps({"params_sha256": p["sha256"], "lib_sha256": p["lib_sha256"]}))
    monkeypatch.setattr(up, "PARAMS", tmp_path / "updates.json")
    monkeypatch.setattr(up, "LIB", tmp_path / "updates_lib.npz")
    monkeypatch.setattr(up, "RECORD", rec)
    assert up.load(tagged=False)[0]["m"] == 1
    np.savez(tmp_path / "updates_lib.npz", e=e_lib * 1.01, doy=doy)
    with pytest.raises(FrozenModelError, match="lib"):
        up.load(tagged=False)
    np.savez(tmp_path / "updates_lib.npz", e=e_lib, doy=doy)
    rec.write_text(json.dumps({"params_sha256": "other", "lib_sha256": p["lib_sha256"]}))
    with pytest.raises(FrozenModelError, match="UPDATES-FREEZE"):
        up.load(tagged=False)


def test_the_decay_runs_from_the_hour_the_error_came_from():
    e = np.full((1, 24), np.nan); e[0, 4] = 0.10  # hour 5 has its error; hours 6..k do not
    eb, j = up.ebar_at(e, 8, 1)
    assert eb[0] == pytest.approx(0.10) and j[0] == 5
    U, _ = up.updated(np.full((1, 24), 100.0), eb, 8, 0.5, j)
    assert U[0, 0] == pytest.approx(100 * (1 + 0.10 * 0.5 ** (9 - 5)))
    U_k, _ = up.updated(np.full((1, 24), 100.0), eb, 8, 0.5)  # without the anchor: the design days' case, j = k
    assert U_k[0, 0] == pytest.approx(100 * (1 + 0.10 * 0.5))


def test_lead_merge_goes_to_the_shorter_lead_and_ties_go_to_smaller_m_then_rho(monkeypatch):
    rng = np.random.default_rng(1)
    F = np.full((90, 24), 100.0); A = F * (1 + rng.normal(0, 0.02, (90, 24)))
    monkeypatch.setattr(up, "NMIN", 200)
    r = up.lead_range(F, A, 1, 0.9)
    assert r["23"]["group"] == r["22"]["group"] and min(r["23"]["group"]) < 23  # 90 pairs at lead 23: merged down
    assert r["1"]["group"] == [1]
    monkeypatch.setattr(up, "mape_pairs", lambda F, A, m, rho: 1.0)  # all equal
    assert up.choose(F, A)[0] == (1, 0.80)
