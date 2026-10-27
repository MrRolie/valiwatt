"""A synthetic noon world for v3's replay tests (docs/plan-v3.md, sections 2, 4, 7, 11 and 12): demand, the station and
NBM-shaped runs with their wind over 2023-01 to 2025-02, and v3 and its three comparison models frozen for 2024 and for
2025 by v3's own freeze (forecast/freeze_v3.py `fit_frozen_v3`), each noon model calibrated on out-of-sample rows of
the year before, made here as v3's validation logs them.

Demand follows replay_world's daily and weekly cycles and heating below 18 °C, with a level that persists from day to
day (as test_freeze_v3's world), so both lags read something. The runs' temperatures are the station's without its
noise, plus noise, in whole °F, with the archive's whole knots of wind (conftest.synth_runs).

The replays of these tests cover two Monday-to-Sunday weeks of each year (`DAYS`), as replay_world's chain does: a
whole year would take minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from conftest import synth_obs, synth_runs, synth_temperature_c
from replay_world import DAY_PHI, DAY_SIGMA, SEASON_NAMES, lst
from valiwatt.breaks import SYNTHETIC_WORLD
from valiwatt.config import data_last
from valiwatt.forecast import freeze_v3 as fv
from valiwatt.forecast import validate as vd
from valiwatt.forecast.evaluate import cold_threshold, errors, weekly_stats
from valiwatt.forecast.features import build, scored_at
from valiwatt.forecast.issue import NOON
from valiwatt.monitor.rules import MIN_WEEK_HOURS, QUANTILES
from valiwatt.quality.demand import build_demand
from valiwatt.sources.nbm import AVAILABILITY_LAG

FIRST, LAST = pd.Timestamp("2023-01-01T06:00Z"), pd.Timestamp("2025-02-01T05:00Z")
# The days a replay of each year covers: two Monday-to-Sunday weeks (2024-01-01 and 2025-01-06 are Mondays).
DAYS = {2024: ("2024-01-01", "2024-01-14"), 2025: ("2025-01-06", "2025-01-19")}
SEED = 7


def world_raw(seed: int = SEED) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The raw demand records (ts_utc, mw, source_line) and the station's (synth_obs's shape) over [FIRST, LAST]."""
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


def world_runs(seed: int = SEED) -> pd.DataFrame:
    """NBM-shaped runs with their wind, from three days before FIRST, each point's temperature the station's without
    its noise plus noise, in whole °F, with their availability; the runs available after LAST left out."""
    runs = synth_runs(FIRST - pd.Timedelta(days=3), LAST, seed=seed, wind=True)
    rng = np.random.default_rng(seed + 11)
    c = synth_temperature_c(runs["ftime"], FIRST) + rng.normal(0, 0.5, len(runs))
    runs["tmp_f"] = np.round(c * 9 / 5 + 32).astype(float)
    runs["available_utc"] = runs["runtime"] + AVAILABILITY_LAG
    return runs[runs["available_utc"] <= LAST].reset_index(drop=True)


def thresholds_of(oos: pd.DataFrame, q: float, last_year: int) -> dict:
    """v3's thresholds from its own out-of-sample weeks (one set for every season, as replay_world's), with
    SYNTHETIC_WORLD's drift alarm."""
    e = errors(oos, "pred_v3")
    covered = ((e["err"].abs() / oos.loc[e.index, "pred_v3"]) <= q).astype(float)
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
class V3World:
    raw: pd.DataFrame
    obs: pd.DataFrame
    runs: pd.DataFrame
    noon: pd.DataFrame  # the noon table over the whole span (features.build at NOON)
    midnight: pd.DataFrame  # v2's table over the whole span
    record_noon: pd.DataFrame  # the noon feed's record of every day (validation's frames/forecast_record_noon)
    oos: pd.DataFrame  # out-of-sample rows of 2023 and 2024, as v3's validation logs them


