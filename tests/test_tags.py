"""The release and freeze tags are pinned to their commits, follow each other, and are annotated.

`PINS` names the commit each tag must be on. A tag is pinned in the commit after it is made, so for that one commit
an unpinned tag passes while it points at HEAD. When `rules-frozen-v2` moves (a departure), its new pin is committed
at once, in a commit that touches only this file: tests/test_departure_record.py reads the tag's successive commits
from the history of `PINS`, so keep it a literal dict, one tag per line.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_departure_record import HistoryMissing, commit_of, git, is_ancestor, require_full_history, scratch_clone
from valiwatt.paths import REPO_ROOT

PINS = {
    "rules-frozen": "affe089f6ed03318f1046f2c1aa6efc21c44604c",
    "v1": "132138ba79fd5700804777efa4f7035f3599a7c4",
    "plan-v2": "ff64c9aa2af418840958d520ddcae42f6150c370",
    "v2-first-run": "7438fc0293c95a808684e1ccdaaffd691f0693ae",
    "rules-frozen-v2": "825b7bd134a88ad38dc62efe361e30f80321c8be",
}

ORDER = ("rules-frozen", "v1", "plan-v2", "v2-first-run", "rules-frozen-v2")


def tag_commit(root: Path, name: str) -> str | None:
    return commit_of(root, f"refs/tags/{name}")


def tag_problems(root: Path, pins: dict[str, str], head: str = "HEAD") -> list[str]:
    """What is wrong with the tags of `root` against `pins` (none: they agree). A shallow clone fails loudly."""
    require_full_history(root)
    problems = []
    on = {name: tag_commit(root, name) for name in (*ORDER, *pins)}
    for name, pin in pins.items():
        if on[name] != pin:
            problems.append(f"tag {name} is on {on[name] or 'no commit'}, not its pin {pin}")
    present = [name for name in ORDER if on[name]]
    for a, b in zip(present, present[1:]):
        if on[a] == on[b] or not is_ancestor(root, on[a], on[b]):
            problems.append(f"tag {a} ({on[a][:12]}) does not come before tag {b} ({on[b][:12]})")
    head_commit = commit_of(root, head)
    for name in present:
        if name not in pins and on[name] != head_commit:
            problems.append(f"tag {name} is not pinned and is not on {head}: pin it in tests/test_tags.py")
        if git(root, "cat-file", "-t", f"refs/tags/{name}") != "tag":
            problems.append(f"tag {name} is not annotated")
    return problems


def test_the_tags_agree_with_their_pins():
    assert tag_problems(REPO_ROOT, PINS) == []


def tagged(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A scratch clone, and its tags as pins: every check passes on them (control)."""
    clone = scratch_clone(tmp_path)
    pins = {name: c for name in ORDER if (c := tag_commit(clone, name))}
    assert pins.keys() >= {"rules-frozen", "v1"}
    assert tag_problems(clone, pins) == []
    return clone, pins


def retag(clone: Path, name: str, commit: str, *, annotated: bool = True) -> None:
    args = ["-a", "-m", "scratch"] if annotated else []
    git(clone, "-c", "user.name=scratch", "-c", "user.email=scratch@example.invalid", "tag", "--force", *args,
        name, commit)


def test_a_pinned_tag_moved_fails(tmp_path):
    clone, pins = tagged(tmp_path)
    earlier = git(clone, "rev-parse", f"{pins['v1']}^")  # still after rules-frozen: only the pin is broken
    retag(clone, "v1", earlier)
    assert tag_problems(clone, pins) == [f"tag v1 is on {earlier}, not its pin {pins['v1']}"]


def test_a_pinned_tag_missing_fails(tmp_path):
    """A pinned tag that is not in the clone (not pushed with --follow-tags, say) fails."""
    clone, pins = tagged(tmp_path)
    git(clone, "tag", "-d", "v1")
    assert tag_problems(clone, pins) == [f"tag v1 is on no commit, not its pin {pins['v1']}"]


def test_a_shallow_clone_fails_loudly(tmp_path):
    with pytest.raises(HistoryMissing, match="shallow"):
        tag_problems(scratch_clone(tmp_path, shallow=True), PINS)


