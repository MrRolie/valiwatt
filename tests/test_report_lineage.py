"""The report's run chain is refused at its first broken link: a run that did not finish or pass, a run made from
other code, a frozen model for another year, a replay under another freeze commit, a validation built on another
data-quality run, a departure recorded in AGENTS.md that the replay does not carry. The runs are fakes in a fake store;
one clean chain passes, and each break spoils one tag of it."""

from types import SimpleNamespace

import pytest
import report_payloads as RP
import report_store as S
import report_world as W

from valiwatt import tracking
from valiwatt.monitor import run as monitor_run
from valiwatt.report import ledger, lineage
from valiwatt.report import validation as RV
from valiwatt.report.render import ReportRefused

CODE, RULES, FREEZE = "c" * 64, "d" * 64, {"freeze_tag": "rules-frozen", "freeze_commit": "f" * 40}

# The departures section of AGENTS.md with none recorded, and with the tag moved to the replay's freeze commit.
NONE_RECORDED = ("\n\nThe monitoring rules are frozen by the `rules-frozen` tag before the 2024 replay. A change to "
                 "`pyproject.toml` or `uv.lock` counts too; the 2024 replay runs again only with `--departure`.\n\n"
                 "None yet.\n")
RECORDED = ("\n\n- 2026-11-02: a threshold changed after the freeze; the tag moves to commit `"
            + FREEZE["freeze_commit"][:12] + "`.\n")


def fake_run(run_id, run_status="FINISHED", **tags):
    return SimpleNamespace(info=SimpleNamespace(run_id=run_id, status=run_status), data=SimpleNamespace(tags=tags))


def clean_chain() -> dict:
    """Every run of the chain by its run ID, each tagged as `follow` expects."""
    made = {"code_sha256": CODE, "rules_sha256": RULES, "status": "complete"}
    on_dq = made | {"dq_run_id": "dq"}
    return {
        "dq": fake_run("dq", gates="passed", code_sha256=CODE),
        "dq2": fake_run("dq2", gates="passed", code_sha256=CODE),  # another passed data-quality run
        "val": fake_run("val", **on_dq),
        "f2024": fake_run("f2024", **on_dq, replay_year="2024", validation_run_id="val", coefficient_sha256="k24"),
        "f2023": fake_run("f2023", **on_dq, replay_year="2023", validation_run_id="val", coefficient_sha256="k23"),
        "replay": fake_run("replay", **on_dq, kind="replay", validation_run_id="val", frozen_run_id="f2024",
                           coefficient_sha256="k24", freeze_commit=FREEZE["freeze_commit"]),
        "pseudo": fake_run("pseudo", **on_dq, kind="pseudo_replay", validation_run_id="val", frozen_run_id="f2023",
                           coefficient_sha256="k23"),
        "breaks": fake_run("breaks", **made, kind="breaks"),
    }


def serve(monkeypatch, runs: dict) -> None:
    """A fake store: the latest run of each kind, and any run by its ID. AGENTS.md records no departure."""
    def latest_run(experiment, filter_string=""):
        for kind in ("pseudo_replay", "breaks", "replay"):
            if f"'{kind}'" in filter_string:
                return runs["pseudo" if kind == "pseudo_replay" else kind]
        raise LookupError(filter_string)

    monkeypatch.setattr(tracking, "latest_run", latest_run)
    monkeypatch.setattr(tracking, "client", lambda: SimpleNamespace(get_run=lambda run_id: runs[run_id]))
    monkeypatch.setattr(monitor_run, "recorded_departures", lambda agents=None, tag=None: NONE_RECORDED)
    monkeypatch.setattr(ledger, "read", lambda path=None: [])  # an empty replay ledger


def test_a_clean_chain_is_followed(monkeypatch):
    serve(monkeypatch, clean_chain())
    chain = lineage.follow(CODE, RULES, FREEZE)
    assert {k: r.info.run_id for k, r in chain.runs().items()} == {
        "dq": "dq", "validation": "val", "frozen_2023": "f2023", "frozen_2024": "f2024", "pseudo_replay": "pseudo",
        "replay": "replay", "breaks": "breaks"}


