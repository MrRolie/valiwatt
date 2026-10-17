"""A small synthetic world for replay tests: two years of history, a frozen model, and thresholds derived from the
world's own validation weeks by the same quantile rule the real validation uses.

Demand follows a daily cycle, a weekly cycle, heating below 18 °C, hourly noise, and a day-level error that persists
from day to day (as weather regimes do), so a day's error is several times noisier than a week's, as in the real data.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from conftest import synth_obs, synth_runs, synth_temperature_c
from valiwatt import paths
from valiwatt.breaks import SYNTHETIC_WORLD
from valiwatt.config import DESIGN_LAST, FREEZE_TAG, LST_OFFSET, data_last
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.evaluate import cold_threshold, errors, weekly_stats
from valiwatt.forecast.features import build, forecast_temperature
from valiwatt.forecast.freeze import fit_frozen
from valiwatt.monitor.rules import MIN_WEEK_HOURS, QUANTILES
from valiwatt.quality.checks import jsonable
from valiwatt.quality.demand import build_demand
from valiwatt.sources.nbm import AVAILABILITY_LAG

FIRST, LAST = pd.Timestamp("2020-01-01T06:00Z"), pd.Timestamp("2022-04-01T05:00Z")
REPLAY_YEAR = 2022
DAY_SIGMA, DAY_PHI = 0.02, 0.6


def world(seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    obs = synth_obs(FIRST, LAST, seed=seed)
    idx = pd.DatetimeIndex(obs["ts_utc"])
    h = np.arange(len(idx))
    rng = np.random.default_rng(seed + 1)
    day = (idx - pd.Timedelta(hours=6)).normalize()
    codes, uniq = pd.factorize(day)
    eps = np.zeros(len(uniq))
    for i in range(1, len(uniq)):
        eps[i] = DAY_PHI * eps[i - 1] + rng.normal(0, DAY_SIGMA * np.sqrt(1 - DAY_PHI ** 2))
    mw = (18_000 + 2_500 * np.sin(2 * np.pi * h / 24) - 900 * (idx.dayofweek >= 5)
          + 350 * np.maximum(18 - obs["temp_c"].to_numpy(), 0) + rng.normal(0, 60, len(idx)))
    mw = mw * (1 + eps[codes])
    raw = pd.DataFrame({"ts_utc": idx, "mw": mw, "source_line": np.arange(2, len(idx) + 2)})
    return raw, obs


def respond(raw: pd.DataFrame, obs: pd.DataFrame, new_obs: pd.DataFrame) -> pd.DataFrame:
    """Demand as it would be under a changed temperature (the world's heating response, applied to the change)."""
    old = np.maximum(18 - obs["temp_c"].to_numpy(), 0)
    new = np.maximum(18 - new_obs["temp_c"].to_numpy(), 0)
    return raw.assign(mw=raw["mw"] + 350 * (new - old))


@lru_cache(maxsize=1)
def frozen_reference_thresholds():
    """The frozen model, the drift reference, and thresholds from the 2021 out-of-sample weeks (one set for every
    season: the world has no seasonal error pattern). The drift alarm is added by `thresholds()`."""
    raw, obs = world()
    f = build(build_demand(raw, FIRST, LAST).frame, obs.set_index("ts_utc")["temp_c"])
    oos = wf.run(f, wf.folds(f"{REPLAY_YEAR - 1}-01", f"{REPLAY_YEAR - 1}-12"), scenarios=("persistence",))
    frozen, _ = fit_frozen(f, oos, REPLAY_YEAR)
    ref = oos[["day"]].assign(forecast=oos["pred_persistence"])
    e = errors(oos, "pred_persistence")
    covered = ((e["err"].abs() / oos.loc[e.index, "pred_persistence"]) <= frozen.q).astype(float)
    w = weekly_stats(e, covered)
    w = w[w["hours"] >= MIN_WEEK_HOURS]
    season = {"mape_high": float(w["mape"].quantile(QUANTILES["mape_high"])),
              "bias_low": float(w["rel_bias"].quantile(QUANTILES["bias_low"])),
              "bias_high": float(w["rel_bias"].quantile(QUANTILES["bias_high"])),
              "coverage_low": float(w["covered"].quantile(QUANTILES["coverage_low"])), "weeks": int(len(w))}
    thresholds = {"performance": {s: dict(season) for s in ("winter", "spring", "summer", "fall")},
                  "last_year": REPLAY_YEAR - 1, "weekly_validation": [{"first_day": str(d)} for d in w["first_day"]]}
    return frozen, ref, thresholds


def frozen_and_reference():
    frozen, ref, _ = frozen_reference_thresholds()
    return frozen, ref


def thresholds():
    """The world's thresholds, with the drift alarm fixed by hand (SYNTHETIC_WORLD, which the report prints) at a level
    clean weeks stay under, read when called."""
    th = frozen_reference_thresholds()[2]
    return {"performance": th["performance"],
            "drift": {"temperature_high": SYNTHETIC_WORLD["drift_alarm"], "forecast_high": SYNTHETIC_WORLD["drift_alarm"]},
            "last_year": th["last_year"], "weekly_validation": th["weekly_validation"]}


# --------------------------------------------------------------------------------------------------------------- v2
#
# The same world with NBM-shaped forecast runs (lane A's conftest logic, their temperatures the world's own), a frozen
# production GAM and its two comparison models fitted on it, and thresholds from the GAM's own out-of-sample weeks.
# Then a chain world shifted to 2022-2025 and a run store holding v2's first 2024 replay, so the 2025 gate, replay and
# evidence run end to end on synthetic data (`chain_store`).

V2_KEYS = ("gam_forecast", "forecast", "persistence")
SEASON_NAMES = ("winter", "spring", "summer", "fall")


def world_between(first, last, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """world()'s demand and observations on another span (world() itself is v1's and stays as it is)."""
    first, last = pd.Timestamp(first), pd.Timestamp(last)
    obs = synth_obs(first, last, seed=seed)
    idx = pd.DatetimeIndex(obs["ts_utc"])
    h = np.arange(len(idx))
    rng = np.random.default_rng(seed + 1)
    day = (idx - pd.Timedelta(hours=6)).normalize()
    codes, uniq = pd.factorize(day)
    eps = np.zeros(len(uniq))
    for i in range(1, len(uniq)):
        eps[i] = DAY_PHI * eps[i - 1] + rng.normal(0, DAY_SIGMA * np.sqrt(1 - DAY_PHI ** 2))
    mw = (18_000 + 2_500 * np.sin(2 * np.pi * h / 24) - 900 * (idx.dayofweek >= 5)
          + 350 * np.maximum(18 - obs["temp_c"].to_numpy(), 0) + rng.normal(0, 60, len(idx)))
    mw = mw * (1 + eps[codes])
    raw = pd.DataFrame({"ts_utc": idx, "mw": mw, "source_line": np.arange(2, len(idx) + 2)})
    return raw, obs


def v2_runs(first, last, seed: int = 0) -> pd.DataFrame:
    """NBM-shaped runs (conftest.synth_runs) from three days before `first` to `last`, each point's temperature the
    world's (synth_obs's without its noise, hours counted from `first`) plus noise, in whole °F, with their
    availability as quality.run.load_runs gives it; the runs available after `last` left out."""
    first, last = pd.Timestamp(first), pd.Timestamp(last)
    runs = synth_runs(first - pd.Timedelta(days=3), last, seed=seed)
    rng = np.random.default_rng(seed + 11)
    c = synth_temperature_c(runs["ftime"], first) + rng.normal(0, 0.5, len(runs))
    runs["tmp_f"] = np.round(c * 9 / 5 + 32).astype(float)
    runs["available_utc"] = runs["runtime"] + AVAILABILITY_LAG
    return runs[runs["available_utc"] <= last].reset_index(drop=True)


def lst(ts) -> pd.Timestamp:
    return (pd.Timestamp(ts) - pd.Timedelta(hours=1) + LST_OFFSET).tz_convert(None).normalize()


def v2_thresholds(oos: pd.DataFrame, q: float, last_year: int) -> dict:
    """The production model's thresholds from its own out-of-sample weeks (one set for every season, as world()'s),
    with SYNTHETIC_WORLD's drift alarm."""
    e = errors(oos, "pred_gam_forecast")
    covered = ((e["err"].abs() / oos.loc[e.index, "pred_gam_forecast"]) <= q).astype(float)
    w = weekly_stats(e, covered)
    w = w[w["hours"] >= MIN_WEEK_HOURS]
    season = {"mape_high": float(w["mape"].quantile(QUANTILES["mape_high"])),
              "bias_low": float(w["rel_bias"].quantile(QUANTILES["bias_low"])),
              "bias_high": float(w["rel_bias"].quantile(QUANTILES["bias_high"])),
              "coverage_low": float(w["covered"].quantile(QUANTILES["coverage_low"])), "weeks": int(len(w))}
    alarm = SYNTHETIC_WORLD["drift_alarm"]
    return {"performance": {s: dict(season) for s in SEASON_NAMES},
            "drift": {"temperature_high": alarm, "forecast_high": alarm},
            "last_year": last_year, "weekly_validation": [{"first_day": str(d)} for d in w["first_day"]]}


@dataclass
class V2World:
    raw: pd.DataFrame
    obs: pd.DataFrame
    runs: pd.DataFrame
    f: pd.DataFrame
    oos: pd.DataFrame
    record: pd.DataFrame
    model: object
    comparators: dict
    reference: pd.DataFrame
    thresholds: dict
    cold_c: float
    replay_year: int


def build_v2_world(first, last, replay_year: int, design_last, seed: int = 0) -> V2World:
    """A v2 world: demand, observations and runs over [first, last]; the feature table of the design span (to
    `design_last`) with the forecast issued by the run rule; the year before `replay_year` validated walk-forward on the
    production GAM and its two comparators; the three frozen for `replay_year`."""
    raw, obs = world_between(first, last, seed)
    runs = v2_runs(first, last, seed)
    design_last = pd.Timestamp(design_last)
    days = pd.date_range(lst(first), lst(design_last))
    issued = forecast_temperature(runs.loc[runs["available_utc"] <= design_last, ["runtime", "ftime", "tmp_f"]],
                                  days)
    temp = obs.set_index("ts_utc")["temp_c"]
    f = build(build_demand(raw[raw["ts_utc"] <= design_last], pd.Timestamp(first), design_last).frame, temp,
              {"forecast": issued})
    year = replay_year - 1
    oos = wf.run(f, wf.folds(f"{year}-01", f"{year}-12"), models=V2_KEYS)
    oos = pd.concat([oos, f.reindex(oos.index)[["temp_forecast", "available_forecast", "temp_observed",
                                                "temp_persistence"]]], axis=1)
    model = fit_frozen(f, oos, replay_year, key="gam_forecast")[0]
    comparators = {k: fit_frozen(f, oos, replay_year, key=k)[0] for k in ("forecast", "persistence")}
    reference = oos.loc[oos["day"] < pd.Timestamp(f"{replay_year}-01-01"), ["day"]].assign(
        forecast=oos["pred_gam_forecast"])
    return V2World(raw=raw, obs=obs, runs=runs, f=f, oos=oos, record=issued.record, model=model,
                   comparators=comparators, reference=reference, thresholds=v2_thresholds(oos, model.q, year),
                   cold_c=cold_threshold(f["temp_forecast"]), replay_year=replay_year)


@lru_cache(maxsize=1)
def world_v2() -> V2World:
    """world()'s demand and observations, with NBM-shaped runs, the frozen production GAM for 2022 and its two
    comparison models, and v2 thresholds from the GAM's 2021 out-of-sample weeks."""
    w = build_v2_world(FIRST, LAST, REPLAY_YEAR, LAST)
    raw, obs = world()
    pd.testing.assert_frame_equal(w.raw, raw)  # the same world
    pd.testing.assert_frame_equal(w.obs, obs)
    return w


# ------------------------------------------------------------------------------- the chain: a run store for 2024-2025

CHAIN_FIRST, CHAIN_LAST = pd.Timestamp("2022-01-01T06:00Z"), pd.Timestamp("2025-02-01T05:00Z")
# The days the chain's replays cover (run.replay_days is patched to these: a whole year would take minutes); 2023's
# are the rehearsal's (`rehearsal_world`), two Monday-to-Sunday weeks.
CHAIN_DAYS = {2023: ("2023-01-02", "2023-01-15"), 2024: ("2024-01-01", "2024-01-14"),
              2025: ("2025-01-01", "2025-01-19")}
# The one 2024 record the 2025 vintage revises.
REVISED_UTC, REVISION_MW = pd.Timestamp("2024-01-08T15:00Z"), 500.0
HQ_SHA = {2024: "a" * 64, 2025: "b" * 64}
SCRATCH_ID = ("-c", "user.name=scratch", "-c", "user.email=scratch@example.invalid", "-c", "commit.gpgsign=false",
              "-c", "tag.gpgsign=false")
V2_HEADING = "## Departures from the v2 frozen rules"
NONE_YET = "None yet.\n"


@lru_cache(maxsize=1)
def chain_world() -> V2World:
    return build_v2_world(CHAIN_FIRST, CHAIN_LAST, 2024, DESIGN_LAST, seed=3)


def vintage(through: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The raw demand and station records of the export through `through`: 2024's ends with 2024; 2025's holds every
    record and revises one of 2024."""
    w = chain_world()
    if through == 2024:
        end = data_last(2024)
        return (w.raw[w.raw["ts_utc"] <= end].reset_index(drop=True),
                w.obs[w.obs["ts_utc"] <= end].reset_index(drop=True))
    raw = w.raw.copy()
    raw.loc[raw["ts_utc"] == REVISED_UTC, "mw"] += REVISION_MW
    return raw, w.obs.copy()


def eccc_manifests(through: int) -> list[dict]:
    return [{"climate_id": "7025251", "months": {"window": {"sha256": f"{through}" * 16}}}]


def nbm_manifest(through: int) -> dict:
    return {"years": {str(y): {"sha256": f"{y}" * 16} for y in range(2019, int(through) + 1)}}


def chain_load_raw(dq):
    return vintage(int(dq.data.params.get("through", 2024)))


def chain_load_runs(dq, years=None):
    """The runs as quality.run.load_runs gives them for `dq`'s window: those available by its last hour, of the years
    listed (by run time)."""
    runs = chain_world().runs
    through = int(dq.data.params.get("through", 2024))
    keep = runs["available_utc"] <= data_last(through)
    if years is not None:
        keep &= runs["runtime"].dt.year.isin([int(y) for y in years])
    return runs[keep].reset_index(drop=True)


def chain_verify_raw(through: int = 2024):
    return {"sha256": HQ_SHA[int(through)]}, eccc_manifests(int(through))


def chain_verify_nbm(through: int = 2024):
    return nbm_manifest(int(through))


def chain_load_inputs():
    from valiwatt.monitor.run import DQ_PASSED
    from valiwatt.quality.run import EXPERIMENT as DQ_EXPERIMENT
    from valiwatt.tracking import latest_run

    return latest_run(DQ_EXPERIMENT, DQ_PASSED), None, None


def stub_measure(*args, **kwargs):
    return {"stub": "the measures themselves are tested in tests/test_sensitivity.py and test_replay_v2.py"}


def stub_reuse(*args, **kwargs):
    return {"stub": "tested in tests/test_replay_v2.py", "longest_lead_h": kwargs.get("longest_lead_h")}


def chain_days(year: int) -> tuple[str, str]:
    return CHAIN_DAYS[year]


def install_chain(mp, root: Path) -> None:
    """Point the code at the chain under `root` (its scratch repository and run store) and at its synthetic data
    loaders; the diagnostics' measures are stubbed (they are tested on their own)."""
    from valiwatt import tracking
    from valiwatt.monitor import rules, sensitivity
    from valiwatt.monitor import run as mrun
    from valiwatt.quality import run as qrun

    repo, store = Path(root) / "repo", Path(root) / "mlruns"
    for module in (rules, mrun, tracking):
        mp.setattr(module, "REPO_ROOT", repo)
    mp.setattr(tracking, "MLRUNS", store)
    mp.setattr(paths, "MLRUNS", store)
    mp.setattr(mrun, "replay_days", chain_days)
    mp.setattr(mrun, "load_inputs", chain_load_inputs)
    mp.setattr(mrun, "load_raw", chain_load_raw)
    mp.setattr(qrun, "load_runs", chain_load_runs)
    mp.setattr(qrun, "_verify_raw", chain_verify_raw)
    mp.setattr(qrun, "_verify_nbm", chain_verify_nbm)
    mp.setattr(sensitivity, "measure", stub_measure)
    mp.setattr(sensitivity, "forecast_run_reuse", stub_reuse)


class _Patch:
    """monkeypatch's setattr, for a process that has no pytest fixture (a forkserver child)."""

    def setattr(self, target, name, value):
        setattr(target, name, value)


def install_in_child(root: str) -> None:
    install_chain(_Patch(), Path(root))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *SCRATCH_ID, *args], cwd=repo, capture_output=True, text=True,
                          check=True).stdout.strip()


def commit(repo: Path, files: dict[str, str] | None = None, message: str = "scratch", empty: bool = False) -> str:
    """A commit in the scratch repository writing `files` (path -> text); returns its hash."""
    for path, text in (files or {}).items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "--no-verify", *(["--allow-empty"] if empty else []), "-m", message)
    return git(repo, "rev-parse", "HEAD")


