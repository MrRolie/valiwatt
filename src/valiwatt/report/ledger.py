"""The replay ledger, `docs/replay-ledger.json`: one entry per finished replay of a year a freeze replays (v1: 2024;
v2: 2024 and 2025), committed with each report.

The MLflow store (`mlruns/`) stays out of git, so a fresh clone has none of the earlier replays. The ledger keeps what
the report needs of each: its run ID, freeze tag and commit, rules and code hashes, start and end times, its status
tag, MAPE, coverage, recommendations, and whether it ran as a departure and from which replays. v2's entries
(`rules-frozen-v2`) add five fields after v1's fourteen, so v1's stay a prefix (docs/plan-v2.md, section 10.5): the
evaluation label its run was tagged with when it opened, the production model's hash, the two verdicts of section 8,
one hash of every artifact the replay was judged by, and the standing its 2025 gate logged ("" for 2024). v1's
entries keep their fourteen fields and are never touched.

- `sync` reads every finished replay of those years in the store (deleted runs included, as the moved-tag check
  counts them) and appends the ones the ledger lacks. An entry already present is never rewritten: a store run that
  disagrees with its entry, in any field of its version's shape, is refused, and an entry the store no longer holds
  (a fresh clone) is kept as it is. Every v2 entry's label is checked against the 2025 starts the store holds,
  unfinished ones included (`starts`). It runs from `valiwatt ledger` and `valiwatt report`, never during the replay
  itself: every run records whether the tree was dirty, and a write in the middle of the chain would mark the later
  runs dirty.
- Every entry is checked field by field against a fixed shape (hashes, run IDs, dates, numbers, the decision table's
  recommendations, the decision rule's verdicts, the labels and standings), so no free text reaches the file, and the
  whole text passes the public-safety scan before it is written (no local path, no private name). Either failing
  refuses the write.
- `lookup` serves the report: a replay absent from the store is shown from its entry, labelled as recorded in the
  ledger and not re-run here. `of_record` walks the 2025 starts to the test of record (plan section 11.3.5).
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

from valiwatt.config import FREEZE_TAG, FREEZES, HELD_OUT_YEAR
from valiwatt.paths import REPO_ROOT

LEDGER = REPO_ROOT / "docs" / "replay-ledger.json"
YEAR = 2024
FIELDS = ("run_id", "year", "freeze_tag", "freeze_commit", "rules_sha256", "code_sha256", "started_utc", "ended_utc",
          "status", "mape", "coverage", "recommendations", "departure", "departure_from")
# v2's entries: v1's fourteen fields, then five (docs/plan-v2.md, section 10.5), appended so v1's stay a prefix.
V2_FIELDS = FIELDS + ("evaluation", "model_sha256", "verdict", "frozen_sha256", "standing")
# The years each freeze replays, read from the configuration: never a second list.
YEARS = {tag: tuple(f["years"]) for tag, f in FREEZES.items()}
# The shape of each freeze's entries. A freeze the configuration lists without a shape here is refused (a test holds
# the two to the same tags), so a new freeze never borrows another's shape by default.
V1_TAG = "rules-frozen"
SHAPES = {V1_TAG: FIELDS, FREEZE_TAG: V2_FIELDS}
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


def fields_for(tag: str) -> tuple:
    """The fields, in order, of an entry under freeze `tag`: v1's fourteen for `rules-frozen`, v2's nineteen for
    `rules-frozen-v2`. A tag the configuration does not list, or lists without a shape, is refused."""
    if not isinstance(tag, str) or tag not in FREEZES or tag not in SHAPES:
        raise LedgerError(f"a ledger entry's freeze_tag must name a freeze tag the configuration lists "
                          f"({sorted(FREEZES)}), not {tag!r}")
    return SHAPES[tag]


def is_v2(tag) -> bool:
    """Whether entries under `tag` have v2's shape."""
    return isinstance(tag, str) and SHAPES.get(tag) == V2_FIELDS


