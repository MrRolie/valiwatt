"""v3's 2025 gate (docs/plan-v3.md, section 11.3 (3) and (4); monitor/run_v3.py `held_out_2025_v3`): v2's conditions
(a) to (f) on v3's tag, store, ledger, freeze record and departures section, and three more, (g) v2 first, (h) fetched
after the freeze and (i) no change after 2025, each refusal naming its condition and its evidence; which ruling lifts
which refusal, and the start's standing (`standing_v3`).

The pure units run on their own; the rest on the synthetic chain of tests/v3_world.py: a scratch repository holding
`v3-rules-frozen` on F0 (pinned), the step-5 rehearsal made by run_replay_v3(2024) itself, its ledger-v3 entry and the
freeze record committed, the export through 2025 (its files and its data-quality run, retrieved thirty days after the
build) and v2's branch with v2's 2025 test of record (started a day after that)."""

import os
import re
import subprocess

import pandas as pd
import pytest

import records_v3_world as rw
import v3_world as vw
from replay_world import artifact_file, departure_entry, ruling
from valiwatt import records_v3, tracking
from valiwatt.config import V3_FREEZE_TAG, data_last
from valiwatt.monitor import run as mrun
from valiwatt.monitor import run_v3
from valiwatt.monitor.run import FreezeError
from valiwatt.sources import eccc, hydroquebec

H = pd.Timedelta(hours=1)


@pytest.fixture
def chain(tmp_path, monkeypatch):
    return vw.chain_copy(tmp_path, monkeypatch, gate=True)


def gate(departure: bool = False) -> dict:
    """The 2025 gate's record (held_out_gate_v3 opens no run)."""
    return run_v3.held_out_gate_v3(2025, departure)[2]


def refused(match: str, departure: bool = False) -> str:
    """The 2025 gate refuses, its message matching `match`; no 2025 run was opened. Returns the message."""
    before = [r.info.run_id for r in vw.replay_runs_v3(2025)]
    with pytest.raises(FreezeError, match=match) as e:
        run_v3.held_out_gate_v3(2025, departure)
    assert [r.info.run_id for r in vw.replay_runs_v3(2025)] == before
    return str(e.value)


def head(chain) -> str:
    return rw.git(chain.repo, "rev-parse", "HEAD")


def section(chain, *entries: str) -> str:
    """The v3 departures section holding `entries`, committed. Returns the commit."""
    return rw.commit(chain.repo, {"AGENTS.md": rw.agents(*entries)}, "v3 departures")


def rule_change(chain, date: pd.Timestamp) -> str:
    """A rule file changed in a commit dated `date`, and `v3-rules-frozen` moved to it (the tag must hold the rules a
    v3 run is made under): a departure, recorded by `moved(chain, c)`; the data-quality run through 2025 made again
    under the new rules ((d)). Returns the commit."""
    c = rw.commit(chain.repo, {rw.RULE_FILE: "MIN = 5\n"}, "a rule change", date=date.isoformat())
    rw.tag(chain.repo, V3_FREEZE_TAG, c)
    vw.log_dq_2025(vw.ledger_time(chain.retrieved))
    return c


def moved(chain, c: str) -> str:
    """The departure entry recording the tag's move from F0 to `c`."""
    return departure_entry(chain.f0, c)


def on(c: str, outcome: str, start: str = "") -> str:
    """A ruling entry in v2's form on commit `c`."""
    return ruling(outcome, c, start)


def on_hi(condition: str, c: str, outcome: str = "post hoc") -> str:
    """A ruling entry on (h) or (i) on commit `c`."""
    return f"- **2026-12-01, the owner's ruling on 2025 ({condition}): {outcome}.** It applies to `{c[:12]}`.\n"


def on_v2_first(c: str) -> str:
    """A ruling entry on v2 first, naming the v2-branch commit by its full hash."""
    return f"- **2026-12-01, the owner's ruling on v2 first.** The test of record of v2 is at `{c}`.\n"


def evidence(chain, rev: str = "HEAD", **tags) -> str:
    """A finished evidence run from a clean tree at `rev`, on the gate's data-quality run (the latest one), as
    evidence-v3 logs one (the evidence itself is tested in tests/test_evidence_v3.py); tags: any to replace."""
    dq = run_v3.latest_dq_v3(tracking.client()).info.run_id
    return rw.fake_run({"kind": run_v3.EVIDENCE_KIND, "status": "complete", "dq_run_id": dq,
                        "rehearsal_run_id": chain.rehearsal, **rw.git_tags(chain.repo, rev), **tags})


def refetch(chain, t: pd.Timestamp) -> str:
    """The export through 2025 fetched again at `t`: its manifest's retrieval time, and a data-quality run reading it."""
    import json

    hydroquebec.manifest_path(2025).write_text(json.dumps({"retrieved_utc": vw.ledger_time(t)}), encoding="utf-8")
    return vw.log_dq_2025(vw.ledger_time(t))


def ends(chain) -> list[pd.Timestamp]:
    from valiwatt.freeze_record import read_freeze_record

    return [pd.Timestamp(r["ended_utc"]) for r in read_freeze_record()["data"]["runs"].values()]


def amend_date(chain, date: pd.Timestamp) -> str:
    """The head commit (the freeze record's) committed again at `date`, nothing else changed: the record committed
    once, at `date`, so the reflogs that keep the commit amended are emptied (rw.forget_reflogs), or the anchor would
    read two additions."""
    env = {**os.environ, "GIT_COMMITTER_DATE": date.isoformat(), "GIT_AUTHOR_DATE": date.isoformat()}
    subprocess.run(["git", *rw.SCRATCH_ID, "commit", "--amend", "--no-edit", "--quiet", "--no-verify"],
                   cwd=chain.repo, check=True, capture_output=True, env=env)
    rw.forget_reflogs(chain.repo)
    return head(chain)


def amend_record(chain, change) -> str:
    """The freeze record's commit (the head) made again with the record's JSON changed by change(data): still the one
    commit touching it, the reflogs that keep the commit amended emptied (as `amend_date`). Returns the commit."""
    import json

    from valiwatt.freeze_record import PATH

    path = chain.repo / PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    rw.git(chain.repo, "add", PATH)
    subprocess.run(["git", *rw.SCRATCH_ID, "commit", "--amend", "--no-edit", "--quiet", "--no-verify"],
                   cwd=chain.repo, check=True, capture_output=True)
    rw.forget_reflogs(chain.repo)
    return head(chain)


def forget(run_id: str) -> None:
    """A run gone from the store, as `mlflow gc` leaves a deleted one."""
    c = tracking.client()
    c.delete_run(run_id)
    c._tracking_client.store._hard_delete_run(run_id)


def dq_2025_runs() -> list[str]:
    """Every v3 data-quality run through 2025 in the store, deleted included."""
    from mlflow.entities import ViewType

    from valiwatt.quality import run_v3 as dq_v3

    c = tracking.client()
    exp = c.get_experiment_by_name(dq_v3.EXPERIMENT)
    return [r.info.run_id for r in c.search_runs([exp.experiment_id], "params.through = '2025'",
                                                  run_view_type=ViewType.ALL)]


def stop_a_start(departure: bool = False) -> dict:
    """A real 2025 start that passed its gate, logged its gate record, and stopped reading its inputs. Returns it as
    replay_starts_v3 gives it, its gate record included."""
    from replay_world import StoppedHere

    def stop(*args, **kwargs):
        raise StoppedHere("inputs")

    with pytest.MonkeyPatch.context() as m:
        m.setattr(run_v3, "_read_inputs_v3", stop)
        with pytest.raises(StoppedHere):
            run_v3.run_replay_v3(2025, n_boot=20, departure=departure)
    (start,) = records_v3.replay_starts_v3(2025)
    assert start["gate"] is not None and not start["finished"]
    return start


def stopped_start(chain) -> dict:
    """A 2025 start under the tag that stopped after it opened (MLflow FAILED, status started), on the head. Returns
    its start as replay_starts_v3 gives it."""
    freeze = records_v3.current_freeze()
    rid = rw.fake_run({"kind": run_v3.KIND, "year": "2025", "freeze_tag": V3_FREEZE_TAG,
                       "freeze_commit": freeze["freeze_commit"], "status": "started", "stage": "inputs_read",
                       "error": "KeyboardInterrupt", **rw.git_tags(chain.repo)}, status="FAILED")
    (start,) = [s for s in records_v3.replay_starts_v3(2025) if s["run_id"] == rid]
    return start


# ------------------------------------------------------------------------------------------ the pure units

def commits(*names):
    """Fake 40-hex commits."""
    return {n: (n * 40)[:40] for n in names}


C = commits("a", "b", "c", "d", "e", "f")
# A line of history a <- b <- c <- d (HEAD); e and f off it.
LINE = ["a", "b", "c", "d"]


def ancestor(x: str, y: str) -> bool:
    names = {v: k for k, v in C.items()}
    a, b = names.get(x), names.get(y)
    return a in LINE and b in LINE and LINE.index(a) <= LINE.index(b)


def entry(kind: str, commit: str, outcome: str | None = "post_hoc", start: str = "") -> dict:
    head_ = {"v2_form": f"2026-12-01, the owner's ruling on 2025: {(outcome or '').replace('_', ' ')}",
             "h": "2026-12-01, the owner's ruling on 2025 (h): post hoc",
             "i": "2026-12-01, the owner's ruling on 2025 (i): post hoc",
             "v2_first": "2026-12-01, the owner's ruling on v2 first"}[kind]
    return {"kind": kind, "condition": {"v2_form": "a_f", "h": "h", "i": "i", "v2_first": "g"}[kind], "head": head_,
            "day": "2026-12-01", "outcome": None if kind == "v2_first" else outcome, "commit": commit, "start": start}


V2 = {"commit": C["e"]}


def standing(causes, rulings, *, rule_commits=(), starts=(), proven=True):
    return run_v3.standing_v3(list(causes), list(rulings), rule_commits=list(rule_commits), starts=list(starts),
                              v2=V2, head=C["d"], evidence=lambda c: "9" * 32 if proven else None,
                              is_ancestor=ancestor)


def test_standing_v3_no_cause_and_no_ruling_is_clean():
    assert standing([], []) == {"ruling": None, "rulings": [], "standing": "clean"}


@pytest.mark.parametrize("outcome", ["clean", "post_hoc"])
def test_standing_v3_a_lifted_by_v2_s_form_takes_its_outcome(outcome):
    s = standing(["a"], [entry("v2_form", C["c"], outcome)], rule_commits=[{"commit": C["b"], "paths": ["x"]}])
    assert s["standing"] == outcome and s["ruling"]["commit"] == C["c"] and s["rulings"][0]["evidence_run_id"]