def agents(*entries: str) -> str:
    """The scratch AGENTS.md: the v2 departures section holding `entries` (none: "None yet.")."""
    return f"# scratch\n\n{V2_HEADING}\n\nThe v2 departures.\n\n" + ("".join(entries) or NONE_YET)


def ruling(outcome: str, applies_to: str, start: str = "", day: str = "2026-12-01") -> str:
    said = f" It rules on the start of {start}." if start else ""
    return f"- **{day}, the owner's ruling on 2025: {outcome}.** It applies to `{applies_to[:12]}`.{said}\n"


def departure_entry(*commits: str, head: str = "2026-11-02, a fix to the gate: a departure") -> str:
    return f"- **{head}.** Commits " + ", ".join(f"`{c[:12]}`" for c in commits) + ".\n"


def make_repo(repo: Path) -> str:
    """The scratch repository: a rule file, a rendering file, the dependencies, AGENTS.md with the v2 section; its
    first commit tagged rules-frozen-v2 (F0). Returns F0."""
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    f0 = commit(repo, {"src/valiwatt/monitor/rules.py": "MIN = 4\n", "src/valiwatt/report/render.py": "# renders\n",
                       "config/stations.toml": "x = 1\n", "pyproject.toml": "[project]\nname = 'scratch'\n",
                       "uv.lock": "version = 1\n", "AGENTS.md": agents(), ".gitignore": "mlruns/\n"}, "rules")
    git(repo, "tag", "-a", "-m", "v2 freeze", FREEZE_TAG, f0)
    return f0


