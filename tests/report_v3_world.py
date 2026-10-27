"""v3's report on the synthetic chain of tests/v3_world.py: the three lineages report/validation_v3.py reads, each built
once per process in a temporary folder; each test renders on a private copy.

- of record: the gate base (the step-5 runs, the rehearsal made by run_replay_v3(2024), its ledger-v3 entry and the
  freeze record committed); `held_out_base` adds a clean 2025 start and its ledger-v3 entry;
- a fresh clone: the gate base's repository with an empty store, then the chain run again on a later vintage (a
  data-quality run labelled a fresh clone, its byte copies and reproduction not run, the validation, the freezes and
  the rehearsal by run_replay_v3(2024) itself);
- before the freeze: a repository without `v3-rules-frozen`, the runs and the rehearsal under `v3-pre-freeze`.

The chain's validation run logs only what a replay reads (the thresholds, cold_c and the frames); the report reads the
whole of results/validation.json and frames/bins.parquet, so `complete` gives the validation run a real
run_validation_v3 result on test_validate_v3's world (`full_validation`), run at the chain's cold_c (its subsets, its
sensitivities and its verdicts cut there, as one validation run cuts them) and keeping the thresholds the frozen
artifacts hash (no digest moves), with the January 2024 fold's bins row the rehearsal reads kept and filled from the
model frozen for 2024; and it gives the data-quality run the results/dq_results.json dq-v3 logs, its sources'
retrieval dates included.

Every run of the chain takes N_BOOT resamples, not the plan's 1,000, to keep the tests fast; `copy` sets
evaluate.N_BOOT to it, the count the report holds a run of record to (validation_v3.resamples).
"""

from __future__ import annotations

import json
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

import records_v3_world as rw
import v3_world as vw
from replay_world import copy_chain

# The retrieval dates of the exports, as the data-quality run's results record them: the runs of record's, and a
# fresh clone's later vintage.
RETRIEVED = {"of_record": "2026-10-14T01:02:03Z", "fresh_clone": "2027-03-02T04:05:06Z"}
MODIFIED = {"of_record": "2026-10-01T00:00:00Z", "fresh_clone": "2027-02-27T00:00:00Z"}
NOT_RUN = {"byte_copies": "not_run", "fresh_clone": "true"}
N_BOOT = 20  # the resamples of every bootstrap interval of the chain (the module's docstring)


@lru_cache(maxsize=None)
def full_validation(cold_c: float) -> dict:
    """run_validation_v3 on test_validate_v3's world (its folds, thresholds and last day), its cold tail cut at the
    chain's `cold_c` (validate.design_cold_c set to it), with the reproduction check of v2's form at midnight against
    its own forecasts (`check_reproduction`): the results as validate-v3 logs them (JSON) and the bins frame."""
    import pytest

    from test_validate import V2_FOLDS
    from test_validate_v3 import LAST_DAY, THRESHOLD_YEARS, world_tables
    from valiwatt.forecast import validate as vd
    from valiwatt.forecast import validate_v3 as v3
    from valiwatt.quality.checks import jsonable

    f_noon, f_midnight, record = world_tables()
    verdicts = {d: [] for d in pd.date_range("2019-01-01", "2021-12-31")}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(vd, "design_cold_c", lambda *a, **k: cold_c)
        v = v3.run_validation_v3(f_noon, f_midnight, day_verdicts=verdicts, forecast_record=record, n_boot=N_BOOT,
                                 first_fold=V2_FOLDS[0], last_fold=V2_FOLDS[1], last_day=LAST_DAY,
                                 threshold_years=THRESHOLD_YEARS)
    assert v["results"]["config"]["cold_c"] == v["results"]["contrasts"]["cold_c"] == cold_c
    out = v["out"]
    recorded = pd.DataFrame({"ts": out.index, "forecast": out["pred_v2_midnight"].to_numpy()})
    reproduction = v3.check_reproduction(out, recorded, days=("2020-01-01", "2021-06-30")) | {"fresh_clone": False}
    results = json.loads(json.dumps(jsonable(v["results"] | {"reproduction": reproduction}), ensure_ascii=False))
    return {"results": results, "bins": v["bins"]}


