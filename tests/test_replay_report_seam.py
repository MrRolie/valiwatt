"""Where the replay's records meet the report's reading of them, on a real run store with no stand-in on either side
(docs/plan-v2.md, sections 10.3, 10.5, 11.3.5 and 13). The synthetic chain of tests/replay_world.py runs
`valiwatt replay` itself; the ledger (report/ledger.py) and the report's lineage (report/lineage.py, report/validation.py)
then read its runs as `valiwatt ledger` and `valiwatt report` read them: the run tags each replay opens with, the 2025
gate's record, the starts, the evaluation label, the hash of what judged each replay, the diagnostics, and a
departure's record. The rest of each side is tested on its own; these tests hold the two to one another."""

import replay_world as rw
from test_freeze_gates import RULE_FILE, V2_TAG, evidence, git, stop_start, v2_departure
from valiwatt import tracking
from valiwatt.config import FREEZE_TAG
from valiwatt.monitor import run as mrun
from valiwatt.report import ledger, lineage
from valiwatt.report import validation as RV

# The fields of the owner's ruling the gate read, as section 9 of the validation report prints them.
RULING_FIELDS = {"commit", "outcome", "head", "start", "evidence_run_id"}


def test_the_ledger_and_the_report_read_the_2025_starts_the_replay_writes(tmp_path, monkeypatch):
    """A first 2025 start stops; under the owner's ruling naming it, the next start finishes; a rerun follows. The
    ledger written from the store accepts each entry's label against the starts the replay gate reads (one rule,
    evaluate.evaluation_label, on both sides), each entry holds the digest of the frozen_sha256 its run logged, equal to
    v2's first 2024 entry's, and the walk of the starts gives the ruled start as the test of record, the stopped one
    first with the ruling that let the next run. The report's lineage reads each record from its own run, its gate's
    record and standing, its through-2025 data-quality run and its diagnostics."""
    c = rw.chain_copy(tmp_path, monkeypatch)
    client = tracking.client()
    stop_start()  # the first 2025 start stops reading its inputs
    (stopped,) = mrun.replay_starts(2025)
    head = git(c.repo, "rev-parse", "HEAD").strip()
    evidence(c)
    rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", head, start=stopped["started_utc"]))}, "the ruling")
    assert mrun.run_replay(2025, n_boot=20) == 0  # the test of record, under the ruling
    assert mrun.run_replay(2025, n_boot=20) == 0  # a rerun
    _, record_run, rerun_run = rw.replay_runs(2025)

    existing = [c.entry]
    starts = ledger.starts(client, ledger={"replays": existing})
    assert [s["run_id"] for s in starts] == [stopped["run_id"], record_run.info.run_id, rerun_run.info.run_id]
    entries = ledger.merge(existing, ledger.from_store(client), starts)  # every label checked against the starts
    first, record, rerun = entries
    assert first == c.entry and (first["year"], record["year"], rerun["year"]) == (2024, 2025, 2025)
    assert (record["run_id"], rerun["run_id"]) == (record_run.info.run_id, rerun_run.info.run_id)
    assert (record["evaluation"], record["standing"]) == ("held_out", "clean")
    assert (rerun["evaluation"], rerun["standing"]) == ("seen_by_developers", "clean")
    for e in (record, rerun):
        tags = client.get_run(e["run_id"]).data.tags
        assert (e["evaluation"], e["standing"], e["model_sha256"]) == (
            tags["evaluation"], tags["standing"], tags["coefficient_sha256"])
        assert e["model_sha256"] == first["model_sha256"] and e["frozen_sha256"] == first["frozen_sha256"]
        logged = mrun._load_json_artifact(client, e["run_id"], "results/replay.json")["frozen_sha256"]
        gate = mrun._load_json_artifact(client, e["run_id"], mrun.GATE_RECORD)
        assert mrun.frozen_digest(logged) == mrun.frozen_digest(gate["frozen_sha256"]) == e["frozen_sha256"]

    walk = ledger.of_record(entries, ledger.starts(client, ledger={"replays": entries}))
    assert walk["record"]["run_id"] == record["run_id"] and walk["entry"] == record and walk["reruns"] == [rerun]
    assert [s["run_id"] for s in walk["stopped"]] == [s["run_id"] for s in walk["unfinished"]] == [stopped["run_id"]]
    passed = walk["stopped"][0]
    assert (passed["stage"], passed["error"], passed["holds_results"]) == ("opened", "RuntimeError", False)
    assert set(passed["ruling"]) == RULING_FIELDS and passed["ruling"]["start"] == stopped["started_utc"]

    record_2024 = lineage._record(client, first["run_id"], entries, 2024, FREEZE_TAG, "the 2024 replay of record")
    held = lineage._held(client, entries, ledger.starts(client, ledger={"replays": entries}), record_2024, FREEZE_TAG,
                         first["frozen_sha256"], [], {})
    assert held.run.info.run_id == record["run_id"] and [r.info.run_id for r in held.reruns] == [rerun["run_id"]]
    assert {k: dq.info.run_id for k, dq in held.dq.items()} == {record["run_id"]: c.dq25, rerun["run_id"]: c.dq25}
    view = RV.record_view(client, held.run, entries, 2025)
    assert view["gate"]["standing"] == view["standing"] == "clean" and view["gate"]["causes"] == ["f"]
    assert set(view["gate"]["ruling"]) == RULING_FIELDS and view["gate"]["ruling"]["start"] == stopped["started_utc"]
    for run_id in (first["run_id"], record["run_id"], rerun["run_id"]):
        diag = RV.diagnostics(client, client.get_run(run_id))
        assert diag is not None and client.get_run(diag["run_id"]).data.tags["replay_run_id"] == run_id