def _log_dq(through: int) -> str:
    import mlflow

    from valiwatt import tracking
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.quality import run as qrun
    from valiwatt.sources import nbm

    tags = {"kind": "dq", "rules_sha256": rules_sha256()}
    with tracking.start_run(qrun.EXPERIMENT, "data-quality", tags=tags) as run:
        mlflow.log_params({"through": through, "window_last_utc": data_last(through).isoformat(),
                           "hq_sha256": HQ_SHA[through],
                           "eccc_manifests_sha256": qrun.eccc_manifests_sha256(eccc_manifests(through)),
                           "nbm_manifest_sha256": nbm.manifest_sha256(nbm_manifest(through)),
                           **({"hq_revised_records": 1} if through > 2024 else {})})
        mlflow.log_text(json.dumps({"checks": {"nbm_runs": {"metrics": {"longest_lead_h": {
            str(y): 72.0 for y in range(2022, through + 1)}}}}}), "results/dq_results.json")
        mlflow.set_tag("gates", "passed")
    return run.info.run_id


def log_dq(through: int, gates: str = "passed", rules: str | None = None, **params) -> str:
    """Another data-quality run in the chain's store (a test's), with its gates, rules hash and params as given."""
    import mlflow

    from valiwatt import tracking
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.quality import run as qrun
    from valiwatt.sources import nbm

    base = {"through": through, "window_last_utc": data_last(through).isoformat(), "hq_sha256": HQ_SHA[through],
            "eccc_manifests_sha256": qrun.eccc_manifests_sha256(eccc_manifests(through)),
            "nbm_manifest_sha256": nbm.manifest_sha256(nbm_manifest(through))}
    with tracking.start_run(qrun.EXPERIMENT, "data-quality",
                            tags={"kind": "dq", "rules_sha256": rules or rules_sha256()}) as run:
        mlflow.log_params(base | params)
        mlflow.set_tag("gates", gates)
    return run.info.run_id


