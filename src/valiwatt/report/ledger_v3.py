"""v3's replay ledger, `docs/replay-ledger-v3.json` (docs/plan-v3.md, section 10.3): one entry per finished v3 replay
under `v3-rules-frozen`, of 2024 (the step-5 rehearsal, labelled seen by the developers) and 2025, written, scanned,
checked and committed as v2's ledger is (report/ledger.py, docs/plan-v2.md section 10.5).

Its entries have v2's nineteen fields, in v2's order, with v3's verdicts: `verdict` is {"terms", "family"}, the
decision rule's two verdicts of section 8.3 (evaluate.TERMS_VERDICTS, evaluate.VERDICTS); `frozen_sha256` is the one
hash of the artifacts that judged the replay (monitor/run.py `frozen_digest` of its results' items: v3, the three
comparison models, whose hashes cover their bin edges and half-widths, the thresholds, cold_c and the forecast-drift
reference). Only `replay_v3` runs are read, so v2's ledger never takes a v3 run, and v2's reader refuses a v3 entry
(its shapes do not list v3's tag); the v3 functions here are v2's bodies on v3's shape, never v2's functions called
on v3's entries. A run made before the tag (`v3-pre-freeze`) never enters it; a run under any other or no freeze tag
is refused. Starts come from records_v3.replay_starts_v3, the one reader of v3's starts.

This file reads only v3's own ledger: never `docs/replay-ledger.json`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

from valiwatt import paths
from valiwatt.config import HELD_OUT_YEAR, V3_FREEZE_TAG, V3_LEDGER, V3_PRE_FREEZE_TAG
from valiwatt.report import ledger as v2_ledger
from valiwatt.report.ledger import (
    COMMIT,
    DAY,
    RUN_ID,
    SHA256,
    TAG,
    UTC,
    V2_FIELDS,
    WORD,
    LedgerError,
    _check_v2,
    _utc,
    recommendation_counts,
)

KIND = "replay_v3"
# The shape of v3's entries: v2's nineteen names, in v2's order (section 10.3).
SHAPES = {V3_FREEZE_TAG: V2_FIELDS}
YEARS = {V3_FREEZE_TAG: v2_ledger.YEARS[V3_FREEZE_TAG]}  # config.FREEZES' years of v3's freeze: 2024 and 2025
VERDICT_KEYS = ["terms", "family"]
ABSENT = v2_ledger.ABSENT


def path() -> Path:
    """The ledger's file, under paths.REPO_ROOT read at call time."""
    return paths.REPO_ROOT / V3_LEDGER


def fields_for(tag: str) -> tuple:
    """The fields of an entry under `tag`: v3's tag only; any other is refused."""
    if tag not in SHAPES:
        raise LedgerError(f"a v3 ledger entry's freeze_tag must be {V3_FREEZE_TAG!r}, not {tag!r}")
    return SHAPES[tag]


def entry(run, results: dict) -> dict:
    """The ledger entry of a finished v3 replay run, from its tags and its `results/replay.json`: v2's entry, with the
    verdicts {"terms", "family"} its results hold and the digest of the `frozen_sha256` it logged."""
    from valiwatt.monitor.run import frozen_digest

    tags, summary = run.data.tags, results["summary"]
    recs = [{"week_start": str(d["week_start"])[:10], "recommendation": d["recommendation"]}
            for d in results["decisions"] if d["recommendation"]]
    if recommendation_counts(recs) != summary["recommendations"]:
        raise LedgerError(f"replay {run.info.run_id}: its decisions and its summary count different recommendations")
    if results.get("freeze_commit") != tags.get("freeze_commit"):
        raise LedgerError(f"replay {run.info.run_id}: its results and its tags name different freeze commits")
    if tags.get("freeze_tag") != results.get("freeze_tag"):
        raise LedgerError(f"replay {run.info.run_id}: its results and its tags name different freeze tags")
    verdict = results.get("verdict")
    if not isinstance(verdict, dict):
        raise LedgerError(f"replay {run.info.run_id}: its results hold no verdict")
    if not isinstance(results.get("frozen_sha256"), dict):
        raise LedgerError(f"replay {run.info.run_id}: its results hold no frozen_sha256 of what judged it")
    e = {
        "run_id": run.info.run_id, "year": int(tags.get("year", "0")), "freeze_tag": results.get("freeze_tag", ""),
        "freeze_commit": tags.get("freeze_commit", ""), "rules_sha256": tags.get("rules_sha256", ""),
        "code_sha256": tags.get("code_sha256", ""), "started_utc": _utc(run.info.start_time),
        "ended_utc": _utc(run.info.end_time), "status": tags.get("status") or "unmarked",
        "mape": summary["mape"]["mean"], "coverage": summary["coverage"]["mean"], "recommendations": recs,
        "departure": tags.get("departure") == "true",
        "departure_from": [x for x in tags.get("departure_from", "").split(",") if x],
        "evaluation": tags.get("evaluation", ""), "model_sha256": tags.get("coefficient_sha256", ""),
        "verdict": {"terms": verdict.get("terms"), "family": verdict.get("family")},
        "frozen_sha256": frozen_digest(results["frozen_sha256"]), "standing": tags.get("standing", ""),
    }
    check_entry(e)
    return e


