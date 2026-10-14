"""The replay ledger, `docs/replay-ledger.json`: one entry per finished 2024 replay, committed with each report.

The MLflow store (`mlruns/`) stays out of git, so a fresh clone has none of the earlier replays. The ledger keeps what
the report needs of each: its run ID, freeze tag and commit, rules and code hashes, start and end times, its status
tag, MAPE, coverage, recommendations, and whether it ran as a departure and from which replays.

- `sync` reads every finished 2024 replay in the store (deleted runs included, as the moved-tag check counts them) and
  appends the ones the ledger lacks. An entry already present is never rewritten: a store run that disagrees with its
  entry is refused, and an entry the store no longer holds (a fresh clone) is kept as it is. It runs from
  `valiwatt ledger` and `valiwatt report`, never during the replay itself: every run records whether the tree was
  dirty, and a write in the middle of the chain would mark the later runs dirty.
- Every entry is checked field by field against a fixed shape (hashes, run IDs, dates, numbers, the decision table's
  recommendations), so no free text reaches the file, and the whole text passes the public-safety scan before it is
  written (no local path, no private name). Either failing refuses the write.
- `lookup` serves the report: a departed replay absent from the store is shown from its entry, labelled as recorded
  in the ledger and not re-run here.
- `digest` is the file's sha256, written into the report's stamp after the sync, so a hand edit to the ledger after a
  render fails `tests/test_report_stamp.py`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from valiwatt.paths import REPO_ROOT

LEDGER = REPO_ROOT / "docs" / "replay-ledger.json"
YEAR = 2024
FIELDS = ("run_id", "year", "freeze_tag", "freeze_commit", "rules_sha256", "code_sha256", "started_utc", "ended_utc",
          "status", "mape", "coverage", "recommendations", "departure", "departure_from")
RUN_ID = re.compile(r"[0-9a-f]{32}")
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
TAG = re.compile(r"[A-Za-z0-9._-]{1,64}")
WORD = re.compile(r"[a-z_]{1,32}")
UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class LedgerError(RuntimeError):
    pass


def _utc(ms: int | None) -> str:
    if ms is None:
        raise LedgerError("a finished replay has no end time")
    return datetime.fromtimestamp(ms // 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def recommendation_counts(recommendations: list[dict]) -> dict:
    """The counts the replay's summary carries, from the ledger's list."""
    names = [r["recommendation"] for r in recommendations]
    return {k: names.count(k) for k in sorted(set(names))}


def entry(run, results: dict) -> dict:
    """The ledger entry of a finished replay run, from its tags and its `results/replay.json`."""
    tags, summary = run.data.tags, results["summary"]
    recs = [{"week_start": str(d["week_start"])[:10], "recommendation": d["recommendation"]}
            for d in results["decisions"] if d["recommendation"]]
    if recommendation_counts(recs) != summary["recommendations"]:
        raise LedgerError(f"replay {run.info.run_id}: its decisions and its summary count different recommendations")
    if results.get("freeze_commit") != tags.get("freeze_commit"):
        raise LedgerError(f"replay {run.info.run_id}: its results and its tags name different freeze commits")
    e = {
        "run_id": run.info.run_id, "year": int(tags.get("year", "0")), "freeze_tag": results.get("freeze_tag", ""),
        "freeze_commit": tags.get("freeze_commit", ""), "rules_sha256": tags.get("rules_sha256", ""),
        "code_sha256": tags.get("code_sha256", ""), "started_utc": _utc(run.info.start_time),
        "ended_utc": _utc(run.info.end_time), "status": tags.get("status") or "unmarked",
        "mape": summary["mape"]["mean"], "coverage": summary["coverage"]["mean"], "recommendations": recs,
        "departure": tags.get("departure") == "true",
        "departure_from": [x for x in tags.get("departure_from", "").split(",") if x],
    }
    check_entry(e)
    return e


def check_entry(e) -> None:
    """The fixed shape of an entry; anything else is refused, so nothing but these fields reaches the file."""
    if not isinstance(e, dict) or tuple(e) != FIELDS:
        raise LedgerError(f"a ledger entry must hold exactly {list(FIELDS)} in that order, not "
                          f"{list(e) if isinstance(e, dict) else type(e).__name__}")
    rid = e["run_id"]

    def bad(field):
        return LedgerError(f"ledger entry {rid!r}: {field} is {e[field]!r}")

    def text(field, pattern):
        if not isinstance(e[field], str) or not pattern.fullmatch(e[field]):
            raise bad(field)

    for field, pattern in (("run_id", RUN_ID), ("freeze_tag", TAG), ("freeze_commit", COMMIT),
                           ("rules_sha256", SHA256), ("code_sha256", SHA256), ("started_utc", UTC),
                           ("ended_utc", UTC), ("status", WORD)):
        text(field, pattern)
    if type(e["year"]) is not int or e["year"] != YEAR:
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
    from valiwatt.monitor.rules import INVESTIGATE, RECALIBRATE, RETRAIN

    recs = e["recommendations"]
    if not isinstance(recs, list) or not all(
            isinstance(r, dict) and list(r) == ["week_start", "recommendation"] and isinstance(r["week_start"], str)
            and DAY.fullmatch(r["week_start"]) and r["recommendation"] in (RETRAIN, RECALIBRATE, INVESTIGATE)
            for r in recs):
        raise bad("recommendations")


def _order(e: dict) -> tuple:
    return e["started_utc"], e["run_id"]


def check(entries) -> list[dict]:
    """A whole ledger: every entry well formed, each run once, sorted by start time."""
    if not isinstance(entries, list):
        raise LedgerError("the ledger's `replays` must be a list")
    for e in entries:
        check_entry(e)
    ids = [e["run_id"] for e in entries]
    if len(set(ids)) != len(ids):
        raise LedgerError(f"the ledger holds a run twice: {sorted({i for i in ids if ids.count(i) > 1})}")
    if entries != sorted(entries, key=_order):
        raise LedgerError("the ledger's entries are not sorted by start time")
    return entries


def read(path: Path = LEDGER) -> list[dict]:
    """The ledger's entries; a missing file holds none."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise LedgerError(f"{path.name} is not valid JSON: {e}") from e
    if not isinstance(data, dict) or list(data) != ["replays"]:
        raise LedgerError(f"{path.name} must hold one key, `replays`")
    return check(data["replays"])