def log_validation(w: V2World, dq_run_id: str, thresholds: dict | None = None, year: int = 2024) -> str:
    """A complete validation run of `w` on `dq_run_id`: its out-of-sample frame, the production model's intervals,
    the forecast record, and the thresholds of the replayed `year` with validation's cold_c."""
    import mlflow

    from valiwatt import tracking
    from valiwatt.forecast import validate as vd
    from valiwatt.monitor.rules import rules_sha256

    with tracking.start_run(vd.EXPERIMENT, "validation", tags={"kind": "validation", "dq_run_id": dq_run_id,
                                                               "rules_sha256": rules_sha256()}) as run:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            w.oos.to_parquet(tmp / "oos.parquet")
            pred = w.oos["pred_gam_forecast"]
            iv = pd.DataFrame({"lower": pred * (1 - w.model.q), "upper": pred * (1 + w.model.q)}, index=w.oos.index)
            iv["covered"] = ((w.oos["target"] >= iv["lower"]) & (w.oos["target"] <= iv["upper"])).astype(float)
            iv.to_parquet(tmp / "intervals.parquet")
            w.record.to_parquet(tmp / "forecast_record.parquet")
            mlflow.log_artifacts(str(tmp), artifact_path="frames")
        mlflow.log_text(json.dumps(jsonable({"thresholds": {str(year): thresholds or w.thresholds},
                                             "config": {"cold_c": w.cold_c}})), "results/validation.json")
        mlflow.set_tag("status", "complete")
    return run.info.run_id


