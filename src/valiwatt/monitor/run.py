"""`valiwatt replay --year Y`: replay a year with its frozen models and log every daily and weekly judgement.

- Y = 2023 is the rehearsal: design years only (records after the end of 2023 are cut before the replay's feeds are
  built), thresholds from validation weeks up to 2022, a side-by-side with the monthly-refitted 2023 folds, and the
  check that the forecast temperatures it issued, and the run that fed each day, are validation's.
- Y = 2024, seen by the developers. It refuses to run unless the rule files and the dependencies are exactly those of
  the freeze tag (config.FREEZE_TAG), the validation and frozen-model runs it reads were made with those same rules,
  and no earlier replay under that tag, of any year the freeze replays, ran under another freeze commit, in the MLflow
  store or in the committed ledger `docs/replay-ledger.json` (a moved tag is a departure, run only with `--departure`
  once it is recorded). The same gate runs inside the replay itself (monitor/replay.py), so no caller reaches 2024
  without it.
- Y = 2025, held out (docs/plan-v2.md, section 11.3): only by its own command, behind one gate with two outcomes
  (`held_out_gate`), reading exactly the runs v2's first 2024 replay read (`frozen_for_2025`).

Every v2 replay opens its tracked run, marked as started, before it reads any input, records each step it reaches and
the class of any error that stops it, and logs no metric or result until every check that can stop it has passed;
its diagnostics (the sensitivity measures) run afterwards in a run of their own. `valiwatt evidence` (`run_evidence`)
produces what the owner rules on, and decides nothing. Every step refuses raw files that differ from the ones the
data-quality run checked.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from valiwatt import paths
from valiwatt.config import DESIGN_LAST, FREEZE_TAG, FREEZES, HELD_OUT_YEAR, THROUGH_DEFAULT, data_last, stations
from valiwatt.forecast.evaluate import (
    SEASONS,
    contrasts,
    errors,
    evaluation_label,
    mean_with_ci,
    paired_difference,
    verdict,
)
from valiwatt.forecast.freeze import load_comparators, load_frozen
from valiwatt.forecast.model import MODELS, PRODUCTION
from valiwatt.forecast.validate import (
    DESIGN_YEARS,
    LineageError,
    load_inputs,
    load_validation,
    validation_results,
)
from valiwatt.monitor import sensitivity
from valiwatt.monitor.drift import evidently_snapshot, normed_wasserstein, seasonal_reference
from valiwatt.monitor.replay import MonitorActedError, ReplayResult, replay
from valiwatt.monitor.rules import (
    MIN_WEEK_HOURS,
    RECALIBRATE,
    RETRAIN,
    _is_rule,
    decide,
    rule_files,
    rules_sha256,
    two_week_rule_alone,
    week_record,
)
from valiwatt.paths import REPO_ROOT
from valiwatt.quality import run as qrun
from valiwatt.quality.checks import jsonable
from valiwatt.sources.nbm import AVAILABILITY_LAG

EXPERIMENT = "valiwatt-monitoring"
PSEUDO_YEAR, REPLAY_YEAR = 2023, 2024
REPLAY_YEARS = (2023, 2024, 2025)
N_BOOT = 1000
# The dependency files the freeze gate compares with the tag's, byte for byte (docs/plan-v2.md, section 10.2).
DEPENDENCIES = ("pyproject.toml", "uv.lock")
# v1's freeze: its departures section is v1's free-form record, read as v1 read it.
V1_TAG = "rules-frozen"
LOCK_NAME = "replay-2025.lock"
# The latest data-quality run that passed, as forecast/validate.py load_inputs finds it.
DQ_PASSED = "tags.kind = 'dq' and tags.gates = 'passed' and attributes.status = 'FINISHED'"
DQ_FINISHED = "tags.kind = 'dq' and attributes.status = 'FINISHED'"
PRED_PRODUCTION = f"pred_{PRODUCTION}"


class FreezeError(RuntimeError):
    pass


class WeatherIdentityError(RuntimeError):
    """The rehearsal issued another forecast temperature, or fed a day from another run, than validation did."""


def _git(*args, root: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root or REPO_ROOT, capture_output=True, text=True, check=False)


def _git_bytes(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, check=False)


def _head() -> str:
    return _git("rev-parse", "HEAD").stdout.strip()


def _resolve(prefix: str) -> str | None:
    """The full commit a prefix names in this history, None when it names none."""
    out = _git("rev-parse", "--verify", "--quiet", f"{prefix}^{{commit}}")
    return out.stdout.strip() or None if out.returncode == 0 else None


def _is_ancestor(a: str, b: str) -> bool:
    """a is b or an ancestor of b."""
    return bool(a) and bool(b) and _git("merge-base", "--is-ancestor", a, b).returncode == 0


def _freeze_tags() -> list[str]:
    return [t for t in _git("tag", "--list", "rules-frozen*").stdout.split() if t]


def check_freeze(tag: str = FREEZE_TAG) -> dict:
    """The rule files must equal the tagged freeze, names and bytes, and the dependency files (DEPENDENCIES) the tag's,
    byte for byte, before a gated replay may run. Refused too: a tag named like a freeze that config.FREEZES does not
    list. The rules hash keeps v1's definition (rules_sha256), so v1's recorded hashes stay comparable."""
    unlisted = sorted(t for t in _freeze_tags() if t not in FREEZES)
    if unlisted or tag not in FREEZES:
        raise FreezeError(f"freeze tags {unlisted or [tag]} are not listed in config.FREEZES: a freeze the "
                          "configuration does not name is refused")
    commit = _git("rev-list", "-n", "1", f"refs/tags/{tag}").stdout.strip()
    if not commit:
        raise FreezeError(f"no `{tag}` tag: freeze the monitoring rules in a named commit first")
    tagged = rules_sha256(commit)
    if tagged != rules_sha256():
        then, now = set(rule_files(commit)), set(rule_files())
        changed = sorted((then ^ now) | {p for p in then & now if _git("diff", "--quiet", commit, "--", p).returncode})
        raise FreezeError(f"rule files differ from `{tag}` ({commit[:12]}): {changed}")
    differ = []
    for p in DEPENDENCIES:
        then = _git_bytes("show", f"{commit}:{p}")
        here = REPO_ROOT / p
        now = here.read_bytes() if here.is_file() else None
        if (then.stdout if then.returncode == 0 else None) != now:
            differ.append(p)
    if differ:
        raise FreezeError(f"dependencies differ from `{tag}` ({commit[:12]}): {differ}")
    return {"freeze_tag": tag, "freeze_commit": commit, "rules_sha256": tagged}


def require_clean_tree() -> None:
    """The working tree holds no uncommitted change, untracked files included (`git status --porcelain` is empty), so
    a run records the commit it ran on. The run store's folder is ignored by git, so a start's lock leaves it clean."""
    status = _git("status", "--porcelain")
    if status.returncode != 0:
        raise FreezeError(f"the tree's state cannot be read: {status.stderr.strip()}")
    if status.stdout.strip():
        raise FreezeError(f"the tree holds {len(status.stdout.splitlines())} uncommitted changes (`git status "
                          "--porcelain`): commit them first, so the run records the code it ran on")


def load_raw(dq):
    """The raw records of the vintage the data-quality run `dq` checked (its param `through`), refused unless they are
    the files it checked. The one loader of raw records: the replays, validation and the frozen-model fits all read
    through it."""
    from valiwatt.quality.run import _verify_raw, eccc_manifests_sha256
    from valiwatt.sources import eccc, hydroquebec

    through = int(dq.data.params.get("through", THROUGH_DEFAULT))
    hq, stations_manifests = _verify_raw() if through == THROUGH_DEFAULT else _verify_raw(through)
    if hq["sha256"] != dq.data.params.get("hq_sha256"):
        raise LineageError(f"the Hydro-Québec file differs from the one data-quality run {dq.info.run_id} checked")
    if eccc_manifests_sha256(stations_manifests) != dq.data.params.get("eccc_manifests_sha256"):
        raise LineageError(f"the ECCC files differ from the ones data-quality run {dq.info.run_id} checked")
    primary = next(s for s in stations() if s.primary)
    if through == THROUGH_DEFAULT:
        return hydroquebec.load_raw(), eccc.load_station(primary)
    return hydroquebec.load_raw(hydroquebec.csv_path(through)), eccc.load_station(primary, through)


def check_lineage(year: int, val, frozen_run, freeze: dict) -> None:
    """The frozen model must come from this validation run, under these rules, for this year."""
    if frozen_run.data.tags.get("validation_run_id") != val.info.run_id:
        raise LineageError(f"frozen model run {frozen_run.info.run_id} was built on another validation run; "
                           f"run `valiwatt freeze --year {year}` again")
    if frozen_run.data.tags.get("rules_sha256") != freeze["rules_sha256"]:
        raise LineageError(f"frozen model run {frozen_run.info.run_id} was made under other rules")
    if frozen_run.data.tags.get("replay_year") != str(year):
        raise LineageError(f"frozen model run {frozen_run.info.run_id} was frozen for another year")


# ------------------------------------------------------------------------------------- the records, scoped by freeze

DEPARTURES_HEADING = FREEZES[FREEZE_TAG]["heading"]
LEDGER = "docs/replay-ledger.json"
# A run a reader could take for a commit, as tests/test_departure_record.py reads the section: 7 or more hexadecimal
# characters with a digit, or 12 or more. A start is named by its start time, in replay_starts' form.
HEX_RUN = re.compile(r"\b(?=[0-9a-f]*[0-9])[0-9a-f]{7,}\b|\b[0-9a-f]{12,}\b")
START_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
RULING = re.compile(r"\d{4}-\d{2}-\d{2}, the owner's ruling on 2025: (clean|post hoc)")


def recorded_departures(agents: str | None = None, tag: str = FREEZE_TAG) -> str:
    """The departures section of AGENTS.md of the freeze `tag` (its heading, config.FREEZES[tag]["heading"], as a whole
    line), up to the next section; "" when no line is that heading."""
    if tag not in FREEZES:
        raise FreezeError(f"no freeze {tag!r} in config.FREEZES")
    text = agents if agents is not None else (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    lines = text.split("\n")
    heading = FREEZES[tag]["heading"]
    if heading not in lines:
        return ""
    return ("\n" + "\n".join(lines[lines.index(heading) + 1:])).split("\n## ", 1)[0]


def section_entries(section: str) -> list[tuple[str | None, str]]:
    """The section cut at each top-level bullet: (its bold head, without a final full stop, or None; its text), as
    tests/test_departure_record.py reads it."""
    out = []
    for part in re.split(r"(?m)^(?=- )", section):
        if part.strip():
            m = re.match(r"- \*\*(.+?)\*\*", part, re.S)
            out.append((" ".join(m.group(1).split()).removesuffix(".") if m else None, part))
    return out


def read_ledger(ledger: dict | None = None) -> list[dict]:
    """The committed ledger's entries. ledger: the parsed file; None reads `docs/replay-ledger.json` (absent: no
    entries). A ledger that cannot be read, or an entry without a freeze commit or a run ID, is refused: an
    unreadable record of earlier replays is not an empty one."""
    if ledger is None:
        path = REPO_ROOT / LEDGER
        if not path.exists():
            return []
        try:
            ledger = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise FreezeError(f"{LEDGER} cannot be read: {e}") from e
    if not isinstance(ledger, dict) or not isinstance(ledger.get("replays", []), list):
        raise FreezeError(f"{LEDGER} holds no list of replays")
    entries = ledger.get("replays", [])
    for e in entries:
        if not isinstance(e, dict) or not e.get("freeze_commit") or not e.get("run_id"):
            raise FreezeError(f"{LEDGER}: an entry without a freeze commit or a run ID: {e!r}")
    return entries


def _tag_of(tags_or_entry: dict) -> str:
    """The freeze tag a stored run or a ledger entry ran under: none (or empty) counts as v1's."""
    return tags_or_entry.get("freeze_tag") or V1_TAG


