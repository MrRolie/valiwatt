"""The freeze and lineage gates refuse what they exist to refuse: a missing or moved tag, a changed or added rule
file or dependency, a frozen model from another validation run, year or rule set, and a model file that no longer
matches itself. The replay itself stops at each gate it calls, before the step the gate protects.

v2 (docs/plan-v2.md, sections 10 and 11.3.4): every check is scoped to its freeze, not to a year; the 2025 gate holds
every check made before a 2025 start, reads no demand record, and has two outcomes, clean or refused, the owner's
ruling entry the only way past a refusal. Its tests run on the synthetic chain of tests/replay_world.py: a scratch
repository tagged rules-frozen-v2 and a run store holding v2's first 2024 replay and its committed ledger entry."""

import fcntl
import json
import multiprocessing
import os
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pandas as pd
import pytest

import replay_world as rw
from replay_world import FIRST, LAST, frozen_and_reference, world
from valiwatt import tracking
from valiwatt.config import FREEZES, data_last
from valiwatt.forecast import freeze as fz
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import build
from valiwatt.forecast.guards import LookaheadError
from valiwatt.forecast.validate import LineageError
from valiwatt.monitor import rules
from valiwatt.monitor import run as mrun
from valiwatt.quality.demand import build_demand

V1_TAG, V2_TAG = "rules-frozen", "rules-frozen-v2"
V1_HEADING = FREEZES[V1_TAG]["heading"]
RULE_FILE = "src/valiwatt/monitor/rules.py"