def log_frozen_models(w: V2World, validation_run_id: str, dq_run_id: str, model=None) -> str:
    from valiwatt.forecast import freeze as fz
    from valiwatt.monitor.rules import rules_sha256

    model = model or w.model
    cols = fz.input_columns(model.family, model.scenario)
    example = w.f[cols].dropna().tail(24).astype({"hour": float})
    run, _ = fz.log_frozen(model, fz.coefficient_table(model), example,
                           {"validation_run_id": validation_run_id, "dq_run_id": dq_run_id,
                            "rules_sha256": rules_sha256()}, comparators=w.comparators)
    return run.info.run_id


def ledger_entry(run_id: str) -> dict:
    """The v2 ledger entry of a finished replay run, in lane E's v2 shape (report/ledger.py V2_FIELDS)."""
    from valiwatt import tracking
    from valiwatt.monitor import run as mrun

    c = tracking.client()
    run = c.get_run(run_id)
    res = mrun._load_json_artifact(c, run_id, "results/replay.json")
    t = run.data.tags
    recs = [{"week_start": str(d["week_start"])[:10], "recommendation": d["recommendation"]}
            for d in res["decisions"] if d["recommendation"]]
    return {"run_id": run_id, "year": int(t["year"]), "freeze_tag": res["freeze_tag"],
            "freeze_commit": t["freeze_commit"], "rules_sha256": t["rules_sha256"], "code_sha256": t["code_sha256"],
            "started_utc": mrun._utc(run.info.start_time), "ended_utc": mrun._utc(run.info.end_time),
            "status": t["status"], "mape": res["summary"]["mape"]["mean"],
            "coverage": res["summary"]["coverage"]["mean"], "recommendations": recs,
            "departure": t["departure"] == "true", "departure_from": [x for x in t["departure_from"].split(",") if x],
            "evaluation": t["evaluation"], "model_sha256": t["coefficient_sha256"],
            "verdict": {k: res["verdict"][k] for k in ("shipped", "family")},
            "frozen_sha256": mrun.frozen_digest(res["frozen_sha256"]), "standing": t.get("standing", "")}


