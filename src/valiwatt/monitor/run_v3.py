"""`valiwatt replay-v3 --year Y` and `valiwatt evidence-v3`: v3's replays, issued at noon (docs/plan-v3.md, sections 10,
11 and 12), beside v2's (monitor/run.py), whose helpers they import and never change.

- Y = 2024, the rehearsal (section 11.1): the models frozen for 2024 replay 2024 hour by hour at noon, v2's form at
  midnight beside them, thresholds from 2021-2023, compared with the monthly refits. It reads design data and passes no
  held-out gate, only v3's first-run guard and the freeze in force (records_v3.current_freeze: `v3-pre-freeze` before
  `v3-rules-frozen` exists, under it after, with the moved-tag check). It checks that each day was fed by the run
  validation fed it, or left unfed for the same reason, that every noon input it issued is validation's, and that each
  hour's bin and half-width are those of the January 2024 fold (`rehearsal_identity`), and refuses to log otherwise.
  Every number is labelled seen by the developers.
- Y = 2025, after v2's test (section 11.3): behind v3's 2025 gate (`held_out_2025_v3`): v2's conditions (a) to (f) on
  v3's tag, store, ledger, freeze record and departures section, and three more, (g) v2 first, (h) fetched after the
  freeze and (i) no change after 2025, each refusal naming its condition and its evidence; the runs the freeze record
  names, never the latest ones.
- `valiwatt evidence-v3` (section 11.3 (3)): the 2024 rehearsal named in the freeze record run again on the newest
  export bounded to 2024, with that rehearsal's frozen artifacts, compared hour by hour with it. It decides nothing.

Every v3 replay opens its tracked run (kind `replay_v3`), marked as started, before it reads any input, records each
step it reaches and the class of any error that stops it, and logs no metric or result until every check that can stop
it has passed, as v2's does. The replay function runs behind v3's gate (`gate_v3`), never v2's. No diagnostics run
here (their noon measures are a later slice's).

Every git read goes through `_git`, or, for the paths a history touches, `dated_commits`'s byte read; each reads
paths.REPO_ROOT at call time, replacement objects off (records_v3.git_env). The gates' entry points
(`held_out_gate_v3`, `check_2025_run_v3`, `run_replay_v3`, `run_evidence_v3`) run whole under
records_v3.replacements_off, so the v2 helpers they call read the history as committed too; the 2025 gate refuses
outright while a replace ref or a grafts file exists (records_v3.history_rewrites).
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

from valiwatt import paths, records_v3
from valiwatt.config import HELD_OUT_YEAR as _V2_HELD_OUT_YEAR
from valiwatt.config import V3_DESIGN_LAST, V3_FREEZE_TAG, data_last
from valiwatt.forecast import evaluate as ev
from valiwatt.forecast.issue import NOON
from valiwatt.forecast.model import V3_COMPARATORS, V3_PRODUCTION
from valiwatt.monitor import replay as rp
from valiwatt.monitor import run as mrun
from valiwatt.monitor.run import FreezeError
from valiwatt.quality.checks import jsonable

KIND, EVIDENCE_KIND, DIAGNOSTIC_KIND = records_v3.KIND, "evidence_v3", "diagnostic_v3"
REHEARSAL_YEAR, HELD_OUT_YEAR = 2024, 2025
REPLAY_YEARS = (REHEARSAL_YEAR, HELD_OUT_YEAR)
LOCK_NAME = "replay-2025-v3.lock"
# Condition (g) reads v2's test of record here, never through a fetch (section 11.3 (3)).
V2_REFS = ("refs/heads/v2", "refs/remotes/origin/v2")
V2_LEDGER = "docs/replay-ledger.json"
V2_TAG = "rules-frozen-v2"
PRED_V3 = f"pred_{V3_PRODUCTION}"
NOON_COMPARATORS = (V3_COMPARATORS["terms"], V3_COMPARATORS["family"])  # v2_noon, v1_noon
MIDNIGHT_KEY = V3_COMPARATORS["horizon"]  # v2_midnight
CAUSES = ("a", "f", "g", "h", "i")
N_BOOT = mrun.N_BOOT
assert HELD_OUT_YEAR == _V2_HELD_OUT_YEAR  # one held-out year for v2 and v3


class RehearsalIdentityError(RuntimeError):
    """The 2024 rehearsal fed a day from another run, issued another noon input, or binned an hour with another edge or
    half-width than validation's January 2024 fold (section 11.1)."""


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=paths.REPO_ROOT, capture_output=True, text=True, check=False,
                          env=records_v3.git_env())


def _commit_of(rev: str) -> str | None:
    out = _git("rev-parse", "-q", "--verify", f"{rev}^{{commit}}")
    return out.stdout.strip() or None if out.returncode == 0 else None


def _utc(value) -> pd.Timestamp:
    """A time read from a record (an ISO time, the ledger's ...mmmZ, a committer date) as a UTC timestamp: times are
    compared as times, never as text (D8 of the slice contract)."""
    t = pd.Timestamp(value)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


# ------------------------------------------------------------------------------------------------- the gates

def gate_v3(last_day: str, data_end, departure: bool, run_id: str | None) -> None:
    """replay()'s gate for every v3 replay (monitor/replay.py `gate`), in place of v2's: a replay reading past the end
    of 2024 only for its started 2025 run of this code (`check_2025_run_v3`); any other, v3's first-run guard and the
    freeze in force (records_v3.current_freeze), with the moved-tag check once `v3-rules-frozen` exists."""
    from valiwatt.firstrun import require_first_run

    if rp.reads_2025(last_day, pd.Timestamp(data_end)):
        check_2025_run_v3(run_id)
        return
    require_first_run()
    freeze = records_v3.current_freeze()
    if freeze["freeze_tag"] == V3_FREEZE_TAG:
        _not_moved(freeze, departure)


def _not_moved(freeze: dict, departure: bool) -> list[str]:
    """v2's moved-tag check on v3's tag, store, ledger and departures section."""
    return mrun.check_tag_not_moved(freeze, records_v3.earlier_freeze_commits_v3(), departure,
                                    recorded=records_v3.v3_section(), ledger=records_v3.ledger_v3_dict())


@records_v3.replacements_off()
def check_2025_run_v3(run_id: str | None) -> None:
    """replay() reads 2025 only for its started run (v2's section 11.3.5, monitor/run.py `check_2025_run`, on v3's
    kinds and tag): `v3-rules-frozen` exists and the rule files and dependencies are its; the run store holds `run_id`
    as a run of year 2025, kind replay_v3 or diagnostic_v3, status tag started, MLflow status RUNNING, git_commit HEAD
    and the tag's freeze commit; a replay run holds the gate record it logged at its opening, whose head_commit is
    its git_commit (the code its gate passed on); a diagnostic run names a finished, complete 2025 replay of the same
    commits."""
    from mlflow.exceptions import MlflowException

    from valiwatt import tracking

    freeze = records_v3.current_freeze()
    if freeze["freeze_tag"] != V3_FREEZE_TAG:
        raise FreezeError(f"a v3 replay reads 2025 only under `{V3_FREEZE_TAG}`, which does not exist")
    if not run_id:
        raise FreezeError("a v3 replay reads 2025 only for its started 2025 run: no run ID was given")
    c = tracking.client()
    try:
        run = c.get_run(run_id)
    except MlflowException as e:
        raise FreezeError(f"a v3 replay reads 2025 only for its started 2025 run: the run store holds no run "
                          f"{run_id}") from e
    t, head = run.data.tags, mrun._head()
    problems = []
    if t.get("year") != str(HELD_OUT_YEAR):
        problems.append(f"its year is {t.get('year')!r}")
    if t.get("kind") not in (KIND, DIAGNOSTIC_KIND):
        problems.append(f"its kind is {t.get('kind')!r}")
    if t.get("status") != "started":
        problems.append(f"its status tag is {t.get('status')!r}, not started")
    if run.info.status != "RUNNING":
        problems.append(f"it is {run.info.status}, not RUNNING")
    if t.get("git_commit") != head:
        problems.append(f"it runs commit {str(t.get('git_commit'))[:12]}, not HEAD {head[:12]}")
    if t.get("freeze_commit") != freeze["freeze_commit"]:
        problems.append(f"its freeze commit is {str(t.get('freeze_commit'))[:12]}, not {freeze['freeze_commit'][:12]}")
    if t.get("kind") == KIND:
        try:
            gated = mrun._load_json_artifact(c, run_id, mrun.GATE_RECORD).get("head_commit")
        except Exception:  # noqa: BLE001 - a run without its gate record was never let through a gate
            gated = None
        if gated != t.get("git_commit"):
            problems.append(f"its gate record names the commit {str(gated)[:12]}, not its own" if gated else
                            "it holds no gate record")
    if t.get("kind") == DIAGNOSTIC_KIND and not _names_finished_replay(c, t):
        problems.append(f"its replay_run_id {t.get('replay_run_id') or 'none'!r} names no finished, complete 2025 "
                        "replay of its commits")
    if problems:
        raise FreezeError(f"a v3 replay reads 2025 only for its started 2025 run of this code; run {run_id}: "
                          + "; ".join(problems))


def _names_finished_replay(c, t: dict) -> bool:
    """A diagnostic run's replay_run_id names a FINISHED, complete 2025 replay_v3 run of its git and freeze commits."""
    from mlflow.exceptions import MlflowException

    try:
        rep = c.get_run(t.get("replay_run_id") or "-")
    except MlflowException:
        return False
    r = rep.data.tags
    return (r.get("kind") == KIND and r.get("year") == str(HELD_OUT_YEAR) and rep.info.status == "FINISHED"
            and r.get("status") == "complete" and r.get("git_commit") == t.get("git_commit")
            and r.get("freeze_commit") == t.get("freeze_commit"))