def entry(run, results: dict) -> dict:
    """The ledger entry of a finished replay run, from its tags and its `results/replay.json`. Its shape follows the
    freeze its results name (`freeze_tag`; a replay without one is v1's): v2's entry copies the evaluation label from
    the run tag `evaluation` (the label's one home, set when the run opened), the production model's hash from the
    tag `coefficient_sha256`, the two verdicts, one digest of the `frozen_sha256` it logged
    (monitor.run.frozen_digest) and the standing from the tag `standing` ("" when absent, as for 2024)."""
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
    if is_v2(results.get("freeze_tag") or V1_TAG):
        if tags.get("freeze_tag") != results["freeze_tag"]:
            raise LedgerError(f"replay {run.info.run_id}: its results and its tags name different freeze tags")
        from valiwatt.monitor.run import frozen_digest

        verdict = results.get("verdict")
        if not isinstance(verdict, dict):
            raise LedgerError(f"replay {run.info.run_id}: its results hold no verdict")
        if not isinstance(results.get("frozen_sha256"), dict):
            raise LedgerError(f"replay {run.info.run_id}: its results hold no frozen_sha256 of what judged it")
        e |= {"evaluation": tags.get("evaluation", ""), "model_sha256": tags.get("coefficient_sha256", ""),
              "verdict": {"shipped": verdict.get("shipped"), "family": verdict.get("family")},
              "frozen_sha256": frozen_digest(results["frozen_sha256"]), "standing": tags.get("standing", "")}
    check_entry(e)
    return e


def check_entry(e) -> None:
    """The fixed shape of an entry; anything else is refused, so nothing but these fields reaches the file. v1's
    entries get exactly v1's checks; v2's add their five fields' (plan section 10.5): the year one its freeze replays,
    both hashes hex64, both verdicts words of the decision rule, the standing empty for 2024 and one of the two
    standings for 2025, and the label `seen_by_developers` for 2024, for 2025 `held_out` only with standing clean and
    `post_hoc` only with standing post hoc, else `seen_by_developers`."""
    if not isinstance(e, dict):
        raise LedgerError(f"a ledger entry must be an object, not {type(e).__name__}")
    fields = fields_for(e.get("freeze_tag"))
    if tuple(e) != fields:
        raise LedgerError(f"a ledger entry under {e.get('freeze_tag')!r} must hold exactly {list(fields)} in that "
                          f"order, not {list(e)}")
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
    from valiwatt.monitor.rules import INVESTIGATE, RECALIBRATE, RETRAIN

    recs = e["recommendations"]
    if not isinstance(recs, list) or not all(
            isinstance(r, dict) and list(r) == ["week_start", "recommendation"] and isinstance(r["week_start"], str)
            and DAY.fullmatch(r["week_start"]) and r["recommendation"] in (RETRAIN, RECALIBRATE, INVESTIGATE)
            for r in recs):
        raise bad("recommendations")
    if fields == V2_FIELDS:
        _check_v2_fields(e, bad, text)


def _check_v2_fields(e: dict, bad, text) -> None:
    from valiwatt.forecast.evaluate import STANDINGS, VERDICTS

    text("model_sha256", SHA256)
    text("frozen_sha256", SHA256)
    v = e["verdict"]
    if not isinstance(v, dict) or list(v) != ["shipped", "family"] or not all(x in VERDICTS for x in v.values()):
        raise bad("verdict")
    held_out = e["year"] == HELD_OUT_YEAR
    if not isinstance(e["standing"], str) or (e["standing"] not in STANDINGS if held_out else e["standing"] != ""):
        raise bad("standing")
    # Each label a year's entry may carry, with the standing it needs (None: either).
    allowed = ({"held_out": "clean", "post_hoc": "post_hoc", "seen_by_developers": None} if held_out
               else {"seen_by_developers": None})
    label = e["evaluation"]
    if not isinstance(label, str) or label not in allowed or allowed[label] not in (None, e["standing"]):
        raise bad("evaluation")


def _order(e: dict) -> tuple:
    return e["started_utc"], e["run_id"]


def check(entries, starts: list[dict] | None = None) -> list[dict]:
    """A whole ledger: every entry well formed, each run once, sorted by start time; and, for v2's entries (plan
    sections 10.3 and 10.5): every entry that is not a departure has the freeze commit of the first v2 2024 entry by
    start; no v2 2025 entry starts before it; and each label is the one `evaluate.evaluation_label` gives it (the
    label's one rule): `seen_by_developers` for 2024, and for 2025 the label of a start after the 2025 starts before
    it, under its own gate's record.

    starts: the 2025 starts `starts()` gives (the store's, unfinished ones included, each with its gate's record, and
    the ledger's), which `sync` and the report pass. None reads the ledger's own 2025 entries as the starts, all
    finished. A start without a gate's record (starts None, or a ledger entry the store lacks) is checked with its
    entry's own standing in the record's place, so the test of record's label is `post_hoc` exactly when its standing
    is. A gate's record that logged another standing than its entry's is refused. v1's one-argument calls keep their
    meaning."""
    if not isinstance(entries, list):
        raise LedgerError("the ledger's `replays` must be a list")
    for e in entries:
        check_entry(e)
    ids = [e["run_id"] for e in entries]
    if len(set(ids)) != len(ids):
        raise LedgerError(f"the ledger holds a run twice: {sorted({i for i in ids if ids.count(i) > 1})}")
    if entries != sorted(entries, key=_order):
        raise LedgerError("the ledger's entries are not sorted by start time")
    for tag in sorted({e["freeze_tag"] for e in entries if is_v2(e["freeze_tag"])}):
        _check_v2(tag, [e for e in entries if e["freeze_tag"] == tag], starts)
    return entries