def git(repo, *args):
    return subprocess.run(["git", *rw.SCRATCH_ID, *args], cwd=repo, capture_output=True, text=True,
                          check=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "src/valiwatt/monitor").mkdir(parents=True)
    (tmp_path / "src/valiwatt/report").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "src/valiwatt/monitor/rules.py").write_text("MIN = 4\n")
    (tmp_path / "src/valiwatt/report/render.py").write_text("# renders\n")
    (tmp_path / "config/stations.toml").write_text("x = 1\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", "-A")
    git(tmp_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "rules")
    monkeypatch.setattr(rules, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mrun, "REPO_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def chain(tmp_path, monkeypatch):
    return rw.chain_copy(tmp_path, monkeypatch)


def no_earlier(tag=V2_TAG, client=None):
    return {}


def test_no_tag_is_refused(repo):
    with pytest.raises(mrun.FreezeError):
        mrun.check_freeze()


def test_tagged_rules_pass_and_a_change_to_a_rendering_file_is_allowed(repo):
    git(repo, "tag", rules.FREEZE_TAG)
    assert mrun.check_freeze()["freeze_commit"] == git(repo, "rev-parse", "HEAD").strip()
    (repo / "src/valiwatt/report/render.py").write_text("# renders differently\n")
    mrun.check_freeze()


def test_an_edited_rule_file_is_refused_and_named(repo):
    git(repo, "tag", rules.FREEZE_TAG)
    (repo / "src/valiwatt/monitor/rules.py").write_text("MIN = 3\n")
    with pytest.raises(mrun.FreezeError, match="monitor/rules.py"):
        mrun.check_freeze()


def test_a_rule_file_added_after_the_tag_is_refused(repo):
    git(repo, "tag", rules.FREEZE_TAG)
    (repo / "src/valiwatt/monitor/extra.py").write_text("THRESHOLD = 0\n")
    with pytest.raises(mrun.FreezeError, match="extra.py"):
        mrun.check_freeze()


NO_LEDGER = {"replays": []}


def test_a_moved_tag_is_refused_unless_recorded_as_a_departure():
    """v1's record: its departures section names the new freeze commit."""
    freeze = {"freeze_tag": V1_TAG, "freeze_commit": "b" * 40}
    assert mrun.check_tag_not_moved(freeze, {"b" * 40: ["r1"]}, departure=False, ledger=NO_LEDGER) == []
    with pytest.raises(mrun.FreezeError):
        mrun.check_tag_not_moved(freeze, {"a" * 40: ["r0"]}, departure=False, ledger=NO_LEDGER)
    with pytest.raises(mrun.FreezeError):  # a bare flag is not a record
        mrun.check_tag_not_moved(freeze, {"a" * 40: ["r0"]}, departure=True, recorded="None yet.", ledger=NO_LEDGER)
    other = f"# x\n\n{V1_HEADING}\n\n- 2026-10-17, commit {'c' * 12}: reason.\n\n## Other\n"
    with pytest.raises(mrun.FreezeError):  # a departure recorded for another commit is not a record of this one
        mrun.check_tag_not_moved(freeze, {"a" * 40: ["r0"]}, departure=True,
                                 recorded=mrun.recorded_departures(other, V1_TAG), ledger=NO_LEDGER)
    agents = f"# x\n\n{V1_HEADING}\n\n- 2026-10-17, commit {'b' * 12}: reason.\n\n## Other\n"
    assert mrun.check_tag_not_moved(freeze, {"a" * 40: ["r0"]}, departure=True,
                                    recorded=mrun.recorded_departures(agents, V1_TAG), ledger=NO_LEDGER) == ["r0"]


def ledger_of(*entries, tag=V2_TAG):
    return {"replays": [{"run_id": r, "year": y, "freeze_tag": tag, "freeze_commit": c,
                         "status": "FINISHED"} for r, y, c in entries]}


def v2_departure(*commits):
    return rw.departure_entry(*commits, head="2026-10-17, a fix to the gate: a departure")


def test_a_ledger_naming_an_earlier_freeze_commit_is_a_moved_tag_even_with_an_empty_store(repo, monkeypatch):
    """A fresh clone has no MLflow store, but the committed ledger lists the replays that ran: one under another
    freeze commit means the tag moved. The replay runs only with --departure and the move recorded."""
    git(repo, "tag", rules.FREEZE_TAG)
    freeze = mrun.check_freeze()
    head = freeze["freeze_commit"]
    agents = f"# x\n\n{mrun.DEPARTURES_HEADING}\n\n{v2_departure(head)}\n## Other\n"
    earlier = ledger_of(("r0", 2024, "a" * 40))
    with pytest.raises(mrun.FreezeError, match="moving the tag is a departure"):
        mrun.check_tag_not_moved(freeze, {}, departure=False, ledger=earlier)
    with pytest.raises(mrun.FreezeError, match="does not name commit"):  # the flag alone is not a record
        mrun.check_tag_not_moved(freeze, {}, departure=True, recorded="None yet.", ledger=earlier)
    assert mrun.check_tag_not_moved(freeze, {}, departure=True, recorded=mrun.recorded_departures(agents),
                                    ledger=earlier) == ["r0"]
    # The controls: the ledger's replays under the tagged commit, or of a year the freeze does not replay, are no
    # departure.
    assert mrun.check_tag_not_moved(freeze, {}, departure=False, ledger=ledger_of(("r1", 2024, head))) == []
    assert mrun.check_tag_not_moved(freeze, {}, departure=False, ledger=ledger_of(("r2", 2023, "a" * 40))) == []

    # The same through the gate itself, reading the committed file, with an empty store.
    monkeypatch.setattr(mrun, "earlier_freeze_commits", no_earlier)
    assert mrun.held_out_gate(2024) == (freeze, [], {})  # no ledger file: no entries
    (repo / "docs").mkdir()
    (repo / mrun.LEDGER).write_text(json.dumps(earlier))
    with pytest.raises(mrun.FreezeError, match="moving the tag is a departure"):
        mrun.held_out_gate(2024)
    (repo / "AGENTS.md").write_text(agents)
    assert mrun.held_out_gate(2024, departure=True) == (freeze, ["r0"], {})


@pytest.mark.parametrize("text", ["{not json", json.dumps({"replays": {}}),
                                  json.dumps({"replays": [{"run_id": "r0", "year": 2024}]})],
                         ids=["not_json", "no_list", "no_freeze_commit"])
def test_a_ledger_that_cannot_be_read_is_refused(repo, text):
    (repo / "docs").mkdir()
    (repo / mrun.LEDGER).write_text(text)
    with pytest.raises(mrun.FreezeError, match="replay-ledger"):
        mrun.ledger_freeze_commits()


def store(tmp_path, monkeypatch):
    import mlflow  # noqa: F401 - the store's client

    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    return tracking.client(), tracking.experiment_id(mrun.EXPERIMENT)


def test_earlier_replays_are_found_even_when_deleted(tmp_path, monkeypatch):
    import mlflow
    from mlflow.entities import ViewType

    c, exp = store(tmp_path, monkeypatch)
    with mlflow.start_run(experiment_id=exp) as r:
        mlflow.set_tags({"kind": "replay", "year": "2024", "freeze_commit": "a" * 40})  # v1's: no freeze_tag
    c.delete_run(r.info.run_id)
    assert not c.search_runs([exp], "tags.kind = 'replay'")  # hidden from the default view
    assert mrun.earlier_freeze_commits(V1_TAG, c) == {"a" * 40: [r.info.run_id]}
    assert c.search_runs([exp], "tags.kind = 'replay'", run_view_type=ViewType.ALL)
    with pytest.raises(mrun.FreezeError):
        mrun.check_tag_not_moved({"freeze_tag": V1_TAG, "freeze_commit": "b" * 40},
                                 mrun.earlier_freeze_commits(V1_TAG, c), departure=False, ledger=NO_LEDGER)


def test_raw_files_other_than_the_checked_ones_are_refused(monkeypatch):
    from valiwatt.quality import run as qrun

    stations = [{"climate_id": "1", "months": {"2024-01": {"sha256": "m" * 64}}}]
    monkeypatch.setattr(qrun, "_verify_raw", lambda: ({"sha256": "h" * 64}, stations))
    good = SimpleNamespace(info=SimpleNamespace(run_id="dq"), data=SimpleNamespace(
        tags={}, params={"hq_sha256": "h" * 64, "eccc_manifests_sha256": qrun.eccc_manifests_sha256(stations)}))
    monkeypatch.setattr(mrun, "stations", lambda: [])
    with pytest.raises(StopIteration):  # the lineage passes; loading then needs a primary station
        mrun.load_raw(good)
    for bad in ({"hq_sha256": "x" * 64}, {"eccc_manifests_sha256": "x" * 64}):
        dq = SimpleNamespace(info=good.info, data=SimpleNamespace(tags={}, params=good.data.params | bad))
        with pytest.raises(LineageError):
            mrun.load_raw(dq)


def test_a_registered_model_that_changed_during_the_replay_fails_it():
    mrun.check_unchanged("a", "a", "a")
    for shas in (("b", "a", "a"), ("a", "b", "a"), ("a", "a", "b")):
        with pytest.raises(mrun.MonitorActedError):
            mrun.check_unchanged(*shas)


def fake_run(run_id, **tags):
    return SimpleNamespace(info=SimpleNamespace(run_id=run_id), data=SimpleNamespace(tags=tags, params={}))


def test_a_frozen_model_from_another_validation_year_or_rule_set_is_refused():
    val = fake_run("val")
    good = dict(validation_run_id="val", rules_sha256="r", replay_year="2024")
    mrun.check_lineage(2024, val, fake_run("f", **good), {"rules_sha256": "r"})
    for bad in ({"validation_run_id": "other"}, {"rules_sha256": "x"}, {"replay_year": "2023"}):
        with pytest.raises(LineageError):
            mrun.check_lineage(2024, val, fake_run("f", **(good | bad)), {"rules_sha256": "r"})


def test_validation_from_another_data_quality_run_or_rule_set_is_refused(monkeypatch):
    from valiwatt.forecast import validate as vd

    with pytest.raises(LineageError):  # a data-quality run made under other rules
        vd.load_validation(fake_run("dq", rules_sha256="old"))
    dq = fake_run("dq", rules_sha256=rules.rules_sha256())
    monkeypatch.setattr(tracking, "latest_run", lambda *a, **k: fake_run("v", dq_run_id="other",
                                                                         rules_sha256=rules.rules_sha256()))
    with pytest.raises(LineageError):
        vd.load_validation(dq)
    monkeypatch.setattr(tracking, "latest_run", lambda *a, **k: fake_run("v", dq_run_id="dq", rules_sha256="old"))
    with pytest.raises(LineageError):
        vd.load_validation(dq)


def test_a_frozen_model_file_that_no_longer_matches_its_hash_is_refused():
    frozen, _ = frozen_and_reference()
    text = frozen.to_json().replace(repr(float(frozen.beta[0])), repr(float(frozen.beta[0]) + 1.0), 1)
    with pytest.raises(fz.FrozenModelError):
        fz.FrozenModel.from_json(text)
    assert fz.FrozenModel.from_json(frozen.to_json()).sha256() == frozen.sha256()


def log_world_frozen(tmp_path, monkeypatch):
    """The synthetic world's frozen model, logged and registered in a throwaway MLflow store."""
    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    frozen, _ = frozen_and_reference()
    raw, obs = world()
    f = build(build_demand(raw, FIRST, LAST).frame, obs.set_index("ts_utc")["temp_c"])
    example = f[fz.INPUT_COLUMNS].dropna().head(24)
    run, version = fz.log_frozen(frozen, pd.DataFrame({"feature": ["x"], "frozen": [0.0]}), example, {})
    return frozen, run, version


def test_the_registered_model_is_refused_when_its_run_records_another_hash(tmp_path, monkeypatch):
    frozen, run, _ = log_world_frozen(tmp_path, monkeypatch)
    loaded, _ = fz.load_frozen(frozen.replay_year)
    assert loaded.sha256() == frozen.sha256()
    tracking.client().set_tag(run.info.run_id, "coefficient_sha256", "0" * 64)
    with pytest.raises(fz.FrozenModelError):
        fz.load_frozen(frozen.replay_year)


def test_an_alias_pointing_at_another_years_model_is_refused(tmp_path, monkeypatch):
    frozen, _, version = log_world_frozen(tmp_path, monkeypatch)
    other = frozen.replay_year - 1
    tracking.client().set_registered_model_alias(fz.MODEL_NAME, f"replay-{other}", version)
    with pytest.raises(fz.FrozenModelError, match=f"frozen for {frozen.replay_year}"):
        fz.load_frozen(other)


class Reached(Exception):
    """A step of the replay past the gate under test: reaching it means the gate let the replay through."""


def reached(step):
    def fake(*args, **kwargs):
        raise Reached(step)
    return fake


@pytest.fixture
def replay_inputs(repo, tmp_path, monkeypatch):
    """run_replay's inputs, faked to pass every gate; each test spoils one. Nothing reads data: the run opens in a
    throwaway store, and the replay stops at its summary, the first step after the never-acts check."""
    from valiwatt.quality import run as qrun

    oos = pd.DataFrame({"day": [pd.Timestamp("2023-06-01")], "pred_gam_forecast": [1.0]})
    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    monkeypatch.setattr(mrun, "earlier_freeze_commits", no_earlier)
    monkeypatch.setattr(mrun, "_locate", lambda year: {"dq_run_id": "dq", "validation_run_id": "val",
                                                       "frozen_run_id": "f", "coefficient_sha256": "a"})
    monkeypatch.setattr(mrun, "load_inputs", lambda: (fake_run("dq"), None, None))
    monkeypatch.setattr(mrun, "load_validation", lambda dq: (fake_run("val"), oos))
    monkeypatch.setattr(mrun, "validation_results", lambda val: {"thresholds": {"2024": {}}})
    monkeypatch.setattr(mrun, "load_raw", lambda dq: (None, None))
    monkeypatch.setattr(mrun, "load_comparators", lambda run: {})
    monkeypatch.setattr(mrun, "forecast_drift_reference", lambda val, year: oos)
    monkeypatch.setattr(qrun, "load_runs", lambda dq, years=None: None)
    monkeypatch.setattr(mrun, "replay", lambda *a, **k: SimpleNamespace(model_sha256="a", clock_end=None))
    monkeypatch.setattr(mrun, "summarize", reached("summarize"))

    def frozen(sha="a", **tags):
        run = fake_run("f", **({"validation_run_id": "val", "rules_sha256": rules.rules_sha256(),
                                "replay_year": "2024", "coefficient_sha256": "a"} | tags))
        return SimpleNamespace(sha256=lambda: sha), run

    def serve(*models):  # load_frozen returns these in turn: before the replay, then after it
        queue = list(models)
        monkeypatch.setattr(mrun, "load_frozen", lambda year: queue.pop(0))

    serve(frozen(), frozen())
    return frozen, serve


def test_the_replay_without_a_freeze_tag_stops_before_reading_any_input(replay_inputs, monkeypatch):
    monkeypatch.setattr(mrun, "load_inputs", reached("load_inputs"))
    with pytest.raises(mrun.FreezeError, match=f"no `{rules.FREEZE_TAG}` tag"):
        mrun.run_replay(2024)


def test_the_replay_under_a_moved_tag_stops_before_reading_any_input(repo, replay_inputs, monkeypatch):
    git(repo, "tag", rules.FREEZE_TAG)
    monkeypatch.setattr(mrun, "load_inputs", reached("load_inputs"))
    monkeypatch.setattr(mrun, "earlier_freeze_commits", lambda tag=V2_TAG, client=None: {"c" * 40: ["r0"]})
    with pytest.raises(mrun.FreezeError, match="moving the tag is a departure"):
        mrun.run_replay(2024)
    (repo / "AGENTS.md").write_text(f"{mrun.DEPARTURES_HEADING}\n\nNone yet.\n")
    with pytest.raises(mrun.FreezeError, match="does not name commit"):  # the flag alone is not a record
        mrun.run_replay(2024, departure=True)


def test_the_replay_refuses_a_frozen_model_from_another_validation_run(repo, replay_inputs, monkeypatch):
    git(repo, "tag", rules.FREEZE_TAG)
    frozen, serve = replay_inputs
    monkeypatch.setattr(mrun, "load_raw", reached("load_raw"))
    serve(frozen(validation_run_id="other"))
    with pytest.raises(LineageError, match="another validation run"):
        mrun.run_replay(2024)


def test_the_replay_fails_when_the_registered_model_changes_during_it(repo, replay_inputs):
    git(repo, "tag", rules.FREEZE_TAG)
    frozen, serve = replay_inputs
    with pytest.raises(Reached, match="summarize"):  # the control: an unchanged model reaches the summary
        mrun.run_replay(2024)
    serve(frozen(), frozen(sha="b"))
    with pytest.raises(mrun.MonitorActedError):
        mrun.run_replay(2024)


def test_the_frozen_model_refuses_training_rows_inside_its_embargo(monkeypatch):
    raw, obs = world()
    f = build(build_demand(raw, FIRST, LAST).frame, obs.set_index("ts_utc")["temp_c"])
    oos = wf.run(f, wf.folds("2021-01", "2021-12"), scenarios=("persistence",))
    real = wf.split
    monkeypatch.setattr(wf, "split", lambda f_, fold: (f_[f_["day"] < fold.test_first_day], real(f_, fold)[1]))
    with pytest.raises(LookaheadError):
        fz.fit_frozen(f, oos, 2022)


def test_the_2024_replay_measures_false_alarms_and_blind_spots_too(chain, monkeypatch):
    """The sensitivity measures run on 2024 as on 2023, so the report can set the two years side by side, in a run of
    their own once the replay has finished; their replays carry the departure flag into the gate inside replay()."""
    tracking.client().set_tag(chain.dq25, "gates", "failed")  # the 2024 chain reads the through-2024 run again
    seen = {}

    def measure(*args, **kwargs):
        (replay_run,) = [r for r in rw.replay_runs(2024) if r.info.run_id != chain.first_2024]
        (diag,) = [r for r in rw.replay_runs(2024, kind="diagnostic")
                   if r.data.tags["replay_run_id"] == replay_run.info.run_id]
        seen.update(kwargs, replay=(replay_run.info.status, replay_run.data.tags["status"]),
                    diagnostic=(diag.info.status, diag.data.tags["status"], diag.info.run_id))
        raise Reached("sensitivity")

    monkeypatch.setattr(mrun.sensitivity, "measure", measure)
    assert mrun.run_replay(2024, n_boot=20) == 1  # the measure failed: the command exits non-zero
    assert seen["departure"] is False
    assert seen["replay"] == ("FINISHED", "complete") and seen["diagnostic"][:2] == ("RUNNING", "started")
    assert seen["run_id"] == seen["diagnostic"][2] and seen["runs"] is not None
    assert seen["forecast_reference"] is not None


def direct_replay(first, last, data_end, **th):
    """replay() called directly, as any caller other than `valiwatt replay` would, on the synthetic world."""
    from replay_world import thresholds as world_thresholds
    from valiwatt.monitor import replay as rp

    raw, obs = world()
    frozen, ref = frozen_and_reference()
    return rp.replay(raw, obs, frozen, world_thresholds() | th, ref, first, last, pd.Timestamp(data_end))


def test_replay_called_directly_on_the_held_out_year_passes_the_freeze_gate(repo, monkeypatch):
    """No entry point skips the gate: replay() itself refuses the held-out year without the tag, under a rule file
    that differs from it, or under a moved tag; a replay inside the design years does not ask for it."""
    monkeypatch.setattr(mrun, "earlier_freeze_commits", no_earlier)
    for first, last, end in (("2024-01-01", "2024-01-07", "2025-01-01T05:00Z"),
                             ("2023-12-25", "2024-01-07", "2024-01-08T05:00Z"),  # a window that crosses into 2024
                             ("2023-12-25", "2023-12-31", "2025-01-01T05:00Z"),  # records of 2024 in view
                             ("2024-01-01", "2024-01-07", "2024-01-01T05:00Z")):  # days of 2024, no record of 2024
        with pytest.raises(mrun.FreezeError, match=f"no `{rules.FREEZE_TAG}` tag"):
            direct_replay(first, last, end)
    git(repo, "tag", rules.FREEZE_TAG)
    (repo / "src/valiwatt/monitor/rules.py").write_text("MIN = 3\n")
    with pytest.raises(mrun.FreezeError, match="monitor/rules.py"):
        direct_replay("2024-01-01", "2024-01-07", "2025-01-01T05:00Z")
    git(repo, "checkout", "--", "src/valiwatt/monitor/rules.py")
    monkeypatch.setattr(mrun, "earlier_freeze_commits", lambda tag=V2_TAG, client=None: {"c" * 40: ["r0"]})
    with pytest.raises(mrun.FreezeError, match="moving the tag is a departure"):
        direct_replay("2024-01-01", "2024-01-07", "2025-01-01T05:00Z")
    # The control: with the gate passed, the replay reaches its next check (thresholds that include the replayed year).
    monkeypatch.setattr(mrun, "earlier_freeze_commits", no_earlier)
    with pytest.raises(LookaheadError, match="cannot judge a replay starting 2024-01-01"):
        direct_replay("2024-01-01", "2024-01-07", "2025-01-01T05:00Z", last_year=2024)
    # A replay inside the design years never asks for the gate: without the tag, the world's own week replays.
    git(repo, "tag", "-d", rules.FREEZE_TAG)
    assert len(direct_replay("2022-01-03", "2022-01-09", LAST).days) == 7


# ------------------------------------------------------------------------------------- v2: the freeze, per tag

def test_a_dependency_changed_after_the_v2_tag_is_refused_and_named(repo):
    """The v2 gate compares pyproject.toml and uv.lock with the tag's, byte for byte (plan section 10.2): what v1 kept
    by hand is a gate."""
    (repo / "pyproject.toml").write_text("[project]\nname = 'scratch'\n")
    (repo / "uv.lock").write_text("version = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "dependencies")
    git(repo, "tag", rules.FREEZE_TAG)
    mrun.check_freeze()  # the control
    for path, text in (("uv.lock", "version = 2\n"), ("pyproject.toml", "[project]\nname = 'other'\n")):
        (repo / path).write_text(text)
        with pytest.raises(mrun.FreezeError, match=f"dependencies differ.*{path}"):
            mrun.check_freeze()
        git(repo, "checkout", "--", path)
        mrun.check_freeze()
    (repo / "uv.lock").unlink()
    with pytest.raises(mrun.FreezeError, match="uv.lock"):
        mrun.check_freeze()


def test_v1_replays_never_count_as_a_moved_v2_tag(tmp_path, monkeypatch):
    """v1's 2024 replays, in the store (no freeze_tag run tag) and in the ledger (freeze tag rules-frozen), ran under
    v1's freeze commit: they are no earlier replay of v2's freeze (control); under v1's own tag they would be."""
    c, exp = store(tmp_path, monkeypatch)
    run = c.create_run(exp, tags={"kind": "replay", "year": "2024", "freeze_commit": "a" * 40, "status": "complete"})
    c.set_terminated(run.info.run_id, "FINISHED")
    ledger = ledger_of(("e" * 32, 2024, "a" * 40), tag=V1_TAG)
    v2 = {"freeze_tag": V2_TAG, "freeze_commit": "b" * 40}
    assert mrun.earlier_freeze_commits(V2_TAG, c) == {}
    assert mrun.ledger_freeze_commits(ledger, V2_TAG) == {}
    assert mrun.check_tag_not_moved(v2, mrun.earlier_freeze_commits(V2_TAG, c), departure=False, ledger=ledger) == []
    with pytest.raises(mrun.FreezeError, match="moving the tag"):  # the same records, read as v1's own
        mrun.check_tag_not_moved({"freeze_tag": V1_TAG, "freeze_commit": "b" * 40},
                                 mrun.earlier_freeze_commits(V1_TAG, c), departure=False, ledger=ledger)


def test_a_v2_replay_under_a_moved_v2_tag_is_refused_unless_recorded(repo):
    """A moved v2 tag runs only with --departure and a departure entry of v2's section naming the new freeze commit
    as the commit the tag moved to: not a ruling entry naming it, not a tag moved back to an earlier freeze commit, not
    a tag moved onto a change commit the entry names (verify-r6, handoff 1)."""
    f0 = git(repo, "rev-parse", "HEAD").strip()
    (repo / RULE_FILE).write_text("MIN = 3\n")
    git(repo, "commit", "-qam", "a change")
    c1 = git(repo, "rev-parse", "HEAD").strip()
    (repo / RULE_FILE).write_text("MIN = 2\n")
    git(repo, "commit", "-qam", "the fix")
    f1 = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "tag", rules.FREEZE_TAG)
    freeze = mrun.check_freeze()
    assert freeze["freeze_commit"] == f1
    earlier = {f0: ["r0"]}

    def check(section, commit=f1, before=earlier):
        return mrun.check_tag_not_moved({"freeze_tag": V2_TAG, "freeze_commit": commit}, before, departure=True,
                                        recorded=section, ledger=NO_LEDGER)

    with pytest.raises(mrun.FreezeError, match="moving the tag is a departure"):
        mrun.check_tag_not_moved(freeze, earlier, departure=False, ledger=NO_LEDGER)
    with pytest.raises(mrun.FreezeError, match="does not name commit"):
        check("\n\nNone yet.\n")
    with pytest.raises(mrun.FreezeError, match="does not name commit"):  # a ruling entry records no move
        check(rw.ruling("clean", f1))
    with pytest.raises(mrun.FreezeError, match="does not name commit"):  # named, but not as a departure's head
        check(f"- **2026-11-02, a fix to the gate.** Commits `{f0[:12]}`, `{f1[:12]}`.\n")
    assert check(v2_departure(f0, c1, f1)) == ["r0"]  # the recorded move (control)
    with pytest.raises(mrun.FreezeError, match="does not name commit"):  # the tag moved back to F0
        check(v2_departure(f0, c1, f1), commit=f0, before={f1: ["r1"]})
    with pytest.raises(mrun.FreezeError, match="does not name commit"):  # the tag moved onto a named change commit
        check(v2_departure(f0, c1, f1), commit=c1)


def test_a_2025_replay_run_on_its_own_sees_a_tag_moved_since_v2_s_2024_replay(chain, tmp_path, monkeypatch):
    """v2's 2024 replay ran under F0, in the store and in the ledger; the tag then moved to F1. The 2025 gate, scoped
    by freeze and not by year, sees the move although no 2025 replay ran before: refused unless the departure is given
    and recorded, from the store alone (the ledger entry removed) and from the ledger alone (an empty store); once
    given and recorded, the gate goes on to its walk, which names the change (cause a)."""
    f1 = rw.commit(chain.repo, {RULE_FILE: "MIN = 5\n"}, "the fix")
    git(chain.repo, "tag", "-f", "-a", "-m", "moved", V2_TAG, f1)
    with pytest.raises(mrun.FreezeError, match="moving the tag is a departure"):
        mrun.held_out_gate(2025)
    with pytest.raises(mrun.FreezeError, match="does not name commit"):
        mrun.held_out_gate(2025, departure=True)
    rw.commit(chain.repo, {"AGENTS.md": rw.agents(v2_departure(chain.f0, f1))}, "record the departure")
    rw.log_dq(2025, hq_revised_records=1)  # data quality checked again under the fixed rules
    with pytest.raises(mrun.FreezeError, match=r"\(a\).*" + f1[:12]):
        mrun.held_out_gate(2025, departure=True)
    entry = chain.entry
    rw.write_ledger(chain.repo, [])  # the store alone
    with pytest.raises(mrun.FreezeError, match="moving the tag is a departure"):
        mrun.held_out_gate(2025)
    rw.write_ledger(chain.repo, [entry])  # the ledger alone: a fresh clone's empty store
    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "empty")
    with pytest.raises(mrun.FreezeError, match="moving the tag is a departure"):
        mrun.held_out_gate(2025)


