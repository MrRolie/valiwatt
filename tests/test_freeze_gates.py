"""The freeze and lineage gates refuse what they exist to refuse: a missing or moved tag, a changed or added rule
file, a frozen model from another validation run, year or rule set, and a model file that no longer matches itself."""

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
    mrun.check_tag_not_moved(freeze, {"b" * 40, ""}, departure=False)
    with pytest.raises(mrun.FreezeError):
        mrun.check_tag_not_moved(freeze, {"a" * 40}, departure=False)
    mrun.check_tag_not_moved(freeze, {"a" * 40}, departure=True)


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

    dq = fake_run("dq")
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


def test_the_registered_model_is_refused_when_its_run_records_another_hash(tmp_path, monkeypatch):
    from valiwatt import tracking

    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    frozen, _ = frozen_and_reference()
    raw, obs = world()
    f = build(build_demand(raw, FIRST, LAST).frame, obs.set_index("ts_utc")["temp_c"])
    example = f[fz.INPUT_COLUMNS].dropna().head(24)
    run, _ = fz.log_frozen(frozen, pd.DataFrame({"feature": ["x"], "frozen": [0.0]}), example, {})
    loaded, _ = fz.load_frozen(frozen.replay_year)
    assert loaded.sha256() == frozen.sha256()
    tracking.client().set_tag(run.info.run_id, "coefficient_sha256", "0" * 64)
    with pytest.raises(fz.FrozenModelError):
        fz.load_frozen(frozen.replay_year)


def test_the_frozen_model_refuses_training_rows_inside_its_embargo(monkeypatch):
    raw, obs = world()
    f = build(build_demand(raw, FIRST, LAST).frame, obs.set_index("ts_utc")["temp_c"])
    oos = wf.run(f, wf.folds("2021-01", "2021-12"), scenarios=("persistence",))
    real = wf.split
    monkeypatch.setattr(wf, "split", lambda f_, fold: (f_[f_["day"] < fold.test_first_day], real(f_, fold)[1]))
    with pytest.raises(LookaheadError):
        fz.fit_frozen(f, oos, 2022)