def test_the_chain_reads_the_departures_section_of_its_own_freeze(monkeypatch):
    """follow reads AGENTS.md's departures section of the freeze it follows (monitor.run.recorded_departures with that
    freeze's tag), never the section the function reads by default, v2's."""
    serve(monkeypatch, clean_chain())
    asked = []
    monkeypatch.setattr(monitor_run, "recorded_departures",
                        lambda agents=None, tag=None: asked.append(tag) or NONE_RECORDED)
    lineage.follow(CODE, RULES, FREEZE)
    assert asked == [FREEZE["freeze_tag"]] == ["rules-frozen"]


# Each break: the run to spoil, the tag to set on it, and the words the refusal must carry. A new link is one row.
BROKEN_LINKS = {
    "breaks_failed": ("breaks", "status", "failed", "breaks run breaks is not marked complete"),
    "other_code": ("pseudo", "code_sha256", "0" * 64, "pseudo_replay run pseudo: code_sha256"),
    "frozen_for_another_year": ("f2024", "replay_year", "2023", "frozen-model 2024 run f2024: replay_year"),
    "replay_under_another_freeze": ("replay", "freeze_commit", "e" * 40, "replay run replay: freeze_commit"),
    # The data-quality run is the one the validation names, so the mismatch shows on the runs built before it.
    "validation_on_another_dq": ("val", "dq_run_id", "dq2", "dq_run_id is 'dq', expected 'dq2'"),
}


@pytest.mark.parametrize("case", list(BROKEN_LINKS))
def test_a_broken_link_in_the_run_chain_is_refused(case, monkeypatch):
    run, tag, value, words = BROKEN_LINKS[case]
    runs = clean_chain()
    runs[run].data.tags[tag] = value
    serve(monkeypatch, runs)
    with pytest.raises(lineage.ChainBroken, match=words):
        lineage.follow(CODE, RULES, FREEZE)


def test_a_departure_replay_brings_the_replays_it_departs_from_and_names_them(monkeypatch):
    """A replay run after a recorded departure must carry the earlier replays into the chain, so the report can label
    it post hoc beside them; one that names none is refused."""
    runs = clean_chain()
    serve(monkeypatch, runs)
    assert lineage.follow(CODE, RULES, FREEZE, recorded=NONE_RECORDED).departed == []
    runs["earlier"] = fake_run("earlier", kind="replay", status="complete", freeze_commit="e" * 40)
    runs["replay"].data.tags |= {"departure": "true", "departure_from": "earlier"}
    assert [r.info.run_id for r in lineage.follow(CODE, RULES, FREEZE, recorded=RECORDED).departed] == ["earlier"]
    runs["earlier"].data.tags["status"] = "failed"  # an earlier replay that never completed cannot stand beside it
    with pytest.raises(lineage.ChainBroken, match="departed replay"):
        lineage.follow(CODE, RULES, FREEZE, recorded=RECORDED)
    runs["earlier"].data.tags["status"] = "complete"
    runs["replay"].data.tags["departure_from"] = ""
    with pytest.raises(lineage.ChainBroken, match="names no earlier replay"):
        lineage.follow(CODE, RULES, FREEZE, recorded=RECORDED)


def test_a_recorded_departure_the_replay_does_not_carry_is_refused(monkeypatch):
    """AGENTS.md records a departure, but the store holds no earlier replay (a fresh clone), so the replay ran as a
    plain one: the report would drop its post hoc label. It is refused; so is the converse, a departure replay whose
    freeze commit AGENTS.md does not name."""
    runs = clean_chain()
    serve(monkeypatch, runs)
    with pytest.raises(lineage.ChainBroken, match="AGENTS.md records a departure .* is not a departure") as refused:
        lineage.follow(CODE, RULES, FREEZE, recorded=RECORDED)
    # The fix it names: the earlier replays come from the store or from the committed ledger (the freeze gate reads
    # both, monitor/run.py ledger_freeze_commits), so a fresh clone holding only the ledger can still depart.
    assert str(refused.value).endswith(
        "is not a departure: the earlier replays it departs from must be in mlruns/ or docs/replay-ledger.json; run "
        "`valiwatt all --departure`")
    runs["earlier"] = fake_run("earlier", kind="replay", status="complete", freeze_commit="e" * 40)
    runs["replay"].data.tags |= {"departure": "true", "departure_from": "earlier"}
    with pytest.raises(lineage.ChainBroken, match="does not name its freeze commit"):
        lineage.follow(CODE, RULES, FREEZE, recorded=NONE_RECORDED)


