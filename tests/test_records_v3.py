"""v3's records as its gates read them (src/valiwatt/records_v3.py; docs/plan-v3.md, sections 10.2, 10.3 and 11.3):
the freeze in force (v3-pre-freeze before the tag, never after it), the tag's first pin read from the history of its
pins, the rulings of v3's departures section, and v3's replay starts and moved-tag scan. Scratch repositories and run
stores only (tests/records_v3_world.py); the pins are also read on this repository, through git."""

from __future__ import annotations

import pytest

from records_v3_world import (
    PRE,
    RULE_FILE,
    commit,
    fake_run,
    git,
    git_tags,
    log_replay_v3,
    pin,
    pins_of,
    scratch_store,
    tag,
)
from valiwatt import records_v3
from valiwatt.config import V3_FREEZE_TAG
from valiwatt.monitor import run as mrun
from valiwatt.monitor.rules import rules_sha256
from valiwatt.paths import REPO_ROOT
from valiwatt.records_v3 import V3FreezeError

# ----------------------------------------------------------------------------------------------- the freeze in force


def test_before_the_tag_a_v3_run_is_pre_freeze_and_after_it_never(tmp_path, monkeypatch):
    """Section 10.3 and D5: with no `v3-rules-frozen`, the freeze is v3-pre-freeze, no commit, the tree's rules. Once the
    tag exists, the freeze is the tag's (its commit and the rules at it), and a tree whose rule files or dependencies
    differ from the tag's is refused, never labelled pre-freeze."""
    s = scratch_store(tmp_path, monkeypatch, frozen=False)
    assert records_v3.current_freeze() == {"freeze_tag": PRE, "freeze_commit": "", "rules_sha256": rules_sha256()}
    f0 = tag(s.repo, V3_FREEZE_TAG)
    assert records_v3.current_freeze() == {"freeze_tag": V3_FREEZE_TAG, "freeze_commit": f0,
                                           "rules_sha256": rules_sha256(f0)}
    commit(s.repo, {RULE_FILE: "MIN = 5\n"})
    with pytest.raises(V3FreezeError, match=r"rule files differ from `v3-rules-frozen`.*monitor/rules.py"):
        records_v3.current_freeze()
    commit(s.repo, {RULE_FILE: "MIN = 4\n"})
    assert records_v3.current_freeze()["freeze_commit"] == f0  # the rules are the tag's again (control)
    (s.repo / "uv.lock").write_text("version = 2\n", encoding="utf-8")
    with pytest.raises(V3FreezeError, match=r"dependencies differ from `v3-rules-frozen`.*uv.lock"):
        records_v3.current_freeze()


def test_an_unlisted_v3_freeze_tag_is_refused(tmp_path, monkeypatch):
    """A tag named like v3's freeze that config.FREEZES does not list is refused, before the tag exists and after it;
    v2's freeze check never sees it (its pattern is `rules-frozen*`)."""
    s = scratch_store(tmp_path, monkeypatch, frozen=False)
    assert records_v3.unlisted_v3_tags() == [] and records_v3.current_freeze()["freeze_tag"] == PRE  # control
    tag(s.repo, f"{V3_FREEZE_TAG}-2")
    assert records_v3.unlisted_v3_tags() == [f"{V3_FREEZE_TAG}-2"] and mrun._freeze_tags() == []
    with pytest.raises(V3FreezeError, match=r"\['v3-rules-frozen-2'\] are not listed"):
        records_v3.current_freeze()
    tag(s.repo, V3_FREEZE_TAG)
    with pytest.raises(V3FreezeError, match=r"\['v3-rules-frozen-2'\] are not listed"):
        records_v3.current_freeze()
    git(s.repo, "tag", "-d", f"{V3_FREEZE_TAG}-2")
    assert records_v3.unlisted_v3_tags() == []
    separated = f"{V3_FREEZE_TAG}\u2028"  # a name Python's split() would cut back to the listed tag's
    tag(s.repo, separated)
    assert records_v3.unlisted_v3_tags() == [separated]