def test_standing_v3_v2_s_form_not_after_every_rule_commit_does_not_apply():
    with pytest.raises(FreezeError, match=r"\(a\) is lifted only by"):
        standing(["a"], [entry("v2_form", C["b"], "clean")], rule_commits=[{"commit": C["c"], "paths": ["x"]}])


@pytest.mark.parametrize("kind", ["v2_form", "h"])
def test_standing_v3_an_entry_off_the_head_s_history_does_not_apply(kind):
    """An entry whose commit is not the head or one of its ancestors applies to nothing (no rule commit to descend
    from here, so only that condition can refuse it)."""
    cause = {"v2_form": "a", "h": "h"}[kind]
    with pytest.raises(FreezeError, match=rf"\({cause}\) is lifted only by"):
        standing([cause], [entry(kind, C["f"], "clean" if kind == "v2_form" else "post_hoc")])
    assert standing([cause], [entry(kind, C["c"], "clean" if kind == "v2_form" else "post_hoc")])["rulings"]


def test_standing_v3_f_lifted_only_by_an_entry_naming_the_latest_start():
    starts = [{"started_utc": "2026-12-01T00:00:00.000Z", "git_commit": C["a"]},
              {"started_utc": "2026-12-02T00:00:00.000Z", "git_commit": C["b"]}]
    with pytest.raises(FreezeError, match=r"\(f\)"):
        standing(["f"], [entry("v2_form", C["c"], "clean", starts[0]["started_utc"])], starts=starts)
    s = standing(["f"], [entry("v2_form", C["c"], "clean", starts[1]["started_utc"])], starts=starts)
    assert s["standing"] == "clean" and s["ruling"]["start"] == starts[1]["started_utc"]


@pytest.mark.parametrize("cause", ["h", "i"])
def test_standing_v3_h_or_i_lifted_by_its_own_entry_is_post_hoc(cause):
    s = standing([cause], [entry(cause, C["c"])])
    assert s["standing"] == "post_hoc" and s["ruling"] is None and [r["kind"] for r in s["rulings"]] == [cause]


@pytest.mark.parametrize("cause,other", [("h", "i"), ("i", "h"), ("h", "v2_form"), ("i", "v2_form")])
def test_standing_v3_h_or_i_is_lifted_by_no_other_entry(cause, other):
    with pytest.raises(FreezeError, match=rf"\({cause}\) is lifted only by"):
        standing([cause], [entry(other, C["c"], "post_hoc")])


@pytest.mark.parametrize("order", ["v2_form_first", "hi_first"])
def test_standing_v3_h_or_i_among_the_causes_is_post_hoc_whatever_v2_s_form_says(order):
    rulings = [entry("v2_form", C["c"], "clean"), entry("i", C["c"])]
    s = standing(["a", "i"], rulings if order == "v2_form_first" else rulings[::-1],
                 rule_commits=[{"commit": C["b"], "paths": ["x"]}])
    assert s["standing"] == "post_hoc" and s["ruling"]["outcome"] == "clean"


def test_standing_v3_v2_first_lifts_g_only_with_the_commit_g_read_and_no_evidence():
    s = run_v3.standing_v3(["g"], [entry("v2_first", C["e"])], rule_commits=[], starts=[], v2=V2, head=C["d"],
                           evidence=lambda c: None, is_ancestor=ancestor)
    assert s["standing"] == "clean" and s["ruling"] is None and s["rulings"][0]["evidence_run_id"] == ""
    with pytest.raises(FreezeError, match=r"\(g\) is lifted only by"):
        standing(["g"], [entry("v2_first", C["f"])])
    with pytest.raises(FreezeError, match=r"\(h\) is lifted only by"):
        standing(["g", "h"], [entry("v2_first", C["e"])])
    with pytest.raises(FreezeError, match=r"\(a\) is lifted only by"):
        standing(["a", "g"], [entry("v2_first", C["e"])], rule_commits=[{"commit": C["b"], "paths": ["x"]}])


@pytest.mark.parametrize("kind", ["v2_form", "h", "i"])
def test_standing_v3_an_applied_entry_needs_its_evidence_run(kind):
    cause = {"v2_form": "a", "h": "h", "i": "i"}[kind]
    with pytest.raises(FreezeError, match="run `valiwatt evidence-v3` first"):
        standing([cause], [entry(kind, C["c"])], rule_commits=[{"commit": C["b"], "paths": ["x"]}], proven=False)


def test_standing_v3_the_last_applying_v2_form_entry_gives_the_standing_when_nothing_fired():
    s = standing([], [entry("v2_form", C["b"], "clean"), entry("v2_form", C["c"], "post_hoc")])
    assert s["standing"] == "post_hoc" and s["ruling"]["commit"] == C["c"]


@pytest.mark.parametrize("kind", ["v2_form", "h"])
def test_standing_v3_an_entry_older_than_an_earlier_start_s_code_does_not_apply(kind):
    """An entry lifts the next start only: its commit descends from the code of every earlier 2025 start. A ruling on
    b, older than the start made on c, lifts nothing for the start after it; one on c (control) does."""
    cause, outcome = {"v2_form": ("a", "clean"), "h": ("h", "post_hoc")}[kind]
    rc = [{"commit": C["a"], "paths": ["x"]}] if kind == "v2_form" else []
    starts = [{"started_utc": "2026-12-01T00:00:00.000Z", "git_commit": C["c"]}]
    with pytest.raises(FreezeError, match=rf"\({cause}\) is lifted only by"):
        standing([cause], [entry(kind, C["b"], outcome)], rule_commits=rc, starts=starts)
    assert standing([cause], [entry(kind, C["c"], outcome)], rule_commits=rc, starts=starts)["rulings"]


@pytest.mark.parametrize("kind", ["h", "i"])
def test_standing_v3_an_applying_h_or_i_entry_keeps_the_start_post_hoc_though_its_cause_is_quiet(kind):
    """Section 11.3 (4), held however the 2025 time is read: an (h) or (i) entry that applies to the start was ruled
    for this code, so the standing stays post hoc when its condition does not fire, alone or beside a clean entry in
    v2's form."""
    assert standing([], [entry(kind, C["c"])])["standing"] == "post_hoc"
    s = standing(["a"], [entry("v2_form", C["c"], "clean"), entry(kind, C["c"])],
                 rule_commits=[{"commit": C["b"], "paths": ["x"]}])
    assert s["standing"] == "post_hoc" and s["ruling"]["outcome"] == "clean"


def test_time_2025_is_the_earlier_of_the_manifest_and_v2_s_first_2025_start_read_as_times():
    """D8: times compared as times, whatever their form; v2's ledger without a 2025 entry leaves the manifest's."""
    t = run_v3.time_2025("2026-12-01T05:00:00-05:00", {"earliest_2025_started_utc": None})
    assert (pd.Timestamp(t["utc"]), t["source"]) == (pd.Timestamp("2026-12-01T10:00Z"), "manifest")
    t = run_v3.time_2025("2026-12-01T10:00:00Z", {"earliest_2025_started_utc": "2026-12-01T09:59:59.000Z"})
    assert (pd.Timestamp(t["utc"]), t["source"]) == (pd.Timestamp("2026-12-01T09:59:59Z"), "v2_ledger")
    t = run_v3.time_2025("2026-12-01T10:00:00Z", {"earliest_2025_started_utc": "2026-12-01T10:00:01.000Z"})
    assert t["source"] == "manifest"


def test_time_2025_is_never_later_than_an_earlier_reading_of_it():
    """The earliest of the current reading and every earlier one, read as times; the current reading on a tie."""
    v2 = {"earliest_2025_started_utc": "2026-12-03T00:00:00.000Z"}
    earlier = [{"utc": "2026-12-01T05:00:00-05:00", "source": "earlier_start", "run_id": "1" * 32},
               {"utc": "2026-12-01T09:00:00.000Z", "source": "dq_retrieved", "run_id": "2" * 32}]
    t = run_v3.time_2025("2026-12-02T00:00:00Z", v2, earlier)
    assert (pd.Timestamp(t["utc"]), t["source"], t["source_run_id"]) == (pd.Timestamp("2026-12-01T09:00Z"),
                                                                          "dq_retrieved", "2" * 32)
    t = run_v3.time_2025("2026-12-01T09:00:00Z", v2, earlier)
    assert (t["source"], t["source_run_id"]) == ("manifest", None)


def test_dated_commits_gives_each_commit_its_date_and_paths_oldest_first(tmp_path, monkeypatch):
    s = rw.scratch_store(tmp_path, monkeypatch, frozen=False)
    a = rw.commit(s.repo, {"x.txt": "1\n"}, "a", date="2026-12-01T10:00:00-05:00")
    rw.git(s.repo, "mv", "x.txt", "y.txt")
    b = rw.commit(s.repo, {}, "b", date="2026-12-02T10:00:00Z")
    got = run_v3.dated_commits(s.f0, b)
    assert [g["commit"] for g in got] == [a, b]
    assert got[0]["committer_utc"] == pd.Timestamp("2026-12-01T15:00Z") and got[0]["paths"] == ["x.txt"]
    assert sorted(got[1]["paths"]) == ["x.txt", "y.txt"]


NON_ASCII_RULE = "src/valiwatt/monitor/règle.py"


def test_dated_commits_read_a_non_ascii_path_as_the_tree_names_it(tmp_path, monkeypatch):
    """A rule file named with letters outside ASCII is read by its name (NUL-separated, never quoted), so it is a rule
    file to (a)'s walk and to (i); so is one holding a double quote, read whole, and a commit touching no path."""
    s = rw.scratch_store(tmp_path, monkeypatch)
    c = rw.commit(s.repo, {NON_ASCII_RULE: "MIN = 6\n"}, "a rule file named in French")
    (got,) = run_v3.dated_commits(s.pin, c)
    assert got["paths"] == [NON_ASCII_RULE] and mrun._is_change(NON_ASCII_RULE)
    assert run_v3._rule_commits_v3(s.f0) == [{"commit": c, "paths": [NON_ASCII_RULE]}]
    quoted = 'src/valiwatt/monitor/r"q.py'
    q = rw.commit(s.repo, {quoted: "MIN = 7\n", "docs/a.md": "a\n"}, "a quoted path")
    e = rw.commit(s.repo, message="nothing", empty=True)
    assert [(x["commit"], sorted(x["paths"])) for x in run_v3.dated_commits(c, e)] == [
        (q, ["docs/a.md", quoted]), (e, [])]
    assert run_v3._rule_commits_v3(s.f0)[-1] == {"commit": q, "paths": [quoted]}


# A rule file whose name holds U+2028 (a line separator to Python's splitlines, never to git): cut at it, it would read
# as "src/valiwatt/x.pyc" and ".py", neither of them a rule file.
SEPARATED_RULE = "src/valiwatt/x.pyc\u2028.py"