def write_ledger(repo: Path, entries: list[dict], message: str = "ledger") -> str:
    return commit(repo, {"docs/replay-ledger.json": json.dumps({"replays": entries}, indent=2) + "\n"}, message)


def replay_runs(year: int, kind: str = "replay") -> list:
    """The store's runs of `kind` and `year`, any status, deleted included, oldest first."""
    from mlflow.entities import ViewType

    from valiwatt import tracking
    from valiwatt.monitor import run as mrun

    c = tracking.client()
    exp = c.get_experiment_by_name(mrun.EXPERIMENT)
    runs = c.search_runs([exp.experiment_id], f"tags.kind = '{kind}' and tags.year = '{year}'",
                         run_view_type=ViewType.ALL) if exp else []
    return sorted(runs, key=lambda r: r.info.start_time)


def fake_run(tags: dict, status: str = "FINISHED", experiment: str | None = None) -> str:
    """A run created by hand in the chain's store, with exactly `tags`, left at `status` (RUNNING: left open)."""
    from valiwatt import tracking
    from valiwatt.monitor import run as mrun

    c = tracking.client()
    run = c.create_run(tracking.experiment_id(experiment or mrun.EXPERIMENT), tags=tags)
    if status != "RUNNING":
        c.set_terminated(run.info.run_id, status)
    return run.info.run_id