# ----------------------------------------------------------------------------------------------------- the pins


@pytest.mark.parametrize("case", ["unmoved", "moved"])
def test_the_first_pin_is_read_from_the_pins_history(tmp_path, monkeypatch, case):
    """D4: "the commit `v3-rules-frozen` was first pinned to" is the first pin of the tag in the history of
    tests/test_tags.py, whatever the tag's current commit: after a departure moves the tag and its pin, the first pin
    stays F0 (moved); with one pin it is that pin (control)."""
    s = scratch_store(tmp_path, monkeypatch)
    if case == "moved":
        fix = commit(s.repo, {RULE_FILE: "MIN = 5\n"})
        tag(s.repo, V3_FREEZE_TAG, fix)
        pin(s.repo, V3_FREEZE_TAG, fix)
        assert records_v3.pinned_commits() == [s.f0, fix]
        assert git(s.repo, "rev-parse", f"{V3_FREEZE_TAG}^{{commit}}") == fix
    else:
        assert records_v3.pinned_commits() == [s.f0]
    assert records_v3.first_pin() == s.f0


def test_a_merge_dropping_the_pin_never_hides_the_first_pin(tmp_path, monkeypatch):
    """The first pin is read from the whole history of tests/test_tags.py: F0 pinned on branch A; branch B, forked
    before that pin, changes a rule; A merged into B keeping B's tests/test_tags.py, without the pin; the tag moved to
    the merge G and pinned there. The pins are F0 then G, and the first pin stays F0, whose commit a history simplified
    at the merge (it follows only the side whose file the merge kept) would never show."""
    s = scratch_store(tmp_path, monkeypatch, pinned=False)
    b = git(s.repo, "symbolic-ref", "--short", "HEAD")
    git(s.repo, "checkout", "-q", "-b", "a")
    pin(s.repo, V3_FREEZE_TAG, s.f0)
    git(s.repo, "checkout", "-q", b)
    commit(s.repo, {RULE_FILE: "MIN = 5\n"}, "a rule change on B")
    git(s.repo, "merge", "-q", "--no-ff", "-s", "ours", "-m", "A merged into B, B's pins kept", "a")
    g = tag(s.repo, V3_FREEZE_TAG)
    assert pins_of(s.repo) == {}
    pin(s.repo, V3_FREEZE_TAG, g)
    assert records_v3.pinned_commits() == [s.f0, g]
    assert records_v3.first_pin() == s.f0


@pytest.mark.parametrize("case", ["sibling", "off_head", "control"])
def test_the_first_pin_is_anchored_across_every_ref(tmp_path, monkeypatch, case):
    """The first pin is read across every ref, never the head's history alone. Sibling (the reviewer's scenario): F0
    pinned in P, P kept on a branch; the working branch restarted from F0, a rule changed in G, the tag moved to G and
    pinned there: two histories pin the tag first to different commits, and the first pin is refused (the head's
    history alone would read G). Off_head: F0 tagged and a later commit F1 pinned on a branch only, the head reset
    before F1: the original pin is no ancestor of the head, refused. The control: the sibling scenario with P kept by
    no ref; the first pin is G, the head's own."""
    s = scratch_store(tmp_path, monkeypatch, pinned=case != "off_head")
    if case == "off_head":
        f1 = commit(s.repo, {RULE_FILE: "MIN = 5\n"}, "F1")
        tag(s.repo, V3_FREEZE_TAG, f1)
        pinned = pin(s.repo, V3_FREEZE_TAG, f1)
        git(s.repo, "branch", "keep", pinned)
        git(s.repo, "reset", "--quiet", "--hard", s.f0)
        with pytest.raises(V3FreezeError, match=rf"first pinned to {f1[:12]} \(set by {pinned[:12]}\), which is not "
                                                "HEAD or an ancestor of it"):
            records_v3.first_pin()
        return
    if case == "sibling":
        git(s.repo, "branch", "keep", s.pin)
    git(s.repo, "reset", "--quiet", "--hard", s.f0)
    g = commit(s.repo, {RULE_FILE: "MIN = 5\n"}, "G, a rule change")
    tag(s.repo, V3_FREEZE_TAG, g)
    pinned = pin(s.repo, V3_FREEZE_TAG, g)
    assert records_v3.pinned_commits() == [g]  # the head's history alone
    if case == "control":
        assert records_v3.first_pin() == g
        return
    with pytest.raises(V3FreezeError, match="first pinned on more than one history, to different commits") as e:
        records_v3.first_pin()
    assert f"{s.pin[:12]} pins {s.f0[:12]}" in str(e.value) and f"{pinned[:12]} pins {g[:12]}" in str(e.value)