# ------------------------------------------------------------------------------------------- on the chain

def test_a_clean_2025_start_with_everything_in_order_passes(chain):
    """The control: the gate passes, its record holding every run the freeze record names, the hashes of what judges
    2025, the causes [] and the standing clean; run_replay_v3(2025) then opens its run, logs the gate's record at once
    and finishes, labelled held out."""
    from valiwatt.freeze_record import read_freeze_record

    rec = gate()
    data = read_freeze_record()["data"]
    assert (rec["causes"], rec["ruling"], rec["standing"]) == ([], None, "clean")
    assert (rec["frozen_run_id"], rec["validation_run_id"], rec["rehearsal_run_id"], rec["dq_run_id"]) == (
        chain.frozen_2025, chain.validation, chain.rehearsal, chain.dq25)
    assert rec["freeze_record"]["commit"] == chain.record and rec["freeze_record"]["first_pin"] == chain.f0
    assert mrun.frozen_digest(rec["frozen_sha256"]) == data["frozen_sha256"]
    assert rec["v2_first"]["commit"] == chain.v2 and rec["t2025"]["source"] == "manifest"
    assert run_v3.run_replay_v3(2025, n_boot=20) == 0
    (run,) = vw.replay_runs_v3(2025)
    t = run.data.tags
    assert (t["status"], t["stage"], t["evaluation"], t["standing"]) == ("complete", "logged", "held_out", "clean")
    assert t["freeze_record_commit"] == chain.record and t["frozen_run_id"] == chain.frozen_2025
    c = tracking.client()
    logged = mrun._load_json_artifact(c, run.info.run_id, mrun.GATE_RECORD)
    assert logged["causes"] == [] and logged["standing"] == "clean"
    results = mrun._load_json_artifact(c, run.info.run_id, "results/replay.json")
    assert results["freeze_record"] == {"commit": chain.record, "sha256": rec["freeze_record"]["sha256"]}
    assert results["evaluation"] == "held_out" and set(results["verdict"]) == {"terms", "family", "label"}


def test_the_v3_ledger_syncs_the_rehearsal_and_a_2025_start_from_the_runs_they_logged(chain):
    """The seam between the replay and ledger-v3 (report/ledger_v3.py `sync`): on the committed ledger holding the
    step-5 rehearsal, a sync of the chain's store changes nothing; after a clean 2025 start, it appends that start,
    labelled held out with its standing clean, the label checked against the starts and the gate's record it logged."""
    from valiwatt.report import ledger_v3

    path = ledger_v3.path()
    before = path.read_bytes()
    (entry,) = ledger_v3.sync()
    assert path.read_bytes() == before and (entry["run_id"], entry["year"]) == (chain.rehearsal, 2024)
    assert (entry["freeze_commit"], entry["evaluation"], entry["standing"]) == (chain.f0, "seen_by_developers", "")
    assert run_v3.run_replay_v3(2025, n_boot=20) == 0
    (run,) = vw.replay_runs_v3(2025)
    first, held = ledger_v3.sync()
    assert first == entry and ledger_v3.read() == [first, held]
    assert (held["run_id"], held["year"], held["evaluation"], held["standing"]) == (run.info.run_id, 2025, "held_out",
                                                                                     "clean")
    assert held["freeze_commit"] == chain.f0 and set(held["verdict"]) == {"terms", "family"}


@pytest.mark.parametrize("case", ["failed", "code", "control"])
def test_a_completed_test_in_ledger_v3_never_becomes_a_stopped_start(chain, case):
    """(c), the store against the committed ledger (v2's section 11.3.4 (c)): test A finished, held out, and its
    ledger-v3 entry committed; then A's run made FAILED in the store (reopened, or ended wrongly), or one of its tags
    changed (its code hash): the next start is refused (c), the store and the ledger disagreeing, never read as
    following a stopped start (no (f)). The control: the store untouched, the next start's gate passes."""
    from valiwatt.report import ledger_v3

    assert run_v3.run_replay_v3(2025, n_boot=20) == 0
    (a,) = vw.replay_runs_v3(2025)
    ledger_v3.sync()
    rw.commit(chain.repo, {}, "ledger-v3 with the test of record")
    assert [e["evaluation"] for e in ledger_v3.read() if e["year"] == 2025] == ["held_out"]
    c = tracking.client()
    if case == "control":
        assert gate()["causes"] == []
        return
    if case == "failed":
        c.set_terminated(a.info.run_id, "FAILED")
        said = rf"run {a.info.run_id} is FAILED in the store, status tag 'complete', yet the ledger records it finished"
    else:
        c.set_tag(a.info.run_id, "code_sha256", "d" * 64)
        said = rf"run {a.info.run_id} in the store disagrees with its ledger-v3 entry on \['code_sha256'\]"
    msg = refused(r"\(c\) the run store and the committed ledger-v3 disagree: " + said)
    assert "(f)" not in msg


def v2_ledger_at(chain, case: str) -> None:
    repo = chain.repo
    if case == "no_ref":
        rw.git(repo, "update-ref", "-d", "refs/heads/v2")
    elif case == "no_ledger":
        branch = rw.git(repo, "symbolic-ref", "--short", "HEAD")
        rw.git(repo, "checkout", "-q", "--detach")
        rw.git(repo, "rm", "-q", "docs/replay-ledger.json")
        rw.git(repo, "update-ref", "refs/heads/v2", rw.commit(repo, {}, "no ledger"))
        rw.git(repo, "checkout", "-q", branch)
    elif case == "no_entry":
        vw.v2_branch(repo, vw.v2_2024_entry())
    elif case == "invalid_ledger":
        vw.v2_branch(repo, vw.v2_2024_entry(),
                     vw.v2_2025_entry(vw.ledger_time(chain.v2_started), evaluation="seen_by_developers"))
    elif case == "bad_shape":
        import json

        text = json.loads(vw.v2_ledger_text(vw.v2_2024_entry(), vw.v2_2025_entry(vw.ledger_time(chain.v2_started))))
        branch = rw.git(repo, "symbolic-ref", "--short", "HEAD")
        rw.git(repo, "checkout", "-q", "--detach")
        c = rw.commit(repo, {"docs/replay-ledger.json": json.dumps(text | {"x": 1}, indent=2) + "\n"}, "v2's ledger")
        rw.git(repo, "update-ref", "refs/heads/v2", c)
        rw.git(repo, "checkout", "-q", branch)
    elif case == "origin_fallback":
        rw.git(repo, "update-ref", "refs/remotes/origin/v2", chain.v2)
        rw.git(repo, "update-ref", "-d", "refs/heads/v2")


@pytest.mark.parametrize("case", ["no_ref", "no_ledger", "no_entry", "invalid_ledger", "bad_shape"])
def test_the_v3_start_refuses_while_v2_s_test_of_record_has_not_finished(chain, case):
    """(g): v2's test of record, read at refs/heads/v2 (else refs/remotes/origin/v2) and never fetched, is the earliest
    2025 entry of v2's ledger labelled held out or post hoc. Without the ref, the ledger, or such an entry (a v2 2025
    start that stopped never enters v2's ledger: "unfinished" is the no-entry case), the start is refused; and so it is
    on a ledger v2's own check refuses (a lone 2025 entry labelled seen by the developers: v2's check never lets a
    first 2025 entry carry that label, so a valid ledger's first 2025 entry is always held out or post hoc), and on
    one holding a key beside `replays`, though its entries are v2's test of record."""
    v2_ledger_at(chain, case)
    refused(r"\(g\) v2's test of record has not finished")


def test_the_v3_start_reads_v2_s_test_of_record_at_origin_when_v2_s_branch_is_absent(chain):
    v2_ledger_at(chain, "origin_fallback")
    rec = gate()
    assert rec["v2_first"]["ref"] == "refs/remotes/origin/v2" and rec["causes"] == []


def test_the_gate_reads_v2_s_ledger_at_the_ref_never_the_working_copy(chain):
    """The committed ledger on the head holds a v2 2025 test of record and v2's branch holds none: (g) fires; the
    reverse (the gate base: v2's branch holds it, the head's ledger does not) passes."""
    assert gate()["causes"] == []
    rw.commit(chain.repo, {"docs/replay-ledger.json": vw.v2_ledger_text(
        vw.v2_2024_entry(), vw.v2_2025_entry(vw.ledger_time(chain.v2_started)))}, "v2's ledger, here")
    vw.v2_branch(chain.repo, vw.v2_2024_entry())
    refused(r"\(g\)")


def test_a_ruling_on_v2_first_lifts_g_only(chain, tmp_path, monkeypatch):
    """A "v2 first" entry naming the v2-branch commit (g) read lifts (g), with no evidence and no standing; it lifts
    neither (a) nor (h); naming another commit, it lifts nothing."""
    v2 = vw.v2_branch(chain.repo, vw.v2_2024_entry())
    section(chain, on_v2_first(head(chain)))
    refused(r"\(g\) is lifted only by")
    section(chain, on_v2_first(v2))
    rec = gate()
    assert rec["causes"] == ["g"] and rec["standing"] == "clean" and rec["ruling"] is None
    assert [(r["kind"], r["commit"], r["evidence_run_id"]) for r in rec["rulings"]] == [("v2_first", v2, "")]

    refetch(chain, chain.record_date - H)  # (h) fires too
    msg = refused(r"\(h\) is lifted only by")
    assert "(g) is lifted" not in msg

    c = vw.chain_copy(tmp_path / "a", monkeypatch, gate=True)
    v2 = vw.v2_branch(c.repo, vw.v2_2024_entry())
    c1 = rule_change(c, chain.retrieved - pd.Timedelta(days=1))
    section(c, moved(c, c1), on_v2_first(v2))
    msg = refused(r"\(a\) is lifted only by", departure=True)
    assert "(g) is lifted" not in msg


ARTIFACTS = ["production", "v2_noon", "v1_noon", "v2_midnight", "thresholds", "cold_c", "reference"]


def edit_json(run_id: str, path: str, change) -> None:
    import json

    f = artifact_file(run_id, path)
    data = json.loads(f.read_text(encoding="utf-8"))
    change(data)
    f.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("item", ARTIFACTS)