@lru_cache(maxsize=1)
def world() -> V3World:
    raw, obs = world_raw()
    runs = world_runs()
    temp = obs.set_index("ts_utc")["temp_c"]
    days = pd.date_range(lst(FIRST), lst(LAST))
    demand = build_demand(raw, FIRST, LAST).frame
    noon_in = vd.forecast_inputs(runs, days, last=LAST, issue=NOON)
    noon = build(demand, temp, noon_in, issue=NOON)
    midnight = build(demand, temp, vd.forecast_inputs(runs, days, last=LAST))
    rows = noon[(noon["day"] >= pd.Timestamp("2023-01-01")) & (noon["day"] <= pd.Timestamp("2024-12-31"))]
    keep = ["target", "day", "hour", "temp_forecast", "wind_forecast", "daymean_forecast", "available_forecast",
            "lagday_observed", "temp_observed", "daymean_observed"]
    oos = rows[keep].copy()
    oos["fold"] = oos["day"].dt.to_period("M").astype(str)
    oos["scored_at"] = scored_at(oos["day"], LAST)
    rng = np.random.default_rng(SEED + 5)
    usable = np.isfinite(rows[["lag48", "lag168", "temp_forecast", "wind_forecast", "daymean_forecast",
                               "lagday_observed"]].to_numpy(dtype=float)).all(axis=1)
    for i, key in enumerate(fv.FROZEN_KEYS):
        pred = oos["target"] * (1 + rng.normal(0, 0.02 + 0.005 * i, len(oos)))
        oos[f"pred_{key}"] = pred.where(usable | (key == "v2_midnight"))
    return V3World(raw=raw, obs=obs, runs=runs, noon=noon, midnight=midnight, record_noon=noon_in["forecast"].record,
                   oos=oos)


@dataclass
class Frozen:
    year: int
    model: object
    comparators: dict  # v2_noon, v1_noon, v2_midnight
    thresholds: dict
    reference: pd.DataFrame
    cold_c: float
    coef: pd.DataFrame  # v3's coefficient table, as freeze-v3 logs it


@lru_cache(maxsize=2)
def frozen(year: int) -> Frozen:
    """v3 and its comparison models frozen for `year` (2024: fitted to 2023-12-24, calibrated on 2023; 2025: to
    2024-12-24, calibrated on 2024), the thresholds from the year before's weeks, the forecast-drift reference (the
    out-of-sample v3 forecasts before the year) and cold_c."""
    w = world()
    tables = {"noon": w.noon, "midnight": w.midnight}
    last_day = pd.Timestamp(f"{year - 1}-12-31")
    fitted = {k: fv.fit_frozen_v3(fv.table_of(k, tables), w.oos, year, key=k, last_day=last_day)
              for k in fv.FROZEN_KEYS}
    models = {k: m for k, (m, _) in fitted.items()}
    before = w.oos[w.oos["day"] < pd.Timestamp(f"{year}-01-01")]
    last_year = before[before["day"].dt.year == year - 1]
    return Frozen(year=year, model=models["v3"], comparators={k: models[k] for k in fv.FROZEN_KEYS[1:]},
                  thresholds=thresholds_of(last_year, models["v3"].q, year - 1),
                  reference=before[["day"]].assign(forecast=before["pred_v3"]),
                  cold_c=cold_threshold(w.noon.loc[w.noon["day"] <= last_day, "temp_forecast"]),
                  coef=fitted["v3"][1])


def no_gate(*args) -> None:
    """A gate that lets every replay through (the gates are tested on their own)."""


def noon_replay(year: int = 2024, *, raw=None, obs=None, runs=None, days=None, midnight: bool = True,
                comparators: bool = True, gate=no_gate, **kwargs):
    """replay() at NOON on the world's two weeks of `year`, its models frozen for `year`, v2's form at midnight beside
    it (`midnight`), the noon comparators scored after the loop (`comparators`). raw, obs, runs: the world's unless
    given."""
    from valiwatt.monitor import replay as rp

    w, fz = world(), frozen(year)
    first, last = days or DAYS[year]
    noon_cmp = {k: m for k, m in fz.comparators.items() if k != "v2_midnight"} if comparators else None
    return rp.replay(w.raw if raw is None else raw, w.obs if obs is None else obs, fz.model, fz.thresholds,
                     fz.reference, first, last, data_last(year if year < 2025 else 2025),
                     runs=w.runs if runs is None else runs, comparators=noon_cmp, issue=NOON,
                     midnight=fz.comparators["v2_midnight"] if midnight else None, gate=gate, **kwargs)


