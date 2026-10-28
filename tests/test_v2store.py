"""The step-2 read of v2's store (docs/plan-v3.md, sections 10.3, 12 "v2's store" and 17): opened read-only, nothing
created or written there, everything read checked against v2's first 2024 replay's ledger entry, what was read logged
into v3's own store; no other command opens it. v2's store here is a synthetic one, built in a scratch directory with
MLflow's own client as v2's chain writes it (a data-quality run, a validation run with its out-of-sample frame, the
replay naming both), and its ledger entry built as report/ledger.py builds one."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from valiwatt import cli, firstrun, tracking, v2store
from valiwatt.config import FREEZE_TAG
from valiwatt.monitor.run import frozen_digest
from valiwatt.report.ledger import _utc

SRC = Path(v2store.__file__).resolve().parent
H64 = {k: hashlib.sha256(k.encode()).hexdigest() for k in ("hq", "eccc", "nbm", "rules", "code", "model", "commit")}


def oos_frame() -> pd.DataFrame:
    """A validation frame shaped as v2's frames/oos.parquet: hour-ending UTC labels, the day, the production forecast
    (blank on some hours)."""
    idx = pd.date_range("2020-01-01T06:00Z", "2020-03-01T05:00Z", freq="h", name="ts_utc")
    day = (idx - pd.Timedelta(hours=1) - pd.Timedelta(hours=5)).tz_convert(None).normalize()
    forecast = 20_000 + 1_000 * np.sin(np.arange(len(idx)) / 7.0)
    forecast[::50] = np.nan
    return pd.DataFrame({"target": forecast + 10, "day": day, "pred_gam_forecast": forecast}, index=idx)


def build_v2_store(root: Path, oos: pd.DataFrame | None = None, tags: dict | None = None,
                   params: dict | None = None, status: dict | None = None) -> dict:
    """v2's store under root/mlruns, and its ledger at root/replay-ledger.json. Returns the run IDs and the entry.
    tags, params: {"dq" | "val" | "replay": {name: value}}, laid over what v2's chain writes; status: {run: status},
    a run left RUNNING when so (each other run FINISHED)."""
    from mlflow import MlflowClient

    extra_tags, extra_params, status = tags or {}, params or {}, status or {}

    def finish(run_id, name):
        if status.get(name, "FINISHED") != "RUNNING":
            c.set_terminated(run_id, status.get(name, "FINISHED"))

    store = root / "mlruns"
    store.mkdir(parents=True)
    c = MlflowClient(tracking_uri=f"sqlite:///{store / 'mlflow.db'}")
    exps = {n: c.create_experiment(n, artifact_location=(store / "artifacts" / n).as_uri())
            for n in ("valiwatt-data-quality", "valiwatt-validation", "valiwatt-monitoring")}
    oos = oos_frame() if oos is None else oos
    staging = root / "staging"
    staging.mkdir()

    dq = c.create_run(exps["valiwatt-data-quality"],
                      tags={"kind": "dq", "gates": "passed"} | extra_tags.get("dq", {})).info.run_id
    for k, v in ({"hq_sha256": H64["hq"], "eccc_manifests_sha256": H64["eccc"], "nbm_manifest_sha256": H64["nbm"],
                  "through": "2024"} | extra_params.get("dq", {})).items():
        c.log_param(dq, k, v)
    finish(dq, "dq")

    val = c.create_run(exps["valiwatt-validation"], tags={"kind": "validation", "status": "complete",
                                                          "dq_run_id": dq, "nbm_manifest_sha256": H64["nbm"]}
                       | extra_tags.get("val", {})).info.run_id
    (staging / "frames").mkdir()
    oos.to_parquet(staging / "frames" / "oos.parquet")
    c.log_artifact(val, str(staging / "frames" / "oos.parquet"), artifact_path="frames")
    finish(val, "val")

    reference = v2store._reference_sha256(oos_frame(), 2024)  # what the replay logged: the true frame's hash
    frozen = {"production": H64["model"], "comparators": {"v1": H64["code"]}, "thresholds": H64["rules"],
              "cold_c": "-11.1", "reference": reference}
    tags = {"kind": "replay", "year": "2024", "freeze_tag": FREEZE_TAG, "freeze_commit": H64["commit"][:40],
            "rules_sha256": H64["rules"], "code_sha256": H64["code"], "coefficient_sha256": H64["model"],
            "evaluation": "seen_by_developers", "status": "complete", "dq_run_id": dq, "validation_run_id": val,
            "frozen_run_id": "f" * 32} | extra_tags.get("replay", {})
    replay = c.create_run(exps["valiwatt-monitoring"], tags=tags).info.run_id
    (staging / "results").mkdir()
    (staging / "results" / "replay.json").write_text(json.dumps(
        {"freeze_tag": FREEZE_TAG, "freeze_commit": tags["freeze_commit"], "frozen_sha256": frozen}))
    c.log_artifact(replay, str(staging / "results" / "replay.json"), artifact_path="results")
    finish(replay, "replay")
    run = c.get_run(replay)
    entry = {"run_id": replay, "year": 2024, "freeze_tag": FREEZE_TAG, "freeze_commit": tags["freeze_commit"],
             "rules_sha256": tags["rules_sha256"], "code_sha256": tags["code_sha256"],
             "started_utc": _utc(run.info.start_time), "ended_utc": _utc(run.info.end_time), "status": "complete",
             "mape": 0.03, "coverage": 0.9, "recommendations": [], "departure": False, "departure_from": [],
             "evaluation": "seen_by_developers", "model_sha256": H64["model"],
             "verdict": {"shipped": "better", "family": "better"}, "frozen_sha256": frozen_digest(frozen),
             "standing": ""}
    write_ledger(root, entry)
    for f in staging.rglob("*"):
        f.unlink() if f.is_file() else None
    return {"store": store, "ledger": root / "replay-ledger.json", "dq": dq, "val": val, "replay": replay,
            "entry": entry, "oos": oos}


def write_ledger(root: Path, *entries: dict) -> None:
    (root / "replay-ledger.json").write_text(json.dumps({"replays": list(entries)}))


def snapshot(root: Path) -> dict:
    """Every path under root (files and directories), each file with its SHA-256."""
    return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir")
            for p in sorted(root.rglob("*"))}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """One synthetic v2 store for the module: every test reads it, none may change it."""
    w = build_v2_store(tmp_path_factory.mktemp("v2"))
    return {**w, "built": snapshot(w["store"])}


@pytest.fixture
def v2(world, tmp_path, monkeypatch):
    """The store, its ledger copied into a scratch checkout of v3 (whose docs/replay-ledger.json the command reads),
    v3's own store in that checkout, the first-run guard passing."""
    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "v3" / "mlruns")
    monkeypatch.setattr(v2store, "REPO_ROOT", tmp_path / "v3")
    (tmp_path / "v3" / "docs").mkdir(parents=True)
    (tmp_path / "v3" / "docs" / "replay-ledger.json").write_bytes(world["ledger"].read_bytes())
    write_ledger(tmp_path, world["entry"])
    monkeypatch.setattr(firstrun, "require_first_run", lambda: "0" * 40)
    return {**world, "ledger": tmp_path / "replay-ledger.json"}