def test_an_artifact_differing_from_the_freeze_record_is_refused(chain, monkeypatch, item):
    """(b): what judges 2025, read now through frozen_chain_v3 of the runs the freeze record names, against the hashes
    the committed record holds, each differing item named: a comparison model, the thresholds, cold_c and the
    reference by a store edit (a comparison model with the hash its run recorded, so only the record can tell); the
    production model as read."""
    import dataclasses

    from valiwatt.forecast.freeze import FrozenModel

    c = tracking.client()
    if item == "production":
        read = mrun._production_model
        monkeypatch.setattr(mrun, "_production_model", lambda run: dataclasses.replace(
            read(run), intercept=read(run).intercept + 1e-6))
        named = "production"
    elif item in ("v2_noon", "v1_noon", "v2_midnight"):
        f = artifact_file(chain.frozen_2025, f"comparators/{item}.json")
        m = FrozenModel.from_json(f.read_text(encoding="utf-8"))
        changed = dataclasses.replace(m, intercept=m.intercept + 1e-6)
        f.write_text(changed.to_json(), encoding="utf-8")
        c.set_tag(chain.frozen_2025, f"comparator_sha256.{item}", changed.sha256())
        named = f"comparators.{item}"
    elif item == "thresholds":
        edit_json(chain.validation, "results/validation.json",
                  lambda d: d["thresholds"]["2025"]["drift"].update(temperature_high=9.0))
        named = "thresholds"
    elif item == "cold_c":
        edit_json(chain.validation, "results/validation.json", lambda d: d["config"].update(cold_c=-30.0))
        named = "cold_c"
    else:
        f = artifact_file(chain.validation, "frames/oos.parquet")
        oos = pd.read_parquet(f)
        oos.loc[oos["pred_v3"].last_valid_index(), "pred_v3"] += 1.0
        oos.to_parquet(f)
        named = "reference"
    msg = refused(r"\(b\) the artifacts that judge 2025 differ from docs/freeze-v3.json")
    assert repr(named) in msg and all(repr(x) not in msg for x in
                                      ["production", "thresholds", "cold_c", "reference"] if x != named)


NAMED = r"\(b\) the runs the freeze record names: .*"
REDERIVED = r"\(b\) the freeze record's writer, run again on the store, refuses its runs: .*"


@pytest.mark.parametrize("case", ["dirty", "rules", "pre_freeze", "end_time", "unfinished", "git_commit", "dq_hashes",
                                  "absent", "reproduction", "fresh_clone", "byte_copies", "model"])
def test_a_named_run_from_a_dirty_tree_or_other_rules_is_refused(chain, case):
    """(b): each run the freeze record names, against the store: a tree that was not clean, other rules, another freeze
    tag (a pre-freeze run), another end time, not finished (its end kept), another code commit, the data-quality run's
    raw-file hashes, a run the store lacks (the record committed naming it). Then the record's lineage claims, never
    taken from the record: its own writer, run again on the store, refuses a validation run whose reproduction failed
    or whose byte copies were not run, and a data-quality run labelled a fresh clone, though the record says
    passed; and it gives another record than one committed naming another model frozen for 2024 (consistently, in the
    frozen 2024 and rehearsal runs, so only the writer can tell)."""
    import sqlite3

    c = tracking.client()
    if case == "dirty":
        c.set_tag(chain.validation, "git_dirty", "true")
        match = NAMED + "ran from a tree that was not clean"
    elif case == "rules":
        c.set_tag(chain.validation, "rules_sha256", "0" * 64)
        match = NAMED + "ran under other rules"
    elif case == "pre_freeze":
        c.set_tag(chain.validation, "freeze_tag", records_v3.V3_PRE_FREEZE_TAG)
        match = NAMED + "freeze_tag is 'v3-pre-freeze' in the store"
    elif case == "end_time":
        run = c.get_run(chain.frozen_2025)
        c.set_terminated(chain.frozen_2025, "FINISHED", end_time=run.info.end_time + 1000)
        match = NAMED + "ended_utc is"
    elif case == "unfinished":
        c.set_terminated(chain.validation, "FAILED", end_time=c.get_run(chain.validation).info.end_time)
        match = NAMED + f"the validation run {chain.validation} is FAILED, not FINISHED$"
    elif case == "git_commit":
        c.set_tag(chain.validation, "git_commit", "f" * 40)
        match = NAMED + f"the validation run {chain.validation}'s git_commit is '{'f' * 40}' in the store"
    elif case == "dq_hashes":
        con = sqlite3.connect(chain.store / "mlflow.db")
        con.execute("update params set value = ? where run_uuid = ? and key = 'hq_sha256'", ("0" * 64, chain.dq24))
        con.commit()
        con.close()
        match = NAMED + f"the data-quality run {chain.dq24}'s hq_sha256 differs from the freeze record's"
    elif case == "absent":
        amend_record(chain, lambda d: d["runs"]["frozen_2025"].update(run_id="9" * 32))
        match = NAMED + f"the frozen_2025 run {'9' * 32} is not in v3's run store"
    elif case == "reproduction":
        c.set_tag(chain.validation, "reproduction", "failed")
        match = REDERIVED + "its reproduction is 'failed', not 'passed'"
    elif case == "fresh_clone":
        c.set_tag(chain.dq24, "fresh_clone", "true")
        match = REDERIVED + "its fresh_clone is 'true', not 'false'"
    elif case == "byte_copies":
        c.set_tag(chain.validation, "byte_copies", "not_run")
        match = REDERIVED + "its byte_copies is 'not_run', not 'passed'"
    else:
        amend_record(chain, lambda d: [d["runs"][role].update(model_sha256="0" * 64)
                                       for role in ("frozen_2024", "rehearsal_2024")])
        match = (r"\(b\) the freeze record's writer, run again on the store, gives another record than the one "
                 r"committed, at \['runs.frozen_2024.model_sha256', 'runs.rehearsal_2024.model_sha256'\]")
    refused(match)


@pytest.mark.parametrize("case", ["record_commit", "run_end"])
def test_an_export_retrieved_before_the_freeze_record_refuses_h(chain, case):
    """(h): the 2025 time (here the manifest's retrieval time) at or before the freeze record's commit, or before a
    named run's end, refuses; an (h) entry and its evidence lift it, the standing post hoc."""
    if case == "record_commit":
        t = chain.record_date - H  # after every run's end, before the record's commit
    else:
        t = min(ends(chain)) - H / 60  # before every run's end, after the record's commit
        amend_date(chain, t - pd.Timedelta(days=1))
    refetch(chain, t)
    msg = refused(r"\(h\) the 2025 time is")
    assert ("the runs" in msg) == (case == "run_end")
    section(chain, on_hi("h", head(chain)))
    evidence(chain)
    rec = gate()
    assert rec["causes"] == ["h"] and rec["standing"] == "post_hoc"


def test_a_refetched_export_with_a_v2_2025_entry_before_the_record_refuses_h(chain):
    """The manifest is later than everything, and v2's 2025 test of record started before the freeze record's commit:
    the 2025 time is v2's start, and (h) fires."""
    vw.v2_branch(chain.repo, vw.v2_2024_entry(), vw.v2_2025_entry(vw.ledger_time(chain.record_date - H)))
    msg = refused(r"\(h\) the 2025 time is")
    assert "(v2_ledger)" in msg


@pytest.mark.parametrize("when", ["after", "before"])
def test_a_rule_change_committed_after_the_2025_time_refuses_i(chain, when):
    """(i): a commit dated after the 2025 time, from the freeze record's commit to the head, touching a rule file,
    fires (i) beside (a); the control, dated before the 2025 time, fires (a) only."""
    shift = pd.Timedelta(days=1) if when == "after" else -pd.Timedelta(days=1)
    c1 = rule_change(chain, chain.retrieved + shift)
    section(chain, moved(chain, c1))
    msg = refused(r"\(a\) commits since", departure=True)
    assert ("(i) commits committed after the 2025 time" in msg) == (when == "after")


@pytest.mark.parametrize("case", ["separator", "quote"])
def test_a_rule_file_named_with_a_line_separator_after_the_2025_time_is_seen_by_a_and_i(chain, case):
    """(a)'s walk and (i) read each path a commit touches whole (git's NUL-separated output): a rule file named with
    U+2028, added after the 2025 time and deleted in the next commit (the tree the frozen one again), fires (a) and
    (i), naming both commits (separator). A docs file whose name holds a double quote, committed after the 2025 time,
    refuses nothing (quote): no path is refused for its name."""
    after = chain.v2_started + H
    if case == "quote":
        rw.commit(chain.repo, {'docs/a"b.md': "notes\n"}, "a docs file with a quote", date=after.isoformat())
        assert gate()["causes"] == []
        return
    added = rw.commit(chain.repo, {SEPARATED_RULE: "MIN = 6\n"}, "a rule file named with U+2028",
                      date=after.isoformat())
    (chain.repo / SEPARATED_RULE).unlink()
    removed = rw.commit(chain.repo, {}, "its removal", date=(after + H).isoformat())
    assert mrun._is_change(SEPARATED_RULE) and not any(mrun._is_change(p) for p in SEPARATED_RULE.split("\u2028"))
    assert run_v3._rule_commits_v3(chain.f0) == [{"commit": added, "paths": [SEPARATED_RULE]},
                                                 {"commit": removed, "paths": [SEPARATED_RULE]}]
    msg = refused(r"\(a\) commits since")
    assert "(i) commits committed after the 2025 time" in msg and added[:12] in msg and removed[:12] in msg


def test_a_non_ascii_rule_file_committed_after_the_2025_time_refuses_the_start(chain):
    """A rule file named with letters outside ASCII, added after the 2025 time: the start is refused, naming it. Since
    monitor/rules.py `rule_files` reads git's names NUL-separated, such a file is a rule file, so the earliest check
    that fires is the freeze check (rule files equal to `v3-rules-frozen`'s), before (a) and (i) (which read the same
    name: test_dated_commits_read_a_non_ascii_path_as_the_tree_names_it)."""
    rw.commit(chain.repo, {NON_ASCII_RULE: "MIN = 6\n"}, "a rule file named in French",
              date=(chain.v2_started + H).isoformat())
    msg = refused(r"rule files differ from `v3-rules-frozen`")
    assert f"['{NON_ASCII_RULE}']" in msg


@pytest.mark.parametrize("path, rule", [("src/valiwatt/model__pycache__v3.py", True),
                                         ("src/valiwatt/cli.py_helpers.py", True),
                                         ("src/valiwatt/cli.py", False),
                                         ("src/valiwatt/report/x.py", False),
                                         ("src/valiwatt/monitor/__pycache__/run.cpython-312.pyc", False)])
def test_only_the_exempt_files_folders_and_caches_themselves_are_not_rule_files(path, rule):
    """monitor/rules.py `_is_rule`: an exempt file is exempt only itself (cli.py, never cli.py_helpers.py), an exempt
    folder everything under it (report/), a cache only a whole __pycache__ folder or a .pyc file, never a name merely
    holding `__pycache__` (the third review's two names)."""
    from valiwatt.monitor.rules import _is_rule

    assert _is_rule(path) is rule


@pytest.mark.parametrize("name", ["src/valiwatt/model__pycache__v3.py", "src/valiwatt/cli.py_helpers.py"],
                         ids=["pycache", "exempt_prefix"])
