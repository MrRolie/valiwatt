"""v3's freeze record, `docs/freeze-v3.json` (src/valiwatt/freeze_record.py; docs/plan-v3.md, sections 10.3, 11.2,
11.3 and 12), and the one loader of what judges a v3 replay.

- The loader (`frozen_chain_v3`) reads v3's production model and its three comparison models from a v3 frozen run,
  and the thresholds, cold_c and forecast-drift reference from v3's validation run; one hash covers each of them,
  bin edges and half-widths included.
- The writer refuses, each refusal seen to fire with the clean chain as its control: without the tag or its pin, a
  second write, 2025 on the machine, a named run unfinished, pre-freeze, dirty, on other rules or off the history, of
  another kind, of another lineage, of a lineage whose checks did not pass (a fresh clone), or judged by other
  artifacts.
- The reader takes the record as the commit that added it holds it, and refuses a second commit touching it; this
  repository's history is checked the same way (vacuous until the record is committed).

Synthetic chains and scratch repositories only (tests/records_v3_world.py)."""

from __future__ import annotations

import dataclasses
import json
import re

import pandas as pd
import pytest

from records_v3_world import (
    COLD_C,
    HQ,
    PRE,
    RULE_FILE,
    THRESHOLDS,
    commit,
    frozen_models,
    git,
    install,
    log_dq_v3,
    record_chain,
    record_chain_base,
    scratch_store,
)
from valiwatt import freeze_record as fr
from valiwatt.config import V3_FREEZE_TAG
from valiwatt.freeze_record import FreezeRecordError
from valiwatt.monitor import run as mrun
from valiwatt.paths import REPO_ROOT


@pytest.fixture(scope="module")
def record() -> dict:
    """The record of the clean chain, made once (make_record reads only)."""
    base, ids = record_chain_base()
    with pytest.MonkeyPatch.context() as mp:
        install(mp, base / "repo", base / "mlruns")
        return fr.make_record(ids["rehearsal"])


@pytest.fixture
def chain(tmp_path, monkeypatch):
    return record_chain(tmp_path, monkeypatch)


def client():
    from valiwatt import tracking

    return tracking.client()


# ------------------------------------------------------------------------------------- what judges a v3 replay


def test_the_loader_reads_v3_s_artifacts_for_its_year(chain):
    """frozen_chain_v3: v3's production model and its three comparison models from the frozen run, checked against
    their hashes; the year's thresholds and cold_c from v3's validation run; the forecast-drift reference from its
    out-of-sample frame's pred_v3, the days before the year only; one digest of the items."""
    c = fr.frozen_chain_v3(chain.frozen_2024, chain.validation, 2024)
    w = frozen_models()[2024]
    assert c["model"].sha256() == w["models"]["v3"].sha256()
    assert {k: m.sha256() for k, m in c["comparators"].items()} == {
        k: w["models"][k].sha256() for k in ("v2_noon", "v1_noon", "v2_midnight")}
    assert c["thresholds"] == THRESHOLDS["2024"] and c["cold_c"] == COLD_C
    oos = w["oos"]
    assert c["reference"]["forecast"].tolist() == oos.loc[oos["day"] < pd.Timestamp("2024-01-01"), "pred_v3"].tolist()
    assert c["frozen_sha256"] == mrun.frozen_artifacts_sha256(c["model"], c["comparators"], c["thresholds"],
                                                              c["cold_c"], c["reference"])
    assert c["digest"] == mrun.frozen_digest(c["frozen_sha256"])
    assert set(c["frozen_sha256"]["comparators"]) == {"v1_noon", "v2_midnight", "v2_noon"}
    c25 = fr.frozen_chain_v3(chain.frozen_2025, chain.validation, 2025)
    assert c25["thresholds"] == THRESHOLDS["2025"] and c25["digest"] != c["digest"]


