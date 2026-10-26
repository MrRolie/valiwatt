"""`valiwatt evidence-v3` (docs/plan-v3.md, section 11.3 (3); monitor/run_v3.py `run_evidence_v3`): the 2024 rehearsal
the freeze record names, run again on the newest export bounded to 2024, with that rehearsal's frozen artifacts,
compared hour by hour with it. It decides nothing: a run of kind evidence_v3, never a start, never a ledger entry.

On the gate base of tests/v3_world.py, whose export through 2025 revises one 2024 record (REVISED_UTC)."""

import dataclasses

import pandas as pd
import pytest

import v3_world as vw
from replay_world import artifact_file
from valiwatt import records_v3, tracking
from valiwatt.forecast.freeze import FrozenModel
from valiwatt.monitor import run as mrun
from valiwatt.monitor import run_v3
from valiwatt.monitor.run import FreezeError


@pytest.fixture
def chain(tmp_path, monkeypatch):
    return vw.chain_copy(tmp_path, monkeypatch, gate=True)


def test_the_evidence_reruns_the_recorded_rehearsal_and_lists_each_differing_hour(chain):
    """The evidence run reads the record's rehearsal and the gate's data-quality run, drops every record and run point
    past 2024 (counted), and lists the hours the revised record changes, the revised hour among them; it is no start
    and no ledger entry."""
    from valiwatt.report import ledger_v3

    assert run_v3.run_evidence_v3() == 0
    (run,) = _runs(run_v3.EVIDENCE_KIND)
    t, p = run.data.tags, run.data.params
    assert (t["status"], t["rehearsal_run_id"], t["dq_run_id"]) == ("complete", chain.rehearsal, chain.dq25)
    assert t["freeze_record_commit"] == chain.record
    assert int(p["records_after_2024_dropped"]) > 0 and int(p["forecast_points_after_2024_dropped"]) > 0
    c = tracking.client()
    summary = mrun._load_json_artifact(c, run.info.run_id, "results/evidence.json")
    assert summary["revised_records"] == 1 and summary["hours_differing"] > 0
    assert "target" in summary["differing"]
    cells = pd.read_parquet(artifact_file(run.info.run_id, "frames/differences.parquet"))
    assert str(vw.REVISED_UTC) in set(cells.loc[cells["column"] == "target", "utc"])
    assert records_v3.replay_starts_v3(2025) == [] and [e["run_id"] for e in ledger_v3.from_store()] == [
        chain.rehearsal]


def _runs(kind: str) -> list:
    from mlflow.entities import ViewType

    c = tracking.client()
    exp = c.get_experiment_by_name(mrun.EXPERIMENT)
    return c.search_runs([exp.experiment_id], f"tags.kind = '{kind}'", run_view_type=ViewType.ALL)


def test_the_evidence_refuses_artifacts_other_than_the_recorded_rehearsal_s(chain):
    """A comparison model of the frozen 2024 run changed in the store, with the hash its run recorded: what the
    rehearsal was judged by is no longer what the freeze record names for it, and no evidence run opens."""
    f = artifact_file(chain.frozen_2024, "comparators/v2_noon.json")
    m = FrozenModel.from_json(f.read_text(encoding="utf-8"))
    changed = dataclasses.replace(m, intercept=m.intercept + 1e-6)
    f.write_text(changed.to_json(), encoding="utf-8")
    tracking.client().set_tag(chain.frozen_2024, "comparator_sha256.v2_noon", changed.sha256())
    with pytest.raises(FreezeError, match="the rehearsal's frozen artifacts, read now, are not those the freeze record"):
        run_v3.run_evidence_v3()
    assert _runs(run_v3.EVIDENCE_KIND) == []


@pytest.mark.parametrize("case", ["dirty", "no_hourly"])
def test_the_evidence_refuses_a_dirty_tree_or_a_rehearsal_without_its_hours_before_it_opens_a_run(chain, case):
    """The evidence is made from a clean tree, so the commit it names is the code it ran; and it compares with the
    hours the recorded rehearsal logged, so a rehearsal run holding no frames/hourly.parquet refuses. Either way no
    evidence run opens."""
    if case == "dirty":
        (chain.repo / "README.md").write_text("dirty\n", encoding="utf-8")
        match = "the tree holds 1 uncommitted changes"
    else:
        artifact_file(chain.rehearsal, "frames/hourly.parquet").unlink()
        match = f"the freeze record's rehearsal run {chain.rehearsal} holds no frames/hourly.parquet"
    with pytest.raises(FreezeError, match=match):
        run_v3.run_evidence_v3()
    assert _runs(run_v3.EVIDENCE_KIND) == []
