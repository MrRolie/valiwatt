"""Shared test helpers for the v2 ledger, lineage and report: v2 ledger entries, 2025 starts and their gates'
records, in the shapes monitor/run.py gives them (`replay_starts`, the 2025 gate's record), and a fixed list of starts
a fake store's tests install in place of `replay_starts`. The digest is monitor/run.py's own `frozen_digest`; the
tests that run on a real MLflow store read the starts through the real `replay_starts`."""

from __future__ import annotations

from valiwatt.config import FREEZE_TAG
from valiwatt.monitor import run as monitor_run
from valiwatt.monitor.run import frozen_digest
from valiwatt.report import ledger

F0, F1, F2 = "a0" * 20, "b1" * 20, "c2" * 20  # freeze commits
RULES, CODE = "d" * 64, "c" * 64
MODEL = "e" * 64
FROZEN = {"production": MODEL, "comparators": {"forecast": "1" * 64, "persistence": "2" * 64},
          "thresholds": "3" * 64, "cold_c": "-14.5", "reference": "4" * 64}
DIGEST = frozen_digest(FROZEN)


def utc(n: int, year: int = 2026, month: int = 11) -> str:
    """The n-th start time, in the ledger's form."""
    return f"{year}-{month:02d}-{n + 1:02d}T00:00:00.000Z"


def end(started: str) -> str:
    return started[:11] + "01" + started[13:]


def v2_entry(run_id: str, year: int = 2024, started: str | None = None, evaluation: str | None = None,
             standing: str | None = None, freeze_commit: str = F0, departure_from=(), mape: float = 0.031,
             coverage: float = 0.901, verdict=("better", "better"), model_sha256: str = MODEL,
             frozen_sha256: str = DIGEST, recommendations=()) -> dict:
    """A v2 ledger entry, its fields in V2_FIELDS's order. Defaults: a 2024 entry labelled seen by the developers
    with no standing; a 2025 one, the clean test of record."""
    started = started or utc(0 if year == 2024 else 10)
    if standing is None:
        standing = "" if year == 2024 else "clean"
    if evaluation is None:
        evaluation = "seen_by_developers" if year == 2024 else {"clean": "held_out", "post_hoc": "post_hoc"}[standing]
    e = {"run_id": run_id, "year": year, "freeze_tag": FREEZE_TAG, "freeze_commit": freeze_commit,
         "rules_sha256": RULES, "code_sha256": CODE, "started_utc": started, "ended_utc": end(started),
         "status": "complete", "mape": mape, "coverage": coverage,
         "recommendations": [{"week_start": w, "recommendation": r} for w, r in recommendations],
         "departure": bool(departure_from), "departure_from": list(departure_from),
         "evaluation": evaluation, "model_sha256": model_sha256,
         "verdict": {"shipped": verdict[0], "family": verdict[1]}, "frozen_sha256": frozen_sha256,
         "standing": standing}
    assert tuple(e) == ledger.V2_FIELDS
    return e


def gate(standing: str = "clean", causes=(), ruling: dict | None = None, frozen: dict | None = None,
         first: str = "f" * 32) -> dict:
    """A 2025 gate's record (results/held_out_gate.json), as lane D's spec gives it."""
    return {"first_2024_run_id": first, "frozen_run_id": "9" * 32, "validation_run_id": "8" * 32,
            "dq_run_id": "7" * 32, "frozen_sha256": FROZEN if frozen is None else frozen, "freeze_commit": F0,
            "head_commit": "1" * 40, "rule_commits": [], "causes": list(causes), "ruling": ruling,
            "standing": standing}


def ruling(outcome: str = "clean", start: str = "", commit: str = "1" * 40, head: str | None = None) -> dict:
    """The owner's ruling entry a gate read: the commit it names, its outcome, its bold head, the stopped start it
    names ("" when none) and the evidence run the gate found."""
    words = "clean" if outcome == "clean" else "post hoc"
    return {"commit": commit, "outcome": outcome, "head": head or f"2026-12-01, the owner's ruling on 2025: {words}",
            "start": start, "evidence_run_id": "6" * 32}


def start(run_id: str, started: str, finished: bool = True, gate_record: dict | None = None, stage: str = "",
          error: str = "", standing: str | None = None, freeze_commit: str = F0, git_commit: str = "1" * 40,
          holds_results: bool | None = None) -> dict:
    """One 2025 start, in the shape lane D's `replay_starts` gives it."""
    return {"run_id": run_id, "started_utc": started, "freeze_commit": freeze_commit, "git_commit": git_commit,
            "standing": (gate_record or {}).get("standing", "") if standing is None else standing,
            "finished": finished, "running": False, "stage": stage or ("logged" if finished else "inputs_read"),
            "error": error, "gate": gate_record,
            "holds_results": finished if holds_results is None else holds_results}


def from_entry(e: dict) -> dict:
    """A ledger entry the store lacks, as `replay_starts` merges it: finished, no gate's record."""
    return start(e["run_id"], e["started_utc"], True, None, stage="", standing=e["standing"], git_commit="",
                 freeze_commit=e["freeze_commit"], holds_results=True)


def install_lane_d(monkeypatch, starts=None) -> None:
    """`starts`, when given, the fixed list monitor/run.py `replay_starts` returns (a fake store's 2025 starts); without
    it, the real `replay_starts` reads the store."""
    if starts is not None:
        monkeypatch.setattr(monitor_run, "replay_starts", lambda year, tag=FREEZE_TAG, client=None, ledger=None:
                            [s for s in starts if year == 2025])
