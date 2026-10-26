"""`valiwatt read-v2 PATH`: v2's store, read once and read-only (docs/plan-v3.md, sections 3.1, 10.3 and 17, step 2).

v2's store is opened at step 2 and never again, the 2025 gate included; its path is given on the command line only, and
no other module of valiwatt names it. PATH is v2's `mlruns/` directory in place, in v2's own checkout (its `mlflow.db`
and its artifact tree): each run's recorded artifact location must lie under PATH, so a copy set aside elsewhere is
refused. The
database is opened through sqlite's read-only, immutable URI (`mode=ro&immutable=1`: no lock, no journal, no
write-ahead index is created beside it), never through MLflow's client, which may migrate a database or create an
experiment, and refused while a write-ahead log sits beside it (pages an immutable reader would not see); artifacts are
files opened for reading under PATH. Nothing is created or written there.

It is read once (section 10.3): `read-v2` refuses when v3's store already holds a read, before it opens anything.
v3's own store is never v2's: `read-v2` refuses a PATH that is v3's store, inside it or holding it, and every v3
command that logs refuses a store holding v2's first 2024 replay run (`require_own_store`).

What is read, and nothing else:
- v2's first 2024 replay: the earliest by start of the committed ledger's 2024 entries under `rules-frozen-v2`
  (`monitor.run.first_v2_2024_entry`, its one home), its run's tags and `results/replay.json`, each checked against
  that entry (the fields the ledger copied from the run, and the one digest of what judged it);
- the data-quality run its tags name: its three raw-file hashes (`hq_sha256`, `eccc_manifests_sha256`,
  `nbm_manifest_sha256`), which v3's byte-copy check compares its files with (quality/byte_copies.py);
- the validation run its tags name: its out-of-sample production forecasts (`frames/oos.parquet`, the column
  `pred_gam_forecast`), checked against the replay's hash of its forecast-drift reference, and so against the entry,
  for v3's reproduction check (section 17, step 2).

What was read is logged into v3's own store as one run of EXPERIMENT (tag kind `v2_read`): the run IDs, the hashes and
the forecasts (OOS_ARTIFACT, columns ts and forecast). `recorded()` gives it back to every later v3 step, which never
opens v2's store.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from valiwatt.config import FREEZE_TAG
from valiwatt.paths import REPO_ROOT

EXPERIMENT = "valiwatt-v2-read"
KIND = "v2_read"
OOS_ARTIFACT = "v2/oos_forecasts.parquet"
RECORD_ARTIFACT = "v2/read.json"
DB_NAME = "mlflow.db"
LEDGER = "docs/replay-ledger.json"
HASH_PARAMS = ("hq_sha256", "eccc_manifests_sha256", "nbm_manifest_sha256")
REPLAY_YEAR = 2024
PRED = "pred_gam_forecast"  # v2's production forecast in its validation frame (monitor.run.PRED_PRODUCTION)
FINISHED = "FINISHED"


class V2StoreRefused(RuntimeError):
    """v2's store cannot be read as section 10.3 says, or what it holds disagrees with v2's ledger entry."""


@dataclass(frozen=True)
class V2Read:
    """What the step-2 read took from v2's store. forecasts: columns ts (hour-ending UTC) and forecast (MW), the
    validation run's out-of-sample production forecast on every hour that has one, sorted by ts."""

    replay_run_id: str
    dq_run_id: str
    validation_run_id: str
    hashes: dict
    forecasts: pd.DataFrame
    entry: dict


def _connect(db: Path) -> sqlite3.Connection:
    """A database read-only and immutable: sqlite refuses every write on this connection, never creates the file, and
    creates nothing beside it. Refused while a write-ahead log sits beside it: its pages are the database's too."""
    if not db.is_file():
        raise V2StoreRefused(f"no {DB_NAME} in v2's store ({db.parent})")
    wal = db.with_name(f"{db.name}-wal")
    if wal.exists():
        raise V2StoreRefused(f"{wal.name} sits beside {db}: a write-ahead log not checkpointed into the database; read "
                             "it once every process that wrote it has closed it")
    return sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro&immutable=1", uri=True)


