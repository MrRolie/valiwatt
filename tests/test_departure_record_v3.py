"""AGENTS.md's record of the departures from v3's freeze agrees with the history (docs/plan-v3.md, sections 10.3 and
11.3 (4)), and v1's and v2's records stay those of the fork.

Before `v3-rules-frozen` exists, no ledger-v3 entry exists, the v3 section names no commit, and `v3-fork` and
`plan-v3` are in the history. Once it exists, its successive commits are read from the history of its pin in
tests/test_tags.py, and every commit from the first of them to the head that touches a rule file or a dependency is
read one by one: each must be named by the departure entry of the tag's move it belongs to (monitor/run.py
`departure_record`, v2's one rule), and none may follow the tag's current commit. The owner's ruling entries take
v2's form, or the (h) or (i) form, which can only say post hoc, each naming a commit of the history; or the v2-first
form, naming by its full 40 characters a `v2`-branch commit that is never looked up in v3's history.

A shallow clone fails loudly. Scratch repositories (tests/records_v3_world.py) for the breaks."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from records_v3_world import RULE_FILE, agents, commit, make_repo, pin, tag
from test_departure_record import (
    DEPENDENCIES,
    HEX_RUN,
    V1_HEADING,
    V2_HEADING,
    HistoryMissing,
    changes,
    commit_of,
    entries_of,
    git,
    is_ancestor,
    pins_at,
    require_full_history,
    section_of,
)
from valiwatt import records_v3
from valiwatt.config import V3_FORK_TAG, V3_FREEZE_TAG, V3_LEDGER, V3_PLAN_TAG
from valiwatt.monitor import run as mrun
from valiwatt.monitor.rules import _is_rule
from valiwatt.paths import REPO_ROOT

V3_HEADING = "## Departures from the v3 frozen rules"
PINS_FILE = "tests/test_tags.py"
FULL = re.compile(r"[0-9a-f]{40}")


def pinned(root: Path, head: str, tag_name: str = V3_FREEZE_TAG) -> list[str]:
    """F0 .. Fn: the successive commits `PINS` gives `tag_name` in the history of tests/test_tags.py."""
    out: list[str] = []
    for c in git(root, "log", "--reverse", "--format=%H", head, "--", PINS_FILE).splitlines():
        p = pins_at(root, c).get(tag_name)
        if p and (not out or out[-1] != p):
            out.append(p)
    return out


def ruling_kind(head: str | None) -> tuple[str | None, str | None]:
    """(kind, outcome) of a ruling head of v3's section (records_v3's forms), (None, None) for any other head."""
    for kind, pattern in (("v2_form", records_v3.V2_FORM), ("hi", records_v3.HI_FORM),
                          ("v2_first", records_v3.V2_FIRST)):
        if m := pattern.fullmatch(head or ""):
            return kind, (m.group(m.lastindex) if m.lastindex else None)
    return None, None


def v3_unfrozen_problems(root: Path, section: str | None, entries: list[dict], head: str = "HEAD") -> list[str]:
    """Before `v3-rules-frozen` exists: no ledger-v3 entry, the v3 section names no commit, and `v3-fork` and `plan-v3`
    in the history of `head`."""
    require_full_history(root)
    problems = []
    if section is None:
        problems.append(f"AGENTS.md has no line {V3_HEADING!r}")
    elif HEX_RUN.findall(section):
        problems.append(f"the v3 section names {HEX_RUN.findall(section)} before `{V3_FREEZE_TAG}` exists")
    if entries:
        problems.append(f"{V3_LEDGER} holds entries before `{V3_FREEZE_TAG}` exists: {[e['run_id'] for e in entries]}")
    for name in (V3_FORK_TAG, V3_PLAN_TAG):
        c = commit_of(root, f"refs/tags/{name}")
        if c is None:
            raise HistoryMissing(f"no `{name}` tag in this clone: fetch the tags with the full history")
        if not is_ancestor(root, c, head):
            problems.append(f"`{name}` ({c[:12]}) is not in the history of {head}")
    return problems


def v3_record_problems(root: Path, section: str, head: str = "HEAD") -> list[str]:
    """What is wrong with the v3 departures section against the history of `root` up to `head` (none: it agrees)."""
    require_full_history(root)
    tag_commit = commit_of(root, f"refs/tags/{V3_FREEZE_TAG}")
    if tag_commit is None:
        raise HistoryMissing(f"no `{V3_FREEZE_TAG}` tag in this clone: fetch the tags with the full history")
    head_commit = commit_of(root, head)
    problems: list[str] = []
    freezes = pinned(root, head)
    if not (freezes and tag_commit == freezes[-1]):
        if tag_commit != head_commit:
            return [f"the tag `{V3_FREEZE_TAG}` is on {tag_commit[:12]}, which is neither its last pin "
                    f"({freezes[-1][:12] if freezes else 'none'}) nor {head}: pin it in {PINS_FILE}"]
        freezes.append(tag_commit)
    for a, b in zip([*freezes, head_commit], [*freezes[1:], head_commit]):
        if a != b and not is_ancestor(root, a, b):
            return [f"freeze commit {a[:12]} does not come before {b[:12]}: a freeze moves forward only"]
    if git(root, "rev-list", "--merges", f"{freezes[0]}..{head}"):
        problems.append(f"a merge commit lies after the freeze commit {freezes[0][:12]}: the record is read one "
                        "commit at a time")

    for entry_head, text in entries_of(section):
        if entry_head is not None and mrun.DEPARTURE_HEAD.fullmatch(entry_head):
            continue
        kind, outcome = ruling_kind(entry_head)
        runs = HEX_RUN.findall(text)
        if kind == "v2_first":  # a v2-branch commit: never looked up in v3's history (section 11.3 (4))
            if len(runs) != 1 or not FULL.fullmatch(runs[0]):
                problems.append(f"the ruling entry {entry_head!r} names {runs}: it names the `v2`-branch commit it "
                                "rules on by its full 40 characters, and nothing else")
            continue
        named = set()
        for run in runs:
            full = commit_of(root, run) if len(run) <= 40 else None
            if len(run) < 12 or full is None:
                problems.append(f"the section names {run}, which is not a commit of this history by at least 12 "
                                "characters")
            else:
                named.add(full)
        if kind is not None:
            if kind == "hi" and outcome == "clean":
                problems.append(f"the ruling entry {entry_head!r} says clean: (h) and (i) can only be ruled post hoc")
            if not named:
                problems.append(f"the ruling entry {entry_head!r} names no commit")
            for c in sorted(named):
                if not is_ancestor(root, c, head):
                    problems.append(f"the ruling entry {entry_head!r} names {c[:12]}, which is not in the history "
                                    f"of {head}")
        elif runs:
            problems.append(f"an entry names a commit but is neither a departure nor the owner's ruling: "
                            f"{entry_head or text.strip()[:60]!r}")

    for c, paths in changes(root, freezes[-1], head).items():
        hit = sorted(p for p in paths if _is_rule(p) or p in DEPENDENCIES)
        if hit:
            problems.append(f"commit {c[:12]} changed {hit} after the tag's commit {freezes[-1][:12]}: a departure "
                            "the tag and the section do not record")
    _, recorded = mrun.departure_record(section, freezes, resolve=lambda run: commit_of(root, run),
                                        is_ancestor=lambda a, b: is_ancestor(root, a, b),
                                        changes=lambda a, b: changes(root, a, b))
    return problems + recorded


def ledger_v3_entries(root: Path) -> list[dict]:
    path = root / V3_LEDGER
    return json.loads(path.read_text(encoding="utf-8"))["replays"] if path.exists() else []


def test_the_v3_record_agrees_with_the_history():
    section = section_of((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8"), V3_HEADING)
    if commit_of(REPO_ROOT, f"refs/tags/{V3_FREEZE_TAG}") is None:
        assert v3_unfrozen_problems(REPO_ROOT, section, ledger_v3_entries(REPO_ROOT)) == []
    else:
        assert section is not None, f"AGENTS.md has no line {V3_HEADING!r}"
        assert v3_record_problems(REPO_ROOT, section) == []


def test_the_v1_and_v2_sections_are_those_of_the_fork():
    """Section 10.1: v1's and v2's records are closed; on the v3 branch their sections in AGENTS.md are, word for
    word, those of `v3-fork`."""
    fork = git(REPO_ROOT, "show", f"{V3_FORK_TAG}:AGENTS.md") + "\n"
    here = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for heading in (V1_HEADING, V2_HEADING):
        assert section_of(here, heading) is not None and section_of(here, heading) == section_of(fork, heading), heading


# ---------------------------------------------------------------------------------------------- the breaks

NONE = "\n\nThe v3 departures.\n\nNone yet.\n"


def section_with(*entries: str) -> str:
    return NONE.replace("None yet.\n", "".join(entries))


def departure(*commits: str, head: str = "2027-02-02, a fix to the gate: a departure") -> str:
    return f"- **{head}.** Commits " + ", ".join(f"`{c[:12]}`" for c in commits) + ".\n"


def ruling(head: str, *commits: str) -> str:
    return f"- **{head}.** It applies to " + ", ".join(f"`{c}`" for c in commits) + ".\n"


@pytest.fixture
def frozen(tmp_path) -> tuple[Path, str]:
    """A scratch repository with `v3-rules-frozen` on F0, pinned at HEAD, and `v3-fork` and `plan-v3` before it: the
    empty record agrees (control), and before the tag the unfrozen record agrees."""
    repo = tmp_path / "repo"
    made = make_repo(repo, frozen=False)
    tag(repo, V3_FORK_TAG, made["f0"])
    tag(repo, V3_PLAN_TAG, made["f0"])
    assert v3_unfrozen_problems(repo, NONE, []) == []
    tag(repo, V3_FREEZE_TAG, made["f0"])
    pin(repo, V3_FREEZE_TAG, made["f0"])
    assert v3_record_problems(repo, NONE) == []
    return repo, made["f0"]


def test_before_the_v3_freeze_a_v3_record_fails(tmp_path):
    """Before the tag: a ledger-v3 entry, a v3 section naming a commit, no v3 section, or a head that does not descend
    from `plan-v3` each fail."""
    repo = tmp_path / "repo"
    made = make_repo(repo, frozen=False)
    tag(repo, V3_FORK_TAG, made["f0"])
    plan = commit(repo, {"docs/plan-v3.md": "plan\n"})
    tag(repo, V3_PLAN_TAG, plan)
    assert v3_unfrozen_problems(repo, NONE, []) == []
    assert v3_unfrozen_problems(repo, NONE, [{"run_id": "a" * 32}]) == [
        f"{V3_LEDGER} holds entries before `{V3_FREEZE_TAG}` exists: {['a' * 32]}"]
    assert v3_unfrozen_problems(repo, section_with(departure(plan)), []) == [
        f"the v3 section names {[plan[:12]]} before `{V3_FREEZE_TAG}` exists"]
    assert v3_unfrozen_problems(repo, None, []) == [f"AGENTS.md has no line {V3_HEADING!r}"]
    assert v3_unfrozen_problems(repo, NONE, [], head=made["f0"]) == [
        f"`{V3_PLAN_TAG}` ({plan[:12]}) is not in the history of {made['f0']}"]


def test_a_v3_rule_change_after_the_tag_unrecorded_fails(frozen):
    repo, f0 = frozen
    change = commit(repo, {RULE_FILE: "MIN = 5\n"})
    assert v3_record_problems(repo, NONE) == [
        f"commit {change[:12]} changed {[RULE_FILE]} after the tag's commit {f0[:12]}: a departure the tag and the "
        "section do not record"]


@pytest.mark.parametrize("path", DEPENDENCIES)
def test_a_v3_dependency_change_after_the_tag_unrecorded_fails(frozen, path):
    repo, f0 = frozen
    change = commit(repo, {path: "changed\n"})
    assert v3_record_problems(repo, NONE) == [
        f"commit {change[:12]} changed {[path]} after the tag's commit {f0[:12]}: a departure the tag and the section "
        "do not record"]


def test_a_recorded_v3_departure_passes(frozen):
    """A rule change, the tag moved to it and pinned at once, the entry naming both freeze commits: the record agrees
    (control); the same move without its entry fails, and records_v3 reads the move's pins (first pin F0)."""
    repo, f0 = frozen
    fix = commit(repo, {RULE_FILE: "MIN = 5\n"})
    tag(repo, V3_FREEZE_TAG, fix)
    pin(repo, V3_FREEZE_TAG, fix)
    assert v3_record_problems(repo, section_with(departure(f0, fix))) == []
    assert v3_record_problems(repo, NONE) == [
        f"the tag moved from {f0[:12]} to {fix[:12]}, but no departure entry records it"]
    assert pinned(repo, "HEAD") == [f0, fix]