def test_the_read_of_v2s_store_leaves_it_byte_identical_and_logs_what_it_read(v2):
    before = snapshot(v2["store"])
    assert v2store.run_read_v2(v2["store"]) == 0
    assert snapshot(v2["store"]) == before == v2["built"]  # no file changed, none created, none removed
    rec = v2store.recorded()
    assert rec["v2_replay_run_id"] == v2["replay"] and rec["v2_dq_run_id"] == v2["dq"]
    assert rec["v2_validation_run_id"] == v2["val"]
    assert (rec["hq_sha256"], rec["eccc_manifests_sha256"], rec["nbm_manifest_sha256"]) == (H64["hq"], H64["eccc"],
                                                                                           H64["nbm"])
    t = tracking.client().get_run(rec["run_id"]).data.tags  # made before v3's freeze (section 10.3, D5)
    assert (t["freeze_tag"], t["freeze_commit"], t["v2_freeze_tag"]) == ("v3-pre-freeze", "", "rules-frozen-v2")
    got = v2store.recorded_forecasts()
    want = v2["oos"]["pred_gam_forecast"].dropna()
    assert list(got.columns) == ["ts", "forecast"]
    assert pd.DatetimeIndex(got["ts"]).equals(pd.DatetimeIndex(want.index, name="ts"))
    assert np.array_equal(got["forecast"].to_numpy(), want.to_numpy())