def _run(con: sqlite3.Connection, run_id: str, what: str) -> dict:
    if not run_id:
        raise V2StoreRefused(f"{what}: no run ID")
    row = con.execute("SELECT run_uuid, status, start_time, end_time, lifecycle_stage, artifact_uri FROM runs "
                      "WHERE run_uuid = ?", (run_id,)).fetchone()
    if row is None:
        raise V2StoreRefused(f"{what} {run_id} is not in v2's store")
    run = dict(zip(("run_id", "status", "start_time", "end_time", "lifecycle_stage", "artifact_uri"), row))
    run["tags"] = dict(con.execute("SELECT key, value FROM tags WHERE run_uuid = ?", (run_id,)).fetchall())
    run["params"] = dict(con.execute("SELECT key, value FROM params WHERE run_uuid = ?", (run_id,)).fetchall())
    if run["status"] != FINISHED or run["lifecycle_stage"] != "active":
        raise V2StoreRefused(f"{what} {run_id} is {run['status']} ({run['lifecycle_stage']}), not a finished run")
    return run


def _artifact(store: Path, run: dict, path: str) -> bytes:
    """One artifact file of a run, read under the store: its artifact URI must be a file URI inside `store`."""
    uri = urllib.parse.urlparse(run["artifact_uri"] or "")
    if uri.scheme != "file":
        raise V2StoreRefused(f"run {run['run_id']}: artifacts at {run['artifact_uri']!r}, not a file under v2's store")
    root = Path(urllib.parse.unquote(uri.path)).resolve()
    if not root.is_relative_to(store.resolve()):
        raise V2StoreRefused(f"run {run['run_id']}: artifacts outside v2's store ({root})")
    file = (root / path).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        raise V2StoreRefused(f"run {run['run_id']}: no artifact {path}")
    with open(file, "rb") as f:
        return f.read()


def _utc(ms) -> str:
    """A run time as report/ledger.py writes it in an entry."""
    from valiwatt.report.ledger import _utc as ledger_utc

    return ledger_utc(None if ms is None else int(ms))


def _reference_sha256(oos: pd.DataFrame, year: int) -> str:
    """The hash v2's replay logged of its forecast-drift reference (monitor.run.forecast_drift_reference and
    frozen_artifacts_sha256's `reference`): the rows of `oos` whose day is before <year>-01-01, day as int64 ns, the
    production forecast as float64."""
    keep = oos["day"] < pd.Timestamp(f"{year}-01-01")
    days = pd.DatetimeIndex(pd.to_datetime(oos.loc[keep, "day"])).as_unit("ns").asi8
    forecast = np.ascontiguousarray(oos.loc[keep, PRED].to_numpy(dtype="<f8"))
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(days, dtype="<i8").tobytes() + b"\0" + forecast.tobytes())
    return h.hexdigest()


def _entry(ledger: Path) -> dict:
    from valiwatt.monitor.run import FreezeError, first_v2_2024_entry, read_ledger

    try:
        return first_v2_2024_entry(read_ledger(json.loads(ledger.read_text(encoding="utf-8"))))
    except (OSError, ValueError, FreezeError) as e:
        raise V2StoreRefused(f"v2's first 2024 replay cannot be found in {ledger.name}: {e}") from e


def _check_replay(run: dict, results: dict, entry: dict) -> list[str]:
    """Each field of the ledger entry that the replay run disagrees with (none: it is the entry's run)."""
    from valiwatt.monitor.run import frozen_digest

    t = run["tags"]
    got = {"year": t.get("year"), "freeze_tag": results.get("freeze_tag"), "freeze_commit": t.get("freeze_commit"),
           "rules_sha256": t.get("rules_sha256"), "code_sha256": t.get("code_sha256"),
           "started_utc": _utc(run["start_time"]), "ended_utc": _utc(run["end_time"]), "status": t.get("status"),
           "evaluation": t.get("evaluation"), "model_sha256": t.get("coefficient_sha256"),
           "frozen_sha256": frozen_digest(results["frozen_sha256"]) if isinstance(results.get("frozen_sha256"), dict)
           else None}
    want = {k: (str(entry.get(k)) if k == "year" else entry.get(k)) for k in got}
    out = [f"{k}: the run has {got[k]!r}, the entry {want[k]!r}" for k in got if got[k] != want[k]]
    if t.get("kind") != "replay":
        out.append(f"kind: the run is {t.get('kind')!r}, not a replay")
    if results.get("freeze_commit") != t.get("freeze_commit"):
        out.append("its results and its tags name different freeze commits")
    return out


