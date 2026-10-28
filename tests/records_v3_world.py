"""Synthetic v3 records for the record tests (and the replay builder's): scratch repositories carrying v3's tags and
their pins, and a v3 run store holding the runs the freeze record names, logged with the tags v3's commands give them
(docs/plan-v3.md, sections 10.2, 10.3 and 17 step 5).

- `install(mp, repo, store)` points every module that reads the repository root or the run store at the scratch ones:
  rules, monitor.run, tracking, firstrun and paths for the root; tracking and paths for the store. The new modules
  (records_v3, freeze_record, report/ledger_v3) read paths.REPO_ROOT at call time.
- `make_repo` builds a scratch repository: a rule file, a rendering file, the dependencies, AGENTS.md with v2's and
  v3's departures sections, tests/test_tags.py with its `PINS` literal; `tag` and `pin` add a v3 tag and its pin.
- `fake_run` logs a run with exactly the tags, parameters and text artifacts given; `git_tags` gives the code tags a
  run of the scratch repository's HEAD carries.
- `record_chain_base` builds once per process the chain the freeze record is written from: a scratch repository with
  `v3-rules-frozen` on F0, pinned; a v3 data-quality run through 2024; a validation run; the models frozen for 2024 and
  2025 (logged by freeze.log_frozen under v3's registered model); the 2024 rehearsal, a `replay_v3` run whose
  results/replay.json holds the hashes of what judged it. `record_chain` gives each test a private copy.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from replay_world import copy_chain
from valiwatt.config import FREEZES, V3_FREEZE_TAG, V3_PRE_FREEZE_TAG

V2_HEADING = FREEZES["rules-frozen-v2"]["heading"]
V3_HEADING = FREEZES[V3_FREEZE_TAG]["heading"]
SCRATCH_ID = ("-c", "user.name=scratch", "-c", "user.email=scratch@example.invalid", "-c", "commit.gpgsign=false",
              "-c", "tag.gpgsign=false")
RULE_FILE = "src/valiwatt/monitor/rules.py"
PINS_FILE = "tests/test_tags.py"
NONE_YET = "None yet.\n"
HQ, ECCC, NBM = "1" * 64, "2" * 64, "3" * 64  # the data-quality run's raw-file hashes
# The thresholds and cold_c the chain's validation run gives each replayed year (their values do not matter here,
# only that they are hashed).
THRESHOLDS = {"2024": {"performance": {"winter": {"mape": 0.05}}, "drift": {"temperature": 1.0}},
              "2025": {"performance": {"winter": {"mape": 0.06}}, "drift": {"temperature": 1.1}}}
COLD_C = -11.5


# --------------------------------------------------------------------------------------- repositories and roots

def install(mp, repo: Path, store: Path) -> None:
    """Point the code at the scratch repository `repo` and the run store `store`, MLflow's cache of artifact
    repositories emptied."""
    from valiwatt import firstrun, paths, tracking
    from valiwatt.monitor import rules
    from valiwatt.monitor import run as mrun

    from mlflow.tracking._tracking_service import utils as mlflow_utils

    for module in (rules, mrun, tracking, firstrun, paths):
        mp.setattr(module, "REPO_ROOT", Path(repo))
    mp.setattr(tracking, "MLRUNS", Path(store))
    mp.setattr(paths, "MLRUNS", Path(store))
    # MLflow caches each run's artifact repository by run ID for the process: a copy of a store holds the same run IDs
    # at other paths, so the cache is emptied whenever the store changes, or a copy's artifacts would be the
    # original's.
    mlflow_utils._artifact_repos_cache.clear()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *SCRATCH_ID, *args], cwd=repo, capture_output=True, text=True,
                          check=True).stdout.strip()


def commit(repo: Path, files: dict[str, str] | None = None, message: str = "scratch", empty: bool = False,
           date: str | None = None) -> str:
    """A commit writing `files` (path -> text); date: its committer and author date (ISO), for the 2025 time."""
    import os

    for path, text in (files or {}).items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    env = {**os.environ, **({"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else {})}
    subprocess.run(["git", *SCRATCH_ID, "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", *SCRATCH_ID, "commit", "--quiet", "--no-verify", *(["--allow-empty"] if empty else []),
                    "-m", message], cwd=repo, check=True, capture_output=True, env=env)
    return git(repo, "rev-parse", "HEAD")


def forget_reflogs(repo: Path) -> None:
    """Every reflog of the scratch repository emptied (`git reflog expire --expire=all --all`): a commit a reset or an
    amend left behind is then kept by nothing the repository records, as after the reflogs expired."""
    git(repo, "reflog", "expire", "--expire=all", "--all")


def agents(*v3_entries: str, v2_entries: tuple[str, ...] = ()) -> str:
    """The scratch AGENTS.md: v2's departures section, then v3's, each holding its entries (none: "None yet.")."""
    return (f"# scratch\n\n{V2_HEADING}\n\nThe v2 departures.\n\n" + ("".join(v2_entries) or NONE_YET)
            + f"\n{V3_HEADING}\n\nThe v3 departures.\n\n" + ("".join(v3_entries) or NONE_YET))


def pins_text(pins: dict) -> str:
    return "PINS = {\n" + "".join(f"    {k!r}: {v!r},\n" for k, v in pins.items()) + "}\n"


def pins_of(repo: Path) -> dict:
    import ast

    text = (repo / PINS_FILE).read_text(encoding="utf-8")
    node = next(n for n in ast.parse(text).body if isinstance(n, ast.Assign) and n.targets[0].id == "PINS")
    return ast.literal_eval(node.value)


def tag(repo: Path, name: str, rev: str = "HEAD", annotated: bool = True) -> str:
    """The tag `name` (re)made on `rev`; returns its commit."""
    git(repo, "tag", "--force", *(["-a", "-m", "scratch"] if annotated else []), name, rev)
    return git(repo, "rev-parse", f"{rev}^{{commit}}")


def pin(repo: Path, name: str, rev: str) -> str:
    """The pin commit: tests/test_tags.py with PINS[name] set to `rev`'s commit, and only it. Returns its hash."""
    full = git(repo, "rev-parse", f"{rev}^{{commit}}")
    return commit(repo, {PINS_FILE: pins_text(pins_of(repo) | {name: full})}, "pin")