@records_v3.replacements_off()
def held_out_gate_v3(year: int, departure: bool = False) -> tuple[dict, list[str], dict]:
    """The gate before any v3 replay. Returns (freeze, departure_from, record): departure_from the run IDs of the
    replays a recorded departure departs from; record {} for 2024, and for 2025 the gate's record.

    2024: v3's first-run guard; the freeze in force (records_v3.current_freeze); once `v3-rules-frozen` exists, the
    moved-tag check on v3's store, ledger and section. 2025: the first-run guard, (a) no replace ref and no grafts file
    (records_v3.history_rewrites: through either, git shows another history than the one committed, to every walk,
    anchor and ancestry the gate reads), a clean tree, `v3-rules-frozen` existing with the rule files and dependencies
    its own and no unlisted v3 freeze tag (current_freeze), the moved tag, then the rest of the gate
    (`held_out_2025_v3`). Replacement objects are off throughout (records_v3.replacements_off). run_replay_v3 holds
    start_lock_v3() across the whole 2025 gate."""
    from valiwatt.firstrun import require_first_run

    if year not in REPLAY_YEARS:
        raise ValueError(f"v3 replays {list(REPLAY_YEARS)}, not {year}")
    require_first_run()
    if year == HELD_OUT_YEAR:
        rewrites = records_v3.history_rewrites()
        if rewrites:
            raise FreezeError(f"(a) {rewrites}: through them git shows another history than the one committed, so no "
                              "walk, anchor or ancestry the 2025 gate reads could be trusted; v3's 2025 test starts "
                              "only while none exists")
        mrun.require_clean_tree()
    freeze = records_v3.current_freeze()
    if year == HELD_OUT_YEAR and freeze["freeze_tag"] != V3_FREEZE_TAG:
        raise FreezeError(f"(c) no `{V3_FREEZE_TAG}` tag: v3's 2025 test runs on the frozen code only")
    departure_from = _not_moved(freeze, departure) if freeze["freeze_tag"] == V3_FREEZE_TAG else []
    if year != HELD_OUT_YEAR:
        return freeze, departure_from, {}
    return freeze, departure_from, held_out_2025_v3(freeze)


# --------------------------------------------------------------------------- (g), (h) and (i): their readings

def v2_test_of_record() -> dict:
    """Condition (g)'s read (section 11.3 (3)), never a fetch: `docs/replay-ledger.json` at `refs/heads/v2`, or at
    `refs/remotes/origin/v2` when the first is absent, as that commit holds it (never a working copy), checked by v2's
    own ledger check (report/ledger.py `check`).

    Returns {"ref": the ref read or None, "commit": its commit or None, "entries_2025": the 2025 entries under
    `rules-frozen-v2`, "test_of_record": the earliest of them (by start) labelled held_out or post_hoc, v2's test of
    record (v2's section 10.5), or None, "earliest_2025_started_utc": the earliest start of any of them, whatever its
    label, or None, "error": why the ledger could not be read ("" when it was)}."""
    from valiwatt.report import ledger as v2_ledger

    out = {"ref": None, "commit": None, "entries_2025": [], "test_of_record": None,
           "earliest_2025_started_utc": None, "error": ""}
    for ref in V2_REFS:
        commit = _commit_of(ref)
        if commit:
            out["ref"], out["commit"] = ref, commit
            break
    else:
        out["error"] = (f"neither {V2_REFS[0]} nor {V2_REFS[1]} exists: v2's test of record is read from v2's branch, "
                        "and the gate never fetches it")
        return out
    shown = _git("show", f"{out['commit']}:{V2_LEDGER}")
    if shown.returncode != 0:
        out["error"] = f"{V2_LEDGER} cannot be read at {out['ref']} ({out['commit'][:12]})"
        return out
    try:
        data = json.loads(shown.stdout)
        if not isinstance(data, dict) or list(data) != ["replays"]:
            raise ValueError("it must hold one key, `replays`")
        entries = v2_ledger.check(data["replays"])
    except Exception as e:  # noqa: BLE001 - an unreadable ledger is no test of record, and says why
        out["error"] = f"{V2_LEDGER} at {out['ref']} ({out['commit'][:12]}) is not a valid ledger: {e}"
        return out
    held = sorted((e for e in entries if e.get("freeze_tag") == V2_TAG and int(e.get("year", 0)) == HELD_OUT_YEAR),
                  key=lambda e: (e["started_utc"], e["run_id"]))
    out["entries_2025"] = held
    labelled = [e for e in held if e.get("evaluation") in ("held_out", "post_hoc")]
    out["test_of_record"] = labelled[0] if labelled else None
    out["earliest_2025_started_utc"] = held[0]["started_utc"] if held else None
    return out


def time_2025(manifest_retrieved_utc: str, v2: dict, earlier: list[dict] = ()) -> dict:
    """The 2025 time (section 11.3 (3), (h)): the earlier of the retrieval time the copied Hydro-Québec manifest of the
    export reaching 2025 records, and the start of the earliest 2025 entry of v2's ledger at the ref (g) reads, whatever
    its label (v2_test_of_record); and never later than any earlier reading of it v3's store holds (`earlier`, from
    `earlier_2025_times`: {"utc", "source", "run_id"}), so a re-fetch, or a v2 ledger read otherwise, never moves it
    later. Each is parsed to a UTC time; the earliest wins, the first read on a tie. Returns {"utc" (ISO),
    "manifest_retrieved_utc", "v2_earliest_2025_started_utc" (None when v2's ledger holds no 2025 entry), "source"
    ("manifest", "v2_ledger", or the earlier reading's: "earlier_start" or "dq_retrieved"), "source_run_id"
    (the run of an earlier reading, else None)}."""
    v2_first = v2.get("earliest_2025_started_utc")
    readings = [{"utc": manifest_retrieved_utc, "source": "manifest", "run_id": None}]
    if v2_first is not None:
        readings.append({"utc": v2_first, "source": "v2_ledger", "run_id": None})
    t, source, run_id = None, None, None
    for x in [*readings, *earlier]:
        if t is None or _utc(x["utc"]) < t:
            t, source, run_id = _utc(x["utc"]), x["source"], x["run_id"]
    return {"utc": t.isoformat(), "manifest_retrieved_utc": manifest_retrieved_utc,
            "v2_earliest_2025_started_utc": v2_first, "source": source, "source_run_id": run_id}


PAGE = 50_000  # the most runs MLflow gives in one page of a search


def every_run(c, experiment_id: str, filter_string: str, view) -> list:
    """Every run of `experiment_id` matching `filter_string` under `view`, page after page until the store gives no
    next page (MLflow's page token): never one bounded page, so no run is left out however many the store holds."""
    out, token = [], None
    while True:
        page = c.search_runs([experiment_id], filter_string, run_view_type=view, max_results=PAGE, page_token=token)
        out.extend(page)
        token = page.token
        if not token:
            return out


def earlier_2025_times(c, starts: list[dict]) -> list[dict]:
    """Every earlier reading of the 2025 time v3's store holds (section 11.3 (4): a start after a stopped one stays post
    hoc, and nothing seen in 2025 buys a held-out label): the 2025 time each earlier 2025 start's gate recorded
    (results/held_out_gate.json, read by records_v3.replay_starts_v3); and, for every v3 data-quality run through 2025,
    whatever its status, deleted included, the retrieval time of the export it read: each an earlier reading of the
    export's retrieval time. A run's own start time is no reading of it (the plan defines the 2025 time by the
    retrieval time and v2's first 2025 start only). The data-quality runs are read to the last page of the search
    (`every_run`). Each {"utc", "source" ("earlier_start" or "dq_retrieved"),
    "run_id"}. A reading that cannot be parsed as a time is refused (FreezeError, (h)): an unreadable earlier time is
    not a later one."""
    from mlflow.entities import ViewType

    from valiwatt.quality import run_v3 as dq_v3

    out = [{"utc": (s["gate"].get("t2025") or {}).get("utc"), "source": "earlier_start", "run_id": s["run_id"]}
           for s in starts if s.get("gate") is not None]
    exp = c.get_experiment_by_name(dq_v3.EXPERIMENT)
    runs = every_run(c, exp.experiment_id, f"tags.kind = '{dq_v3.KIND}' and params.through = '{HELD_OUT_YEAR}'",
                     ViewType.ALL) if exp else []
    for r in runs:
        out.append({"utc": r.data.params.get("hq_retrieved_utc"), "source": "dq_retrieved", "run_id": r.info.run_id})
    for x in out:
        try:
            if x["utc"] is None or pd.isna(_utc(x["utc"])):
                raise ValueError("no time")
        except (TypeError, ValueError) as e:
            raise FreezeError(f"(h) the 2025 time: run {x['run_id']}'s {x['source']} time {x['utc']!r} cannot be read "
                              f"as a time: {e}") from e
    return out


COMMIT_LINE = "/C "  # a path in a tree never starts with "/", so no path is read as a commit's line


def dated_commits(a: str | None, b: str) -> list[dict]:
    """Each commit of a..b (every ancestor of b when `a` is None), oldest first: {"commit", "parents", "committer_utc"
    (UTC timestamp), "paths" (those it touched, a rename read as a deletion and an addition)}. Read NUL-separated
    (`-z`): each path whole, as the tree names it, whatever it holds (letters outside ASCII, a double quote, a line or
    paragraph separator, a newline), never quoted and never split, so a rule file is a rule file here whatever its
    name. Output not in that form is refused (FreezeError), never passed over. monitor/run.py `_commits` is
    untouched."""
    span = [f"{a}..{b}"] if a else [b]
    out = subprocess.run(["git", "log", "-z", "--no-renames", f"--format={COMMIT_LINE}%H %cI %P", "--name-only",
                          *span], cwd=paths.REPO_ROOT, capture_output=True, check=False, env=records_v3.git_env())
    if out.returncode != 0:
        raise FreezeError(f"the history {' '.join(span)} cannot be read: "
                          f"{out.stderr.decode('utf-8', 'replace').strip()}")
    # Each commit is its line, NUL, then, when it touched paths, a newline and each path followed by NUL.
    commits: list[dict] = []
    first = False
    for token in out.stdout.decode("utf-8", "surrogateescape").split("\0"):
        if token.startswith(COMMIT_LINE):
            commit, date, *parents = token[len(COMMIT_LINE):].split()
            commits.append({"commit": commit, "parents": parents, "committer_utc": _utc(date), "paths": []})
            first = True
        elif token and commits and (not first or token.startswith("\n")):
            commits[-1]["paths"].append(token[1:] if first else token)
            first = False
        elif token:
            raise FreezeError(f"the history {' '.join(span)} cannot be read path by path: {token!r} follows no commit")
    return list(reversed(commits))


def _rule_commits_v3(first_pin: str) -> list[dict]:
    """(a)'s walk (section 11.3 (3)): every commit after the commit `v3-rules-frozen` was first pinned to, up to HEAD,
    that touches a rule file or a dependency, with those paths, oldest first, read commit by commit (`dated_commits`,
    which reads each path whole, as (i) does), so a change since reverted counts. Refused, as v2's (monitor/run.py
    `_rule_commits`): a shallow clone, a HEAD that does not descend from the first pin, a merge commit."""
    if _git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        raise FreezeError("(a) a shallow clone: the commits since the freeze are read one by one from the full history")
    if not records_v3._is_ancestor(first_pin, "HEAD"):
        raise FreezeError(f"(a) HEAD ({mrun._head()[:12]}) does not descend from {first_pin[:12]}, the commit "
                          f"`{V3_FREEZE_TAG}` was first pinned to")
    try:
        commits = dated_commits(first_pin, "HEAD")
    except FreezeError as e:
        raise FreezeError(f"(a) {e}") from e
    merges = [x["commit"] for x in commits if len(x["parents"]) > 1]
    if merges:
        raise FreezeError(f"(a) merge commits {[c[:12] for c in merges]} lie after {first_pin[:12]}, the commit "
                          f"`{V3_FREEZE_TAG}` was first pinned to: the changes are read one commit at a time")
    return [{"commit": x["commit"], "paths": hit} for x in commits
            if (hit := sorted(p for p in x["paths"] if mrun._is_change(p)))]