def test_v2s_database_is_opened_read_only(v2):
    con = v2store._connect(v2["store"] / v2store.DB_NAME)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            con.execute("CREATE TABLE written (a)")
    finally:
        con.close()
    with pytest.raises(v2store.V2StoreRefused, match="no mlflow.db"):
        v2store._connect(v2["store"] / "elsewhere" / v2store.DB_NAME)
    assert not (v2["store"] / "elsewhere").exists()


@pytest.mark.parametrize("field,value", [("rules_sha256", "0" * 64), ("code_sha256", "1" * 64),
                                         ("freeze_commit", "2" * 40), ("model_sha256", "3" * 64),
                                         ("frozen_sha256", "4" * 64), ("started_utc", "2026-10-17T23:11:15.047Z")])
def test_a_v2_store_that_disagrees_with_the_ledger_entry_is_refused(v2, field, value):
    entry = {**v2["entry"], field: value}
    write_ledger(v2["ledger"].parent, entry)
    with pytest.raises(v2store.V2StoreRefused, match=field):
        v2store.read_v2_store(v2["store"], v2["ledger"])
    (v2store.REPO_ROOT / "docs" / "replay-ledger.json").write_bytes(v2["ledger"].read_bytes())
    with pytest.raises(v2store.V2StoreRefused, match=field):
        v2store.run_read_v2(v2["store"])
    assert v2store.recorded() is None  # nothing logged in v3's store


def test_validation_forecasts_other_than_the_replays_are_refused(world, tmp_path):
    changed = oos_frame()
    changed.iloc[5, changed.columns.get_loc("pred_gam_forecast")] += 1e-6
    other = build_v2_store(tmp_path / "v2", oos=changed)
    with pytest.raises(v2store.V2StoreRefused, match="not those whose hash the replay logged"):
        v2store.read_v2_store(other["store"], other["ledger"])
    assert len(v2store.read_v2_store(world["store"], world["ledger"]).forecasts) == oos_frame()[
        "pred_gam_forecast"].notna().sum()  # the control: the replay's own forecasts are read



@pytest.mark.parametrize(("fault", "match"), [
    ({"status": {"replay": "KILLED"}}, "is KILLED .*not a finished run"),
    ({"status": {"dq": "RUNNING"}}, "is RUNNING .*not a finished run"),
    ({"tags": {"dq": {"gates": "failed"}}}, "is not a data-quality run whose gates passed"),
    ({"params": {"dq": {"hq_sha256": "x"}}}, r"holds no SHA-256 for \['hq_sha256'\]"),
    ({"tags": {"val": {"status": "running"}}}, "is not a complete validation run"),
    ({"tags": {"val": {"dq_run_id": "0" * 32}}}, f"was built on data-quality run '{'0' * 32}'"),
    ({"tags": {"val": {"nbm_manifest_sha256": H64["code"]}}}, "read other NBM files than data-quality run"),
], ids=["replay_killed", "dq_running", "dq_failed", "hash_not_sha256", "validation_running", "validation_other_dq",
        "validation_other_nbm"])