def test_a_rule_file_named_like_an_exempt_one_after_the_2025_time_is_seen_by_a_and_i(chain, name):
    """(a) and (i) (the third review's scenario): a rule file whose name holds `__pycache__`, or begins with an exempt
    file's name, added after the 2025 time and deleted in the next commit (the tree the frozen one again): both commits
    touch a rule file, so (a) and (i) fire, naming both, and no ruling applies."""
    after = chain.v2_started + H
    added = rw.commit(chain.repo, {name: "MIN = 6\n"}, "a rule file named like an exempt one",
                      date=after.isoformat())
    (chain.repo / name).unlink()
    removed = rw.commit(chain.repo, {}, "its removal", date=(after + H).isoformat())
    assert run_v3._rule_commits_v3(chain.f0) == [{"commit": added, "paths": [name]},
                                                 {"commit": removed, "paths": [name]}]
    msg = refused(r"\(a\) commits since")
    assert "(i) commits committed after the 2025 time" in msg and added[:12] in msg and removed[:12] in msg


@pytest.mark.parametrize("cause", ["h", "i"])
def test_a_clean_ruling_on_h_or_i_is_refused_and_a_post_hoc_one_lifts_it(chain, cause):
    """An (h) or (i) entry saying clean is refused, naming the section; post hoc, with its evidence, it lifts its
    condition (and v2's form lifts (a) beside (i))."""
    entries = []
    if cause == "h":
        refetch(chain, chain.record_date - H)
    else:
        c1 = rule_change(chain, chain.retrieved + pd.Timedelta(days=1))
        entries = [moved(chain, c1), on(c1, "clean")]
    target = head(chain)
    section(chain, *entries, on_hi(cause, target, "clean"))
    refused(r"\(h\), \(i\) the v3 departures section: .* says clean", departure=cause == "i")
    section(chain, *entries, on_hi(cause, target))
    evidence(chain)
    rec = gate(departure=cause == "i")
    assert cause in rec["causes"] and rec["standing"] == "post_hoc"


@pytest.mark.parametrize("order", ["i_then_a", "a_then_i"])
def test_a_and_i_firing_give_a_post_hoc_standing_whatever_the_order(chain, order):
    c1 = rule_change(chain, chain.retrieved + pd.Timedelta(days=1))
    rulings = [on_hi("i", c1), on(c1, "clean")]
    section(chain, moved(chain, c1), *(rulings if order == "i_then_a" else rulings[::-1]))
    evidence(chain)
    rec = gate(departure=True)
    assert rec["causes"] == ["a", "i"] and rec["standing"] == "post_hoc" and rec["ruling"]["outcome"] == "clean"


@pytest.mark.parametrize("cause", ["h", "i"])
def test_a_start_after_a_stopped_one_with_a_clean_f_ruling_stays_post_hoc_when_h_or_i_fires(chain, cause):
    """A stopped 2025 start fires (f); v2's form saying clean, naming that start, lifts it (and (a)); (h) or (i)
    firing beside it, lifted by its own entry, leaves the standing post hoc."""
    entries = []
    if cause == "i":
        c1 = rule_change(chain, chain.retrieved + pd.Timedelta(days=1))
        entries.append(moved(chain, c1))
        section(chain, *entries)
    else:
        refetch(chain, chain.record_date - H)
    start = stopped_start(chain)
    target = head(chain)
    section(chain, *entries, on(target, "clean", start["started_utc"]), on_hi(cause, target))
    evidence(chain)
    rec = gate(departure=cause == "i")
    assert "f" in rec["causes"] and cause in rec["causes"]
    assert rec["ruling"]["outcome"] == "clean" and rec["ruling"]["start"] == start["started_utc"]
    assert rec["standing"] == "post_hoc"


def test_a_ruling_without_its_evidence_run_refuses(chain):
    """(a) fired and v2's form applies, but no evidence run exists for its commit: refused, naming evidence-v3; after
    `valiwatt evidence-v3`, the start passes with the ruling's standing."""
    c1 = rule_change(chain, chain.retrieved - pd.Timedelta(days=1))
    section(chain, moved(chain, c1), on(c1, "clean"))
    refused("run `valiwatt evidence-v3` first", departure=True)
    assert run_v3.run_evidence_v3(departure=True) == 0
    rec = gate(departure=True)
    assert rec["causes"] == ["a"] and rec["standing"] == "clean" and rec["rulings"][0]["evidence_run_id"]


@pytest.mark.parametrize("case", ["dirty", "other_dq", "before_ruling", "off_head", "control"])
def test_an_evidence_run_the_ruling_cannot_rest_on_leaves_the_start_refused(chain, case):
    """v2's section 11.3.5 on v3's kind: a ruling applies only with a finished evidence run made from a clean tree, on
    the gate's data-quality run, for the commit it names or a descendant up to the head. One made from a dirty tree, on
    an older data-quality run, on a commit before the ruling's, or on a descendant of it off the head's history, leaves
    the start refused; the control passes."""
    c1 = rule_change(chain, chain.retrieved - pd.Timedelta(days=1))
    section(chain, moved(chain, c1), on(c1, "clean"))
    if case == "dirty":
        evidence(chain, git_dirty="true")
    elif case == "other_dq":
        evidence(chain, dq_run_id=chain.dq25)  # the run before the rule change; the gate reads the latest
    elif case == "before_ruling":
        evidence(chain, rev=chain.f0)
    elif case == "off_head":
        branch = rw.git(chain.repo, "symbolic-ref", "--short", "HEAD")
        rw.git(chain.repo, "checkout", "-q", "-b", "side")
        side = rw.commit(chain.repo, {"notes.txt": "side\n"}, "side")
        rw.git(chain.repo, "checkout", "-q", branch)
        evidence(chain, git_commit=side)
    else:
        evidence(chain)
        assert gate(departure=True)["standing"] == "clean"
        return
    refused("run `valiwatt evidence-v3` first", departure=True)


def _changed_in_the_run(monkeypatch, nth: int) -> None:
    """freeze_record.frozen_chain_v3 hashes one item otherwise on its `nth` call inside the 2025 run (the calls before
    it opens, the gate's, are untouched): 1, the inputs read; 2, the never-acts check after the replay."""
    from valiwatt import freeze_record

    load = freeze_record.frozen_chain_v3
    calls = []

    def changed(*args, **kwargs):
        import mlflow

        out = load(*args, **kwargs)
        if mlflow.active_run() is not None:
            calls.append(1)
            if len(calls) == nth:
                out = {**out, "frozen_sha256": {**out["frozen_sha256"], "thresholds": "0" * 64}}
        return out

    monkeypatch.setattr(freeze_record, "frozen_chain_v3", changed)


@pytest.mark.parametrize("when", ["inputs", "after_replay"])
def test_the_artifacts_that_judge_2025_changed_inside_the_run_stop_it(chain, monkeypatch, when):
    """Section 11.3 (3): the artifacts read inside the 2025 run are those the gate checked (the time between the check
    and the use), and they are unchanged after the replay (the never-acts check): either change stops the run, which
    holds no result."""
    from valiwatt.monitor.replay import MonitorActedError

    _changed_in_the_run(monkeypatch, 1 if when == "inputs" else 2)
    error, match = {"inputs": (FreezeError, "differ from those the 2025 gate checked"),
                    "after_replay": (MonitorActedError, "the artifacts frozen for 2025 changed during the replay"),
                    }[when]
    with pytest.raises(error, match=match):
        run_v3.run_replay_v3(2025, n_boot=20)
    (run,) = vw.replay_runs_v3(2025)
    assert run.data.tags["error"] == error.__name__ and run.data.metrics == {}
    assert "results/replay.json" not in mrun._artifact_paths(tracking.client(), run.info.run_id)


@pytest.mark.parametrize("term", ["dq_retrieved", "earlier_start"])
def test_the_2025_time_never_moves_later_so_h_still_fires_after_a_refetch(chain, term):
    """Section 11.3 (4): the 2025 time is never later than an earlier reading of it, so an export re-fetched after v2's
    2025 start, its manifest time later, still fires (h) when 2025 was on the machine before the freeze record's
    commit; with an (h) entry for this code the start runs post hoc. Each earlier reading of the export's retrieval
    time alone holds the time back: a data-quality run that read the export before the commit; the gate record of a
    stopped start whose (h) fired, the data-quality run that read the early export gone from the store (a run's start
    time is no reading of it: the test after this one). In the last, the stopped start's (h) entry names a commit
    before the one that start ran, so it does not apply again: the next start is refused (h) until a new entry, then
    runs post hoc, and finishes so labelled."""
    before = chain.record_date - H
    start = None
    if term == "dq_retrieved":
        refetch(chain, before)
    else:
        early = refetch(chain, before)
        section(chain, on_hi("h", head(chain)))
        evidence(chain)
        start = stop_a_start()
        assert start["gate"]["causes"] == ["h"] and start["gate"]["standing"] == "post_hoc"
        forget(early)
    refetch(chain, chain.v2_started + H)
    refused(rf"\(h\) the 2025 time is {re.escape(before.isoformat())} \({term} of run [0-9a-f]{{32}}\)")
    now = run_v3.time_2025(vw.ledger_time(chain.v2_started + H), run_v3.v2_test_of_record())
    assert pd.Timestamp(now["utc"]) > chain.record_date  # the current reading alone would let the start through
    target = head(chain)
    section(chain, *([on(target, "clean", start["started_utc"])] if start else []), on_hi("h", target))
    evidence(chain)
    rec = gate()
    assert "h" in rec["causes"] and rec["standing"] == "post_hoc"
    if start:
        assert rec["causes"] == ["f", "h"]
        assert run_v3.run_replay_v3(2025, n_boot=20) == 0
        run = next(r for r in vw.replay_runs_v3(2025) if r.info.run_id != start["run_id"])
        assert (run.data.tags["status"], run.data.tags["evaluation"]) == ("complete", "post_hoc")


def test_a_data_quality_run_s_start_time_is_no_reading_of_the_2025_time(chain):
    """The 2025 time is the export's retrieval time or v2's first 2025 start, never a clock the plan does not define:
    a data-quality run through 2025 that read the export at its retrieval time, but whose start the machine's clock
    put before the freeze record's commit (a clock moved back), leaves (h) quiet."""
    early = vw.log_dq_2025(vw.ledger_time(chain.retrieved))
    tracking.client().set_terminated(early, "FAILED")
    vw.date_dq_run(early, at=(chain.record_date - H).isoformat())  # older than the gate's data-quality run
    rec = gate()
    assert rec["causes"] == [] and rec["t2025"]["source"] == "manifest" and rec["dq_run_id"] == chain.dq25