def ledger_freeze_commits(ledger: dict | None = None, tag: str = FREEZE_TAG) -> dict[str, list[str]]:
    """The freeze commit of every replay the committed ledger lists under `tag`, of every year that freeze replays
    (config.FREEZES[tag]["years"]), with their run IDs. The ledger travels with the repository, so a fresh clone with an
    empty MLflow store still sees the replays that ran before. An entry that does not name its year counts: counting
    one too many can only refuse."""
    years = FREEZES[tag]["years"]
    out: dict[str, list[str]] = {}
    for e in read_ledger(ledger):
        if _tag_of(e) == tag and int(e.get("year", years[0])) in years:
            out.setdefault(e["freeze_commit"], []).append(str(e["run_id"]))
    return out


def earlier_freeze_commits(tag: str = FREEZE_TAG, client=None) -> dict[str, list[str]]:
    """The freeze commit of every replay in the store under `tag` (a run without the run tag freeze_tag counts as v1's,
    `rules-frozen`), of every year the freeze replays, whatever its status (an unfinished start counts), deleted runs
    included, with their run IDs."""
    out: dict[str, list[str]] = {}
    for r in _replay_runs(client):
        if _tag_of(r.data.tags) == tag and r.data.tags.get("year") in {str(y) for y in FREEZES[tag]["years"]}:
            out.setdefault(r.data.tags.get("freeze_commit", ""), []).append(r.info.run_id)
    return out


def _replay_runs(client=None, kind: str = "replay", view: str = "ALL") -> list:
    from mlflow.entities import ViewType

    from valiwatt import tracking

    c = client or tracking.client()
    exp = c.get_experiment_by_name(EXPERIMENT)
    if exp is None:
        return []
    return c.search_runs([exp.experiment_id], f"tags.kind = '{kind}'", run_view_type=getattr(ViewType, view),
                         max_results=50_000)


# A departure entry of a v2 section: its bold head ends "a departure" (AGENTS.md, v2 departures section).
DEPARTURE_HEAD = re.compile(r".*\ba departure")


def _is_change(path: str) -> bool:
    """A path whose change after the freeze is a departure: a rule file (rules._is_rule) or a dependency."""
    return _is_rule(path) or path in DEPENDENCIES


def departure_record(section: str, freezes: list[str] | None = None, *, resolve=None, is_ancestor=None,
                     changes=None) -> tuple[list[dict], list[str]]:
    """v2's departure record read and checked by its one rule (docs/plan-v2.md, section 10.3): the moved-tag check of
    every v2 replay and the 2025 gate (`check_tag_not_moved`), the report (report/lineage.py `check_recorded`) and the
    history test (tests/test_departure_record.py `v2_record_problems`) read the section here.

    Each departure entry (its bold head ending "a departure"), in section order, records one move of the tag: it names,
    by at least 12 characters each, the commit the tag moved from, the commit it moved to, and every commit between
    them (from..to, the second included) that touches a rule file or a dependency, read commit by commit, and no
    other commit. Without `freezes`, an entry's move is read from the commits it names: from, the one every other
    descends from; to, the one that descends from every other; and each entry's move starts where the one before it
    ended. freezes: the tag's successive commits F0 .. Fn, when the caller knows them (the history test reads them from
    the tag's pins): entry i then records the move from F(i-1) to Fi, and every move has its entry.

    resolve(prefix) -> the full commit or None, is_ancestor(a, b), changes(a, b) -> {commit: paths} of a..b: the
    history read (this repository's by default; the report and the tests pass their own).

    Returns (moves, problems): each departure entry's {"head", "text", "named" (the full commits, in the order
    named), "from", "to"} ("from" and "to" None when it records no move), and what is wrong with the record, each a
    sentence; none: every move is recorded completely."""
    resolve, is_ancestor, changes = resolve or _resolve, is_ancestor or _is_ancestor, changes or _changes
    moves, problems = [], []
    for head, text in section_entries(section):
        if head is None or not DEPARTURE_HEAD.fullmatch(head):
            continue
        named: list[str] = []
        for run in HEX_RUN.findall(text):
            full = resolve(run) if 12 <= len(run) <= 40 else None
            if full is None:
                problems.append(f"the departure entry {head!r} names {run}, which is not a commit of this history by "
                                "at least 12 characters")
            elif full not in named:
                named.append(full)
        moves.append({"head": head, "text": text, "named": named, "from": None, "to": None})

    if freezes is not None:
        pairs = list(zip(freezes, freezes[1:]))
        for i in range(max(len(pairs), len(moves))):
            if i >= len(moves):
                problems.append(f"the tag moved from {pairs[i][0][:12]} to {pairs[i][1][:12]}, but no departure "
                                "entry records it")
            elif i >= len(pairs):
                problems.append(f"the departure entry {moves[i]['head']!r} records no move of the tag")
            else:
                moves[i]["from"], moves[i]["to"] = pairs[i]
                problems += _move_problems(moves[i], changes, check_ends=True)
        return moves, problems

    before = None
    for m in moves:
        n = m["named"]
        if len(n) < 2:
            problems.append(f"the departure entry {m['head']!r} names fewer than two commits: it must name the commit "
                            "the tag moved from and the one it moved to")
            before = None
            continue
        first = [c for c in n if all(is_ancestor(c, o) for o in n)]
        last = [c for c in n if all(is_ancestor(o, c) for o in n)]
        if len(first) != 1 or len(last) != 1:
            problems.append(f"the departure entry {m['head']!r} names no single commit before all the others and one "
                            "after them: it does not say which commit the tag moved from and to")
            before = None
            continue
        m["from"], m["to"] = first[0], last[0]
        if before is not None and m["from"] != before:
            problems.append(f"the departure entry {m['head']!r} moves the tag from {m['from'][:12]}, not from "
                            f"{before[:12]}, the commit the departure entry before it moved the tag to")
        problems += _move_problems(m, changes, check_ends=False)
        before = m["to"]
    return moves, problems


def _move_problems(m: dict, changes, check_ends: bool) -> list[str]:
    """One departure entry against its move: the two freeze commits named (`check_ends`: when the move was given, not
    read from the entry), every rule or dependency commit of from..to named, and nothing else."""
    out, named, frm, to = [], set(m["named"]), m["from"], m["to"]
    if check_ends:
        for what, c in (("moved from", frm), ("moved to", to)):
            if c not in named:
                out.append(f"the departure entry {m['head']!r} does not name the commit the tag {what}, {c[:12]}")
    made = {c for c, paths in changes(frm, to).items() if any(_is_change(x) for x in paths)}
    for c in sorted(made - named):
        out.append(f"the departure entry {m['head']!r} does not name {c[:12]}, which changed a rule file or a "
                   "dependency in its move")
    for c in sorted(named - made - {frm, to}):
        out.append(f"the departure entry {m['head']!r} names {c[:12]}, neither a change of its move nor a commit the "
                   "tag moved from or to")
    return out


def check_tag_not_moved(freeze: dict, earlier: dict[str, list[str]], departure: bool,
                        recorded: str | None = None, ledger: dict | None = None) -> list[str]:
    """An earlier replay under the freeze's tag (freeze["freeze_tag"]), of any year that freeze replays, ran under
    another freeze commit: the tag moved, which is a departure. It runs only with `departure` AND the move recorded in
    the freeze's departures section of AGENTS.md: for v1, the new freeze commit named in it; for v2, the complete record
    of every move (`departure_record`: each departure entry names the commit the tag moved from, the one it moved to
    and every rule or dependency commit between them), its last move to the new freeze commit, and every earlier
    replay's freeze commit one the record moved the tag from or to (a ruling entry naming a commit records no move).
    Returns the earlier run IDs.

    earlier: the freeze commits of the replays in the MLflow store (earlier_freeze_commits of that tag); the committed
    ledger's are added to them (ledger_freeze_commits; `ledger` None reads the file)."""
    tag = freeze["freeze_tag"]
    merged = {c: list(ids) for c, ids in earlier.items()}
    for c, ids in ledger_freeze_commits(ledger, tag).items():
        merged[c] = sorted(set(merged.get(c, [])) | set(ids))
    moved = {c: ids for c, ids in merged.items() if c != freeze["freeze_commit"]}
    if not moved:
        return []
    if not departure:
        raise FreezeError(f"earlier replays under `{tag}` ran under freeze commits "
                          f"{[c[:12] or '(none)' for c in moved]}, not {freeze['freeze_commit'][:12]}: moving the tag "
                          "is a departure; record it in AGENTS.md and rerun with --departure")
    section = recorded_departures(tag=tag) if recorded is None else recorded
    if tag == V1_TAG:
        if freeze["freeze_commit"][:12] not in section:
            raise FreezeError(f"--departure given, but AGENTS.md's departures section does not name commit "
                              f"{freeze['freeze_commit'][:12]}")
    else:
        moves, problems = departure_record(section)
        if problems:
            raise FreezeError(f"--departure given, but AGENTS.md's `{tag}` departures section is not a complete record "
                              "of the tag's moves: " + "; ".join(problems))
        if not moves or moves[-1]["to"] != freeze["freeze_commit"]:
            raise FreezeError(f"--departure given, but AGENTS.md's `{tag}` departures section does not name commit "
                              f"{freeze['freeze_commit'][:12]} as the commit the tag moved to, in its last entry whose "
                              "bold head ends \"a departure\"")
        recorded_freezes = {moves[0]["from"], *(m["to"] for m in moves)}
        unrecorded = sorted(c for c in moved if c not in recorded_freezes)
        if unrecorded:
            raise FreezeError(f"--departure given, but earlier replays under `{tag}` ran under freeze commits "
                              f"{[c[:12] or '(none)' for c in unrecorded]}, which no departure entry of AGENTS.md's "
                              f"`{tag}` section names as a commit the tag moved from or to")
    return sorted({i for ids in moved.values() for i in ids})