def test_a_tag_out_of_order_fails(tmp_path):
    clone, pins = tagged(tmp_path)
    before_v1 = git(clone, "rev-parse", f"{pins['rules-frozen']}^")
    retag(clone, "plan-v2", before_v1)
    problems = tag_problems(clone, pins | {"plan-v2": before_v1})
    assert problems == [f"tag v1 ({pins['v1'][:12]}) does not come before tag plan-v2 ({before_v1[:12]})"]
    retag(clone, "plan-v2", pins["v1"])  # two tags on one commit: neither comes before the other
    problems = tag_problems(clone, pins | {"plan-v2": pins["v1"]})
    assert problems == [f"tag v1 ({pins['v1'][:12]}) does not come before tag plan-v2 ({pins['v1'][:12]})"]


def test_an_unpinned_tag_passes_only_on_head(tmp_path):
    clone, pins = tagged(tmp_path)
    unpinned = {k: v for k, v in pins.items() if k != "v1"}
    assert tag_problems(clone, unpinned, head=pins["v1"]) == []  # the commit it was just made on (control)
    assert tag_problems(clone, unpinned) == ["tag v1 is not pinned and is not on HEAD: pin it in tests/test_tags.py"]


def test_a_lightweight_tag_fails(tmp_path):
    clone, pins = tagged(tmp_path)
    retag(clone, "v1", pins["v1"], annotated=False)
    assert tag_problems(clone, pins) == ["tag v1 is not annotated"]


def ledger_freeze_problems(entries: list[dict], root: Path = REPO_ROOT, tag: str = "rules-frozen-v2") -> list[str]:
    """What ties a committed replay-ledger entry of `tag` to the tag itself, and fails: its freeze commit is not the
    tag's commit, or its rules hash is not the hash of the rule files at the tag (monitor/rules.py `rules_sha256`)."""
    from valiwatt.monitor.rules import rules_sha256

    require_full_history(root)
    commit = tag_commit(root, tag)
    if commit is None:
        return [f"tag {tag} is missing"]
    rules = rules_sha256(commit)
    problems = []
    for e in entries:
        if e["freeze_tag"] != tag:
            continue
        if e["freeze_commit"] != commit:
            problems.append(f"entry {e['run_id']} names freeze commit {e['freeze_commit']}, not {tag}'s {commit}")
        if e["rules_sha256"] != rules:
            problems.append(f"entry {e['run_id']} carries rules hash {e['rules_sha256']}, not the rules at {tag}")
    return problems


def test_every_committed_v2_ledger_entry_ran_on_the_rules_at_the_freeze_tag():
    """Checkable from a clone, without data or a run store: each replay the committed ledger lists under
    rules-frozen-v2 names the tag's commit and the hash of the rule files at the tag."""
    from valiwatt.report import ledger

    entries = ledger.read()
    assert [e for e in entries if e["freeze_tag"] == "rules-frozen-v2"], "the ledger lists no v2 replay"
    assert ledger_freeze_problems(entries) == []


def test_a_ledger_entry_off_the_tag_fails():
    from valiwatt.report import ledger

    entries = [e for e in ledger.read() if e["freeze_tag"] == "rules-frozen-v2"]
    moved = [{**entries[0], "freeze_commit": "0" * 40}, {**entries[-1], "rules_sha256": "0" * 64}]
    problems = ledger_freeze_problems(moved)
    assert len(problems) == 2 and "freeze commit" in problems[0] and "rules hash" in problems[1]


def model_problems(entries: list[dict], tag: str = "rules-frozen-v2", year: int = 2024) -> list[str]:
    """What the README says of v2's 2024 replays in the committed ledger, and fails: they do not all carry one model
    hash and one hash of the frozen artifacts."""
    mine = [e for e in entries if e["freeze_tag"] == tag and e["year"] == year]
    return [f"the {tag} {year} entries carry {len(set(e[k] for e in mine))} values of {k}"
            for k in ("model_sha256", "frozen_sha256") if len({e[k] for e in mine}) != 1]


def test_every_committed_v2_2024_replay_carries_one_model_and_one_set_of_frozen_artifacts():
    from valiwatt.report import ledger

    entries = ledger.read()
    assert model_problems(entries) == []
    first = next(e for e in entries if e["freeze_tag"] == "rules-frozen-v2" and e["year"] == 2024)
    assert len(model_problems([*entries, {**first, "run_id": "0" * 32, "model_sha256": "0" * 64}])) == 1
    assert len(model_problems([*entries, {**first, "run_id": "0" * 32, "frozen_sha256": "0" * 64}])) == 1