def test_a_v2_store_whose_runs_are_not_those_the_replay_names_is_refused(tmp_path, fault, match):
    """Section 10.3: the replay run finished, the data-quality run it names a passed one holding three SHA-256, the
    validation run it names complete and built on that run with its NBM files; each fault is refused with its own
    message, and that one alone (the control: the store as v2's chain writes it, read in the tests above)."""
    other = build_v2_store(tmp_path / "v2", **fault)
    with pytest.raises(v2store.V2StoreRefused, match=match) as e:
        v2store.read_v2_store(other["store"], other["ledger"])
    assert "; " not in str(e.value)


def test_a_copy_of_v2s_store_set_aside_elsewhere_is_refused(world, tmp_path):
    """PATH is v2's mlruns/ in place: a copy's runs record their artifacts under the original, outside PATH."""
    copy = tmp_path / "elsewhere" / "mlruns"
    shutil.copytree(world["store"], copy)
    with pytest.raises(v2store.V2StoreRefused, match="artifacts outside v2's store"):
        v2store.read_v2_store(copy, world["ledger"])
    assert snapshot(world["store"]) == world["built"]


def test_v2s_store_is_read_once_and_a_second_read_is_refused_before_it_opens_anything(v2, monkeypatch):
    """Section 10.3, "v2's store is opened once": a second `read-v2` is refused before v2's database or its ledger
    entry's runs are opened; v3's store keeps its one read, v2's store is unchanged."""
    assert v2store.run_read_v2(v2["store"]) == 0
    first = v2store.recorded()
    opened, real = [], v2store._connect
    monkeypatch.setattr(v2store, "_connect", lambda db: opened.append(db) if Path(db).resolve().is_relative_to(
        v2["store"].resolve()) else real(db))
    monkeypatch.setattr(v2store, "read_v2_store", lambda *a, **k: opened.append(a))
    with pytest.raises(v2store.V2StoreRefused, match="read once"):
        v2store.run_read_v2(v2["store"])
    assert opened == [] and v2store.recorded() == first
    c = tracking.client()
    assert len(c.search_runs([c.get_experiment_by_name(v2store.EXPERIMENT).experiment_id])) == 1
    assert snapshot(v2["store"]) == v2["built"]


def _second_read_refused(v2, monkeypatch) -> None:
    """A second `read-v2` is refused as "read once" before v2's database is opened or a run is created in v3's store;
    v2's store is unchanged."""
    from mlflow.entities import ViewType

    c = tracking.client()
    exp = c.get_experiment_by_name(v2store.EXPERIMENT).experiment_id
    before = len(c.search_runs([exp], run_view_type=ViewType.ALL))
    opened, real = [], v2store._connect
    monkeypatch.setattr(v2store, "_connect", lambda db: opened.append(db) if Path(db).resolve().is_relative_to(
        v2["store"].resolve()) else real(db))
    monkeypatch.setattr(v2store, "read_v2_store", lambda *a, **k: opened.append(a))
    with pytest.raises(v2store.V2StoreRefused, match="read once"):
        v2store.run_read_v2(v2["store"])
    assert opened == [] and len(c.search_runs([exp], run_view_type=ViewType.ALL)) == before
    assert snapshot(v2["store"]) == v2["built"]


