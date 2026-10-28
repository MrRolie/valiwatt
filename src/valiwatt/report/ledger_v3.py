"""v3's replay ledger, `docs/replay-ledger-v3.json` (docs/plan-v3.md, section 10.3): one entry per finished v3 replay
under `v3-rules-frozen`, of 2024 (the step-5 rehearsal, labelled seen by the developers) and 2025, written, scanned,
checked and committed as v2's ledger is (report/ledger.py, docs/plan-v2.md section 10.5).

What the ledger accepts (the shape of an entry, the checks of a whole ledger, an entry derived from a run, how a stored
run and its recorded entry disagree) is decided in ledger_rules_v3.py, a rule file the 2025 gate reads, and imported
here; this file renders, scans and writes the ledger and decides nothing of its own. Only `replay_v3` runs are read, so
v2's ledger never takes a v3 run, and v2's reader refuses a v3 entry (its shapes do not list v3's tag). A run made
before the tag (`v3-pre-freeze`) never enters it; a run under any other or no freeze tag is refused. Starts come from
records_v3.replay_starts_v3, the one reader of v3's starts.

This file reads only v3's own ledger: never `docs/replay-ledger.json`.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from valiwatt import paths
from valiwatt.config import HELD_OUT_YEAR, V3_FREEZE_TAG, V3_LEDGER, V3_PRE_FREEZE_TAG
from valiwatt.ledger_rules_v3 import (  # noqa: F401 - the ledger's rules, read here and by the gate from one home
    KIND,
    SHAPES,
    VERDICT_KEYS,
    YEARS,
    _order,
    check,
    check_entry,
    disagreement,
    entry,
    fields_for,
    path,
    read,
)
from valiwatt.report import ledger as v2_ledger
from valiwatt.report.ledger import LedgerError  # noqa: F401 - what the ledger refuses, as callers catch it here

ABSENT = v2_ledger.ABSENT


def dumps(entries: list[dict]) -> str:
    return json.dumps({"replays": entries}, indent=2, ensure_ascii=False) + "\n"


def digest(p: Path | None = None) -> str:
    """The sha256 of the file's bytes (v3's report stamp records it); `absent` without a file."""
    p = path() if p is None else p
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ABSENT


def from_store(c=None) -> list[dict]:
    """The entry of every finished `replay_v3` run in the store (MLflow FINISHED and status tag `complete`), deleted
    runs included, under `v3-rules-frozen`. A pre-freeze run (the step-2 rehearsal) is skipped, never entered, and so
    is a fresh clone's rerun (its tag `fresh_clone` true: docs/plan-v3.md section 3.1, a fresh clone writes no ledger
    entry); a run under any other freeze tag, or none, is refused; a run of a year v3's freeze does not replay is
    refused by its entry's check."""
    from mlflow.entities import ViewType

    from valiwatt import tracking
    from valiwatt.monitor.run import EXPERIMENT

    c = c or tracking.client()
    exp = c.get_experiment_by_name(EXPERIMENT)
    if exp is None:
        return []
    runs = c.search_runs([exp.experiment_id], f"tags.kind = '{KIND}' and attributes.status = 'FINISHED' and "
                                              "tags.status = 'complete'",
                         run_view_type=ViewType.ALL, max_results=50_000)
    out = []
    for run in runs:
        t = run.data.tags.get("freeze_tag") or ""
        if t == V3_PRE_FREEZE_TAG or run.data.tags.get("fresh_clone") == "true":
            continue
        if t != V3_FREEZE_TAG:
            raise LedgerError(f"v3 replay {run.info.run_id} carries the freeze tag {t!r}: a v3 ledger entry is under "
                              f"{V3_FREEZE_TAG!r}")
        out.append(entry(run, v2_ledger.results_of(c, run)))
    return out


def starts(c=None, year: int = HELD_OUT_YEAR, ledger: dict | None = None) -> list[dict]:
    """Every v3 replay start of `year` (records_v3.replay_starts_v3, the one reader of v3's starts). ledger: the parsed
    ledger-v3 it merges; None, the committed file."""
    from valiwatt.records_v3 import replay_starts_v3

    return replay_starts_v3(year, c, ledger)


def merge(existing: list[dict], stored: list[dict], starts: list[dict] | None = None) -> list[dict]:
    """The existing entries unchanged, plus the stored runs they lack, sorted by start time. A stored run whose entry
    differs in any field is refused: the ledger is append-only."""
    by_id = {e["run_id"]: e for e in existing}
    new = []
    for s in stored:
        if s["run_id"] in by_id:
            diff = disagreement(s, by_id[s["run_id"]])
            if diff:
                raise LedgerError(f"replay {s['run_id']} in the store disagrees with its v3 ledger entry on {diff}; "
                                  "the ledger is append-only, so an entry is never rewritten")
        else:
            new.append(s)
    return check(sorted(existing + new, key=_order), starts)


def scan(text: str, root: Path | None = None) -> None:
    from valiwatt.safety import private_patterns, scan_text

    issues = scan_text(V3_LEDGER, text, private_patterns(root or paths.REPO_ROOT))
    if issues:
        raise LedgerError(f"the v3 ledger's new content fails the public-safety scan, so it is not written: {issues}")


def sync(p: Path | None = None, c=None, root: Path | None = None) -> list[dict]:
    """Append the store's finished v3 replays the ledger lacks; write only when the content changes. Every label is
    checked against the store's 2025 starts, unfinished ones included, and this ledger's."""
    p = path() if p is None else p
    existing = read(p)
    entries = merge(existing, from_store(c), starts(c, ledger={"replays": existing}))
    text = dumps(entries)
    old = p.read_text(encoding="utf-8") if p.exists() else None
    if text == old or (old is None and not entries):
        return entries
    scan(text, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=p.parent, delete=False, suffix=".tmp") as f:
        f.write(text)
    os.replace(f.name, p)
    return entries


def run_sync() -> int:
    """`valiwatt ledger-v3`: sync docs/replay-ledger-v3.json from v3's store."""
    entries = sync()
    print(f"v3 replay ledger: {len(entries)} entries in {V3_LEDGER}")
    return 0