def test_a_v2_first_ruling_naming_a_v2_branch_hash_passes_the_history_check(frozen):
    """Section 11.3 (4): the v2-first entry names a `v2`-branch commit by its full hash, which v3's history lacks: it is
    never looked up, so it passes; a v2-form entry naming the same hash fails (control that fires)."""
    repo, _ = frozen
    v2_commit = "abcdef0123456789abcdef0123456789abcdef01"
    assert commit_of(repo, v2_commit) is None
    assert v3_record_problems(repo, section_with(ruling("2027-03-03, the owner's ruling on v2 first", v2_commit))) == []
    head = "2027-03-03, the owner's ruling on 2025: post hoc"
    assert v3_record_problems(repo, section_with(ruling(head, v2_commit))) == [
        f"the section names {v2_commit}, which is not a commit of this history by at least 12 characters",
        f"the ruling entry {head!r} names no commit"]


def test_a_v2_first_ruling_with_a_short_hash_fails(frozen):
    repo, _ = frozen
    head = "2027-03-03, the owner's ruling on v2 first"
    v2_commit = "abcdef0123456789abcdef0123456789abcdef01"
    for named in ((v2_commit[:12],), (v2_commit, v2_commit[::-1])):
        assert v3_record_problems(repo, section_with(ruling(head, *named))) == [
            f"the ruling entry {head!r} names {list(named)}: it names the `v2`-branch commit it rules on by its full "
            "40 characters, and nothing else"]