@pytest.mark.parametrize("crash", ["after_the_read", "while_logging", "control"])
def test_a_read_that_crashed_after_opening_v2s_store_refuses_every_later_read(v2, monkeypatch, crash):
    """Section 10.3, "v2's store is opened once", never fail-open: the read run is created in v3's store before v2's
    store is opened, so a read that crashed after opening it (no complete read logged, `recorded` None) still refuses
    a second read. The control: with no read attempted, the first read passes and is the one complete read."""
    import mlflow

    assert v2store.attempted() == []
    if crash == "control":
        assert v2store.run_read_v2(v2["store"]) == 0
        assert v2store.recorded() is not None
        (a,) = v2store.attempted()
        assert (a["status"], a["lifecycle_stage"], a["tag_status"]) == ("FINISHED", "active", "complete")
    else:
        real = v2store.read_v2_store

        def read_then_crash(*a, **k):
            real(*a, **k)
            raise RuntimeError("crashed after the read")

        def log_text(*a, **k):
            raise OSError("disk full")

        with monkeypatch.context() as m:
            if crash == "after_the_read":
                m.setattr(v2store, "read_v2_store", read_then_crash)
            else:
                m.setattr(mlflow, "log_text", log_text)
            with pytest.raises((RuntimeError, OSError)):
                v2store.run_read_v2(v2["store"])
        assert v2store.recorded() is None  # no complete read: nothing for a later step to consume
        (a,) = v2store.attempted()
        assert (a["status"], a["lifecycle_stage"], a["tag_status"]) == ("FAILED", "active", "started")
    _second_read_refused(v2, monkeypatch)


@pytest.mark.parametrize("left", ["deleted_complete", "killed_running"])
def test_a_deleted_or_killed_read_still_refuses_a_second_read(v2, monkeypatch, left):
    """A complete read soft-deleted from v3's store (MLflow's delete_run) is no longer given to later steps
    (`recorded` reads active runs) and still refuses a second read: v2's store was opened. So does a read whose
    process was killed while it read, its run left RUNNING with its tag status started."""
    if left == "deleted_complete":
        assert v2store.run_read_v2(v2["store"]) == 0
        rec = v2store.recorded()
        tracking.client().delete_run(rec["run_id"])
        want = (rec["run_id"], "FINISHED", "deleted")
    else:
        c = tracking.client()
        exp = c.get_experiment_by_name(v2store.EXPERIMENT) or c.get_experiment(c.create_experiment(v2store.EXPERIMENT))
        run = c.create_run(exp.experiment_id, tags={"kind": v2store.KIND, "status": "started"})
        want = (run.info.run_id, "RUNNING", "active")
    assert v2store.recorded() is None
    (a,) = v2store.attempted()
    assert (a["run_id"], a["status"], a["lifecycle_stage"]) == want
    _second_read_refused(v2, monkeypatch)


@pytest.mark.parametrize("own", ["same", "path_inside_own", "own_inside_path"])
def test_read_v2_refuses_a_path_that_is_v3s_own_store(v2, monkeypatch, own):
    """v3 logs into its own store: a PATH that is v3's store, lies inside it or holds it is refused before either is
    opened, and v2's store is unchanged."""
    monkeypatch.setattr(tracking, "MLRUNS", {"same": v2["store"], "path_inside_own": v2["store"].parent,
                                             "own_inside_path": v2["store"] / "v3" / "mlruns"}[own])
    with pytest.raises(v2store.V2StoreRefused, match="is v3's own store"):
        v2store.run_read_v2(v2["store"])
    assert snapshot(v2["store"]) == v2["built"]


def test_v2s_database_is_opened_immutable_and_refused_beside_a_write_ahead_log(world, v2, tmp_path):
    """A copy of v2's store with its database in write-ahead mode: read through the immutable URI, nothing is created
    beside it (a read-only reader alone would create the log and its index); a write-ahead log beside it is refused,
    before anything is read or logged."""
    copy = tmp_path / "wal" / "mlruns"
    shutil.copytree(world["store"], copy)
    db = copy / v2store.DB_NAME
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.close()
    before = snapshot(copy)
    con = v2store._connect(db)
    try:
        assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 3
    finally:
        con.close()
    assert snapshot(copy) == before
    (copy / f"{v2store.DB_NAME}-wal").write_bytes(b"")
    before = snapshot(copy)
    with pytest.raises(v2store.V2StoreRefused, match="write-ahead log"):
        v2store.run_read_v2(copy)
    assert snapshot(copy) == before and v2store.recorded() is None