def test_before_any_pin_there_is_no_first_pin(tmp_path, monkeypatch):
    """The tag made and not yet pinned: nothing has a first pin, and every reader of it refuses."""
    scratch_store(tmp_path, monkeypatch, pinned=False)
    assert records_v3.pinned_commits() == []
    with pytest.raises(V3FreezeError, match=r"not pinned yet: pin the tag in tests/test_tags.py first"):
        records_v3.first_pin()


def test_pinned_commits_agree_with_the_history_test_s_reading(tmp_path, monkeypatch):
    """The runtime reader of pins (records_v3.pinned_commits) and the history test's (test_departure_record
    pinned_freezes) give the same commits: on this repository for v2's tag, up to HEAD and up to the fork, and on a
    scratch clone whose v2 tag moved twice, a pin repeated by later commits counted once."""
    from test_departure_record import RULE_FILE as V2_RULE_FILE
    from test_departure_record import (
        V2_FREEZE,
        commit_of,
        move_v2,
        pinned_freezes,
        scratch_clone,
        scratch_commit,
    )
    from valiwatt import paths

    for head in ("HEAD", "v3-fork"):
        assert records_v3.pinned_commits(V2_FREEZE, head) == pinned_freezes(REPO_ROOT, head) != []
    clone = scratch_clone(tmp_path)
    f0 = commit_of(clone, "v1")
    git(clone, "checkout", "--quiet", "--detach", f0)
    move_v2(clone, f0)
    scratch_commit(clone, "README.md")
    fix = scratch_commit(clone, V2_RULE_FILE)
    move_v2(clone, fix)
    monkeypatch.setattr(paths, "REPO_ROOT", clone)
    assert records_v3.pinned_commits(V2_FREEZE) == pinned_freezes(clone, "HEAD") == [f0, fix]


# ------------------------------------------------------------------------------------------------- the rulings

FULL = "0123456789abcdef0123456789abcdef01234567"  # a commit of this history, resolved by the stub
OTHER = "fedcba9876543210fedcba9876543210fedcba98"  # a second one
V2_BRANCH = "abcdef0123456789abcdef0123456789abcdef01"  # a v2-branch commit, never resolved here
START = "2027-03-02T10:11:12.345Z"


def resolve_stub(calls: list | None = None):
    def resolve(prefix):
        if calls is not None:
            calls.append(prefix)
        return next((c for c in (FULL, OTHER) if c.startswith(prefix)), None)
    return resolve


def entry(head: str, body: str) -> str:
    return f"- **{head}.** {body}\n"


