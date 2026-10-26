"""AGENTS.md's records of the departures from each freeze agree with the history.

v1, frozen by `rules-frozen` and closed at the `v1` tag. Its departures section names the commit the `rules-frozen`
tag moved to and the commits that made the departure. Those commits, without the tag's, must be exactly the commits
that changed a rule file (monitor/rules.py `_is_rule`) between the earlier freeze commit and the tag; and this
departure changed no dependency (`pyproject.toml`, `uv.lock`, which v1's freeze gate does not hash). The earlier
freeze commit is the one the ledger's plain v1 replays ran under (with several, the one the departure replays depart
from). At `v1`, the rule files and the dependencies are the tag's. v1's range ends at `v1`, so v2's changes after it
are not v1 departures.

v2, frozen by `rules-frozen-v2` (docs/plan-v2.md, sections 10.3 and 10.4). Before the tag exists, no ledger entry is
v2's, the v2 section names no commit and `v1` is in the history. Once it exists, its successive commits are read from
the history of its pin in tests/test_tags.py, and every commit from the first of them to the head that touches a rule
file or a dependency is read one by one, so a change since reverted is seen: each must be named by the departure entry
of the tag's move it belongs to, and none may follow the tag's current commit. Each departure entry names the commit
the tag moved from and the one it moved to; a commit it names that is neither fails. The owner's ruling entries on
2025 are read by the 2025 gate, not here: here each must name a commit of the history.

On the `v3` branch v2's record is closed at `v3-fork` (docs/plan-v3.md, section 10.1), as v1's is at `v1`: v2's
history is read up to the fork, with the tag's commit its pin as of the fork and the v2 section as the fork holds it,
so a later move of `rules-frozen-v2` on the `v2` branch, or v3's own rule changes, are no v2 departures. v3's record is
checked in tests/test_departure_record_v3.py.

The checks read the history, so a shallow clone fails them loudly instead of passing on history they cannot see; CI
checks out the full history (`fetch-depth: 0`, .github/workflows/checks.yml), as the public-safety scan needs too.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

from valiwatt.monitor import run as mrun
from valiwatt.monitor.rules import _is_rule
from valiwatt.paths import REPO_ROOT
from valiwatt.report import ledger
from valiwatt.report.lineage import COMMIT

DEPENDENCIES = ("pyproject.toml", "uv.lock")
V1_FREEZE, V1_END, V2_FREEZE = "rules-frozen", "v1", "rules-frozen-v2"
V2_END = "v3-fork"  # v2's checked range ends at the fork on the v3 branch (config.FREEZES["rules-frozen-v2"]["end"])
V1_HEADING = "## Departures from the frozen monitoring rules"
V2_HEADING = "## Departures from the v2 frozen rules"
PINS_FILE = "tests/test_tags.py"
# Any run a reader could take for a commit: 7 or more hexadecimal characters with a digit, or 12 or more.
HEX_RUN = re.compile(r"\b(?=[0-9a-f]*[0-9])[0-9a-f]{7,}\b|\b[0-9a-f]{12,}\b")
RULING = re.compile(r"\d{4}-\d{2}-\d{2}, the owner's ruling on 2025: (clean|post hoc)")


class HistoryMissing(RuntimeError):
    pass


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


def commit_of(root: Path, rev: str) -> str | None:
    out = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], cwd=root,
                         capture_output=True, text=True, check=False)
    return out.stdout.strip() or None


def is_ancestor(root: Path, a: str, b: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=root, check=False).returncode == 0


def require_full_history(root: Path) -> None:
    if git(root, "rev-parse", "--is-shallow-repository") == "true":
        raise HistoryMissing("shallow clone: the departure record is checked against the full history "
                             "(actions/checkout fetch-depth: 0)")


def changes(root: Path, a: str, b: str) -> dict[str, list[str]]:
    """Each commit in a..b with the paths it touched (renames as a deletion and an addition)."""
    out, commit = {}, None
    for line in git(root, "log", "--no-renames", "--format=C %H", "--name-only", f"{a}..{b}").splitlines():
        if line.startswith("C "):
            commit = line[2:]
            out[commit] = []
        elif line.strip():
            out[commit].append(line.strip())
    return out


def section_of(text: str, heading: str) -> str | None:
    """The section of AGENTS.md under `heading`, up to the next section; None when no line is that heading."""
    lines = text.split("\n")
    if heading not in lines:
        return None
    return ("\n" + "\n".join(lines[lines.index(heading) + 1:])).split("\n## ", 1)[0]


def agents_text(root: Path = REPO_ROOT) -> str:
    return (root / "AGENTS.md").read_text(encoding="utf-8")


# v1: the closed range rules-frozen .. v1.

def earlier_freeze(entries: list[dict], tag_commit: str) -> set[str]:
    """The freeze commit the departure departs from: the plain replays' one, or, with several, the one of the plain
    replays the departure replays under the tag name."""
    plain = {e["freeze_commit"] for e in entries if not e["departure"]}
    if len(plain) <= 1:
        return plain
    by_id = {e["run_id"]: e for e in entries}
    return {by_id[x]["freeze_commit"] for e in entries if e["departure"] and e["freeze_commit"] == tag_commit
            for x in e["departure_from"] if x in by_id and not by_id[x]["departure"]}


def departure_record_problems(root: Path, section: str, entries: list[dict], *, tag: str, head: str) -> list[str]:
    """What is wrong with v1's departures section against the history of `root` up to `head` (none: it agrees)."""
    require_full_history(root)
    tag_commit = commit_of(root, tag)
    if tag_commit is None:
        raise HistoryMissing(f"no `{tag}` tag in this clone: fetch the tags with the full history")
    problems = []
    if not is_ancestor(root, tag_commit, head):
        problems.append(f"the tag's commit {tag_commit[:12]} is not in the history of {head}")
    since = git(root, "diff", "--no-renames", "--name-only", tag_commit, head).splitlines()
    rules_since = sorted(p for p in since if _is_rule(p))
    if rules_since:
        problems.append(f"rule files changed after the tag's commit {tag_commit[:12]}: {rules_since}")
    deps_since = sorted(p for p in since if p in DEPENDENCIES)
    if deps_since:
        problems.append(f"dependencies changed after the tag's commit {tag_commit[:12]}: {deps_since}")

    named_prefixes = COMMIT.findall(section)
    if not named_prefixes:
        departures = [e["run_id"] for e in entries if e["departure"]]
        if departures:
            problems.append(f"the ledger holds departure replays {departures}, but the section names no commit")
        return problems

    named = set()
    for prefix in named_prefixes:
        full = commit_of(root, prefix)
        if full is None:
            problems.append(f"the section names {prefix}, which is no commit of this history")
        else:
            named.add(full)
    if tag_commit not in named:
        problems.append(f"the section does not name the tag's commit {tag_commit[:12]}")
    earlier = earlier_freeze(entries, tag_commit)
    if len(earlier) != 1:
        problems.append(f"the ledger gives no single earlier freeze commit: {sorted(c[:12] for c in earlier)}")
        return problems
    (earlier_commit,) = earlier
    if not is_ancestor(root, earlier_commit, tag_commit):
        problems.append(f"the earlier freeze commit {earlier_commit[:12]} is not in the history of the tag")
        return problems
    if git(root, "rev-list", "--merges", f"{earlier_commit}..{tag_commit}"):
        problems.append("a merge commit lies between the earlier freeze commit and the tag; attribute it by hand")
    touched = changes(root, earlier_commit, tag_commit)
    rule_commits = {c for c, paths in touched.items() if any(_is_rule(p) for p in paths)}
    dep_commits = sorted(c[:12] for c, paths in touched.items() if any(p in DEPENDENCIES for p in paths))
    if dep_commits:
        problems.append(f"the dependencies changed between {earlier_commit[:12]} and the tag, in {dep_commits}")
    unnamed = sorted(c[:12] for c in rule_commits - named - {tag_commit})
    extra = sorted(c[:12] for c in named - rule_commits - {tag_commit})
    if unnamed:
        problems.append(f"commits changed rule files after {earlier_commit[:12]} but the section does not name them: "
                        f"{unnamed}")
    if extra:
        problems.append(f"the section names commits that changed no rule file after {earlier_commit[:12]}: {extra}")
    return problems