# ------------------------------------------------------------------- the chain: v3's lineage in a scratch repository
#
# A scratch repository (records_v3_world.make_repo: `v3-rules-frozen` on F0, pinned; `v3-first-run` on F0; v2's
# ledger with v2's first 2024 replay, which v3's store must not hold) and a run store under one root, beside a data
# folder for the files past 2024, the code pointed at all three (`install_chain`). The rehearsal base holds the runs
# a 2024 rehearsal reads: a v3 data-quality run through 2024, a validation run of this world, the models frozen for
# 2024 and 2025. The gate base adds the step-5 rehearsal (run_replay_v3(2024) itself), its ledger-v3 entry and the
# freeze record, each committed, then the export through 2025 (its files, its data-quality run) and v2's branch with
# v2's 2025 test of record. Each base is built once per process; each test works on a copy (replay_world.copy_chain).

# The one 2024 record the export through 2025 revises (the evidence finds it).
REVISED_UTC, REVISION_MW = pd.Timestamp("2024-01-08T15:00Z"), 500.0
HQ_SHA = {2024: "1" * 64, 2025: "4" * 64}
V2_2024_RUN, V2_2025_RUN = "1" * 32, "2" * 32
V2_2024_STARTED = "2025-12-01T00:00:00.000Z"
DQ_EXPERIMENT_KIND = "dq_v3"


def ledger_time(t: pd.Timestamp) -> str:
    """A time in the ledgers' form (...mmmZ)."""
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"