def read_v2_store(store, ledger: Path | None = None) -> V2Read:
    """Read v2's store at `store` (v2's `mlruns/` directory) as section 10.3 says, checking everything read against v2's
    first 2024 replay's ledger entry (`ledger`: docs/replay-ledger.json of this checkout by default). Writes nothing
    anywhere; refused (V2StoreRefused, naming each disagreement) when the store disagrees with the entry. Pure but for
    the reads: `run_read_v2` guards and logs it."""
    store = Path(store)
    entry = _entry(Path(ledger) if ledger is not None else REPO_ROOT / LEDGER)
    con = _connect(store / DB_NAME)
    try:
        replay = _run(con, entry["run_id"], "v2's first 2024 replay run")
        results = json.loads(_artifact(store, replay, "results/replay.json"))
        problems = _check_replay(replay, results, entry)
        if problems:
            raise V2StoreRefused(f"v2's first 2024 replay run {entry['run_id']} disagrees with its ledger entry: "
                                 + "; ".join(problems))
        dq = _run(con, replay["tags"].get("dq_run_id", ""), "the data-quality run of v2's first 2024 replay")
        val = _run(con, replay["tags"].get("validation_run_id", ""), "the validation run of v2's first 2024 replay")
    finally:
        con.close()
    problems = []
    if dq["tags"].get("kind") != "dq" or dq["tags"].get("gates") != "passed":
        problems.append(f"data-quality run {dq['run_id']} is not a data-quality run whose gates passed")
    hashes = {k: dq["params"].get(k, "") for k in HASH_PARAMS}
    bad = [k for k, v in hashes.items() if len(v) != 64 or any(ch not in "0123456789abcdef" for ch in v)]
    if bad:
        problems.append(f"data-quality run {dq['run_id']} holds no SHA-256 for {bad}")
    if val["tags"].get("kind") != "validation" or val["tags"].get("status") != "complete":
        problems.append(f"validation run {val['run_id']} is not a complete validation run")
    if val["tags"].get("dq_run_id") != dq["run_id"]:
        problems.append(f"validation run {val['run_id']} was built on data-quality run "
                        f"{val['tags'].get('dq_run_id')!r}, not the replay's {dq['run_id']}")
    if val["tags"].get("nbm_manifest_sha256") != hashes["nbm_manifest_sha256"]:
        problems.append(f"validation run {val['run_id']} read other NBM files than data-quality run {dq['run_id']}")
    oos = pd.read_parquet(io.BytesIO(_artifact(store, val, "frames/oos.parquet")), columns=["day", PRED])
    reference = results["frozen_sha256"].get("reference")
    if _reference_sha256(oos, REPLAY_YEAR) != reference:
        problems.append(f"the out-of-sample forecasts of validation run {val['run_id']} are not those whose hash the "
                        f"replay logged ({str(reference)[:12]}), and so not those of the ledger entry")
    if problems:
        raise V2StoreRefused("; ".join(problems))
    ts = pd.DatetimeIndex(oos.index)
    if ts.tz is None or str(ts.tz) != "UTC" or ts.has_duplicates:
        raise V2StoreRefused(f"validation run {val['run_id']}: its out-of-sample frame is not indexed by unique UTC "
                             "hour-ending labels")
    f = pd.DataFrame({"ts": ts, "forecast": oos[PRED].to_numpy(dtype=float)})
    f = f[np.isfinite(f["forecast"])].sort_values("ts", kind="stable").reset_index(drop=True)
    return V2Read(replay_run_id=replay["run_id"], dq_run_id=dq["run_id"], validation_run_id=val["run_id"],
                  hashes=hashes, forecasts=f, entry=dict(entry))


def require_own_store() -> None:
    """v3's own store is not v2's (docs/plan-v3.md, section 10.3: v3 runs only from its own checkout, its store that
    checkout's mlruns/), checked by every v3 command that logs before it logs: refused (V2StoreRefused) when the store
    holds the run of v2's first 2024 replay that v2's ledger entry names. The store's database is read through the
    read-only connection; a store not yet created holds nothing."""
    from valiwatt import tracking

    replay = _entry(REPO_ROOT / LEDGER)["run_id"]
    db = Path(tracking.MLRUNS) / DB_NAME
    if not db.is_file():
        return
    con = _connect(db)
    try:
        held = con.execute("SELECT 1 FROM runs WHERE run_uuid = ?", (replay,)).fetchone()
    finally:
        con.close()
    if held is not None:
        raise V2StoreRefused(f"the store at {tracking.MLRUNS} holds v2's first 2024 replay run {replay}: it is v2's "
                             "store, and no v3 command logs there; run v3 from its own checkout")