@pytest.fixture(scope="module")
def recorded() -> tuple[str, list[dict]]:
    section = section_of(agents_text(), V1_HEADING)
    assert section is not None, f"AGENTS.md has no line {V1_HEADING!r}"
    return section, [e for e in ledger.read() if e["freeze_tag"] == V1_FREEZE]


def test_the_departures_section_names_exactly_the_commits_that_changed_the_rules(recorded):
    section, entries = recorded
    assert departure_record_problems(REPO_ROOT, section, entries, tag=V1_FREEZE, head=V1_END) == []


def test_before_any_departure_the_record_still_checks_the_rules_and_the_dependencies(recorded):
    """A section that names no commit is checked too: the rule files and the dependencies at `v1` are the tag's, and
    no replay in the ledger ran as a departure. On this history: true at the tag, and false at the earlier freeze
    commit, whose rule files the departure changed."""
    section, entries = recorded
    tag_commit = commit_of(REPO_ROOT, V1_FREEZE)
    plain = [e | {"freeze_commit": tag_commit} for e in entries if not e["departure"]]
    none = "\n\nNone yet.\n"
    assert departure_record_problems(REPO_ROOT, none, plain, tag=V1_FREEZE, head=V1_END) == []
    assert any("departure replays" in p
               for p in departure_record_problems(REPO_ROOT, none, entries, tag=V1_FREEZE, head=V1_END))
    (earlier,) = earlier_freeze(entries, tag_commit)
    assert any("rule files changed after the tag's commit" in p
               for p in departure_record_problems(REPO_ROOT, none, plain, tag=earlier, head=V1_END))


