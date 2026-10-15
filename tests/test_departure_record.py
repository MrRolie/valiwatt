"""AGENTS.md's record of the departures from the frozen monitoring rules agrees with the history.

The departures section names the commit the `rules-frozen` tag moved to and the commits that made the departure.
Those commits, without the tag's, must be exactly the commits that changed a rule file (monitor/rules.py `_is_rule`)
between the earlier freeze commit and the tag; and this departure changed no dependency (`pyproject.toml`,
`uv.lock`, which the freeze gate does not hash): a later departure that does must extend this check to name the
commits that change them. The earlier freeze commit is the one the ledger's plain replays ran
under (with several, the one the departure replays depart from). At any time, departure or not, the rule files and the
dependencies at HEAD are the tag's: a later change would be a departure the section does not record.

The check reads the history, so a shallow clone fails it loudly instead of passing on history it cannot see; CI checks
out the full history (`fetch-depth: 0`, .github/workflows/checks.yml), as the public-safety scan needs too.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from valiwatt.monitor.rules import FREEZE_TAG, _is_rule
from valiwatt.monitor.run import recorded_departures
from valiwatt.paths import REPO_ROOT
from valiwatt.report import ledger
from valiwatt.report.lineage import COMMIT

DEPENDENCIES = ("pyproject.toml", "uv.lock")


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


def earlier_freeze(entries: list[dict], tag_commit: str) -> set[str]:
    """The freeze commit the departure departs from: the plain replays' one, or, with several, the one of the plain
    replays the departure replays under the tag name."""
    plain = {e["freeze_commit"] for e in entries if not e["departure"]}
    if len(plain) <= 1:
        return plain
    by_id = {e["run_id"]: e for e in entries}
    return {by_id[x]["freeze_commit"] for e in entries if e["departure"] and e["freeze_commit"] == tag_commit
            for x in e["departure_from"] if x in by_id and not by_id[x]["departure"]}


def departure_record_problems(root: Path, section: str, entries: list[dict], tag: str = FREEZE_TAG,
                              head: str = "HEAD") -> list[str]:
    """What is wrong with the departures section against the history of `root` (none: it agrees)."""
    if git(root, "rev-parse", "--is-shallow-repository") == "true":
        raise HistoryMissing("shallow clone: the departure record is checked against the full history "
                             "(actions/checkout fetch-depth: 0)")
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
    return recorded_departures(), ledger.read()


def test_the_departures_section_names_exactly_the_commits_that_changed_the_rules(recorded):
    section, entries = recorded
    assert departure_record_problems(REPO_ROOT, section, entries) == []


def test_before_any_departure_the_record_still_checks_the_rules_and_the_dependencies(recorded):
    """A section that names no commit is checked too: the rule files and the dependencies at HEAD are the tag's, and
    no replay in the ledger ran as a departure. On this history: true at the tag, and false at the earlier freeze
    commit, whose rule files the departure changed."""
    section, entries = recorded
    tag_commit = commit_of(REPO_ROOT, FREEZE_TAG)
    plain = [e | {"freeze_commit": tag_commit} for e in entries if not e["departure"]]
    none = "\n\nNone yet.\n"
    assert departure_record_problems(REPO_ROOT, none, plain) == []
    assert any("departure replays" in p for p in departure_record_problems(REPO_ROOT, none, entries))
    (earlier,) = earlier_freeze(entries, tag_commit)
    assert any("rule files changed after the tag's commit" in p
               for p in departure_record_problems(REPO_ROOT, none, plain, tag=earlier))


def test_a_section_missing_a_departure_commit_fails(recorded):
    section, entries = recorded
    tag_commit = commit_of(REPO_ROOT, FREEZE_TAG)
    named = [p for p in COMMIT.findall(section) if not tag_commit.startswith(p)]
    assert named
    for prefix in named:
        problems = departure_record_problems(REPO_ROOT, section.replace(prefix, "a later commit"), entries)
        assert any("does not name them" in p and prefix[:12] in p for p in problems), prefix


def test_a_section_naming_a_commit_that_changed_no_rule_fails(recorded):
    section, entries = recorded
    tag_commit = commit_of(REPO_ROOT, FREEZE_TAG)
    (earlier,) = earlier_freeze(entries, tag_commit)
    quiet = [c for c, paths in changes(REPO_ROOT, earlier, tag_commit).items()
             if c != tag_commit and not any(_is_rule(p) for p in paths)]
    assert quiet  # the range holds report and docs commits
    problems = departure_record_problems(REPO_ROOT, section + f"\n- also `{quiet[0][:12]}`.\n", entries)
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


def commit_on_tag(clone: Path, path: str) -> str:
    """In the clone: a commit on top of the tag's that appends a line to `path`, and the tag moved to it."""
    tag_commit = commit_of(clone, FREEZE_TAG)
    git(clone, "checkout", "--quiet", "--detach", tag_commit)
    with open(clone / path, "a", encoding="utf-8") as f:
        f.write("\n# scratch change\n")
    git(clone, "add", path)
    git(clone, "-c", "user.name=scratch", "-c", "user.email=scratch@example.invalid", "-c", "commit.gpgsign=false",
        "commit", "--quiet", "--no-verify", "-m", "scratch")
    new = git(clone, "rev-parse", "HEAD")
    git(clone, "tag", "--force", FREEZE_TAG, new)
    return new


