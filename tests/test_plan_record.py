"""docs/plan-v2.md is fixed at the `plan-v2` tag, and its section 18 lists every later change: the history agrees.

- Every commit after `plan-v2` that changes the page outside section 18, or changes section 18 other than by adding
  to it, is named in section 18 ("Before the first v2 run" or "After the first v2 run"). A commit that only adds to
  section 18 is exempt, or the record would chase itself.
- Once `v2-first-run` exists, every commit from it to v2's first freeze commit (the first pin of `rules-frozen-v2`;
  the tag itself before its pin commit; HEAD before the freeze) that touches a rule file (monitor/rules.py `_is_rule`),
  `pyproject.toml` or `uv.lock` is named under "After the first v2 run". After the freeze, such a change is a
  departure, recorded in AGENTS.md (tests/test_departure_record.py).
- Section 18 holds no other hexadecimal run of 7 characters or more: each it holds names, by at least 12 characters,
  a commit of the history.

A shallow clone fails loudly, and a merge commit after `plan-v2` fails: the history is read one commit at a time.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from test_departure_record import (
    DEPENDENCIES,
    HEX_RUN,
    SCRATCH,
    V2_FREEZE,
    HistoryMissing,
    changes,
    commit_of,
    git,
    is_ancestor,
    pinned_freezes,
    require_full_history,
    scratch_clone,
    scratch_commit,
)
from valiwatt.monitor.rules import _is_rule
from valiwatt.paths import REPO_ROOT

PLAN = "docs/plan-v2.md"
PLAN_TAG, FIRST_RUN = "plan-v2", "v2-first-run"
SECTION_18 = "## 18. Changes since this page was first committed"
BEFORE, AFTER = "### Before the first v2 run", "### After the first v2 run"


def file_at(root: Path, commit: str, path: str) -> str | None:
    out = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=root, capture_output=True, text=True, check=False)
    return out.stdout if out.returncode == 0 else None


def split_18(text: str) -> tuple[str, str | None]:
    """The page outside section 18, and section 18 up to the next section (None when no line is its heading)."""
    lines = text.split("\n")
    if SECTION_18 not in lines:
        return text, None
    i = lines.index(SECTION_18)
    j = next((k for k in range(i + 1, len(lines)) if lines[k].startswith("## ")), len(lines))
    return "\n".join(lines[:i] + lines[j:]), "\n".join(lines[i:j])


def subsection(section: str, heading: str) -> str | None:
    lines = section.split("\n")
    if heading not in lines:
        return None
    i = lines.index(heading)
    j = next((k for k in range(i + 1, len(lines)) if lines[k].startswith("### ")), len(lines))
    return "\n".join(lines[i:j])


def only_adds(old: str | None, new: str | None) -> bool:
    """Section 18 at a commit keeps every line of its parent's, in order, but the "None yet." placeholders."""
    if old is None or new is None:
        return False
    rest = iter(new.split("\n"))
    return all(any(line == kept for kept in rest) for line in old.split("\n")
               if line.strip() and line.strip() != "None yet.")


def named_in(root: Path, text: str, head: str) -> tuple[set[str], list[str]]:
    """The commits `text` names, and a problem for each hexadecimal run that names no commit of the history."""
    named, problems = set(), []
    for run in HEX_RUN.findall(text):
        full = commit_of(root, run) if 12 <= len(run) <= 40 else None
        if full is None or not is_ancestor(root, full, head):
            problems.append(f"section 18 holds {run}, which names no commit of the history by at least 12 characters")
        else:
            named.add(full)
    return named, problems


def first_freeze(root: Path, head: str) -> str:
    """Where the section 18 range of rule changes ends: v2's first freeze commit, or `head` before the freeze."""
    pins = pinned_freezes(root, head)
    return pins[0] if pins else commit_of(root, f"refs/tags/{V2_FREEZE}") or head