def test_an_unfinished_start_counts_for_the_moved_tag_check(tmp_path, monkeypatch):
    """A start that failed, its status tag still started, ran under its freeze commit as much as a finished one."""
    c, exp = store(tmp_path, monkeypatch)
    run = c.create_run(exp, tags={"kind": "replay", "year": "2025", "freeze_tag": V2_TAG, "freeze_commit": "a" * 40,
                                  "status": "started", "stage": "opened"})
    c.set_terminated(run.info.run_id, "FAILED")
    assert mrun.earlier_freeze_commits(V2_TAG, c) == {"a" * 40: [run.info.run_id]}
    with pytest.raises(mrun.FreezeError, match="moving the tag is a departure"):
        mrun.check_tag_not_moved({"freeze_tag": V2_TAG, "freeze_commit": "b" * 40},
                                 mrun.earlier_freeze_commits(V2_TAG, c), departure=False, ledger=NO_LEDGER)


def test_a_stored_replay_without_a_freeze_tag_counts_as_v1_s(tmp_path, monkeypatch):
    c, exp = store(tmp_path, monkeypatch)
    run = c.create_run(exp, tags={"kind": "replay", "year": "2024", "freeze_commit": "a" * 40, "status": "complete"})
    c.set_terminated(run.info.run_id, "FINISHED")
    assert mrun.earlier_freeze_commits(V1_TAG, c) == {"a" * 40: [run.info.run_id]}
    assert mrun.earlier_freeze_commits(V2_TAG, c) == {}
    assert [s["run_id"] for s in mrun.replay_starts(2024, V1_TAG, c, ledger=NO_LEDGER)] == [run.info.run_id]
    assert mrun.replay_starts(2024, V2_TAG, c, ledger=NO_LEDGER) == []