@pytest.mark.parametrize("path, fails", [("README.md", False), ("pyproject.toml", True)])
def test_a_dependency_change_in_the_departure_fails(tmp_path, recorded, path, fails):
    """On a scratch clone, the tag moves to a new commit that changes `path` and the section names it in place of the
    old tag: a README change is no departure (control), a pyproject.toml change in the range is refused."""
    section, entries = recorded
    clone = scratch_clone(tmp_path)
    old = commit_of(clone, FREEZE_TAG)
    new = commit_on_tag(clone, path)
    assert old[:12] in section
    problems = departure_record_problems(clone, section.replace(old[:12], new[:12]), entries)
    if fails:
        assert len(problems) == 1 and "dependencies changed between" in problems[0] and new[:12] in problems[0]
    else:
        assert problems == []


def test_a_section_that_does_not_name_the_tag_commit_fails(recorded):
    section, entries = recorded
    tag_commit = commit_of(REPO_ROOT, FREEZE_TAG)
    assert tag_commit[:12] in section
    problems = departure_record_problems(REPO_ROOT, section.replace(tag_commit[:12], "the tag"), entries)
    assert any("does not name the tag's commit" in p and tag_commit[:12] in p for p in problems)


def test_a_dependency_change_after_the_tag_fails(tmp_path, recorded):
    """On a scratch clone, a commit after the tag's changes pyproject.toml and the tag stays put: a dependency change
    the section does not record."""
    section, entries = recorded
    clone = scratch_clone(tmp_path)
    tag_commit = commit_of(clone, FREEZE_TAG)
    git(clone, "checkout", "--quiet", "--detach", "HEAD")
    with open(clone / "pyproject.toml", "a", encoding="utf-8") as f:
        f.write("\n# scratch change\n")
    git(clone, "add", "pyproject.toml")
    git(clone, "-c", "user.name=scratch", "-c", "user.email=scratch@example.invalid", "-c", "commit.gpgsign=false",
        "commit", "--quiet", "--no-verify", "-m", "scratch")
    assert commit_of(clone, FREEZE_TAG) == tag_commit
    problems = departure_record_problems(clone, section, entries)
    assert problems == [f"dependencies changed after the tag's commit {tag_commit[:12]}: ['pyproject.toml']"]


def test_a_shallow_clone_fails_loudly(tmp_path, recorded):
    section, entries = recorded
    with pytest.raises(HistoryMissing, match="shallow"):
        departure_record_problems(scratch_clone(tmp_path, shallow=True), section, entries)