@pytest.mark.parametrize("case", ["v2_form-clean", "v2_form-post_hoc", "h", "i", "v2_first"])
def test_each_ruling_form_is_read(case):
    """Section 11.3 (4): each form's head gives its kind, the condition it lifts and its outcome; v2's form may name a
    stopped start. v2's own reader of rulings (mrun._rulings) reads none of v3's (h), (i) or v2-first heads."""
    heads = {"v2_form-clean": "2027-03-03, the owner's ruling on 2025: clean",
             "v2_form-post_hoc": "2027-03-03, the owner's ruling on 2025: post hoc",
             "h": "2027-03-03, the owner's ruling on 2025 (h): post hoc",
             "i": "2027-03-03, the owner's ruling on 2025 (i): post hoc",
             "v2_first": "2027-03-03, the owner's ruling on v2 first"}
    head = heads[case]
    body = {"v2_form-clean": f"It applies to `{FULL[:12]}`; the stopped start of {START}.",
            "v2_first": f"v2's test did not run; the `v2` branch at `{V2_BRANCH}`."}.get(case,
                                                                                      f"It applies to `{FULL}`.")
    section = "\n\nThe v3 departures.\n\n" + entry(head, body)
    (r,) = records_v3.rulings_v3(section, resolve=resolve_stub())
    want = {"v2_form-clean": ("v2_form", "a_f", "clean", FULL, START),
            "v2_form-post_hoc": ("v2_form", "a_f", "post_hoc", FULL, ""),
            "h": ("h", "h", "post_hoc", FULL, ""), "i": ("i", "i", "post_hoc", FULL, ""),
            "v2_first": ("v2_first", "g", None, V2_BRANCH, "")}[case]
    assert (r["kind"], r["condition"], r["outcome"], r["commit"], r["start"]) == want
    assert r["head"] == head and r["day"] == "2027-03-03"
    if case.startswith("v2_form"):
        assert len(records_v3.rulings_v3(section + entry(heads["h"], f"`{FULL}`."), resolve=resolve_stub())) == 2
    else:
        assert mrun._rulings(section) == []  # v2's reader passes over v3's heads
    assert mrun.departure_record(section)[1] == []  # no ruling is read as a departure


def test_a_v2_first_ruling_is_never_resolved_in_v3_s_history():
    """D10: a v2-first entry's hash is never resolved in v3's history, so a v2-branch commit v3's history lacks is read
    as written; v2's form, in the same section, is resolved."""
    calls = []
    section = (entry("2027-03-03, the owner's ruling on v2 first", f"`{V2_BRANCH}`.")
               + entry("2027-03-04, the owner's ruling on 2025: post hoc", f"`{FULL[:12]}`."))
    first, v2_form = records_v3.rulings_v3(section, resolve=resolve_stub(calls))
    assert first["commit"] == V2_BRANCH and v2_form["commit"] == FULL
    assert calls == [FULL[:12]]  # the v2-first hash was never looked up


MALFORMED = [
    ("no_commit", "2027-03-03, the owner's ruling on 2025: clean", "It applies to the frozen code.", "names 0 commits"),
    ("two_commits", "2027-03-03, the owner's ruling on 2025: clean", f"`{FULL[:12]}` and `{OTHER[:12]}`.",
     "names 2 commits"),
    ("unresolved", "2027-03-03, the owner's ruling on 2025 (h): post hoc", f"`{'9' * 12}`.",
     "is no commit of this history"),
    ("short", "2027-03-03, the owner's ruling on 2025 (i): post hoc", f"`{FULL[:9]}`.",
     "is no commit of this history"),
    ("two_starts", "2027-03-03, the owner's ruling on 2025: post hoc",
     f"`{FULL[:12]}`, {START} and 2027-03-02T10:11:13.000Z.", "names 2 starts"),
    ("h_start", "2027-03-03, the owner's ruling on 2025 (h): post hoc", f"`{FULL[:12]}`, {START}.", "names a start"),
    ("i_start", "2027-03-03, the owner's ruling on 2025 (i): post hoc", f"`{FULL[:12]}`, {START}.", "names a start"),
    ("v2_first_start", "2027-03-03, the owner's ruling on v2 first", f"`{V2_BRANCH}`, {START}.", "names a start"),
    ("v2_first_short", "2027-03-03, the owner's ruling on v2 first", f"`{V2_BRANCH[:12]}`.", "full 40 characters"),
    ("v2_first_two", "2027-03-03, the owner's ruling on v2 first", f"`{V2_BRANCH}` then `{FULL}`.",
     "full 40 characters"),
]