def test_the_2025_time_never_moves_later_so_i_still_fires_after_a_refetch(chain):
    """The (i) variant: a rule change committed after the export's retrieval and before v2's 2025 start fires (a) and
    (i); the start, ruled on both, stops. Every data-quality run that read that export gone from the store, and the
    export re-fetched after v2's start, the stopped start's gate record still holds the 2025 time, so (i) still fires,
    and new entries for this code leave the start post hoc."""
    c1 = rule_change(chain, chain.retrieved + pd.Timedelta(hours=12))
    section(chain, moved(chain, c1), on(c1, "clean"), on_hi("i", c1))
    evidence(chain)
    start = stop_a_start(departure=True)
    assert start["gate"]["causes"] == ["a", "i"]
    for run_id in dq_2025_runs():
        forget(run_id)
    refetch(chain, chain.v2_started + H)
    refused(r"\(i\) commits committed after the 2025 time", departure=True)
    target = head(chain)
    section(chain, moved(chain, c1), on(target, "clean", start["started_utc"]), on_hi("i", target))
    evidence(chain)
    rec = gate(departure=True)
    assert rec["causes"] == ["a", "f", "i"] and rec["standing"] == "post_hoc"


OFF_HEAD = (r"\(a\) the commit that adds the freeze record, {record}, is not HEAD \([0-9a-f]{{12}}\) or an ancestor "
            r"of it: the start does not run the code the record froze\. .*\(a\) is lifted only by")


def test_a_freeze_record_whose_commit_is_not_an_ancestor_of_the_start_refuses_a(chain):
    """(a), section 11.3 (3): the record R, the one commit of the repository's refs that adds it, kept by a branch; the
    start runs elsewhere, on a commit that does not descend from R (a branch forked at R's parent, which holds the
    first pin, every named run's commit and the rehearsal's ledger entry). The gate reads R through its anchor and
    refuses (a), no ruling applying. The control: a start on a descendant of R."""
    rw.commit(chain.repo, {"README.md": "later\n"}, "a descendant")
    assert gate()["causes"] == []
    rw.git(chain.repo, "branch", "with-record")
    rw.git(chain.repo, "checkout", "-q", "-b", "elsewhere", f"{chain.record}^")
    rw.commit(chain.repo, {"README.md": "elsewhere\n"}, "not a descendant")
    refused(OFF_HEAD.format(record=chain.record[:12]))


@pytest.mark.parametrize("outcome", ["clean", pytest.param("post hoc", id="post_hoc")])
def test_a_ruling_and_its_evidence_lift_a_freeze_record_off_the_start_s_history(chain, outcome):
    """(a), the record's ancestry, is ruled on as any (a) cause (section 11.3 (4)): the record R kept by a branch, the
    working branch reset to R's parent. Without a ruling the start is refused (a); `valiwatt evidence-v3` reads the
    record through its anchor as the gate does, and runs; with an entry in v2's form on the start's code and that
    evidence, the gate passes, (a) among its causes, the standing the entry's. With a clean entry the start then runs
    and finishes, held out."""
    rw.git(chain.repo, "branch", "keep", chain.record)
    rw.git(chain.repo, "reset", "--quiet", "--hard", f"{chain.record}^")
    refused(OFF_HEAD.format(record=chain.record[:12]))
    target = head(chain)
    section(chain, on(target, outcome))
    assert run_v3.run_evidence_v3() == 0
    (ev,) = [r for r in mrun._replay_runs(tracking.client(), kind=run_v3.EVIDENCE_KIND)]
    assert ev.data.tags["freeze_record_commit"] == chain.record
    rec = gate()
    assert (rec["causes"], rec["standing"]) == (["a"], outcome.replace(" ", "_"))
    assert rec["freeze_record"]["commit"] == chain.record and rec["ruling"]["commit"] == target
    assert rec["rulings"][0]["evidence_run_id"] == ev.info.run_id
    if outcome == "clean":
        assert run_v3.run_replay_v3(2025, n_boot=20) == 0
        (run,) = vw.replay_runs_v3(2025)
        assert (run.data.tags["status"], run.data.tags["evaluation"]) == ("complete", "held_out")


@pytest.mark.parametrize("case", ["sibling", "keep_ref", "stash_ref", "reflog", "off_head", "control"])
def test_a_freeze_record_committed_again_on_a_sibling_history_refuses_a(chain, case):
    """(a), the record's anchor (section 11.3 (3)): the record R committed and kept by a ref; the working branch reset
    to R's parent. The writer refuses to write a record there (R is the first one); a second record R' committed by
    hand, the same bytes, is refused (a), the gate reading two additions across the repository's refs and reflogs,
    whatever keeps R: a branch (sibling), a ref outside heads, remotes and tags (keep_ref, refs/keep/r), a stash-like
    ref (stash_ref, refs/stash of the scratch repository), or no ref at all, R left only in the reflogs the reset wrote
    (reflog, the third review's scenario). The head before R', whose history holds no record while a branch holds R,
    reads R and is refused (a) as a cause, no ruling applying (off_head). The control: R kept by no ref and the reflogs
    expired, so nothing the repository records keeps R; R' passes the gate."""
    from valiwatt.freeze_record import PATH, FreezeRecordError, write_freeze_record

    text = rw.git(chain.repo, "show", f"{chain.record}:{PATH}") + "\n"
    keep = {"sibling": ["branch", "keep"], "off_head": ["branch", "keep"], "keep_ref": ["update-ref", "refs/keep/r"],
            "stash_ref": ["update-ref", "refs/stash"], "reflog": None, "control": None}[case]
    if keep:
        rw.git(chain.repo, *keep, chain.record)
    rw.git(chain.repo, "reset", "--quiet", "--hard", f"{chain.record}^")
    if case == "reflog":
        assert rw.git(chain.repo, "for-each-ref", "--contains", chain.record) == ""  # no ref keeps R
    if case == "control":
        rw.forget_reflogs(chain.repo)
    if case == "off_head":
        refused(OFF_HEAD.format(record=chain.record[:12]))
        return
    if case != "control":
        with pytest.raises(FreezeRecordError, match=rf"commits \['{chain.record[:12]}'\] of the repository's refs and "
                                                    "reflogs add"):
            write_freeze_record(chain.rehearsal)
    again = rw.commit(chain.repo, {PATH: text}, "the freeze record, again", date=chain.record_date.isoformat())
    if case == "control":
        rec = gate()
        assert rec["freeze_record"]["commit"] == again and rec["causes"] == []
        return
    refused(rf"\(a\) the freeze record: 2 commits across the repository's refs and reflogs add {PATH} "
            rf"\(\['{chain.record[:12]}', '{again[:12]}'\]\)")


@pytest.mark.parametrize("case", ["replace", "replacements_off", "grafts"])
def test_a_replaced_or_grafted_history_refuses_the_2025_gate(chain, monkeypatch, case):
    """(a), the history as committed (the third review's scenario): after the freeze record's commit R and the 2025
    time, C changes a rule file and D reverts it exactly. `git replace --graft D R` (replace) makes git show D as R's
    child, with no rule change on it: C is gone from every walk, and the anchors still read R and the pin unchanged.
    The gate refuses outright, naming the replace ref. With that refusal taken away (replacements_off:
    records_v3.history_rewrites finds nothing), the gate still reads C, since its git reads, the walks and the
    ancestry checks included, run with replacement objects off: (a) and (i) fire, naming C and D, no ruling applying.
    A grafts file giving D the parent R (grafts), which git reads even with replacement objects off, is refused
    outright the same way. Without either, the same C and D refuse (a) and (i) as in the second case
    (test_a_commit_between_the_gate_and_the_run_refuses reads the same change and revert)."""
    r = head(chain)
    rule = (chain.repo / rw.RULE_FILE).read_text(encoding="utf-8")
    after = chain.v2_started + H
    c = rw.commit(chain.repo, {rw.RULE_FILE: "MIN = 5\n"}, "a rule change", date=after.isoformat())
    d = rw.commit(chain.repo, {rw.RULE_FILE: rule}, "its exact revert", date=(after + H).isoformat())
    if case == "grafts":
        grafts = chain.repo / ".git" / "info" / "grafts"
        grafts.parent.mkdir(exist_ok=True)
        grafts.write_text(f"{d} {r}\n", encoding="utf-8")
        rewrite = f"the grafts file {grafts}"
    else:
        rw.git(chain.repo, "replace", "--graft", d, r)
        rewrite = f"the replace ref refs/replace/{d}"
    shown = subprocess.run(["git", "log", "--format=%H", f"{r}..HEAD"], cwd=chain.repo, capture_output=True,
                           text=True, check=True).stdout.split()
    assert shown == [d]  # git, rewrites on, no longer shows C
    if case == "replacements_off":
        monkeypatch.setattr(records_v3, "history_rewrites", lambda: [])
        msg = refused(r"\(a\) commits since")
        assert "(i) commits committed after the 2025 time" in msg and c[:12] in msg and d[:12] in msg
        assert os.environ.get("GIT_NO_REPLACE_OBJECTS") is None  # the gate's environment restored after it
        return
    msg = refused(r"\(a\) \['the ")
    assert rewrite in msg and "another history than the one committed" in msg


@pytest.mark.parametrize("case", ["freeze_tag", "year", "git_commit", "freeze_commit", "control"])
def test_a_stopped_2025_start_retagged_is_never_left_out_of_the_starts(chain, case):
    """(c), (f): a real 2025 start A passed its gate, logged its gate record and stopped (the third review's
    scenario). Its tags changed afterwards with the MLflow client never take it out of the starts: its freeze tag set
    to `v3-pre-freeze` (freeze_tag, the reviewer's retag), its year to 2024, its git_commit or its freeze_commit to
    another commit: each disagrees with the gate record A logged at its opening, and the starts' reader refuses (c),
    naming A, before any run is left out. The control: A untouched, a stopped start, and the next start is refused
    (f), A named."""
    start = stop_a_start()
    rid = start["run_id"]
    other = "9" * 40
    if case == "control":
        msg = refused(r"\(f\) the 2025 test of record did not finish")
        assert rid in msg
        return
    tracking.client().set_tag(rid, case, {"freeze_tag": "v3-pre-freeze", "year": "2024", "git_commit": other,
                                          "freeze_commit": other}[case])
    reason = (r" of 2025 carries the freeze tag 'v3-pre-freeze'" if case == "freeze_tag" else
              rf"'s tags disagree with the gate record it logged at its opening: {case} ")
    with pytest.raises(records_v3.V3FreezeError, match=rf"\(c\) v3 replay run {rid}{reason}"):
        records_v3.replay_starts_v3(2025)
    if case == "freeze_commit":
        # A freeze commit other than the tag's is also a moved tag, which the gate's moved-tag check reads first.
        refused(rf"\(c\) v3 replay run {rid}{reason}|moving the tag is a departure")
        return
    refused(rf"\(c\) v3 replay run {rid}{reason}")