def plan_record_problems(root: Path, text: str | None = None, head: str = "HEAD") -> list[str]:
    """What is wrong with section 18 against the history of `root` up to `head` (none: it agrees). `text` is the page
    (None: the page at `head`)."""
    require_full_history(root)
    head_commit = commit_of(root, head)
    text = text if text is not None else file_at(root, head, PLAN)
    if text is None:
        return [f"{PLAN} is missing at {head}"]
    _, section = split_18(text)
    if section is None:
        return [f"{PLAN} has no line {SECTION_18!r}"]
    problems = [f"section 18 has no line {h!r}" for h in (BEFORE, AFTER) if subsection(section, h) is None]
    if problems:  # a section 18 whose headings cannot be found cannot be read
        return problems
    named, problems = named_in(root, section, head)
    after, _ = named_in(root, subsection(section, AFTER), head)

    tag = commit_of(root, f"refs/tags/{PLAN_TAG}")
    if tag is None:
        page_commits = git(root, "log", "--format=%H", head, "--", PLAN).splitlines()
        if page_commits in ([], [head_commit]):  # the pre-registration commit, before its tag: nothing follows it
            return problems
        raise HistoryMissing(f"no `{PLAN_TAG}` tag in this clone, but commits follow the page: fetch the tags")
    if git(root, "rev-list", "--merges", f"{tag}..{head}"):
        problems.append(f"a merge commit lies after `{PLAN_TAG}`: the record is read one commit at a time")

    for c, paths in changes(root, tag, head).items():
        if PLAN not in paths:
            continue
        old, new = file_at(root, f"{c}^", PLAN), file_at(root, c, PLAN)
        exempt = old is not None and new is not None and split_18(old)[0] == split_18(new)[0] \
            and only_adds(split_18(old)[1], split_18(new)[1])
        if not exempt and c not in named:
            problems.append(f"commit {c[:12]} changes {PLAN} outside section 18, or rewrites it, but section 18 "
                            "does not name it")

    first_run = commit_of(root, f"refs/tags/{FIRST_RUN}")
    if first_run is not None:
        for c, paths in changes(root, first_run, first_freeze(root, head)).items():
            hit = sorted(p for p in paths if _is_rule(p) or p in DEPENDENCIES)
            if hit and c not in after:
                problems.append(f"commit {c[:12]} changed {hit} after `{FIRST_RUN}`, but section 18 does not name it "
                                f"under {AFTER!r}")
    return problems


def test_the_plan_record_agrees_with_the_history():
    assert plan_record_problems(REPO_ROOT, (REPO_ROOT / PLAN).read_text(encoding="utf-8")) == []


def commit_plan(clone: Path, old: str, new: str) -> str:
    page = clone / PLAN
    text = page.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    page.write_text(text.replace(old, new), encoding="utf-8")
    git(clone, "add", PLAN)
    git(clone, *SCRATCH, "commit", "--quiet", "--no-verify", "-m", "scratch")
    return git(clone, "rev-parse", "HEAD")


def entry(heading: str, line: str) -> tuple[str, str]:
    """The replacement that adds `line` under `heading` of section 18, in place of its "None yet."."""
    return f"{heading}\n\nNone yet.\n", f"{heading}\n\n{line}\n"


def tag(clone: Path, name: str) -> None:
    git(clone, *SCRATCH, "tag", "--force", "-a", "-m", "scratch", name, "HEAD")


@pytest.fixture
def planned(tmp_path) -> Path:
    """A scratch clone at the commit `plan-v2` tags (tagged here when the repository has no such tag yet), without the
    later v2 tags: the record agrees (control)."""
    clone = scratch_clone(tmp_path)
    tagged = commit_of(clone, f"refs/tags/{PLAN_TAG}")
    git(clone, "checkout", "--quiet", "--detach", tagged or "HEAD")
    for later in (FIRST_RUN, V2_FREEZE):
        if commit_of(clone, f"refs/tags/{later}"):
            git(clone, "tag", "--delete", later)
    if tagged is None:
        if file_at(clone, "HEAD", PLAN) is None:  # this checkout's page, not committed yet
            (clone / PLAN).write_text((REPO_ROOT / PLAN).read_text(encoding="utf-8"), encoding="utf-8")
            git(clone, "add", PLAN)
            git(clone, *SCRATCH, "commit", "--quiet", "--no-verify", "-m", "plan")
        tag(clone, PLAN_TAG)
    assert plan_record_problems(clone) == []
    return clone


def test_a_plan_edit_outside_section_18_not_named_fails(planned):
    edit = commit_plan(planned, "## 17. Order of work", "## 17. The order of work")
    assert plan_record_problems(planned) == [
        f"commit {edit[:12]} changes {PLAN} outside section 18, or rewrites it, but section 18 does not name it"]
    commit_plan(planned, *entry(BEFORE, f"- 2026-11-01: section 17's title reworded, commit `{edit[:12]}`."))
    assert plan_record_problems(planned) == []  # named, by a commit that only adds to section 18 (control)


def test_a_section_18_entry_rewritten_must_be_named(planned):
    commit_plan(planned, *entry(BEFORE, "- 2026-11-01: a typo fixed in section 3."))
    assert plan_record_problems(planned) == []  # an addition only (control)
    rewrite = commit_plan(planned, "a typo fixed in section 3.", "two typos fixed in section 3.")
    assert plan_record_problems(planned) == [
        f"commit {rewrite[:12]} changes {PLAN} outside section 18, or rewrites it, but section 18 does not name it"]
    commit_plan(planned, *entry(AFTER, f"- 2026-11-02: the entry above reworded, commit `{rewrite[:12]}`."))
    assert plan_record_problems(planned) == []