def dumps(entries: list[dict]) -> str:
    return json.dumps({"replays": entries}, indent=2, ensure_ascii=False) + "\n"


ABSENT = "absent"


def digest(path: Path | None = None) -> str:
    """The sha256 of the ledger file's bytes, which the report's stamp records; the word `absent` when there is no
    file, so a ledger removed after the render differs from the stamp too."""
    path = LEDGER if path is None else path
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ABSENT


def from_store(c=None) -> list[dict]:
    """The entry of every finished 2024 replay in the store, deleted runs included."""
    from mlflow.entities import ViewType

    from valiwatt import tracking
    from valiwatt.monitor.run import EXPERIMENT

    c = c or tracking.client()
    exp = c.get_experiment_by_name(EXPERIMENT)
    if exp is None:
        return []
    runs = c.search_runs([exp.experiment_id], f"tags.kind = 'replay' and tags.year = '{YEAR}' and "
                                              "attributes.status = 'FINISHED'",
                         run_view_type=ViewType.ALL, max_results=50_000)
    return [entry(run, results_of(c, run)) for run in runs]


def results_of(c, run) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            p = Path(c.download_artifacts(run.info.run_id, "results/replay.json", tmp))
        except Exception as e:  # noqa: BLE001 - any failure to read the run's results refuses the entry
            raise LedgerError(f"replay {run.info.run_id}: its results/replay.json cannot be read ({e})") from e
        return json.loads(p.read_text(encoding="utf-8"))


def disagreement(stored: dict, recorded: dict) -> list[str]:
    return [k for k in FIELDS if stored.get(k) != recorded.get(k)]


def merge(existing: list[dict], stored: list[dict]) -> list[dict]:
    """The existing entries unchanged, plus the stored runs they lack, sorted by start time. A stored run whose entry
    differs is refused: the ledger is append-only."""
    by_id = {e["run_id"]: e for e in existing}
    new = []
    for s in stored:
        if s["run_id"] in by_id:
            diff = disagreement(s, by_id[s["run_id"]])
            if diff:
                raise LedgerError(f"replay {s['run_id']} in the store disagrees with its ledger entry on {diff}; "
                                  "the ledger is append-only, so an entry is never rewritten")
        else:
            new.append(s)
    return check(sorted(existing + new, key=_order))


def scan(text: str, root: Path = REPO_ROOT) -> None:
    from valiwatt.safety import private_patterns, scan_text

    issues = scan_text("docs/replay-ledger.json", text, private_patterns(root))
    if issues:
        raise LedgerError(f"the ledger's new content fails the public-safety scan, so it is not written: {issues}")


def sync(path: Path = LEDGER, c=None, root: Path = REPO_ROOT) -> list[dict]:
    """Append the store's finished 2024 replays the ledger lacks; write only when the content changes."""
    existing = read(path)
    entries = merge(existing, from_store(c))
    text = dumps(entries)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if text == old or (old is None and not entries):
        return entries
    scan(text, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as f:
        f.write(text)
    os.replace(f.name, path)
    return entries


def lookup(run_id: str, entries: list[dict]) -> dict | None:
    found = [e for e in entries if e["run_id"] == run_id]
    return found[0] if found else None


def as_departure(e: dict) -> dict:
    """A departed replay as the report shows it, from its ledger entry alone: the summary's shape, and the label."""
    return {"id": e["run_id"], "freeze_commit": e["freeze_commit"], "recorded": True,
            "recommendations": e["recommendations"],
            "summary": {"mape": {"mean": e["mape"]}, "coverage": {"mean": e["coverage"]},
                        "recommendations": recommendation_counts(e["recommendations"])}}