@pytest.mark.parametrize("case", ["other_year", "other_validation", "not_v3", "absent"])
def test_the_loader_refuses_another_year_lineage_or_version(chain, case):
    c = client()
    args = {"other_year": (chain.frozen_2024, chain.validation, 2025),
            "other_validation": (chain.frozen_2024, chain.dq, 2024),
            "not_v3": (chain.frozen_2024, chain.validation, 2024),
            "absent": ("9" * 32, chain.validation, 2024)}[case]
    if case == "not_v3":
        c.set_tag(chain.frozen_2024, "version", "v2")
    match = {"other_year": "was frozen for '2024', not 2025", "other_validation": "was built on validation run",
             "not_v3": "not a v3 frozen run", "absent": "not in the run store"}[case]
    with pytest.raises(FreezeRecordError, match=match):
        fr.frozen_chain_v3(*args)


def test_v2_s_comparators_are_read_unless_v3_s_are_asked_for(chain):
    """freeze.load_comparators: with no keys, v2's two comparison models (its behaviour unchanged), so a v3 frozen run
    is refused; with v3's keys, its three, each checked against v3's registry."""
    from valiwatt.forecast.freeze import FrozenModelError, load_comparators
    from valiwatt.forecast.model import V3_COMPARATORS

    run = client().get_run(chain.frozen_2025)
    with pytest.raises(FrozenModelError, match=r"the comparison models are \['forecast', 'persistence'\]"):
        load_comparators(run)
    assert sorted(load_comparators(run, keys=tuple(V3_COMPARATORS.values()))) == ["v1_noon", "v2_midnight", "v2_noon"]
    with pytest.raises(FrozenModelError, match=r"the comparison models are \['v1_noon', 'v2_noon'\]"):
        load_comparators(run, keys=("v2_noon", "v1_noon"))


def _interval(m, **change):
    return dataclasses.replace(m, interval={**m.interval, **change})


@pytest.mark.parametrize("case", ["production", "v2_noon", "v1_noon", "v2_midnight", "bin_edge", "half_width",
                                  "thresholds", "cold_c", "reference"])
def test_the_one_artifact_hash_covers_each_artifact(case):
    """Section 10.3: the one hash of the artifacts that judge 2025 covers v3, the three comparison models, the bin
    edges and half-widths (inside each model's hash), the thresholds, cold_c and the reference: changing any one
    changes the digest, and the differing item is named."""
    w25, w24 = frozen_models()[2025]["models"], frozen_models()[2024]["models"]
    reference = pd.DataFrame({"day": pd.to_datetime(["2024-12-30", "2024-12-31"]), "forecast": [21000.0, 22000.0]})
    base = (w25["v3"], {k: w25[k] for k in ("v2_noon", "v1_noon", "v2_midnight")}, THRESHOLDS["2025"], COLD_C,
            reference)
    model, comparators, thresholds, cold_c, ref = base
    comparators = dict(comparators)
    if case == "production":
        model = w24["v3"]
    elif case in ("v2_noon", "v1_noon"):
        comparators[case] = w24[case]  # calibrated on other rows: other half-widths
    elif case == "v2_midnight":  # no interval, and its fit is the same for both years: one coefficient moved
        comparators[case] = dataclasses.replace(w25[case], intercept=w25[case].intercept + 1e-6)
    elif case == "bin_edge":
        model = _interval(model, bin_edge=model.interval["bin_edge"] + 0.5)
    elif case == "half_width":
        model = _interval(model, q_rest=model.interval["q_rest"] + 1e-4)
    elif case == "thresholds":
        thresholds = json.loads(json.dumps(thresholds))
        thresholds["drift"]["temperature"] += 1e-9
    elif case == "cold_c":
        cold_c = cold_c + 1e-9
    else:
        ref = ref.assign(forecast=ref["forecast"] + [0.0, 1e-6])
    was = mrun.frozen_artifacts_sha256(*base)
    now = mrun.frozen_artifacts_sha256(model, comparators, thresholds, cold_c, ref)
    named = {"bin_edge": "production", "half_width": "production"}.get(case, case)
    named = f"comparators.{named}" if named in ("v2_noon", "v1_noon", "v2_midnight") else named
    assert mrun.frozen_digest(now) != mrun.frozen_digest(was)
    assert mrun._differences(now, was) == [named]