@pytest.mark.parametrize("path", ["src/valiwatt/monitor/rules.py", "pyproject.toml", "uv.lock"])
def test_a_rule_change_after_the_first_run_not_named_fails(planned, path):
    scratch_commit(planned, path)
    assert plan_record_problems(planned) == []  # before `v2-first-run`, nothing to list (control)
    tag(planned, FIRST_RUN)
    change = scratch_commit(planned, path)
    unnamed = [f"commit {change[:12]} changed {[path]} after `{FIRST_RUN}`, but section 18 does not name it under "
               f"{AFTER!r}"]
    assert plan_record_problems(planned) == unnamed
    commit_plan(planned, *entry(BEFORE, f"- 2026-11-01: a fix, commit `{change[:12]}`."))
    assert plan_record_problems(planned) == unnamed  # named under the wrong heading
    commit_plan(planned, *entry(AFTER, f"- 2026-11-01: a fix, commit `{change[:12]}`, after the rehearsal run."))
    assert plan_record_problems(planned) == []
    tag(planned, V2_FREEZE)
    scratch_commit(planned, path)
    assert plan_record_problems(planned) == []  # after the freeze, a departure in AGENTS.md, not here (control)


def test_section_18_holds_no_other_hexadecimal_run():
    text = (REPO_ROOT / PLAN).read_text(encoding="utf-8")
    assert plan_record_problems(REPO_ROOT, text) == []
    absent = "0123456789abcdef0123"
    for run in ("7025251", git(REPO_ROOT, "rev-parse", "HEAD")[:7], "c" * 32, absent):  # a reachable commit by 7
        bad = text.replace(f"{AFTER}\n\nNone yet.", f"{AFTER}\n\n- station or commit `{run}`.", 1) \
            if f"{AFTER}\n\nNone yet." in text else text.rstrip("\n") + f"\n- station or commit `{run}`.\n"
        assert plan_record_problems(REPO_ROOT, bad) == [
            f"section 18 holds {run}, which names no commit of the history by at least 12 characters"], run


def test_section_18_and_its_two_headings_are_required():
    text = (REPO_ROOT / PLAN).read_text(encoding="utf-8")
    assert plan_record_problems(REPO_ROOT, text.replace(SECTION_18, "## 18. Changes")) == [
        f"{PLAN} has no line {SECTION_18!r}"]
    for heading in (BEFORE, AFTER):
        assert plan_record_problems(REPO_ROOT, text.replace(heading, "### Changes")) == [
            f"section 18 has no line {heading!r}"]
    assert plan_record_problems(REPO_ROOT, head="v1") == [f"{PLAN} is missing at v1"]


def test_section_18_naming_a_commit_off_the_history_fails(planned):
    base = git(planned, "rev-parse", "HEAD")
    side = scratch_commit(planned, "README.md")
    git(planned, "checkout", "--quiet", "--detach", base)
    commit_plan(planned, *entry(BEFORE, f"- 2026-11-01: a fix, commit `{side[:12]}`."))
    assert plan_record_problems(planned) == [
        f"section 18 holds {side[:12]}, which names no commit of the history by at least 12 characters"]


def test_a_merge_commit_after_the_plan_fails(planned):
    base = git(planned, "rev-parse", "HEAD")
    scratch_commit(planned, "README.md")
    git(planned, "branch", "side")
    git(planned, "checkout", "--quiet", "--detach", base)
    scratch_commit(planned, "LICENSE")
    git(planned, *SCRATCH, "merge", "--quiet", "--no-ff", "--no-edit", "side")
    assert plan_record_problems(planned) == [
        f"a merge commit lies after `{PLAN_TAG}`: the record is read one commit at a time"]


def test_a_missing_plan_tag_fails_loudly_once_commits_follow_the_page(planned):
    git(planned, "tag", "--delete", PLAN_TAG)
    assert plan_record_problems(planned) == []  # the pre-registration commit itself, before its tag (control)
    scratch_commit(planned, "README.md")
    with pytest.raises(HistoryMissing, match="no `plan-v2` tag"):
        plan_record_problems(planned)


def test_a_shallow_clone_fails_loudly(tmp_path):
    with pytest.raises(HistoryMissing, match="shallow"):
        plan_record_problems(scratch_clone(tmp_path, shallow=True))