def vintage(through: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The raw demand and station records of the export through `through`: 2024's ends with 2024; 2025's holds every
    record of the world and revises one of 2024."""
    w = world()
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
    runs = world().runs
    keep = runs["available_utc"] <= data_last(int(dq.data.params.get("through", 2024)))
    if years is not None:
        keep &= runs["runtime"].dt.year.isin([int(y) for y in years])
    return runs[keep].reset_index(drop=True)


def chain_verify_raw(through: int = 2024):
    return {"sha256": HQ_SHA[int(through)]}, eccc_manifests(int(through))


def chain_verify_nbm(through: int = 2024):
    return nbm_manifest(int(through))


def chain_days(year: int) -> tuple[str, str]:
    return DAYS[year]


def install_chain(mp, root: Path) -> None:
    """Point the code at the chain under `root`: its repository and run store (records_v3_world.install, and v2store,
    which binds the root at import), its data folder for the raw files and manifests past 2024, and its synthetic
    loaders and hash checks; v3's replays cover `DAYS`."""
    import records_v3_world as rw
    from valiwatt import v2store
    from valiwatt.monitor import run as mrun
    from valiwatt.monitor import run_v3
    from valiwatt.quality import run as qrun
    from valiwatt.sources import eccc, hydroquebec, nbm

    root = Path(root)
    repo, store, data = root / "repo", root / "mlruns", root / "data"
    rw.install(mp, repo, store)
    mp.setattr(v2store, "REPO_ROOT", repo)
    mp.setattr(hydroquebec, "CSV_PATH", data / "hydroquebec" / "export.csv")
    mp.setattr(hydroquebec, "MANIFEST_PATH", data / "hydroquebec" / "manifest.json")
    mp.setattr(eccc, "RAW_ECCC", data / "eccc")
    mp.setattr(nbm, "RAW_NBM", data / "nbm")
    mp.setattr(nbm, "MANIFEST_PATH", data / "nbm" / "manifest.json")
    mp.setattr(mrun, "load_raw", chain_load_raw)
    mp.setattr(qrun, "load_runs", chain_load_runs)
    mp.setattr(qrun, "_verify_raw", chain_verify_raw)
    mp.setattr(qrun, "_verify_nbm", chain_verify_nbm)
    mp.setattr(run_v3, "replay_days", chain_days)


def validation_frames(year: int = 2024) -> dict[str, pd.DataFrame]:
    """The frames v3's validation logs, as the rehearsal reads them: oos and the noon feed's forecast record holding,
    of 2024, exactly the days the rehearsal replays (DAYS[2024]), as validation's would for a rehearsal of those days
    (the identity compares every 2024 hour and day either holds); the January 2024 fold's interval layer (bins: the
    layer of the model frozen for 2024, which the identity requires); the refitted intervals of v3."""
    w, fz = world(), frozen(year)
    first, last = (pd.Timestamp(d) for d in DAYS[year])
    keep = (w.oos["day"] < pd.Timestamp(f"{year}-01-01")) | ((w.oos["day"] >= first) & (w.oos["day"] <= last))
    oos = w.oos[keep]
    days = pd.DatetimeIndex(w.record_noon.index)
    record = w.record_noon[(days < pd.Timestamp(f"{year}-01-01")) | ((days >= first) & (days <= last))]
    layer = fz.model.interval
    bins = pd.DataFrame([{"model": "v3", "fold": f"{year}-01", "edge_c": float(layer["bin_edge"]),
                          "q_cold": float(layer["q_cold"]), "q_rest": float(layer["q_rest"]),
                          "fallback": bool(layer["fallback"])}])
    pred = oos["pred_v3"]
    iv = pd.DataFrame({"lower": pred * (1 - fz.model.q), "upper": pred * (1 + fz.model.q)}, index=oos.index)
    iv["covered"] = ((oos["target"] >= iv["lower"]) & (oos["target"] <= iv["upper"])).astype(float).where(
        pred.notna() & oos["target"].notna())
    return {"oos": oos, "forecast_record_noon": record, "bins": bins, "intervals_v3": iv}


def log_validation(dq_run_id: str, frames: dict | None = None, **more) -> str:
    """A complete v3 validation run on `dq_run_id`, as validate-v3 logs it: `validation_frames` and
    results/validation.json, the thresholds of 2024 and 2025 and the cold threshold of the world's models. more: tags
    to replace (a fresh clone's: reproduction and byte copies not run)."""
    import json
    import tempfile
    from pathlib import Path as P

    import mlflow

    from valiwatt import records_v3, tracking
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.quality.checks import jsonable

    freeze = records_v3.current_freeze()
    tags = {"kind": "validation_v3", "version": "v3", "freeze_tag": freeze["freeze_tag"],
            "freeze_commit": freeze["freeze_commit"], "dq_run_id": dq_run_id, "rules_sha256": rules_sha256(),
            "reproduction": "passed", "byte_copies": "passed", "fresh_clone": "false", "issue": "noon", **more}
    with tracking.start_run(vd.EXPERIMENT, "validation-v3", tags=tags) as run:
        with tempfile.TemporaryDirectory() as tmp:
            for name, frame in (frames or validation_frames()).items():
                frame.to_parquet(P(tmp) / f"{name}.parquet")
            mlflow.log_artifacts(tmp, artifact_path="frames")
        mlflow.log_text(json.dumps(jsonable({"thresholds": {str(y): frozen(y).thresholds for y in (2024, 2025)},
                                             "config": {"cold_c": frozen(2024).cold_c}})), "results/validation.json")
        mlflow.set_tag("status", "complete")
    return run.info.run_id


def log_frozen(year: int, validation_run_id: str, dq_run_id: str, model=None, **more) -> str:
    """The world's models frozen for `year`, as freeze-v3 logs them (v3's registered model, alias replay-<year>).
    more: tags to replace, as for `log_validation`."""
    from valiwatt import records_v3
    from valiwatt.forecast import freeze as fz_
    from valiwatt.forecast.freeze_v3 import MODEL_NAME_V3
    from valiwatt.forecast.model import MODELS_V3
    from valiwatt.monitor.rules import rules_sha256

    fz = frozen(year)
    example = world().noon[["day_type", "hour", *MODELS_V3["v3"].inputs]].dropna().tail(24).astype({"hour": float})
    freeze = records_v3.current_freeze()
    tags = {"version": "v3", "freeze_tag": freeze["freeze_tag"], "validation_run_id": validation_run_id,
            "dq_run_id": dq_run_id, "rules_sha256": rules_sha256(), "issue": "noon", "reproduction": "passed",
            "byte_copies": "passed", "fresh_clone": "false", **more}
    run, _ = fz_.log_frozen(model or fz.model, fz.coef, example, tags, comparators=fz.comparators,
                            model_name=MODEL_NAME_V3)
    return run.info.run_id


def write_past_2024(retrieved_utc: str) -> list[Path]:
    """Every raw file and manifest past 2024 a 2025 replay reads (run_v3.files_past_2024), written as byte copies
    into the data folder; the Hydro-Québec manifest through 2025 records `retrieved_utc`. Returns them."""
    import json

    from valiwatt.monitor import run_v3
    from valiwatt.sources import hydroquebec

    files = run_v3.files_past_2024()
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"{f.name}\n", encoding="utf-8")
    hydroquebec.manifest_path(2025).write_text(json.dumps({"retrieved_utc": retrieved_utc}), encoding="utf-8")
    return files