@pytest.mark.parametrize("ruled", ["unruled", "ruled"])
def test_named_runs_off_the_start_s_history_are_checked_against_the_record_s_commit(chain, ruled):
    """(a), never (b) (the third review's scenario): every named run was made on L (the chain's commit of v2's ledger,
    documentation only), and the record R committed after it on the working branch A. The start runs on B, forked
    before L at the pin commit: it descends from the first pin and holds the committed ledgers (v2's and ledger-v3,
    with the rehearsal's entry) on commits of its own, and lacks L and R. The gate derives the record again checking
    each named run's commit against R, the record's commit, never B's HEAD: the runs lie between the first pin and R,
    so only R off B's history fires, the (a) a ruling lifts. Unruled: refused (a) only, no (b). Ruled: an entry in v2's
    form on B and its evidence run (`valiwatt evidence-v3`) lift it; the gate passes, (a) its one cause, clean."""
    from valiwatt.freeze_record import ROLES, read_freeze_record

    data = read_freeze_record()["data"]
    (made_on,) = {data["runs"][role]["git_commit"] for role in ROLES}
    fork = rw.git(chain.repo, "rev-parse", f"{made_on}^")
    assert records_v3._is_ancestor(chain.f0, fork) and fork != chain.f0  # the pin commit
    assert records_v3._is_ancestor(made_on, chain.record)
    rw.git(chain.repo, "checkout", "-q", "-b", "b", fork)
    rw.commit(chain.repo, {"README.md": "B's own\n"}, "a documentation commit of B")
    rw.git(chain.repo, "checkout", chain.record, "--", "docs/replay-ledger.json", "docs/replay-ledger-v3.json")
    rw.commit(chain.repo, {}, "the committed ledgers, on B")
    assert not records_v3._is_ancestor(made_on, "HEAD") and not records_v3._is_ancestor(chain.record, "HEAD")
    if ruled == "unruled":
        msg = refused(OFF_HEAD.format(record=chain.record[:12]))
        assert "(b)" not in msg and "not between the first pin" not in msg
        return
    target = head(chain)
    section(chain, on(target, "clean"))
    assert run_v3.run_evidence_v3() == 0
    (ev,) = [r for r in mrun._replay_runs(tracking.client(), kind=run_v3.EVIDENCE_KIND)]
    rec = gate()
    assert (rec["causes"], rec["standing"]) == (["a"], "clean")
    assert rec["freeze_record"]["commit"] == chain.record and rec["ruling"]["commit"] == target
    assert rec["rulings"][0]["evidence_run_id"] == ev.info.run_id


@pytest.mark.parametrize("case", ["plain", "changed"])
def test_a_plain_merge_of_the_record_s_branch_is_no_second_addition(chain, case):
    """(a) and (c), the record written once (section 10.3): the working branch holds the record R; branch `main`,
    forked at R's parent and given a commit of its own, merges the working branch (v3 merged into a branch without the
    record). The plain merge adds nothing: back on the working branch, the gate reads R, the one adding commit across
    the repository's refs, and passes with no cause (control); on `main`, the record reads as R. A merge whose result
    changes the record's bytes touches it: on `main`, the gate refuses (c), naming the merge; the working branch, whose
    history does not hold that merge, still reads R as committed."""
    from valiwatt.freeze_record import PATH, adding_commits, read_freeze_record

    work = rw.git(chain.repo, "rev-parse", "--abbrev-ref", "HEAD")
    text = rw.git(chain.repo, "show", f"{chain.record}:{PATH}") + "\n"
    rw.git(chain.repo, "checkout", "-q", "-b", "main", f"{chain.record}^")
    rw.commit(chain.repo, {"README.md": "main's own\n"}, "a commit of the branch without the record")
    rw.git(chain.repo, "merge", "--no-ff", "--no-commit", work)
    if case == "changed":
        (chain.repo / PATH).write_text(text.replace("\n", "\n ", 1), encoding="utf-8")
    merge = rw.commit(chain.repo, message=f"{work} merged into main")
    assert adding_commits() == [chain.record]
    if case == "plain":
        assert read_freeze_record()["commit"] == chain.record
    else:
        refused(rf"\(c\) the freeze record: commits \['{merge[:12]}'\] touch {PATH} after {chain.record[:12]} added "
                "it")
    rw.git(chain.repo, "checkout", "-q", work)
    rec = gate()
    assert rec["freeze_record"]["commit"] == chain.record and rec["causes"] == []


def test_another_v3_2025_replay_running_refuses(chain):
    rw.fake_run({"kind": run_v3.KIND, "year": "2025", "freeze_tag": V3_FREEZE_TAG, "freeze_commit": chain.f0,
                 "status": "started", **rw.git_tags(chain.repo)}, status="RUNNING")
    refused(r"\(c\) another v3 2025 replay is RUNNING")


def test_a_bad_v3_diagnostic_run_refuses(chain):
    rw.fake_run({"kind": run_v3.DIAGNOSTIC_KIND, "year": "2025", "replay_run_id": "0" * 32,
                 **rw.git_tags(chain.repo)})
    refused(r"\(c\) 2025 diagnostic runs .* name no finished, complete 2025 replay")


@pytest.mark.parametrize("case", ["failed", "running", "deleted", "control"])
def test_a_later_data_quality_run_that_did_not_pass_refuses_d(chain, case):
    """(d) reads the latest data-quality run (v2's section 11.3.4 (d)), whatever its status: a run through 2025 started
    after the one that passed, on the same files and recording the same retrieval time, that failed before its gates
    passed, is still running, or failed and was deleted, refuses (d); the older passing run is never read in its
    place. The control: the later run finished and passed, and the gate reads it."""
    c = tracking.client()
    later = vw.log_dq_2025(vw.ledger_time(chain.retrieved))
    if case == "control":
        assert gate()["dq_run_id"] == later
        return
    if case == "running":
        c.update_run(later, "RUNNING")
    else:
        c.set_terminated(later, "FAILED")
    if case == "deleted":
        c.delete_run(later)
    refused(rf"\(d\) the latest v3 data-quality run {later} is not a finished, passing through-2025 run under the "
            rf"current rules: it is {'RUNNING' if case == 'running' else 'FAILED'}, not FINISHED")


def test_a_later_data_quality_run_through_2024_is_never_d_s_latest_run(chain):
    """(d) reads the latest data-quality run through 2025 (plan-v3's section 18 of 2026-10-26): a run through 2024,
    started after the through-2025 run that passed, on the same rules and files, is not that run, and the gate reads the
    run through 2025 and passes."""
    later = rw.log_dq_v3(chain.repo, 2024)
    vw.date_dq_run(later)  # started after every other
    rec = gate()
    assert (rec["dq_run_id"], rec["causes"]) == (chain.dq25, [])


def test_a_deleted_data_quality_run_that_finished_and_passed_passes_d(chain):
    """(d) reads the latest data-quality run through 2025 whatever its lifecycle (section 18 includes deleted runs): a
    run soft-deleted after it finished and passed, its parameters, tags and checked files unchanged, is read and
    passes; the start runs and finishes on it, held out. (Status, gates, rules and files refuse it as any other:
    test_a_later_data_quality_run_that_did_not_pass_refuses_d, test_the_gate_s_hard_refusals_name_their_condition.)"""
    c = tracking.client()
    c.delete_run(chain.dq25)
    assert c.get_run(chain.dq25).info.lifecycle_stage == "deleted"
    rec = gate()
    assert (rec["dq_run_id"], rec["causes"], rec["standing"]) == (chain.dq25, [], "clean")
    assert run_v3.run_replay_v3(2025, n_boot=20) == 0
    (run,) = vw.replay_runs_v3(2025)
    assert (run.data.tags["status"], run.data.tags["evaluation"], run.data.tags["dq_run_id"]) == (
        "complete", "held_out", chain.dq25)


@pytest.mark.parametrize("case", ["paged", "control"])
def test_an_early_retrieval_on_a_later_page_of_data_quality_runs_still_fires_h(chain, monkeypatch, case):
    """(h), the 2025 time never later than an earlier reading of it: data-quality run D0 read the export retrieved
    before the freeze record's commit; the export was then re-fetched after v2's 2025 start and read by newer runs
    through 2025. The search for those runs is read page by page, one run a page here (run_v3.PAGE), so D0, the oldest,
    falls on the last page: (h) still fires, naming D0 (paged). The control: no D0, the same pages, the start passes."""
    monkeypatch.setattr(run_v3, "PAGE", 1)
    before = chain.record_date - H
    d0 = None
    if case == "paged":
        d0 = refetch(chain, before)
        vw.date_dq_run(d0, at=(chain.record_date - 2 * H).isoformat())  # the oldest run: the last page
    for _ in range(3):
        refetch(chain, chain.v2_started + H)
    if case == "control":
        assert gate()["causes"] == []
        return
    assert len(dq_2025_runs()) == 5
    refused(rf"\(h\) the 2025 time is {re.escape(before.isoformat())} \(dq_retrieved of run {d0}\)")


@pytest.mark.parametrize("link", ["symbolic", "hard"])
def test_a_2025_file_that_is_a_link_refuses_e(chain, link):
    """(e): every raw file and manifest past 2024 the replay reads is a byte copy: a 2025 month of a station reached
    through a symbolic link, or with a second hard link, refuses."""
    f = next(p for p in run_v3.files_past_2024() if p.parent.parent == eccc.RAW_ECCC and p.name.startswith("2025"))
    other = chain.root / "elsewhere.csv"
    if link == "symbolic":
        f.rename(other)
        f.symlink_to(other)
    else:
        os.link(f, other)
    refused(r"\(e\) a raw file past 2024 is not a byte copy")


def test_a_manifest_time_other_than_the_data_quality_run_s_refuses_e(chain):
    """(e): the copied Hydro-Québec manifest through 2025 was retrieved at the time the data-quality run read, compared
    as a time: the same time written another way passes; another time refuses."""
    import json

    path = hydroquebec.manifest_path(2025)
    path.write_text(json.dumps({"retrieved_utc": chain.retrieved.tz_convert("America/Montreal").isoformat()}),
                    encoding="utf-8")
    assert gate()["causes"] == []
    path.write_text(json.dumps({"retrieved_utc": vw.ledger_time(chain.retrieved + H)}), encoding="utf-8")
    refused(r"\(e\) the copied Hydro-Québec manifest through 2025 was retrieved at")


@pytest.mark.parametrize("case", ["none", "other_kind", "finished", "other_commit", "control", "year", "status_tag",
                                  "freeze_commit", "diagnostic", "no_tag", "gate_head", "no_gate_record"])
