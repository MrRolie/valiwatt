"""v3's replay ledger, `docs/replay-ledger-v3.json` (src/valiwatt/report/ledger_v3.py; docs/plan-v3.md, section
10.3): v2's nineteen fields with the verdicts terms and family; append-only; pre-freeze runs never entered; each
2025 label checked against the starts before it; scanned before it is written; never v2's file, and v2's ledger
refuses a v3 entry. Scratch repositories and run stores only (tests/records_v3_world.py)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from records_v3_world import PRE, fake_run, git_tags, log_replay_v3, replay_results, replay_v3_tags, scratch_store
from valiwatt.config import V3_FREEZE_TAG
from valiwatt.monitor.run import frozen_digest
from valiwatt.paths import REPO_ROOT
from valiwatt.report import ledger, ledger_v3
from valiwatt.report.ledger import LedgerError


@pytest.fixture
def world(tmp_path, monkeypatch):
    return scratch_store(tmp_path, monkeypatch)


def stored(run_id: str):
    from valiwatt import tracking

    c = tracking.client()
    run = c.get_run(run_id)
    return run, ledger.results_of(c, run)


def entry_of(run_id: str) -> dict:
    return ledger_v3.entry(*stored(run_id))


def test_a_v3_entry_has_v2_s_nineteen_fields_with_the_terms_and_family_verdicts(world):
    """Section 10.3: v2's nineteen fields in v2's order; the verdicts are the decision rule's terms and family; the
    frozen hash is the digest of the items the replay logged; 2024 is seen by the developers, with no standing."""
    rid = log_replay_v3(world.repo, 2024)
    run, results = stored(rid)
    e = ledger_v3.entry(run, results)
    assert tuple(e) == ledger.V2_FIELDS and len(e) == 19
    assert e["verdict"] == {"terms": "better", "family": "not_shown_better"}
    assert e["frozen_sha256"] == frozen_digest(results["frozen_sha256"])
    assert (e["freeze_tag"], e["freeze_commit"], e["evaluation"], e["standing"]) == (
        V3_FREEZE_TAG, world.f0, "seen_by_developers", "")
    assert ledger_v3.from_store() == [e]


def test_a_v3_entry_with_v2_s_verdict_keys_is_refused(world):
    """v3's verdicts are {"terms", "family"}: v2's keys (shipped, family), a terms verdict that is not one of v3's,
    or a family verdict that is not one of v2's family verdicts, is refused; the entry itself passes (control)."""
    e = entry_of(log_replay_v3(world.repo, 2024))
    ledger_v3.check_entry(e)
    for verdict in ({"shipped": "better", "family": "better"}, {"family": "better", "terms": "better"},
                    {"terms": "better_cold_untested", "family": "better"},
                    {"terms": "better", "family": "not_testable"}):
        with pytest.raises(LedgerError, match="verdict is"):
            ledger_v3.check_entry({**e, "verdict": verdict})


def test_v2_s_ledger_refuses_a_v3_entry(world):
    """D2: v2's shapes do not list v3's tag, so v2's ledger refuses a v3 entry (one never borrows the other's shape);
    and v3's refuses v2's."""
    e = entry_of(log_replay_v3(world.repo, 2024))
    with pytest.raises(LedgerError, match="freeze_tag must name a freeze tag the configuration lists"):
        ledger.check_entry(e)
    v2_entry = {**e, "freeze_tag": "rules-frozen-v2", "verdict": {"shipped": "better", "family": "better"}}
    ledger.check_entry(v2_entry)  # the same entry in v2's shape and tag (control)
    with pytest.raises(LedgerError, match="freeze_tag must be 'v3-rules-frozen'"):
        ledger_v3.check_entry(v2_entry)


FIELD_CHANGES = {"year": 2025, "freeze_tag": "rules-frozen-v2", "freeze_commit": "9" * 40, "rules_sha256": "9" * 64,
                 "code_sha256": "9" * 64, "started_utc": "2027-01-01T00:00:00.000Z",
                 "ended_utc": "2027-01-01T00:00:01.000Z", "status": "stopped", "mape": 0.5, "coverage": 0.5,
                 "recommendations": [{"week_start": "2024-01-08", "recommendation": "retrain"}], "departure": True,
                 "departure_from": ["9" * 32], "evaluation": "held_out", "model_sha256": "9" * 64,
                 "verdict": {"terms": "worse", "family": "worse"}, "frozen_sha256": "9" * 64, "standing": "clean"}


@pytest.mark.parametrize("field", list(FIELD_CHANGES))
def test_a_store_run_differing_from_its_v3_entry_in_one_field_is_refused(world, field):
    """Append-only: a stored run whose entry differs from the recorded one in any field of v3's shape is refused
    (run_id names the run, so a run of another ID is another entry)."""
    recorded = entry_of(log_replay_v3(world.repo, 2024))
    changed = {**recorded, field: FIELD_CHANGES[field]}
    assert ledger_v3.disagreement(changed, recorded) == [field]
    with pytest.raises(LedgerError, match=rf"disagrees with its v3 ledger entry on \['{field}'\]"):
        ledger_v3.merge([recorded], [changed])
    assert ledger_v3.merge([recorded], [recorded]) == [recorded]  # control