def stand_in(standing: str) -> dict:
    """The gate's record a start without one is checked with: its entry's own standing, no cause, no ruling."""
    return {"causes": [], "ruling": None, "standing": standing}


def _gate_of(e: dict, start: dict | None) -> dict:
    gate = start.get("gate") if start else None
    if gate is None:
        return stand_in(e["standing"])
    if gate.get("standing") != e["standing"]:
        raise LedgerError(f"ledger entry {e['run_id']}: its standing {e['standing']!r} is not the one its gate "
                          f"logged when it started ({gate.get('standing')!r})")
    return gate


def _check_v2(tag: str, entries: list[dict], starts: list[dict] | None) -> None:
    from valiwatt.forecast.evaluate import evaluation_label

    first_year = min(YEARS[tag])
    firsts = [e for e in entries if e["year"] == first_year]
    first = firsts[0] if firsts else None
    for e in entries:
        if first is None or e["started_utc"] < first["started_utc"]:
            raise LedgerError(f"ledger entry {e['run_id']} ({e['year']}) starts before any {tag} replay of "
                              f"{first_year}: v2's first {first_year} replay comes first")
        if not e["departure"] and e["freeze_commit"] != first["freeze_commit"]:
            raise LedgerError(f"ledger entry {e['run_id']} is not a departure, yet its freeze commit "
                              f"{e['freeze_commit'][:12]} is not that of the first {tag} replay of {first_year} "
                              f"({first['freeze_commit'][:12]})")
    if starts is None:
        held = [{"run_id": e["run_id"], "started_utc": e["started_utc"], "finished": True, "gate": None}
                for e in entries if e["year"] == HELD_OUT_YEAR]
    else:
        held = list(starts)
    by_id = {s.get("run_id"): s for s in held}
    for e in entries:
        if e["year"] == HELD_OUT_YEAR:
            earlier = [s for s in held if s.get("started_utc", "") < e["started_utc"]]
            try:
                want = evaluation_label(e["year"], earlier, _gate_of(e, by_id.get(e["run_id"])))
            except ValueError as err:
                raise LedgerError(f"ledger entry {e['run_id']}: its label cannot be checked against the "
                                  f"{HELD_OUT_YEAR} starts before it: {err}") from err
        else:
            want = evaluation_label(e["year"], [])
        if e["evaluation"] != want:
            raise LedgerError(f"ledger entry {e['run_id']} ({e['year']}) is labelled {e['evaluation']!r}, but the "
                              f"starts before it and its gate's record give {want!r}")


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
    """The entry of every finished replay in the store (MLflow FINISHED and status tag `complete`) of a year some
    freeze replays (`YEARS`: 2024 and 2025), deleted runs included. A run whose freeze does not replay its year is
    refused by its entry's check, never dropped."""
    from mlflow.entities import ViewType

    from valiwatt import tracking
    from valiwatt.monitor.run import EXPERIMENT

    c = c or tracking.client()
    exp = c.get_experiment_by_name(EXPERIMENT)
    if exp is None:
        return []
    years = {str(y) for ys in YEARS.values() for y in ys}
    runs = c.search_runs([exp.experiment_id], "tags.kind = 'replay' and attributes.status = 'FINISHED' and "
                                              "tags.status = 'complete'",
                         run_view_type=ViewType.ALL, max_results=50_000)
    return [entry(run, results_of(c, run)) for run in runs if run.data.tags.get("year") in years]


def starts(c=None, year: int = HELD_OUT_YEAR, tag: str = FREEZE_TAG, ledger: dict | None = None) -> list[dict]:
    """Every replay start of `year` under `tag`, whatever its status, deleted runs included, by start time, each with
    its gate's record, and the ledger's entries the store lacks: monitor.run.replay_starts, the one reader of starts,
    never a second. ledger: the parsed ledger ({"replays": [...]}) it merges; None, the committed file."""
    from valiwatt.monitor.run import replay_starts

    if ledger is None:
        return replay_starts(year, tag, c)
    return replay_starts(year, tag, c, ledger)