def run_read_v2(store) -> int:
    """`valiwatt read-v2 PATH`: the first-run guard (section 10.2); PATH neither v3's own store, nor inside it, nor
    holding it; v3's store not v2's (`require_own_store`); no read already in v3's store (v2's store is read once);
    then `read_v2_store`, then one run of EXPERIMENT in v3's own store logging what was read: tags kind,
    v2_replay_run_id, v2_dq_run_id, v2_validation_run_id, v2_freeze_tag, and the freeze the read is made under
    (freeze_tag, freeze_commit: records_v3.current_freeze, v3-pre-freeze before v3's tag, section 10.3); params the
    three raw-file hashes and the forecasts' hour count; OOS_ARTIFACT and RECORD_ARTIFACT. Each refusal
    (V2StoreRefused, or V3FreezeError for the freeze) comes before v2's store is opened."""
    import tempfile

    import mlflow

    from valiwatt import tracking
    from valiwatt.firstrun import require_first_run
    from valiwatt.quality.checks import jsonable
    from valiwatt.records_v3 import current_freeze
    from valiwatt.tracking import start_run

    require_first_run()
    given, own = Path(store).resolve(), Path(tracking.MLRUNS).resolve()
    if given == own or given.is_relative_to(own) or own.is_relative_to(given):
        raise V2StoreRefused(f"{store} is v3's own store ({own}), inside it or holding it: v3 logs into its own store, "
                             "never into v2's")
    require_own_store()
    if (done := recorded()) is not None:
        raise V2StoreRefused(f"v2's store was read once, run {done['run_id']} of v3's store: it is never opened again "
                             "(docs/plan-v3.md, section 10.3)")
    freeze = current_freeze()
    r = read_v2_store(store)
    tags = {"kind": KIND, "v2_replay_run_id": r.replay_run_id, "v2_dq_run_id": r.dq_run_id,
            "v2_validation_run_id": r.validation_run_id, "v2_freeze_tag": FREEZE_TAG,
            "freeze_tag": freeze["freeze_tag"], "freeze_commit": freeze["freeze_commit"]}
    with start_run(EXPERIMENT, "read-v2", tags=tags) as run:
        mlflow.log_params({**r.hashes, "forecast_hours": len(r.forecasts),
                           "first_ts": r.forecasts["ts"].min().isoformat() if len(r.forecasts) else "",
                           "last_ts": r.forecasts["ts"].max().isoformat() if len(r.forecasts) else ""})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / Path(OOS_ARTIFACT).name
            r.forecasts.to_parquet(path, index=False)
            mlflow.log_artifact(str(path), artifact_path=str(Path(OOS_ARTIFACT).parent))
        mlflow.log_text(json.dumps(jsonable({"entry": r.entry, "hashes": r.hashes, "replay_run_id": r.replay_run_id,
                                             "dq_run_id": r.dq_run_id, "validation_run_id": r.validation_run_id}),
                                   indent=2, ensure_ascii=False), RECORD_ARTIFACT)
        mlflow.set_tag("status", "complete")
    print(f"read-v2: v2's first 2024 replay {r.replay_run_id}, data-quality run {r.dq_run_id}, validation run "
          f"{r.validation_run_id} ({len(r.forecasts)} forecast hours); logged as run {run.info.run_id}")
    return 0


def recorded(client=None) -> dict | None:
    """The latest complete read of v2's store in v3's own store: {run_id, v2_replay_run_id, v2_dq_run_id,
    v2_validation_run_id, hq_sha256, eccc_manifests_sha256, nbm_manifest_sha256}; None when there is none (a fresh
    clone, section 3.1). Never opens v2's store."""
    from valiwatt import tracking

    c = client or tracking.client()
    exp = c.get_experiment_by_name(EXPERIMENT)
    if exp is None:
        return None
    runs = c.search_runs([exp.experiment_id], filter_string=f"tags.kind = '{KIND}' and tags.status = 'complete' "
                         "and attributes.status = 'FINISHED'", order_by=["attributes.start_time DESC"], max_results=1)
    if not runs:
        return None
    run = runs[0]
    return {"run_id": run.info.run_id,
            **{k: run.data.tags.get(k, "") for k in ("v2_replay_run_id", "v2_dq_run_id", "v2_validation_run_id")},
            **{k: run.data.params.get(k, "") for k in HASH_PARAMS}}


def recorded_forecasts(client=None) -> pd.DataFrame:
    """The forecasts the latest complete read logged (columns ts and forecast), for the reproduction check; refused
    (V2StoreRefused) when no read was logged."""
    import mlflow

    from valiwatt import tracking

    c = client or tracking.client()
    rec = recorded(c)
    if rec is None:
        raise V2StoreRefused("no read of v2's store in v3's store: run `valiwatt read-v2 PATH` first")
    path = mlflow.artifacts.download_artifacts(run_id=rec["run_id"], artifact_path=OOS_ARTIFACT)
    return pd.read_parquet(path)