def test_pre_freeze_rehearsals_never_enter_the_v3_ledger(world, tmp_path):
    """D5: a finished pre-freeze rehearsal (step 2) is skipped; the step-5 rehearsal under the tag is entered; a v3
    replay run under no freeze tag is refused, never folded into v1's freeze."""
    path = tmp_path / "replay-ledger-v3.json"
    log_replay_v3(world.repo, 2024, PRE)
    assert ledger_v3.from_store() == [] and ledger_v3.sync(path, root=tmp_path) == [] and not path.exists()
    frozen = log_replay_v3(world.repo, 2024)
    assert [e["run_id"] for e in ledger_v3.sync(path, root=tmp_path)] == [frozen]
    t = replay_v3_tags(world.repo, 2024)
    del t["freeze_tag"]
    bare = fake_run(t, texts={"results/replay.json": json.dumps(replay_results({**t, "freeze_tag": ""}))})
    with pytest.raises(LedgerError, match=f"v3 replay {bare} carries the freeze tag ''"):
        ledger_v3.from_store()


def test_a_v3_2025_label_is_checked_against_the_starts_before_it(world):
    """v2's rule on v3's tag (report/ledger.py _check_v2): the 2025 test of record is held out when its standing is
    clean (control); after a finished 2025 start, a later one labelled held out is refused; and a 2025 entry starting
    before the first 2024 one is refused."""
    first = entry_of(log_replay_v3(world.repo, 2024))
    record = entry_of(log_replay_v3(world.repo, 2025, evaluation="held_out", standing="clean"))
    assert ledger_v3.check([first, record]) == [first, record]
    starts = ledger_v3.starts()
    assert [s["run_id"] for s in starts] == [record["run_id"]]
    assert ledger_v3.check([first, record], starts) == [first, record]
    rerun = entry_of(log_replay_v3(world.repo, 2025, evaluation="held_out", standing="clean"))
    with pytest.raises(LedgerError, match="give 'seen_by_developers'"):
        ledger_v3.check([first, record, rerun], ledger_v3.starts())
    with pytest.raises(LedgerError, match="starts before any v3-rules-frozen replay of 2024"):
        ledger_v3.check([{**record, "started_utc": "2000-01-01T00:00:00.000Z"}, first])


def test_the_v3_ledger_text_passes_the_public_safety_scan_or_is_not_written(world, tmp_path):
    path = tmp_path / "replay-ledger-v3.json"
    log_replay_v3(world.repo, 2024)
    (tmp_path / ".private-patterns").write_text("v3-rules-frozen\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="public-safety scan"):
        ledger_v3.sync(path, root=tmp_path)
    assert not path.exists()
    (tmp_path / ".private-patterns").write_text("", encoding="utf-8")
    entries = ledger_v3.sync(path, root=tmp_path)  # control: written, and only on change
    text = path.read_text(encoding="utf-8")
    assert text == ledger_v3.dumps(entries) and ledger_v3.read(path) == entries
    assert ledger_v3.sync(path, root=tmp_path) == entries and path.read_text(encoding="utf-8") == text
    assert ledger_v3.digest(path) != ledger_v3.ABSENT and ledger_v3.digest(tmp_path / "none.json") == ledger_v3.ABSENT


def test_every_committed_v3_ledger_entry_ran_on_the_rules_at_its_tag():
    """Checkable from a clone, without data or a run store: each committed v3 entry names the freeze commit of
    `v3-rules-frozen` (or, as a recorded departure, a commit of the tag's pins) and the rules hash at that commit.
    Vacuous until v3's first entry: before the tag, the ledger holds none."""
    from test_departure_record_v3 import pinned
    from test_tags import tag_commit
    from valiwatt.monitor.rules import rules_sha256

    entries = ledger_v3.read()
    if tag_commit(REPO_ROOT, V3_FREEZE_TAG) is None:
        assert entries == []
        return
    pins = set(pinned(REPO_ROOT, "HEAD"))
    for e in entries:
        assert e["freeze_commit"] in pins, e["run_id"]
        assert e["rules_sha256"] == rules_sha256(e["freeze_commit"]), e["run_id"]


def test_the_v3_ledger_never_reads_v2_s_ledger_file(world):
    """v3's ledger reads only its own file: no code of report/ledger_v3.py names v2's file or its path constant, and
    an unreadable v2 ledger beside it changes nothing."""
    tree = ast.parse(Path(ledger_v3.__file__).read_text(encoding="utf-8"))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef)) and n.body and isinstance(n.body[0], ast.Expr)}
    constants = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and id(n) not in docstrings]
    assert not [c for c in constants if "replay-ledger.json" in c]
    names = {n.attr if isinstance(n, ast.Attribute) else n.id for n in ast.walk(tree)
             if isinstance(n, (ast.Attribute, ast.Name))}
    assert "LEDGER" not in names and "read_ledger" not in names
    (world.repo / "docs").mkdir(exist_ok=True)
    (world.repo / "docs" / "replay-ledger.json").write_text("not json", encoding="utf-8")
    rid = log_replay_v3(world.repo, 2024)
    entries = ledger_v3.sync()
    assert [e["run_id"] for e in entries] == [rid] and ledger_v3.read() == entries
    assert ledger_v3.path() == world.repo / "docs" / "replay-ledger-v3.json"


def test_v3_entries_never_reach_v2_s_ledger_sync(world, tmp_path):
    """D3: v2's ledger reads `replay` runs only: a finished v3 replay is not among its entries."""
    log_replay_v3(world.repo, 2024)
    fake_run({"kind": "replay_v3", "year": "2025", "freeze_tag": V3_FREEZE_TAG, "status": "complete",
              **git_tags(world.repo)})
    assert ledger.from_store() == []