def test_a_section_missing_a_departure_commit_fails(recorded):
    section, entries = recorded
    tag_commit = commit_of(REPO_ROOT, V1_FREEZE)
    named = [p for p in COMMIT.findall(section) if not tag_commit.startswith(p)]
    assert named
    for prefix in named:
        problems = departure_record_problems(REPO_ROOT, section.replace(prefix, "a later commit"), entries,
                                             tag=V1_FREEZE, head=V1_END)
        assert any("does not name them" in p and prefix[:12] in p for p in problems), prefix


def test_a_section_naming_a_commit_that_changed_no_rule_fails(recorded):
    section, entries = recorded
    tag_commit = commit_of(REPO_ROOT, V1_FREEZE)
    (earlier,) = earlier_freeze(entries, tag_commit)
    quiet = [c for c, paths in changes(REPO_ROOT, earlier, tag_commit).items()
             if c != tag_commit and not any(_is_rule(p) for p in paths)]
    assert quiet  # the range holds report and docs commits
    problems = departure_record_problems(REPO_ROOT, section + f"\n- also `{quiet[0][:12]}`.\n", entries,
                                         tag=V1_FREEZE, head=V1_END)
    assert any("changed no rule file" in p and quiet[0][:12] in p for p in problems)


def scratch_clone(tmp_path: Path, *, shallow: bool = False) -> Path:
    dest = tmp_path / "clone"
    args = ["git", "clone", "--quiet", "--no-checkout"]
    if shallow:
        args += ["--depth", "1", REPO_ROOT.resolve().as_uri()]
    else:
        args += [str(REPO_ROOT)]
    subprocess.run([*args, str(dest)], check=True, capture_output=True)
    return dest


SCRATCH = ("-c", "user.name=scratch", "-c", "user.email=scratch@example.invalid", "-c", "commit.gpgsign=false",
           "-c", "tag.gpgsign=false")


def scratch_commit(clone: Path, *paths: str) -> str:
    """In the clone: a commit on top of HEAD that appends a line to each of `paths`."""
    for path in paths:
        with open(clone / path, "a", encoding="utf-8") as f:
            f.write("\n# scratch change\n")
        git(clone, "add", path)
    git(clone, *SCRATCH, "commit", "--quiet", "--no-verify", "-m", "scratch")
    return git(clone, "rev-parse", "HEAD")


def commit_on_tag(clone: Path, path: str) -> str:
    """In the clone: a commit on top of the v1 tag's commit that appends a line to `path`, and the tag moved to it."""
    tag_commit = commit_of(clone, V1_FREEZE)
    git(clone, "checkout", "--quiet", "--detach", tag_commit)
    new = scratch_commit(clone, path)
    git(clone, "tag", "--force", V1_FREEZE, new)
    return new


@pytest.mark.parametrize("path, fails", [("README.md", False), ("pyproject.toml", True)])
def test_a_dependency_change_in_the_departure_fails(tmp_path, recorded, path, fails):
    """On a scratch clone, the tag moves to a new commit that changes `path` and the section names it in place of the
    old tag: a README change is no departure (control), a pyproject.toml change in the range is refused."""
    section, entries = recorded
    clone = scratch_clone(tmp_path)
    old = commit_of(clone, V1_FREEZE)
    new = commit_on_tag(clone, path)
    assert old[:12] in section
    problems = departure_record_problems(clone, section.replace(old[:12], new[:12]), entries, tag=V1_FREEZE,
                                         head=new)
    if fails:
        assert len(problems) == 1 and "dependencies changed between" in problems[0] and new[:12] in problems[0]
    else:
        assert problems == []


def test_a_section_that_does_not_name_the_tag_commit_fails(recorded):
    section, entries = recorded
    tag_commit = commit_of(REPO_ROOT, V1_FREEZE)
    assert tag_commit[:12] in section
    problems = departure_record_problems(REPO_ROOT, section.replace(tag_commit[:12], "the tag"), entries,
                                         tag=V1_FREEZE, head=V1_END)
    assert any("does not name the tag's commit" in p and tag_commit[:12] in p for p in problems)