def test_every_freeze_tag_in_the_repository_is_listed_in_freezes(repo):
    """A tag named like a freeze that config.FREEZES does not list is refused (plan section 10.3)."""
    git(repo, "tag", rules.FREEZE_TAG)
    mrun.check_freeze()
    git(repo, "tag", "rules-frozen-v3")
    with pytest.raises(mrun.FreezeError, match="rules-frozen-v3"):
        mrun.check_freeze()
    git(repo, "tag", "-d", "rules-frozen-v3")
    mrun.check_freeze()
    with pytest.raises(mrun.FreezeError, match="not listed"):
        mrun.check_freeze("rules-frozen-v3")


def test_recorded_departures_reads_the_section_of_its_tag():
    text = (f"# x\n\n{V1_HEADING}\n\nv1's record, `affe089f6ed0`.\n\n{FREEZES[V2_TAG]['heading']}\n\nNone yet.\n\n"
            "## After\n\nother\n")
    v1, v2 = mrun.recorded_departures(text, V1_TAG), mrun.recorded_departures(text, V2_TAG)
    assert "affe089f6ed0" in v1 and "None yet." not in v1
    assert "None yet." in v2 and "affe089f6ed0" not in v2 and "other" not in v2
    assert mrun.recorded_departures(text) == v2  # the default is v2's freeze
    assert mrun.recorded_departures(text.replace(FREEZES[V2_TAG]["heading"], "## Something else"), V2_TAG) == ""
    with pytest.raises(mrun.FreezeError):
        mrun.recorded_departures(text, "rules-frozen-v3")


# --------------------------------------------------------------------------------------------- v2: the 2025 gate

def test_a_2025_replay_without_a_v2_2024_ledger_entry_is_refused(chain):
    rw.write_ledger(chain.repo, [])
    with pytest.raises(mrun.FreezeError, match="holds no 2024 entry"):
        mrun.held_out_gate(2025)
    with pytest.raises(mrun.FreezeError, match="holds no 2024 entry"):
        mrun.run_replay(2025)
    assert rw.replay_runs(2025) == []


def test_a_clean_2025_start_with_nothing_changed_passes(chain):
    """The control: every condition met, the gate's record holds every run ID, the frozen artifacts' hashes and the
    standing clean, and the 2025 vintage revising a 2024 record starts clean too, its revised record listed by the
    data-quality run and the frozen artifacts unchanged. The gate opens no run."""
    freeze, departure_from, held = mrun.held_out_gate(2025)
    head = git(chain.repo, "rev-parse", "HEAD").strip()
    assert departure_from == [] and freeze["freeze_commit"] == chain.f0
    assert held["standing"] == "clean" and held["causes"] == [] and held["ruling"] is None
    assert held["rule_commits"] == [] and held["head_commit"] == head and held["freeze_commit"] == chain.f0
    assert (held["first_2024_run_id"], held["frozen_run_id"], held["validation_run_id"], held["dq_run_id"]) == (
        chain.first_2024, chain.frozen, chain.validation, chain.dq25)
    logged = mrun._load_json_artifact(tracking.client(), chain.first_2024, "results/replay.json")["frozen_sha256"]
    assert held["frozen_sha256"] == logged and mrun.frozen_digest(logged) == chain.entry["frozen_sha256"]
    assert tracking.client().get_run(chain.dq25).data.params["hq_revised_records"] == "1"
    assert rw.replay_runs(2025) == []