def make_repo(repo: Path, *, frozen: bool = True, pinned: bool = True) -> dict:
    """The scratch repository: F0 holds a rule file, a rendering file, the dependencies, AGENTS.md with both sections
    and tests/test_tags.py with no pin; `frozen`: v3-rules-frozen on F0; `pinned`: its pin committed next (HEAD).
    Returns {"f0", "pin" (None when not pinned)}."""
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    f0 = commit(repo, {RULE_FILE: "MIN = 4\n", "src/valiwatt/report/render.py": "# renders\n",
                       "config/stations.toml": "x = 1\n", "pyproject.toml": "[project]\nname = 'scratch'\n",
                       "uv.lock": "version = 1\n", "AGENTS.md": agents(), ".gitignore": "mlruns/\n",
                       PINS_FILE: pins_text({}), "README.md": "scratch\n"}, "rules")
    if frozen:
        tag(repo, V3_FREEZE_TAG, f0)
    return {"f0": f0, "pin": pin(repo, V3_FREEZE_TAG, f0) if frozen and pinned else None}


def git_tags(repo: Path, rev: str = "HEAD") -> dict:
    """The code tags tracking.start_run gives a run of `rev` from a clean tree."""
    return {"git_commit": git(repo, "rev-parse", rev), "git_dirty": "false", "code_sha256": "c" * 64}


# ---------------------------------------------------------------------------------------------------- runs

def fake_run(tags: dict, *, experiment: str | None = None, status: str = "FINISHED", params: dict | None = None,
             texts: dict | None = None, metrics: dict | None = None) -> str:
    """A run created by hand in the installed store, with exactly `tags`, `params`, `metrics` and text artifacts
    (`texts`: path -> text), left at `status` (RUNNING: left open). Returns its run ID."""
    from valiwatt import tracking
    from valiwatt.monitor import run as mrun

    c = tracking.client()
    run = c.create_run(tracking.experiment_id(experiment or mrun.EXPERIMENT), tags={k: str(v) for k, v in tags.items()})
    rid = run.info.run_id
    for k, v in (params or {}).items():
        c.log_param(rid, k, v)
    for k, v in (metrics or {}).items():
        c.log_metric(rid, k, v)
    for path, text in (texts or {}).items():
        c.log_text(rid, text, path)
    if status != "RUNNING":
        c.set_terminated(rid, status)
    return rid