def test_a_v3_replay_reads_2025_only_for_its_started_run(chain, case):
    """replay()'s gate for a v3 replay reading 2025 (gate_v3 -> check_2025_run_v3): only under `v3-rules-frozen`, and
    only for a started, running 2025 replay_v3 run of the head and the tag's freeze commit; a diagnostic_v3 run only
    when it names a finished, complete 2025 replay of its commits. Each fault alone: no run ID, another kind, finished,
    another commit, another year, a status tag other than started, another freeze commit, a diagnostic run naming no
    finished replay, no tag, a gate record naming another commit than the run's (the gate passed on another code), no
    gate record."""
    import json

    tags = {"kind": run_v3.KIND, "year": "2025", "status": "started", "freeze_commit": chain.f0,
            **rw.git_tags(chain.repo)}
    texts = {mrun.GATE_RECORD: json.dumps({"head_commit": tags["git_commit"]})}
    status = "RUNNING"
    match = "a v3 replay reads 2025 only for its started 2025 run"
    if case == "other_kind":
        tags["kind"] = "replay"
    elif case == "finished":
        status = "FINISHED"
    elif case == "other_commit":
        tags["git_commit"] = chain.f0
    elif case == "year":
        tags["year"] = "2024"
    elif case == "status_tag":
        tags["status"] = "complete"
    elif case == "freeze_commit":
        tags["freeze_commit"] = "e" * 40
    elif case == "diagnostic":
        tags |= {"kind": run_v3.DIAGNOSTIC_KIND, "replay_run_id": "0" * 32}
    elif case == "no_tag":
        rw.git(chain.repo, "tag", "-d", V3_FREEZE_TAG)
        match = "a v3 replay reads 2025 only under `v3-rules-frozen`, which does not exist"
    elif case == "gate_head":
        texts = {mrun.GATE_RECORD: json.dumps({"head_commit": chain.f0})}
        match = rf"its gate record names the commit {chain.f0[:12]}, not its own"
    elif case == "no_gate_record":
        texts = {}
        match = "it holds no gate record"
    rid = None if case == "none" else rw.fake_run(tags, status=status, texts=texts)
    if case == "control":
        run_v3.gate_v3("2025-01-19", data_last(2025), False, rid)
        return
    with pytest.raises(FreezeError, match=match):
        run_v3.gate_v3("2025-01-19", data_last(2025), False, rid)


def _change_and_revert(chain) -> str:
    """A rule change committed after the 2025 time and reverted: the tree is again the frozen one, on a new commit.
    Returns that commit."""
    rule = (chain.repo / rw.RULE_FILE).read_text(encoding="utf-8")
    after = chain.v2_started + H
    rw.commit(chain.repo, {rw.RULE_FILE: "MIN = 5\n"}, "a rule change", date=after.isoformat())
    return rw.commit(chain.repo, {rw.RULE_FILE: rule}, "its revert", date=(after + H).isoformat())


@pytest.mark.parametrize("when", ["before_open", "at_open"])
def test_a_commit_between_the_gate_and_the_run_refuses(chain, monkeypatch, when):
    """The run is bound to the code its gate passed on: the gate passes on H; then a rule change and its revert,
    committed after the 2025 time, give J, whose tree is the frozen one. Before the run opens (J made while the run's
    inputs are located), the start is refused and no run opens; at the opening (J made as the run is created, so the
    run records J), the run stops before reading any input, holding no result. Either way neither (a) nor (i) was read
    on J. The control is test_a_clean_2025_start_with_everything_in_order_passes."""
    from valiwatt import tracking as tr

    h = head(chain)
    made = []
    if when == "before_open":
        locate = run_v3._locate_v3

        def moved(*args, **kwargs):
            made.append(_change_and_revert(chain))
            return locate(*args, **kwargs)

        monkeypatch.setattr(run_v3, "_locate_v3", moved)
    else:
        start = tr.start_run

        def moved(*args, **kwargs):
            made.append(_change_and_revert(chain))
            return start(*args, **kwargs)

        monkeypatch.setattr(tr, "start_run", moved)
    with pytest.raises(FreezeError, match=r"\(c\) the 2025 gate passed on " + h[:12] + ", but HEAD is "):
        run_v3.run_replay_v3(2025, n_boot=20)
    (j,) = made
    assert head(chain) == j != h and rw.git(chain.repo, "status", "--porcelain") == ""
    runs = vw.replay_runs_v3(2025)
    if when == "before_open":
        assert runs == []
        return
    (run,) = runs
    t = run.data.tags
    assert (t["git_commit"], t["stage"], t["error"], run.data.metrics) == (j, "opened", "FreezeError", {})
    assert mrun._artifact_paths(tracking.client(), run.info.run_id) == [mrun.GATE_RECORD]


def test_a_second_2025_start_while_the_lock_is_held_opens_no_run(chain):
    with run_v3.start_lock_v3():
        with pytest.raises(FreezeError, match=r"\(c\) another v3 2025 start is being made"):
            run_v3.run_replay_v3(2025, n_boot=20)
    assert vw.replay_runs_v3(2025) == []


@pytest.mark.parametrize("case", ["no_tag", "record", "ledger_v3", "dq_window", "merge", "dirty", "moved_tag",
                                  "dq_none", "dq_gates", "dq_rules", "raw_hash", "manifest_time", "first_pin",
                                  "unknown_start", "unreadable_time"])
def test_the_gate_s_hard_refusals_name_their_condition(chain, case):
    """The refusals no ruling lifts, each naming its condition: (c) no `v3-rules-frozen` tag; (c) the freeze record
    touched by a second commit; (c) ledger-v3 without the record's rehearsal; (d) the latest data-quality run through
    2025 not reaching its end; (a) a merge commit after the first pin; (c) an uncommitted change; (c) the tag moved with no
    departure recorded; (d) the latest data-quality run failed, or its gates failed or its rules are other; (e) a
    raw file whose hash differs from the data-quality run's, or a copied manifest with no retrieval time; (b) a record
    naming another first pin; (c) a 2025 start in the committed ledger-v3 that the run store lacks; (h) a data-quality
    run through 2025 recording no retrieval time (an unreadable earlier reading of the 2025 time is not a later one)."""
    import json

    repo = chain.repo
    c = tracking.client()
    if case == "dirty":
        (repo / "README.md").write_text("dirty\n", encoding="utf-8")
        match = r"the tree holds 1 uncommitted changes"
    elif case == "moved_tag":
        rw.tag(repo, V3_FREEZE_TAG, rw.commit(repo, {rw.RULE_FILE: "MIN = 5\n"}, "a rule change"))
        vw.log_dq_2025(vw.ledger_time(chain.retrieved))
        match = r"moving the tag is a departure"
    elif case == "dq_none":
        from valiwatt.quality import run_v3 as dq_v3

        exp = c.get_experiment_by_name(dq_v3.EXPERIMENT)
        for r in c.search_runs([exp.experiment_id], "tags.kind = 'dq_v3'"):
            c.set_terminated(r.info.run_id, "FAILED")
        match = r"\(d\) the latest v3 data-quality run .* it is FAILED, not FINISHED"
    elif case == "dq_gates":
        c.set_tag(chain.dq25, "gates", "failed")
        match = r"\(d\) the latest v3 data-quality run .* its gates are 'failed', not passed"
    elif case == "dq_rules":
        c.set_tag(chain.dq25, "rules_sha256", "0" * 64)
        match = r"\(d\) the latest v3 data-quality run .* it ran under other rules"
    elif case == "raw_hash":
        vw.log_dq_2025(vw.ledger_time(chain.retrieved), hq_sha256="0" * 64)
        match = r"\(e\) \['the Hydro-Québec file'\] differ by hash"
    elif case == "manifest_time":
        hydroquebec.manifest_path(2025).write_text(json.dumps({"retrieved": "?"}), encoding="utf-8")
        match = r"\(e\) the copied Hydro-Québec manifest through 2025 gives no retrieval time"
    elif case == "first_pin":
        other = rw.git(repo, "rev-parse", "HEAD~1")
        amend_record(chain, lambda d: d.update(first_pin=other))
        match = rf"\(b\) the freeze record names the first pin {other[:12]}"
    elif case == "unknown_start":
        from valiwatt.report import ledger_v3

        (e,) = ledger_v3.read()
        extra = dict(e, run_id="8" * 32, year=2025, evaluation="held_out", standing="clean",
                     started_utc=vw.ledger_time(chain.retrieved + pd.Timedelta(days=2)))
        rw.commit(repo, {"docs/replay-ledger-v3.json": ledger_v3.dumps([e, extra])}, "ledger-v3 with a 2025 start")
        match = rf"\(c\) 2025 starts \['{'8' * 32}'\] are in the committed ledger-v3 but not in the run store"
    elif case == "unreadable_time":
        from valiwatt.quality import run_v3 as dq_v3

        rid = rw.fake_run({"kind": dq_v3.KIND}, experiment=dq_v3.EXPERIMENT, params={"through": 2025}, status="FAILED")
        vw.date_dq_run(rid, at=(chain.retrieved - H).isoformat())  # older than the gate's: (d) passes
        match = rf"\(h\) the 2025 time: run {rid}'s dq_retrieved time None cannot be read as a time"
    elif case == "no_tag":
        rw.git(repo, "tag", "-d", V3_FREEZE_TAG)
        match = r"\(c\) no `v3-rules-frozen` tag"
    elif case == "record":
        rw.git(repo, "rm", "-q", "docs/freeze-v3.json")
        removed = rw.commit(repo, {}, "the record removed")
        match = rf"\(c\) the freeze record: commits \['{removed[:12]}'\] touch docs/freeze-v3.json after"
    elif case == "ledger_v3":
        rw.commit(repo, {"docs/replay-ledger-v3.json": '{"replays": []}\n'}, "ledger-v3 emptied")
        match = r"\(c\) the committed ledger-v3 holds no entry of the freeze record's rehearsal"
    elif case == "dq_window":
        vw.log_dq_2025(vw.ledger_time(chain.retrieved), window_last_utc=data_last(2024).isoformat())  # the latest
        match = rf"\(d\) the latest v3 data-quality run .* its last hour is {re.escape(data_last(2024).isoformat())}"
    else:
        branch = rw.git(repo, "symbolic-ref", "--short", "HEAD")
        rw.git(repo, "checkout", "-q", "-b", "side")
        rw.commit(repo, {"notes.txt": "side\n"}, "side")
        rw.git(repo, "checkout", "-q", branch)
        rw.commit(repo, {"README.md": "main\n"}, "main")
        rw.git(repo, "merge", "-q", "--no-ff", "-m", "merge", "side")
        match = r"\(a\) merge commits"
    refused(match)