# ---------------------------------------------------------------------------------------------- the writer


def test_a_clean_lineage_writes_the_record_and_reads_back(chain):
    """The control: on the clean chain the record is written in its fixed shape, names every run with its commit,
    clean tree, tag, end time and the first pin's rules, the 2025 model and the one hash of its artifacts; committed
    alone, it reads back as written."""
    from valiwatt.monitor.rules import rules_sha256

    path = fr.write_freeze_record(chain.rehearsal)
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert text == fr.dumps(data) and fr.check_record(data) is data
    assert (data["freeze_tag"], data["first_pin"], data["rules_sha256"]) == (V3_FREEZE_TAG, chain.f0,
                                                                            rules_sha256(chain.f0))
    ids = {"data_quality": chain.dq, "validation": chain.validation, "frozen_2024": chain.frozen_2024,
           "rehearsal_2024": chain.rehearsal, "frozen_2025": chain.frozen_2025}
    assert {role: r["run_id"] for role, r in data["runs"].items()} == ids
    head = git(chain.repo, "rev-parse", "HEAD")
    for role, r in data["runs"].items():
        run = client().get_run(r["run_id"])
        assert (r["git_commit"], r["git_dirty"], r["ended_utc"]) == (head, False, mrun._utc(run.info.end_time)), role
    assert data["runs"]["data_quality"]["hq_sha256"] == HQ
    c25 = fr.frozen_chain_v3(chain.frozen_2025, chain.validation, 2025)
    assert (data["model_sha256"], data["frozen_sha256"], data["frozen_items"]) == (
        c25["model"].sha256(), c25["digest"], c25["frozen_sha256"])
    added = commit(chain.repo, message="the freeze record", date="2027-01-05T12:00:00+00:00")
    rec = fr.read_freeze_record()
    assert (rec["commit"], rec["data"], rec["committer_utc"]) == (added, data, pd.Timestamp("2027-01-05T12:00Z"))
    assert rec["sha256"] == __import__("hashlib").sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("case", ["no_tag", "no_pin", "rules_changed"])
def test_the_writer_refuses_without_the_tag_or_its_pin(chain, case):
    if case == "no_tag":
        git(chain.repo, "tag", "-d", V3_FREEZE_TAG)
        match = "no `v3-rules-frozen` tag"
    elif case == "no_pin":
        git(chain.repo, "reset", "--quiet", "--hard", chain.f0)  # the pin commit dropped: the tag never pinned
        match = "not pinned yet"
    else:
        commit(chain.repo, {RULE_FILE: "MIN = 5\n"})
        match = "rule files differ"
    with pytest.raises(FreezeRecordError, match=match):
        fr.write_freeze_record(chain.rehearsal)
    assert not (chain.repo / fr.PATH).exists()


@pytest.mark.parametrize("case", ["file", "commit", "branch", "other_ref"])
def test_the_writer_writes_once(chain, case):
    """Written once: refused when the file is in the working tree, when any commit of the history added it, even one
    whose file was deleted since, or when a commit kept by another branch, or by a ref outside heads, remotes and tags
    (refs/keep/r), added it (the head reset to before it)."""
    path = chain.repo / fr.PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    if case == "commit":
        added = commit(chain.repo, message="a record")
        path.unlink()
        commit(chain.repo, message="removed")
        match = rf"commits \['{added[:12]}'\] of the repository's refs add docs/freeze-v3.json"
    elif case in ("branch", "other_ref"):
        added = commit(chain.repo, message="a record")
        git(chain.repo, *(["branch", "keep"] if case == "branch" else ["update-ref", "refs/keep/r", added]))
        git(chain.repo, "reset", "--quiet", "--hard", f"{added}^")
        assert not path.exists() and fr.touching_commits(added) == []
        match = rf"commits \['{added[:12]}'\] of the repository's refs add docs/freeze-v3.json"
    else:
        match = "exists: the freeze record is written once"
    with pytest.raises(FreezeRecordError, match=match):
        fr.write_freeze_record(chain.rehearsal)