def check_entry(e) -> None:
    """The fixed shape of a v3 entry, v2's checks field by field (report/ledger.py check_entry and its v2 fields),
    with v3's verdicts: exactly {"terms", "family"}, terms one of evaluate.TERMS_VERDICTS and family one of
    evaluate.VERDICTS; freeze_tag v3's only."""
    from valiwatt.forecast.evaluate import STANDINGS, TERMS_VERDICTS, VERDICTS
    from valiwatt.monitor.rules import INVESTIGATE, RECALIBRATE, RETRAIN

    if not isinstance(e, dict):
        raise LedgerError(f"a ledger entry must be an object, not {type(e).__name__}")
    fields = fields_for(e.get("freeze_tag"))
    if tuple(e) != fields:
        raise LedgerError(f"a v3 ledger entry must hold exactly {list(fields)} in that order, not {list(e)}")
    rid = e["run_id"]

    def bad(field):
        return LedgerError(f"v3 ledger entry {rid!r}: {field} is {e[field]!r}")

    def text(field, pattern):
        if not isinstance(e[field], str) or not pattern.fullmatch(e[field]):
            raise bad(field)

    for field, pattern in (("run_id", RUN_ID), ("freeze_tag", TAG), ("freeze_commit", COMMIT),
                           ("rules_sha256", SHA256), ("code_sha256", SHA256), ("started_utc", UTC),
                           ("ended_utc", UTC), ("status", WORD), ("model_sha256", SHA256),
                           ("frozen_sha256", SHA256)):
        text(field, pattern)
    if type(e["year"]) is not int or e["year"] not in YEARS[e["freeze_tag"]]:
        raise bad("year")
    for field, high in (("mape", math.inf), ("coverage", 1.0)):
        x = e[field]
        if type(x) is not float or not (0.0 <= x <= high):
            raise bad(field)
    if type(e["departure"]) is not bool:
        raise bad("departure")
    if not isinstance(e["departure_from"], list) or not all(isinstance(x, str) and RUN_ID.fullmatch(x)
                                                             for x in e["departure_from"]):
        raise bad("departure_from")
    if e["departure"] != bool(e["departure_from"]):
        raise bad("departure_from")
    recs = e["recommendations"]
    if not isinstance(recs, list) or not all(
            isinstance(r, dict) and list(r) == ["week_start", "recommendation"] and isinstance(r["week_start"], str)
            and DAY.fullmatch(r["week_start"]) and r["recommendation"] in (RETRAIN, RECALIBRATE, INVESTIGATE)
            for r in recs):
        raise bad("recommendations")
    v = e["verdict"]
    if not isinstance(v, dict) or list(v) != VERDICT_KEYS or v["terms"] not in TERMS_VERDICTS \
            or v["family"] not in VERDICTS:
        raise bad("verdict")
    held_out = e["year"] == HELD_OUT_YEAR
    if not isinstance(e["standing"], str) or (e["standing"] not in STANDINGS if held_out else e["standing"] != ""):
        raise bad("standing")
    allowed = ({"held_out": "clean", "post_hoc": "post_hoc", "seen_by_developers": None} if held_out
               else {"seen_by_developers": None})
    label = e["evaluation"]
    if not isinstance(label, str) or label not in allowed or allowed[label] not in (None, e["standing"]):
        raise bad("evaluation")


def _order(e: dict) -> tuple:
    return e["started_utc"], e["run_id"]


def check(entries, starts: list[dict] | None = None) -> list[dict]:
    """A whole v3 ledger: every entry well formed, each run once, sorted by start time; then v2's rule on v3's tag
    (report/ledger.py `_check_v2`): every entry that is not a departure has the freeze commit of the first 2024 entry,
    no 2025 entry starts before it, and each label is evaluate.evaluation_label's under the starts before it (`starts`:
    records_v3.replay_starts_v3(2025), unfinished ones included; None reads the ledger's own 2025 entries)."""
    if not isinstance(entries, list):
        raise LedgerError("the v3 ledger's `replays` must be a list")
    for e in entries:
        check_entry(e)
    ids = [e["run_id"] for e in entries]
    if len(set(ids)) != len(ids):
        raise LedgerError(f"the v3 ledger holds a run twice: {sorted({i for i in ids if ids.count(i) > 1})}")
    if entries != sorted(entries, key=_order):
        raise LedgerError("the v3 ledger's entries are not sorted by start time")
    if entries:
        _check_v2(V3_FREEZE_TAG, entries, starts)
    return entries


def read(p: Path | None = None) -> list[dict]:
    """The v3 ledger's entries; a missing file holds none."""
    p = path() if p is None else p
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise LedgerError(f"{p.name} is not valid JSON: {e}") from e
    if not isinstance(data, dict) or list(data) != ["replays"]:
        raise LedgerError(f"{p.name} must hold one key, `replays`")
    return check(data["replays"])


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


def disagreement(stored: dict, recorded: dict) -> list[str]:
    """The fields on which a stored run's entry and its recorded one differ: every field of v3's shape, its freeze tag
    included, and any field the recorded entry holds beyond it."""
    fields = SHAPES[V3_FREEZE_TAG]
    return [k for k in fields if stored.get(k) != recorded.get(k)] + [k for k in recorded if k not in fields]


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