def edit_json(run_id, path, change):
    f = rw.artifact_file(run_id, path)
    data = json.loads(f.read_text(encoding="utf-8"))
    change(data)
    f.write_text(json.dumps(data), encoding="utf-8")


def edit_comparator(chain):
    """A store edit: the frozen run's comparator `forecast` changed, and the hash its run recorded with it."""
    import dataclasses

    w = rw.chain_world()
    changed = dataclasses.replace(w.comparators["forecast"], q=w.comparators["forecast"].q * 1.01)
    rw.artifact_file(chain.frozen, "comparators/forecast.json").write_text(changed.to_json(), encoding="utf-8")
    tracking.client().set_tag(chain.frozen, "comparator_sha256.forecast", changed.sha256())
    return changed


def test_a_2025_start_whose_frozen_artifacts_differ_is_refused(tmp_path, monkeypatch):
    """Each artifact that judges 2025, against v2's first 2024 ledger entry: the production model's hash against its
    model_sha256; a comparison model, the thresholds, cold_c and the reference against its one hash of them, each named
    against the hashes the run logged; and a store edit changing a comparison model and the hashes its run logged
    together, refused against the committed entry."""
    def fresh(name):
        return rw.chain_copy(tmp_path / name, monkeypatch)

    c = fresh("production")
    rw.write_ledger(c.repo, [c.entry | {"model_sha256": "0" * 64}])
    with pytest.raises(mrun.FreezeError, match="production model"):
        mrun.held_out_gate(2025)

    c = fresh("comparator")
    edit_comparator(c)
    with pytest.raises(mrun.FreezeError, match=r"\['comparators.forecast'\] differ"):
        mrun.held_out_gate(2025)

    c = fresh("thresholds")
    edit_json(c.validation, "results/validation.json",
              lambda d: d["thresholds"]["2024"]["performance"]["winter"].update(mape_high=0.5))
    with pytest.raises(mrun.FreezeError, match=r"\['thresholds'\] differ"):
        mrun.held_out_gate(2025)

    c = fresh("cold")
    edit_json(c.validation, "results/validation.json", lambda d: d["config"].update(cold_c=d["config"]["cold_c"] - 1))
    with pytest.raises(mrun.FreezeError, match=r"\['cold_c'\] differ"):
        mrun.held_out_gate(2025)

    c = fresh("reference")
    f = rw.artifact_file(c.validation, "frames/oos.parquet")
    oos = pd.read_parquet(f)
    oos.iloc[0, oos.columns.get_loc("pred_gam_forecast")] += 1.0
    oos.to_parquet(f)
    with pytest.raises(mrun.FreezeError, match=r"\['reference'\] differ"):
        mrun.held_out_gate(2025)

    c = fresh("both")
    changed = edit_comparator(c)
    edit_json(c.first_2024, "results/replay.json",
              lambda d: d["frozen_sha256"]["comparators"].update(forecast=changed.sha256()))
    with pytest.raises(mrun.FreezeError, match="no item differs .* refused against the committed entry"):
        mrun.held_out_gate(2025)
    assert rw.replay_runs(2025) == []


def test_a_2025_diagnostic_run_without_its_finished_replay_refuses_the_gate(tmp_path, monkeypatch):
    """Every 2025 diagnostic run names a finished, complete 2025 replay of its own commits (plan section 11.3.5): one
    naming a FAILED replay, one naming a replay of other commits, one naming none, one naming none and deleted from the
    store (deleted runs are read too), and one naming a finished diagnostic run (itself naming a finished replay)
    instead of a replay each refuse, named; one naming a finished, complete replay of its commits passes (control)."""
    def diagnostic(replay_run_id, c):
        head = git(c.repo, "rev-parse", "HEAD").strip()
        tags = {"kind": "diagnostic", "year": "2025", "git_commit": head, "freeze_commit": c.f0, "status": "complete"}
        return rw.fake_run(tags | ({"replay_run_id": replay_run_id} if replay_run_id else {}))

    def finished_replay(c, git_commit):
        return rw.fake_run({"kind": "replay", "year": "2025", "freeze_tag": V2_TAG, "freeze_commit": c.f0,
                            "git_commit": git_commit, "status": "complete"})

    for name in ("failed", "other_commits", "none", "deleted", "names_a_diagnostic"):
        c = rw.chain_copy(tmp_path / name, monkeypatch)
        head = git(c.repo, "rev-parse", "HEAD").strip()
        target = None
        if name == "failed":
            target = rw.fake_run({"kind": "replay", "year": "2025", "freeze_tag": V2_TAG, "freeze_commit": c.f0,
                                  "git_commit": head, "status": "started"}, status="FAILED")
        elif name == "other_commits":
            target = finished_replay(c, "0" * 40)
        elif name == "names_a_diagnostic":
            target = diagnostic(finished_replay(c, head), c)  # a diagnostic run that passes on its own
        diag = diagnostic(target, c)
        if name == "deleted":
            tracking.client().delete_run(diag)
        with pytest.raises(mrun.FreezeError, match=f"diagnostic runs .*{diag}") as e:
            mrun.held_out_gate(2025)
        if name == "names_a_diagnostic":  # the diagnostic run it names is not itself refused
            assert f"{target} (replay_run_id" not in str(e.value)

    c = rw.chain_copy(tmp_path / "control", monkeypatch)
    assert mrun.run_replay(2025, n_boot=20) == 0
    (replay_run,) = rw.replay_runs(2025)
    (diag,) = rw.replay_runs(2025, kind="diagnostic")
    assert diag.data.tags["replay_run_id"] == replay_run.info.run_id and diag.data.tags["status"] == "complete"
    assert mrun.held_out_gate(2025)[2]["standing"] == "clean"


def test_a_2025_start_without_its_runs_in_the_store_is_refused(tmp_path, monkeypatch):
    """Three cases: v2's first 2024 replay run absent from the store, as in a fresh clone; its frozen run absent; a
    2025 ledger entry whose run the store lacks (its code commit is unknown)."""
    c = rw.chain_copy(tmp_path / "clone", monkeypatch)
    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "empty")
    with pytest.raises(mrun.FreezeError, match="first 2024 replay run .* is not in the run store"):
        mrun.held_out_gate(2025)

    c = rw.chain_copy(tmp_path / "frozen", monkeypatch)
    tracking.client().set_tag(c.first_2024, "frozen_run_id", "f" * 32)
    with pytest.raises(mrun.FreezeError, match="frozen run of v2's first 2024 replay .* is not in the run store"):
        mrun.held_out_gate(2025)

    c = rw.chain_copy(tmp_path / "entry", monkeypatch)
    later = c.entry | {"run_id": "c" * 32, "year": 2025, "started_utc": "2027-01-01T00:00:00.000Z"}
    rw.write_ledger(c.repo, [c.entry, later])
    with pytest.raises(mrun.FreezeError, match="not in the run store: the code they ran on is unknown"):
        mrun.held_out_gate(2025)


def test_a_2025_gate_refuses_a_data_quality_run_that_is_not_a_passing_through_2025_run(chain):
    """The latest FINISHED data-quality run, whatever its gates: one through 2024; one failing its gates while an
    older one passed; one made under other rules."""
    rw.log_dq(2024)
    with pytest.raises(mrun.FreezeError, match="window ends with 2024"):
        mrun.held_out_gate(2025)
    rw.log_dq(2025, gates="failed", hq_revised_records=1)
    with pytest.raises(mrun.FreezeError, match="gates are 'failed'"):
        mrun.held_out_gate(2025)
    rw.log_dq(2025, rules="0" * 64, hq_revised_records=1)
    with pytest.raises(mrun.FreezeError, match="other rules"):
        mrun.held_out_gate(2025)
    rw.log_dq(2025, hq_revised_records=1)
    assert mrun.held_out_gate(2025)[2]["standing"] == "clean"  # the control


