"""v3's records, as every v3 gate reads them (docs/plan-v3.md, sections 10.2, 10.3 and 11.3).

- **The freeze in force** (`current_freeze`). Before `v3-rules-frozen` exists, every v3 run carries the freeze tag
  `v3-pre-freeze` and no freeze commit. Once it exists, no run is pre-freeze: a v3 run is under the tag, so its rule
  files and dependencies must be the tag's (monitor/run.py `check_freeze` on v3's tag), or it is refused. A tag named
  like v3's freeze that config.FREEZES does not list is refused either way.
- **The tag's pins** (`pinned_commits`, `first_pin`). "The commit `v3-rules-frozen` was first pinned to" is the first
  commit `PINS` gives the tag in the history of tests/test_tags.py, read as the history test reads v2's tag's pins
  (tests/test_departure_record.py `pins_at`, `pinned_freezes`), every parent of a merge followed. Before any pin,
  nothing has a first pin.
- **The v3 departures section** (`v3_section`, `rulings_v3`): v2's forms on v3's tag, and the two more of section
  11.3 (4): "<date>, the owner's ruling on 2025 (h): post hoc" or "(i)", which can only say post hoc, and "<date>, the
  owner's ruling on v2 first", which names a `v2`-branch commit by its full hash, is never resolved in v3's history
  and lifts (g) only.
- **v3's replay starts and the moved-tag scan** (`replay_starts_v3`, `earlier_freeze_commits_v3`, `ledger_v3_dict`):
  v2's readers on v3's run kind (`replay_v3`), tag and ledger (`docs/replay-ledger-v3.json`). A v3 run without a
  freeze tag is refused, never folded into v1's freeze as v2's readers fold it; a pre-freeze run is never a start
  under the tag nor a freeze commit of it.

Every git read goes through `_git`, which reads paths.REPO_ROOT at call time.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess

from valiwatt import paths
from valiwatt.config import FREEZES, V3_FREEZE_TAG, V3_LEDGER, V3_PRE_FREEZE_TAG
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


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=paths.REPO_ROOT, capture_output=True, text=True, check=False)


def _commit_of(rev: str) -> str | None:
    out = _git("rev-parse", "-q", "--verify", f"{rev}^{{commit}}")
    return out.stdout.strip() or None if out.returncode == 0 else None


def _resolve(prefix: str) -> str | None:
    """The full commit a prefix names in this history, None when it names none."""
    return _commit_of(prefix)


# ----------------------------------------------------------------------------------------------- the freeze in force

def unlisted_v3_tags() -> list[str]:
    """The tags named like v3's freeze (`v3-rules-frozen*`) that config.FREEZES does not list, sorted. v2's check
    lists `rules-frozen*` only, so it never sees them; this is v3's half of that refusal."""
    return sorted(t for t in _git("tag", "--list", f"{V3_FREEZE_TAG}*").stdout.split() if t and t not in FREEZES)


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


def first_pin(tag: str = V3_FREEZE_TAG, head: str = "HEAD") -> str:
    """The commit `tag` was first pinned to (section 11.3 (3)): the rules hash every run of the freeze record must
    carry is the rules hash at it, and the walk of (a) starts from it. Refused (V3FreezeError) before any pin."""
    pins = pinned_commits(tag, head)
    if not pins:
        raise V3FreezeError(f"`{tag}` is not pinned yet: pin the tag in {PINS_FILE} first")
    return pins[0]


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


def replay_starts_v3(year: int, client=None, ledger: dict | None = None) -> list[dict]:
    """The one reader of v3's replay starts (ledger-v3's `starts` and the 2025 gate call it): every `replay_v3` run of
    `year` under `v3-rules-frozen` in the store, whatever its status, deleted included, plus every ledger-v3 entry of
    that year the store lacks, by start time; each start in mrun.replay_starts' shape. A pre-freeze run is no start
    under the tag; a run without a freeze tag is refused. ledger: the parsed ledger-v3; None, the committed file."""
    from valiwatt import tracking

    c = client or tracking.client()
    out = []
    for r in mrun._replay_runs(c, kind=KIND):
        t = r.data.tags
        if _freeze_tag_of(r) != V3_FREEZE_TAG or t.get("year") != str(year):
            continue
        artifacts = mrun._artifact_paths(c, r.info.run_id)
        out.append({"run_id": r.info.run_id, "started_utc": mrun._utc(r.info.start_time),
                    "freeze_commit": t.get("freeze_commit", ""), "git_commit": t.get("git_commit", ""),
                    "standing": t.get("standing", ""),
                    "finished": r.info.status == "FINISHED" and t.get("status") == "complete",
                    "running": r.info.status == "RUNNING", "stage": t.get("stage", ""), "error": t.get("error", ""),
                    "gate": mrun._load_json_artifact(c, r.info.run_id, mrun.GATE_RECORD)
                    if mrun.GATE_RECORD in artifacts else None,
                    "holds_results": bool(r.data.metrics) or any(a != mrun.GATE_RECORD for a in artifacts)})
    stored = {s["run_id"] for s in out}
    for e in (ledger if ledger is not None else ledger_v3_dict())["replays"]:
        if e.get("freeze_tag") == V3_FREEZE_TAG and int(e.get("year", year)) == year and e["run_id"] not in stored:
            out.append({"run_id": e["run_id"], "started_utc": e.get("started_utc", ""),
                        "freeze_commit": e["freeze_commit"], "git_commit": "", "standing": e.get("standing", ""),
                        "finished": True, "running": False, "stage": "", "error": "", "gate": None,
                        "holds_results": True})
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
