"""v3's records, as every v3 gate reads them (docs/plan-v3.md, sections 10.2, 10.3 and 11.3).

- **The freeze in force** (`current_freeze`). Before `v3-rules-frozen` exists, every v3 run carries the freeze tag
  `v3-pre-freeze` and no freeze commit. Once it exists, no run is pre-freeze: a v3 run is under the tag, so its rule
  files and dependencies must be the tag's (monitor/run.py `check_freeze` on v3's tag), or it is refused. A tag named
  like v3's freeze that config.FREEZES does not list is refused either way.
- **The tag's pins** (`pinned_commits`, `first_pin`). `pinned_commits` reads the successive commits `PINS` gives the
  tag in the history of tests/test_tags.py up to a head, as the history test reads v2's tag's pins
  (tests/test_departure_record.py `pins_at`, `pinned_freezes`), every parent of a merge followed. "The commit
  `v3-rules-frozen` was first pinned to" (`first_pin`) is anchored across every ref, not only the head's history: the
  pin set by the earliest commit, among those of HEAD, `--all` and `--reflog` that set it, that no other such commit
  precedes; refused when two such histories set different pins, or when that pin is not an ancestor of HEAD. Before
  any pin, nothing has a first pin.
- **The v3 departures section** (`v3_section`, `rulings_v3`): v2's forms on v3's tag, and the two more of section
  11.3 (4): "<date>, the owner's ruling on 2025 (h): post hoc" or "(i)", which can only say post hoc, and "<date>, the
  owner's ruling on v2 first", which names a `v2`-branch commit by its full hash, is never resolved in v3's history
  and lifts (g) only.
- **v3's replay starts and the moved-tag scan** (`replay_starts_v3`, `earlier_freeze_commits_v3`, `ledger_v3_dict`):
  v2's readers on v3's run kind (`replay_v3`), tag and ledger (`docs/replay-ledger-v3.json`). A v3 run without a
  freeze tag is refused, never folded into v1's freeze as v2's readers fold it; a pre-freeze run is never a start
  under the tag nor a freeze commit of it. A run's tags can be changed after it ran, so before any run is kept or left
  out of the starts by its tags, a 2025 run under another tag than v3's is refused, a run whose tags say it completed
  is refused unless the store says it finished, a 2025 run without the gate record it logged at its opening is
  refused unless it demonstrably stopped while it opened (`opening_stopped`), and every run holding that record is
  reconciled with it, a start the post-opening bind stopped read by the trace the bind wrote (`_gate_of`).
- **The history as committed** (`NO_REPLACE`, `replacements_off`, `history_rewrites`). Replacement objects (`git
  replace`, refs/replace/*) make git show another object in place of a commit, a tree or a blob, so a walk, an
  ancestry or a tree read through them is not the committed one. Every git read here, in freeze_record.py and in
  monitor/run_v3.py runs with them off (GIT_NO_REPLACE_OBJECTS), and the gates' entry points run whole under
  `replacements_off`, so the v2 helpers they call (monitor/run.py, monitor/rules.py) read with them off too. Git still
  reads a grafts file then, so the 2025 gate and the freeze record's writer refuse outright while a grafts file or any
  replace ref exists (`history_rewrites`).

Every git read goes through `_git`, which reads paths.REPO_ROOT at call time.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

from valiwatt import paths
from valiwatt.config import FREEZES, HELD_OUT_YEAR, V3_FREEZE_TAG, V3_LEDGER, V3_PRE_FREEZE_TAG
from valiwatt.monitor import run as mrun
from valiwatt.monitor.run import HEX_RUN, START_TIME, FreezeError

KIND = "replay_v3"  # v3's replay runs; v2's readers filter on "replay" and never see them
PINS_FILE = "tests/test_tags.py"
# The ruling heads of v3's section (section 11.3 (4)), each read whole (mrun.section_entries: the bold head, without
# its final full stop). v2's form lifts (a) and (f); the (h) and (i) forms lift only their condition; "v2 first" (g).
V2_FORM = re.compile(r"\d{4}-\d{2}-\d{2}, the owner's ruling on 2025: (clean|post hoc)")
HI_FORM = re.compile(r"\d{4}-\d{2}-\d{2}, the owner's ruling on 2025 \(([hi])\): (clean|post hoc)")
V2_FIRST = re.compile(r"\d{4}-\d{2}-\d{2}, the owner's ruling on v2 first")
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")


class V3FreezeError(FreezeError):
    """What a v3 gate refuses."""


# Replacement objects off for every git read of v3's gates, anchors and walks (git's GIT_NO_REPLACE_OBJECTS).
NO_REPLACE = {"GIT_NO_REPLACE_OBJECTS": "1"}


def git_env() -> dict:
    """The environment of every git read of v3's records: this process's, replacement objects off."""
    return {**os.environ, **NO_REPLACE}


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=paths.REPO_ROOT, capture_output=True, text=True, check=False,
                          env=git_env())


@contextmanager
def replacements_off():
    """Replacement objects off (NO_REPLACE) in this process's environment for the block, each earlier value restored
    after it: every git the block runs, the v2 helpers' (monitor/run.py, monitor/rules.py) included, reads objects as
    committed. Also a decorator (each call enters it afresh)."""
    before = {k: os.environ.get(k) for k in NO_REPLACE}
    os.environ.update(NO_REPLACE)
    try:
        yield
    finally:
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def history_rewrites() -> list[str]:
    """What makes git show another history than the one committed, each named: every replace ref (refs/replace/*);
    GIT_REPLACE_REF_BASE set, which moves where git looks for them; a grafts file (`git rev-parse --git-path
    info/grafts`: the repository's info/grafts, the common directory's for a worktree, or GIT_GRAFT_FILE's). Empty
    when there is none. Replacement objects are off for v3's reads, a grafts file is not (git reads it with them off):
    the 2025 gate and the freeze record's writer refuse while this is not empty. A repository that cannot be read
    here is refused (V3FreezeError): an unread list of rewrites is not an empty one."""
    listed = _git("for-each-ref", "--format=%(refname)", "refs/replace/")
    grafts = _git("rev-parse", "--git-path", "info/grafts")
    if listed.returncode != 0 or grafts.returncode != 0:
        raise V3FreezeError(f"the replace refs and the grafts file cannot be read: "
                            f"{(listed.stderr or grafts.stderr).strip()}")
    out = [f"the replace ref {ref}" for ref in listed.stdout.split("\n") if ref]
    if os.environ.get("GIT_REPLACE_REF_BASE"):
        out.append(f"GIT_REPLACE_REF_BASE={os.environ['GIT_REPLACE_REF_BASE']!r}")
    path = Path(grafts.stdout.rstrip("\n"))
    path = path if path.is_absolute() else Path(paths.REPO_ROOT) / path
    if path.exists():
        out.append(f"the grafts file {path}")
    return out


def _commit_of(rev: str) -> str | None:
    out = _git("rev-parse", "-q", "--verify", f"{rev}^{{commit}}")
    return out.stdout.strip() or None if out.returncode == 0 else None


def _is_ancestor(a: str, b: str) -> bool:
    """a is b or an ancestor of b."""
    return bool(a) and bool(b) and _git("merge-base", "--is-ancestor", a, b).returncode == 0


def _resolve(prefix: str) -> str | None:
    """The full commit a prefix names in this history, None when it names none."""
    return _commit_of(prefix)


# ----------------------------------------------------------------------------------------------- the freeze in force

def unlisted_v3_tags() -> list[str]:
    """The tags named like v3's freeze (`v3-rules-frozen*`) that config.FREEZES does not list, sorted. v2's check
    lists `rules-frozen*` only, so it never sees them; this is v3's half of that refusal."""
    # One name per line: a tag's name holds no newline, but may hold other separators (U+2028) that split() cuts at.
    return sorted(t for t in _git("tag", "--list", f"{V3_FREEZE_TAG}*").stdout.split("\n") if t and t not in FREEZES)


def current_freeze() -> dict:
    """The freeze a v3 run is made under (section 10.3): {"freeze_tag", "freeze_commit", "rules_sha256"}.

    `v3-rules-frozen` absent: v3-pre-freeze, no freeze commit, the rules hash of the tree. Present: the tag's freeze,
    after monitor/run.py `check_freeze` on it (the rule files and the dependencies equal the tag's), so no v3 run is
    made under the tag on other rules; refused (V3FreezeError) when they differ. Refused either way when a tag named
    like v3's freeze is not listed in config.FREEZES."""
    from valiwatt.monitor.rules import rules_sha256

    unlisted = unlisted_v3_tags()
    if unlisted:
        raise V3FreezeError(f"freeze tags {unlisted} are not listed in config.FREEZES: a freeze the configuration does "
                            "not name is refused")
    if _commit_of(f"refs/tags/{V3_FREEZE_TAG}") is None:
        return {"freeze_tag": V3_PRE_FREEZE_TAG, "freeze_commit": "", "rules_sha256": rules_sha256()}
    try:
        return mrun.check_freeze(V3_FREEZE_TAG)
    except FreezeError as e:
        raise V3FreezeError(f"`{V3_FREEZE_TAG}` exists, so a v3 run is made under it, and its rules must be the "
                            f"tag's: {e}") from e


# ----------------------------------------------------------------------------------------------------- the pins

def _pins_at(commit: str) -> dict:
    """`PINS` as tests/test_tags.py holds it at `commit` (empty when the file or the literal is missing): the same
    reading as tests/test_departure_record.py `pins_at`."""
    out = _git("show", f"{commit}:{PINS_FILE}")
    if out.returncode != 0:
        return {}
    try:
        tree = ast.parse(out.stdout)
    except SyntaxError:
        return {}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(
            node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == "PINS" for t in targets) and node.value is not None:
            try:
                pins = ast.literal_eval(node.value)
            except ValueError:
                return {}
            return pins if isinstance(pins, dict) else {}
    return {}


def pinned_commits(tag: str = V3_FREEZE_TAG, head: str = "HEAD") -> list[str]:
    """The successive commits `PINS` gives `tag` in the history of tests/test_tags.py up to `head`, oldest first, a
    pin repeated by a later commit counted once (D4 of the slice contract; tests/test_departure_record.py
    `pinned_freezes`, the same on a history without merges). The history is read whole (`--full-history`, every
    parent of a merge followed) in topological order, parents first: a merge that keeps a side of the file without
    the pin never hides the commit that pinned it first. A shallow clone, or a history that cannot be read, is
    refused: pins it cannot see are not absent."""
    if _git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        raise V3FreezeError("a shallow clone: the pins of the freeze tag are read from the full history")
    log = _git("log", "--full-history", "--topo-order", "--reverse", "--format=%H", head, "--", PINS_FILE)
    if log.returncode != 0:
        raise V3FreezeError(f"the history of {PINS_FILE} up to {head} cannot be read: {log.stderr.strip()}")
    out: list[str] = []
    for commit in log.stdout.split():
        pin = _pins_at(commit).get(tag)
        if pin and (not out or out[-1] != pin):
            out.append(pin)
    return out


# Where the anchors (the first pin here, the freeze record's adding commit in freeze_record.py) are read beside the
# head: every ref under refs/ and HEAD (`--all`), and every reflog (`--reflog`), every worktree's included.
ANCHOR_REVS = ("--all", "--reflog")


def pin_settings(tag: str = V3_FREEZE_TAG, head: str = "HEAD") -> list[dict]:
    """Every commit of `head`'s history, of every ref the repository holds (`--all`: branches, remote-tracking refs,
    tags, the stash, any other namespace) and of every reflog (`--reflog`: a commit a reset, an amend or a moved ref
    left behind, still recorded by the repository) that sets `PINS[tag]` in tests/test_tags.py: its pin is set and no
    parent's pin is that one (a merge carrying a side's pin sets nothing). Oldest first (topological order), each
    {"commit", "pin"}. Read whole (`--full-history`). A shallow clone, or a history that cannot be read, is refused."""
    if _git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        raise V3FreezeError("a shallow clone: the pins of the freeze tag are read from the full history")
    log = _git("log", "--full-history", "--topo-order", "--reverse", "--format=%H %P", head, *ANCHOR_REVS, "--",
               PINS_FILE)
    if log.returncode != 0:
        raise V3FreezeError(f"the history of {PINS_FILE} across the repository's refs and reflogs cannot be read: "
                            f"{log.stderr.strip()}")
    pins: dict[str, str | None] = {}

    def pin_at(commit: str) -> str | None:
        if commit not in pins:
            pins[commit] = _pins_at(commit).get(tag) or None
        return pins[commit]

    out = []
    for line in log.stdout.split("\n"):
        if not line:
            continue
        commit, *parents = line.split()
        pin = pin_at(commit)
        if pin and all(pin_at(p) != pin for p in parents):
            out.append({"commit": commit, "pin": pin})
    return out


def first_pin(tag: str = V3_FREEZE_TAG, head: str = "HEAD") -> str:
    """The commit `tag` was first pinned to (section 11.3 (3)): the rules hash every run of the freeze record must
    carry is the rules hash at it, and the walk of (a) starts from it. Anchored across every ref and reflog, never the
    head's history alone (`pin_settings`): the pin set by the earliest of the root settings, those no other setting
    precedes in history. Refused (V3FreezeError): before any pin; when root settings disagree (a sibling history, kept
    by any ref or left in a reflog, that pinned the tag elsewhere first: which pin is the original cannot be read, and
    the head's history alone would choose its own); when the original pin is not `head` or an ancestor of it."""
    settings = pin_settings(tag, head)
    if not settings:
        raise V3FreezeError(f"`{tag}` is not pinned yet: pin the tag in {PINS_FILE} first")
    roots = [s for s in settings if not any(o["commit"] != s["commit"] and _is_ancestor(o["commit"], s["commit"])
                                            for o in settings)]
    if len({s["pin"] for s in roots}) > 1:
        raise V3FreezeError(f"`{tag}` was first pinned on more than one history, to different commits: "
                            + "; ".join(f"{s['commit'][:12]} pins {s['pin'][:12]}" for s in roots)
                            + ": the original pin is the one every history across the repository's refs and reflogs "
                              "agrees on, and these do not agree")
    original = roots[0]["pin"]
    if not _is_ancestor(original, head):
        raise V3FreezeError(f"`{tag}` was first pinned to {original[:12]} (set by {roots[0]['commit'][:12]}), which is "
                            f"not {head} or an ancestor of it")
    return original


# ------------------------------------------------------------------------------------------ the departures section

def v3_section(agents: str | None = None) -> str:
    """The v3 departures section of AGENTS.md ("" when the heading is absent). agents: the file's text; None reads
    paths.REPO_ROOT's."""
    text = agents if agents is not None else (paths.REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    return mrun.recorded_departures(text, tag=V3_FREEZE_TAG)


def rulings_v3(section: str, resolve=None) -> list[dict]:
    """Every ruling entry of the v3 section, in section order (section 11.3 (4)), each {"kind" (v2_form, h, i or
    v2_first), "condition" ("a_f", "h", "i" or "g"), "head", "day", "outcome" ("clean", "post_hoc"; None for v2
    first), "commit" (the full commit), "start" (its start time, "" when none)}. Departure entries are not rulings
    (mrun.departure_record reads them).

    Refused (V3FreezeError, naming the entry): an (h) or (i) entry that says clean; a v2-form, (h) or (i) entry that
    does not name exactly one commit, by at least 12 characters, that `resolve` turns into a commit (default: this
    history); an entry naming more than one start; an (h), (i) or v2-first entry naming a start (only v2's form rules
    on a stopped start); a v2-first entry naming anything but exactly one commit by its full 40 characters, which is
    never resolved here: it names a `v2`-branch commit that only (g) reads."""
    resolve = resolve or _resolve
    out = []
    for head, text in mrun.section_entries(section):
        head = head or ""
        if m := V2_FORM.fullmatch(head):
            kind, condition, outcome = "v2_form", "a_f", m.group(1)
        elif m := HI_FORM.fullmatch(head):
            kind = condition = m.group(1)
            outcome = m.group(2)
            if outcome == "clean":
                raise V3FreezeError(f"the ruling entry {head!r} says clean: ({kind}) can only be ruled post hoc "
                                    "(docs/plan-v3.md, section 11.3 (4))")
        elif V2_FIRST.fullmatch(head):
            kind, condition, outcome = "v2_first", "g", None
        else:
            continue
        starts = sorted(set(START_TIME.findall(text)))
        if len(starts) > 1:
            raise V3FreezeError(f"the ruling entry {head!r} names {len(starts)} starts: at most one")
        if starts and kind != "v2_form":
            raise V3FreezeError(f"the ruling entry {head!r} names a start: only an entry in v2's form rules on a "
                                "stopped start")
        runs = HEX_RUN.findall(text)
        if kind == "v2_first":
            if len(runs) != 1 or not FULL_COMMIT.fullmatch(runs[0]):
                raise V3FreezeError(f"the ruling entry {head!r} names {runs}: it names the one `v2`-branch commit it "
                                    "rules on by its full 40 characters, and nothing else")
            commit = runs[0]
        else:
            commits = set()
            for run in runs:
                full = resolve(run) if 12 <= len(run) <= 40 else None
                if full is None:
                    raise V3FreezeError(f"the ruling entry {head!r} names {run}, which is no commit of this history "
                                        "by at least 12 characters (a start is named by its start time)")
                commits.add(full)
            if len(commits) != 1:
                raise V3FreezeError(f"the ruling entry {head!r} names {len(commits)} commits: it names the one "
                                    "commit it applies to")
            commit = commits.pop()
        out.append({"kind": kind, "condition": condition, "head": head, "day": head[:10],
                    "outcome": None if outcome is None else outcome.replace(" ", "_"), "commit": commit,
                    "start": starts[0] if starts else ""})
    return out


# --------------------------------------------------------------------------------- the starts and the moved tag

def _freeze_tag_of(run) -> str:
    """The freeze tag of a stored v3 replay run: v3's tag or v3-pre-freeze; anything else, an absent or empty tag
    included, is refused (never folded into v1's freeze, as v2's readers fold it)."""
    tag = run.data.tags.get("freeze_tag") or ""
    if tag not in (V3_FREEZE_TAG, V3_PRE_FREEZE_TAG):
        raise V3FreezeError(f"v3 replay run {run.info.run_id} carries the freeze tag {tag!r}: a v3 run is under "
                            f"`{V3_FREEZE_TAG}` or `{V3_PRE_FREEZE_TAG}`")
    return tag


def ledger_v3_dict() -> dict:
    """The committed ledger-v3 (`docs/replay-ledger-v3.json`) parsed as {"replays": [...]}, for
    mrun.ledger_freeze_commits and mrun.check_tag_not_moved; absent: no entries. Read here, not through the ledger's
    writer (report/ledger_v3.py), so what the gate reads stays among the rule files. A file that cannot be read, or an
    entry without a run ID or a freeze commit, or under another freeze tag than v3's, is refused: an unreadable record
    of earlier replays is not an empty one."""
    path = paths.REPO_ROOT / V3_LEDGER
    if not path.exists():
        return {"replays": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise V3FreezeError(f"{V3_LEDGER} cannot be read: {e}") from e
    if not isinstance(data, dict) or list(data) != ["replays"] or not isinstance(data["replays"], list):
        raise V3FreezeError(f"{V3_LEDGER} must hold one key, `replays`, a list")
    for e in data["replays"]:
        if not isinstance(e, dict) or not e.get("run_id") or not e.get("freeze_commit") \
                or e.get("freeze_tag") != V3_FREEZE_TAG:
            raise V3FreezeError(f"{V3_LEDGER}: an entry without a run ID or a freeze commit, or not under "
                                f"`{V3_FREEZE_TAG}`: {e!r}")
    return {"replays": data["replays"]}


def _bind_explains(run, gate: dict, artifacts: list[str]) -> bool:
    """`run`'s git_commit differs from its gate record's head_commit because the post-opening bind stopped it
    (monitor/run_v3.py `_bind_to_gate_v3`: HEAD moved between the gate and the run's opening, so the run recorded
    another commit than the one its gate passed on), and its trace is the one that bind leaves, intact: tag
    stopped_at `bind`, bind_gate_commit the record's head_commit, bind_run_commit its own git_commit, status tag
    started, stage opened, error FreezeError, never FINISHED, no metric and no artifact but the gate record. Such a
    start stopped before reading any input, and is adjudicated under (f) like any other stopped start."""
    t = run.data.tags
    return (t.get("stopped_at") == "bind" and bool(gate.get("head_commit"))
            and t.get("bind_gate_commit") == gate.get("head_commit") and bool(t.get("git_commit"))
            and t.get("bind_run_commit") == t.get("git_commit") and t.get("status") == "started"
            and t.get("stage") == "opened" and t.get("error") == "FreezeError" and run.info.status != "FINISHED"
            and not run.data.metrics and list(artifacts) == [mrun.GATE_RECORD])


def opening_stopped(run, artifacts: list[str]) -> bool:
    """`run` (a 2025 `replay_v3` run, with `artifacts`) demonstrably stopped while it opened, before it logged its gate
    record and before it read any input (monitor/run_v3.py `run_replay_v3`: the run is created with tags status started
    and stage opened, and logs results/held_out_gate.json before anything else): status tag started, stage opened, no
    `stopped_at` (the post-opening bind stops a run after its gate record is logged), never FINISHED, no metric and no
    artifact at all. The one shape of a 2025 run that holds no gate record."""
    t = run.data.tags
    return (t.get("status") == "started" and t.get("stage") == "opened" and not t.get("stopped_at")
            and run.info.status != "FINISHED" and not run.data.metrics and not list(artifacts))


def _gate_of(c, run, tag: str, artifacts: list[str]) -> dict | None:
    """The gate record `run` (a `replay_v3` run under the freeze tag `tag`, with `artifacts`) logged at its opening
    (results/held_out_gate.json; only a 2025 start logs one), after its tags are reconciled with it: a tag or a status
    can be changed after the run, and a start retagged out of the starts would leave a later start no earlier one, a
    completion made a stopped start would let a later start be the test of record. None for a run of another year,
    which logs none, and for a 2025 run that demonstrably stopped while it opened, before it logged its record or read
    any input (`opening_stopped`). Refused (V3FreezeError, (c)): a run whose status tag says it completed (status
    `complete`, set through the client only once the run is FINISHED) that is not FINISHED in the store (reopened,
    then ended FAILED or KILLED, or left RUNNING); a 2025 run under another freeze tag than `v3-rules-frozen` (a 2025
    replay runs only under it, so the tag was changed); any other 2025 run without its gate record (completed, or past
    its opening: the record it logged first is gone, and neither its tags nor its reading of the 2025 time can be
    checked); a gate record that cannot be read; a run holding one whose year is not 2025, whose freeze tag is not
    v3's, whose git_commit is not the record's head_commit (unless the post-opening bind stopped it and its intact
    trace says so, `_bind_explains`) or whose freeze_commit is not the record's."""
    t, rid = run.data.tags, run.info.run_id
    if t.get("status") == "complete" and run.info.status != "FINISHED":
        raise V3FreezeError(f"(c) v3 replay run {rid}'s status tag says it completed (stage {t.get('stage')!r}, "
                            f"{'holding' if 'results/replay.json' in artifacts else 'without'} results/replay.json), "
                            f"yet it is {run.info.status} in the store: its own record of its completion and the store "
                            "disagree, and a completion never becomes a stopped start")
    if t.get("year") == str(HELD_OUT_YEAR) and tag != V3_FREEZE_TAG:
        raise V3FreezeError(f"(c) v3 replay run {rid} of {HELD_OUT_YEAR} carries the freeze tag {tag!r}: a "
                            f"{HELD_OUT_YEAR} replay runs only under `{V3_FREEZE_TAG}`, so its tag was changed after "
                            "it ran, and a run is never left out of the starts by a changed tag")
    if mrun.GATE_RECORD not in artifacts:
        if t.get("year") != str(HELD_OUT_YEAR) or opening_stopped(run, artifacts):
            return None
        raise V3FreezeError(f"(c) v3 replay run {rid} of {HELD_OUT_YEAR} holds no gate record {mrun.GATE_RECORD}, yet "
                            f"it is {run.info.status} in the store, status tag {t.get('status')!r}, stage "
                            f"{t.get('stage')!r}, with {len(artifacts)} artifact(s) and {len(run.data.metrics)} "
                            "metric(s): only a start stopped while it opened, before it logged that record or read "
                            "any input, holds none; the record it logged first is gone, and a run is never kept or "
                            "left out of the starts, nor its reading of the 2025 time skipped, without it")
    try:
        gate = mrun._load_json_artifact(c, rid, mrun.GATE_RECORD)
    except Exception as e:  # noqa: BLE001 - an unreadable gate record reconciles nothing: the run is refused
        raise V3FreezeError(f"(c) v3 replay run {rid}'s gate record {mrun.GATE_RECORD} cannot be read: {e}") from e
    if not isinstance(gate, dict):
        raise V3FreezeError(f"(c) v3 replay run {rid}'s gate record {mrun.GATE_RECORD} is not a record: {gate!r}")
    want = {"year": str(HELD_OUT_YEAR), "freeze_tag": V3_FREEZE_TAG, "git_commit": gate.get("head_commit"),
            "freeze_commit": gate.get("freeze_commit")}
    differ = {k: f"{k} {t.get(k)!r}, its gate record's {v!r}" for k, v in want.items() if not v or t.get(k) != v}
    if list(differ) == ["git_commit"] and _bind_explains(run, gate, artifacts):
        return gate
    if differ:
        raise V3FreezeError(f"(c) v3 replay run {rid}'s tags disagree with the gate record it logged at its opening: "
                            + "; ".join(differ.values()) + ": its tags were changed after it ran, and a run is never "
                            "left out of the starts by a changed tag")
    return gate


def replay_starts_v3(year: int, client=None, ledger: dict | None = None) -> list[dict]:
    """The one reader of v3's replay starts (ledger-v3's `starts` and the 2025 gate call it): every `replay_v3` run of
    `year` under `v3-rules-frozen` in the store, whatever its status, deleted included, plus every ledger-v3 entry of
    that year the store lacks, by start time; each start in mrun.replay_starts' shape. A pre-freeze run is no start
    under the tag; a run without a freeze tag is refused. Before any run is kept or left out by its tags, a run whose
    status tag says it completed is refused unless it is FINISHED in the store, a 2025 run under another tag than v3's
    is refused, a 2025 run without its gate record is refused unless it demonstrably stopped while it opened, and every
    run's gate record is reconciled with its tags (`_gate_of`). Each start also says whether it is such a run
    ("opening_stopped": it holds no gate record, by its shape; a ledger entry the store lacks is not one).
    ledger: the parsed ledger-v3; None, the committed file."""
    from valiwatt import tracking

    c = client or tracking.client()
    out = []
    for r in mrun._replay_runs(c, kind=KIND):
        t, tag = r.data.tags, _freeze_tag_of(r)
        artifacts = mrun._artifact_paths(c, r.info.run_id)
        gate = _gate_of(c, r, tag, artifacts)
        if tag != V3_FREEZE_TAG or t.get("year") != str(year):
            continue
        out.append({"run_id": r.info.run_id, "started_utc": mrun._utc(r.info.start_time),
                    "freeze_commit": t.get("freeze_commit", ""), "git_commit": t.get("git_commit", ""),
                    "standing": t.get("standing", ""),
                    "finished": r.info.status == "FINISHED" and t.get("status") == "complete",
                    "running": r.info.status == "RUNNING", "stage": t.get("stage", ""), "error": t.get("error", ""),
                    "gate": gate, "opening_stopped": gate is None and opening_stopped(r, artifacts),
                    "holds_results": bool(r.data.metrics) or any(a != mrun.GATE_RECORD for a in artifacts)})
    stored = {s["run_id"] for s in out}
    for e in (ledger if ledger is not None else ledger_v3_dict())["replays"]:
        if e.get("freeze_tag") == V3_FREEZE_TAG and int(e.get("year", year)) == year and e["run_id"] not in stored:
            out.append({"run_id": e["run_id"], "started_utc": e.get("started_utc", ""),
                        "freeze_commit": e["freeze_commit"], "git_commit": "", "standing": e.get("standing", ""),
                        "finished": True, "running": False, "stage": "", "error": "", "gate": None,
                        "opening_stopped": False, "holds_results": True})
    return sorted(out, key=lambda s: (s["started_utc"], s["run_id"]))


def earlier_freeze_commits_v3(client=None) -> dict[str, list[str]]:
    """The freeze commit of every `replay_v3` run in the store under `v3-rules-frozen`, of every year the freeze
    replays (2024 and 2025), whatever its status, deleted runs included, with their run IDs: the store's half of
    mrun.check_tag_not_moved for v3 (the committed ledger-v3's half is `ledger_v3_dict`). Pre-freeze runs are under no
    freeze commit and are left out; a run without a freeze tag is refused."""
    from valiwatt import tracking

    c = client or tracking.client()
    years = {str(y) for y in FREEZES[V3_FREEZE_TAG]["years"]}
    out: dict[str, list[str]] = {}
    for r in mrun._replay_runs(c, kind=KIND):
        if _freeze_tag_of(r) == V3_FREEZE_TAG and r.data.tags.get("year") in years:
            out.setdefault(r.data.tags.get("freeze_commit", ""), []).append(r.info.run_id)
    return out
