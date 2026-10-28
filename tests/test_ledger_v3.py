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
    """v3's ledger reads only its own file: no code of report/ledger_v3.py, nor of its rules (ledger_rules_v3.py),
    names v2's file or its path constant, and an unreadable v2 ledger beside it changes nothing."""
    from valiwatt import ledger_rules_v3

    for module in (ledger_v3, ledger_rules_v3):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef)) and n.body and isinstance(n.body[0], ast.Expr)}
        constants = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                     and id(n) not in docstrings]
        assert not [c for c in constants if "replay-ledger.json" in c], module.__name__
        names = {n.attr if isinstance(n, ast.Attribute) else n.id for n in ast.walk(tree)
                 if isinstance(n, (ast.Attribute, ast.Name))}
        assert "LEDGER" not in names and "read_ledger" not in names, module.__name__
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


# ------------------------------------------------------------------- what the gate decides on is inside the freeze

# The modules v3's 2025 gate runs (monitor/run_v3.py `held_out_2025_v3`, its starts and records, its ledger rules).
GATE_MODULES = ("src/valiwatt/monitor/run_v3.py", "src/valiwatt/records_v3.py", "src/valiwatt/ledger_rules_v3.py")
# The files outside the rule-file set those modules may import: v2's ledger (its `_check_v2`, its patterns, its
# results reader), which the gate calls for (g) and for the ledger's checks, and its package's docstring. v2's code is
# closed at `v3-fork` (docs/plan-v3.md, section 10.1): each must stay byte-identical to its content there.
PINNED_AT_FORK = ("src/valiwatt/report/__init__.py", "src/valiwatt/report/ledger.py")


def _valiwatt_imports(path: Path) -> set[str]:
    """Each file of src/ a module imports from valiwatt, at module level or inside a function."""
    src = REPO_ROOT / "src"
    names = set()
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.split(".")[0] == "valiwatt":
            names |= {n.module, *(f"{n.module}.{a.name}" for a in n.names)}
        elif isinstance(n, ast.Import):
            names |= {a.name for a in n.names if a.name.split(".")[0] == "valiwatt"}
    out = set()
    for name in names:
        base = src.joinpath(*name.split("."))
        for f in (base.with_suffix(".py"), base / "__init__.py"):
            if f.is_file():
                out.add(f.relative_to(REPO_ROOT).as_posix())
    return out


def test_what_the_2025_gate_runs_is_a_rule_file_or_v2_s_code_as_at_the_fork():
    """Section 10.3, the freeze gate: rule files and dependencies equal to the tag's. Every module of valiwatt the 2025
    gate's modules import is a rule file (monitor/rules.py `rule_files`), so a change to what decides acceptance (the
    ledger's checks, its entries derived from the store, their disagreement) changes the rules hash; the ledger's
    writer (report/ledger_v3.py) renders and decides nothing, and is not among them; the only others are v2's ledger
    and its package, which `test_v2_s_ledger_code_the_gate_calls_is_byte_identical_to_v3_fork` holds as at the
    fork."""
    from valiwatt.monitor.rules import rule_files

    rules = set(rule_files())
    assert set(GATE_MODULES) <= rules and "src/valiwatt/report/ledger_v3.py" not in rules
    outside = {m: sorted(f for f in _valiwatt_imports(REPO_ROOT / m) if f not in rules and f not in PINNED_AT_FORK)
               for m in GATE_MODULES}
    assert outside == {m: [] for m in GATE_MODULES}


def test_v2_s_ledger_code_the_gate_calls_is_byte_identical_to_v3_fork():
    """v2's ledger code (report/ledger.py and its package), which the 2025 gate calls for (g), the ledger's checks
    and the reconciliation, lies outside the rule-file set; v2's code is closed at `v3-fork` (section 10.1), so it is
    held byte-identical to its content there: any change to it fails here."""
    from test_report_stamp import _at, fork_commit

    commit = fork_commit()
    changed = [p for p in PINNED_AT_FORK if (REPO_ROOT / p).read_bytes() != _at(commit, "show", f"{commit}:{p}")]
    assert changed == [], f"v2's code differs from {commit[:12]} (v3-fork): {changed}"