def _utc(ms: int | None) -> str:
    """A run's MLflow time in the ledger's form, to the millisecond (YYYY-MM-DDTHH:MM:SS.mmmZ): the ruling entry, the
    gate's match, evaluation_label and the ledger compare it character for character."""
    if ms is None:
        return ""
    return datetime.fromtimestamp(ms // 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def _artifact_paths(c, run_id: str, path: str | None = None) -> list[str]:
    out = []
    for a in c.list_artifacts(run_id, path):
        out += _artifact_paths(c, run_id, a.path) if a.is_dir else [a.path]
    return out


GATE_RECORD = "results/held_out_gate.json"


def _load_json_artifact(c, run_id: str, path: str):
    with tempfile.TemporaryDirectory() as tmp:
        return json.loads(Path(c.download_artifacts(run_id, path, tmp)).read_text(encoding="utf-8"))


def replay_starts(year: int, tag: str = FREEZE_TAG, client=None, ledger: dict | None = None) -> list[dict]:
    """The one reader of replay starts (report/ledger.py's `starts` calls it): every run of kind replay of `year` under
    `tag` in the store, whatever its status, deleted included, plus every ledger entry of that year and tag the store
    lacks, by start time. Each start: run_id, started_utc (MLflow's start time in the ledger's form; a ledger entry's
    own), freeze_commit, git_commit ("" for a ledger entry the store lacks: its code commit is unknown), standing (the
    run tag, "" when absent), finished (MLflow FINISHED and status tag complete, or a ledger entry), running (MLflow
    RUNNING), stage and error (the run tags, "" when absent), gate (the run's results/held_out_gate.json, None when the
    store lacks the run or the file) and holds_results (the run holds a metric, or an artifact other than
    results/held_out_gate.json: a result of its year anyone opening the store could read; true for a ledger entry)."""
    from valiwatt import tracking

    c = client or tracking.client()
    out = []
    for r in _replay_runs(c):
        t = r.data.tags
        if t.get("year") != str(year) or _tag_of(t) != tag:
            continue
        artifacts = _artifact_paths(c, r.info.run_id)
        out.append({"run_id": r.info.run_id, "started_utc": _utc(r.info.start_time),
                    "freeze_commit": t.get("freeze_commit", ""), "git_commit": t.get("git_commit", ""),
                    "standing": t.get("standing", ""),
                    "finished": r.info.status == "FINISHED" and t.get("status") == "complete",
                    "running": r.info.status == "RUNNING", "stage": t.get("stage", ""), "error": t.get("error", ""),
                    "gate": _load_json_artifact(c, r.info.run_id, GATE_RECORD) if GATE_RECORD in artifacts else None,
                    "holds_results": bool(r.data.metrics) or any(a != GATE_RECORD for a in artifacts)})
    stored = {s["run_id"] for s in out}
    for e in read_ledger(ledger):
        if _tag_of(e) == tag and int(e.get("year", year)) == year and e["run_id"] not in stored:
            out.append({"run_id": e["run_id"], "started_utc": e.get("started_utc", ""),
                        "freeze_commit": e["freeze_commit"], "git_commit": "", "standing": e.get("standing", ""),
                        "finished": True, "running": False, "stage": "", "error": "", "gate": None,
                        "holds_results": True})
    return sorted(out, key=lambda s: (s["started_utc"], s["run_id"]))


# ------------------------------------------------------------------------------- what judges a v2 replay, and 2025

def frozen_artifacts_sha256(model, comparators: dict, thresholds: dict, cold_c: float,
                            reference: pd.DataFrame) -> dict:
    """The one hashing of what a v2 replay is judged by (plan section 10.5), logged by every v2 replay as
    results/replay.json's `frozen_sha256`: the production model's hash, each comparison model's, the thresholds'
    (sha256 of their canonical JSON: sorted keys, jsonable), cold_c as repr(float), and the forecast-drift reference's
    (sha256 of its day, int64 ns, and forecast, float64, bytes). Not freeze.frozen_sha256, which hashes one frozen
    model."""
    days = pd.DatetimeIndex(pd.to_datetime(reference["day"])).as_unit("ns").asi8
    forecast = np.ascontiguousarray(reference["forecast"].to_numpy(dtype="<f8"))
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(days, dtype="<i8").tobytes() + b"\0" + forecast.tobytes())
    th = json.dumps(jsonable(thresholds), sort_keys=True, ensure_ascii=False).encode()
    return {"production": model.sha256(), "comparators": {k: m.sha256() for k, m in sorted(comparators.items())},
            "thresholds": hashlib.sha256(th).hexdigest(), "cold_c": repr(float(cold_c)), "reference": h.hexdigest()}


def frozen_digest(d: dict) -> str:
    """One hash of everything a v2 replay is judged by: sha256 of frozen_artifacts_sha256()'s dict as canonical JSON
    (sorted keys, no spaces). The ledger's v2 entry holds it as its field `frozen_sha256` (report/ledger.py entry), and
    frozen_for_2025 compares it."""
    return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def forecast_drift_reference(validation_run, year: int) -> pd.DataFrame:
    """The forecast-drift reference of a replayed year, its one loader (2023, 2024 and frozen_for_2025): the validation
    run's frames/oos.parquet read as a projection onto `day` and the production forecast, so no target is read; the
    rows whose day is before <year>-01-01; columns day and forecast."""
    import mlflow

    from valiwatt.tracking import client

    client()
    path = mlflow.artifacts.download_artifacts(run_id=validation_run.info.run_id, artifact_path="frames/oos.parquet")
    oos = pd.read_parquet(path, columns=["day", PRED_PRODUCTION])
    keep = oos["day"] < pd.Timestamp(f"{year}-01-01")
    return pd.DataFrame({"day": oos.loc[keep, "day"], "forecast": oos.loc[keep, PRED_PRODUCTION]})


def first_v2_2024_entry(entries: list[dict]) -> dict:
    """v2's first 2024 replay: the earliest by start of the committed ledger's 2024 entries under the v2 freeze (an
    entry is a finished replay), whose artifacts judge 2025 (plan section 11.3.3)."""
    v2 = [e for e in entries if _tag_of(e) == FREEZE_TAG and int(e.get("year", 0)) == REPLAY_YEAR]
    if not v2:
        raise FreezeError(f"the committed ledger holds no 2024 entry under `{FREEZE_TAG}`: 2025 is judged by the "
                          "artifacts of v2's first 2024 replay, which must have finished and be recorded first")
    first = min(v2, key=lambda e: (e.get("started_utc", ""), e["run_id"]))
    missing = [k for k in ("model_sha256", "frozen_sha256") if not first.get(k)]
    if missing:
        raise FreezeError(f"v2's first 2024 ledger entry {first['run_id']} lacks {missing}")
    return first


def _get_run(c, run_id: str, what: str):
    from mlflow.exceptions import MlflowException

    if not run_id:
        raise FreezeError(f"{what}: no run ID")
    try:
        return c.get_run(run_id)
    except MlflowException as e:
        raise FreezeError(f"{what} {run_id} is not in the run store (a fresh clone, or a store set aside): {e}") from e


def _production_model(frozen_run):
    """The production model of a frozen run, read from that run (never through the registry's latest alias); its file
    must match its own hash (FrozenModel.from_json) and the hash its run recorded."""
    import mlflow

    from valiwatt.tracking import client

    client()
    model = mlflow.pyfunc.load_model(f"runs:/{frozen_run.info.run_id}/model").unwrap_python_model().frozen
    if model.sha256() != frozen_run.data.tags.get("coefficient_sha256"):
        raise FreezeError(f"the production model of frozen run {frozen_run.info.run_id} does not match the hash its "
                          "run recorded")
    return model


def _differences(d: dict, logged: dict | None) -> list[str]:
    """The items of a frozen_artifacts_sha256 dict that differ from the one a run logged."""
    if not isinstance(logged, dict):
        return ["the frozen_sha256 its run logged (absent)"]
    out = [k for k in ("production", "thresholds", "cold_c", "reference") if d.get(k) != logged.get(k)]
    mine, theirs = d.get("comparators", {}), logged.get("comparators") or {}
    out += [f"comparators.{k}" for k in sorted(set(mine) | set(theirs)) if mine.get(k) != theirs.get(k)]
    return out


def frozen_for_2025(first_2024: dict, client=None) -> dict:
    """The artifacts that judge 2025 (plan section 11.3.3), found and checked from the run store's records without
    reading a demand record; the 2025 gate, run_replay(2025) and run_evidence read them through this one function.
    first_2024: v2's first 2024 ledger entry (first_v2_2024_entry); its run must be in the store, and its tags
    frozen_run_id and validation_run_id name the runs read: never load_frozen's latest alias, never the latest
    validation run. The production model and the comparators come from that frozen run; the 2024 thresholds and
    cold_c from that validation run, and the reference by forecast_drift_reference(val, 2024).

    Refused (FreezeError, naming each difference) unless the production model's hash is the entry's model_sha256 and
    frozen_digest of what was loaded is the entry's frozen_sha256: the committed, append-only entry, so a store edit
    that changes an artifact and the hashes its run logged together is still refused. Each item that differs is named
    against the frozen_sha256 dict the first 2024 replay's run logged in results/replay.json."""
    from valiwatt import tracking

    c = client or tracking.client()
    run = _get_run(c, first_2024.get("run_id", ""), "v2's first 2024 replay run")
    frozen_run = _get_run(c, run.data.tags.get("frozen_run_id", ""), "the frozen run of v2's first 2024 replay")
    val = _get_run(c, run.data.tags.get("validation_run_id", ""), "the validation run of v2's first 2024 replay")
    model = _production_model(frozen_run)
    comparators = load_comparators(frozen_run)
    vres = validation_results(val)
    thresholds = vres["thresholds"][str(REPLAY_YEAR)]
    cold_c = float(vres["config"]["cold_c"])
    reference = forecast_drift_reference(val, REPLAY_YEAR)
    d = frozen_artifacts_sha256(model, comparators, thresholds, cold_c, reference)
    problems = []
    if model.sha256() != first_2024.get("model_sha256"):
        problems.append(f"the production model of frozen run {frozen_run.info.run_id} ({model.sha256()[:12]}) is not "
                        f"the one v2's first 2024 ledger entry records ({str(first_2024.get('model_sha256'))[:12]})")
    if frozen_digest(d) != first_2024.get("frozen_sha256"):
        try:
            logged = _load_json_artifact(c, run.info.run_id, "results/replay.json").get("frozen_sha256")
        except Exception:  # noqa: BLE001 - an unreadable record names nothing; the refusal stands on the entry
            logged = None
        named = _differences(d, logged)
        problems.append("the frozen artifacts differ from v2's first 2024 ledger entry's frozen_sha256: "
                        + (f"{named} differ from the hashes run {run.info.run_id} logged" if named else
                           f"no item differs from the hashes run {run.info.run_id} logged, so the run store was "
                           "changed with them: refused against the committed entry"))
    if problems:
        raise FreezeError("; ".join(problems))
    return {"frozen_run_id": frozen_run.info.run_id, "validation_run_id": val.info.run_id, "model": model,
            "comparators": comparators, "thresholds": thresholds, "cold_c": cold_c, "reference": reference,
            "frozen_sha256": d}


