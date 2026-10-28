"""v3's tags (docs/plan-v3.md, sections 10.1, 10.2 and 12): `v3-fork`, `plan-v3`, `v3-first-run` and
`v3-rules-frozen` leave v2's frozen code's freeze check passing, since none takes a `rules-frozen*` name; they follow
each other and are annotated; and a move of `rules-frozen-v2` on the `v2` branch fails no v3 tag check, v2's record
being closed at the fork. Scratch clones only: no tag of this repository is created, moved or deleted."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_departure_record import SCRATCH, git, is_ancestor, scratch_clone, scratch_commit
from test_tags import ORDER, PINS, retag, tag_commit, tag_problems, tagged

FORK = "3a1cd4a8c763"  # v2's last render, the commit v3 forks from (section 10.1)
V3_TAGS = ("v3-fork", "plan-v3", "v3-first-run", "v3-rules-frozen")


def make_tag(clone: Path, name: str, commit: str) -> None:
    git(clone, *SCRATCH, "tag", "--force", "-a", "-m", "scratch", name, commit)


def v2_code_with_v3_tags(tmp_path: Path) -> Path:
    """A scratch clone checked out at v2's code (the fork commit), its copied v3 tags deleted, then the four v3 tags
    made again: `v3-fork` on the fork commit, the three others on scratch commits above it that touch no rule file and
    no dependency (README.md)."""
    clone = scratch_clone(tmp_path)
    git(clone, "checkout", "--quiet", "--detach", FORK)
    for name in V3_TAGS:
        if tag_commit(clone, name):
            git(clone, "tag", "-d", name)
    make_tag(clone, "v3-fork", git(clone, "rev-parse", "HEAD"))
    for name in V3_TAGS[1:]:
        make_tag(clone, name, scratch_commit(clone, "README.md"))
    assert sorted(t for t in git(clone, "tag", "--list", "v3-*", "plan-v3").split()) == sorted(V3_TAGS)
    return clone


def v2_freeze_check(clone: Path) -> subprocess.CompletedProcess:
    """v2's own freeze check, `check_freeze()` of the fork's code, run from the clone in a process of its own: the
    clone's src first on the path, its repository the clone's."""
    code = ("import valiwatt, pathlib, sys; "
            f"assert pathlib.Path(valiwatt.__file__).resolve().is_relative_to(pathlib.Path({str(clone)!r}).resolve()), "
            "valiwatt.__file__; "
            "from valiwatt.monitor.run import check_freeze; print(check_freeze())")
    env = {k: v for k, v in os.environ.items() if not k.startswith("MLFLOW_")} | {"PYTHONPATH": str(clone / "src")}
    return subprocess.run([sys.executable, "-c", code], cwd=clone, env=env, capture_output=True, text=True,
                          check=False)


def test_creating_the_four_v3_tags_on_v2_s_code_leaves_v2_s_freeze_check_passing(tmp_path):
    """Section 12, Tags: on a scratch clone of v2's code (3a1cd4a8c763), the four v3 tags leave v2's freeze check
    passing: none is named `rules-frozen*`, the pattern v2's frozen code refuses unlisted."""
    clone = v2_code_with_v3_tags(tmp_path)
    done = v2_freeze_check(clone)
    assert done.returncode == 0, done.stderr
    assert "'freeze_tag': 'rules-frozen-v2'" in done.stdout


def test_a_v3_tag_named_like_a_v2_freeze_stops_v2_s_freeze_check(tmp_path):
    """The control that fires: the same clone with a tag `rules-frozen-v3` (a v3 freeze named in v2's pattern) stops v2's
    freeze check, as it would stop v2's 2025 test."""
    clone = v2_code_with_v3_tags(tmp_path)
    make_tag(clone, "rules-frozen-v3", "HEAD")
    done = v2_freeze_check(clone)
    assert done.returncode != 0
    assert "['rules-frozen-v3'] are not listed in config.FREEZES" in done.stderr


def moved_on_v2(tmp_path: Path) -> tuple[Path, str]:
    """A scratch clone and a commit of it after the fork and off v3's history, as a later commit of the `v2` branch
    would be: made in the clone on the fork commit, so no local or remote branch of the checkout is read (CI's
    checkout holds only the pushed branch)."""
    clone = scratch_clone(tmp_path)
    head = git(clone, "rev-parse", "HEAD")
    git(clone, "checkout", "--quiet", "--detach", PINS["v3-fork"])
    v2_tip = scratch_commit(clone, "README.md")
    git(clone, "checkout", "--quiet", "--detach", head)
    assert v2_tip != PINS["rules-frozen-v2"] and not is_ancestor(clone, v2_tip, head)
    return clone, v2_tip