def test_a_2025_gate_refuses_a_raw_file_the_data_quality_run_did_not_check(chain, monkeypatch):
    """The raw files agree with the data-quality run by hash only: a file whose bytes changed, or whose manifest's
    hash is not the run's (the Hydro-Québec file, the ECCC files, the NBM files, one case each), refuses, and a start
    so refused opens no run. The control: with every raw-record reader patched to raise and pandas' parquet
    reader patched to raise on every read but a projection onto day and the production forecast, the clean gate still
    reaches its outcome, its one frame read being the forecast-drift reference's."""
    from valiwatt.quality import run as qrun
    from valiwatt.sources import eccc, hydroquebec
    from valiwatt.sources.http import FetchRefused

    def changed_bytes(through=2024):
        raise FetchRefused("Hydro-Québec CSV through 2025 no longer matches its manifest; re-fetch")

    monkeypatch.setattr(qrun, "_verify_raw", changed_bytes)
    with pytest.raises(mrun.FreezeError, match="no longer matches its manifest"):
        mrun.held_out_gate(2025)
    monkeypatch.setattr(qrun, "_verify_raw", lambda through=2024: ({"sha256": "c" * 64}, rw.eccc_manifests(through)))
    with pytest.raises(mrun.FreezeError, match="Hydro-Québec file"):
        mrun.held_out_gate(2025)
    # The Hydro-Québec file the run checked, but ECCC files of another vintage: refused before any run opens (load_raw
    # would refuse them only inside an open start, which would then stand as a stopped test of record).
    monkeypatch.setattr(qrun, "_verify_raw",
                        lambda through=2024: ({"sha256": rw.HQ_SHA[2025]}, rw.eccc_manifests(2024)))
    with pytest.raises(mrun.FreezeError, match="the ECCC files"):
        mrun.run_replay(2025, n_boot=20)
    assert rw.replay_runs(2025) == []
    monkeypatch.setattr(qrun, "_verify_raw", rw.chain_verify_raw)
    monkeypatch.setattr(qrun, "_verify_nbm", lambda through=2024: rw.nbm_manifest(2024))
    with pytest.raises(mrun.FreezeError, match="NBM files"):
        mrun.held_out_gate(2025)
    monkeypatch.setattr(qrun, "_verify_nbm", rw.chain_verify_nbm)

    for module, name in ((hydroquebec, "load_raw"), (hydroquebec, "load_window"), (eccc, "load_station"),
                         (mrun, "load_raw"), (mrun, "load_inputs")):
        monkeypatch.setattr(module, name, Reached.raiser(f"{module.__name__}.{name}"))
    real, reads = pd.read_parquet, []

    def projection_only(path, *args, **kwargs):
        if kwargs.get("columns") != ["day", "pred_gam_forecast"] or args:
            raise Reached(f"a parquet read other than the reference's projection: {kwargs.get('columns')}")
        reads.append(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", projection_only)
    assert mrun.held_out_gate(2025)[2]["standing"] == "clean"
    assert len(reads) == 1


def _raiser(cls, what):
    def fake(*args, **kwargs):
        raise cls(what)
    return fake


Reached.raiser = classmethod(_raiser)


def test_a_2025_start_while_another_is_running_is_refused(chain):
    """One start at a time: a 2025 replay RUNNING in the store (another start, or one a killed process left) refuses
    the next; the refusal says how to end a dead one."""
    rw.fake_run({"kind": "replay", "year": "2025", "freeze_tag": V2_TAG, "freeze_commit": chain.f0,
                 "status": "started", "stage": "inputs_read"}, status="RUNNING")
    with pytest.raises(mrun.FreezeError, match="RUNNING.*set_terminated"):
        mrun.held_out_gate(2025)
    with pytest.raises(mrun.FreezeError, match="RUNNING"):
        mrun.run_replay(2025)
    assert len(rw.replay_runs(2025)) == 1


def lock_is_held(root) -> bool:
    fd = os.open(root / "mlruns" / mrun.LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def test_two_simultaneous_2025_starts_open_one_run(chain, monkeypatch):
    """Two forkserver processes call run_replay(2025) on the same store, released together by a barrier, the gate
    slowed so that both would pass it before either opens: exactly one run opens, the other caller is refused naming
    the lock and opens none (verify-r6, handoff 3: each child installs the chain and the slow gate itself). And the
    lock is held from the gate to the opening of the run and the logging of the gate's record, then released."""
    ctx = multiprocessing.get_context("forkserver")
    barrier, results = ctx.Barrier(2), ctx.Queue()
    procs = [ctx.Process(target=rw.simultaneous_start, args=(str(chain.root), barrier, results, 3.0))
             for _ in range(2)]
    for p in procs:
        p.start()
    outcomes = sorted(results.get(timeout=300) for _ in procs)
    for p in procs:
        p.join(timeout=60)
    assert [o for o, _ in outcomes] == ["opened", "refused"], outcomes
    assert "another 2025 start is being made" in outcomes[1][1]
    (start,) = rw.replay_runs(2025)
    assert start.data.tags["error"] == "StoppedHere"

    # In one process: the lock is held through the gate, the opening of the run and the logging of the gate's record,
    # and released before the inputs are read. The stopped start above needs the owner's ruling naming it first.
    import mlflow

    stopped = mrun.replay_starts(2025)[-1]["started_utc"]
    head = git(chain.repo, "rev-parse", "HEAD").strip()
    evidence(chain)
    rw.commit(chain.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", head, start=stopped))}, "the owner's ruling")
    held = []
    real_gate, real_start, real_log_text = mrun.held_out_gate, tracking.start_run, mlflow.log_text

    def gate(*args, **kwargs):
        out = real_gate(*args, **kwargs)
        held.append(("gate", lock_is_held(chain.root)))
        return out

    @contextmanager
    def opened(*args, **kwargs):
        with real_start(*args, **kwargs) as run:
            held.append(("opened", lock_is_held(chain.root)))
            yield run

    def log_text(text, path, *args, **kwargs):
        held.append((path, lock_is_held(chain.root)))
        return real_log_text(text, path, *args, **kwargs)

    def read(*args, **kwargs):
        held.append(("read", lock_is_held(chain.root)))
        raise rw.StoppedHere("read")

    monkeypatch.setattr(mrun, "held_out_gate", gate)
    monkeypatch.setattr(tracking, "start_run", opened)
    monkeypatch.setattr(mlflow, "log_text", log_text)
    monkeypatch.setattr(mrun, "_read_inputs", read)
    with pytest.raises(rw.StoppedHere):
        mrun.run_replay(2025)
    assert held == [("gate", True), ("opened", True), (mrun.GATE_RECORD, True), ("read", False)], held
    assert not lock_is_held(chain.root)


def test_the_2025_gate_walks_every_commit_after_the_first_freeze_commit(tmp_path, monkeypatch):
    """Commit by commit from the freeze commit of v2's earliest 2024 start to HEAD: a rule change and its revert with
    the tag unmoved refuse, both named with their paths, where an endpoint diff holds neither; a dependency change
    and its revert refuse; an empty commit is no change (control)."""
    c = rw.chain_copy(tmp_path / "rules", monkeypatch)
    change = rw.commit(c.repo, {RULE_FILE: "MIN = 3\n"}, "change")
    revert = rw.commit(c.repo, {RULE_FILE: "MIN = 4\n"}, "revert")
    assert not git(c.repo, "diff", "--name-only", c.f0, "HEAD", "--", "src").strip()
    with pytest.raises(mrun.FreezeError) as e:
        mrun.held_out_gate(2025)
    said = str(e.value)
    assert "(a)" in said and all(f"{x[:12]} ['{RULE_FILE}']" in said for x in (change, revert))

    c = rw.chain_copy(tmp_path / "dependency", monkeypatch)
    bump = rw.commit(c.repo, {"uv.lock": "version = 2\n"}, "bump")
    back = rw.commit(c.repo, {"uv.lock": "version = 1\n"}, "back")
    with pytest.raises(mrun.FreezeError, match=rf"{bump[:12]} \['uv.lock'\].*{back[:12]} \['uv.lock'\]"):
        mrun.held_out_gate(2025)

    c = rw.chain_copy(tmp_path / "empty", monkeypatch)
    rw.commit(c.repo, message="nothing", empty=True)
    assert mrun.held_out_gate(2025)[2]["rule_commits"] == []


def test_the_2025_gate_refuses_a_merge_a_shallow_clone_and_a_head_off_the_freeze_commit(tmp_path, monkeypatch):
    c = rw.chain_copy(tmp_path / "merge", monkeypatch)
    base = git(c.repo, "rev-parse", "HEAD").strip()
    git(c.repo, "checkout", "-q", "-b", "side")
    rw.commit(c.repo, {"README": "side\n"}, "side")
    git(c.repo, "checkout", "-q", "-")
    rw.commit(c.repo, {"LICENSE": "main\n"}, "main")
    git(c.repo, "merge", "-q", "--no-ff", "--no-edit", "side")
    assert len(git(c.repo, "log", "-1", "--format=%P").split()) == 2 and base
    with pytest.raises(mrun.FreezeError, match="merge commits"):
        mrun.held_out_gate(2025)

    c = rw.chain_copy(tmp_path / "shallow", monkeypatch)
    (c.repo / ".git" / "shallow").write_text(c.f0 + "\n")  # the history marked cut at F0, as a shallow clone's
    assert git(c.repo, "rev-parse", "--is-shallow-repository").strip() == "true"
    with pytest.raises(mrun.FreezeError, match="shallow clone"):
        mrun.held_out_gate(2025)

    c = rw.chain_copy(tmp_path / "off", monkeypatch)
    git(c.repo, "checkout", "-q", "--orphan", "off")
    rw.commit(c.repo, message="the same files, on a history of their own")
    with pytest.raises(mrun.FreezeError, match="does not descend from the freeze commit"):
        mrun.held_out_gate(2025)


def test_a_fix_after_a_failed_first_2024_start_refuses_the_2025_gate(chain):
    """The first v2 2024 start, under F0, failed; a rule fix was committed, the tag moved to F1 and recorded as a
    departure; the departure replay of 2024 finished and is the first v2 2024 ledger entry. A 2025 start with
    --departure is refused with cause (a), naming the fix and its paths: the walk starts at F0, the earliest 2024
    start's freeze commit, while the artifacts are checked against the finished F1 entry."""
    c = tracking.client()
    c.set_tag(chain.first_2024, "status", "started")  # v2's first 2024 start, as one that stopped
    c.set_terminated(chain.first_2024, "FAILED")
    rw.write_ledger(chain.repo, [])
    fix = rw.commit(chain.repo, {RULE_FILE: "MIN = 5\n"}, "the fix")
    git(chain.repo, "tag", "-f", "-a", "-m", "moved", V2_TAG, fix)
    rw.commit(chain.repo, {"AGENTS.md": rw.agents(v2_departure(chain.f0, fix))}, "record the departure")
    # Validation and the freeze run again under the fixed rules, and the departure replay of 2024 under F1.
    w = rw.chain_world()
    dq24 = rw.log_dq(2024)
    val = rw.log_validation(w, dq24)
    rw.log_frozen_models(w, val, dq24)
    assert mrun.run_replay(2024, n_boot=20, departure=True) == 0
    (departed,) = [r for r in rw.replay_runs(2024) if r.info.run_id != chain.first_2024]
    assert departed.data.tags["freeze_commit"] == fix and departed.data.tags["departure_from"] == chain.first_2024
    rw.write_ledger(chain.repo, [rw.ledger_entry(departed.info.run_id)])
    rw.log_dq(2025, hq_revised_records=1)
    with pytest.raises(mrun.FreezeError, match=rf"\(a\) commits since the freeze commit {chain.f0[:12]}.*"
                                               rf"{fix[:12]} \['{RULE_FILE}'\]"):
        mrun.held_out_gate(2025, departure=True)


def evidence(chain, commit=None, **tags):
    """A finished evidence run, made by hand, of `commit` (HEAD) on the gate's data-quality run."""
    head = commit or git(chain.repo, "rev-parse", "HEAD").strip()
    return rw.fake_run({"kind": "evidence", "status": "complete", "git_dirty": "false", "git_commit": head,
                        "dq_run_id": chain.dq25, "first_2024_run_id": chain.first_2024} | tags)


def change_and_revert(chain):
    rw.commit(chain.repo, {RULE_FILE: "MIN = 3\n"}, "change")
    return rw.commit(chain.repo, {RULE_FILE: "MIN = 4\n"}, "revert")


@pytest.mark.parametrize("outcome, standing", [("clean", "clean"), ("post hoc", "post_hoc")], ids=["clean", "post_hoc"])
def test_an_owner_s_ruling_entry_lifts_a_refusal_with_its_outcome(chain, outcome, standing):
    """After a rule change (and its revert, the tag unmoved), the owner's entry naming the commit before it, with a
    finished evidence run of that commit on the gate's data-quality run (`valiwatt evidence` itself), starts the
    replay with its outcome: clean, tagged held_out; post hoc, tagged post_hoc; in the run tags and the gate's record,
    which names the evidence run."""
    ruled = change_and_revert(chain)
    with pytest.raises(mrun.FreezeError, match=r"\(a\)"):
        mrun.held_out_gate(2025)
    assert mrun.run_evidence() == 0
    (ev,) = mrun._replay_runs(tracking.client(), kind="evidence")
    rw.commit(chain.repo, {"AGENTS.md": rw.agents(rw.ruling(outcome, ruled))}, "the owner's ruling")
    assert mrun.run_replay(2025, n_boot=20) == 0
    (start,) = rw.replay_runs(2025)
    assert start.data.tags["standing"] == standing
    assert start.data.tags["evaluation"] == ("held_out" if standing == "clean" else "post_hoc")
    gate = mrun._load_json_artifact(tracking.client(), start.info.run_id, mrun.GATE_RECORD)
    assert gate["standing"] == standing and gate["causes"] == ["a"]
    assert gate["ruling"] == {"commit": ruled, "outcome": standing,
                              "head": f"2026-12-01, the owner's ruling on 2025: {outcome}", "start": "",
                              "evidence_run_id": ev.info.run_id}


def side_commit(chain) -> str:
    """A commit on a branch of its own, off HEAD's line (a branch never merged, or the old side of a rebase); HEAD
    is back where it was and the tree is clean."""
    branch = git(chain.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    git(chain.repo, "checkout", "-q", "-b", "side")
    side = rw.commit(chain.repo, {"notes.md": "a commit HEAD never holds\n"}, "on a side branch")
    git(chain.repo, "checkout", "-q", branch)
    return side


def test_a_ruling_entry_that_does_not_apply_leaves_the_refusal(tmp_path, monkeypatch):
    """One case each: a rule commit after the commit it names; an earlier 2025 start whose code descends past it; a
    commit off HEAD's line (a descendant of the rule commits on a side branch); a head in other words, which is no
    ruling; and, refused: an entry whose commit cannot be resolved, one naming its commit by fewer than 12
    characters, one naming two starts. Refused naming `valiwatt evidence`: no evidence run, one not finished, one made
    from a dirty tree, one of a commit neither C nor a descendant of it up to HEAD (an ancestor of C; a descendant of C
    off HEAD's line), one on another data-quality run. The control: a valid evidence run lifts it."""
    def fresh(name):
        c = rw.chain_copy(tmp_path / name, monkeypatch)
        return c, change_and_revert(c)

    c, ruled = fresh("later_change")
    evidence(c)
    rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", ruled))}, "ruling")
    change_and_revert(c)
    with pytest.raises(mrun.FreezeError, match=r"\(a\)"):
        mrun.held_out_gate(2025)

    c, ruled = fresh("start_past_it")
    evidence(c)
    after = rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", ruled))}, "ruling")
    stopped = rw.fake_run({"kind": "replay", "year": "2025", "freeze_tag": V2_TAG, "freeze_commit": c.f0,
                           "git_commit": after, "status": "started", "stage": "opened"}, status="FAILED")
    start = mrun.replay_starts(2025)[-1]["started_utc"]
    assert mrun.replay_starts(2025)[-1]["run_id"] == stopped
    rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", ruled, start=start))}, "the ruling names it")
    with pytest.raises(mrun.FreezeError, match=r"\(a\).*\(f\)"):
        mrun.held_out_gate(2025)

    c, ruled = fresh("off_head")
    side = side_commit(c)  # it descends from both rule commits, but HEAD does not hold it
    evidence(c)
    rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", side))}, "a ruling naming a commit off HEAD's line")
    with pytest.raises(mrun.FreezeError, match=r"\(a\)"):
        mrun.held_out_gate(2025)

    c, ruled = fresh("other_words")
    evidence(c)
    other = rw.ruling("clean", ruled).replace("the owner's ruling", "the owner's decision")
    rw.commit(c.repo, {"AGENTS.md": rw.agents(other)}, "not a ruling")
    with pytest.raises(mrun.FreezeError, match=r"\(a\)"):
        mrun.held_out_gate(2025)

    c, ruled = fresh("unresolved")
    rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", "abcdef012345"))}, "a commit of no history")
    with pytest.raises(mrun.FreezeError, match="names abcdef012345, which is no commit"):
        mrun.held_out_gate(2025)

    c, ruled = fresh("short_prefix")
    while not any(ch.isdigit() for ch in ruled[:10]):  # the section's pattern reads a 7-11 character run as a commit
        ruled = rw.commit(c.repo, message="another commit", empty=True)  # only when it holds a digit
    evidence(c)
    rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", ruled[:10]))}, "a ruling naming 10 characters")
    with pytest.raises(mrun.FreezeError, match=f"names {ruled[:10]}, which is no commit .* by at least 12"):
        mrun.held_out_gate(2025)

    c, ruled = fresh("two_starts")
    evidence(c)
    two = rw.ruling("clean", ruled, start="2026-01-01T00:00:00.000Z").rstrip("\n") + \
        " And on the start of 2026-01-02T00:00:00.000Z.\n"
    rw.commit(c.repo, {"AGENTS.md": rw.agents(two)}, "a ruling naming two starts")
    with pytest.raises(mrun.FreezeError, match="names 1 commits and 2 starts"):
        mrun.held_out_gate(2025)

    cases = {"none": None, "unfinished": {"status": "started"}, "dirty": {"git_dirty": "true"},
             "off_commit": "f0", "off_head": "side", "other_dq": {"dq_run_id": "dq24"}}
    for name, spoil in cases.items():
        c, ruled = fresh(f"evidence_{name}")
        if spoil == "f0":
            evidence(c, commit=c.f0)
        elif spoil == "side":  # a descendant of C, made on a branch HEAD does not hold
            evidence(c, commit=side_commit(c))
        elif spoil is not None:
            evidence(c, **{k: getattr(c, v, v) for k, v in spoil.items()})
        rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", ruled))}, "ruling")
        with pytest.raises(mrun.FreezeError, match="run `valiwatt evidence` first"):
            mrun.held_out_gate(2025)
    c, ruled = fresh("control")
    ev = evidence(c)
    rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", ruled))}, "ruling")
    assert mrun.held_out_gate(2025)[2]["ruling"]["evidence_run_id"] == ev


def stop_start(where="load_raw", error=RuntimeError):
    """The next 2025 start stops after its run opens, at `where` (a function of monitor/run.py), restored after."""
    real = getattr(mrun, where)

    def fail(*args, **kwargs):
        raise error("stopped on purpose")

    setattr(mrun, where, fail)
    try:
        with pytest.raises(error):
            mrun.run_replay(2025, n_boot=20)
    finally:
        setattr(mrun, where, real)


def test_after_an_unfinished_test_of_record_a_later_start_refuses_until_a_ruling(tmp_path, monkeypatch):
    """A FAILED first start: the next start refuses with nothing changed, naming the stopped start, its start time,
    stage, error and whether its run holds results; a ruling naming the commit HEAD was at but no start, or another
    start, leaves it refused. With one naming the stopped start, and its evidence run, saying clean, the next start runs
    tagged held_out with standing clean; saying post hoc, post_hoc. If that start stops too, the start after it refuses
    again until a ruling naming it; once the ruled start has finished, a later start is a rerun, tagged
    seen_by_developers, and (f) stays quiet. A stopped first start deleted from the run store still refuses the
    next."""
    c = rw.chain_copy(tmp_path / "clean", monkeypatch)
    stop_start()
    (first,) = mrun.replay_starts(2025)
    assert (first["stage"], first["error"], first["holds_results"]) == ("opened", "RuntimeError", False)
    with pytest.raises(mrun.FreezeError) as e:
        mrun.held_out_gate(2025)
    said = str(e.value)
    assert all(x in said for x in ("(f)", first["run_id"], first["started_utc"], "'opened'", "'RuntimeError'",
                                   "holds no results")), said
    head = git(c.repo, "rev-parse", "HEAD").strip()
    evidence(c)
    rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", head))}, "a ruling naming no start")
    with pytest.raises(mrun.FreezeError, match=r"\(f\)"):
        mrun.held_out_gate(2025)
    head = git(c.repo, "rev-parse", "HEAD").strip()
    evidence(c)
    rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", head, start="2026-01-01T00:00:00.000Z"))},
              "a ruling naming another start")
    with pytest.raises(mrun.FreezeError, match=r"\(f\)"):
        mrun.held_out_gate(2025)
    head = git(c.repo, "rev-parse", "HEAD").strip()
    evidence(c)
    rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", head, start=first["started_utc"]))},
              "the ruling naming the stopped start")
    stop_start(where="_never_acts", error=mrun.MonitorActedError)  # the ruled start stops too
    second = mrun.replay_starts(2025)[-1]
    assert second["stage"] == "replayed" and second["standing"] == "clean"
    with pytest.raises(mrun.FreezeError, match=rf"\(f\).*{second['run_id']}"):
        mrun.held_out_gate(2025)
    head = git(c.repo, "rev-parse", "HEAD").strip()
    evidence(c)
    rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", head, start=first["started_utc"]))},
              "a ruling of the current commit naming the earlier stopped start")
    with pytest.raises(mrun.FreezeError, match=rf"\(f\).*{second['run_id']}"):
        mrun.held_out_gate(2025)
    head = git(c.repo, "rev-parse", "HEAD").strip()
    evidence(c)
    rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("clean", head, start=second["started_utc"]))},
              "the ruling naming the second stopped start")
    assert mrun.run_replay(2025, n_boot=20) == 0
    record = rw.replay_runs(2025)[-1]
    assert (record.data.tags["evaluation"], record.data.tags["standing"]) == ("held_out", "clean")
    assert mrun.run_replay(2025, n_boot=20) == 0  # a rerun: (f) quiet, seen by the developers
    rerun = rw.replay_runs(2025)[-1]
    assert rerun.data.tags["evaluation"] == "seen_by_developers"
    gate = mrun._load_json_artifact(tracking.client(), rerun.info.run_id, mrun.GATE_RECORD)
    assert gate["causes"] == [] and gate["ruling"] is None and gate["standing"] == "clean"
    stop_start()  # a rerun that stops after the test of record finished: (f) stays quiet
    assert mrun.held_out_gate(2025)[2]["causes"] == []

    c = rw.chain_copy(tmp_path / "post_hoc", monkeypatch)
    stop_start()
    (first,) = mrun.replay_starts(2025)
    head = git(c.repo, "rev-parse", "HEAD").strip()
    evidence(c)
    rw.commit(c.repo, {"AGENTS.md": rw.agents(rw.ruling("post hoc", head, start=first["started_utc"]))},
              "the ruling: post hoc")
    assert mrun.run_replay(2025, n_boot=20) == 0
    record = rw.replay_runs(2025)[-1]
    assert (record.data.tags["evaluation"], record.data.tags["standing"]) == ("post_hoc", "post_hoc")

    # A stopped first start deleted from the run store still counts (deleted runs are read): the next start refuses.
    c = rw.chain_copy(tmp_path / "deleted", monkeypatch)
    stop_start()
    (first,) = mrun.replay_starts(2025)
    tracking.client().delete_run(first["run_id"])
    assert [(s["run_id"], s["finished"]) for s in mrun.replay_starts(2025)] == [(first["run_id"], False)]
    with pytest.raises(mrun.FreezeError, match=rf"\(f\).*{first['run_id']}"):
        mrun.held_out_gate(2025)