def _check_dq_2025(c):
    """The 2025 gate's (d) and (e): the latest FINISHED data-quality run, whatever its gates, must have passed them,
    reach the end of 2025 and run under the current rules; and the raw files must agree with it by hash only (each
    file against its manifest, then the manifests' hashes against the run's params), no record parsed."""
    from valiwatt.quality.run import EXPERIMENT as DQ_EXPERIMENT
    from valiwatt.sources import nbm
    from valiwatt.sources.http import FetchRefused
    from valiwatt.tracking import latest_run

    try:
        dq = latest_run(DQ_EXPERIMENT, DQ_FINISHED)
    except LookupError as e:
        raise FreezeError(f"no finished data-quality run: {e}") from e
    p, t = dq.data.params, dq.data.tags
    problems = []
    if t.get("gates") != "passed":
        problems.append(f"its gates are {t.get('gates')!r}, not passed")
    if p.get("through") != str(HELD_OUT_YEAR):
        problems.append(f"its window ends with {p.get('through')}, not {HELD_OUT_YEAR}")
    if p.get("window_last_utc") != data_last(HELD_OUT_YEAR).isoformat():
        problems.append(f"its last hour is {p.get('window_last_utc')}, not {data_last(HELD_OUT_YEAR).isoformat()}")
    if t.get("rules_sha256") != rules_sha256():
        problems.append("it ran under other rules")
    if problems:
        raise FreezeError(f"the latest finished data-quality run {dq.info.run_id} is not a passing through-"
                          f"{HELD_OUT_YEAR} run under the current rules: {'; '.join(problems)}")
    try:
        hq, eccc_manifests = qrun._verify_raw(HELD_OUT_YEAR)
        nbm_manifest = qrun._verify_nbm(HELD_OUT_YEAR)
    except FetchRefused as e:
        raise FreezeError(f"a raw file no longer matches its manifest: {e}") from e
    differ = []
    if hq.get("sha256") != p.get("hq_sha256"):
        differ.append("the Hydro-Québec file")
    if qrun.eccc_manifests_sha256(eccc_manifests) != p.get("eccc_manifests_sha256"):
        differ.append("the ECCC files")
    if nbm.manifest_sha256(nbm_manifest) != p.get("nbm_manifest_sha256"):
        differ.append("the NBM files")
    if differ:
        raise FreezeError(f"{differ} differ by hash from those data-quality run {dq.info.run_id} checked")
    return dq


def _commits(a: str, b: str, root: Path | None = None) -> list[tuple[str, list[str], list[str]]]:
    """Each commit of a..b, newest first: the commit, its parents and the paths it touched, a rename read as a
    deletion and an addition (`git log --no-renames --name-only`), commit by commit, so a change since reverted is
    seen. root: the repository (REPO_ROOT by default)."""
    out = _git("log", "--no-renames", "--format=C %H %P", "--name-only", f"{a}..{b}", root=root)
    if out.returncode != 0:
        raise FreezeError(f"the history since {a[:12]} cannot be read: {out.stderr.strip()}")
    commits: list[tuple[str, list[str], list[str]]] = []
    for line in out.stdout.splitlines():
        if line.startswith("C "):
            parts = line[2:].split()
            commits.append((parts[0], parts[1:], []))
        elif line.strip():
            commits[-1][2].append(line.strip())
    return commits


def _changes(a: str, b: str, root: Path | None = None) -> dict[str, list[str]]:
    """Each commit of a..b with the paths it touched (`_commits`)."""
    return {c: files for c, _, files in _commits(a, b, root)}


def _rule_commits(freeze_commit: str) -> list[dict]:
    """Every commit after `freeze_commit` up to HEAD that touches a rule file (rules._is_rule) or a dependency, with
    those paths, oldest first, read commit by commit as tests/test_departure_record.py changes() reads them, so a
    change since reverted counts. Refused: a shallow clone, a HEAD that does not descend from the freeze commit, a merge
    commit (more than one parent). An empty commit lists no path and is no rule commit."""
    if _git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        raise FreezeError("a shallow clone: the commits since the freeze are read one by one from the full history")
    if not _is_ancestor(freeze_commit, "HEAD"):
        raise FreezeError(f"HEAD ({_head()[:12]}) does not descend from the freeze commit {freeze_commit[:12]} of v2's "
                          "earliest 2024 start")
    commits = _commits(freeze_commit, "HEAD")
    merges = [c for c, parents, _ in commits if len(parents) > 1]
    if merges:
        raise FreezeError(f"merge commits {[c[:12] for c in merges]} lie after the freeze commit {freeze_commit[:12]}: "
                          "the changes are read one commit at a time")
    return [{"commit": c, "paths": hit} for c, _, files in reversed(commits)
            if (hit := sorted(p for p in files if _is_change(p)))]


def _rulings(section: str) -> list[dict]:
    """The owner's ruling entries on 2025 of v2's section, in section order: those whose bold head is "<YYYY-MM-DD>,
    the owner's ruling on 2025: clean" or "...: post hoc". Each names one commit by at least 12 characters, resolved in
    this history (an entry naming none, several, or one that cannot be resolved refuses), and at most one start, by
    its start time."""
    out = []
    for head, text in section_entries(section):
        m = RULING.fullmatch(head or "")
        if not m:
            continue
        commits = set()
        for run in HEX_RUN.findall(text):
            full = _resolve(run) if len(run) >= 12 else None
            if full is None:
                raise FreezeError(f"the ruling entry {head!r} names {run}, which is no commit of this history by at "
                                  "least 12 characters (a start is named by its start time, never its run ID)")
            commits.add(full)
        starts = sorted(set(START_TIME.findall(text)))
        if len(commits) != 1 or len(starts) > 1:
            raise FreezeError(f"the ruling entry {head!r} names {len(commits)} commits and {len(starts)} starts: it "
                              "names the one commit it applies to and at most one start")
        out.append({"head": head, "outcome": "clean" if m.group(1) == "clean" else "post_hoc",
                    "commit": commits.pop(), "start": starts[0] if starts else ""})
    return out


def _check_diagnostics_2025(c) -> None:
    """Every 2025 diagnostic run in the store (whatever its status, deleted included) names by replay_run_id a 2025
    replay that is FINISHED, status complete, of its git_commit and freeze_commit (plan section 11.3.5)."""
    from mlflow.exceptions import MlflowException

    bad = []
    for d in _replay_runs(c, kind="diagnostic"):
        t = d.data.tags
        if t.get("year") != str(HELD_OUT_YEAR):
            continue
        try:
            rep = c.get_run(t.get("replay_run_id") or "-")
        except MlflowException:
            rep = None
        ok = rep is not None and rep.data.tags.get("kind") == "replay" and rep.data.tags.get("year") == str(
            HELD_OUT_YEAR) and rep.info.status == "FINISHED" and rep.data.tags.get("status") == "complete" \
            and rep.data.tags.get("git_commit") == t.get("git_commit") \
            and rep.data.tags.get("freeze_commit") == t.get("freeze_commit")
        if not ok:
            bad.append(f"{d.info.run_id} (replay_run_id {t.get('replay_run_id') or 'none'!r})")
    if bad:
        raise FreezeError(f"2025 diagnostic runs {bad} name no finished, complete 2025 replay of their own commits")


def _evidence_run(c, commit: str, dq_run_id: str):
    """The latest finished evidence run made from a clean tree on `dq_run_id`, whose commit is `commit` or a
    descendant of it that is HEAD or an ancestor of HEAD; None when there is none."""
    found = []
    for ev in _replay_runs(c, kind="evidence", view="ACTIVE_ONLY"):
        t = ev.data.tags
        if ev.info.status == "FINISHED" and t.get("status") == "complete" and t.get("git_dirty") == "false" \
                and t.get("dq_run_id") == dq_run_id and _is_ancestor(commit, t.get("git_commit", "")) \
                and _is_ancestor(t.get("git_commit", ""), "HEAD"):
            found.append(ev)
    return max(found, key=lambda r: r.info.start_time) if found else None


def _held_out_2025(freeze: dict) -> dict:
    """The rest of the 2025 gate (plan section 11.3.4), after the clean tree, the freeze and the moved tag: each refusal
    a FreezeError naming the condition and its evidence. Reads no demand record."""
    from valiwatt import tracking

    c = tracking.client()
    starts = replay_starts(HELD_OUT_YEAR, client=c)
    running = [s["run_id"] for s in starts if s["running"]]
    if running:
        raise FreezeError(f"another 2025 replay is RUNNING in the run store: {running}. One start at a time; a run a "
                          "killed process left RUNNING is ended as FAILED with the MLflow client first "
                          "(MlflowClient().set_terminated(run_id, 'FAILED')), and still counts as an unfinished start")
    _check_diagnostics_2025(c)
    entries = read_ledger()
    first_2024 = first_v2_2024_entry(entries)
    _get_run(c, first_2024["run_id"], "v2's first 2024 replay run")
    first_2024_start = replay_starts(REPLAY_YEAR, client=c)[0]
    dq = _check_dq_2025(c)
    chain = frozen_for_2025(first_2024, client=c)
    rule_commits = _rule_commits(first_2024_start["freeze_commit"])
    unknown = [s["run_id"] for s in starts if not s["git_commit"]]
    if unknown:
        raise FreezeError(f"2025 starts {unknown} are in the committed ledger but not in the run store: the code they "
                          "ran on is unknown")
    causes = (["a"] if rule_commits else []) + (["f"] if starts and not any(s["finished"] for s in starts) else [])
    latest = starts[-1] if starts else None
    head = _head()
    applying = [e for e in _rulings(recorded_departures(tag=FREEZE_TAG))
                if _is_ancestor(e["commit"], head)
                and all(_is_ancestor(x["commit"], e["commit"]) for x in rule_commits)
                and all(_is_ancestor(s["git_commit"], e["commit"]) for s in starts)
                and ("f" not in causes or e["start"] == latest["started_utc"])]
    ruling, standing = None, "clean"
    if applying:
        last = applying[-1]
        evidence = _evidence_run(c, last["commit"], dq.info.run_id)
        if evidence is None:
            raise FreezeError(f"the owner's ruling entry {last['head']!r} applies, but no finished evidence run "
                              f"made from a clean tree exists for commit {last['commit'][:12]}, or a descendant up to "
                              f"HEAD, on data-quality run {dq.info.run_id}: run `valiwatt evidence` first")
        ruling = {"commit": last["commit"], "outcome": last["outcome"], "head": last["head"], "start": last["start"],
                  "evidence_run_id": evidence.info.run_id}
        standing = last["outcome"]
    elif causes:
        said = []
        if "a" in causes:
            said.append(f"(a) commits since the freeze commit {first_2024_start['freeze_commit'][:12]} of v2's "
                        "earliest 2024 start touch rule files or dependencies: "
                        + "; ".join(f"{x['commit'][:12]} {x['paths']}" for x in rule_commits))
        if "f" in causes:
            said.append(f"(f) the 2025 test of record did not finish: the latest start, of {latest['started_utc']} "
                        f"(run {latest['run_id']}), stopped at stage {latest['stage'] or 'unknown'!r} with error "
                        f"{latest['error'] or 'none'!r}, and its run "
                        f"{'holds' if latest['holds_results'] else 'holds no'} results ({len(starts)} 2025 starts, "
                        "none finished)")
        raise FreezeError("the 2025 start is refused: " + " ".join(said) + ". `valiwatt evidence` produces the "
                          "evidence; only the owner's dated ruling entry in AGENTS.md's v2 departures section"
                          + (", naming the stopped start by its start time," if "f" in causes else "")
                          + " lifts this refusal")
    return {"first_2024_run_id": first_2024["run_id"], "frozen_run_id": chain["frozen_run_id"],
            "validation_run_id": chain["validation_run_id"], "dq_run_id": dq.info.run_id,
            "frozen_sha256": chain["frozen_sha256"], "freeze_commit": freeze["freeze_commit"], "head_commit": head,
            "rule_commits": rule_commits, "causes": causes, "ruling": ruling, "standing": standing}