def build_chain(root: Path) -> dict:
    """The chain under `root`: the scratch repository (F0 tagged rules-frozen-v2), a through-2024 data-quality run, a
    validation run and the 2024 frozen run, v2's first 2024 replay run by run_replay(2024) itself and its ledger entry
    committed, then a through-2025 data-quality run on a vintage revising one 2024 record."""
    import pytest

    from valiwatt.monitor import run as mrun

    w = chain_world()
    f0 = make_repo(Path(root) / "repo")
    with pytest.MonkeyPatch.context() as mp:
        install_chain(mp, root)
        dq24 = _log_dq(2024)
        val = log_validation(w, dq24)
        frozen = log_frozen_models(w, val, dq24)
        assert mrun.run_replay(2024, n_boot=20) == 0
        (first,) = replay_runs(2024)
        entry = ledger_entry(first.info.run_id)
        write_ledger(Path(root) / "repo", [entry])
        dq25 = _log_dq(2025)
    return {"f0": f0, "dq24": dq24, "dq25": dq25, "validation": val, "frozen": frozen,
            "first_2024": first.info.run_id, "entry": entry}


@lru_cache(maxsize=1)
def chain_base() -> tuple[Path, dict]:
    """The chain built once per process, in a temporary folder removed at exit; each test works on a copy."""
    import atexit

    root = Path(tempfile.mkdtemp(prefix="valiwatt-chain-"))
    atexit.register(shutil.rmtree, root, True)
    ids = build_chain(root / "chain")
    return root / "chain", ids


def chain_copy(tmp_path: Path, mp):
    """A private copy of the chain under `tmp_path`, the code pointed at it (install_chain): its root, scratch
    repository and run store, and the IDs it was built with."""
    from types import SimpleNamespace

    base, ids = chain_base()
    root = Path(tmp_path) / "chain"
    copy_chain(base, root)
    install_chain(mp, root)
    return SimpleNamespace(root=root, repo=root / "repo", store=root / "mlruns", **ids)


REHEARSAL_FIRST, REHEARSAL_LAST = pd.Timestamp("2021-01-01T06:00Z"), pd.Timestamp("2023-02-01T05:00Z")


@lru_cache(maxsize=1)
def rehearsal_world() -> V2World:
    """A v2 world for the 2023 rehearsal: the models frozen for 2023 (calibrated on its 2022 folds), thresholds from
    its 2022 weeks, and an out-of-sample frame and forecast record holding, of 2023, exactly the days the rehearsal
    replays (CHAIN_DAYS[2023]), as validation's would for a rehearsal of those days: the weather identity compares
    every 2023 hour and day validation holds with the replay's."""
    import dataclasses

    w = build_v2_world(REHEARSAL_FIRST, REHEARSAL_LAST, 2023, REHEARSAL_LAST, seed=5)
    first, last = (pd.Timestamp(d) for d in CHAIN_DAYS[2023])
    jan = wf.run(w.f, wf.folds("2023-01", "2023-01"), models=V2_KEYS)
    jan = pd.concat([jan, w.f.reindex(jan.index)[["temp_forecast", "available_forecast", "temp_observed",
                                                  "temp_persistence"]]], axis=1)
    jan = jan[(jan["day"] >= first) & (jan["day"] <= last)]
    days = pd.DatetimeIndex(w.record.index)
    record = w.record[(days < pd.Timestamp("2023-01-01")) | ((days >= first) & (days <= last))]
    return dataclasses.replace(w, oos=pd.concat([w.oos, jan[w.oos.columns]]), record=record)