def replay_v3_tags(repo: Path, year: int = 2024, freeze_tag: str = V3_FREEZE_TAG, **more) -> dict:
    """The tags of a finished `replay_v3` run of `year` (the slice contract, section 3.5), on the repository's HEAD,
    under `freeze_tag` (its commit the tag's; "" before it) and the rules at HEAD."""
    from valiwatt.monitor.rules import rules_sha256

    commit_ = git(repo, "rev-parse", f"refs/tags/{V3_FREEZE_TAG}^{{commit}}") if freeze_tag == V3_FREEZE_TAG else ""
    return {"kind": "replay_v3", "year": str(year), "issue": "noon", "freeze_tag": freeze_tag,
            "freeze_commit": commit_, "rules_sha256": rules_sha256(), "coefficient_sha256": "d" * 64,
            "departure": "false", "departure_from": "", "evaluation": "seen_by_developers",
            "dq_run_id": "e" * 32, "frozen_run_id": "f" * 32, "validation_run_id": "a" * 32, "status": "complete",
            "stage": "logged", **git_tags(repo), **more}


def replay_results(tags: dict, frozen_sha256: dict | None = None, *, mape: float = 0.05, coverage: float = 0.9,
                   recommendations: tuple = (), terms: str = "better", family: str = "not_shown_better") -> dict:
    """The keys of a replay_v3 run's results/replay.json that the ledger and the freeze record read."""
    from valiwatt.report.ledger import recommendation_counts

    recs = [{"week_start": d, "recommendation": r} for d, r in recommendations]
    return {"year": int(tags["year"]), "kind": "replay_v3", "freeze_tag": tags["freeze_tag"],
            "freeze_commit": tags["freeze_commit"], "rules_sha256": tags["rules_sha256"],
            "summary": {"mape": {"mean": mape}, "coverage": {"mean": coverage},
                        "recommendations": recommendation_counts(recs)},
            "decisions": recs, "verdict": {"terms": terms, "family": family, "label": "seen_by_developers"},
            "frozen_sha256": frozen_sha256 or {"production": "0" * 64, "comparators": {}, "thresholds": "0" * 64,
                                               "cold_c": repr(COLD_C), "reference": "0" * 64}}


def log_replay_v3(repo: Path, year: int = 2024, freeze_tag: str = V3_FREEZE_TAG, *, status: str = "FINISHED",
                  results: dict | None = None, **tags) -> str:
    """A `replay_v3` run as the replay logs one, by hand: its tags, its metrics and results/replay.json; a 2025 run
    under v3's tag also its gate record (results/held_out_gate.json, as run_replay_v3 logs it at its opening), naming
    its commit and freeze commit, no cause and its standing, and the marker it sets once that record is logged
    (records_v3.GATE_LOGGED)."""
    from valiwatt.monitor.run import GATE_RECORD
    from valiwatt.records_v3 import GATE_LOGGED

    t = replay_v3_tags(repo, year, freeze_tag, **tags)
    res = results or replay_results(t)
    texts = {"results/replay.json": json.dumps(res)}
    if year == 2025 and freeze_tag == V3_FREEZE_TAG:
        texts[GATE_RECORD] = json.dumps({"head_commit": t["git_commit"], "freeze_commit": t["freeze_commit"],
                                         "causes": [], "ruling": None, "standing": t.get("standing", "")})
        t = {GATE_LOGGED: "true", **t}
    return fake_run(t, status=status, metrics={"mape": res["summary"]["mape"]["mean"]}, texts=texts)


# ------------------------------------------------------------------------------------------ the frozen models