def held_out_gate(year: int, departure: bool = False) -> tuple[dict, list[str], dict]:
    """The gate before any replay that reads past the design window. Returns (freeze, departure_from, held_out):
    departure_from the run IDs of the replays a recorded departure departs from; held_out {} for 2024, and for 2025
    the gate's record.

    Every year: the rule files and the dependencies equal the freeze tag's (check_freeze), and no earlier replay under
    the tag, of any year it replays, ran under another freeze commit unless the departure is requested and recorded
    (check_tag_not_moved). run_replay calls it before its run opens; replay() again for 2024, so no entry point skips
    it.

    2025 (plan section 11.3.4), before any run opens and without reading a demand record: (c) a clean tree, no other
    2025 replay RUNNING, every 2025 diagnostic run naming its finished replay, v2's first 2024 entry in the committed
    ledger and its run in the store; (d) the latest finished data-quality run passed, reaches 2025, under these rules;
    (e) the raw files agree with it by hash; (b) the frozen artifacts (frozen_for_2025); (a, f) the standing: no rule or
    dependency commit since the freeze commit of v2's earliest 2024 start, and no 2025 start that stopped without one
    finishing, unless the owner's ruling entry applies and its evidence run exists. run_replay holds start_lock()
    across this whole gate."""
    if year not in FREEZES[FREEZE_TAG]["years"]:
        raise ValueError(f"the `{FREEZE_TAG}` gate is for {FREEZES[FREEZE_TAG]['years']}, not {year}")
    if year == HELD_OUT_YEAR:
        require_clean_tree()
    freeze = check_freeze()
    departure_from = check_tag_not_moved(freeze, earlier_freeze_commits(freeze["freeze_tag"]), departure)
    if year != HELD_OUT_YEAR:
        return freeze, departure_from, {}
    return freeze, departure_from, _held_out_2025(freeze)


def check_2025_run(run_id: str | None) -> None:
    """replay() reads 2025 only for its started run (plan section 11.3.5): check_freeze(), then the run store holds
    `run_id`, looked up with this process's own client (never mlflow.active_run(), which a forkserver worker does not
    inherit), as a run of year 2025, kind replay or diagnostic, status tag started, MLflow status RUNNING, git_commit
    HEAD and freeze_commit check_freeze()'s; and, for a diagnostic run, its replay_run_id names a 2025 replay run that
    is FINISHED, status complete, of the same git_commit and freeze_commit. The 2025 gate itself is not called here: it
    ran once, before that run opened (called again, its condition f would refuse the open start)."""
    from mlflow.exceptions import MlflowException

    from valiwatt import tracking

    freeze = check_freeze()
    if not run_id:
        raise FreezeError("a replay reads 2025 only for its started 2025 run: no run ID was given")
    c = tracking.client()
    try:
        run = c.get_run(run_id)
    except MlflowException as e:
        raise FreezeError(f"a replay reads 2025 only for its started 2025 run: the run store holds no run {run_id}") \
            from e
    t, head = run.data.tags, _head()
    problems = []
    if t.get("year") != str(HELD_OUT_YEAR):
        problems.append(f"its year is {t.get('year')!r}")
    if t.get("kind") not in ("replay", "diagnostic"):
        problems.append(f"its kind is {t.get('kind')!r}")
    if t.get("status") != "started":
        problems.append(f"its status tag is {t.get('status')!r}, not started")
    if run.info.status != "RUNNING":
        problems.append(f"it is {run.info.status}, not RUNNING")
    if t.get("git_commit") != head:
        problems.append(f"it runs commit {str(t.get('git_commit'))[:12]}, not HEAD {head[:12]}")
    if t.get("freeze_commit") != freeze["freeze_commit"]:
        problems.append(f"its freeze commit is {str(t.get('freeze_commit'))[:12]}, not {freeze['freeze_commit'][:12]}")
    if t.get("kind") == "diagnostic":
        try:
            rep = c.get_run(t.get("replay_run_id") or "-")
        except MlflowException:
            rep = None
        if rep is None or rep.data.tags.get("kind") != "replay" or rep.data.tags.get("year") != str(HELD_OUT_YEAR) \
                or rep.info.status != "FINISHED" or rep.data.tags.get("status") != "complete" \
                or rep.data.tags.get("git_commit") != t.get("git_commit") \
                or rep.data.tags.get("freeze_commit") != t.get("freeze_commit"):
            problems.append(f"its replay_run_id {t.get('replay_run_id') or 'none'!r} names no finished, complete 2025 "
                            "replay of its commits")
    if problems:
        raise FreezeError(f"a replay reads 2025 only for its started 2025 run of this code; run {run_id}: "
                          + "; ".join(problems))


