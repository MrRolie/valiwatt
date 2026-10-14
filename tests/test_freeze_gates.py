"""The freeze and lineage gates refuse what they exist to refuse: a missing or moved tag, a changed or added rule
file, a frozen model from another validation run, year or rule set, and a model file that no longer matches itself.
The replay itself stops at each gate it calls, before the step the gate protects."""

import subprocess
from types import SimpleNamespace

import pandas as pd
import pytest

from replay_world import FIRST, LAST, frozen_and_reference, world
from valiwatt.forecast import freeze as fz
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import build
from valiwatt.forecast.guards import LookaheadError
from valiwatt.forecast.validate import LineageError
from valiwatt.monitor import rules
from valiwatt.monitor import run as mrun
from valiwatt.quality.demand import build_demand


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


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


def test_a_moved_tag_is_refused_unless_recorded_as_a_departure():
    freeze = {"freeze_commit": "b" * 40}
    assert mrun.check_tag_not_moved(freeze, {"b" * 40: ["r1"]}, departure=False) == []
    with pytest.raises(mrun.FreezeError):
        mrun.check_tag_not_moved(freeze, {"a" * 40: ["r0"]}, departure=False)
    with pytest.raises(mrun.FreezeError):  # a bare flag is not a record
        mrun.check_tag_not_moved(freeze, {"a" * 40: ["r0"]}, departure=True, recorded="None yet.")
    other = f"# x\n\n{mrun.DEPARTURES_HEADING}\n\n- 2026-10-17, commit {'c' * 12}: reason.\n\n## Other\n"
    with pytest.raises(mrun.FreezeError):  # a departure recorded for another commit is not a record of this one
        mrun.check_tag_not_moved(freeze, {"a" * 40: ["r0"]}, departure=True,
                                 recorded=mrun.recorded_departures(other))
    agents =f"# x\n\n{mrun.DEPARTURES_HEADING}\n\n- 2026-10-17, commit {'b' * 12}: reason.\n\n## Other\n"
    assert mrun.check_tag_not_moved(freeze, {"a" * 40: ["r0"]}, departure=True,
                                    recorded=mrun.recorded_departures(agents)) == ["r0"]


def test_earlier_replays_are_found_even_when_deleted(tmp_path, monkeypatch):
    import mlflow
    from mlflow.entities import ViewType

    from valiwatt import tracking

    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    c = tracking.client()
    exp = tracking.experiment_id(mrun.EXPERIMENT)
    with mlflow.start_run(experiment_id=exp) as r:
        mlflow.set_tags({"kind": "replay", "year": "2024", "freeze_commit": "a" * 40})
    c.delete_run(r.info.run_id)
    assert not c.search_runs([exp], "tags.kind = 'replay'")  # hidden from the default view
    assert mrun.earlier_freeze_commits(2024, c) == {"a" * 40: [r.info.run_id]}
    assert c.search_runs([exp], "tags.kind = 'replay'", run_view_type=ViewType.ALL)
    with pytest.raises(mrun.FreezeError):
        mrun.check_tag_not_moved({"freeze_commit": "b" * 40}, mrun.earlier_freeze_commits(2024, c), departure=False)


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
    from valiwatt import tracking
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
    from valiwatt import tracking

    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    frozen, _ = frozen_and_reference()
    raw, obs = world()
    f = build(build_demand(raw, FIRST, LAST).frame, obs.set_index("ts_utc")["temp_c"])
    example = f[fz.INPUT_COLUMNS].dropna().head(24)
    run, version = fz.log_frozen(frozen, pd.DataFrame({"feature": ["x"], "frozen": [0.0]}), example, {})
    return frozen, run, version


def test_the_registered_model_is_refused_when_its_run_records_another_hash(tmp_path, monkeypatch):
    from valiwatt import tracking

    frozen, run, _ = log_world_frozen(tmp_path, monkeypatch)
    loaded, _ = fz.load_frozen(frozen.replay_year)
    assert loaded.sha256() == frozen.sha256()
    tracking.client().set_tag(run.info.run_id, "coefficient_sha256", "0" * 64)
    with pytest.raises(fz.FrozenModelError):
        fz.load_frozen(frozen.replay_year)


def test_an_alias_pointing_at_another_years_model_is_refused(tmp_path, monkeypatch):
    from valiwatt import tracking

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
def replay_inputs(repo, monkeypatch):
    """run_replay's inputs, faked to pass every gate; each test spoils one. Nothing reads data or MLflow: the replay
    stops at its summary, the first step after the last gate."""
    oos = pd.DataFrame({"day": [pd.Timestamp("2023-06-01")], "pred_persistence": [1.0]})
    monkeypatch.setattr(mrun, "earlier_freeze_commits", lambda year: {})
    monkeypatch.setattr(mrun, "load_inputs", lambda: (fake_run("dq"), None, None))
    monkeypatch.setattr(mrun, "load_validation", lambda dq: (fake_run("val"), oos))
    monkeypatch.setattr(mrun, "validation_results", lambda val: {"thresholds": {"2024": {}}})
    monkeypatch.setattr(mrun, "load_raw", lambda dq: (None, None))
    monkeypatch.setattr(mrun, "replay", lambda *a: SimpleNamespace(model_sha256="a", clock_end=None))
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
    with pytest.raises(mrun.FreezeError, match="no `rules-frozen` tag"):
        mrun.run_replay(2024)


def test_the_replay_under_a_moved_tag_stops_before_reading_any_input(repo, replay_inputs, monkeypatch):
    git(repo, "tag", rules.FREEZE_TAG)
    monkeypatch.setattr(mrun, "load_inputs", reached("load_inputs"))
    monkeypatch.setattr(mrun, "earlier_freeze_commits", lambda year: {"c" * 40: ["r0"]})
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