def test_the_writer_creates_the_file_exclusively(chain, monkeypatch):
    """The record is created with open mode "x": a file another writer created after the first check, while this one
    derived its record, is refused and left as that writer wrote it, never replaced."""
    path = chain.repo / fr.PATH
    dumps = fr.dumps

    def raced(data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("another writer's record\n", encoding="utf-8")
        return dumps(data)

    monkeypatch.setattr(fr, "dumps", raced)
    with pytest.raises(FreezeRecordError, match="was created while the record was derived"):
        fr.write_freeze_record(chain.rehearsal)
    assert path.read_text(encoding="utf-8") == "another writer's record\n"


@pytest.mark.parametrize("case", ["hq_file", "hq_manifest", "dq_2025"])
def test_the_writer_refuses_once_2025_is_on_the_machine(chain, monkeypatch, tmp_path, case):
    """Section 10.3, "before any 2025 record reaches the machine": a Hydro-Québec 2025 file or manifest on disk, or a
    v3 data-quality run through 2025 in the store, whatever its status, refuses the record."""
    from valiwatt.sources import hydroquebec

    if case == "dq_2025":
        bad = log_dq_v3(chain.repo, through=2025)
        client().set_terminated(bad, "FAILED")
        match = "data-quality run through 2025"
    else:
        f = tmp_path / f"{case}.json"
        f.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(hydroquebec, "csv_path" if case == "hq_file" else "manifest_path",
                            lambda through=2024: f if through == 2025 else tmp_path / "absent")
        match = "2025 is on the machine"
    with pytest.raises(FreezeRecordError, match=match):
        fr.write_freeze_record(chain.rehearsal)


@pytest.mark.parametrize("case", ["dirty", "rules", "pre_freeze", "unfinished", "off_history"])
def test_the_writer_refuses_a_named_run_from_a_dirty_tree_other_rules_or_before_the_freeze(chain, case):
    """Each named run must be finished and complete, under `v3-rules-frozen` (a pre-freeze run never), from a clean
    tree, on the rules at the first pin, from a commit between the first pin and HEAD."""
    c = client()
    role, run_id = {"dirty": ("frozen_2025", chain.frozen_2025), "rules": ("validation", chain.validation),
                    "pre_freeze": ("frozen_2024", chain.frozen_2024), "unfinished": ("rehearsal_2024", chain.rehearsal),
                    "off_history": ("data_quality", chain.dq)}[case]
    if case == "dirty":
        c.set_tag(run_id, "git_dirty", "true")
    elif case == "rules":
        c.set_tag(run_id, "rules_sha256", "0" * 64)
    elif case == "pre_freeze":
        c.set_tag(run_id, "freeze_tag", PRE)
    elif case == "unfinished":
        c.set_tag(run_id, "status", "started")
    else:
        head = git(chain.repo, "rev-parse", "HEAD")
        side = commit(chain.repo, {"README.md": "side\n"})
        git(chain.repo, "reset", "--quiet", "--hard", head)
        c.set_tag(run_id, "git_commit", side)
    match = {"dirty": "not clean", "rules": "under other rules", "pre_freeze": "ran under the freeze tag 'v3-pre-freeze'",
             "unfinished": "not finished and complete", "off_history": "not between the first pin"}[case]
    with pytest.raises(FreezeRecordError, match=rf"{role} run {run_id}.*{match}"):
        fr.write_freeze_record(chain.rehearsal)


@pytest.mark.parametrize("case", ["rehearsal_kind", "rehearsal_year", "validation_kind", "dq_kind", "dq_gates",
                                  "dq_through", "frozen_version"])
def test_the_writer_refuses_a_run_of_another_kind(chain, case):
    c = client()
    run_id, key, value = {"rehearsal_kind": (chain.rehearsal, "kind", "replay"),
                          "rehearsal_year": (chain.rehearsal, "year", "2025"),
                          "validation_kind": (chain.validation, "kind", "validation"),
                          "dq_kind": (chain.dq, "kind", "dq"), "dq_gates": (chain.dq, "gates", "failed"),
                          "dq_through": (chain.dq, None, None),
                          "frozen_version": (chain.frozen_2024, "version", "v2")}[case]
    if key is None:
        import sqlite3

        con = sqlite3.connect(chain.store / "mlflow.db")  # a param cannot be rewritten through the client
        con.execute("update params set value = '2023' where run_uuid = ? and key = 'through'", (run_id,))
        con.commit()
        con.close()
        match = "reads the window through '2023'"
    else:
        c.set_tag(run_id, key, value)
        match = f"its {key} is '{value}'"
    with pytest.raises(FreezeRecordError, match=match):
        fr.write_freeze_record(chain.rehearsal)


@pytest.mark.parametrize("case", ["byte_copies", "fresh_clone", "reproduction"])
def test_the_writer_refuses_a_lineage_not_passed(chain, case):
    """D11, the slice-3 forward note: a data-quality run whose byte copies were not checked, a run labelled a fresh
    clone, or a validation or frozen run whose reproduction did not pass gets no freeze record."""
    c = client()
    run_id, value = {"byte_copies": (chain.dq, "not_run"), "fresh_clone": (chain.validation, "true"),
                     "reproduction": (chain.frozen_2025, "not_run")}[case]
    c.set_tag(run_id, case, value)
    with pytest.raises(FreezeRecordError, match=rf"run {run_id}: its {case} is '{value}'.*fresh clone"):
        fr.write_freeze_record(chain.rehearsal)


@pytest.mark.parametrize("case", ["frozen_2025_validation", "frozen_2025_dq", "validation_dq",
                                  "frozen_2024_validation", "frozen_2024_dq"])
def test_the_writer_refuses_runs_that_disagree_on_their_lineage(chain, case):
    """The runs named from the rehearsal must name each other: the frozen run for 2025 and the one for 2024 are built
    on the validation run and the data-quality run the rehearsal read, and the validation on that data-quality run."""
    c = client()
    role, key = case.rsplit("_", 1)
    run_id = {"frozen_2025": chain.frozen_2025, "frozen_2024": chain.frozen_2024, "validation": chain.validation}[role]
    tag = {"validation": "validation_run_id", "dq": "dq_run_id"}[key]
    c.set_tag(run_id, tag, "9" * 32)
    with pytest.raises(FreezeRecordError, match=rf"{role} run {run_id} names {tag} '9{{32}}'"):
        fr.write_freeze_record(chain.rehearsal)


@pytest.mark.parametrize("case", ["absent_run", "no_2025_model", "production_hash", "rehearsal_artifacts",
                                  "rehearsal_model"])
def test_the_writer_refuses_runs_missing_or_judged_by_other_artifacts(chain, case):
    """A named run not in the store, no model frozen for 2025, a production model whose file does not match the hash
    its run recorded, and a rehearsal judged by other artifacts, or another model, than its frozen run's."""
    from valiwatt.forecast.freeze_v3 import MODEL_NAME_V3

    c = client()
    if case == "absent_run":
        c.set_tag(chain.rehearsal, "frozen_run_id", "9" * 32)
        match = "frozen_2024 run 9{32} is not in v3's run store"
    elif case == "no_2025_model":
        c.delete_registered_model_alias(MODEL_NAME_V3, "replay-2025")
        match = "no model frozen for 2025"
    elif case == "production_hash":
        c.set_tag(chain.frozen_2025, "coefficient_sha256", "0" * 64)
        match = "does not match the hash its run recorded"
    elif case == "rehearsal_artifacts":
        res = mrun._load_json_artifact(c, chain.rehearsal, "results/replay.json")
        res["frozen_sha256"]["cold_c"] = repr(COLD_C + 1)
        c.log_text(chain.rehearsal, json.dumps(res), "results/replay.json")
        match = r"judged by other artifacts.*\['cold_c'\]"
    else:
        c.set_tag(chain.rehearsal, "coefficient_sha256", "0" * 64)
        match = "names another production model"
    with pytest.raises(FreezeRecordError, match=match):
        fr.write_freeze_record(chain.rehearsal)


def test_the_record_text_passes_the_public_safety_scan_or_is_not_written(chain, monkeypatch):
    from valiwatt import safety

    monkeypatch.setattr(safety, "scan_text", lambda name, text, extra=None: [f"{name}:1: a private name"])
    with pytest.raises(FreezeRecordError, match="public-safety scan"):
        fr.write_freeze_record(chain.rehearsal)
    assert not (chain.repo / fr.PATH).exists()


# ------------------------------------------------------------------------------------------------ the shape


def _set(data: dict, path: str, value):
    out = json.loads(json.dumps(data))
    *keys, last = path.split(".")
    node = out
    for k in keys:
        node = node[k]
    node[last] = value
    return out


def _drop(data: dict, path: str):
    out = json.loads(json.dumps(data))
    *keys, last = path.split(".")
    node = out
    for k in keys:
        node = node[k]
    del node[last]
    return out


def _redigest(data: dict) -> dict:
    """The record with its frozen_sha256 made again from its items, so only the item changed is wrong."""
    return {**data, "frozen_sha256": mrun.frozen_digest(data["frozen_items"])}


SHAPE_BREAKS = {
    "extra_top": lambda d: {**d, "note": "x"},
    "missing_top": lambda d: _drop(d, "frozen_items"),
    "top_order": lambda d: dict(reversed(list(d.items()))),
    "tag": lambda d: _set(d, "freeze_tag", "rules-frozen-v2"),
    "first_pin": lambda d: _set(d, "first_pin", d["first_pin"][:12]),
    "role_missing": lambda d: _drop(d, "runs.frozen_2024"),
    "field_extra": lambda d: _set(d, "runs.validation.note", "x"),
    "dirty_text": lambda d: _set(d, "runs.data_quality.git_dirty", "false"),
    "pre_freeze": lambda d: _set(d, "runs.frozen_2025.freeze_tag", PRE),
    "run_rules": lambda d: _set(d, "runs.rehearsal_2024.rules_sha256", "0" * 64),
    "run_id": lambda d: _set(d, "runs.validation.run_id", "x" * 32),
    "ended_utc": lambda d: _set(d, "runs.frozen_2024.ended_utc", "2027-01-05T12:00:00Z"),
    "through": lambda d: _set(d, "runs.data_quality.through", 2025),
    "fresh_clone": lambda d: _set(d, "runs.validation.fresh_clone", True),
    "reproduction": lambda d: _set(d, "runs.validation.reproduction", "not_run"),
    "lineage": lambda d: _set(d, "runs.frozen_2025.validation_run_id", "9" * 32),
    "cold_c": lambda d: _redigest(_set(d, "frozen_items.cold_c", "-11.50")),  # not repr(float), digest made again
    "comparators": lambda d: _set(d, "frozen_items.comparators", {**d["frozen_items"]["comparators"],
                                                                   "forecast": "0" * 64}),
    "digest": lambda d: _set(d, "frozen_sha256", "0" * 64),
    "model": lambda d: _set(d, "model_sha256", d["runs"]["frozen_2024"]["model_sha256"]),
}


@pytest.mark.parametrize("case", sorted(SHAPE_BREAKS))
def test_the_record_shape_refuses_anything_else(record, case):
    """check_record: the fixed shape, field by field and in order, and the record agreeing with itself; the clean
    record passes (control)."""
    assert fr.check_record(json.loads(fr.dumps(record))) == record
    with pytest.raises(FreezeRecordError):
        fr.check_record(SHAPE_BREAKS[case](record))


# ------------------------------------------------------------------------------------------------ the reader


def record_problems(head: str = "HEAD") -> list[str]:
    """What is wrong with the freeze record's history up to `head` and across the repository's refs, read by the
    writer's and the reader's one definition (freeze_record.anchor): none when no commit adds it, or when exactly one
    commit adds it and no commit of `head`'s history touches it after."""
    try:
        fr.anchor(head)
    except FreezeRecordError as e:
        return [str(e)]
    return []


def test_the_freeze_record_is_added_by_one_commit_and_never_touched_again():
    """This repository's history: no commit adds docs/freeze-v3.json yet (step 5 commits it), or exactly one adds it
    and none touches it later; a record present reads as its commit holds it."""
    assert fr.paths.REPO_ROOT == REPO_ROOT
    assert record_problems() == []
    if fr.anchor():
        fr.read_freeze_record()


def test_a_second_commit_touching_the_freeze_record_fails(tmp_path, monkeypatch, record):
    """Section 12, the 2025 gate: one adding commit passes the history check and reads (control); a second commit
    touching the file, even one that writes the same record again after an edit, fails both."""
    s = scratch_store(tmp_path, monkeypatch)
    path = s.repo / fr.PATH
    added = commit(s.repo, {fr.PATH: fr.dumps(record)}, "the freeze record")
    assert record_problems() == [] and fr.read_freeze_record()["data"] == record
    second = commit(s.repo, {fr.PATH: fr.dumps(record).replace("\n", "\n ", 1)}, "touched")
    touched = f"commits {[second[:12]]} touch {fr.PATH} after {added[:12]} added it"
    assert record_problems() == [f"{touched}: it is written once, by one commit"]
    with pytest.raises(FreezeRecordError, match=re.escape(touched)):
        fr.read_freeze_record()
    assert path.exists()


def test_a_record_deleted_and_added_again_fails(tmp_path, monkeypatch, record):
    """A deletion touches the record, and a re-addition is a second addition: the record is then added by another
    commit than its first, and the reader refuses (as the anchor, and on the deletion alone before the re-addition)."""
    from valiwatt.freeze_record import FreezeRecordAnchorError

    s = scratch_store(tmp_path, monkeypatch)
    added = commit(s.repo, {fr.PATH: fr.dumps(record)}, "added")
    (s.repo / fr.PATH).unlink()
    removed = commit(s.repo, message="removed")
    with pytest.raises(FreezeRecordError, match=re.escape(f"commits {[removed[:12]]} touch {fr.PATH} after "
                                                          f"{added[:12]} added it")):
        fr.read_freeze_record()
    again = commit(s.repo, {fr.PATH: fr.dumps(record)}, "added again")
    with pytest.raises(FreezeRecordAnchorError, match=re.escape(f"2 commits across the repository's refs add {fr.PATH} "
                                                                f"({[added[:12], again[:12]]})")):
        fr.read_freeze_record()


@pytest.mark.parametrize("case", ["plain", "changed", "dropped"])
def test_a_plain_merge_carrying_the_record_adds_nothing(tmp_path, monkeypatch, record, case):
    """Section 12, the freeze record: R committed on branch `a`; branch `b`, forked before it, without it; `a` merged
    into `b`. A plain merge (its tree holds R as `a` holds it) neither adds the record nor touches it: one adding
    commit across the refs, R, and the reader reads R (control), although git lists the merge in the record's history
    (one of its sides lacks it). A merge whose result changes the record's bytes (changed), or drops it (dropped),
    touches it, and the reader refuses, naming the merge."""
    s = scratch_store(tmp_path, monkeypatch)
    text = fr.dumps(record)
    fork = git(s.repo, "rev-parse", "HEAD")
    git(s.repo, "checkout", "-q", "-b", "a")
    r = commit(s.repo, {fr.PATH: text}, "the freeze record")
    git(s.repo, "checkout", "-q", "-b", "b", fork)
    commit(s.repo, {"README.md": "b's own\n"}, "b's own commit")
    git(s.repo, "merge", "--no-ff", "--no-commit", "a")
    if case == "changed":
        (s.repo / fr.PATH).write_text(text.replace("\n", "\n ", 1), encoding="utf-8")
    elif case == "dropped":
        (s.repo / fr.PATH).unlink()
    merge = commit(s.repo, message="a merged into b")
    assert git(s.repo, "rev-parse", f"{merge}^2") == r
    assert merge in [c for c, _ in fr._history("HEAD")]  # listed: one of its sides lacks the record
    assert fr.adding_commits() == [r]
    if case == "plain":
        assert fr.touching_commits(r) == [] and record_problems() == []
        rec = fr.read_freeze_record()
        assert (rec["commit"], rec["data"]) == (r, record)
        return
    touched = f"commits {[merge[:12]]} touch {fr.PATH} after {r[:12]} added it"
    assert fr.touching_commits(r) == [merge] and record_problems() == [f"{touched}: it is written once, by one commit"]
    with pytest.raises(FreezeRecordError, match=re.escape(touched)):
        fr.read_freeze_record()


@pytest.mark.parametrize("case", ["sibling", "off_head", "tag", "remote", "keep_ref", "stash_ref", "unreferenced"])
def test_the_record_s_anchor_is_its_one_addition_across_the_repository_s_refs(tmp_path, monkeypatch, record, case):
    """The record's anchor: the one commit of HEAD's history and every ref (`--all`) that adds it. Record R committed
    and kept by a branch (or a tag, a remote-tracking ref, a ref of another namespace, refs/keep/r, or refs/stash of
    the scratch repository); the working branch reset to R's parent: a second record R' written there and committed
    (the same bytes) is refused (sibling). Before R', a head whose history holds no record while a ref holds R reads R
    through its anchor (off_head: the 2025 gate makes that an (a) cause). The control: R kept by no ref (reachable from
    the reflog only), R' reads as the record; the anchor reads refs, never the reflog."""
    from valiwatt.freeze_record import FreezeRecordAnchorError

    s = scratch_store(tmp_path, monkeypatch)
    text = fr.dumps(record)
    r = commit(s.repo, {fr.PATH: text}, "the freeze record")
    keep = {"sibling": ["branch", "keep"], "off_head": ["branch", "keep"], "tag": ["tag", "keep"],
            "remote": ["update-ref", "refs/remotes/origin/v3"], "keep_ref": ["update-ref", "refs/keep/r"],
            "stash_ref": ["update-ref", "refs/stash"], "unreferenced": None}[case]
    if keep:
        git(s.repo, *keep, r)
    git(s.repo, "reset", "--quiet", "--hard", f"{r}^")
    if case == "off_head":
        assert fr.touching_commits(r) == [] and fr.anchor() == r
        rec = fr.read_freeze_record()
        assert (rec["commit"], rec["data"]) == (r, record)
        return
    again = commit(s.repo, {fr.PATH: text}, "the freeze record, again")
    if case == "unreferenced":
        assert fr.adding_commits() == [again] and fr.read_freeze_record()["commit"] == again
        return
    assert fr.touching_commits(again) == [] and fr.adding_commits() == [r, again]
    with pytest.raises(FreezeRecordAnchorError, match=r"2 commits across the repository's refs add docs/freeze-v3.json"):
        fr.read_freeze_record()


def test_the_record_is_read_as_the_commit_that_added_it_holds_it(tmp_path, monkeypatch, record):
    """Section 12, the freeze record: an edited working copy, or a deleted one, changes nothing the reader returns;
    the committed bytes read back (control); read up to a head before it, the record is still its adding commit's (the
    2025 gate makes that head's start an (a) cause); with no ref holding it, there is no record."""
    s = scratch_store(tmp_path, monkeypatch)
    text = fr.dumps(record)
    added = commit(s.repo, {fr.PATH: text}, "the freeze record")
    first = fr.read_freeze_record()
    assert first["commit"] == added and first["data"] == record
    edited = _set(record, "runs.frozen_2025.model_sha256", "0" * 64)
    (s.repo / fr.PATH).write_text(fr.dumps(edited), encoding="utf-8")
    assert fr.read_freeze_record() == first
    (s.repo / fr.PATH).unlink()
    assert fr.read_freeze_record() == first
    before = git(s.repo, "rev-parse", f"{added}^")
    assert fr.read_freeze_record(head=before) == first  # the record is in the repository, after this head
    git(s.repo, "reset", "--quiet", "--hard", before)
    with pytest.raises(FreezeRecordError, match="refs adds docs/freeze-v3.json: commit the freeze record first"):
        fr.read_freeze_record()  # no ref holds it: no record at all