@pytest.mark.parametrize("command", ["dq-v3", "validate-v3", "freeze-v3", "read-v2"])
def test_a_v3_command_whose_own_store_is_v2s_refuses_before_it_logs(v2, tmp_path, monkeypatch, command):
    """Section 10.3, "v3 runs only from its own checkout": a store holding v2's first 2024 replay run is v2's, and
    every v3 command that logs refuses it before it reads or logs anything. v3's store here is a copy of v2's
    synthetic store (for read-v2, PATH is v2's store in place), unchanged after the refusal, as v2's is."""
    from valiwatt.forecast.freeze_v3 import run_freeze_v3
    from valiwatt.forecast.validate_v3 import run_validate_v3
    from valiwatt.quality import run as qrun
    from valiwatt.quality import run_v3

    own = tmp_path / "own" / "mlruns"
    shutil.copytree(v2["store"], own)
    monkeypatch.setattr(tracking, "MLRUNS", own)
    reads = []

    def reader(name):
        return lambda *a, **k: reads.append(name)

    monkeypatch.setattr(run_v3, "latest_passed", reader("v3's data-quality run"))
    monkeypatch.setattr(run_v3, "check_byte_copies", reader("byte copies"))
    monkeypatch.setattr(qrun, "_verify_manifests", reader("raw files"))
    monkeypatch.setattr(v2store, "recorded", reader("v3's read of v2's store"))
    monkeypatch.setattr(v2store, "read_v2_store", reader("v2's store"))
    before = snapshot(own)
    run = {"dq-v3": lambda: run_v3.run_dq_v3(fresh_clone=True), "validate-v3": run_validate_v3,
           "freeze-v3": lambda: run_freeze_v3(2024), "read-v2": lambda: v2store.run_read_v2(v2["store"])}[command]
    with pytest.raises(v2store.V2StoreRefused, match="holds v2's first 2024 replay run"):
        run()
    assert reads == [] and snapshot(own) == before and snapshot(v2["store"]) == v2["built"]


def _calls(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "valiwatt.v2store":
            names |= {a.name for a in node.names}
        if isinstance(node, ast.ImportFrom) and node.module == "valiwatt" and any(a.name == "v2store" for a in node.names):
            names.add("*")
        if isinstance(node, ast.Import) and any(a.name == "valiwatt.v2store" for a in node.names):
            names.add("*")
    return names


def test_no_other_v3_command_opens_v2s_store():
    """Only `read-v2` reaches the functions that open v2's store, and only with the path it is given: no module of
    valiwatt but cli.py imports them, cli.py only in cmd_read_v2, the command's path has no default, and no module
    names a v2 store or opens an sqlite database but v2store.py."""
    openers = {"run_read_v2", "read_v2_store", "_connect", "_artifact", "*"}
    for path in SRC.rglob("*.py"):
        if path.name == "v2store.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        reached = _calls(tree) & openers
        if path.name == "cli.py" and path.parent == SRC:
            fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and _calls(n) & openers]
            assert [f.name for f in fns] == ["cmd_read_v2"], path
        else:
            assert not reached, f"{path} reaches v2's store through {reached}"
        assert "sqlite3" not in path.read_text(encoding="utf-8"), f"{path} opens an sqlite database"
    choices = next(a for a in cli.build_parser()._actions if hasattr(a, "choices") and a.choices).choices
    positional = [a for a in choices["read-v2"]._actions if not a.option_strings]
    assert [a.dest for a in positional] == ["path"] and positional[0].required and positional[0].default is None
    for name, p in choices.items():
        if name != "read-v2":
            assert not {a.dest for a in p._actions} & {"path", "store", "v2_store"}, name
    paths_src = (SRC / "paths.py").read_text(encoding="utf-8")
    assert "v2" not in paths_src.lower()