def dq_results(lineage: str) -> dict:
    """The sources block of a v3 data-quality run's results/dq_results.json, retrieved on RETRIEVED[lineage]."""
    t = pd.Timestamp(RETRIEVED[lineage])
    later = (t + pd.Timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"sources": {"hydroquebec": {"retrieved_utc": RETRIEVED[lineage], "dataset_modified": MODIFIED[lineage]},
                        "eccc": [{"key": "montreal", "climate_id": "7025251", "primary": True, "months": 72,
                                  "first_retrieved_utc": RETRIEVED[lineage], "last_retrieved_utc": later}],
                        "nbm": {"years": {str(y): {"retrieved_utc": later} for y in range(2019, 2025)}}}}


def complete(dq_run_id: str, validation_run_id: str, lineage: str = "of_record", reproduction: dict | None = None):
    """Give the chain's validation run the whole of a validation's results and bins, and its data-quality run the
    sources of its results (the module's docstring). reproduction: the check's outcome to log (a fresh clone's: not
    run)."""
    from valiwatt import tracking
    from valiwatt.monitor import run as mrun

    c = tracking.client()
    current = mrun._load_json_artifact(c, validation_run_id, "results/validation.json")
    full = full_validation(current["config"]["cold_c"])
    results = full["results"] | {"thresholds": current["thresholds"]}
    if reproduction is not None:
        results["reproduction"] = reproduction
    c.log_text(validation_run_id, json.dumps(results, indent=2, ensure_ascii=False), "results/validation.json")
    layer = vw.frozen(2024).model.interval
    january = {"model": "v3", "fold": "2024-01", "edge_c": float(layer["bin_edge"]), "q_cold": float(layer["q_cold"]),
               "q_rest": float(layer["q_rest"]), "q_pooled": float(layer["q_pooled"]),
               "cold_n": int(layer["cold_n"]), "cold_weeks": int(layer["cold_weeks"]),
               "fallback": bool(layer["fallback"]), "calibration_n": int(layer["cold_n"])}
    bins = pd.concat([full["bins"], pd.DataFrame([january])], ignore_index=True)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bins.parquet"
        bins.to_parquet(path)
        c.log_artifact(validation_run_id, str(path), "frames")
    c.log_text(dq_run_id, json.dumps(dq_results(lineage)), "results/dq_results.json")


def _base(name: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"valiwatt-report-v3-{name}-"))
    import atexit

    atexit.register(shutil.rmtree, root, True)
    return root / "chain"


@lru_cache(maxsize=1)
def of_record_base() -> tuple[Path, dict]:
    import pytest

    gate, ids = vw.gate_base()
    root = _base("of-record")
    copy_chain(gate, root)
    with pytest.MonkeyPatch.context() as mp:
        vw.install_chain(mp, root)
        complete(ids["dq24"], ids["validation"])
    return root, dict(ids)


@lru_cache(maxsize=1)
def held_out_base() -> tuple[Path, dict]:
    """The runs of record with a clean 2025 start (run_replay_v3(2025)) and ledger-v3 synced, uncommitted."""
    import pytest

    from valiwatt.monitor import run_v3
    from valiwatt.report import ledger_v3

    base, ids = of_record_base()
    root = _base("held-out")
    copy_chain(base, root)
    with pytest.MonkeyPatch.context() as mp:
        vw.install_chain(mp, root)
        assert run_v3.run_replay_v3(2025, n_boot=N_BOOT) == 0
        (run,) = vw.replay_runs_v3(2025)
        ledger_v3.sync()
    return root, dict(ids, held_out=run.info.run_id)