@pytest.mark.parametrize("case, head, body, match", MALFORMED, ids=[m[0] for m in MALFORMED])
def test_a_ruling_naming_no_commit_two_commits_or_a_start_where_none_belongs_is_refused(case, head, body, match):
    """Section 11.3 (4), §2.5 of the slice contract: each malformed entry is refused, naming it."""
    with pytest.raises(V3FreezeError, match=match):
        records_v3.rulings_v3(entry(head, body), resolve=resolve_stub())


def test_the_v3_section_is_read_under_its_own_heading():
    """The v3 section is the text under its heading, never v2's; this repository's names no ruling yet."""
    text = ("# x\n\n## Departures from the v2 frozen rules\n\n"
            + entry("2027-03-03, the owner's ruling on 2025: clean", f"`{FULL}`.")
            + "\n## Departures from the v3 frozen rules\n\nNone yet.\n")
    assert records_v3.v3_section(text) == "\n\nNone yet.\n" and records_v3.rulings_v3(records_v3.v3_section(text)) == []
    assert records_v3.rulings_v3(records_v3.v3_section()) == []


# ------------------------------------------------------------------------------------- the starts and the moved tag


@pytest.mark.parametrize("case", ["pre_freeze", "v2_kind", "empty_tag"])
def test_replay_starts_v3_see_only_v3_replays_under_v3_s_tag(tmp_path, monkeypatch, case):
    """D3 and D5: v3's starts are its `replay_v3` runs under `v3-rules-frozen` (the control: one finished 2025 run, a
    start and a freeze commit of the moved-tag scan); a pre-freeze rehearsal is neither, a v2 replay run is never
    read, and a v3 replay without a freeze tag is refused, never folded into v1's freeze."""
    s = scratch_store(tmp_path, monkeypatch)
    f0 = s.f0
    held = log_replay_v3(s.repo, 2025, standing="clean")
    (start,) = records_v3.replay_starts_v3(2025)
    assert (start["run_id"], start["finished"], start["freeze_commit"], start["standing"]) == (held, True, f0, "clean")
    assert records_v3.earlier_freeze_commits_v3() == {f0: [held]}
    if case == "pre_freeze":
        log_replay_v3(s.repo, 2025, PRE)
        log_replay_v3(s.repo, 2024, PRE)
        assert [x["run_id"] for x in records_v3.replay_starts_v3(2025)] == [held]
        assert records_v3.replay_starts_v3(2024) == [] and records_v3.earlier_freeze_commits_v3() == {f0: [held]}
    elif case == "v2_kind":
        v2 = fake_run({"kind": "replay", "year": "2025", "freeze_tag": V3_FREEZE_TAG, "freeze_commit": "9" * 40,
                       **git_tags(s.repo)})
        assert [x["run_id"] for x in records_v3.replay_starts_v3(2025)] == [held]
        assert records_v3.earlier_freeze_commits_v3() == {f0: [held]}
        assert [x["run_id"] for x in mrun.replay_starts(2025, V3_FREEZE_TAG)] == [v2]  # v2's reader sees only it
    else:
        bare = fake_run({"kind": "replay_v3", "year": "2024", "freeze_commit": f0, **git_tags(s.repo)})
        for read in (lambda: records_v3.replay_starts_v3(2025), records_v3.earlier_freeze_commits_v3):
            with pytest.raises(V3FreezeError, match=f"v3 replay run {bare} carries the freeze tag ''"):
                read()