def test_a_dependency_change_after_the_tag_fails(tmp_path, recorded):
    """On a scratch clone, a commit after `v1` changes pyproject.toml and the tag stays put: a dependency change the
    section does not record."""
    section, entries = recorded
    clone = scratch_clone(tmp_path)
    tag_commit = commit_of(clone, V1_FREEZE)
    git(clone, "checkout", "--quiet", "--detach", V1_END)
    new = scratch_commit(clone, "pyproject.toml")
    assert commit_of(clone, V1_FREEZE) == tag_commit
    problems = departure_record_problems(clone, section, entries, tag=V1_FREEZE, head=new)
    assert problems == [f"dependencies changed after the tag's commit {tag_commit[:12]}: ['pyproject.toml']"]


def test_v1_is_checked_up_to_the_v1_tag_only(tmp_path, recorded):
    """v1's range ends at `v1`: a rule change after it (v2's work) is no v1 departure (control), and the same change
    read up to HEAD would be one."""
    section, entries = recorded
    clone = scratch_clone(tmp_path)
    git(clone, "checkout", "--quiet", "--detach", "HEAD")
    scratch_commit(clone, "src/valiwatt/monitor/rules.py")
    assert departure_record_problems(clone, section, entries, tag=V1_FREEZE, head=V1_END) == []
    assert any("rule files changed after the tag's commit" in p
               for p in departure_record_problems(clone, section, entries, tag=V1_FREEZE, head="HEAD"))


def test_a_shallow_clone_fails_loudly(tmp_path, recorded):
    section, entries = recorded
    with pytest.raises(HistoryMissing, match="shallow"):
        departure_record_problems(scratch_clone(tmp_path, shallow=True), section, entries, tag=V1_FREEZE,
                                  head=V1_END)


# v2: rules-frozen-v2, read from its pins, commit by commit.

def pins_at(root: Path, commit: str) -> dict[str, str]:
    """`PINS` as tests/test_tags.py holds it at `commit` (empty when the file or the literal is missing)."""
    out = subprocess.run(["git", "show", f"{commit}:{PINS_FILE}"], cwd=root, capture_output=True, text=True,
                         check=False)
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


def pinned_freezes(root: Path, head: str) -> list[str]:
    """F0 .. Fn: the successive commits `PINS` gives `rules-frozen-v2` in the history of tests/test_tags.py."""
    out: list[str] = []
    for commit in git(root, "log", "--reverse", "--format=%H", head, "--", PINS_FILE).splitlines():
        pin = pins_at(root, commit).get(V2_FREEZE)
        if pin and (not out or out[-1] != pin):
            out.append(pin)
    return out


def entries_of(section: str) -> list[tuple[str | None, str]]:
    """The section cut at each top-level bullet: (its bold head, without a final full stop, or None; its text). The
    text before the first bullet is an entry without a head."""
    out = []
    for part in re.split(r"(?m)^(?=- )", section):
        if part.strip():
            m = re.match(r"- \*\*(.+?)\*\*", part, re.S)
            out.append((" ".join(m.group(1).split()).removesuffix(".") if m else None, part))
    return out


def v2_unfrozen_problems(root: Path, section: str | None, entries: list[dict], head: str = "HEAD") -> list[str]:
    """Before `rules-frozen-v2` exists: no ledger entry is v2's, the v2 section names no commit, and `v1` is in the
    history of `head`."""
    require_full_history(root)
    v1 = commit_of(root, f"refs/tags/{V1_END}")
    if v1 is None:
        raise HistoryMissing(f"no `{V1_END}` tag in this clone: fetch the tags with the full history")
    problems = []
    if section is None:
        problems.append(f"AGENTS.md has no line {V2_HEADING!r}")
    elif HEX_RUN.findall(section):
        problems.append(f"the v2 section names {HEX_RUN.findall(section)} before `{V2_FREEZE}` exists")
    v2_entries = [e["run_id"] for e in entries if e.get("freeze_tag") == V2_FREEZE]
    if v2_entries:
        problems.append(f"the ledger holds entries under `{V2_FREEZE}` before the tag exists: {v2_entries}")
    if not is_ancestor(root, v1, head):
        problems.append(f"`{V1_END}` ({v1[:12]}) is not in the history of {head}")
    return problems