@lru_cache(maxsize=1)
def frozen_models() -> dict:
    """v3 and its three comparison models frozen on a small synthetic world (as tests/test_freeze_v3.py builds it), for
    2024 and for 2025: {year: {"models": {key: FrozenModel}, "coef": its coefficient table, "example": an input
    example, "oos": validation's out-of-sample frame}}. The two years' models are calibrated on out-of-sample rows of
    different noise, so their hashes differ; each carries its replay year."""
    from conftest import synth_demand, synth_obs, synth_runs
    from valiwatt.forecast import freeze_v3 as fv
    from valiwatt.forecast import validate as vd
    from valiwatt.forecast.features import build, scored_at
    from valiwatt.forecast.issue import NOON
    from valiwatt.forecast.model import MODELS_V3
    from valiwatt.quality.demand import build_demand

    first, last = pd.Timestamp("2019-10-01T06:00Z"), pd.Timestamp("2021-01-01T05:00Z")
    raw = synth_demand(first, last, seed=4)
    obs = synth_obs(first, last, seed=4).set_index("ts_utc")["temp_c"]
    days = pd.DatetimeIndex(raw["ts_utc"]).tz_convert(None).normalize()
    level = pd.Series(np.cumsum(np.random.default_rng(4).normal(0, 150, len(days.unique()))), index=days.unique())
    raw["mw"] = (raw["mw"] + 300 * np.maximum(18 - obs.reindex(raw["ts_utc"]).to_numpy(), 0)
                 + level.reindex(days).to_numpy())
    runs = synth_runs(first - pd.Timedelta(days=3), last, seed=4, wind=True)
    runs = runs[runs["runtime"] + pd.Timedelta(hours=2) <= last].reset_index(drop=True)
    design_days = pd.date_range("2019-10-01", "2020-12-31")
    demand = build_demand(raw, first, last).frame
    tables = {"noon": build(demand, obs, vd.forecast_inputs(runs, design_days, last=last, issue=NOON), issue=NOON),
              "midnight": build(demand, obs, vd.forecast_inputs(runs, design_days, last=last))}
    f = tables["noon"]
    rows = f[(f["day"] >= pd.Timestamp("2020-01-01")) & (f["day"] <= pd.Timestamp("2020-12-31"))]
    spec = MODELS_V3["v3"]
    example = f[["day_type", "hour", *spec.inputs]].dropna().tail(24).astype({"hour": float})
    out = {}
    for year, seed in ((2024, 5), (2025, 6)):
        oos = rows[["target", "day", "temp_forecast"]].copy()
        oos["fold"] = oos["day"].dt.to_period("M").astype(str)
        oos["scored_at"] = scored_at(oos["day"], last)
        rng = np.random.default_rng(seed)
        for i, key in enumerate(fv.FROZEN_KEYS):
            oos[f"pred_{key}"] = oos["target"] * (1 + rng.normal(0, 0.02 + 0.01 * i, len(oos)))
        fitted = {k: fv.fit_frozen_v3(fv.table_of(k, tables), oos, 2021, key=k, last_day=pd.Timestamp("2020-12-31"))
                  for k in fv.FROZEN_KEYS}
        models = {k: dataclasses.replace(m, replay_year=year) for k, (m, _) in fitted.items()}
        out[year] = {"models": models, "coef": fitted["v3"][1], "example": example, "oos": oos}
    return out


# ------------------------------------------------------------------------------------------------ the chain

def lineage_tags(**more) -> dict:
    return {"byte_copies": "passed", "fresh_clone": "false", **more}


def log_dq_v3(repo: Path, through: int = 2024, freeze_tag: str = V3_FREEZE_TAG, **tags) -> str:
    """A finished v3 data-quality run whose gates passed, as dq-v3 tags it, on the repository's HEAD."""
    from valiwatt.config import data_last
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.quality import run_v3

    commit_ = git(repo, "rev-parse", f"refs/tags/{V3_FREEZE_TAG}^{{commit}}") if freeze_tag == V3_FREEZE_TAG else ""
    t = {"kind": "dq_v3", "issue": "noon", "rules_sha256": rules_sha256(), "gates": "passed", "freeze_tag": freeze_tag,
         "freeze_commit": commit_, **lineage_tags(), **git_tags(repo), **tags}
    return fake_run(t, experiment=run_v3.EXPERIMENT,
                    params={"through": through, "window_last_utc": data_last(through).isoformat(), "hq_sha256": HQ,
                            "eccc_manifests_sha256": ECCC, "nbm_manifest_sha256": NBM,
                            "hq_retrieved_utc": "2026-11-01T00:00:00Z"})


def log_validation_v3(repo: Path, dq_run_id: str, oos: pd.DataFrame, freeze_tag: str = V3_FREEZE_TAG,
                      **tags) -> str:
    """A complete v3 validation run on `dq_run_id`: frames/oos.parquet and results/validation.json (THRESHOLDS,
    COLD_C), as validate-v3 logs them."""
    import mlflow

    from valiwatt import tracking
    from valiwatt.forecast import validate as vd
    from valiwatt.monitor.rules import rules_sha256

    t = {"kind": "validation_v3", "version": "v3", "freeze_tag": freeze_tag, "dq_run_id": dq_run_id,
         "rules_sha256": rules_sha256(), "reproduction": "passed", **lineage_tags(), **tags}
    with tracking.start_run(vd.EXPERIMENT, "validation-v3", tags=t) as run:
        with tempfile.TemporaryDirectory() as tmp:
            oos.to_parquet(Path(tmp) / "oos.parquet")
            mlflow.log_artifacts(tmp, artifact_path="frames")
        mlflow.log_text(json.dumps({"thresholds": THRESHOLDS, "config": {"cold_c": COLD_C}}),
                        "results/validation.json")
        mlflow.set_tag("status", "complete")
    return run.info.run_id