def test_a_later_2025_start_is_tagged_seen_by_developers(chain):
    assert mrun.run_replay(2025, n_boot=20) == 0
    assert mrun.run_replay(2025, n_boot=20) == 0
    first, later = rw.replay_runs(2025)
    assert first.data.tags["evaluation"] == "held_out" and later.data.tags["evaluation"] == "seen_by_developers"


def test_the_replay_function_refuses_2025_on_its_own(chain):
    """replay() called on 2025 without the command, with no run of its own in the store, is refused even when the 2025
    gate would pass: the gate is the command's, and the replay function reads 2025 only for its started run."""
    from valiwatt.monitor import replay as rp

    assert mrun.held_out_gate(2025)[2]["standing"] == "clean"
    w = rw.chain_world()
    raw, obs = rw.vintage(2025)
    with pytest.raises(mrun.FreezeError, match="no run ID"):
        rp.replay(raw, obs, w.model, w.thresholds, w.reference, "2025-01-01", "2025-01-12", data_last(2025),
                  runs=w.runs)
    with pytest.raises(mrun.FreezeError, match="no run ID"):  # 2025's days with records only to the end of 2024
        rp.replay(raw, obs, w.model, w.thresholds, w.reference, "2025-01-01", "2025-01-12", data_last(2024),
                  runs=w.runs)