def of_record(entries: list[dict], starts: list[dict], year: int = HELD_OUT_YEAR, tag: str = FREEZE_TAG) -> dict | None:
    """The test of record of `year` (docs/plan-v2.md, section 11.3.5), from the starts `starts()` gives. Not named
    test_*: pytest would collect it.

    None when nothing started. Otherwise the starts are walked by start time: the first is the test of record; while
    it has not finished, the next start, if any, takes its place, and must have opened under the owner's ruling naming
    it (that start's gate's record holds "f" among its causes and a ruling whose `start` is the record's
    started_utc), else ReportRefused; once the record has finished, every later start is a rerun. Returns:
    "record", the test of record's start; "entry", its ledger entry when it finished, else None; "stopped", every
    start the walk passed over, in order, each with its stage, error and, under "ruling", the ruling that let the next
    one run; "unfinished", every start that did not finish, in order (those passed over, the record when it did not
    finish, and any later start that stopped); "reruns", the ledger entry of every later start that finished. A
    finished start the ledger lacks is refused: the ledger is synced before any render."""
    from valiwatt.report.render import ReportRefused

    ordered = sorted(starts, key=lambda s: (s["started_utc"], s["run_id"]))
    if not ordered:
        return None
    mine = {e["run_id"]: e for e in entries if e["freeze_tag"] == tag and e["year"] == year}

    def entry_of(s):
        e = mine.get(s["run_id"])
        if e is None:
            raise ReportRefused(f"the {year} start {s['run_id']} finished, but the replay ledger holds no entry for "
                                "it: run `valiwatt ledger` first")
        return e

    i, stopped = 0, []
    while not ordered[i]["finished"] and i + 1 < len(ordered):
        record, nxt = ordered[i], ordered[i + 1]
        gate = nxt.get("gate") or {}
        ruling = gate.get("ruling")
        if not ("f" in (gate.get("causes") or []) and isinstance(ruling, dict)
                and ruling.get("start") == record["started_utc"]):
            raise ReportRefused(f"the {year} start {nxt['run_id']} follows the stopped start of "
                                f"{record['started_utc']}, but its gate's record holds no owner's ruling naming that "
                                "start: it cannot be the test of record, and nothing lets it run")
        stopped.append({**record, "ruling": ruling})
        i += 1
    record = ordered[i]
    return {"record": record, "entry": entry_of(record) if record["finished"] else None, "stopped": stopped,
            "unfinished": [s for s in ordered if not s["finished"]],
            "reruns": [entry_of(s) for s in ordered[i + 1:] if s["finished"]]}


def results_of(c, run) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            p = Path(c.download_artifacts(run.info.run_id, "results/replay.json", tmp))
        except Exception as e:  # noqa: BLE001 - any failure to read the run's results refuses the entry
            raise LedgerError(f"replay {run.info.run_id}: its results/replay.json cannot be read ({e})") from e
        return json.loads(p.read_text(encoding="utf-8"))


def disagreement(stored: dict, recorded: dict) -> list[str]:
    """The fields on which a stored run's entry and its recorded one differ: every field of its freeze's shape
    (`fields_for`), v2's five included, and any field the recorded entry holds beyond it."""
    fields = fields_for(stored.get("freeze_tag"))
    return [k for k in fields if stored.get(k) != recorded.get(k)] + [k for k in recorded if k not in fields]


def merge(existing: list[dict], stored: list[dict], starts: list[dict] | None = None) -> list[dict]:
    """The existing entries unchanged, plus the stored runs they lack, sorted by start time. A stored run whose entry
    differs is refused: the ledger is append-only. `starts` goes to `check`."""
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
    return check(sorted(existing + new, key=_order), starts)


def scan(text: str, root: Path = REPO_ROOT) -> None:
    from valiwatt.safety import private_patterns, scan_text

    issues = scan_text("docs/replay-ledger.json", text, private_patterns(root))
    if issues:
        raise LedgerError(f"the ledger's new content fails the public-safety scan, so it is not written: {issues}")


def sync(path: Path = LEDGER, c=None, root: Path = REPO_ROOT) -> list[dict]:
    """Append the store's finished replays the ledger lacks; write only when the content changes. Every v2 label is
    checked against the store's 2025 starts, unfinished ones included, and this ledger's (`starts`)."""
    existing = read(path)
    entries = merge(existing, from_store(c), starts(c, ledger={"replays": existing}))
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