def install_rehearsal(mp, chain) -> dict:
    """The 2023 rehearsal on a chain copy: the through-2025 data-quality run's gates set failed, so the latest passing
    one is the through-2024 run (as the 2024 tests do); on it, a validation run of rehearsal_world() with thresholds
    for 2023, and the models frozen for 2023 from it (alias replay-2023). The raw records and the runs read are the
    rehearsal world's, the runs those of the years listed (by run time). Returns the run IDs and the `years` each read
    of the runs asked for."""
    from valiwatt import tracking
    from valiwatt.monitor import run as mrun
    from valiwatt.quality import run as qrun

    w = rehearsal_world()
    tracking.client().set_tag(chain.dq25, "gates", "failed")
    val = log_validation(w, chain.dq24, year=2023)
    frozen = log_frozen_models(w, val, chain.dq24)
    asked = []

    def load_runs(dq, years=None):
        asked.append(None if years is None else tuple(years))
        if years is None:
            return w.runs.copy()
        return w.runs[w.runs["runtime"].dt.year.isin([int(y) for y in years])].reset_index(drop=True)

    mp.setattr(mrun, "load_raw", lambda dq: (w.raw, w.obs))
    mp.setattr(qrun, "load_runs", load_runs)
    return {"validation": val, "frozen": frozen, "dq": chain.dq24, "years": asked}


def artifact_file(run_id: str, path: str) -> Path:
    """The file of a run's artifact in the chain's local store (to edit it as a store edit would)."""
    from urllib.parse import unquote, urlparse

    from valiwatt import tracking

    uri = tracking.client().get_run(run_id).info.artifact_uri
    return Path(unquote(urlparse(uri).path)) / path


class StoppedHere(RuntimeError):
    """Raised by a stub on purpose: the step it replaces was reached."""


def simultaneous_start(root: str, barrier, results, delay: float) -> None:
    """A forkserver child: the chain installed in this fresh process, the 2025 gate slowed by `delay` seconds so that
    two callers would both pass it before either opens, the inputs' read stubbed to stop at once after the run opens;
    released by `barrier` together with the other caller, it reports how its run_replay(2025) ended."""
    install_in_child(root)
    from valiwatt.monitor import run as mrun

    real = mrun.held_out_gate

    def slow(year, departure=False):
        out = real(year, departure)
        time.sleep(delay)
        return out

    def stop(*args, **kwargs):
        raise StoppedHere("the run opened")

    mrun.held_out_gate = slow
    mrun._read_inputs = stop
    barrier.wait()
    try:
        mrun.run_replay(2025)
        results.put(("finished", ""))
    except mrun.FreezeError as e:
        results.put(("refused", str(e)))
    except StoppedHere:
        results.put(("opened", ""))
    except Exception as e:  # noqa: BLE001 - reported, so the parent never waits on a child that died
        results.put(("error", f"{type(e).__name__}: {e}"))


def worker_init(inputs: dict) -> None:
    """A sensitivity worker's initializer for the chain: the chain installed in the fresh worker process (its root in
    the inputs), then the measures' own initializer."""
    from valiwatt.monitor import sensitivity

    inputs = dict(inputs)
    install_in_child(inputs.pop("_chain_root"))
    sensitivity._init(inputs)


def copy_chain(base: Path, dest: Path) -> None:
    """A private copy of a built chain: the repository and the run store, the store's absolute paths re-rooted."""
    shutil.copytree(base, dest)
    old, new = str(base), str(dest)
    con = sqlite3.connect(dest / "mlruns" / "mlflow.db")
    try:
        for (table,) in con.execute("select name from sqlite_master where type = 'table'").fetchall():
            for col in [r[1] for r in con.execute(f"pragma table_info('{table}')").fetchall()]:
                con.execute(f'update "{table}" set "{col}" = replace("{col}", ?, ?) where typeof("{col}") = '
                            f"'text' and \"{col}\" like ?", (old, new, f"%{old}%"))
        con.commit()
    finally:
        con.close()