@contextmanager
def start_lock():
    """The reservation of a 2025 start (plan section 11.3.4 (c)): an exclusive, non-blocking lock (fcntl.flock,
    LOCK_EX | LOCK_NB) on paths.MLRUNS / "replay-2025.lock", inside the run store's folder, which git ignores. A caller
    that cannot take it is refused at once and opens no run. The lock dies with its process."""
    paths.MLRUNS.mkdir(parents=True, exist_ok=True)
    fd = os.open(paths.MLRUNS / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise FreezeError(f"another 2025 start is being made (the lock {LOCK_NAME} in the run store is held): "
                              "this caller opens no run") from e
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------------------- the replay's summaries

def summarize(r: ReplayResult, n_boot: int = N_BOOT) -> dict:
    e = errors(r.hourly.assign(fold=r.hourly["day"].dt.to_period("M").astype(str)), "forecast")
    e["covered"] = r.hourly["covered"].reindex(e.index)
    recs = [d["recommendation"] for d in r.decisions if d["recommendation"]]
    return {
        "mape": mean_with_ci(e, "ape", n_boot), "bias": mean_with_ci(e, "rel_bias", n_boot),
        "coverage": mean_with_ci(e.dropna(subset=["covered"]), "covered", n_boot),
        "hours_scored": int(len(e)), "hours_issued": int(r.hourly["forecast"].notna().sum()),
        "days": len(r.days), "days_failed": sum(d["failed"] for d in r.days),
        "weeks": len(r.weeks), "weeks_evaluated": sum(w["evaluated"] for w in r.weeks),
        "recommendations": {k: recs.count(k) for k in sorted(set(recs))},
        "flag_weeks": _flag_weeks(r.weeks),
        "retrain_under_two_week_rule_alone": retrain_under_two_week_rule_alone(r.weeks),
    }


def retrain_under_two_week_rule_alone(weeks: list[dict]) -> list[dict]:
    """Sensitivity, never acted on: the weeks where the plan's first wording of the retrain rule (weekly flags only,
    any mix, two weeks running) would have said retrain, with both weeks' statistics."""
    out = []
    for i, w in enumerate(weeks):
        prev = weeks[i - 1] if i else None
        if two_week_rule_alone(w, prev):
            out.append({"week_start": w["week_start"],
                        "weeks": [{"week_start": x["week_start"], "stats": x["stats"], "flags": x["flags"],
                                   "trimmed": x["trimmed"]} for x in (prev, w)]})
    return out


POWER_BIASES = (-0.08, -0.06, -0.04, -0.02, 0.02, 0.04, 0.06, 0.08)
POWER_HALF_WIDTHS = (0.9, 0.8, 0.7, 0.6, 0.5)
POWER_HORIZON_WEEKS = 8


def weekly_decisions(hourly: pd.DataFrame, r: ReplayResult, thresholds: dict) -> tuple[list[dict], list[dict]]:
    """Week records and decisions recomputed from an hourly frame, with the replay's own days and drift values."""
    weeks, decisions = [], []
    for w0 in r.weeks:
        ws = w0["week_start"]
        part = hourly[(hourly["day"] >= ws) & (hourly["day"] < ws + pd.Timedelta(days=7))]
        days = [d for d in r.days if ws <= d["day"] < ws + pd.Timedelta(days=7)]
        w = week_record(ws, part.dropna(subset=["ape", "covered"]), days, w0["drift"], thresholds)
        decisions.append(decide(w, weeks[-1] if weeks else None, weeks[-2] if len(weeks) > 1 else None))
        weeks.append(w)
    return weeks, decisions


def _season_starts(r: ReplayResult) -> list[pd.Timestamp]:
    starts = []
    for month in (1, 4, 7, 10):
        cands = [w["week_start"] for w in r.weeks if w["evaluated"] and w["week_start"].month == month]
        if cands:
            starts.append(min(cands))
    return starts


def _rescore(h: pd.DataFrame) -> pd.DataFrame:
    ok = h["target"].notna() & h["forecast"].notna()
    h["ape"] = ((h["target"] - h["forecast"]).abs() / h["target"]).where(ok)
    h["rel_bias"] = ((h["target"] - h["forecast"]) / h["target"]).where(ok)
    h["covered"] = ((h["target"] >= h["lower"]) & (h["target"] <= h["upper"])).astype(float).where(ok)
    return h


def detection_power(r: ReplayResult, thresholds: dict, biases=POWER_BIASES, half_widths=POWER_HALF_WIDTHS,
                    horizon: int = POWER_HORIZON_WEEKS) -> dict:
    """How fast the rules catch a sustained change, measured on this year's own hours and never acted on.

    From the first full week of each season, either demand runs a fraction b above the forecast (forecast and
    interval divided by 1 + b; weeks until *retrain*), or the interval's half-width is multiplied by k (weeks until
    *recalibrate*). The weekly rules are run again on the changed hours; None means nothing within `horizon` weeks."""
    out = {"bias": [], "half_width": []}
    for start in _season_starts(r):
        season = SEASONS[(start + pd.Timedelta(days=3)).month]
        late = (r.hourly["day"] >= start).to_numpy()
        for kind, values, target in (("bias", biases, RETRAIN), ("half_width", half_widths, RECALIBRATE)):
            for x in values:
                h = r.hourly.copy()
                if kind == "bias":
                    for col in ("forecast", "lower", "upper"):
                        h.loc[late, col] = h.loc[late, col] / (1 + x)
                else:
                    half = (h["upper"] - h["lower"]) / 2
                    h.loc[late, "lower"] = h.loc[late, "forecast"] - x * half[late]
                    h.loc[late, "upper"] = h.loc[late, "forecast"] + x * half[late]
                _, decisions = weekly_decisions(_rescore(h), r, thresholds)
                hit = [d["week_start"] for d in decisions if d["recommendation"] == target
                       and start <= d["week_start"] < start + pd.Timedelta(weeks=horizon)]
                out[kind].append({"start": start, "season": season, kind: x,
                                  "weeks": int((hit[0] - start).days // 7 + 1) if hit else None})
    return out


def _flag_weeks(weeks: list[dict]) -> dict:
    out = {"mape_high": 0, "bias_high": 0, "bias_low": 0, "coverage_low": 0, "temperature_drift": 0,
           "forecast_drift": 0}
    for w in weeks:
        if w["evaluated"]:
            for k, v in w["flags"].items():
                out[k] += int(v)
        out["temperature_drift"] += int(w["drift_flags"]["temperature"])
        out["forecast_drift"] += int(w["drift_flags"]["forecast"])
    return out


def compare_refitted(r: ReplayResult, oos: pd.DataFrame, intervals: pd.DataFrame, thresholds: dict,
                     reference: pd.DataFrame, year: int, n_boot: int = N_BOOT, pred_col: str = PRED_PRODUCTION) -> dict:
    """The frozen model against validation's monthly-refitted folds on the same year: errors, coverage, alarms. v2: the
    production model's out-of-sample forecasts (`pred_col`) and validation's frames/intervals.parquet, its intervals."""
    ref_year = oos[oos["fold"].str.startswith(str(year))]
    frozen = errors(r.hourly.assign(fold=r.hourly["day"].dt.to_period("M").astype(str)), "forecast")
    refit = errors(ref_year, pred_col)
    refit["covered"] = intervals["covered"].reindex(refit.index)
    common = frozen.index.intersection(refit.index)
    frozen, refit = frozen.loc[common], refit.loc[common]
    frozen["covered"] = r.hourly["covered"].reindex(common)

    weeks, decisions = [], []
    days = {d["day"]: d for d in r.days}
    by_week = {w["week_start"]: w for w in r.weeks}
    for ws in by_week:
        in_week = (refit["day"] >= ws) & (refit["day"] < ws + pd.Timedelta(days=7))
        part = refit[in_week].dropna(subset=["ape", "covered"])
        fc = ref_year.loc[ref_year["day"].ge(ws) & ref_year["day"].lt(ws + pd.Timedelta(days=7)), pred_col]
        wd = [days[d] for d in sorted(days) if ws <= d < ws + pd.Timedelta(days=7)]
        if len(wd) == 7:
            drift = {"temperature": by_week[ws]["drift"]["temperature"],
                     "forecast": normed_wasserstein(fc.to_numpy(), seasonal_reference(reference["forecast"],
                                                                                      reference["day"], ws),
                                                    MIN_WEEK_HOURS)}
        else:  # a partial week: quiet, as in the replay
            drift = {"temperature": float("nan"), "forecast": float("nan")}
        w = week_record(ws, part, wd, drift, thresholds)
        decisions.append(decide(w, weeks[-1] if weeks else None, weeks[-2] if len(weeks) > 1 else None))
        weeks.append(w)
    recs = lambda ds: {k: [d["recommendation"] for d in ds].count(k)  # noqa: E731
                       for k in sorted({d["recommendation"] for d in ds if d["recommendation"]})}
    return {
        "hours": int(len(common)),
        "mape": {"frozen": mean_with_ci(frozen, "ape", n_boot), "refitted": mean_with_ci(refit, "ape", n_boot)},
        "frozen_minus_refitted_ape": paired_difference(frozen, refit, "ape", n_boot),
        "bias": {"frozen": mean_with_ci(frozen, "rel_bias", n_boot),
                 "refitted": mean_with_ci(refit, "rel_bias", n_boot)},
        "coverage": {"frozen": mean_with_ci(frozen.dropna(subset=["covered"]), "covered", n_boot),
                     "refitted": mean_with_ci(refit.dropna(subset=["covered"]), "covered", n_boot)},
        "flag_weeks": {"frozen": _flag_weeks(r.weeks), "refitted": _flag_weeks(weeks)},
        "recommendations": {"frozen": recs(r.decisions), "refitted": recs(decisions)},
        "refitted_decisions": decisions,
        "refitted_retrain_under_two_week_rule_alone": retrain_under_two_week_rule_alone(weeks),
    }


def comparator_summary(hourly: pd.DataFrame, comparators: dict, n_boot: int = N_BOOT) -> dict:
    """Each comparison model's MAPE and interval coverage, with their week-block intervals, on the hours it forecast
    with a usable target, and the number of those hours."""
    base = hourly.assign(fold=hourly["day"].dt.to_period("M").astype(str))
    out = {}
    for key in sorted(comparators):
        e = errors(base, f"pred_{key}")
        inside = (base["target"] >= base[f"lower_{key}"]) & (base["target"] <= base[f"upper_{key}"])
        e["covered"] = inside.astype(float).reindex(e.index)
        out[key] = {"mape": mean_with_ci(e, "ape", n_boot),
                    "coverage": mean_with_ci(e.dropna(subset=["covered"]), "covered", n_boot), "hours": int(len(e))}
    return out


def _why(hourly: pd.DataFrame, key: str) -> pd.Series:
    """Why comparison model `key` has no forecast for each hour ("" where it has one): its temperature input, with the
    day's forecast-record reason when it reads the forecast and no run fed the day, and the demand lags it lacks (an
    unusable or absent demand hour), joined by ", "."""
    _, scenario = MODELS[key]
    temp = f"temp_{scenario}"
    reason = hourly["forecast_reason"].fillna("") if scenario == "forecast" else pd.Series("", index=hourly.index)
    temp_label = (temp + " (" + reason + ")").where(reason != "", temp)
    why = pd.Series("", index=hourly.index)
    for missing, label in ((hourly[temp].isna(), temp_label), (hourly["lag24"].isna(), "lag24"),
                           (hourly["lag168"].isna(), "lag168")):
        label = label if isinstance(label, pd.Series) else pd.Series(label, index=hourly.index)
        why = why.where(~missing, why.where(why == "", why + ", ") + label)
    return why.where(hourly[f"pred_{key}"].isna(), "")


def contrast_frame(hourly: pd.DataFrame, comparators: dict) -> pd.DataFrame:
    """The frame evaluate.contrasts reads, on the replayed year's hours: the target, day, forecast temperature, the
    three forecasts (the production model's is the replay's) and why each lacks a forecast for an hour (plan section
    8.3: the year's five busiest hours carry it)."""
    frame = pd.DataFrame({"target": hourly["target"], "day": hourly["day"], "temp_forecast": hourly["temp_forecast"],
                          PRED_PRODUCTION: hourly["forecast"]}, index=hourly.index)
    frame[f"why_{PRODUCTION}"] = hourly["not_predicted_because"].where(hourly["forecast"].isna(), "")
    for key in sorted(comparators):
        frame[f"pred_{key}"] = hourly[f"pred_{key}"]
        frame[f"why_{key}"] = _why(hourly, key)
    return frame


def forecast_error(hourly: pd.DataFrame) -> dict:
    """The issued forecast temperature minus the ECCC observation at the target hour, on the replayed hours that have
    both: by month and by season, its mean, mean absolute error and hours. Evidence, without a threshold."""
    err = (hourly["temp_forecast"] - hourly["temp_observed"]).dropna()
    day = pd.to_datetime(hourly.loc[err.index, "day"])

    def table(keys) -> dict:
        return {str(k): {"mean_c": float(g.mean()), "mae_c": float(g.abs().mean()), "hours": int(len(g))}
                for k, g in err.groupby(keys)}

    return {"by_month": table(day.dt.to_period("M").astype(str).to_numpy()),
            "by_season": table(day.dt.month.map(SEASONS).to_numpy())}


def weather_identity(hourly: pd.DataFrame, record: pd.DataFrame, oos: pd.DataFrame, validation_record: pd.DataFrame,
                     year: int) -> dict:
    """The rehearsal's input check (plan section 11.1): the forecast temperature it issued equals validation's
    (frames/oos.parquet, temp_forecast) on every hour of `year`, exactly, and each day of `year` was fed by the same
    run at the same rank, or left unfed for the same reason (frames/forecast_record.parquet). Refused
    (WeatherIdentityError) on any difference, before anything is logged."""
    theirs = oos.loc[pd.to_datetime(oos["day"]).dt.year == year, "temp_forecast"]
    ours = hourly.loc[pd.to_datetime(hourly["day"]).dt.year == year, "temp_forecast"]
    hours = theirs.index.union(ours.index)
    a = ours.reindex(hours).to_numpy(dtype=float)
    b = theirs.reindex(hours).to_numpy(dtype=float)
    same = (a == b) | (np.isnan(a) & np.isnan(b))
    both = np.isfinite(a) & np.isfinite(b)
    diff = float(np.max(np.abs(a[both] - b[both]))) if both.any() else 0.0
    vr = validation_record[pd.DatetimeIndex(validation_record.index).year == year]
    rr = record[pd.DatetimeIndex(record.index).year == year]
    days = vr.index.union(rr.index)
    run_diff = 0
    for d in days:
        if d not in vr.index or d not in rr.index:
            run_diff += 1
            continue
        x, y = vr.loc[d], rr.loc[d]
        same_run = (pd.isna(x["runtime"]) and pd.isna(y["runtime"])) or x["runtime"] == y["runtime"]
        same_rank = (pd.isna(x["rank"]) and pd.isna(y["rank"])) or x["rank"] == y["rank"]
        run_diff += int(not (same_run and same_rank and str(x["reason"]) == str(y["reason"])))
    if not same.all() or run_diff:
        raise WeatherIdentityError(f"the {year} rehearsal's issued forecast differs from validation's: "
                                   f"{int((~same).sum())} of {len(hours)} hours (largest difference {diff} °C) and "
                                   f"{run_diff} of {len(days)} days fed by another run or left unfed for another "
                                   "reason: design and replay must read the same input")
    return {"hours": int(len(hours)), "max_abs_diff_c": diff, "days": int(len(days)), "run_or_reason_differences": 0}


def v1_reconciliation(comparators: dict, ledger: dict | None = None) -> dict | None:
    """v1's model in v2's harness against v1's own 2024 record (plan section 11.2): the persistence comparator's 2024
    MAPE and coverage beside those of the latest `rules-frozen` 2024 ledger entry (None when the ledger holds none)."""
    v1 = [e for e in read_ledger(ledger) if _tag_of(e) == V1_TAG and int(e.get("year", REPLAY_YEAR)) == REPLAY_YEAR]
    if not v1:
        return None
    last = max(v1, key=lambda e: (e.get("started_utc", ""), e["run_id"]))
    mape, coverage = comparators["persistence"]["mape"]["mean"], comparators["persistence"]["coverage"]["mean"]
    return {"ledger_run_id": last["run_id"], "ledger_mape": last["mape"], "mape": mape,
            "mape_rel_diff": (mape - last["mape"]) / last["mape"], "ledger_coverage": last["coverage"],
            "coverage": coverage}


def check_unchanged(registered_sha: str, replayed_sha: str, run_sha: str) -> None:
    """After the replay, the registered model must still be the one the replay used and its run recorded."""
    if not (registered_sha == replayed_sha == run_sha):
        raise MonitorActedError("the registered frozen model changed during the replay")


def _evidently_checks(r: ReplayResult, thresholds: dict, folder: Path) -> list[dict]:
    """The drift statistic of every flagged week recomputed by Evidently and compared, its snapshot saved in `folder`
    (a temporary folder: nothing is logged until every check has passed)."""
    checks = []
    for ws, sample in r.drift_samples.items():
        for name, (cur, ref) in sample.items():
            ours = normed_wasserstein(cur, ref)
            theirs, snap = evidently_snapshot(cur, ref, thresholds["drift"][f"{name}_high"], column=name)
            if not np.isclose(ours, theirs, rtol=1e-6):
                raise RuntimeError(f"drift statistic disagrees with Evidently for {name} {ws.date()}: {ours} vs "
                                   f"{theirs}")
            snap.save_html(str(folder / f"evidently-{ws.date()}-{name}.html"))
            checks.append({"week_start": ws, "name": name, "ours": ours, "evidently": theirs})
    return checks


def _longest_lead_h(c, dq, year: int) -> float | None:
    """The replayed year's longest lead of any run's points, as the data-quality run's nbm_runs finding records it."""
    try:
        res = _load_json_artifact(c, dq.info.run_id, "results/dq_results.json")
        value = res["checks"]["nbm_runs"]["metrics"]["longest_lead_h"].get(str(year))
    except Exception:  # noqa: BLE001 - the measure reports a missing lead as missing, never invents one
        return None
    return None if value is None else float(value)


# ----------------------------------------------------------------------------------------------------- the replay

def replay_days(year: int) -> tuple[str, str]:
    """The local standard days a replay of `year` covers: the whole year."""
    return f"{year}-01-01", f"{year}-12-31"


def _locate(year: int) -> dict:
    """The runs a 2023 or 2024 replay will read, found from their records before its run opens, so that it opens with
    their IDs and the frozen production model's hash: the latest data-quality run that passed (as load_inputs finds
    it), the latest complete validation run (as load_validation), and the frozen run the alias replay-<year> names (as
    load_frozen). Inside its run the replay reads them, and refuses unless they are these."""
    from valiwatt.forecast.freeze import MODEL_NAME
    from valiwatt.forecast.validate import EXPERIMENT as VAL_EXPERIMENT
    from valiwatt.quality.run import EXPERIMENT as DQ_EXPERIMENT
    from valiwatt.tracking import client, latest_run

    dq = latest_run(DQ_EXPERIMENT, DQ_PASSED)
    val = latest_run(VAL_EXPERIMENT, "tags.kind = 'validation' and tags.status = 'complete'")
    c = client()
    frozen = c.get_run(c.get_model_version_by_alias(MODEL_NAME, f"replay-{year}").run_id)
    return {"dq_run_id": dq.info.run_id, "validation_run_id": val.info.run_id, "frozen_run_id": frozen.info.run_id,
            "coefficient_sha256": frozen.data.tags.get("coefficient_sha256", "")}


def _same_run(what: str, got: str, located: str) -> None:
    if got != located:
        raise LineageError(f"the {what} read is run {got}, not {located}, the one this replay's run opened with")


def _set(tag: str, value: str) -> None:
    import mlflow

    mlflow.set_tag(tag, value)


def _read_inputs(year: int, freeze: dict, located: dict, held_out: dict) -> dict:
    """The replay's inputs, read inside its open run: 2023 and 2024 as v1 (load_inputs, load_validation,
    load_frozen(year), check_lineage), the comparators from the same frozen run and the reference by
    forecast_drift_reference(val, year); 2025 exactly held_out's runs (frozen_for_2025, the dq run by its ID)."""
    from valiwatt.tracking import client

    if year == HELD_OUT_YEAR:
        c = client()
        dq = _get_run(c, held_out["dq_run_id"], "the gate's data-quality run")
        first_2024 = first_v2_2024_entry(read_ledger())
        if first_2024["run_id"] != held_out["first_2024_run_id"]:
            raise FreezeError(f"v2's first 2024 ledger entry is {first_2024['run_id']}, not the gate's "
                              f"{held_out['first_2024_run_id']}")
        chain = frozen_for_2025(first_2024, client=c)
        if chain["frozen_sha256"] != held_out["frozen_sha256"]:
            raise FreezeError("the artifacts frozen for 2024 read now differ from those the 2025 gate checked: "
                              f"{_differences(chain['frozen_sha256'], held_out['frozen_sha256'])}")
        raw_demand, raw_temp = load_raw(dq)
        return {"dq": dq, "first_2024": first_2024, "model": chain["model"], "comparators": chain["comparators"],
                "thresholds": chain["thresholds"], "cold_c": chain["cold_c"], "reference": chain["reference"],
                "raw_demand": raw_demand, "raw_temp": raw_temp, "runs": qrun.load_runs(dq),
                "data_end": data_last(HELD_OUT_YEAR), "frozen_run": None, "val": None, "oos": None, "vres": None}
    dq, _, _ = load_inputs()
    _same_run("data-quality run", dq.info.run_id, located["dq_run_id"])
    val, oos = load_validation(dq)
    _same_run("validation run", val.info.run_id, located["validation_run_id"])
    vres = validation_results(val)
    frozen, frozen_run = load_frozen(year)
    _same_run("frozen run", frozen_run.info.run_id, located["frozen_run_id"])
    check_lineage(year, val, frozen_run, freeze)
    comparators = load_comparators(frozen_run)
    raw_demand, raw_temp = load_raw(dq)
    runs = qrun.load_runs(dq, years=DESIGN_YEARS) if year == PSEUDO_YEAR else qrun.load_runs(dq)
    return {"dq": dq, "model": frozen, "comparators": comparators, "thresholds": vres["thresholds"][str(year)],
            "cold_c": None, "reference": forecast_drift_reference(val, year), "raw_demand": raw_demand,
            "raw_temp": raw_temp, "runs": runs, "data_end": DESIGN_LAST if year == PSEUDO_YEAR else data_last(year),
            "frozen_run": frozen_run, "val": val, "oos": oos, "vres": vres}


def _never_acts(year: int, inputs: dict, r: ReplayResult, located: dict) -> None:
    """After the replay, the three models' hashes are the ones it used and its run opened with: the production model
    (check_unchanged) and the comparators (load_comparators again; for 2025, frozen_for_2025 again)."""
    if year == HELD_OUT_YEAR:
        chain = frozen_for_2025(inputs["first_2024"])
        again, comparators = chain["model"], chain["comparators"]
        if chain["frozen_sha256"] != frozen_artifacts_sha256(inputs["model"], inputs["comparators"],
                                                              inputs["thresholds"], inputs["cold_c"],
                                                              inputs["reference"]):
            raise MonitorActedError("the artifacts frozen for 2024 changed during the 2025 replay")
    else:
        again, _ = load_frozen(year)
        comparators = load_comparators(inputs["frozen_run"])
    check_unchanged(again.sha256(), r.model_sha256, located["coefficient_sha256"])
    before = {k: m.sha256() for k, m in inputs["comparators"].items()}
    if {k: m.sha256() for k, m in comparators.items()} != before or getattr(r, "comparators_sha256", before) != before:
        raise MonitorActedError("a comparison model changed during the replay")


def _results(year: int, kind: str, r: ReplayResult, inputs: dict, freeze: dict, departure_from: list,
             evaluation: str, n_boot: int) -> dict:
    """Everything results/replay.json holds, computed before any check that can stop the run is passed."""
    import mlflow

    model, thresholds = inputs["model"], inputs["thresholds"]
    results = {"year": year, "kind": kind, "data_end": inputs["data_end"], "clock_end": r.clock_end,
               "summary": summarize(r, n_boot), "days": r.days, "weeks": r.weeks, "decisions": r.decisions,
               "thresholds": thresholds,
               "model": {"sha256": r.model_sha256, "q": model.q, "train_last_day": model.train_last_day,
                         "lag_coefficients": model.lag_coefficients()},
               "power": detection_power(r, thresholds), "departure_from": departure_from, **freeze}
    # cold_c from validation's config (plan section 6.2); for 2025, the one frozen_for_2025 read.
    cold_c = inputs["cold_c"] if inputs["cold_c"] is not None else float(inputs["vres"]["config"]["cold_c"])
    inputs["cold_c"] = cold_c
    results["frozen_sha256"] = frozen_artifacts_sha256(model, inputs["comparators"], thresholds, cold_c,
                                                       inputs["reference"])
    results["comparators"] = comparator_summary(r.hourly, inputs["comparators"], n_boot)
    c = contrasts(contrast_frame(r.hourly, inputs["comparators"]), cold_c, n_boot=n_boot)
    results["contrasts"] = c | {"label": evaluation}
    results["verdict"] = verdict(c) | {"label": evaluation}
    results["forecast_error"] = forecast_error(r.hourly)
    if year == PSEUDO_YEAR:
        from valiwatt.tracking import client

        client()
        val = inputs["val"]
        frames = {name: pd.read_parquet(mlflow.artifacts.download_artifacts(
            run_id=val.info.run_id, artifact_path=f"frames/{name}.parquet"))
            for name in ("intervals", "forecast_record")}
        results["refitted"] = compare_refitted(r, inputs["oos"], frames["intervals"], thresholds, inputs["reference"],
                                               year, n_boot)
        results["weather_identity"] = weather_identity(r.hourly, r.forecast_record, inputs["oos"],
                                                       frames["forecast_record"], year)
    if year == REPLAY_YEAR:
        results["v1_reconciliation"] = v1_reconciliation(results["comparators"])
    return results


def _open(year: int, departure: bool) -> dict:
    """The gate (2024, 2025) and every tag the replay's run carries, computed before it opens. 2025: under the start
    lock, which run_replay holds from here to the opening of the run."""
    if year == PSEUDO_YEAR:
        freeze = {"freeze_tag": "", "freeze_commit": "", "rules_sha256": rules_sha256()}
        departure_from, held_out = [], {}
    else:
        freeze, departure_from, held_out = held_out_gate(year, departure)
    if year == HELD_OUT_YEAR:
        located = {k: held_out[k] for k in ("dq_run_id", "validation_run_id", "frozen_run_id")}
        located["coefficient_sha256"] = held_out["frozen_sha256"]["production"]
        evaluation = evaluation_label(year, replay_starts(year), held_out)
    else:
        located = _locate(year)
        evaluation = evaluation_label(year, [])
    kind = "pseudo_replay" if year == PSEUDO_YEAR else "replay"
    tags = {"kind": kind, "year": str(year), "freeze_tag": freeze["freeze_tag"],
            "freeze_commit": freeze["freeze_commit"], "rules_sha256": freeze["rules_sha256"],
            "coefficient_sha256": located["coefficient_sha256"], "departure": str(bool(departure_from)).lower(),
            "departure_from": ",".join(departure_from), "evaluation": evaluation,
            "dq_run_id": located["dq_run_id"], "frozen_run_id": located["frozen_run_id"],
            "validation_run_id": located["validation_run_id"], "status": "started", "stage": "opened"}
    if year == HELD_OUT_YEAR:
        tags["standing"] = held_out["standing"]
    return {"freeze": freeze, "departure_from": departure_from, "held_out": held_out, "located": located,
            "evaluation": evaluation, "kind": kind, "tags": tags}


def run_replay(year: int, n_boot: int = N_BOOT, departure: bool = False) -> int:
    """One order for 2023, 2024 and 2025 (plan sections 10.5 and 11.3.5): the gate (2024, 2025); the tracked run,
    opened with every tag it will carry (status started, stage opened), and for 2025 the gate's record logged at once
    as results/held_out_gate.json, the gate, the label and this opening all under start_lock(); then, inside the run,
    the inputs read (stage inputs_read), the replay (stage replayed), every check that can stop it (the never-acts
    check, the results and the rehearsal's weather identity, the Evidently cross-check in a temporary folder; stage
    checked), and only then the metrics, frames/hourly.parquet, the Evidently files and, last, results/replay.json
    (stage logged), then status complete. An exception after the run opens leaves it in the store, status started, its
    stage the last it reached and tag error the exception's class name, never its message. Then the diagnostics, in a
    run of their own (`_diagnostics`); one that fails leaves the replay finished, and the command exits non-zero."""
    import mlflow

    from valiwatt.tracking import start_run

    if year not in REPLAY_YEARS:
        raise ValueError(f"replay years are {PSEUDO_YEAR} (rehearsal), {REPLAY_YEAR} and {HELD_OUT_YEAR}")
    first_day, last_day = replay_days(year)
    with ExitStack() as opened:
        with ExitStack() as locked:
            if year == HELD_OUT_YEAR:
                locked.enter_context(start_lock())
            plan = _open(year, departure)
            run = opened.enter_context(start_run(EXPERIMENT, plan["kind"], tags=plan["tags"]))
            if year == HELD_OUT_YEAR:
                mlflow.log_text(json.dumps(jsonable(plan["held_out"]), indent=2, ensure_ascii=False), GATE_RECORD)
        # The lock is released: the run is open, so a later caller's gate sees it RUNNING.
        try:
            inputs = _read_inputs(year, plan["freeze"], plan["located"], plan["held_out"])
            _set("stage", "inputs_read")
            r = replay(inputs["raw_demand"], inputs["raw_temp"], inputs["model"], inputs["thresholds"],
                       inputs["reference"], first_day, last_day, inputs["data_end"], departure=departure,
                       runs=inputs["runs"], comparators=inputs["comparators"],
                       run_id=run.info.run_id if year == HELD_OUT_YEAR else None)
            _set("stage", "replayed")
            _never_acts(year, inputs, r, plan["located"])
            results = _results(year, plan["kind"], r, inputs, plan["freeze"], plan["departure_from"],
                               plan["evaluation"], n_boot)
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                checks = _evidently_checks(r, inputs["thresholds"], tmp)
                _set("stage", "checked")
                s = results["summary"]
                mlflow.log_metrics({"mape": s["mape"]["mean"], "bias": s["bias"]["mean"],
                                    "coverage": s["coverage"]["mean"], "days_failed": s["days_failed"],
                                    **{f"rec.{k}": v for k, v in s["recommendations"].items()},
                                    **{f"mape.{k}": v["mape"]["mean"] for k, v in results["comparators"].items()},
                                    **{f"contrast.{k}": results["contrasts"][k]["difference"]
                                       for k in ("shipped", "family", "input")}})
                r.hourly.to_parquet(tmp / "hourly.parquet")
                mlflow.log_artifact(str(tmp / "hourly.parquet"), artifact_path="frames")
                for p in sorted(tmp.glob("evidently-*.html")):
                    mlflow.log_artifact(str(p), artifact_path="evidently")
            results["evidently_checks"] = checks
            results["run_id"] = run.info.run_id
            mlflow.log_text(json.dumps(jsonable(results), indent=2, ensure_ascii=False), "results/replay.json")
            _set("stage", "logged")
            _set("status", "complete")
        except BaseException as e:
            _set("error", type(e).__name__)
            raise
    rec = s["recommendations"]
    print(f"{plan['kind']} {year} ({plan['evaluation']}): run {run.info.run_id}; MAPE {100 * s['mape']['mean']:.2f} %, "
          f"coverage {100 * s['coverage']['mean']:.1f} %, failed days {s['days_failed']}, recommendations "
          f"{rec or 'none'}; verdicts {results['verdict']['shipped']} (shipped), {results['verdict']['family']} "
          "(family)")
    try:
        _diagnostics(year, run.info.run_id, plan["freeze"], inputs, r, departure)
    except Exception as e:  # noqa: BLE001 - a diagnostic never stops or changes the finished replay
        print(f"diagnostics of replay {run.info.run_id} FAILED ({type(e).__name__}); the replay itself finished")
        return 1
    return 0


def _diagnostics(year: int, replay_run_id: str, freeze: dict, inputs: dict, r: ReplayResult, departure: bool) -> None:
    """The sensitivity measures of the finished replay, in a run of their own (kind diagnostic, tags year,
    replay_run_id, git_commit, freeze_commit, status started then complete), opened only after the replay's run has
    ended complete; their replays of 2025 pass its run ID. results/diagnostics.json: sensitivity,
    sensitivity_seconds, reuse_sensitivity (plan section 9.2, with the replayed year's longest_lead_h)."""
    import mlflow

    from valiwatt.tracking import client, start_run

    args = (inputs["raw_demand"], inputs["raw_temp"], inputs["model"], inputs["thresholds"], inputs["reference"], r,
            inputs["data_end"])
    with start_run(EXPERIMENT, "diagnostic", tags={"kind": "diagnostic", "year": str(year),
                                                   "replay_run_id": replay_run_id,
                                                   "freeze_commit": freeze["freeze_commit"],
                                                   "status": "started"}) as diag:
        try:
            started = time.monotonic()
            sens = sensitivity.measure(*args, departure=departure, runs=inputs["runs"],
                                       forecast_reference=r.temperature_reference, run_id=diag.info.run_id)
            seconds = time.monotonic() - started
            reuse = sensitivity.forecast_run_reuse(*args, inputs["runs"], r.temperature_reference,
                                                   longest_lead_h=_longest_lead_h(client(), inputs["dq"], year),
                                                   departure=departure, run_id=diag.info.run_id)
            mlflow.log_metric("sensitivity_seconds", seconds)
            mlflow.log_text(json.dumps(jsonable({"sensitivity": sens, "sensitivity_seconds": seconds,
                                                 "reuse_sensitivity": reuse}), indent=2, ensure_ascii=False),
                            "results/diagnostics.json")
            _set("status", "complete")
        except BaseException as e:
            _set("error", type(e).__name__)
            raise


# ------------------------------------------------------------------------------------------------- the evidence

def _missing(x) -> bool:
    return np.ndim(x) == 0 and bool(pd.isna(x))


def _same(x, y) -> bool:
    """Two cells equal bit for bit: missing equals missing; floats compared by their bytes."""
    if _missing(x) or _missing(y):
        return _missing(x) and _missing(y)
    if isinstance(x, (float, np.floating)) and isinstance(y, (float, np.floating)):
        return np.float64(x).tobytes() == np.float64(y).tobytes()
    try:
        return bool(x == y)
    except (TypeError, ValueError):
        return False


def compare_hourly(original: pd.DataFrame, rerun: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """Hour by hour, on every column either frame holds, bit for bit (NaN equal to NaN; a column or an hour only one
    holds differs): the summary and every differing cell (utc, column, original, rerun; an absent cell shown as
    "absent", the others as their repr)."""
    hours = original.index.union(rerun.index)
    columns = sorted(set(original.columns) | set(rerun.columns))
    cells, differing, hours_differing = [], {}, set()
    for col in columns:
        a = original[col].reindex(hours).to_numpy(dtype=object) if col in original else None
        b = rerun[col].reindex(hours).to_numpy(dtype=object) if col in rerun else None
        in_a = hours.isin(original.index) & (a is not None)
        in_b = hours.isin(rerun.index) & (b is not None)
        n = 0
        for i, t in enumerate(hours):
            x = a[i] if in_a[i] else None
            y = b[i] if in_b[i] else None
            if in_a[i] != in_b[i] or (in_a[i] and not _same(x, y)):
                n += 1
                hours_differing.add(t)
                cells.append({"utc": t, "column": col, "original": repr(x) if in_a[i] else "absent",
                              "rerun": repr(y) if in_b[i] else "absent"})
        if n:
            differing[col] = n
    frame = pd.DataFrame(cells, columns=["utc", "column", "original", "rerun"])
    return {"hours": int(len(hours)), "columns": int(len(columns)), "hours_differing": int(len(hours_differing)),
            "differing": differing}, frame


def run_evidence(departure: bool = False) -> int:
    """`valiwatt evidence` (plan section 11.3.5): what the owner rules on; it decides nothing, and no gate reads what it
    holds (the 2025 gate checks only that a finished one, made from a clean tree, exists for the ruling's commit, on
    its data-quality run). First a clean tree, so the evidence is of the commit it names. Refused without v2's first
    2024 entry in the committed ledger and its run, with frames/hourly.parquet, in the store, or when the latest
    finished data-quality run is not a passing through-2025 run whose raw files agree with it by hash. v2's 2024
    replay is run again on that newest export, every raw record labelled after the end of 2024 dropped at once and
    counted, with exactly the artifacts frozen for 2024 and the code at HEAD, and compared hour by hour with v2's first
    2024 replay on every column either holds. A run of kind evidence, never replay: no ledger entry, no start, no
    moved-tag count."""
    import mlflow

    from valiwatt.tracking import client, start_run

    require_clean_tree()
    c = client()
    first_2024 = first_v2_2024_entry(read_ledger())
    run0 = _get_run(c, first_2024["run_id"], "v2's first 2024 replay run")
    if "frames/hourly.parquet" not in _artifact_paths(c, run0.info.run_id):
        raise FreezeError(f"v2's first 2024 replay run {run0.info.run_id} holds no frames/hourly.parquet to compare "
                          "with")
    dq = _check_dq_2025(c)
    chain = frozen_for_2025(first_2024, client=c)
    with start_run(EXPERIMENT, "evidence", tags={"kind": "evidence", "first_2024_run_id": first_2024["run_id"],
                                                 "dq_run_id": dq.info.run_id, "status": "started"}) as run:
        try:
            raw_demand, raw_temp = load_raw(dq)
            runs = qrun.load_runs(dq)
            end = data_last(REPLAY_YEAR)
            late_d, late_t = (raw_demand["ts_utc"] > end).to_numpy(), (raw_temp["ts_utc"] > end).to_numpy()
            raw_demand, raw_temp = raw_demand[~late_d].reset_index(drop=True), raw_temp[~late_t].reset_index(drop=True)
            available = runs["available_utc"] if "available_utc" in runs else runs["runtime"] + AVAILABILITY_LAG
            late_r = (available > end).to_numpy()
            runs = runs[~late_r].reset_index(drop=True)
            mlflow.log_params({"records_after_2024_dropped": int(late_d.sum() + late_t.sum()),
                               "forecast_points_after_2024_dropped": int(late_r.sum()),
                               "first_2024_run_id": first_2024["run_id"]})
            first_day, last_day = replay_days(REPLAY_YEAR)
            r = replay(raw_demand, raw_temp, chain["model"], chain["thresholds"], chain["reference"], first_day,
                       last_day, end, departure=departure, runs=runs, comparators=chain["comparators"])
            with tempfile.TemporaryDirectory() as tmp:
                original = pd.read_parquet(c.download_artifacts(run0.info.run_id, "frames/hourly.parquet", tmp))
            summary, cells = compare_hourly(original, r.hourly)
            summary["revised_records"] = (int(dq.data.params["hq_revised_records"])
                                          if "hq_revised_records" in dq.data.params else None)
            with tempfile.TemporaryDirectory() as tmp:
                cells.assign(utc=cells["utc"].astype(str)).to_parquet(Path(tmp) / "differences.parquet")
                mlflow.log_artifact(str(Path(tmp) / "differences.parquet"), artifact_path="frames")
            mlflow.log_text(json.dumps(jsonable(summary), indent=2, ensure_ascii=False), "results/evidence.json")
            _set("status", "complete")
        except BaseException as e:
            _set("error", type(e).__name__)
            raise
    print(f"evidence run {run.info.run_id}: v2's 2024 replay run again on data-quality run {dq.info.run_id} "
          f"({summary['revised_records']} revised records): {summary['hours_differing']} of {summary['hours']} hours "
          f"differ from replay {first_2024['run_id']}, in columns {sorted(summary['differing']) or 'none'}. It decides "
          "nothing; the owner rules.")
    return 0