def v2_record_problems(root: Path, section: str, head: str = "HEAD", *, tag_commit: str | None = None) -> list[str]:
    """What is wrong with the v2 departures section against the history of `root` up to `head` (none: it agrees).
    tag_commit: the commit the tag is read on (the record closed at the fork: its pin as of the fork); None, the live
    tag's."""
    require_full_history(root)
    tag = tag_commit or commit_of(root, f"refs/tags/{V2_FREEZE}")
    if tag is None:
        raise HistoryMissing(f"no `{V2_FREEZE}` tag in this clone: fetch the tags with the full history")
    head_commit = commit_of(root, head)
    problems: list[str] = []

    # The freeze commits: every pin, then the tag at head when its pin is the next commit.
    freezes = pinned_freezes(root, head)
    if not (freezes and tag == freezes[-1]):
        if tag != head_commit:
            return [f"the tag `{V2_FREEZE}` is on {tag[:12]}, which is neither its last pin "
                    f"({freezes[-1][:12] if freezes else 'none'}) nor {head}: pin it in {PINS_FILE}"]
        freezes.append(tag)
    for a, b in zip([*freezes, head_commit], [*freezes[1:], head_commit]):
        if a != b and not is_ancestor(root, a, b):
            return [f"freeze commit {a[:12]} does not come before {b[:12]}: a freeze moves forward only"]
    if git(root, "rev-list", "--merges", f"{freezes[0]}..{head}"):
        problems.append(f"a merge commit lies after the freeze commit {freezes[0][:12]}: the record is read one "
                        "commit at a time")

    # The section: ruling entries set aside, departure entries read by the one rule (below), any other entry naming a
    # commit refused.
    for entry_head, text in entries_of(section):
        if entry_head is not None and mrun.DEPARTURE_HEAD.fullmatch(entry_head):
            continue
        named = set()
        for run in HEX_RUN.findall(text):
            full = commit_of(root, run) if len(run) <= 40 else None
            if len(run) < 12 or full is None:
                problems.append(f"the section names {run}, which is not a commit of this history by at least 12 "
                                "characters")
            else:
                named.add(full)
        if entry_head is not None and RULING.fullmatch(entry_head):
            if not named:
                problems.append(f"the ruling entry {entry_head!r} names no commit")
            for c in sorted(named):
                if not is_ancestor(root, c, head):
                    problems.append(f"the ruling entry {entry_head!r} names {c[:12]}, which is not in the history "
                                    f"of {head}")
        elif HEX_RUN.findall(text):
            problems.append(f"an entry names a commit but is neither a departure nor the owner's ruling on 2025: "
                            f"{entry_head or text.strip()[:60]!r}")

    # The walk after the tag's current commit: a rule or dependency change there is a departure nothing records.
    for c, paths in changes(root, freezes[-1], head).items():
        hit = sorted(p for p in paths if _is_rule(p) or p in DEPENDENCIES)
        if hit:
            problems.append(f"commit {c[:12]} changed {hit} after the tag's commit {freezes[-1][:12]}: a departure "
                            "the tag and the section do not record")
    # Each move of the tag, F(i-1) to Fi, against departure entry i: the one rule the replay gate and the report
    # read (monitor/run.py departure_record), on this clone's history.
    _, recorded = mrun.departure_record(section, freezes, resolve=lambda run: commit_of(root, run),
                                        is_ancestor=lambda a, b: is_ancestor(root, a, b),
                                        changes=lambda a, b: changes(root, a, b))
    return problems + recorded


def test_the_v2_record_agrees_with_the_history():
    """v2's record, closed at `v3-fork`: the history up to the fork, the tag read on its pin as of the fork, and the v2
    section as AGENTS.md held it there."""
    fork = commit_of(REPO_ROOT, f"refs/tags/{V2_END}")
    assert fork is not None, f"no `{V2_END}` tag: fetch the tags with the full history"
    section = section_of(git(REPO_ROOT, "show", f"{fork}:AGENTS.md") + "\n", V2_HEADING)
    if commit_of(REPO_ROOT, f"refs/tags/{V2_FREEZE}") is None:
        assert v2_unfrozen_problems(REPO_ROOT, section, ledger.read(), head=V2_END) == []
    else:
        assert section is not None, f"AGENTS.md has no line {V2_HEADING!r}"
        assert v2_record_problems(REPO_ROOT, section, V2_END, tag_commit=pins_at(REPO_ROOT, fork)[V2_FREEZE]) == []


NONE = ("\n\nThe rules are frozen by the tag. A ruling's head: \"<date>, the owner's ruling on 2025: clean\".\n\n"
        "None yet.\n")


def test_before_the_v2_freeze_a_v2_record_fails():
    """Before `rules-frozen-v2` exists: a ledger entry under it, a v2 section that names a commit, no v2 section, or
    a head that does not descend from `v1` each fail; the empty record passes (control)."""
    v1 = commit_of(REPO_ROOT, V1_END)
    assert v2_unfrozen_problems(REPO_ROOT, NONE, []) == []
    assert v2_unfrozen_problems(REPO_ROOT, NONE, [{"run_id": "a" * 32, "freeze_tag": V2_FREEZE}]) == [
        f"the ledger holds entries under `{V2_FREEZE}` before the tag exists: {['a' * 32]}"]
    assert v2_unfrozen_problems(REPO_ROOT, NONE + f"- moved to `{v1[:12]}`\n", []) == [
        f"the v2 section names {[v1[:12]]} before `{V2_FREEZE}` exists"]
    assert v2_unfrozen_problems(REPO_ROOT, None, []) == [f"AGENTS.md has no line {V2_HEADING!r}"]
    assert v2_unfrozen_problems(REPO_ROOT, NONE, [], head=V1_FREEZE) == [
        f"`{V1_END}` ({v1[:12]}) is not in the history of {V1_FREEZE}"]