# ------------------------------------------------------------------------------------------- the standing (D9)

def standing_v3(causes: list[str], rulings: list[dict], *, rule_commits: list[dict], starts: list[dict], v2: dict,
                head: str, evidence, is_ancestor=None, said: dict | None = None) -> dict:
    """Which ruling lifts which refusal, and the start's standing (section 11.3 (4)). Pure apart from `evidence` and
    `is_ancestor`.

    causes: the conditions that fired, among CAUSES; rulings: records_v3.rulings_v3 of v3's section, in section order;
    rule_commits: (a)'s walk; starts: every earlier 2025 start (records_v3.replay_starts_v3); v2: (g)'s read
    (v2_test_of_record); head: the commit the start runs; evidence(commit) -> the run ID of a finished evidence run
    made from a clean tree for that commit or a descendant up to HEAD, on the gate's data-quality run, or None;
    is_ancestor(a, b): a is b or an ancestor of b (this history's by default); said: each firing condition's evidence,
    for the refusal.

    - An entry in v2's form ("clean" or "post hoc") lifts (a) and (f), under v2's rule: its commit is HEAD or an
      ancestor of it, every rule commit of the walk is an ancestor of it, it descends from the code of every earlier
      2025 start, and, when (f) fired, it names the latest start. The last such entry applies.
    - An (h) entry lifts (h) only, an (i) entry (i) only, each under the same rule but naming no start; neither lifts
      the other. They can only say post hoc (records_v3.rulings_v3 refuses a clean one).
    - A "v2 first" entry lifts (g) only, when its full hash is the commit (g) read; it needs no evidence and gives no
      standing.
    - Every applied entry in v2's form, (h) or (i) needs its evidence run (v2's section 11.3.5), else the start stays
      refused, naming `valiwatt evidence-v3`.
    - The standing: post hoc whenever (h) or (i) is among the causes, or an (h) or (i) entry applies (it was ruled for
      this code, so a 2025 time read otherwise later never makes the start clean), whatever else applies; otherwise the
      last applying entry in v2's form gives it (even when no cause fired, as in v2); otherwise clean.

    Refused (FreezeError) unless every cause is lifted, naming each condition left. Returns {"ruling": the applying
    entry in v2's form or None (with its "start", for evaluate.evaluation_label), "rulings": every applied entry with
    its evidence_run_id ("" for v2 first), "standing"}."""
    is_ancestor = is_ancestor or records_v3._is_ancestor
    said = said or {}
    unknown = sorted(set(causes) - set(CAUSES))
    if unknown:
        raise ValueError(f"causes {unknown} are none of {list(CAUSES)}")
    latest = max((s["started_utc"] for s in starts), default=None)

    def applies(r: dict) -> bool:
        if r["kind"] == "v2_first":
            return v2.get("commit") is not None and r["commit"] == v2["commit"]
        ok = (is_ancestor(r["commit"], head) and all(is_ancestor(x["commit"], r["commit"]) for x in rule_commits)
              and all(is_ancestor(s["git_commit"], r["commit"]) for s in starts))
        if r["kind"] == "v2_form" and "f" in causes:
            ok = ok and r["start"] == latest
        return ok

    last = {}
    for r in rulings:
        if applies(r):
            last[r["kind"]] = r
    lifted = {"a": "v2_form", "f": "v2_form", "g": "v2_first", "h": "h", "i": "i"}
    forms = {"v2_form": "\"<date>, the owner's ruling on 2025: clean\" or \"... post hoc\"",
             "h": "\"<date>, the owner's ruling on 2025 (h): post hoc\"",
             "i": "\"<date>, the owner's ruling on 2025 (i): post hoc\"",
             "v2_first": "\"<date>, the owner's ruling on v2 first\", naming the v2-branch commit (g) read"}
    left = [c for c in causes if lifted[c] not in last]
    if left:
        raise FreezeError("the 2025 start is refused: " + " ".join(said.get(c, f"({c}) fired.") for c in left)
                          + " " + "; ".join(f"({c}) is lifted only by an entry {forms[lifted[c]]} in AGENTS.md's v3 "
                                            "departures section that applies to this start" for c in left)
                          + ". `valiwatt evidence-v3` produces the evidence the owner rules on")
    applied = []
    for kind in ("v2_form", "h", "i"):
        r = last.get(kind)
        if r is None:
            continue
        run_id = evidence(r["commit"])
        if run_id is None:
            raise FreezeError(f"the owner's ruling entry {r['head']!r} applies, but no finished evidence run made from "
                              f"a clean tree exists for commit {r['commit'][:12]}, or a descendant up to HEAD, on the "
                              "gate's data-quality run: run `valiwatt evidence-v3` first")
        applied.append({**r, "evidence_run_id": run_id})
    if "v2_first" in last and "g" in causes:
        applied.append({**last["v2_first"], "evidence_run_id": ""})
    form = next((r for r in applied if r["kind"] == "v2_form"), None)
    ruling = None if form is None else {"commit": form["commit"], "outcome": form["outcome"], "head": form["head"],
                                        "start": form["start"], "evidence_run_id": form["evidence_run_id"]}
    if "h" in causes or "i" in causes or any(r["kind"] in ("h", "i") for r in applied):
        standing = "post_hoc"
    elif ruling is not None:
        standing = ruling["outcome"]
    else:
        standing = "clean"
    return {"ruling": ruling, "rulings": applied, "standing": standing}


# ------------------------------------------------------------------------------------------- the 2025 gate

def _check_diagnostics_v3(c) -> None:
    """(c): every 2025 diagnostic_v3 run in the store (whatever its status, deleted included) names by replay_run_id a
    finished, complete 2025 replay_v3 run of its git_commit and freeze_commit (v2's section 11.3.5)."""
    bad = [f"{d.info.run_id} (replay_run_id {d.data.tags.get('replay_run_id') or 'none'!r})"
           for d in mrun._replay_runs(c, kind=DIAGNOSTIC_KIND)
           if d.data.tags.get("year") == str(HELD_OUT_YEAR) and not _names_finished_replay(c, d.data.tags)]
    if bad:
        raise FreezeError(f"(c) 2025 diagnostic runs {bad} name no finished, complete 2025 replay of their own "
                          "commits")


def _reconcile_ledger_v3(c, entries: list[dict]) -> None:
    """(c) the run store and the committed ledger-v3 agree (v2's section 11.3.4 (c): a disagreement between them
    refuses): every `replay_v3` run of the store, of any year and status, deleted included, that a committed entry
    records is FINISHED with status tag complete, and its entry derived again from the store (report/ledger_v3.py
    `entry`, from its tags and results/replay.json) is the committed one field for field (`disagreement`). A run the
    ledger records as finished, held out, post hoc or seen by the developers, is never read as a stopped start because
    its store status changed since; nor is any other field of the store taken over the committed record. Refused
    (FreezeError, (c)), naming each run and what differs."""
    from valiwatt.report import ledger as v2_ledger
    from valiwatt.report import ledger_v3

    recorded = {e["run_id"]: e for e in entries}
    problems = []
    for run in mrun._replay_runs(c, kind=KIND):
        e, rid, t = recorded.get(run.info.run_id), run.info.run_id, run.data.tags
        if e is None:
            continue
        if run.info.status != "FINISHED" or t.get("status") != "complete":
            problems.append(f"run {rid} is {run.info.status} in the store, status tag {t.get('status')!r}, yet the "
                            f"ledger records it finished ({e['year']}, {e['evaluation']}, started {e['started_utc']}): "
                            "a recorded completion never becomes an unfinished start")
            continue
        try:
            stored = ledger_v3.entry(run, v2_ledger.results_of(c, run))
        except Exception as ex:  # noqa: BLE001 - a run that cannot be read as its entry disagrees with it
            problems.append(f"run {rid} cannot be read as its ledger-v3 entry: {ex}")
            continue
        if diff := ledger_v3.disagreement(stored, e):
            problems.append(f"run {rid} in the store disagrees with its ledger-v3 entry on {diff}")
    if problems:
        raise FreezeError("(c) the run store and the committed ledger-v3 disagree: " + "; ".join(problems))


def files_past_2024() -> list[Path]:
    """Every raw file and manifest past 2024 a 2025 replay reads (section 11.3 (1): byte copies of the 2025 files and
    their manifests): the Hydro-Québec export through 2025 and its manifest; each station's month files of the window
    through 2025 that the window through 2024 lacks, and the station's manifest; the NBM file of 2025 and its
    manifest. One list with the data-quality run's byte-copy check (quality/byte_copies.py `files_past_window`)."""
    from valiwatt.quality.byte_copies import files_past_window

    return files_past_window(HELD_OUT_YEAR)


def latest_dq_v3(c):
    """(d)'s "latest data-quality run" (v2's section 11.3.4 (d), read by plan-v3's section 18 of 2026-10-26): the v3
    data-quality run through 2025 started last, whatever its status, deleted included, so a later run through 2025
    that failed, stopped or is still running is the latest and refuses, never passed over for an older one that
    passed; a run through another year is not read. Refused (FreezeError, (d)) when the store holds none."""
    from mlflow.entities import ViewType

    from valiwatt.quality import run_v3 as dq_v3

    exp = c.get_experiment_by_name(dq_v3.EXPERIMENT)
    runs = c.search_runs([exp.experiment_id], f"tags.kind = '{dq_v3.KIND}' and params.through = '{HELD_OUT_YEAR}'",
                         run_view_type=ViewType.ALL, order_by=["attributes.start_time DESC"],
                         max_results=1) if exp else []
    if not runs:
        raise FreezeError(f"(d) no v3 data-quality run through {HELD_OUT_YEAR} in the run store: run `valiwatt dq-v3 "
                          f"--through {HELD_OUT_YEAR}` first")
    return runs[0]