@pytest.mark.parametrize("condition", ["h", "i"])
def test_a_clean_ruling_on_h_or_i_is_refused(frozen, condition):
    """Section 12, the 2025 gate: a "clean" ruling on (h) or (i) is refused, by the gate's reader of rulings
    (records_v3.rulings_v3) and by the history check; a "post hoc" one is read (control)."""
    repo, f0 = frozen
    post_hoc = f"2027-03-03, the owner's ruling on 2025 ({condition}): post hoc"
    clean = f"2027-03-03, the owner's ruling on 2025 ({condition}): clean"
    ok = section_with(ruling(post_hoc, f0[:12]))
    resolve = (lambda p: f0 if f0.startswith(p) else None)
    assert v3_record_problems(repo, ok) == []
    (r,) = records_v3.rulings_v3(ok, resolve=resolve)
    assert (r["kind"], r["condition"], r["outcome"], r["commit"]) == (condition, condition, "post_hoc", f0)
    bad = section_with(ruling(clean, f0[:12]))
    assert v3_record_problems(repo, bad) == [
        f"the ruling entry {clean!r} says clean: (h) and (i) can only be ruled post hoc"]
    with pytest.raises(records_v3.V3FreezeError, match=rf"\({condition}\) can only be ruled post hoc"):
        records_v3.rulings_v3(bad, resolve=resolve)


def test_a_v3_ruling_off_the_history_or_naming_no_commit_fails(frozen):
    repo, f0 = frozen
    base = git(repo, "rev-parse", "HEAD")
    side = commit(repo, {"README.md": "side\n"})
    git(repo, "checkout", "--quiet", "--detach", base)
    head = "2027-03-03, the owner's ruling on 2025 (i): post hoc"
    assert v3_record_problems(repo, section_with(ruling(head, side[:12]))) == [
        f"the ruling entry {head!r} names {side[:12]}, which is not in the history of HEAD"]
    assert v3_record_problems(repo, section_with(f"- **{head}.** It applies to the frozen code.\n")) == [
        f"the ruling entry {head!r} names no commit"]


def test_the_v3_section_is_read_under_its_own_heading_only(frozen):
    """records_v3.v3_section and the history test read the same section, under v3's heading, never v2's."""
    repo, f0 = frozen
    text = agents(departure(f0, f0), v2_entries=(departure(f0),))
    assert records_v3.v3_section(text) == section_of(text, V3_HEADING) == "\n\nThe v3 departures.\n\n" + departure(f0, f0)