RULE_FILE = "src/valiwatt/monitor/rules.py"


def tag_v2(clone: Path, commit: str) -> None:
    git(clone, *SCRATCH, "tag", "--force", "-a", "-m", "scratch", V2_FREEZE, commit)


def pin_v2(clone: Path, commit: str) -> str:
    """In the clone: the pin commit, tests/test_tags.py with `PINS["rules-frozen-v2"]` set to `commit`, and only it."""
    path = clone / PINS_FILE
    text = path.read_text(encoding="utf-8") if path.exists() else "PINS = {}\n"
    node = next(n for n in ast.parse(text).body if isinstance(n, ast.Assign) and n.targets[0].id == "PINS")
    pins = ast.literal_eval(node.value) | {V2_FREEZE: commit}
    lines = text.splitlines(keepends=True)
    literal = "PINS = {\n" + "".join(f"    {k!r}: {v!r},\n" for k, v in pins.items()) + "}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines[:node.lineno - 1]) + literal + "".join(lines[node.end_lineno:]), encoding="utf-8")
    git(clone, "add", PINS_FILE)
    git(clone, *SCRATCH, "commit", "--quiet", "--no-verify", "-m", "pin")
    return git(clone, "rev-parse", "HEAD")


def move_v2(clone: Path, commit: str) -> str:
    """The tag moved to `commit` and its pin committed at once (plan section 10.4); returns the pin commit."""
    tag_v2(clone, commit)
    return pin_v2(clone, commit)


def departure(*commits: str, head: str = "2026-11-02, a fix to the gate: a departure") -> str:
    return f"- **{head}.** Commits " + ", ".join(f"`{c[:12]}`" for c in commits) + ".\n"


def section_with(*entries: str) -> str:
    return NONE.replace("None yet.\n", "".join(entries))


@pytest.fixture
def v2_clone(tmp_path) -> tuple[Path, str]:
    """A scratch clone with `rules-frozen-v2` on F0, the `v1` commit (before any real pin of the tag), and pinned in
    the next commit, which is HEAD: the empty record agrees (control)."""
    clone = scratch_clone(tmp_path)
    f0 = commit_of(clone, V1_END)
    git(clone, "checkout", "--quiet", "--detach", f0)
    move_v2(clone, f0)
    assert pinned_freezes(clone, "HEAD") == [f0]
    assert v2_record_problems(clone, NONE) == []
    return clone, f0


def v2_entries(f0: str, f1: str) -> list[dict]:
    """The ledger v1's function would read: a plain v2 replay under F0, a departure replay under F1."""
    return [{"run_id": "a" * 32, "freeze_tag": V2_FREEZE, "freeze_commit": f0, "departure": False,
             "departure_from": []},
            {"run_id": "b" * 32, "freeze_tag": V2_FREEZE, "freeze_commit": f1, "departure": True,
             "departure_from": ["a" * 32]}]


def test_a_v2_departure_naming_the_freeze_commit_it_moved_from_passes(v2_clone):
    """A rule change, the tag moved to it and pinned, the entry naming both freeze commits: v2's record passes
    (control). v1's function reads the same section as naming a commit that changed no rule file."""
    clone, f0 = v2_clone
    fix = scratch_commit(clone, RULE_FILE)
    move_v2(clone, fix)
    section = section_with(departure(f0, fix))
    assert v2_record_problems(clone, section) == []
    v1_problems = departure_record_problems(clone, section, v2_entries(f0, fix), tag=V2_FREEZE, head="HEAD")
    assert v1_problems == [f"the section names commits that changed no rule file after {f0[:12]}: {[f0[:12]]}"]


def test_a_recorded_v2_dependency_departure_passes(v2_clone):
    """A dependency change recorded as a departure passes v2's record (control); v1's function refuses every
    dependency change in its range, recorded or not."""
    clone, f0 = v2_clone
    bump = scratch_commit(clone, "pyproject.toml")
    move_v2(clone, bump)
    section = section_with(departure(f0, bump))
    assert v2_record_problems(clone, section) == []
    v1_problems = departure_record_problems(clone, section, v2_entries(f0, bump), tag=V2_FREEZE, head="HEAD")
    assert f"the dependencies changed between {f0[:12]} and the tag, in {[bump[:12]]}" in v1_problems


def test_a_v2_rule_change_after_the_tag_fails(v2_clone):
    clone, f0 = v2_clone
    change = scratch_commit(clone, RULE_FILE)
    assert v2_record_problems(clone, NONE) == [
        f"commit {change[:12]} changed {[RULE_FILE]} after the tag's commit {f0[:12]}: a departure the tag and the "
        "section do not record"]