def _check_dq_2025_v3(c) -> tuple:
    """(d) and (e), checked without reading a demand record: the latest v3 data-quality run through 2025
    (`latest_dq_v3`, whatever its status, deleted included) finished, passed its gates, reaches the end of 2025 and ran
    under the current rules (a deleted run is read as any other: section 18 includes it);
    the raw files agree with it by hash only (each against its manifest, then the manifests' hashes against the run's
    params); every file and manifest
    past 2024 the replay reads is a byte copy (quality/byte_copies.py `require_copy`: no link, one name); and the
    copied 2025 Hydro-Québec manifest's retrieval time is the run's `hq_retrieved_utc`. Returns (the run, that
    retrieval time as the manifest writes it)."""
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.quality import run as qrun
    from valiwatt.quality.byte_copies import require_copy
    from valiwatt.sources import hydroquebec, nbm
    from valiwatt.sources.http import FetchRefused

    dq = latest_dq_v3(c)
    p, t = dq.data.params, dq.data.tags
    problems = []
    if dq.info.status != "FINISHED":
        problems.append(f"it is {dq.info.status}, not FINISHED")
    if t.get("gates") != "passed":
        problems.append(f"its gates are {t.get('gates')!r}, not passed")
    if p.get("window_last_utc") != data_last(HELD_OUT_YEAR).isoformat():
        problems.append(f"its last hour is {p.get('window_last_utc')}, not {data_last(HELD_OUT_YEAR).isoformat()}")
    if t.get("rules_sha256") != rules_sha256():
        problems.append("it ran under other rules")
    if problems:
        raise FreezeError(f"(d) the latest v3 data-quality run {dq.info.run_id} is not a finished, passing through-"
                          f"{HELD_OUT_YEAR} run under the current rules: {'; '.join(problems)}")
    try:
        for f in files_past_2024():
            require_copy(f)
        hq, eccc_manifests = qrun._verify_raw(HELD_OUT_YEAR)
        nbm_manifest = qrun._verify_nbm(HELD_OUT_YEAR)
    except FetchRefused as e:
        raise FreezeError(f"(e) a raw file past 2024 is not a byte copy, or no longer matches its manifest: {e}") \
            from e
    differ = []
    if hq.get("sha256") != p.get("hq_sha256"):
        differ.append("the Hydro-Québec file")
    if qrun.eccc_manifests_sha256(eccc_manifests) != p.get("eccc_manifests_sha256"):
        differ.append("the ECCC files")
    if nbm.manifest_sha256(nbm_manifest) != p.get("nbm_manifest_sha256"):
        differ.append("the NBM files")
    if differ:
        raise FreezeError(f"(e) {differ} differ by hash from those data-quality run {dq.info.run_id} checked")
    try:
        retrieved = json.loads(Path(hydroquebec.manifest_path(HELD_OUT_YEAR)).read_text(encoding="utf-8"))[
            "retrieved_utc"]
        same = _utc(retrieved) == _utc(p.get("hq_retrieved_utc"))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise FreezeError(f"(e) the copied Hydro-Québec manifest through {HELD_OUT_YEAR} gives no retrieval time "
                          f"that can be read against data-quality run {dq.info.run_id}'s: {e}") from e
    if not same:
        raise FreezeError(f"(e) the copied Hydro-Québec manifest through {HELD_OUT_YEAR} was retrieved at {retrieved}, "
                          f"not at {p.get('hq_retrieved_utc')}, the time data-quality run {dq.info.run_id} read")
    return dq, retrieved


def _check_record_runs(c, data: dict) -> None:
    """(b): each run the freeze record names is in the store, FINISHED, and every per-run field of the record is the
    store's: its code commit, a clean tree, its rules hash (the record's, the rules at the first pin), its freeze tag
    and its end time; the data-quality run's three raw-file hashes are the record's."""
    from mlflow.exceptions import MlflowException

    from valiwatt.freeze_record import ROLES

    problems = []
    for role in ROLES:
        rec = data["runs"][role]
        try:
            run = c.get_run(rec["run_id"])
        except MlflowException:
            problems.append(f"the {role} run {rec['run_id']} is not in v3's run store")
            continue
        t = run.data.tags
        if run.info.status != "FINISHED":
            problems.append(f"the {role} run {rec['run_id']} is {run.info.status}, not FINISHED")
        if t.get("git_dirty") != "false" or rec["git_dirty"] is not False:
            problems.append(f"the {role} run {rec['run_id']} ran from a tree that was not clean (git_dirty "
                            f"{t.get('git_dirty')!r})")
        if t.get("rules_sha256") != data["rules_sha256"] or rec["rules_sha256"] != data["rules_sha256"]:
            problems.append(f"the {role} run {rec['run_id']} ran under other rules than those at the commit "
                            f"`{V3_FREEZE_TAG}` was first pinned to")
        for field, stored in (("git_commit", t.get("git_commit")), ("freeze_tag", t.get("freeze_tag")),
                              ("ended_utc", mrun._utc(run.info.end_time))):
            if stored != rec[field]:
                problems.append(f"the {role} run {rec['run_id']}'s {field} is {stored!r} in the store and "
                                f"{rec[field]!r} in the freeze record")
        if role == "data_quality":
            for k in ("hq_sha256", "eccc_manifests_sha256", "nbm_manifest_sha256"):
                if run.data.params.get(k) != rec[k]:
                    problems.append(f"the data-quality run {rec['run_id']}'s {k} differs from the freeze record's")
    if problems:
        raise FreezeError("(b) the runs the freeze record names: " + "; ".join(problems))