def log_frozen_v3(year: int, validation_run_id: str, dq_run_id: str, freeze_tag: str = V3_FREEZE_TAG,
                  models: dict | None = None, **tags) -> str:
    """The models frozen for `year`, logged as freeze-v3 logs them (v3's registered model, alias replay-<year>)."""
    from valiwatt.forecast import freeze as fz
    from valiwatt.forecast.freeze_v3 import MODEL_NAME_V3
    from valiwatt.monitor.rules import rules_sha256

    w = frozen_models()[year]
    models = models or w["models"]
    t = {"version": "v3", "freeze_tag": freeze_tag, "validation_run_id": validation_run_id, "dq_run_id": dq_run_id,
         "rules_sha256": rules_sha256(), "issue": "noon", "reproduction": "passed", **lineage_tags(), **tags}
    run, _ = fz.log_frozen(models["v3"], w["coef"], w["example"], t,
                           comparators={k: m for k, m in models.items() if k != "v3"}, model_name=MODEL_NAME_V3)
    return run.info.run_id


def build_record_chain(root: Path) -> dict:
    """The chain under `root` (`repo`, `mlruns`): `v3-rules-frozen` on F0, pinned at HEAD; the step-5 runs, each
    under the tag, from the clean tree at HEAD, on the first pin's rules."""
    import pytest

    from valiwatt.freeze_record import frozen_chain_v3

    w = frozen_models()
    repo, store = Path(root) / "repo", Path(root) / "mlruns"
    made = make_repo(repo)
    with pytest.MonkeyPatch.context() as mp:
        install(mp, repo, store)
        dq = log_dq_v3(repo)
        val = log_validation_v3(repo, dq, w[2024]["oos"])
        f24 = log_frozen_v3(2024, val, dq)
        f25 = log_frozen_v3(2025, val, dq)
        chain = frozen_chain_v3(f24, val, 2024)
        rehearsal = log_replay_v3(repo, 2024, coefficient_sha256=chain["model"].sha256(), dq_run_id=dq,
                                  validation_run_id=val, frozen_run_id=f24)
        from valiwatt import tracking
        from valiwatt.monitor import run as mrun

        c = tracking.client()
        res = mrun._load_json_artifact(c, rehearsal, "results/replay.json") | {"frozen_sha256": chain["frozen_sha256"]}
        c.log_text(rehearsal, json.dumps(res), "results/replay.json")
    return {**made, "dq": dq, "validation": val, "frozen_2024": f24, "frozen_2025": f25, "rehearsal": rehearsal}


@lru_cache(maxsize=1)
def record_chain_base() -> tuple[Path, dict]:
    """The chain built once per process, in a temporary folder removed at exit; each test works on a copy."""
    import atexit

    root = Path(tempfile.mkdtemp(prefix="valiwatt-records-v3-"))
    atexit.register(shutil.rmtree, root, True)
    ids = build_record_chain(root / "chain")
    return root / "chain", ids


def record_chain(tmp_path: Path, mp) -> SimpleNamespace:
    """A private copy of the chain under `tmp_path`, the code pointed at it."""
    base, ids = record_chain_base()
    root = Path(tmp_path) / "chain"
    copy_chain(base, root)
    install(mp, root / "repo", root / "mlruns")
    return SimpleNamespace(root=root, repo=root / "repo", store=root / "mlruns", **ids)


def scratch_store(tmp_path: Path, mp, **repo_kwargs) -> SimpleNamespace:
    """A scratch repository (make_repo) and an empty run store under `tmp_path`, the code pointed at them."""
    repo, store = Path(tmp_path) / "repo", Path(tmp_path) / "mlruns"
    made = make_repo(repo, **repo_kwargs)
    install(mp, repo, store)
    return SimpleNamespace(repo=repo, store=store, **made)


PRE = V3_PRE_FREEZE_TAG