@pytest.mark.parametrize("path", DEPENDENCIES)
def test_a_v2_dependency_change_after_the_tag_fails(v2_clone, path):
    clone, f0 = v2_clone
    change = scratch_commit(clone, path)
    assert v2_record_problems(clone, NONE) == [
        f"commit {change[:12]} changed {[path]} after the tag's commit {f0[:12]}: a departure the tag and the "
        "section do not record"]


def test_a_v2_rule_change_reverted_with_the_tag_unmoved_fails(v2_clone):
    """A rule change and its revert leave HEAD's rule files equal to the tag's, which an endpoint diff (v1's check)
    passes; read commit by commit, both are changes the section does not record."""
    clone, f0 = v2_clone
    change = scratch_commit(clone, RULE_FILE)
    git(clone, *SCRATCH, "revert", "--no-edit", "HEAD")
    revert = git(clone, "rev-parse", "HEAD")
    assert not [p for p in git(clone, "diff", "--name-only", f0, "HEAD").splitlines() if _is_rule(p)]
    assert departure_record_problems(clone, NONE, [], tag=V2_FREEZE, head="HEAD") == []
    assert sorted(v2_record_problems(clone, NONE)) == sorted(
        f"commit {c[:12]} changed {[RULE_FILE]} after the tag's commit {f0[:12]}: a departure the tag and the section "
        "do not record" for c in (change, revert))


def test_a_v2_departure_missing_a_change_commit_fails(v2_clone):
    clone, f0 = v2_clone
    first = scratch_commit(clone, RULE_FILE)
    second = scratch_commit(clone, "config/stations.toml")
    move_v2(clone, second)
    assert v2_record_problems(clone, section_with(departure(f0, first, second))) == []  # control
    head = "2026-11-02, a fix to the gate: a departure"
    assert v2_record_problems(clone, section_with(departure(f0, second))) == [
        f"the departure entry {head!r} does not name {first[:12]}, which changed a rule file or a dependency in its "
        "move"]


def test_a_v2_departure_naming_an_unrelated_commit_fails(v2_clone):
    clone, f0 = v2_clone
    pin = git(clone, "rev-parse", "HEAD")  # the pin commit: it touches tests/test_tags.py only
    fix = scratch_commit(clone, RULE_FILE)
    move_v2(clone, fix)
    head = "2026-11-02, a fix to the gate: a departure"
    assert v2_record_problems(clone, section_with(departure(f0, fix, pin))) == [
        f"the departure entry {head!r} names {pin[:12]}, neither a change of its move nor a commit the tag moved "
        "from or to"]


def test_a_v2_departure_not_naming_its_freeze_commits_fails(v2_clone):
    clone, f0 = v2_clone
    readme = scratch_commit(clone, "README.md")
    fix = scratch_commit(clone, RULE_FILE)
    move_v2(clone, readme)  # the tag moves to a commit that touches no rule file; the next one does
    move_v2(clone, fix)
    first, second = "2026-11-02, a move: a departure", "2026-11-03, a fix: a departure"
    assert v2_record_problems(clone, section_with(departure(f0, readme, head=first),
                                                  departure(readme, fix, head=second))) == []  # control
    assert v2_record_problems(clone, section_with(departure(readme, head=first),
                                                  departure(fix, head=second))) == [
        f"the departure entry {first!r} does not name the commit the tag moved from, {f0[:12]}",
        f"the departure entry {second!r} does not name the commit the tag moved from, {readme[:12]}"]
    assert v2_record_problems(clone, section_with(departure(f0, head=first), departure(readme, fix, head=second))) == [
        f"the departure entry {first!r} does not name the commit the tag moved to, {readme[:12]}"]
    assert v2_record_problems(clone, section_with(departure(f0, readme, fix, head=first),
                                                  departure(readme, fix, head=second))) == [
        f"the departure entry {first!r} names {fix[:12]}, neither a change of its move nor a commit the tag moved "
        "from or to"]  # another move's change commit belongs to that move only


def test_a_v2_move_without_its_departure_entry_fails(v2_clone):
    """A move of the tag the section does not record, and a departure entry with no move, each fail."""
    clone, f0 = v2_clone
    fresh = git(clone, "rev-parse", "HEAD")
    extra = "2026-11-02, a fix to the gate: a departure"
    assert v2_record_problems(clone, section_with(departure(f0, fresh))) == [
        f"the departure entry {extra!r} records no move of the tag"]
    fix = scratch_commit(clone, RULE_FILE)
    move_v2(clone, fix)
    assert v2_record_problems(clone, NONE) == [
        f"the tag moved from {f0[:12]} to {fix[:12]}, but no departure entry records it"]


