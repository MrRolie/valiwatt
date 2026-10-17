"""Shared test helpers for the v2 ledger, lineage and report: v2 ledger entries, 2025 starts and their gates'
records, and stand-ins for lane D's interfaces (monitor/run.py `replay_starts` and `frozen_digest`), written to the
shapes lane D's spec gives them. A stand-in is installed only where monitor/run.py lacks the real function, so once
lane D lands the tests that run on a real MLflow store exercise the real seam."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from valiwatt import tracking
from valiwatt.config import FREEZE_TAG
from valiwatt.monitor import run as monitor_run
from valiwatt.report import ledger

F0, F1, F2 = "a0" * 20, "b1" * 20, "c2" * 20  # freeze commits
RULES, CODE = "d" * 64, "c" * 64
MODEL = "e" * 64
FROZEN = {"production": MODEL, "comparators": {"forecast": "1" * 64, "persistence": "2" * 64},
          "thresholds": "3" * 64, "cold_c": "-14.5", "reference": "4" * 64}


def frozen_digest(d: dict) -> str:
    """Lane D's `frozen_digest`, as its spec gives it: sha256 of the dict as canonical JSON (sorted keys, no
    spaces)."""
    return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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


def store_starts(year: int, tag: str = FREEZE_TAG, client=None, ledger_data: dict | None = None) -> list[dict]:
    """A stand-in for lane D's `replay_starts` over a real MLflow store: every replay run of `year` under `tag` (no
    freeze tag counts as v1's), whatever its status, deleted included, each with its gate's record, plus the ledger's
    entries the store lacks, by start time."""
    from mlflow.entities import ViewType

    c = client or tracking.client()
    exp = c.get_experiment_by_name(monitor_run.EXPERIMENT)
    runs = [] if exp is None else c.search_runs(
        [exp.experiment_id], f"tags.kind = 'replay' and tags.year = '{year}'", run_view_type=ViewType.ALL,
        max_results=50_000)
    out = []
    for r in runs:
        t = r.data.tags
        if (t.get("freeze_tag") or ledger.V1_TAG) != tag:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            try:
                g = json.loads(Path(c.download_artifacts(r.info.run_id, "results/held_out_gate.json", tmp))
                               .read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - no gate's record
                g = None
        out.append({"run_id": r.info.run_id, "started_utc": ledger._utc(r.info.start_time),
                    "freeze_commit": t.get("freeze_commit", ""), "git_commit": t.get("git_commit", ""),
                    "standing": t.get("standing", ""),
                    "finished": r.info.status == "FINISHED" and t.get("status") == "complete",
                    "running": r.info.status == "RUNNING", "stage": t.get("stage", ""), "error": t.get("error", ""),
                    "gate": g, "holds_results": bool(r.data.metrics) or r.info.status == "FINISHED"})
    data = ledger_data if ledger_data is not None else {"replays": ledger.read()}
    ids = {s["run_id"] for s in out}
    for e in data.get("replays", []):
        if e["freeze_tag"] == tag and e["year"] == year and e["run_id"] not in ids:
            out.append(from_entry(e))
    return sorted(out, key=lambda s: (s["started_utc"], s["run_id"]))


def install_lane_d(monkeypatch, starts=None) -> None:
    """Lane D's functions where monitor/run.py lacks them; `starts`, when given, a fixed list `replay_starts`
    returns (a fake store's), whatever is installed."""
    if not hasattr(monitor_run, "frozen_digest"):
        monkeypatch.setattr(monitor_run, "frozen_digest", frozen_digest, raising=False)
    if starts is not None:
        monkeypatch.setattr(monitor_run, "replay_starts", lambda year, tag=FREEZE_TAG, client=None, ledger=None:
                            [s for s in starts if year == 2025], raising=False)
    elif not hasattr(monitor_run, "replay_starts"):
        monkeypatch.setattr(monitor_run, "replay_starts", store_starts, raising=False)