def date_dq_run(run_id: str, not_before: str | None = None, at: str | None = None) -> None:
    """The v3 data-quality run `run_id` dated as started (its end moved with it) at `at`, or else after every other v3
    data-quality run and no earlier than `not_before`. The chain's clocks are synthetic (the freeze record is
    committed a day after the build, the export through 2025 retrieved thirty days after it), so a run that read that
    export is dated no earlier than its retrieval, as on a real machine, and a run logged later stays the latest (the
    2025 gate's (d) reads the latest data-quality run by start)."""
    import sqlite3

    from mlflow.entities import ViewType

    from valiwatt import tracking
    from valiwatt.quality import run_v3 as dq_v3

    c = tracking.client()
    run = c.get_run(run_id)
    if at is not None:
        start = int(pd.Timestamp(at).value // 10**6)
    else:
        exp = c.get_experiment_by_name(dq_v3.EXPERIMENT)
        others = [r.info.start_time for r in c.search_runs([exp.experiment_id], "", run_view_type=ViewType.ALL)
                  if r.info.run_id != run_id]
        start = max(run.info.start_time, max(others, default=0) + 1,
                    int(pd.Timestamp(not_before).value // 10**6) if not_before else 0)
    end = None if run.info.end_time is None else start + max(run.info.end_time - run.info.start_time, 0)
    con = sqlite3.connect(tracking.MLRUNS / "mlflow.db")
    try:
        con.execute("update runs set start_time = ?, end_time = ? where run_uuid = ?", (start, end, run_id))
        con.commit()
    finally:
        con.close()


def log_dq_2025(retrieved_utc: str, **params) -> str:
    """A finished v3 data-quality run through 2025 whose gates passed, its hashes those the patched checks give, as
    dq-v3 logs it, dated as started no earlier than `retrieved_utc` and after every other (`date_dq_run`); params: any
    to replace."""
    import mlflow

    from valiwatt import records_v3, tracking
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.quality import run as qrun
    from valiwatt.quality import run_v3 as dq_v3
    from valiwatt.sources import nbm

    freeze = records_v3.current_freeze()
    tags = {"kind": dq_v3.KIND, "issue": "noon", "rules_sha256": rules_sha256(), "freeze_tag": freeze["freeze_tag"],
            "freeze_commit": freeze["freeze_commit"], "byte_copies": "passed", "fresh_clone": "false"}
    base = {"through": 2025, "window_last_utc": data_last(2025).isoformat(), "hq_sha256": HQ_SHA[2025],
            "eccc_manifests_sha256": qrun.eccc_manifests_sha256(eccc_manifests(2025)),
            "nbm_manifest_sha256": nbm.manifest_sha256(nbm_manifest(2025)), "hq_retrieved_utc": retrieved_utc,
            "hq_revised_records": 1}
    with tracking.start_run(dq_v3.EXPERIMENT, "dq-v3", tags=tags) as run:
        mlflow.log_params(base | params)
        mlflow.set_tag("gates", "passed")
    date_dq_run(run.info.run_id, not_before=retrieved_utc)
    return run.info.run_id


def v2_ledger_text(*entries: dict) -> str:
    import json

    return json.dumps({"replays": list(entries)}, indent=2) + "\n"


def v2_branch(repo: Path, *entries: dict, ref: str = "refs/heads/v2") -> str:
    """A commit off the head holding v2's ledger with `entries`, pointed at by `ref` (v2's branch, or its copy from the
    remote), the working tree left on its branch. Returns the commit."""
    import records_v3_world as rw

    branch = rw.git(repo, "symbolic-ref", "--short", "HEAD")
    rw.git(repo, "checkout", "-q", "--detach")
    c = rw.commit(repo, {"docs/replay-ledger.json": v2_ledger_text(*entries)}, "v2's ledger", empty=True)
    rw.git(repo, "update-ref", ref, c)
    rw.git(repo, "checkout", "-q", branch)
    return c


def v2_2024_entry() -> dict:
    """v2's first 2024 replay, as v2's ledger holds it: started before anything of the chain."""
    from report_world import v2_entry

    return v2_entry(V2_2024_RUN, started=V2_2024_STARTED)


def v2_2025_entry(started_utc: str, evaluation: str = "held_out", run_id: str = V2_2025_RUN) -> dict:
    from report_world import v2_entry

    standing = {"held_out": "clean", "post_hoc": "post_hoc"}.get(evaluation, "clean")
    return v2_entry(run_id, year=2025, started=started_utc, evaluation=evaluation, standing=standing)


def replay_runs_v3(year: int, kind: str = "replay_v3") -> list:
    """The store's runs of `kind` and `year`, any status, deleted included, oldest first."""
    from replay_world import replay_runs

    return replay_runs(year, kind)


def build_rehearsal_base(root: Path) -> dict:
    """The rehearsal base under `root` (see above)."""
    import pytest

    import records_v3_world as rw
    from valiwatt.config import V3_FIRST_RUN_TAG

    repo = Path(root) / "repo"
    made = rw.make_repo(repo)
    rw.tag(repo, V3_FIRST_RUN_TAG, made["f0"])
    rw.commit(repo, {"docs/replay-ledger.json": v2_ledger_text(v2_2024_entry())}, "v2's ledger")
    with pytest.MonkeyPatch.context() as mp:
        install_chain(mp, root)
        dq = rw.log_dq_v3(repo, 2024)
        val = log_validation(dq)
        f24 = log_frozen(2024, val, dq)
        f25 = log_frozen(2025, val, dq)
    return {"f0": made["f0"], "first_pin": made["f0"], "dq24": dq, "validation": val, "frozen_2024": f24,
            "frozen_2025": f25}


def build_gate_base(root: Path, rehearsal_root: Path, ids: dict) -> dict:
    """The gate base under `root`, from a copy of the rehearsal base (see above). Times: every run ends and the freeze
    record is committed (dated a day after the build) before the export through 2025 was retrieved (thirty days after
    it), its data-quality run dated as started then (`date_dq_run`), and v2's 2025 test of record started a day after
    that."""
    import pytest

    import records_v3_world as rw
    from replay_world import copy_chain
    from valiwatt.freeze_record import PATH, write_freeze_record
    from valiwatt.monitor import run_v3
    from valiwatt.report import ledger_v3

    copy_chain(rehearsal_root, root)
    repo = Path(root) / "repo"
    now = pd.Timestamp.now(tz="UTC").floor("s")
    record_date, retrieved = now + pd.Timedelta(days=1), now + pd.Timedelta(days=30)
    v2_started = retrieved + pd.Timedelta(days=1)
    with pytest.MonkeyPatch.context() as mp:
        install_chain(mp, root)
        assert run_v3.run_replay_v3(2024, n_boot=20) == 0
        (rehearsal,) = replay_runs_v3(2024)
        rw.commit(repo, {"docs/replay-ledger-v3.json": ledger_v3.dumps(ledger_v3.from_store())}, "ledger-v3")
        v2 = v2_branch(repo, v2_2024_entry(), v2_2025_entry(ledger_time(v2_started)))
        write_freeze_record(rehearsal.info.run_id)
        record = rw.commit(repo, {}, "the freeze record", date=record_date.isoformat())
        assert rw.git(repo, "log", "--format=%H", "--", PATH) == record
        write_past_2024(ledger_time(retrieved))
        dq25 = log_dq_2025(ledger_time(retrieved))
    return {**ids, "rehearsal": rehearsal.info.run_id, "record": record, "v2": v2, "dq25": dq25,
            "record_date": record_date, "retrieved": retrieved, "v2_started": v2_started}


@lru_cache(maxsize=1)
def rehearsal_base() -> tuple[Path, dict]:
    import atexit
    import shutil
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="valiwatt-v3-chain-"))
    atexit.register(shutil.rmtree, root, True)
    ids = build_rehearsal_base(root / "rehearsal")
    return root / "rehearsal", ids


@lru_cache(maxsize=1)
def gate_base() -> tuple[Path, dict]:
    base, ids = rehearsal_base()
    root = base.parent / "gate"
    return root, build_gate_base(root, base, ids)


def chain_copy(tmp_path: Path, mp, gate: bool = False):
    """A private copy of the rehearsal base (or, `gate`, of the gate base) under `tmp_path`, the code pointed at it:
    its root, repository, store, data folder and the IDs it was built with."""
    from types import SimpleNamespace

    from replay_world import copy_chain

    base, ids = gate_base() if gate else rehearsal_base()
    root = Path(tmp_path) / "chain"
    copy_chain(base, root)
    install_chain(mp, root)
    return SimpleNamespace(root=root, repo=root / "repo", store=root / "mlruns", data=root / "data", **ids)