def test_the_committed_v3_ledger_joins_the_starts_and_the_moved_tag_scan(tmp_path, monkeypatch):
    """A fresh clone's store is empty: ledger-v3's entries are its starts and its freeze commits. A ledger-v3 entry
    under another freeze commit than the tag's is a moved tag (mrun.check_tag_not_moved on v3's records); an entry not
    under v3's tag, or an unreadable file, is refused; no file holds no entry."""
    import json

    s = scratch_store(tmp_path, monkeypatch)
    assert records_v3.ledger_v3_dict() == {"replays": []}
    e = {"run_id": "1" * 32, "year": 2025, "freeze_tag": V3_FREEZE_TAG, "freeze_commit": s.f0,
         "started_utc": "2027-01-01T00:00:00.000Z", "standing": "clean"}
    commit(s.repo, {"docs/replay-ledger-v3.json": json.dumps({"replays": [e]})})
    (start,) = records_v3.replay_starts_v3(2025)
    assert (start["run_id"], start["finished"], start["git_commit"]) == (e["run_id"], True, "")
    freeze = records_v3.current_freeze()
    args = (records_v3.earlier_freeze_commits_v3(), False)
    kwargs = {"recorded": records_v3.v3_section(), "ledger": records_v3.ledger_v3_dict()}
    assert mrun.check_tag_not_moved(freeze, *args, **kwargs) == []  # control
    moved = {"replays": [e | {"freeze_commit": "9" * 40}]}
    with pytest.raises(mrun.FreezeError, match="moving the tag is a departure"):
        mrun.check_tag_not_moved(freeze, *args, recorded=kwargs["recorded"], ledger=moved)
    for bad in ({"replays": [e | {"freeze_tag": "rules-frozen-v2"}]}, {"replays": [e | {"freeze_tag": ""}]},
                {"entries": []}):
        (s.repo / "docs/replay-ledger-v3.json").write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(V3FreezeError):
            records_v3.ledger_v3_dict()
    (s.repo / "docs/replay-ledger-v3.json").write_text("{", encoding="utf-8")
    with pytest.raises(V3FreezeError, match="cannot be read"):
        records_v3.ledger_v3_dict()


def test_the_moved_tag_check_never_reads_a_v2_first_ruling_s_hash_as_a_v3_commit(tmp_path, monkeypatch):
    """Section 11.3 (4): a "v2 first" ruling names a `v2`-branch commit read only by (g). The moved-tag check on v3's
    section (monitor/run_v3.py `_not_moved`, v2's check_tag_not_moved and departure_record) reads departure entries
    only: with the tag moved from F0 to a fix, the move recorded and a "v2 first" ruling in the same section naming a
    full hash this repository does not hold, the check passes, and no reader of v3's section resolves that hash."""
    from records_v3_world import agents
    from replay_world import departure_entry
    from valiwatt.monitor import run_v3

    s = scratch_store(tmp_path, monkeypatch)
    earlier = log_replay_v3(s.repo, 2024, freeze_commit=s.f0)
    fix = commit(s.repo, {RULE_FILE: "MIN = 5\n"}, "a fix")
    tag(s.repo, V3_FREEZE_TAG, fix)
    pin(s.repo, V3_FREEZE_TAG, fix)
    v2_first = f"- **2027-03-03, the owner's ruling on v2 first.** v2's test of record is at `{V2_BRANCH}`.\n"
    commit(s.repo, {"AGENTS.md": agents(departure_entry(s.f0, fix), v2_first)}, "the move, and a ruling on v2 first")
    assert mrun._resolve(V2_BRANCH) is None  # no such commit here
    resolved = []
    resolve = mrun._resolve

    def spy(prefix):
        resolved.append(prefix)
        return resolve(prefix)

    monkeypatch.setattr(mrun, "_resolve", spy)
    monkeypatch.setattr(records_v3, "_resolve", spy)
    assert run_v3._not_moved(records_v3.current_freeze(), True) == [earlier]
    (ruling,) = records_v3.rulings_v3(records_v3.v3_section())
    assert (ruling["kind"], ruling["commit"]) == ("v2_first", V2_BRANCH)
    assert resolved and not [p for p in resolved if V2_BRANCH.startswith(p) or p.startswith(V2_BRANCH[:12])]
