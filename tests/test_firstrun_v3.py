"""v3's first-run guard (docs/plan-v3.md, sections 10.2 and 12 "First run"): every v3 command that computes on real
data refuses unless `v3-first-run` exists and is an ancestor of the head, before it opens any file. Built as v2's
guard and its tests (tests/test_pipeline_refuses.py), on a scratch repository."""

from __future__ import annotations

import subprocess

import pytest

from valiwatt import firstrun
from valiwatt.config import FIRST_RUN_TAG, V3_FIRST_RUN_TAG


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def history(tmp_path, monkeypatch):
    """Commits a and b on the main line, c on a side branch from a; HEAD on b."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "test")
    commits = {}
    for name in ("a", "b"):
        (repo / f"{name}.txt").write_text(name)
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", name)
        commits[name] = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "side", commits["a"])
    (repo / "c.txt").write_text("c")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "c")
    commits["c"] = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    monkeypatch.setattr(firstrun, "REPO_ROOT", repo)
    return repo, commits


def tag(repo, commit, name=V3_FIRST_RUN_TAG):
    git(repo, "tag", "-f", "-a", "-m", "first run", name, commit)


def test_a_v3_real_data_run_without_the_v3_first_run_tag_is_refused(history):
    repo, c = history
    with pytest.raises(firstrun.FirstRunRefused, match="no `v3-first-run` tag"):
        firstrun.require_first_run()
    tag(repo, c["a"], FIRST_RUN_TAG)  # v2's tag is not v3's
    with pytest.raises(firstrun.FirstRunRefused, match="no `v3-first-run` tag"):
        firstrun.require_first_run()
    tag(repo, c["c"])  # on a side branch: not an ancestor of HEAD
    with pytest.raises(firstrun.FirstRunRefused, match="does not descend"):
        firstrun.require_first_run()
    tag(repo, c["b"])
    git(repo, "checkout", "-q", c["a"])  # HEAD before the tag
    with pytest.raises(firstrun.FirstRunRefused, match="does not descend"):
        firstrun.require_first_run()


def test_a_v3_real_data_run_from_a_descendant_of_the_v3_first_run_tag_passes(history):
    repo, c = history
    tag(repo, c["a"])  # HEAD (b) descends from it
    assert firstrun.require_first_run() == c["a"]
    tag(repo, c["b"])  # HEAD itself
    assert firstrun.require_first_run() == c["b"]


def test_every_v3_real_data_command_calls_the_guard_before_opening_a_file(history, monkeypatch, tmp_path):
    """dq-v3, read-v2, validate-v3, freeze-v3, replay-v3 (2024 and 2025), evidence-v3 and freeze-record-v3 refuse on a
    head without the tag before they read anything: every reader they reach first (v3's store and its check against
    v2's, the byte-copy check, v3's data-quality run, the raw files, v2's store and its ledger, the freeze record, the
    start lock in the run store's folder) is replaced by one that records the call, or points into a folder that must
    stay absent."""
    from valiwatt import freeze_record, paths, tracking, v2store
    from valiwatt.forecast import freeze_v3, validate_v3
    from valiwatt.monitor import run as mrun
    from valiwatt.monitor import run_v3 as replay_v3
    from valiwatt.quality import run as qrun
    from valiwatt.quality import run_v3

    reads = []

    def reader(name):
        return lambda *a, **k: reads.append(name)

    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    monkeypatch.setattr(paths, "MLRUNS", tmp_path / "mlruns")
    monkeypatch.setattr(freeze_record, "read_freeze_record", reader("the freeze record"))
    monkeypatch.setattr(mrun, "load_raw", reader("raw records"))
    monkeypatch.setattr(v2store, "recorded", reader("v3's store"))
    monkeypatch.setattr(run_v3, "check_byte_copies", reader("byte copies"))
    monkeypatch.setattr(qrun, "_verify_manifests", reader("raw files"))
    monkeypatch.setattr(qrun, "_verify_nbm", reader("NBM files"))
    monkeypatch.setattr(v2store, "read_v2_store", reader("v2's store"))
    monkeypatch.setattr(v2store, "_entry", reader("v2's ledger"))
    monkeypatch.setattr(v2store, "require_own_store", reader("v3's store against v2's"))
    monkeypatch.setattr(run_v3, "latest_passed", reader("v3's data-quality run"))
    with pytest.raises(firstrun.FirstRunRefused):
        run_v3.run_dq_v3()
    with pytest.raises(firstrun.FirstRunRefused):
        v2store.run_read_v2(tmp_path / "absent")
    with pytest.raises(firstrun.FirstRunRefused):
        validate_v3.run_validate_v3()
    with pytest.raises(firstrun.FirstRunRefused):
        freeze_v3.run_freeze_v3(2024)
    for year in (2024, 2025):
        with pytest.raises(firstrun.FirstRunRefused):
            replay_v3.run_replay_v3(year)
    with pytest.raises(firstrun.FirstRunRefused):
        replay_v3.run_evidence_v3()
    with pytest.raises(firstrun.FirstRunRefused):
        freeze_record.run_write_freeze_record("0" * 32)
    assert reads == [] and not (tmp_path / "mlruns").exists()