def test_with_no_departure_recorded_a_clean_chain_passes(monkeypatch):
    """Control: a section whose prose names no commit lets a plain replay through, and a recorded departure lets
    through the departure replay that carries it."""
    runs = clean_chain()
    serve(monkeypatch, runs)
    assert lineage.COMMIT.findall(NONE_RECORDED) == []
    assert lineage.follow(CODE, RULES, FREEZE).departed == []
    runs["earlier"] = fake_run("earlier", kind="replay", status="complete", freeze_commit="e" * 40)
    runs["replay"].data.tags |= {"departure": "true", "departure_from": "earlier"}
    assert [r.info.run_id for r in lineage.follow(CODE, RULES, FREEZE, recorded=RECORDED).departed] == ["earlier"]


def v1_departures(agents: str | None = None) -> str:
    """v1's departures section as monitor.run reads it, whether `recorded_departures` takes the freeze tag (lane D's
    form, whose default is v2's freeze) or reads v1's section only (BASE)."""
    import inspect

    takes_tag = "tag" in inspect.signature(monitor_run.recorded_departures).parameters
    return monitor_run.recorded_departures(agents, **({"tag": "rules-frozen"} if takes_tag else {}))


def test_a_departures_section_that_cannot_be_found_is_refused(monkeypatch):
    """The repository's AGENTS.md has the section the check reads; under another heading the section reads empty, and
    a chain checked against nothing is refused rather than passed."""
    from valiwatt.config import FREEZES

    agents = (monitor_run.REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert v1_departures(agents).strip()
    section = v1_departures(agents.replace(FREEZES["rules-frozen"]["heading"], "## Changes to the monitoring rules"))
    serve(monkeypatch, clean_chain())  # from here on, the fake store's own AGENTS.md section
    with pytest.raises(lineage.ChainBroken, match="no departures section"):
        lineage.follow(CODE, RULES, FREEZE, recorded=section)


def test_a_departure_named_by_a_short_hash_is_refused(monkeypatch):
    """A departure recorded by a 7-character hash would match no 12-character freeze commit and slip past the check."""
    serve(monkeypatch, clean_chain())
    with pytest.raises(lineage.ChainBroken, match="fewer than 12 characters"):
        lineage.follow(CODE, RULES, FREEZE, recorded="- 2026-10-14: rules changed at 8ad0fd0 (post hoc).")


def test_the_freeze_gate_and_the_report_read_the_same_ledger():
    """The ledger's path has two homes: the freeze gate's (monitor/run.py, a rule file) and the report's writer and
    reader (report/ledger.py). Moving one without the other would let the gate miss the replays the report records."""
    assert (monitor_run.REPO_ROOT / monitor_run.LEDGER).resolve() == ledger.LEDGER.resolve()
    assert ledger.LEDGER.resolve().relative_to(monitor_run.REPO_ROOT.resolve()).as_posix() == "docs/replay-ledger.json"


# --- v2: the chain of `rules-frozen-v2`, its records and its departures (a fake store, tests/report_store.py) ---------

LANGS = ("fr", "en")


def section(text: str, number: int) -> str:
    return text.split(f"\n## {number}. ", 1)[1].split("\n## ", 1)[0]


def rerun_2024(w, run_id="e" * 32) -> str:
    w.add_replay(run_id, w.payloads["r"])
    w.add_diagnostics(run_id, w.payloads["diag"]["replay"])
    return run_id


def predecessor_id(p) -> str:
    return p.run_id if isinstance(p, (lineage.Unfinished, lineage.Absent)) else lineage.run_id_of(p)


def test_the_chain_follows_the_v2_2024_replay_not_a_v1_or_a_2025_one(monkeypatch):
    """The store holds, after v2's 2024 replay, a v1 2024 replay (no freeze tag) and a 2025 replay: the chain's 2024
    replay is v2's, and the 2025 one is the test of record, not the chain's replay."""
    w = S.World().install(monkeypatch)
    w.add(S.fake_run("6" * 32, start=w.tick(), kind="replay", year="2024", status="complete", code_sha256=S.CODE,
                     rules_sha256=S.RULES, freeze_commit="e" * 40, validation_run_id="val", dq_run_id="dq"),
          {"results/replay.json": RP.V1_PLAIN})
    w.add_2025()
    chain = w.follow()
    assert chain.replay.info.run_id == S.REPLAY and lineage.run_id_of(chain.record_2024) == S.REPLAY
    assert chain.held.record["run_id"] == "b" * 32
    assert {k: lineage.run_id_of(r) for k, r in chain.runs().items()}["replay"] == S.REPLAY


@pytest.mark.parametrize("lang", LANGS)
def test_a_clean_2025_with_nothing_changed_renders_beside_the_2024_replay_of_record(monkeypatch, lang):
    """Control (plan section 13): a through-2025 data-quality run, the 2025 test of record on v2's first 2024
    replay's frozen run, and a later 2024 rerun on the render's code. The 2024 replay of record is the first; the
    rerun is shown beside it and changes nothing."""
    w = S.World().install(monkeypatch)
    w.add_2025()
    rerun = rerun_2024(w)
    chain = w.follow()
    assert chain.replay.info.run_id == rerun and lineage.run_id_of(chain.record_2024) == S.REPLAY
    ctx = w.context(chain)
    assert ctx["rec24"]["run_id"] == S.REPLAY and ctx["rerun24"]["run_id"] == rerun and ctx["r_source"] == "record"
    assert ctx["rerun24"]["differences"] == [] and ctx["status"] == "confirmed"
    eight = section(RP.render(ctx, lang), 8)
    assert eight.index(S.REPLAY) < eight.index(rerun)
    assert ("et il donne les mêmes résultats imprimés" if lang == "fr" else "and it gives the same printed results") \
        in eight


def test_a_2025_replay_whose_frozen_hashes_differ_from_the_2024_record_s_is_refused(monkeypatch):
    w = S.World().install(monkeypatch)
    w.add_2025()
    assert w.follow().held.record["run_id"] == "b" * 32  # control
    for key in ("thresholds", "production"):
        w = S.World().install(monkeypatch)
        w.add_2025(frozen={**W.FROZEN, key: "0" * 64})
        with pytest.raises(lineage.ChainBroken, match="judged by frozen artifacts whose hashes differ"):
            w.follow()


def test_a_2025_rerun_whose_frozen_hashes_differ_from_the_2024_record_s_is_refused(monkeypatch):
    """Every 2025 rerun, as the test of record, is judged by exactly the artifacts frozen for v2's first 2024 replay
    (plan section 13): a rerun with one hash of its frozen_sha256 changed refuses; with them equal, it is a rerun."""
    w = S.World().install(monkeypatch)
    w.add_2025()
    w.add_2025(run_id="c" * 32, evaluation="seen_by_developers")
    assert [lineage.run_id_of(r) for r in w.follow().held.reruns] == ["c" * 32]  # control
    for key in ("thresholds", "production", "reference"):
        w = S.World().install(monkeypatch)
        w.add_2025()
        w.add_2025(run_id="c" * 32, evaluation="seen_by_developers", frozen={**W.FROZEN, key: "0" * 64})
        with pytest.raises(lineage.ChainBroken, match="2025 rerun c{32} was judged by frozen artifacts whose hashes "
                                                      "differ"):
            w.follow()


def test_a_2025_record_whose_data_quality_run_does_not_reach_the_end_of_2025_is_refused(monkeypatch):
    """The data-quality run a 2025 record read (its run tag dq_run_id) must be a passing run through 2025, to its
    last hour: one that names the through-2024 run, or a through-2025 run whose window stops short, refuses; and so
    for a rerun."""
    from valiwatt.config import data_last

    for spoil in ("through_2024", "short_window", "rerun"):
        w = S.World().install(monkeypatch)
        w.add_2025()
        if spoil == "through_2024":
            w.runs["b" * 32].data.tags["dq_run_id"] = "dq"
            who = "test of record " + "b" * 32
        elif spoil == "short_window":
            w.runs["dq25"].data.params["window_last_utc"] = data_last(2024).isoformat()
            who = "test of record " + "b" * 32
        else:
            w.add_2025(run_id="c" * 32, evaluation="seen_by_developers")
            assert w.follow().held.reruns  # control
            w.runs["c" * 32].data.tags["dq_run_id"] = "dq"
            who = "rerun " + "c" * 32
        with pytest.raises(lineage.ChainBroken, match=f"2025 {who}: its data-quality run .* does not reach the end "
                                                      "of 2025"):
            w.follow()


def test_an_unfinished_test_of_record_judged_by_other_frozen_artifacts_is_refused(monkeypatch):
    """An unfinished test of record is listed, never a result; but the gate's record it logged when it opened must
    still name the 2024 record's frozen artifacts."""
    w = S.World().install(monkeypatch)
    w.add_2025(finished=False)
    assert not w.follow().held.finished  # control
    w = S.World().install(monkeypatch)
    w.add_2025(finished=False, gate=W.gate("clean", frozen={**W.FROZEN, "thresholds": "0" * 64}))
    with pytest.raises(lineage.ChainBroken, match="its gate's record names frozen artifacts whose hashes differ"):
        w.follow()


def test_a_2024_replay_tagged_with_another_evaluation_is_refused(monkeypatch):
    """The chain's 2024 replay carries the label its run was tagged with when it opened (plan section 10.5): tagged
    anything but seen_by_developers, or untagged, no v2 number on 2024 renders from it, whatever its results say."""
    w = S.World().install(monkeypatch)
    assert w.context()["r_source"] == "chain"  # control
    for label in ("held_out", "rehearsal", None):
        w = S.World().install(monkeypatch)
        if label is None:
            del w.runs[S.REPLAY].data.tags["evaluation"]
        else:
            w.runs[S.REPLAY].data.tags["evaluation"] = label
        with pytest.raises(ReportRefused, match="not seen_by_developers: no v2 number on 2024"):
            w.context()


@pytest.mark.parametrize("lang", LANGS)
def test_a_record_from_older_code_is_stated_not_refused(monkeypatch, lang):
    """Control: v2's first 2024 replay and the test of record ran on code the render does not have (a pin commit
    came after them); the chain's own runs are on the render's code; both records render with their code stated."""
    old = "0" * 64
    w = S.World().install(monkeypatch)
    w.runs[S.REPLAY].data.tags |= {"code_sha256": old, "git_commit": "2" * 40}
    w.add_2025(code_sha256=old)
    rerun_2024(w)
    ctx = w.context()
    assert ctx["rec24"]["code_sha256"] == old and ctx["held"]["record"]["code_sha256"] == old
    text = RP.render(ctx, lang)
    assert f"`{old[:12]}`" in section(text, 8) and f"`{old[:12]}`" in section(text, 9)


def test_a_comparator_hash_that_differs_from_the_replay_s_is_refused(monkeypatch):
    for key in W.FROZEN["comparators"]:
        w = S.World().install(monkeypatch)
        w.runs["f2024"].data.tags[f"comparator_sha256.{key}"] = "0" * 64
        with pytest.raises(lineage.ChainBroken, match=rf"comparison models \['{key}'\] have other hashes"):
            w.follow()
    w = S.World().install(monkeypatch)
    w.files[(S.REPLAY, "results/replay.json")]["frozen_sha256"] = {**W.FROZEN, "production": "0" * 64}
    with pytest.raises(lineage.ChainBroken, match="judged by another production model"):
        w.follow()
    w = S.World().install(monkeypatch)
    del w.runs["f2023"].data.tags["comparator_sha256.forecast"]  # the rehearsal's frozen run too
    with pytest.raises(lineage.ChainBroken, match=r"frozen-model 2023 .* comparison models \['forecast'\]"):
        w.follow()


@pytest.mark.parametrize("lang", LANGS)
def test_a_v2_departure_from_an_unfinished_start_and_two_freeze_commits_renders(monkeypatch, lang):
    """Control: v2's 2024 replay departs, under F2, from v2's first 2024 replay (F0) and from a 2024 start under F1
    that never finished; two departure entries move the tag from F0 to F1 and from F1 to F2. Each predecessor is
    shown, the unfinished one as unfinished. Breaks: a predecessor in neither the store nor the ledger, or an earlier
    freeze commit no departure entry names, refuses."""
    w = S.World().install(monkeypatch)
    stopped = "3" * 32
    run = w.add_replay(stopped, w.payloads["r"], freeze_commit=W.F1, status="FAILED")
    run.data.tags |= {"status": "started", "stage": "replayed", "error": "MemoryError"}
    w.files.pop((stopped, "results/replay.json"))
    dep = "c" * 32
    w.add_replay(dep, w.payloads["r"], freeze_commit=W.F2, departure="true", departure_from=f"{S.REPLAY},{stopped}")
    w.add_diagnostics(dep, w.payloads["diag"]["replay"])
    both = S.departure_entry("2026-11-20", [W.F0, W.F1]) + S.departure_entry("2026-11-27", [W.F1, W.F2])
    recorded = S.NONE_RECORDED.replace("None yet.\n", both)
    freeze = {"freeze_tag": S.TAG, "freeze_commit": W.F2}

    def follow(section_text=recorded):
        return lineage.follow(S.CODE, S.RULES, freeze, recorded=section_text, entries=list(w.entries), starts=[])

    chain = follow()
    assert [predecessor_id(p) for p in chain.departed] == [S.REPLAY, stopped]
    assert isinstance(chain.departed[1], lineage.Unfinished)
    ctx = RV.context(chain, S.CODE, S.RULES, list(w.entries), [])
    shown = {d["id"]: d for d in ctx["departures"]}
    assert shown[stopped]["unfinished"] and not shown[S.REPLAY]["unfinished"]
    assert [e["to"] for e in ctx["dep_entries"]] == [W.F1[:12], W.F2[:12]]
    eight = section(RP.render(ctx, lang), 8)
    assert S.REPLAY in eight and stopped in eight and "`MemoryError`" in eight and RV.SAY[lang]["unfinished"] in eight
    assert all(f"`{c[:12]}`" in eight for c in (W.F0, W.F1, W.F2))
    w.runs[dep].data.tags["departure_from"] = f"{S.REPLAY},{stopped},{'7' * 32}"
    with pytest.raises(lineage.ChainBroken, match="neither in the store nor in the replay ledger"):
        follow()
    w.runs[dep].data.tags["departure_from"] = f"{S.REPLAY},{stopped}"
    one = S.NONE_RECORDED.replace("None yet.\n", S.departure_entry("2026-11-27", [W.F1, W.F2]))
    with pytest.raises(lineage.ChainBroken, match=f"freeze commit {W.F0[:12]}, which no departure entry"):
        follow(one)
    assert follow().replay.info.run_id == dep


def test_v1_history_comes_only_from_rules_frozen_entries():
    first = W.v2_entry("a" * 32, 2024, W.utc(0))
    dep = W.v2_entry("c" * 32, 2024, W.utc(1), freeze_commit=W.F1, departure_from=["a" * 32])
    shown = lineage.v1_history([RP.V1_PLAIN, RP.V1_DEPARTED, first, dep])
    assert [r.entry["run_id"] for r in shown] == [RP.V1_PLAIN["run_id"], RP.V1_DEPARTED["run_id"]]
    assert [r.entry["run_id"] for r in lineage.v1_history([RP.V1_PLAIN, first, dep])] == [RP.V1_PLAIN["run_id"]]
    with pytest.raises(lineage.ChainBroken, match="no plain v1 2024 replay"):
        lineage.v1_history([first, dep])


def test_check_recorded_reads_the_v2_section(monkeypatch):
    """v2's section holds departures (a bold head ending "a departure", naming the commit the tag moved from and the
    one it moved to) and the owner's rulings on 2025, which name commits and are no departures. A departure replay's
    freeze commit must be the one a departure entry moved the tag to (handoff 1): not one moved back to, nor a change
    commit the entry names on the way."""
    history = S.History(W.F0, W.F1, W.F2)
    monkeypatch.setattr(lineage, "resolve", history.resolve)
    monkeypatch.setattr(lineage, "is_ancestor", history.is_ancestor)
    monkeypatch.setattr(lineage, "changes", history.changes)

    def replay(commit, departure):
        return S.fake_run("a" * 32, freeze_commit=commit, departure="true" if departure else "false")

    def recorded(*entries):
        return S.NONE_RECORDED.replace("None yet.\n", "".join(entries))

    ruling = S.ruling_entry("2026-12-01", "clean", W.F0)
    moved = S.departure_entry("2026-11-20", [W.F0, W.F1])
    assert lineage.check_recorded(replay(W.F0, False), recorded(ruling), S.TAG) == []  # a ruling is no departure
    with pytest.raises(lineage.ChainBroken, match="records a departure"):
        lineage.check_recorded(replay(W.F0, False), recorded(moved, ruling), S.TAG)
    got = lineage.check_recorded(replay(W.F1, True), recorded(moved, ruling), S.TAG)
    assert [(e["kind"], e["named"]) for e in got] == [("departure", [W.F0[:12], W.F1[:12]])]
    with pytest.raises(lineage.ChainBroken, match="moved the tag to its freeze commit"):
        lineage.check_recorded(replay(W.F0, True), recorded(moved), S.TAG)  # moved back to F0
    through = S.departure_entry("2026-11-20", [W.F0, W.F1, W.F2])  # F1, a rule change on the way to F2
    assert lineage.check_recorded(replay(W.F2, True), recorded(through), S.TAG)
    with pytest.raises(lineage.ChainBroken, match="moved the tag to its freeze commit"):
        lineage.check_recorded(replay(W.F1, True), recorded(through), S.TAG)
    with pytest.raises(lineage.ChainBroken, match="names fewer than two commits"):
        lineage.check_recorded(replay(W.F1, True), recorded(S.departure_entry("2026-11-20", [W.F1])), S.TAG)
    with pytest.raises(lineage.ChainBroken, match="not a commit of this history"):
        lineage.check_recorded(replay(W.F1, True), recorded(S.departure_entry("2026-11-20", [W.F0, "9" * 40])),
                               S.TAG)


def test_check_recorded_refuses_an_incomplete_v2_departure_record(monkeypatch):
    """The report reads v2's departure record by the replay gate's one rule (monitor/run.py departure_record): an
    entry that names only the commit the tag moved to, or skips a rule change of its move, is refused, and so is a
    second move that does not start where the first ended; the complete record passes (control)."""
    history = S.History(W.F0, W.F1, W.F2)  # every fake commit after F0 a rule change
    monkeypatch.setattr(lineage, "resolve", history.resolve)
    monkeypatch.setattr(lineage, "is_ancestor", history.is_ancestor)
    monkeypatch.setattr(lineage, "changes", history.changes)
    replay = S.fake_run("a" * 32, freeze_commit=W.F2, departure="true")

    def recorded(*entries):
        return S.NONE_RECORDED.replace("None yet.\n", "".join(entries))

    (e,) = lineage.check_recorded(replay, recorded(S.departure_entry("2026-11-20", [W.F0, W.F1, W.F2])), S.TAG)
    assert (e["from"], e["to"]) == (W.F0, W.F2)
    with pytest.raises(lineage.ChainBroken, match="names fewer than two commits"):
        lineage.check_recorded(replay, recorded(S.departure_entry("2026-11-20", [W.F2])), S.TAG)
    with pytest.raises(lineage.ChainBroken, match=f"does not name {W.F1[:12]}, which changed a rule file"):
        lineage.check_recorded(replay, recorded(S.departure_entry("2026-11-20", [W.F0, W.F2])), S.TAG)
    two = S.departure_entry("2026-11-20", [W.F0, W.F1]) + S.departure_entry("2026-11-27", [W.F1, W.F2])
    assert [x["to"] for x in lineage.check_recorded(replay, recorded(two), S.TAG)] == [W.F1, W.F2]
    apart = S.departure_entry("2026-11-20", [W.F0, W.F1]) + S.departure_entry("2026-11-27", [W.F0, W.F1, W.F2])
    with pytest.raises(lineage.ChainBroken, match=f"moves the tag from {W.F0[:12]}, not from {W.F1[:12]}"):
        lineage.check_recorded(replay, recorded(apart), S.TAG)


def test_check_named_refuses_a_predecessor_under_a_change_commit_the_entry_names(monkeypatch):
    """The entry names F0, a change commit F1 and F2: it moved the tag from F0 to F2. A predecessor that ran under F1
    ran under no commit the tag moved from or to, and is refused, although the entry names F1; predecessors under F0
    and F2 pass (controls)."""
    history = S.History(W.F0, W.F1, W.F2)  # every fake commit after F0 a rule change
    monkeypatch.setattr(lineage, "resolve", history.resolve)
    monkeypatch.setattr(lineage, "is_ancestor", history.is_ancestor)
    monkeypatch.setattr(lineage, "changes", history.changes)
    replay = S.fake_run("a" * 32, freeze_commit=W.F2, departure="true")
    section = S.NONE_RECORDED.replace("None yet.\n", S.departure_entry("2026-11-20", [W.F0, W.F1, W.F2]))
    (e,) = lineage.check_recorded(replay, section, S.TAG)
    assert (e["from"], e["to"]) == (W.F0, W.F2) and W.F1[:12] in [c[:12] for c in e["named"]]
    for ok in (W.F0, W.F2):
        lineage.check_named([S.fake_run("p" * 32, freeze_commit=ok)], [e], "replay a")
    with pytest.raises(lineage.ChainBroken, match=f"freeze commit {W.F1[:12]}, which no departure entry"):
        lineage.check_named([S.fake_run("p" * 32, freeze_commit=W.F1)], [e], "replay a")


# --- the standing of 2025: the one its gate logged ------------------------------------------------------------------

def test_the_report_prints_the_standing_its_gate_logged(monkeypatch):
    """A post-hoc test of record: the report prints the standing its gate logged. A ruling entry added to the section
    after the start changes nothing rendered; a gate's record that says another standing than the run's tag refuses,
    and so does a status computed from anything but the test of record."""
    w = S.World().install(monkeypatch)
    w.add_2025(standing="post_hoc", gate=W.gate("post_hoc", ["a"], W.ruling("post_hoc")))
    ctx = w.context()
    assert ctx["status"] == "post_hoc" and ctx["held"]["record"]["standing"] == "post_hoc"
    texts = {lang: RP.render(ctx, lang) for lang in LANGS}
    w.section = S.NONE_RECORDED.replace("None yet.\n", S.ruling_entry("2026-12-09", "clean", W.F0))
    later = w.context()
    assert all(RP.render(later, lang) == texts[lang] for lang in LANGS)
    w.files[("b" * 32, "results/held_out_gate.json")] = {**W.gate("post_hoc"), "standing": "clean"}
    with pytest.raises(ReportRefused, match="is not the one its gate's record logged"):
        w.context()
    forced = {**ctx, "status": "confirmed"}
    for lang in LANGS:
        line = RV.status_sentence(RV.environment(lang), forced, lang)
        with pytest.raises(ReportRefused, match="labelled held out"):
            RV.check_rendered(texts[lang], lang, forced, "validation", line)


def test_a_finished_2025_replay_without_its_gate_record_is_refused(monkeypatch):
    """The standing tag removed: the entry the run gives has no standing, refused by the ledger's check; the gate's
    record removed: the run lacks what the standing is read from. Both refuse the chain."""
    for spoil, words in (("standing", "standing is ''"),
                         ("record", "lacks the standing its gate logged when it opened, or the gate's record")):
        w = S.World().install(monkeypatch)
        w.add_2025()
        if spoil == "standing":
            del w.runs["b" * 32].data.tags["standing"]
        else:
            del w.files[("b" * 32, "results/held_out_gate.json")]
        with pytest.raises(lineage.ChainBroken, match=words):
            w.follow()
    # control: a start that did not finish, without either, is listed as unfinished
    w = S.World().install(monkeypatch)
    w.add_2025(finished=False)
    del w.runs["b" * 32].data.tags["standing"]
    del w.files[("b" * 32, "results/held_out_gate.json")]
    ctx = w.context()
    assert ctx["status"] == "unfinished" and not ctx["held"]["record"]["finished"]
    for lang in LANGS:
        assert "b" * 32 in section(RP.render(ctx, lang), 9)


@pytest.mark.parametrize("lang", LANGS)
def test_the_ruling_entry_the_gate_read_is_printed(monkeypatch, lang):
    """Control: a post-hoc test of record shows the ruling's head and the status post hoc; after a stopped start, the
    ruling the next start's gate read names that start, and the report prints both."""
    w = S.World().install(monkeypatch)
    ruled = W.ruling("post_hoc")
    w.add_2025(standing="post_hoc", gate=W.gate("post_hoc", ["a"], ruled))
    ctx = w.context()
    text = RP.render(ctx, lang)
    assert ruled["head"] in section(text, 9) and ctx["status"] == "post_hoc"
    assert RV._bullets_after(text, "## En bref" if lang == "fr" else "## In brief")[0].startswith(
        "**Statut : 2025 rejouée" if lang == "fr" else "**Status: 2025 replayed")
    w = S.World().install(monkeypatch)
    w.add_2025(run_id="5" * 32, finished=False)
    stopped = w.starts[-1]["started_utc"]
    ruled = W.ruling("clean", start=stopped)
    w.add_2025(gate=W.gate("clean", ["f"], ruled))
    ctx = w.context()
    assert ctx["held"]["record"]["run_id"] == "b" * 32 and [s["run_id"] for s in ctx["held"]["stopped"]] == ["5" * 32]
    nine = section(RP.render(ctx, lang), 9)
    assert ruled["head"] in nine and stopped in nine and nine.index("5" * 32) < nine.index("b" * 32)