@lru_cache(maxsize=1)
def fresh_clone_base() -> tuple[Path, dict]:
    """The gate base's repository (the freeze record and ledger-v3 committed) with an empty store, the chain run again
    there as a fresh clone runs it."""
    import pytest

    from valiwatt.monitor import run_v3

    gate, ids = vw.gate_base()
    root = _base("fresh-clone")
    copy_chain(gate, root)
    shutil.rmtree(root / "mlruns")
    with pytest.MonkeyPatch.context() as mp:
        vw.install_chain(mp, root)
        dq = rw.log_dq_v3(root / "repo", 2024, **NOT_RUN)
        val = vw.log_validation(dq, reproduction="not_run", **NOT_RUN)
        f24 = vw.log_frozen(2024, val, dq, reproduction="not_run", **NOT_RUN)
        f25 = vw.log_frozen(2025, val, dq, reproduction="not_run", **NOT_RUN)
        assert run_v3.run_replay_v3(2024, n_boot=N_BOOT) == 0
        (rerun,) = vw.replay_runs_v3(2024)
        complete(dq, val, "fresh_clone", reproduction={  # as validate-v3 logs it on a fresh clone
            "status": "not_run", "fresh_clone": True,
            "why": "a fresh clone: its data-quality run is labelled so, and no read of v2's store exists"})
    recorded = {k: ids[k] for k in ("dq24", "validation", "frozen_2024", "frozen_2025", "rehearsal")}
    return root, {"recorded": recorded, "dq": dq, "validation": val, "frozen_2024": f24, "frozen_2025": f25,
                  "rehearsal": rerun.info.run_id}


@lru_cache(maxsize=1)
def pre_freeze_base() -> tuple[Path, dict]:
    """A repository without `v3-rules-frozen` (records_v3_world.make_repo(frozen=False)), `v3-first-run` on F0, and
    the chain under `v3-pre-freeze`: data quality, validation, both freezes and the rehearsal."""
    import pytest

    from valiwatt.config import V3_FIRST_RUN_TAG, V3_PRE_FREEZE_TAG
    from valiwatt.monitor import run_v3

    root = _base("pre-freeze")
    repo = root / "repo"
    f0 = rw.make_repo(repo, frozen=False)["f0"]
    rw.tag(repo, V3_FIRST_RUN_TAG, f0)
    rw.commit(repo, {"docs/replay-ledger.json": vw.v2_ledger_text(vw.v2_2024_entry())}, "v2's ledger")
    with pytest.MonkeyPatch.context() as mp:
        vw.install_chain(mp, root)
        dq = rw.log_dq_v3(repo, 2024, freeze_tag=V3_PRE_FREEZE_TAG)
        val = vw.log_validation(dq)
        f24 = vw.log_frozen(2024, val, dq)
        f25 = vw.log_frozen(2025, val, dq)
        assert run_v3.run_replay_v3(2024, n_boot=N_BOOT) == 0
        (rehearsal,) = vw.replay_runs_v3(2024)
        complete(dq, val)
    return root, {"f0": f0, "dq": dq, "validation": val, "frozen_2024": f24, "frozen_2025": f25,
                  "rehearsal": rehearsal.info.run_id}


BASES = {"of_record": of_record_base, "held_out": held_out_base, "fresh_clone": fresh_clone_base,
         "pre_freeze": pre_freeze_base}


def copy(name: str, tmp_path: Path, mp) -> SimpleNamespace:
    """A private copy of the base `name` (BASES) under `tmp_path`, the code pointed at it."""
    from valiwatt.forecast import evaluate as ev

    base, ids = BASES[name]()
    root = Path(tmp_path) / "chain"
    copy_chain(base, root)
    vw.install_chain(mp, root)
    mp.setattr(ev, "N_BOOT", N_BOOT)
    return SimpleNamespace(root=root, repo=root / "repo", store=root / "mlruns", ids=ids)


def render(chain) -> dict:
    """render_v3 on the copy: {lang: page text} and the stamp, as written under the copy's docs/report/v3/."""
    from valiwatt.report.validation_v3 import render_v3

    written = render_v3()
    out = chain.repo / "docs" / "report" / "v3"
    assert written == [out / "validation.fr.md", out / "validation.en.md"]
    return {"fr": written[0].read_text(encoding="utf-8"), "en": written[1].read_text(encoding="utf-8"),
            "stamp": json.loads((out / "stamp.json").read_text(encoding="utf-8"))}