def test_a_v2_section_entry_with_another_head_fails(v2_clone):
    """An entry that names a commit is a departure or the owner's ruling on 2025, in those words; any other head
    fails, a mis-worded ruling included."""
    clone, f0 = v2_clone
    fix = scratch_commit(clone, RULE_FILE)
    move_v2(clone, fix)
    for other in ("2026-11-02, a fix to the gate: a change", "2026-11-02, the owner's ruling on 2025: cleanish"):
        problems = v2_record_problems(clone, section_with(departure(f0, fix, head=other)))
        assert f"an entry names a commit but is neither a departure nor the owner's ruling on 2025: {other!r}" \
            in problems, other
    preamble = NONE.replace("None yet.", f"Moved at `{fix[:12]}`.\n\n{departure(f0, fix)}")
    assert any("neither a departure nor the owner's ruling" in p for p in v2_record_problems(clone, preamble))


def test_a_v2_ruling_entry_names_a_commit_of_the_history(v2_clone):
    """The owner's ruling entries are set aside for the 2025 gate (control), each naming a commit of the history of
    HEAD: one naming no commit, or one off that history, fails."""
    clone, f0 = v2_clone
    pin = git(clone, "rev-parse", "HEAD")
    side = scratch_commit(clone, "README.md")
    git(clone, "checkout", "--quiet", "--detach", pin)
    for outcome in ("clean", "post hoc"):
        head = f"2026-12-01, the owner's ruling on 2025: {outcome}"
        ruling = f"- **{head}.** It applies to `{pin[:12]}`; the start of 2026-11-30T10:11:12.345Z.\n"
        assert v2_record_problems(clone, section_with(ruling)) == []
        assert v2_record_problems(clone, section_with(ruling.replace(f"`{pin[:12]}`", "the commit before"))) == [
            f"the ruling entry {head!r} names no commit"]
        assert v2_record_problems(clone, section_with(ruling.replace(pin[:12], side[:12]))) == [
            f"the ruling entry {head!r} names {side[:12]}, which is not in the history of HEAD"]


def test_a_v2_section_naming_a_run_that_is_no_commit_fails(v2_clone):
    """A commit named by fewer than 12 characters, a run ID (32 hexadecimal characters) or a station number of 7
    digits would be read as a commit: each fails."""
    clone, f0 = v2_clone
    head = "2026-12-01, the owner's ruling on 2025: clean"
    pin = git(clone, "rev-parse", "HEAD")
    for run in (pin[:7], "c" * 32, "7025251"):  # a reachable commit by 7 characters: no dangling object needed
        ruling = f"- **{head}.** It applies to `{pin[:12]}`, and to `{run}`.\n"
        assert v2_record_problems(clone, section_with(ruling)) == [
            f"the section names {run}, which is not a commit of this history by at least 12 characters"]


def test_a_v2_tag_moved_without_its_pin_fails(v2_clone):
    """The tag moved to HEAD passes before its pin commit (control); once a later commit follows unpinned, the tag
    is on a commit no pin names, and fails."""
    clone, f0 = v2_clone
    fix = scratch_commit(clone, RULE_FILE)
    tag_v2(clone, fix)
    section = section_with(departure(f0, fix))
    assert v2_record_problems(clone, section) == []
    scratch_commit(clone, "README.md")
    assert v2_record_problems(clone, section) == [
        f"the tag `{V2_FREEZE}` is on {fix[:12]}, which is neither its last pin ({f0[:12]}) nor HEAD: pin it in "
        f"{PINS_FILE}"]


def test_a_v2_tag_moved_backwards_fails(v2_clone):
    clone, f0 = v2_clone
    parent = git(clone, "rev-parse", f"{f0}^")
    move_v2(clone, parent)
    assert v2_record_problems(clone, NONE) == [
        f"freeze commit {f0[:12]} does not come before {parent[:12]}: a freeze moves forward only"]


def test_a_v2_merge_commit_after_the_tag_fails(v2_clone):
    clone, f0 = v2_clone
    pin = git(clone, "rev-parse", "HEAD")
    scratch_commit(clone, "README.md")
    git(clone, "branch", "side")
    git(clone, "checkout", "--quiet", "--detach", pin)
    scratch_commit(clone, "LICENSE")
    git(clone, *SCRATCH, "merge", "--quiet", "--no-ff", "--no-edit", "side")
    assert v2_record_problems(clone, NONE) == [
        f"a merge commit lies after the freeze commit {f0[:12]}: the record is read one commit at a time"]


def test_a_v2_shallow_clone_fails_loudly(tmp_path):
    with pytest.raises(HistoryMissing, match="shallow"):
        v2_record_problems(scratch_clone(tmp_path, shallow=True), NONE)