def test_rules_frozen_v2_moved_on_the_v2_branch_fails_no_v3_tag_check(tmp_path):
    """Section 10.1: `rules-frozen-v2` moved to a commit of the `v2` branch, after the fork and off v3's history, fails
    no v3 tag check: the tag is read as of the fork."""
    clone, v2_tip = moved_on_v2(tmp_path)
    assert tag_problems(clone, PINS) == []  # control
    retag(clone, "rules-frozen-v2", v2_tip)
    assert tag_commit(clone, "rules-frozen-v2") == v2_tip
    assert tag_problems(clone, PINS) == []
    git(clone, "tag", "-d", "rules-frozen-v2")  # but the tag must still exist
    assert tag_problems(clone, PINS) == [f"tag rules-frozen-v2 is on no commit, not its pin {PINS['rules-frozen-v2']}"]


def test_the_v3_tags_follow_each_other_and_are_annotated(tmp_path):
    """Section 10.2: each v3 tag annotated and pinned, each an ancestor of the next (control); a tag out of order, or a
    lightweight one, fails."""
    clone = scratch_clone(tmp_path)
    git(clone, "checkout", "--quiet", "--detach", "HEAD")
    first_run = scratch_commit(clone, "README.md")
    frozen = scratch_commit(clone, "README.md")
    make_tag(clone, "v3-first-run", first_run)
    make_tag(clone, "v3-rules-frozen", frozen)
    pins = PINS | {"v3-first-run": first_run, "v3-rules-frozen": frozen}
    assert tag_problems(clone, pins) == []
    fork = PINS["v3-fork"]
    make_tag(clone, "v3-first-run", fork)  # before plan-v3
    assert tag_problems(clone, pins | {"v3-first-run": fork}) == [
        f"tag plan-v3 ({PINS['plan-v3'][:12]}) does not come before tag v3-first-run ({fork[:12]})"]
    make_tag(clone, "v3-first-run", first_run)
    retag(clone, "v3-rules-frozen", frozen, annotated=False)
    assert tag_problems(clone, pins) == ["tag v3-rules-frozen is not annotated"]


@pytest.mark.parametrize("name", ["v3-first-run", "v3-rules-frozen"])
def test_a_v3_tag_not_pinned_passes_only_on_head(tmp_path, name):
    """A v3 tag is pinned in the commit after it is made: on HEAD unpinned it passes (control), and not after. Read
    without its pin, and without the tags after it, so the case holds once they are made and pinned too."""
    later = ORDER[ORDER.index(name) + 1:]
    pins = {k: v for k, v in PINS.items() if k != name and k not in later}
    clone = scratch_clone(tmp_path)
    for tag in later:
        if tag_commit(clone, tag):
            git(clone, "tag", "-d", tag)
    git(clone, "checkout", "--quiet", "--detach", "HEAD")
    made = scratch_commit(clone, "README.md")
    make_tag(clone, name, made)
    assert tag_problems(clone, pins) == []
    scratch_commit(clone, "README.md")
    assert tag_problems(clone, pins) == [f"tag {name} is not pinned and is not on HEAD: pin it in tests/test_tags.py"]


def test_v2_s_tag_tests_read_rules_frozen_v2_as_of_the_fork_after_it_moves_on_the_v2_branch(tmp_path):
    """Section 10.1, through v2's own tag tests: `rules-frozen-v2` moved in a scratch clone to a commit after the fork
    and off v3's history, test_tags.tagged() still reads it at its pin as of the fork, its control passes, and a moved
    v1 is still caught."""
    clone, v2_tip = moved_on_v2(tmp_path)
    retag(clone, "rules-frozen-v2", v2_tip)
    clone, pins = tagged(tmp_path, clone)
    assert pins["rules-frozen-v2"] == PINS["rules-frozen-v2"]
    earlier = git(clone, "rev-parse", f"{pins['v1']}^")
    retag(clone, "v1", earlier)
    assert tag_problems(clone, pins) == [f"tag v1 is on {earlier}, not its pin {pins['v1']}"]