def test_the_report_reads_the_departure_the_replay_records(tmp_path, monkeypatch):
    """v2's first 2024 start stopped; a rule fix moved the tag, recorded as a departure; the departure replay of 2024
    finished. Its ledger entry is a departure from the stopped start, and the report's lineage reads the same record
    the replay gate read: the departure entry moved the tag to the replay's freeze commit, and the stopped start, its
    predecessor, is shown as unfinished under the earlier freeze commit that entry names."""
    c = rw.chain_copy(tmp_path, monkeypatch)
    monkeypatch.setattr(lineage, "REPO_ROOT", c.repo)
    client = tracking.client()
    client.set_tag(c.first_2024, "status", "started")  # v2's first 2024 start, as one that stopped
    client.set_terminated(c.first_2024, "FAILED")
    rw.write_ledger(c.repo, [])
    fix = rw.commit(c.repo, {RULE_FILE: "MIN = 5\n"}, "the fix")
    git(c.repo, "tag", "-f", "-a", "-m", "moved", V2_TAG, fix)
    rw.commit(c.repo, {"AGENTS.md": rw.agents(v2_departure(c.f0, fix))}, "record the departure")
    w = rw.chain_world()
    dq24 = rw.log_dq(2024)
    val = rw.log_validation(w, dq24)
    rw.log_frozen_models(w, val, dq24)
    assert mrun.run_replay(2024, n_boot=20, departure=True) == 0
    (departed,) = [r for r in rw.replay_runs(2024) if r.info.run_id != c.first_2024]

    entries = ledger.merge([], ledger.from_store(client))
    assert [e["run_id"] for e in entries] == [departed.info.run_id]
    (e,) = entries
    assert (e["departure"], e["departure_from"], e["freeze_commit"]) == (True, [c.first_2024], fix)
    section = mrun.recorded_departures(tag=FREEZE_TAG)
    (dep,) = lineage.check_recorded(departed, section, FREEZE_TAG)
    assert lineage.moved_to(dep) == fix and mrun._moved_to(section, fix)
    (pred,) = lineage._predecessors(client, e["departure_from"], entries, FREEZE_TAG, False)
    assert isinstance(pred, lineage.Unfinished) and pred.run_id == c.first_2024
    assert lineage._freeze_commit_of(pred) == c.f0
    lineage.check_named([pred], [dep], f"replay {departed.info.run_id}")
    shown = RV.departure(client, pred)
    assert (shown["unfinished"], shown["freeze_commit"], shown["year"]) == (True, c.f0, "2024")