def _paths_differ(a, b, at: str = "") -> list[str]:
    """The paths (runs.validation.reproduction, ...) at which two records parsed from JSON differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        return [p for k in dict.fromkeys([*a, *b]) for p in _paths_differ(a.get(k), b.get(k), f"{at}.{k}" if at else k)]
    return [] if a == b and type(a) is type(b) else [at or "the record"]


def _evidence_run_v3(c, commit: str, dq_run_id: str):
    """The latest finished evidence_v3 run made from a clean tree on `dq_run_id`, whose commit is `commit` or a
    descendant of it that is HEAD or an ancestor of HEAD; None when there is none (v2's rule, monitor/run.py
    `_evidence_run`, on v3's kind). Deleted runs included: the evidence requirement sets no lifecycle condition, and a
    deleted run keeps its record and its artifacts."""
    found = []
    for e in mrun._replay_runs(c, kind=EVIDENCE_KIND):
        t = e.data.tags
        if e.info.status == "FINISHED" and t.get("status") == "complete" and t.get("git_dirty") == "false" \
                and t.get("dq_run_id") == dq_run_id and records_v3._is_ancestor(commit, t.get("git_commit", "")) \
                and records_v3._is_ancestor(t.get("git_commit", ""), "HEAD"):
            found.append(e)
    return max(found, key=lambda r: r.info.start_time) if found else None


def held_out_2025_v3(freeze: dict) -> dict:
    """The rest of v3's 2025 gate (section 11.3 (3)), after the first-run guard, the clean tree, the freeze and the
    moved tag, in this order, each refusal a FreezeError naming its condition and its evidence. Reads no demand record.

    0. (c) the committed ledger-v3 read, and every run of the store it records reconciled with its entry
    (`_reconcile_ledger_v3`), before the starts are built (records_v3.replay_starts_v3 on that ledger); 1. (c) no
    other 2025 replay is RUNNING; 2. (c) every 2025 diagnostic run names its finished replay; 3. (c) the
    freeze record is committed, read as the commit that added it holds it (freeze_record.read_freeze_record), and (a)
    that commit is the one commit of the repository's refs and reflogs (HEAD, `--all` and `--reflog`) that adds it;
    4. (b) its first pin and
    rules hash are the tag's first pin (records_v3.first_pin) and the rules at it; 5. (a) the record's commit
    descends from the first pin, and, a cause the owner's ruling can lift (section 11.3 (3) and (4)), is HEAD or an
    ancestor of HEAD: every check after it runs on the record as its commit holds it; 6. (c) ledger-v3 holds the
    record's rehearsal under the first pin; 7. (d), (e) the data (`_check_dq_2025_v3`); 8. (b) the named runs against
    the record (`_check_record_runs`), the artifacts that judge 2025 (freeze_record.frozen_chain_v3 of the record's
    frozen 2025 and validation runs) against its hashes, each item that differs named, then the record derived again by its
    own writer (freeze_record.make_record on the record's rehearsal and frozen 2025 runs), which must refuse nothing
    and give the committed record field for field: every lineage claim (byte copies, fresh clone, reproduction, each
    run finished and complete, its kind, its commit between the first pin and the record's commit, not the start's
    HEAD: a record off the start's history is step 5's (a) cause) is checked against the store,
    never taken from the record; 9. (a)'s walk from the first pin; 10. (g) v2's test of record at its ref; 11. (h) the
    record's commit and every named run's end precede the 2025 time, which is never later than an earlier reading of
    it (`earlier_2025_times`); 12. (i) no commit dated after the 2025 time, from the record's commit to HEAD, touches a
    rule file or a dependency; 13. the causes; 14. the rulings of v3's section (a clean (h) or (i) entry refused); 15. the standing
    (`standing_v3`). Returns the gate's record, which run_replay_v3 logs at once as results/held_out_gate.json: v2's
    `causes`, `ruling` and `standing`, so evaluate.evaluation_label reads it unchanged."""
    from valiwatt import tracking
    from valiwatt.freeze_record import (FreezeRecordAnchorError, FreezeRecordError, ROLES, dumps, frozen_chain_v3,
                                        make_record, read_freeze_record)
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.report import ledger_v3

    c = tracking.client()
    # 0. (c) the committed ledger-v3, and every run of the store it records reconciled with its entry, before the
    # starts are built from both.
    try:
        entries = ledger_v3.read()
    except ledger_v3.LedgerError as e:
        raise FreezeError(f"(c) the committed ledger-v3 cannot be read: {e}") from e
    _reconcile_ledger_v3(c, entries)
    # 1-2. (c) one start at a time, and the diagnostics.
    starts = records_v3.replay_starts_v3(HELD_OUT_YEAR, client=c, ledger={"replays": entries})
    running = [s["run_id"] for s in starts if s["running"]]
    if running:
        raise FreezeError(f"(c) another v3 2025 replay is RUNNING in the run store: {running}. One start at a time; a "
                          "run a killed process left RUNNING is ended as FAILED with the MLflow client first, and "
                          "still counts as an unfinished start")
    _check_diagnostics_v3(c)
    # 3. (c) the freeze record, as its commit holds it; (a) its anchor, the one commit of the repository's refs and
    # reflogs that adds it, wherever it lies (step 5 reads whether it is an ancestor of HEAD).
    try:
        rec = read_freeze_record()
    except FreezeRecordAnchorError as e:
        raise FreezeError(f"(a) the freeze record: {e}") from e
    except FreezeRecordError as e:
        raise FreezeError(f"(c) the freeze record: {e}") from e
    data = rec["data"]
    # 4. (b) its pin and rules.
    try:
        first = records_v3.first_pin()
    except FreezeError as e:
        raise FreezeError(f"(b) {e}") from e
    if data["first_pin"] != first or data["rules_sha256"] != rules_sha256(first):
        raise FreezeError(f"(b) the freeze record names the first pin {data['first_pin'][:12]} and its rules hash; "
                          f"`{V3_FREEZE_TAG}` was first pinned to {first[:12]}, whose rules hash "
                          f"{rules_sha256(first)[:12]} the record must hold")
    # 5. (a) the record's commit: off HEAD's history, a cause (13), ruled on as any (a) cause.
    head = mrun._head()
    record_off_head = not records_v3._is_ancestor(rec["commit"], head)
    if not records_v3._is_ancestor(first, rec["commit"]):
        raise FreezeError(f"(a) the commit that adds the freeze record, {rec['commit'][:12]}, does not descend from "
                          f"{first[:12]}, the commit `{V3_FREEZE_TAG}` was first pinned to")
    # 6. (c) ledger-v3 holds the rehearsal.
    rehearsal_id = data["runs"]["rehearsal_2024"]["run_id"]
    entry = next((e for e in entries if e["run_id"] == rehearsal_id), None)
    if entry is None or entry["freeze_commit"] != first:
        raise FreezeError(f"(c) the committed ledger-v3 holds no entry of the freeze record's rehearsal {rehearsal_id} "
                          f"under the freeze commit {first[:12]}: run `valiwatt ledger-v3` and commit it first")
    # 7. (d), (e) the data.
    dq, retrieved = _check_dq_2025_v3(c)
    # 8. (b) the runs and the artifacts.
    _check_record_runs(c, data)
    try:
        chain = frozen_chain_v3(data["runs"]["frozen_2025"]["run_id"], data["runs"]["validation"]["run_id"],
                                HELD_OUT_YEAR, client=c)
    except FreezeRecordError as e:
        raise FreezeError(f"(b) the artifacts the freeze record names: {e}") from e
    if chain["digest"] != data["frozen_sha256"] or chain["model"].sha256() != data["model_sha256"]:
        named = mrun._differences(chain["frozen_sha256"], data["frozen_items"])
        if chain["model"].sha256() != data["model_sha256"] and "production" not in named:
            named = ["production", *named]
        raise FreezeError(f"(b) the artifacts that judge 2025 differ from docs/freeze-v3.json: {named or ['the digest']}"
                          " differ from the hashes the freeze record holds")
    try:
        derived = make_record(rehearsal_id, client=c, frozen_2025=data["runs"]["frozen_2025"]["run_id"],
                              head=rec["commit"])
    except FreezeRecordError as e:
        raise FreezeError(f"(b) the freeze record's writer, run again on the store, refuses its runs: {e}") from e
    differ = _paths_differ(json.loads(dumps(derived)), data)
    if differ:
        raise FreezeError(f"(b) the freeze record's writer, run again on the store, gives another record than the one "
                          f"committed, at {differ}: the record is never its own authority")
    # 9. (a)'s walk.
    rule_commits = _rule_commits_v3(first)
    unknown = [s["run_id"] for s in starts if not s["git_commit"]]
    if unknown:
        raise FreezeError(f"(c) 2025 starts {unknown} are in the committed ledger-v3 but not in the run store: the code "
                          "they ran on is unknown")
    # 10. (g) v2 first.
    v2 = v2_test_of_record()
    # 11. (h) fetched after the freeze, the 2025 time never later than an earlier reading of it.
    t2025 = time_2025(retrieved, v2, earlier_2025_times(c, starts))
    t = _utc(t2025["utc"])
    late_ends = [role for role in ROLES if _utc(data["runs"][role]["ended_utc"]) >= t]
    h_fires = rec["committer_utc"] >= t or bool(late_ends)
    # 12. (i) no change after 2025.
    parent = _commit_of(f"{rec['commit']}^")
    try:
        span = dated_commits(parent, "HEAD")
    except FreezeError as e:
        raise FreezeError(f"(i) {e}") from e
    after = [x for x in span if x["committer_utc"] > t and any(mrun._is_change(p) for p in x["paths"])]
    # 13. The causes, in CAUSES' order.
    fired = {"a": bool(rule_commits) or record_off_head, "f": bool(starts) and not any(s["finished"] for s in starts),
             "g": v2["test_of_record"] is None, "h": h_fires, "i": bool(after)}
    causes = [x for x in CAUSES if fired[x]]
    latest = starts[-1] if starts else None
    said = {
        "a": " ".join(([f"(a) the commit that adds the freeze record, {rec['commit'][:12]}, is not HEAD ({head[:12]}) "
                        "or an ancestor of it: the start does not run the code the record froze."]
                       if record_off_head else [])
                      + (["(a) commits since " + first[:12] + ", the commit `" + V3_FREEZE_TAG + "` was first pinned "
                          "to, touch rule files or dependencies: "
                          + "; ".join(f"{x['commit'][:12]} {x['paths']}" for x in rule_commits) + "."]
                         if rule_commits else [])),
        "f": "" if latest is None else (
            f"(f) the 2025 test of record did not finish: the latest start, of {latest['started_utc']} (run "
            f"{latest['run_id']}), stopped at stage {latest['stage'] or 'unknown'!r} with error "
            f"{latest['error'] or 'none'!r}, and its run {'holds' if latest['holds_results'] else 'holds no'} results "
            f"({len(starts)} 2025 starts, none finished)."),
        "g": "(g) v2's test of record has not finished: " + (
            v2["error"] or f"v2's ledger at {v2['ref']} ({str(v2['commit'])[:12]}) holds no 2025 entry under "
                           f"`{V2_TAG}` labelled held out or post hoc") + ".",
        "h": f"(h) the 2025 time is {t2025['utc']} ({t2025['source']}"
             + (f" of run {t2025['source_run_id']}" if t2025["source_run_id"] else "")
             + f"); the freeze record's commit {rec['commit'][:12]} was committed at {rec['committer_utc'].isoformat()}"
             + (f", and the runs {late_ends} ended" if late_ends else "") + " at or after it.",
        "i": f"(i) commits committed after the 2025 time ({t2025['utc']}) touch rule files or dependencies: "
             + "; ".join(f"{x['commit'][:12]} at {x['committer_utc'].isoformat()} {sorted(p for p in x['paths'] if mrun._is_change(p))}"
                         for x in after) + ".",
    }
    # 14. The rulings.
    try:
        rulings = records_v3.rulings_v3(records_v3.v3_section())
    except FreezeError as e:
        raise FreezeError(f"(h), (i) the v3 departures section: {e}") from e
    # 15. The standing.
    def evidence(commit: str) -> str | None:
        run = _evidence_run_v3(c, commit, dq.info.run_id)
        return None if run is None else run.info.run_id

    standing = standing_v3(causes, rulings, rule_commits=rule_commits, starts=starts, v2=v2, head=head,
                           evidence=evidence, said=said)
    tr = v2["test_of_record"]
    return {"freeze_record": {"commit": rec["commit"], "sha256": rec["sha256"], "first_pin": first,
                              "committer_utc": rec["committer_utc"].isoformat()},
            "frozen_run_id": data["runs"]["frozen_2025"]["run_id"],
            "validation_run_id": data["runs"]["validation"]["run_id"], "rehearsal_run_id": rehearsal_id,
            "dq_run_id": dq.info.run_id, "frozen_sha256": chain["frozen_sha256"], "model_sha256": data["model_sha256"],
            "freeze_commit": freeze["freeze_commit"], "head_commit": head, "rule_commits": rule_commits,
            "rule_commits_after_2025": [{"commit": x["commit"], "committer_utc": x["committer_utc"].isoformat(),
                                         "paths": x["paths"]} for x in after],
            "v2_first": None if tr is None else {"ref": v2["ref"], "commit": v2["commit"], "entry_run_id": tr["run_id"],
                                                 "started_utc": tr["started_utc"], "evaluation": tr["evaluation"]},
            "t2025": t2025, "causes": causes, "ruling": standing["ruling"], "rulings": standing["rulings"],
            "standing": standing["standing"]}


@contextmanager
def start_lock_v3():
    """The reservation of a v3 2025 start (v2's section 11.3.4 (c), monitor/run.py `start_lock`, copied: v2's is not
    edited): an exclusive, non-blocking lock on paths.MLRUNS / LOCK_NAME, inside v3's run store's folder. A caller that
    cannot take it is refused at once and opens no run."""
    paths.MLRUNS.mkdir(parents=True, exist_ok=True)
    fd = os.open(paths.MLRUNS / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise FreezeError(f"(c) another v3 2025 start is being made (the lock {LOCK_NAME} in the run store is "
                              "held): this caller opens no run") from e
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ------------------------------------------------------------------------------------- the rehearsal's checks

def contrast_frame_v3(hourly: pd.DataFrame) -> pd.DataFrame:
    """The frame evaluate.judge_v3 reads, on the replayed hours: the target, day, hour, the noon feed's temperature,
    and the forecast of v3 (the replay's own) and of each comparison model, each with why it has none for an hour:
    v3's reasons as issued; the noon comparators share v3's usable rows, so they carry v3's reason ("calendar" when v3
    has a forecast and they have none); v2's form at midnight its own."""
    v3_why = hourly["not_predicted_because"].where(hourly["forecast"].isna(), "")
    frame = pd.DataFrame({"target": hourly["target"], "day": hourly["day"], "hour": hourly["hour"],
                          "temp_forecast": hourly["temp_forecast"], PRED_V3: hourly["forecast"],
                          f"why_{V3_PRODUCTION}": v3_why}, index=hourly.index)
    for key in NOON_COMPARATORS:
        pred = hourly[f"pred_{key}"]
        why = v3_why.where(v3_why != "", "calendar")
        frame[f"pred_{key}"], frame[f"why_{key}"] = pred, why.where(pred.isna(), "")
    frame[f"pred_{MIDNIGHT_KEY}"] = hourly[f"pred_{MIDNIGHT_KEY}"]
    frame[f"why_{MIDNIGHT_KEY}"] = hourly[f"why_{MIDNIGHT_KEY}"].where(hourly[f"pred_{MIDNIGHT_KEY}"].isna(), "")
    return frame


def _same(a: pd.Series, b: pd.Series) -> np.ndarray:
    """Equal bit for bit on the same labels, missing equal to missing: floats by their bytes, times by their
    nanoseconds, anything else by value."""
    if pd.api.types.is_datetime64_any_dtype(a) or pd.api.types.is_datetime64_any_dtype(b):
        x, y = (pd.to_datetime(s, utc=True).astype("datetime64[ns, UTC]") for s in (a, b))
        return ((x == y) | (x.isna() & y.isna())).to_numpy()
    if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
        x, y = a.to_numpy(dtype="<f8"), b.to_numpy(dtype="<f8")
        return (x.view("<i8") == y.view("<i8")) | (np.isnan(x) & np.isnan(y))
    x, y = a.to_numpy(dtype=object), b.to_numpy(dtype=object)
    return np.array([(pd.isna(p) and pd.isna(q)) if (pd.isna(p) or pd.isna(q)) else p == q for p, q in zip(x, y)],
                    dtype=bool)


IDENTITY_INPUTS = ("temp_forecast", "wind_forecast", "daymean_forecast", "available_forecast", "lagday_observed")


def rehearsal_identity(hourly: pd.DataFrame, record: pd.DataFrame, oos: pd.DataFrame,
                       validation_record: pd.DataFrame, bins: pd.DataFrame, frozen, year: int = REHEARSAL_YEAR) -> dict:
    """The rehearsal's identity with validation (section 11.1), on every day and hour of `year` either holds; refused
    (RehearsalIdentityError, with the counts) on any difference, before anything is logged.

    - Each day was fed by the same run, at the same rank, or left unfed for the same reason: the replay's forecast
      record against validation's frames/forecast_record_noon.parquet (runtime, rank, reason; missing equal to
      missing). A day only one holds differs.
    - Every noon input it issued is validation's, bit for bit, missing equal to missing (frames/oos.parquet):
      IDENTITY_INPUTS, the run's temperature, wind, day's mean and availability, and the lag day's mean (so a day
      unfed for want of its lag day's mean shows a blank lagday_observed on both sides). An hour only one holds
      differs.
    - The interval layer: the frozen 2024 model's (bin_edge, q_cold, q_rest, fallback) is exactly the January fold's
      row of frames/bins.parquet (model v3; edge_c, q_cold, q_rest, fallback), whose clock is the same; each hour's
      bin is evaluate.bin_of of validation's temperature against that fold's edge; and each hour's issued interval is
      its forecast x (1 - q, 1 + q), q that row's q_cold or q_rest by that bin, bit for bit (the product
      FrozenModel.predict issues, so exact), so a half-width the replay issued from anything but the January fold's
      row differs."""
    o = oos[pd.to_datetime(oos["day"]).dt.year == year]
    h = hourly[pd.to_datetime(hourly["day"]).dt.year == year]
    hours = o.index.union(h.index)
    inputs = {"only_one": int((~hours.isin(h.index) | ~hours.isin(o.index)).sum())}
    inputs |= {c: int((~_same(h[c].reindex(hours), o[c].reindex(hours))).sum()) for c in IDENTITY_INPUTS}

    vr = validation_record[pd.DatetimeIndex(validation_record.index).year == year]
    rr = record[pd.DatetimeIndex(record.index).year == year]
    days = vr.index.union(rr.index)
    run_diff = {"only_one": int((~days.isin(rr.index) | ~days.isin(vr.index)).sum())}
    for col in ("runtime", "rank", "reason"):
        run_diff[col] = int((~_same(rr[col].reindex(days), vr[col].reindex(days))).sum())

    row = bins[(bins["model"] == V3_PRODUCTION) & (bins["fold"] == f"{year}-01")]
    layer = frozen.interval or {}
    if len(row) != 1:
        layer_diff = ["the January fold's row (none, or several)"]
        edge = np.nan
    else:
        r = row.iloc[0]
        edge = float(r["edge_c"])
        layer_diff = [k for k, theirs in (("bin_edge", edge), ("q_cold", float(r["q_cold"])),
                                          ("q_rest", float(r["q_rest"])), ("fallback", bool(r["fallback"])))
                      if layer.get(k) != theirs]
    want = pd.Series(ev.bin_of(o["temp_forecast"].reindex(hours).to_numpy(dtype=float), edge), index=hours)
    got = h["bin"].reindex(hours).astype(object)
    bins_diff = int((~_same(got.where(got.notna(), None), want.where(want.notna(), None))).sum())
    widths_diff = 0
    if len(row) == 1:
        q = np.where(want == "cold", float(r["q_cold"]), np.where(want == "rest", float(r["q_rest"]), np.nan))
        f = h["forecast"].reindex(hours).to_numpy(dtype=float)
        widths_diff = sum(int((~_same(h[col].reindex(hours), pd.Series(f * (1 + sign * q), index=hours))).sum())
                          for col, sign in (("lower", -1), ("upper", 1)))

    if any(inputs.values()) or any(run_diff.values()) or layer_diff or bins_diff or widths_diff:
        raise RehearsalIdentityError(
            f"the {year} rehearsal differs from validation: of {len(hours)} hours, inputs differ "
            f"{ {k: v for k, v in inputs.items() if v} }, {bins_diff} hours take another bin and {widths_diff} "
            f"interval bounds another half-width than the January {year} fold's; of {len(days)} days, "
            f"{ {k: v for k, v in run_diff.items() if v} } fed by another run, at another rank or left unfed for "
            f"another reason; the interval layer differs from the January {year} fold's on {layer_diff}: design and "
            "replay must read the same input (docs/plan-v3.md, section 11.1)")
    return {"hours": int(len(hours)), "days": int(len(days)), "inputs": list(IDENTITY_INPUTS),
            "layer": {k: layer.get(k) for k in ("bin_edge", "q_cold", "q_rest", "fallback")}, "differences": 0}


def compare_refitted_v3(r, oos: pd.DataFrame, intervals: pd.DataFrame, thresholds: dict, reference: pd.DataFrame,
                        year: int, n_boot: int = N_BOOT) -> dict:
    """The frozen v3 against validation's monthly-refitted folds on the same year (monitor/run.py `compare_refitted`
    with v3's forecast, pred_v3, and validation's frames/intervals_v3.parquet, its binned intervals)."""
    return mrun.compare_refitted(r, oos, intervals, thresholds, reference, year, n_boot, pred_col=PRED_V3)


def comparator_summary_v3(hourly: pd.DataFrame, n_boot: int = N_BOOT) -> dict:
    """Each comparison model's MAPE on the hours it forecast with a usable target, and the coverage of its interval
    when it issues one (v2's form at midnight issues none: None)."""
    base = hourly.assign(fold=hourly["day"].dt.to_period("M").astype(str))
    out = {}
    for key in (*NOON_COMPARATORS, MIDNIGHT_KEY):
        e = ev.errors(base, f"pred_{key}")
        cov = None
        if hourly[f"lower_{key}"].notna().any():
            inside = (base["target"] >= base[f"lower_{key}"]) & (base["target"] <= base[f"upper_{key}"])
            e["covered"] = inside.astype(float).reindex(e.index)
            cov = ev.mean_with_ci(e.dropna(subset=["covered"]), "covered", n_boot)
        out[key] = {"mape": ev.mean_with_ci(e, "ape", n_boot), "coverage": cov, "hours": int(len(e))}
    return out


def coverage_by_bin(hourly: pd.DataFrame, n_boot: int = N_BOOT) -> dict:
    """v3's coverage in each temperature bin and overall (section 7), on the hours it forecast with a usable target;
    None for a bin without such an hour."""
    e = ev.errors(hourly.assign(fold=hourly["day"].dt.to_period("M").astype(str)), "forecast")
    e["covered"] = hourly["covered"].reindex(e.index)
    e["bin"] = hourly["bin"].reindex(e.index)
    e = e.dropna(subset=["covered"])
    out = {}
    for name, part in (("cold", e[e["bin"] == "cold"]), ("rest", e[e["bin"] == "rest"]), ("all", e)):
        out[name] = ev.mean_with_ci(part, "covered", n_boot) if len(part) else None
    return out


# ------------------------------------------------------------------------------------------------ the replay

def replay_days(year: int) -> tuple[str, str]:
    """The local standard days a v3 replay of `year` covers: the whole year."""
    return f"{year}-01-01", f"{year}-12-31"


def _locate_v3(year: int, record: dict) -> dict:
    """The runs a v3 replay will read, found from their records before its run opens, so that it opens with their IDs
    and the frozen production model's hash. 2024: the latest v3 data-quality run that passed, which must be of the
    2019-2024 window with its byte copies checked (quality/run_v3.py `require_design_run`), the latest complete v3
    validation run, and the run of v3's registered model's alias replay-2024. 2025: exactly the gate's record's (the
    freeze record's runs, the gate's data-quality run). "fresh_clone": whether the data-quality run is labelled a fresh
    clone's (2025: never), which the run's tags carry (section 3.1)."""
    from valiwatt.forecast import validate as vd
    from valiwatt.forecast.freeze_v3 import MODEL_NAME_V3
    from valiwatt.quality import run_v3 as dq_v3
    from valiwatt.tracking import client, latest_run

    if year == HELD_OUT_YEAR:
        return {"dq_run_id": record["dq_run_id"], "validation_run_id": record["validation_run_id"],
                "frozen_run_id": record["frozen_run_id"], "coefficient_sha256": record["model_sha256"],
                "fresh_clone": False}
    dq = dq_v3.latest_passed()
    lineage = dq_v3.require_design_run(dq)
    val = latest_run(vd.EXPERIMENT, "tags.kind = 'validation_v3' and tags.status = 'complete'")
    c = client()
    frozen = c.get_run(c.get_model_version_by_alias(MODEL_NAME_V3, f"replay-{year}").run_id)
    return {"dq_run_id": dq.info.run_id, "validation_run_id": val.info.run_id, "frozen_run_id": frozen.info.run_id,
            "coefficient_sha256": frozen.data.tags.get("coefficient_sha256", ""),
            "fresh_clone": bool(lineage["fresh_clone"])}


def _read_inputs_v3(year: int, freeze: dict, located: dict, record: dict) -> dict:
    """The replay's inputs, read inside its open run. 2024: the data-quality run (`require_design_run` again: a fresh
    clone's is refused once a read of v2's store is logged), v3's validation run on it and the models frozen for 2024
    (load_frozen under v3's registered model), each the one the run opened with; the frozen run built on that
    validation run, under the freeze in force, for 2024; what judges it through freeze_record.frozen_chain_v3; the raw
    records and the runs of 2019-2024 the data-quality run checked; the data end with 2024. 2025: exactly the freeze
    record's runs, through frozen_chain_v3, checked again against the hashes the gate checked; the gate's data-quality
    run; the data end with 2025."""
    import mlflow

    from valiwatt.forecast.freeze import load_frozen
    from valiwatt.forecast.freeze_v3 import MODEL_NAME_V3
    from valiwatt.forecast.validate import LineageError
    from valiwatt.forecast.validate_v3 import V3_DESIGN_YEARS, load_validation_v3
    from valiwatt.freeze_record import frozen_chain_v3
    from valiwatt.quality import run as qrun
    from valiwatt.quality import run_v3 as dq_v3
    from valiwatt.tracking import client

    c = client()
    if year == HELD_OUT_YEAR:
        dq = mrun._get_run(c, located["dq_run_id"], "the gate's data-quality run")
        chain = frozen_chain_v3(located["frozen_run_id"], located["validation_run_id"], HELD_OUT_YEAR, client=c)
        if chain["frozen_sha256"] != record["frozen_sha256"]:
            raise FreezeError("the artifacts the freeze record names, read now, differ from those the 2025 gate "
                              f"checked: {mrun._differences(chain['frozen_sha256'], record['frozen_sha256'])}")
        raw_demand, raw_temp = mrun.load_raw(dq)
        return {"dq": dq, "chain": chain, "raw_demand": raw_demand, "raw_temp": raw_temp,
                "runs": qrun.load_runs(dq, years=(*V3_DESIGN_YEARS, HELD_OUT_YEAR), wind=True),
                "data_end": data_last(HELD_OUT_YEAR), "oos": None, "val": chain["validation_run"]}
    dq = dq_v3.latest_passed()
    mrun._same_run("data-quality run", dq.info.run_id, located["dq_run_id"])
    dq_v3.require_design_run(dq)
    val, oos = load_validation_v3(dq)
    mrun._same_run("validation run", val.info.run_id, located["validation_run_id"])
    frozen, frozen_run = load_frozen(year, MODEL_NAME_V3)
    mrun._same_run("frozen run", frozen_run.info.run_id, located["frozen_run_id"])
    t = frozen_run.data.tags
    for what, got, want in (("validation run", t.get("validation_run_id"), val.info.run_id),
                            ("rules", t.get("rules_sha256"), freeze["rules_sha256"]),
                            ("freeze tag", t.get("freeze_tag"), freeze["freeze_tag"]),
                            ("year", t.get("replay_year"), str(year))):
        if got != want:
            raise LineageError(f"frozen run {frozen_run.info.run_id} has another {what} ({got!r}) than this replay's "
                               f"({want!r}); run `valiwatt freeze-v3 --year {year}` again")
    chain = frozen_chain_v3(frozen_run.info.run_id, val.info.run_id, year, client=c)
    if chain["model"].sha256() != frozen.sha256():
        raise LineageError(f"the registered model for {year} is not frozen run {frozen_run.info.run_id}'s")
    frames = {name: pd.read_parquet(mlflow.artifacts.download_artifacts(run_id=val.info.run_id,
                                                                        artifact_path=f"frames/{name}.parquet"))
              for name in ("forecast_record_noon", "bins", f"intervals_{V3_PRODUCTION}")}
    raw_demand, raw_temp = mrun.load_raw(dq)
    return {"dq": dq, "chain": chain, "raw_demand": raw_demand, "raw_temp": raw_temp,
            "runs": qrun.load_runs(dq, years=V3_DESIGN_YEARS, wind=True), "data_end": V3_DESIGN_LAST, "oos": oos,
            "val": val, "frames": frames}


def _replay_v3(inputs: dict, first_day: str, last_day: str, departure: bool, run_id: str | None):
    """The noon replay of [first_day, last_day] on the inputs read, behind v3's gate: v3 the production model, v2's
    and v1's forms at noon scored after the loop, v2's form at midnight issued beside it (monitor/replay.py)."""
    chain = inputs["chain"]
    cmp = chain["comparators"]
    return rp.replay(inputs["raw_demand"], inputs["raw_temp"], chain["model"], chain["thresholds"],
                     chain["reference"], first_day, last_day, inputs["data_end"], departure=departure,
                     runs=inputs["runs"], comparators={k: cmp[k] for k in NOON_COMPARATORS}, run_id=run_id,
                     issue=NOON, midnight=cmp[MIDNIGHT_KEY], gate=gate_v3)


def _never_acts_v3(year: int, inputs: dict, r, located: dict) -> None:
    """After the replay, the models' hashes are those it used and its run opened with: what judges it read again
    through freeze_record.frozen_chain_v3 hashes the same, its production model is the one the replay used and the
    run recorded, and each comparison model is unchanged."""
    from valiwatt.freeze_record import frozen_chain_v3

    chain = inputs["chain"]
    again = frozen_chain_v3(chain["frozen_run"].info.run_id, chain["validation_run"].info.run_id, year)
    if again["frozen_sha256"] != chain["frozen_sha256"]:
        raise rp.MonitorActedError(f"the artifacts frozen for {year} changed during the replay: "
                                   f"{mrun._differences(again['frozen_sha256'], chain['frozen_sha256'])}")
    mrun.check_unchanged(again["model"].sha256(), r.model_sha256, located["coefficient_sha256"])
    # The replay checks v2's form at midnight itself (monitor/replay.py); the noon comparators it scored are on r.
    before = {k: m.sha256() for k, m in chain["comparators"].items()}
    if {k: m.sha256() for k, m in again["comparators"].items()} != before \
            or r.comparators_sha256 != {k: before[k] for k in NOON_COMPARATORS}:
        raise rp.MonitorActedError("a comparison model changed during the replay")


def _results_v3(year: int, r, inputs: dict, freeze: dict, departure_from: list, evaluation: str, first_day: str,
                last_day: str, n_boot: int, record: dict) -> dict:
    """Everything results/replay.json holds, computed, with every check that can stop the run (the rehearsal's
    identity first), before anything is logged."""
    chain = inputs["chain"]
    model, thresholds = chain["model"], chain["thresholds"]
    results = {"year": year, "kind": KIND, "data_end": inputs["data_end"], "clock_end": r.clock_end}
    if year == REHEARSAL_YEAR:
        f = inputs["frames"]
        results["identity"] = rehearsal_identity(r.hourly, r.forecast_record, inputs["oos"], f["forecast_record_noon"],
                                                 f["bins"], model, year)
    judgement = ev.judge_v3(contrast_frame_v3(r.hourly), chain["cold_c"], first_day, last_day, n_boot)
    results |= {"summary": mrun.summarize(r, n_boot), "days": r.days, "weeks": r.weeks, "decisions": r.decisions,
                "thresholds": thresholds,
                "model": {"sha256": r.model_sha256, "interval": model.interval, "train_last_day": model.train_last_day,
                          "lag_coefficients": model.lag_coefficients()},
                "power": mrun.detection_power(r, thresholds), "departure_from": departure_from, **freeze,
                "frozen_sha256": chain["frozen_sha256"], "comparators": comparator_summary_v3(r.hourly, n_boot),
                "judgement": judgement | {"label": evaluation},
                "verdict": judgement["verdict"] | {"label": evaluation},
                "coverage_by_bin": coverage_by_bin(r.hourly, n_boot), "forecast_error": mrun.forecast_error(r.hourly),
                "evaluation": evaluation, "n_boot": n_boot}  # the resamples of every bootstrap interval above
    if year == REHEARSAL_YEAR:
        results["refitted"] = compare_refitted_v3(r, inputs["oos"], inputs["frames"][f"intervals_{V3_PRODUCTION}"],
                                                  thresholds, chain["reference"], year, n_boot)
    else:
        results["freeze_record"] = {"commit": record["freeze_record"]["commit"],
                                    "sha256": record["freeze_record"]["sha256"]}
    return results


def _open_v3(year: int, departure: bool) -> dict:
    """The gate and every tag the replay's run carries, computed before it opens (2025: under the start lock, which
    run_replay_v3 holds from here to the opening of the run). v3's store must be its own (v2store.require_own_store)
    before anything is logged."""
    from valiwatt.v2store import require_own_store

    freeze, departure_from, record = held_out_gate_v3(year, departure)
    require_own_store()
    located = _locate_v3(year, record)
    if year == HELD_OUT_YEAR:
        evaluation = ev.evaluation_label(year, records_v3.replay_starts_v3(year), record)
    else:
        evaluation = ev.evaluation_label(year, [])
    tags = {"kind": KIND, "year": str(year), "issue": NOON.name, "freeze_tag": freeze["freeze_tag"],
            "freeze_commit": freeze["freeze_commit"], "rules_sha256": freeze["rules_sha256"],
            "coefficient_sha256": located["coefficient_sha256"], "departure": str(bool(departure_from)).lower(),
            "departure_from": ",".join(departure_from), "evaluation": evaluation,
            "dq_run_id": located["dq_run_id"], "frozen_run_id": located["frozen_run_id"],
            "validation_run_id": located["validation_run_id"], "status": "started", "stage": "opened",
            # A fresh clone's rerun (section 3.1) says so: it never enters ledger-v3 (report/ledger_v3.py).
            "fresh_clone": str(located["fresh_clone"]).lower()}
    if year == HELD_OUT_YEAR:
        tags |= {"standing": record["standing"], "freeze_record_commit": record["freeze_record"]["commit"]}
    return {"freeze": freeze, "departure_from": departure_from, "record": record, "located": located,
            "evaluation": evaluation, "tags": tags}


def _bind_to_gate_v3(record: dict, run_id: str | None = None) -> None:
    """A 2025 start runs the code its gate passed on (v2's section 11.3.5, every start's trace): HEAD is still the
    gate record's `head_commit` and the tree still clean; with `run_id`, the opened run also records that commit as its
    `git_commit`, from a clean tree. A commit made between the gate and the run (a rule change and its revert leave
    the tree as frozen, on a commit whose (a) and (i) no gate read) refuses (FreezeError, (c)): before the run opens,
    no run; after, the run stops before any input is read, and first records on itself where and why it stopped (tags
    stopped_at `bind`, bind_gate_commit the gate's head_commit, bind_run_commit the commit it recorded): the trace
    records_v3._gate_of reads as a stopped start, adjudicated under (f), never as tags changed after the run."""
    from valiwatt import tracking

    head = mrun._head()
    problems = [] if head == record["head_commit"] else [f"HEAD is {head[:12]}"]
    if run_id is not None:
        c = tracking.client()
        t = c.get_run(run_id).data.tags
        if t.get("git_commit") != record["head_commit"]:
            problems.append(f"run {run_id} records commit {str(t.get('git_commit'))[:12]}")
        if t.get("git_dirty") != "false":
            problems.append(f"run {run_id} was opened from a tree that was not clean (git_dirty "
                            f"{t.get('git_dirty')!r})")
        if problems:
            for k, v in {"stopped_at": "bind", "bind_gate_commit": record["head_commit"],
                         "bind_run_commit": t.get("git_commit") or ""}.items():
                c.set_tag(run_id, k, v)
    if problems:
        raise FreezeError(f"(c) the 2025 gate passed on {record['head_commit'][:12]}, but {'; '.join(problems)}: a "
                          "start runs only the commit its gate read")
    mrun.require_clean_tree()


@records_v3.replacements_off()
def run_replay_v3(year: int, n_boot: int = N_BOOT, departure: bool = False) -> int:
    """`valiwatt replay-v3 --year Y`, in v2's order (monitor/run.py `run_replay`): the gate (`held_out_gate_v3`); the
    tracked run, kind replay_v3, opened with every tag it will carry (status started, stage opened), and for 2025 the
    gate's record logged at once as results/held_out_gate.json, the gate, the label and this opening all under
    start_lock_v3(), HEAD still the commit the gate passed on before the run opens and the run's own commit after
    (`_bind_to_gate_v3`); then, inside the run, the inputs read (stage inputs_read), the noon replay (stage replayed),
    every check that can stop it (the never-acts check, the rehearsal's identity, the results, the Evidently
    cross-check in a temporary folder; stage checked), and only then the metrics, frames/hourly.parquet, the Evidently
    files and, last, results/replay.json (stage logged), then status complete. An exception after the run opens leaves it in the store,
    status started, its stage the last it reached and tag error the exception's class name, never its message. No
    diagnostics."""
    import mlflow

    from valiwatt.firstrun import require_first_run
    from valiwatt.tracking import start_run

    if year not in REPLAY_YEARS:
        raise ValueError(f"v3 replays {REHEARSAL_YEAR} (the rehearsal) and {HELD_OUT_YEAR}, not {year}")
    require_first_run()  # before the start lock opens its file in the run store (section 10.2)
    first_day, last_day = replay_days(year)
    with ExitStack() as opened:
        with ExitStack() as locked:
            if year == HELD_OUT_YEAR:
                locked.enter_context(start_lock_v3())
            plan = _open_v3(year, departure)
            if year == HELD_OUT_YEAR:
                _bind_to_gate_v3(plan["record"])  # before the run opens: a moved HEAD opens no run
            run = opened.enter_context(start_run(mrun.EXPERIMENT, KIND, tags=plan["tags"]))
            if year == HELD_OUT_YEAR:
                try:  # the run exists: a stop while its gate record is logged keeps its error class too
                    mlflow.log_text(json.dumps(jsonable(plan["record"]), indent=2, ensure_ascii=False),
                                    mrun.GATE_RECORD)
                except BaseException as e:
                    mrun._set("error", type(e).__name__)
                    raise
        try:
            if year == HELD_OUT_YEAR:
                _bind_to_gate_v3(plan["record"], run.info.run_id)  # the code the run records, before any input
            inputs = _read_inputs_v3(year, plan["freeze"], plan["located"], plan["record"])
            mrun._set("stage", "inputs_read")
            r = _replay_v3(inputs, first_day, last_day, departure, run.info.run_id if year == HELD_OUT_YEAR else None)
            mrun._set("stage", "replayed")
            _never_acts_v3(year, inputs, r, plan["located"])
            results = _results_v3(year, r, inputs, plan["freeze"], plan["departure_from"], plan["evaluation"],
                                  first_day, last_day, n_boot, plan["record"])
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                checks = mrun._evidently_checks(r, inputs["chain"]["thresholds"], tmp)
                mrun._set("stage", "checked")
                s = results["summary"]
                contrasts = results["judgement"]["contrasts"]
                mlflow.log_metrics({"mape": s["mape"]["mean"], "bias": s["bias"]["mean"],
                                    "coverage": s["coverage"]["mean"], "days_failed": s["days_failed"],
                                    **{f"rec.{k}": v for k, v in s["recommendations"].items()},
                                    **{f"mape.{k}": v["mape"]["mean"] for k, v in results["comparators"].items()},
                                    **{f"contrast.{k}": contrasts[k]["all"]["difference"]
                                       for k in ("terms", "family", "whole") if contrasts[k]["all"] is not None}})
                r.hourly.to_parquet(tmp / "hourly.parquet")
                mlflow.log_artifact(str(tmp / "hourly.parquet"), artifact_path="frames")
                for p in sorted(tmp.glob("evidently-*.html")):
                    mlflow.log_artifact(str(p), artifact_path="evidently")
            results["evidently_checks"] = checks
            results["run_id"] = run.info.run_id
            mlflow.log_text(json.dumps(jsonable(results), indent=2, ensure_ascii=False), "results/replay.json")
            mrun._set("stage", "logged")
            mrun._set("status", "complete")
        except BaseException as e:
            mrun._set("error", type(e).__name__)
            raise
    rec = s["recommendations"]
    v = results["verdict"]
    print(f"v3 replay {year} ({plan['evaluation']}): run {run.info.run_id}; MAPE {100 * s['mape']['mean']:.2f} %, "
          f"coverage {100 * s['coverage']['mean']:.1f} %, failed days {s['days_failed']}, recommendations "
          f"{rec or 'none'}; verdicts {v['terms']} (terms), {v['family']} (family)")
    return 0


# ------------------------------------------------------------------------------------------------- the evidence

@records_v3.replacements_off()
def run_evidence_v3(departure: bool = False) -> int:
    """`valiwatt evidence-v3` (section 11.3 (3), v2's section 11.3.5): what the owner rules on; it decides nothing, and
    no gate reads what it holds (the 2025 gate checks only that a finished one, made from a clean tree, exists for the
    ruling's commit, on its data-quality run). In order: v3's first-run guard, a clean tree, the freeze record as its
    one adding commit holds it (freeze_record.read_freeze_record, as the gate reads it: a record whose commit is not an
    ancestor of HEAD is the gate's (a), which a ruling resting on this evidence lifts); the record's rehearsal run in
    the store with its frames/hourly.parquet; the latest v3 data-quality run through 2025, whatever its status, a
    finished, passing run whose raw files agree with it (the gate's (d) and (e)); the rehearsal's frozen artifacts
    (freeze_record.frozen_chain_v3 of the record's frozen 2024 and validation runs), their digest and
    production model the record's for the rehearsal. Then the rehearsal's noon replay run again on that newest export,
    every raw record and run point labelled or available after the end of 2024 dropped at load and counted, with
    exactly those artifacts and the code at HEAD, compared hour by hour with the rehearsal on every column either
    holds (monitor/run.py `compare_hourly`), with the 2019-2024 records the newest export revised. A run of kind
    evidence_v3: never a start, never a ledger entry, no identity check (its inputs are revised by design)."""
    import mlflow

    from valiwatt.firstrun import require_first_run
    from valiwatt.forecast.validate_v3 import V3_DESIGN_YEARS
    from valiwatt.freeze_record import FreezeRecordError, frozen_chain_v3, read_freeze_record
    from valiwatt.quality import run as qrun
    from valiwatt.sources.nbm import AVAILABILITY_LAG
    from valiwatt.tracking import client, start_run
    from valiwatt.v2store import require_own_store

    require_first_run()
    mrun.require_clean_tree()
    try:
        rec = read_freeze_record()
    except FreezeRecordError as e:
        raise FreezeError(f"the evidence reruns the rehearsal the freeze record names: {e}") from e
    require_own_store()
    data = rec["data"]
    c = client()
    rehearsal = data["runs"]["rehearsal_2024"]
    run0 = mrun._get_run(c, rehearsal["run_id"], "the freeze record's rehearsal run")
    if "frames/hourly.parquet" not in mrun._artifact_paths(c, run0.info.run_id):
        raise FreezeError(f"the freeze record's rehearsal run {run0.info.run_id} holds no frames/hourly.parquet to "
                          "compare with")
    dq, _ = _check_dq_2025_v3(c)
    try:
        chain = frozen_chain_v3(data["runs"]["frozen_2024"]["run_id"], data["runs"]["validation"]["run_id"],
                                REHEARSAL_YEAR, client=c)
    except FreezeRecordError as e:
        raise FreezeError(f"the rehearsal's frozen artifacts: {e}") from e
    if chain["digest"] != rehearsal["frozen_sha256"] or chain["model"].sha256() != rehearsal["model_sha256"]:
        raise FreezeError("the rehearsal's frozen artifacts, read now, are not those the freeze record names for it: "
                          + ("its production model differs; " if chain["model"].sha256() != rehearsal["model_sha256"]
                             else "") + f"digest {chain['digest'][:12]}, recorded {rehearsal['frozen_sha256'][:12]}")
    with start_run(mrun.EXPERIMENT, EVIDENCE_KIND, tags={
            "kind": EVIDENCE_KIND, "dq_run_id": dq.info.run_id, "rehearsal_run_id": run0.info.run_id,
            "freeze_record_commit": rec["commit"], "status": "started"}) as run:
        try:
            raw_demand, raw_temp = mrun.load_raw(dq)
            runs = qrun.load_runs(dq, years=(*V3_DESIGN_YEARS, HELD_OUT_YEAR), wind=True)
            end = data_last(REHEARSAL_YEAR)
            late_d, late_t = (raw_demand["ts_utc"] > end).to_numpy(), (raw_temp["ts_utc"] > end).to_numpy()
            raw_demand, raw_temp = raw_demand[~late_d].reset_index(drop=True), raw_temp[~late_t].reset_index(drop=True)
            available = runs["available_utc"] if "available_utc" in runs else runs["runtime"] + AVAILABILITY_LAG
            late_r = (available > end).to_numpy()
            runs = runs[~late_r].reset_index(drop=True)
            mlflow.log_params({"records_after_2024_dropped": int(late_d.sum() + late_t.sum()),
                               "forecast_points_after_2024_dropped": int(late_r.sum()),
                               "rehearsal_run_id": run0.info.run_id})
            first_day, last_day = replay_days(REHEARSAL_YEAR)
            inputs = {"chain": chain, "raw_demand": raw_demand, "raw_temp": raw_temp, "runs": runs, "data_end": end}
            r = _replay_v3(inputs, first_day, last_day, departure, None)
            with tempfile.TemporaryDirectory() as tmp:
                original = pd.read_parquet(c.download_artifacts(run0.info.run_id, "frames/hourly.parquet", tmp))
            summary, cells = mrun.compare_hourly(original, r.hourly)
            summary["revised_records"] = (int(dq.data.params["hq_revised_records"])
                                          if "hq_revised_records" in dq.data.params else None)
            with tempfile.TemporaryDirectory() as tmp:
                cells.assign(utc=cells["utc"].astype(str)).to_parquet(Path(tmp) / "differences.parquet")
                mlflow.log_artifact(str(Path(tmp) / "differences.parquet"), artifact_path="frames")
            mlflow.log_text(json.dumps(jsonable(summary), indent=2, ensure_ascii=False), "results/evidence.json")
            mrun._set("status", "complete")
        except BaseException as e:
            mrun._set("error", type(e).__name__)
            raise
    print(f"v3 evidence run {run.info.run_id}: the 2024 rehearsal {run0.info.run_id} run again on data-quality run "
          f"{dq.info.run_id} ({summary['revised_records']} revised records): {summary['hours_differing']} of "
          f"{summary['hours']} hours differ, in columns {sorted(summary['differing']) or 'none'}. It decides nothing; "
          "the owner rules.")
    return 0
